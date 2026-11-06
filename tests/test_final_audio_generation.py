"""Offline quote, paid-gate, and raw sample tests for final-frame audio EOF."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_generation as fixtures
from backend import generation, generation_audio as ga, media, store, workflow


class FinalAudioGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.GenerationContracts.setUpClass.__func__(cls)
        cls.tail_source = cls.material_root / 'final_tail.mov'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=blue:s=640x480:r=30:d=4',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4.02',
            '-map', '0:v:0', '-map', '1:a:0', '-vf', 'settb=1/30000,setpts=PTS+1230',
            '-c:v', 'libx264', '-threads', '2', '-enc_time_base', '1/30000', '-fps_mode', 'passthrough', '-video_track_timescale', '30000',
            '-c:a', 'pcm_s32le', '-ac', '2', cls.tail_source])

    tearDownClass = classmethod(fixtures.GenerationContracts.tearDownClass.__func__)
    seed_preparation = fixtures.GenerationContracts.seed_preparation
    run_jobs = fixtures.GenerationContracts.run_jobs
    prepare = fixtures.GenerationContracts.prepare
    confirmation = fixtures.GenerationContracts.confirmation
    drive = fixtures.GenerationContracts.drive

    def setUp(self):
        fixtures.GenerationContracts.setUp(self)
        asset = store.add_asset(self.pid, self.tail_source, 'source', meta=media.probe(self.tail_source))
        p = store.get_project(self.pid)
        p.update(source=asset, source_meta={**asset['meta'], 'duration': asset['meta']['video_duration_seconds']})
        store.put_project(p)

    def ready(self):
        return self.prepare(purpose='candidate_sample', segment_ids=['B'], include_source_audio=True)

    def test_formal_prepare_freezes_full_video_short_wav_canonical_prompt_without_upload(self):
        state = self.ready(); quote = state['quote']; contract = quote['input_contract']['source_audio']['B']
        self.assertEqual(quote['segment_ids'], ['B'])
        self.assertEqual(quote['input_seconds'], 2)
        self.assertEqual(contract['selected_video_duration_rational'], '2')
        self.assertEqual(contract['coverage_duration_rational'], '1979/1000')
        self.assertEqual(contract['sample_count'], 94992)
        self.assertEqual(contract['reference_coverage_policy'], ga.FINAL_TAIL_POLICY)
        self.assertEqual([m['type'] for m in quote['material_manifest'][0]['media']], ['reference_video', 'reference_image', 'reference_audio'])
        frozen = generation._read(generation._root(self.pid) / 'preparation.json')['quote']
        generation._assert_scope(frozen); generation._assert_current_audio_contract(frozen)
        self.assertTrue(frozen['media'][0]['prompt'].endswith(ga.reference_prompt_note(contract)))
        self.assertIn('完整视频仍保留2.000000000秒', frozen['media'][0]['prompt'])
        self.assertEqual(self.cloud.uploads + self.cloud.posts, [])
        p = store.get_project(self.pid)
        self.assertEqual(p['segments'][1]['end'], 4)
        self.assertFalse(p['segments'][1].get('candidate_review'))

    def test_confirm_and_worker_reject_recomputed_contract_change_before_any_upload(self):
        state = self.ready(); body = self.confirmation(state)
        original = ga.plan_reference_audio
        def changed(*args, **kwargs):
            plan = original(*args, **kwargs); plan['source_stop_sample'] -= 1; return plan
        with patch.object(ga, 'plan_reference_audio', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '合同'):
                generation.confirm(self.pid, body)
        self.assertEqual(self.cloud.uploads + self.cloud.posts, [])
        run_id = generation.confirm(self.pid, body)['current_run']['id']
        with patch.object(ga, 'plan_reference_audio', side_effect=changed):
            result, _ = self.drive(run_id)
        self.assertEqual(result['segments'][0]['status'], 'NOT_SUBMITTED')
        self.assertEqual(self.cloud.uploads + self.cloud.posts, [])

    def test_worker_checks_actual_frozen_wav_again_after_upload_before_generation_post(self):
        state = self.ready(); run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        run = generation._read(generation._run_path(self.pid, run_id))
        audio = next(m for m in run['segments'][0]['media'] if m['type'] == 'reference_audio')
        upload = self.cloud.client_type._upload
        def upload_then_change(client, path):
            result = upload(client, path)
            if Path(path).resolve() == Path(audio['path']).resolve():
                content = bytearray(Path(path).read_bytes()); content[-1] ^= 1; Path(path).write_bytes(content)
            return result
        with patch.object(self.cloud.client_type, '_upload', upload_then_change):
            result, _ = self.drive(run_id)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(result['segments'][0]['status'], 'CANCELED')

    def test_completed_tail_raw_candidate_survives_mask_changes_without_reauthorizing_paid_run(self):
        state = self.ready(); run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        self.drive(run_id)
        old = store.get_project(self.pid)['segments'][1]['sample_candidate']
        run_path = generation._run_path(self.pid, run_id); original = run_path.read_bytes()
        self.assertTrue(generation.state(self.pid)['sample_states']['B']['input_current'])
        workflow.attach(self.pid, self.mask, 'edit_mask', segment_id='B')
        generation.inspect_sample_result(self.pid, {'segment_id': 'B'})
        self.run_jobs()
        sample = generation.state(self.pid)['sample_states']['B']
        self.assertTrue(sample['input_current'])
        self.assertEqual(sample['candidate_asset_id'], old['id'])
        self.assertEqual(run_path.read_bytes(), original)
        self.assertEqual(len(self.cloud.posts), 1)
        self.assertFalse(store.get_project(self.pid)['segments'][1].get('candidate_review'))
        self.assertFalse(store.get_project(self.pid).get('final'))
        with patch.object(ga, 'FINAL_TAIL_POLICY', 'DIFFERENT_POLICY'):
            current = store.get_project(self.pid)
            self.assertFalse(generation._sample_state(current, 'B', generation._read(run_path), cached=False)['input_current'])
            with self.assertRaises(ValueError):
                generation.inspect_sample_result(self.pid, {'segment_id': 'B'})


if __name__ == '__main__':
    unittest.main()
