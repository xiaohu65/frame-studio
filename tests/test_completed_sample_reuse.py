"""Original cloud sample contracts with local media and a simulated provider."""
import copy
from pathlib import Path
from unittest.mock import patch
import unittest

import test_generation as generation_fixtures
from backend import generation, generation_audio, media, store, workflow


class CompletedSampleReuseContracts(unittest.TestCase):
    setUpClass = classmethod(generation_fixtures.GenerationContracts.setUpClass.__func__)
    tearDownClass = classmethod(generation_fixtures.GenerationContracts.tearDownClass.__func__)
    setUp = generation_fixtures.GenerationContracts.setUp
    seed_preparation = generation_fixtures.GenerationContracts.seed_preparation
    run_jobs = generation_fixtures.GenerationContracts.run_jobs
    prepare = generation_fixtures.GenerationContracts.prepare
    confirmation = generation_fixtures.GenerationContracts.confirmation
    drive = generation_fixtures.GenerationContracts.drive
    audio_project = generation_fixtures.GenerationContracts.audio_project
    def completed(self, *, audio=False):
        if audio:
            self.audio_project()
        ready = self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=audio)
        run_id = generation.confirm(self.pid, self.confirmation(ready))['current_run']['id']
        self.drive(run_id)
        return run_id

    def new_mask(self, width=160):
        dest = self.data / f'new_mask_{width}_{store.uid()}.mkv'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=black:s=640x480:r=30:d=2',
            '-vf', f'drawbox=x=0:y=0:w={width}:h=480:color=white:t=fill,format=gray', '-c:v', 'ffv1', '-threads', '2', dest])
        workflow.attach(self.pid, dest, 'edit_mask', segment_id='A')
        return dest

    def test_legacy_mask_derivatives_stay_archived_and_never_define_current_raw_sample(self):
        run_id = self.completed()
        run_path = generation._run_path(self.pid, run_id)
        quote_path = generation._root(self.pid) / 'preparation.json'
        original_run = run_path.read_bytes()
        original_quote = quote_path.read_bytes()
        preview_path = self.data / 'legacy_preview.mp4'
        preview_path.write_bytes(b'legacy preview artifact')
        preview = store.add_asset(self.pid, preview_path, 'sample_preview')
        project = store.get_project(self.pid)
        segment = project['segments'][0]
        segment['sample_preview'] = preview
        segment['sample_preview_report'] = {'preview_asset_id': preview['id'], 'preview_sha256': preview['sha256']}
        segment['sample_provenance'].update(local_reuse={'binding_fingerprint': 'legacy'},
            local_derivation_history=[{'preview': preview}], local_material_confirmation={'preview_asset_id': preview['id']})
        store.put_project(project)
        self.new_mask()
        before = store.project_path(self.pid).read_bytes()

        state = generation.state(self.pid)
        sample = state['sample_states']['A']
        self.assertTrue(sample['input_current'])
        self.assertTrue(state['current_run']['segments'][0]['attached'])
        self.assertEqual(sample['candidate_asset_id'], segment['sample_candidate']['id'])
        self.assertNotEqual(sample['candidate_asset_id'], preview['id'])
        self.assertFalse(sample['final_approved'])
        for key in ('preview_current', 'preview_asset_id', 'adaptation_current', 'local_binding_current',
                    'material_confirmation_current', 'reuse_available', 'scope_support_blocked'):
            self.assertNotIn(key, sample)
        for action in ('prepare_sample_preview', 'reuse_completed_sample', 'confirm_sample_materials'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '已移除小样遮罩合成.*原始小样.*原声试听'):
                generation.dispatch(self.pid, {'action': action, 'segment_id': 'A'})
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assertEqual(run_path.read_bytes(), original_run)
        self.assertEqual(quote_path.read_bytes(), original_quote)
        self.assertEqual(preview_path.read_bytes(), b'legacy preview artifact')
        self.assertEqual(self.jobs, [])
        self.assertEqual(len(self.cloud.posts), 1)

    def test_actual_input_changes_invalidate_raw_sample_and_block_inspection(self):
        self.completed(); self.new_mask()
        base = store.get_project(self.pid)
        changes = {
            'source_bytes': lambda p: p['source'].update(path=str(self.short_candidate)),
            'range': lambda p: p['segments'][0].update(end=1.5),
            'reference_image': lambda p: p['roles'][0]['images'].clear(),
            'role_binding': lambda p: p['source_actors'][0].update(scope='person'),
            'prompt': lambda p: p['prompts'].update(A='已改变的实际提示词'),
            'model': lambda p: p['settings'].update(model='wan3.0-video-prime'),
            'parameters': lambda p: p['settings'].update(resolution='720P'),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                current = copy.deepcopy(base); change(current); store.put_project(current)
                self.assertFalse(generation.state(self.pid)['sample_states']['A']['input_current'])
                with self.assertRaises(ValueError):
                    generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
                store.put_project(copy.deepcopy(base))
        self.assertEqual(len(self.cloud.posts), 1)

    def test_frozen_audio_bytes_or_contract_changes_block_raw_sample_inspection(self):
        run_id = self.completed(audio=True); self.new_mask()
        path = generation._run_path(self.pid, run_id)
        original = path.read_bytes(); run = generation._read(path)
        audio = next(m for m in run['segments'][0]['media'] if m['type'] == 'reference_audio')
        audio_bytes = Path(audio['path']).read_bytes()
        Path(audio['path']).write_bytes(b'changed audio')
        with self.assertRaises(ValueError):
            generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        Path(audio['path']).write_bytes(audio_bytes)
        with patch.object(generation_audio, 'REFERENCE_METHOD', 'CHANGED_REFERENCE_CONTRACT'):
            with self.assertRaises(ValueError):
                generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(self.cloud.posts), 1)

    def test_mask_change_does_not_block_raw_audition_and_reuses_the_same_lossless_audio(self):
        run_id = self.completed(audio=True)
        generation.prepare_sample_audition(self.pid, {'segment_id': 'A'}); self.run_jobs()
        old = store.get_project(self.pid)['segments'][0]['sample_provenance']['audition']['audio_asset']
        self.new_mask()
        local = generation.state(self.pid)['sample_states']['A']
        self.assertTrue(local['input_current']); self.assertTrue(local['audition_current'])
        reused = generation.prepare_sample_audition(self.pid, {'segment_id': 'A'})
        self.assertTrue(reused['reused']); self.assertEqual(reused['asset']['id'], old['id'])
        self.assertEqual(len(self.cloud.posts), 1)

    def test_pending_quote_and_queued_paid_authority_still_expire_when_masks_change(self):
        self.completed(); self.new_mask(160)
        quote = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        body = self.confirmation(quote)
        queued = generation.confirm(self.pid, body)['current_run']['id']
        self.new_mask(80)
        run, _ = self.drive(queued)
        self.assertEqual(run['segments'][0]['status'], 'NOT_SUBMITTED')
        self.assertEqual(len(self.cloud.posts), 1)
        # A separate unconsumed old quote is also still governed by full _fp.
        pending = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        pending_body = self.confirmation(pending)
        self.new_mask(40)
        with self.assertRaisesRegex(ValueError, '过期'):
            generation.confirm(self.pid, pending_body)
        self.assertEqual(len(self.cloud.posts), 1)

    def test_wrong_segment_failed_run_and_modified_raw_cannot_be_current(self):
        run_id = self.completed()
        with self.assertRaises(ValueError):
            generation.inspect_sample_result(self.pid, {'segment_id': 'B'})
        run_path = generation._run_path(self.pid, run_id)
        original_run = run_path.read_bytes()
        generation._mutate_run(self.pid, run_id, lambda run: run.update(status='FAILED'))
        with self.assertRaises(ValueError):
            generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        run_path.write_bytes(original_run)

        state = generation.state(self.pid)
        self.assertTrue(state['current_run']['segments'][0]['attached'])
        raw = state['current_run']['segments'][0]['asset']
        Path(raw['path']).write_bytes(b'changed raw sample')
        state = generation.state(self.pid)
        self.assertFalse(state['sample_states']['A']['input_current'])
        self.assertFalse(state['current_run']['segments'][0]['attached'])
        self.assertFalse(state['current_run']['local_current'])
        with self.assertRaises(ValueError):
            generation.prepare_sample_audition(self.pid, {'segment_id': 'A'})
        self.assertEqual(len(self.cloud.posts), 1)

    def test_cross_project_receipt_cannot_verify_raw_even_when_paths_and_candidate_are_identical(self):
        run_id = self.completed(); self.new_mask()
        original = store.get_project(self.pid)
        other_pid = store.create_project('另一个临时项目')['id']
        cloned = copy.deepcopy(original); cloned['id'] = other_pid; store.put_project(cloned)
        generation._run_path(other_pid, run_id).parent.mkdir(parents=True)
        generation._run_path(other_pid, run_id).write_bytes(generation._run_path(self.pid, run_id).read_bytes())
        self.assertFalse(generation.state(other_pid)['sample_states']['A']['input_current'])
        with self.assertRaises(ValueError):
            generation.inspect_sample_result(other_pid, {'segment_id': 'A'})
        self.assertEqual(len(self.cloud.posts), 1)

    def test_queued_audition_cannot_attach_to_a_different_raw_sample(self):
        self.completed(audio=True)
        generation.prepare_sample_audition(self.pid, {'segment_id': 'A'})
        other = store.add_asset(self.pid, self.reference, 'generated', meta=media.probe(self.reference))
        project = store.get_project(self.pid)
        project['segments'][0]['sample_candidate'] = other
        store.put_project(project)
        before = store.project_path(self.pid).read_bytes()
        with self.assertRaises(ValueError):
            self.run_jobs()
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assertEqual(len(self.cloud.posts), 1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(CompletedSampleReuseContracts(name) for name in CompletedSampleReuseContracts.__dict__ if name.startswith('test_'))
