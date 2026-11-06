"""Only public reads consume successes; every cache key includes actual bytes."""
import copy
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend import generation, store


class PublicAudioValidationCache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(store, 'DATA', self.root)
        self.patch.start(); self.addCleanup(self.patch.stop)
        generation._public_audio_success.clear()
        self.addCleanup(generation._public_audio_success.clear)
        source, wav = self.root / 'source.mov', self.root / 'reference.wav'
        source.write_bytes(b'actual source bytes'); wav.write_bytes(b'actual audio bytes')
        p = store.create_project('isolated cache test')
        p.update(source={'id': 'source', 'path': str(source), 'sha256': store.digest(source)},
                 segments=[{'id': 'C', 'start': 0, 'end': 2}], prompts={'C': 'manual prompt'})
        store.put_project(p)
        self.project = store.get_project(p['id'])
        self.pid = p['id']
        self.wav = wav
        self.quote = {'snapshot': copy.deepcopy(self.project), 'settings': self.project['settings'],
            'purpose': 'candidate_sample', 'segment_ids': ['C'],
            'input_policy': {'include_source_audio': True},
            'input_contract': {'source_audio': {'C': {'coverage': 'real natural EOF'}}},
            'media': [{'segment_id': 'C', 'prompt': 'actual effective prompt', 'parameters': {'audio': False},
                'media': [{'type': 'reference_audio', 'path': str(wav), 'sha256': store.digest(wav)}]}]}

    def test_public_success_is_reused_but_default_paid_validation_always_runs(self):
        with patch.object(generation, '_validate_current_audio_contract') as validate:
            generation._assert_current_audio_contract(self.quote, cached=True)
            generation._assert_current_audio_contract(self.quote, cached=True)
            self.assertEqual(validate.call_count, 1)
            generation._assert_current_audio_contract(self.quote)
            generation._assert_current_audio_contract(self.quote)
            self.assertEqual(validate.call_count, 3)

    def test_actual_wav_and_source_changes_fail_cheaply_even_when_mtime_and_size_are_restored(self):
        for path in [self.wav, Path(self.project['source']['path'])]:
            with self.subTest(path=path.name):
                generation._public_audio_success.clear()
                original = path.read_bytes(); stat = path.stat()
                with patch.object(generation, '_validate_current_audio_contract') as validate:
                    generation._assert_current_audio_contract(self.quote, cached=True)
                    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    with self.assertRaisesRegex(ValueError, '输入文件发生变化'):
                        generation._assert_current_audio_contract(self.quote, cached=True)
                    self.assertEqual(validate.call_count, 1)
                path.write_bytes(original)

    def test_contract_prompt_and_declared_input_changes_invalidate_success(self):
        with patch.object(generation, '_validate_current_audio_contract') as validate:
            generation._assert_current_audio_contract(self.quote, cached=True)
            altered = copy.deepcopy(self.quote)
            altered['input_contract']['source_audio']['C']['coverage'] = 'different coverage'
            generation._assert_current_audio_contract(altered, cached=True)
            altered = copy.deepcopy(self.quote); altered['media'][0]['prompt'] = 'changed effective prompt'
            generation._assert_current_audio_contract(altered, cached=True)
            self.assertEqual(validate.call_count, 3)

    def test_failures_and_files_changed_during_validation_are_never_published(self):
        with patch.object(generation, '_validate_current_audio_contract', side_effect=ValueError('invalid contract')) as validate:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    generation._assert_current_audio_contract(self.quote, cached=True)
            self.assertEqual(validate.call_count, 2)
        self.assertEqual(generation._public_audio_success, {})
        def change_file(_):
            self.wav.write_bytes(b'changed audio bytes')
        with patch.object(generation, '_validate_current_audio_contract', side_effect=change_file):
            with self.assertRaisesRegex(ValueError, '输入文件发生变化'):
                generation._assert_current_audio_contract(self.quote, cached=True)
        self.assertEqual(generation._public_audio_success, {})

    def test_concurrent_cold_public_reads_perform_one_complete_validation(self):
        def slow(_):
            time.sleep(.03)
        with patch.object(generation, '_validate_current_audio_contract', side_effect=slow) as validate:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: generation._assert_current_audio_contract(self.quote, cached=True), range(4)))
            self.assertEqual(validate.call_count, 1)

    def test_unrelated_cold_quote_does_not_block_a_warm_success(self):
        other = copy.deepcopy(self.quote); other['media'][0]['prompt'] = 'unrelated quote'
        started, release = threading.Event(), threading.Event()
        def validate(record):
            if record is other:
                started.set(); release.wait(timeout=3)
        with patch.object(generation, '_validate_current_audio_contract', side_effect=validate):
            generation._assert_current_audio_contract(self.quote, cached=True)
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(generation._assert_current_audio_contract, other, cached=True)
                try:
                    self.assertTrue(started.wait(timeout=1))
                    start = time.perf_counter()
                    generation._assert_current_audio_contract(self.quote, cached=True)
                    self.assertLess(time.perf_counter()-start, .3)
                finally:
                    release.set(); future.result(timeout=2)

    def test_source_is_rechecked_after_waiting_for_cache_guard(self):
        computed = threading.Event()
        original_key = generation._audio_validation_key
        def signal(record):
            result = original_key(record); computed.set(); return result
        with patch.object(generation, '_validate_current_audio_contract'):
            generation._assert_current_audio_contract(self.quote, cached=True)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with generation._public_audio_lock:
                    with patch.object(generation, '_audio_validation_key', side_effect=signal):
                        future = pool.submit(generation._assert_current_audio_contract, self.quote, cached=True)
                        self.assertTrue(computed.wait(timeout=1))
                        source = Path(self.project['source']['path'])
                        data = source.read_bytes(); source.write_bytes(bytes([data[0]^1])+data[1:])
                with self.assertRaisesRegex(ValueError, '输入文件发生变化'):
                    future.result(timeout=2)

    def test_startup_prewarms_only_current_unexpired_unconsumed_ready_quotes_without_writes_or_tasks(self):
        current = generation._fp(self.project)
        path = generation._root(self.pid) / 'preparation.json'
        for status, expiry, fingerprint, consumed, expected in [
                ('READY', time.time()+60, current, False, 1),
                ('READY', time.time()-1, current, False, 0),
                ('READY', time.time()+60, 'old-input', False, 0),
                ('READY', time.time()+60, current, True, 0),
                ('FAILED', time.time()+60, current, False, 0)]:
            quote = {**copy.deepcopy(self.quote), 'fingerprint': fingerprint, 'expires_at': expiry}
            if consumed:
                quote['confirmed_run'] = 'already-confirmed'
            store.save_json(path, {'status': status, 'fingerprint': fingerprint, 'quote': quote})
            before = path.read_bytes()
            with patch.object(generation, '_public_audio_validation') as prime, patch.object(generation, 'start_worker') as worker:
                generation.recover()
                self.assertEqual(prime.call_count, expected)
                if expected:
                    self.assertEqual(prime.call_args.kwargs, {'force': True})
                worker.assert_not_called()
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
