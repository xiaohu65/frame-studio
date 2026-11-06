"""Offline end-to-end local replacement contracts; no provider requests."""
import json
import os
import copy
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from backend import generation, media, provider, replacement, replacement_media, replacement_workflow, raw_delivery, store, workflow


class GenericReplacementWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.media_tmp.name)
        cls.source = cls.root/'original.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=720x480:r=24:d=1',
            '-f', 'lavfi', '-i', 'sine=frequency=700:sample_rate=48000:duration=1.1',
            '-filter:v', 'setpts=PTS+0.041/TB', '-fps_mode', 'passthrough', '-c:v', 'libx264',
            '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.source])
        cls.target = cls.root/'warrior.png'
        Image.new('RGB', (64, 96), '#ce6820').save(cls.target)
        cls.mask = cls.root/'edit.mkv'
        cls.candidate = cls.root/'candidate.mp4'
        mask = np.zeros((24, 480, 720), np.uint8)
        mask[..., :360] = 255
        candidate = np.zeros((24, 480, 720, 3), np.uint8)
        candidate[..., 2] = 255
        for path, pix, frames, codec, output in [(cls.mask, 'gray', mask, 'ffv1', 'gray'),
                                                  (cls.candidate, 'rgb24', candidate, 'libx264', 'yuv420p')]:
            result = subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', pix,
                '-s', '720x480', '-r', '24', '-i', 'pipe:0', '-an', '-c:v', codec, '-pix_fmt', output, '-threads', '2', str(path)],
                input=frames.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError(result.stderr.decode())

    @classmethod
    def tearDownClass(cls):
        cls.media_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patches = [patch.object(store, 'DATA', self.data), patch.object(workflow, 'DATA', self.data),
            patch.object(workflow, 'CONFIG', self.data/'private-config.json'), patch.object(workflow, 'RUNTIME_KEY', None),
            patch.object(provider.WanClient, '_request_json', side_effect=AssertionError('local test forbids provider requests')),
            patch.object(provider.WanClient, '_upload', side_effect=AssertionError('local test forbids cloud upload'))]
        self.mocks = [item.start() for item in self.patches]
        p = store.create_project('通用替换离线验证')
        self.pid = p['id']
        workflow.attach(self.pid, self.source, 'source')
        p = store.get_project(self.pid)
        self.sid = p['segments'][0]['id']
        store.update_project(self.pid, {'expected_revision': p['revision'],
            'settings': {**p['settings'], 'resolution': '480P'},
            'roles': [{'id': 'warrior', 'name': '武者', 'color': '#ce6820', 'images': [], 'description': '银灰发，保留定妆造型'}],
            'source_actors': [{'id': 'actor_01', 'label': '人物一', 'source_description': '开场左侧人物，持续跟随本人',
                'scope': 'head', 'target_role_id': 'warrior', 'color': '#ce6820',
                'observations': [{'segment_id': self.sid, 'time': 0, 'box': [.1, .1, .4, .5], 'visibility': 'visible', 'confirmed': True}]}]})
        workflow.attach(self.pid, self.target, 'reference', role='warrior')
        workflow.attach(self.pid, self.mask, 'edit_mask', segment_id=self.sid)

    def tearDown(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(job['status'] in ('QUEUED', 'RUNNING') for job in store.list_jobs(self.pid)):
            time.sleep(.01)
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def job(self, action, **body):
        request = {'action': action, 'segment_id': self.sid, **body}
        created = store.start_job(self.pid, action, lambda jid: workflow.action(self.pid, request, jid))
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            job = json.loads(store.job_path(created['job_id']).read_text())
            if job['status'] not in ('QUEUED', 'RUNNING'):
                return job
            time.sleep(.01)
        self.fail('local job did not complete')

    def success(self, action, **body):
        job = self.job(action, **body)
        self.assertEqual(job['status'], 'SUCCEEDED', job['message'])
        return job['result']

    def checks(self):
        return {key: True for key in replacement_workflow.CHECKS}

    def approve_plan(self):
        p = store.get_project(self.pid)
        return self.success('review_replacement', approved=True, checks=self.checks(), fingerprint=replacement.fingerprint(p))

    def prepare_raw(self, approve_plan=True):
        workflow.attach(self.pid, self.candidate, 'generated', segment_id=self.sid)
        self.success('prepare_replacement')
        if approve_plan:
            self.approve_plan()
        return self.success('validate_candidate')

    def approve_candidate(self):
        p = store.get_project(self.pid)
        return self.success('review_candidate', approved=True, checks=self.checks(),
            fingerprint=replacement_workflow.candidate_fingerprint(p, p['segments'][0]))

    def assert_no_cloud(self):
        self.mocks[-1].assert_not_called()
        self.mocks[-2].assert_not_called()
        self.assertEqual(list(self.data.glob('projects/*/wan-tasks/request-*.json')), [])

    def test_manually_received_raw_needs_a_selected_and_compatible_delivery_tier(self):
        for tier in (None, '720P'):
            with self.subTest(tier=tier):
                p = store.get_project(self.pid)
                store.update_project(self.pid, {'settings': {**p['settings'], 'resolution': tier}})
                workflow.attach(self.pid, self.candidate, 'generated', segment_id=self.sid)
                report = self.success('validate_candidate')['report']
                self.assertFalse(report['ready'])
                self.assertIn('DELIVERY_SIZE_UNVERIFIED', {issue['code'] for issue in report['issues']})

    def test_no_key_confirmed_export_import_acceptance_and_assembly_needs_no_legacy_review(self):
        # Real 24fps/native-size media intentionally cannot use the strict Wan
        # path, but a platform export and exact native candidate remain valid.
        fresh = store.get_project(self.pid)
        self.assertIsNone(fresh['generic_review'])
        self.assertIsNone(fresh['material_confirmation'])
        def local_client(config=None, state_dir=None):
            return provider.WanClient(config, state_dir or self.data/'no-key-provider')
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': '', 'BAILIAN_API_KEY': ''}), \
             patch.object(workflow, 'config', return_value={'api_key': None, 'base_url': 'https://dashscope.aliyuncs.com/api/v1'}), \
             patch.object(generation, 'WanClient', side_effect=local_client):
            started = generation.request_prepare(self.pid, {'mode': 'export', 'material_confirmed': True})
            jid = started['preparation']['job_id']
            deadline = time.monotonic()+30
            while time.monotonic()<deadline:
                job = json.loads(store.job_path(jid).read_text())
                if job['status'] not in ('QUEUED', 'RUNNING'):
                    break
                time.sleep(.01)
            self.assertEqual(job['status'], 'SUCCEEDED', job.get('message'))
        state = generation.state(self.pid)
        self.assertEqual(state['preparation']['status'], 'EXPORTED')
        self.assertFalse(state['compatibility']['ready'])
        self.assertTrue(state['material_confirmation']['current'])
        self.assertEqual(state['material_confirmation']['source'], 'export')
        self.assertIsNone(store.get_project(self.pid)['generic_review'])
        # Stand in for an exact platform result using the prepared source clip;
        # this exercises import/acceptance, not real character generation quality.
        candidate_path = store.get_project(self.pid)['segments'][0]['reference']['path']
        workflow.attach(self.pid, candidate_path, 'generated', segment_id=self.sid)
        self.assertTrue(self.success('validate_candidate')['report']['ready'])
        p = store.get_project(self.pid)
        fingerprint = replacement_workflow.candidate_fingerprint(p, p['segments'][0])
        incomplete = self.job('review_candidate', approved=True, checks={'identity': True}, fingerprint=fingerprint)
        self.assertEqual(incomplete['status'], 'FAILED')
        self.assertIsNone(store.get_project(self.pid)['segments'][0]['candidate_review'])
        self.success('review_candidate', approved=True, checks=self.checks(), fingerprint=fingerprint)
        self.success('assemble')
        self.assertIsNone(store.get_project(self.pid)['generic_review'])
        self.assertTrue(Path(store.get_project(self.pid)['final']['path']).exists())
        final = store.get_project(self.pid)
        verified = replacement_media.validate_candidate(final['source']['path'], final['final']['path'], 0, final['timeline_duration'])
        self.assertTrue(verified['ready'], verified['issues'])
        self.assert_no_cloud()

    def test_stale_material_confirmation_cannot_replace_current_candidate_acceptance(self):
        self.success('prepare_replacement')
        p = store.get_project(self.pid)
        p['material_confirmation'] = {'confirmed': True, 'fingerprint': 'old-version', 'source': 'export', 'confirmed_at': store.now()}
        store.put_project(p)
        workflow.attach(self.pid, self.candidate, 'generated', segment_id=self.sid)
        self.success('validate_candidate')
        p = store.get_project(self.pid)
        job = self.job('review_candidate', approved=True, checks=self.checks(), fingerprint=replacement_workflow.candidate_fingerprint(p, p['segments'][0]))
        self.assertEqual(job['status'], 'FAILED')
        self.assertIn('材料尚未确认', job['message'])
        self.assertIsNone(store.get_project(self.pid)['segments'][0]['candidate_review'])
        self.assert_no_cloud()

    def test_full_local_prepare_review_raw_assemble_and_package(self):
        plan = self.success('replacement_check')['plan']
        self.assertTrue(plan['ready'], plan['issues'])
        self.assertEqual(plan['mappings'][0]['target_role_id'], 'warrior')
        self.assertEqual(plan['references'][0]['reference'], '图1')
        prepared = self.success('prepare_replacement')
        self.assertTrue(Path(prepared['preparation']['contact_sheet']['path']).is_file())
        self.assertTrue(Path(prepared['preparation']['manifest_asset']['path']).is_file())
        self.assertEqual(prepared['preparation']['timelines'][self.sid]['timeline']['frame_count'], 24)
        p = store.get_project(self.pid)
        self.success('review_replacement', approved=True, checks=self.checks(), fingerprint=replacement.fingerprint(p))
        self.assertTrue(store.get_project(self.pid)['generic_review']['approved'])
        package = self.success('package')
        with zipfile.ZipFile(package['asset']['path']) as archive:
            manifest = json.loads(archive.read('人物引用与原片时间轴.json'))
            self.assertEqual(manifest['segments'][0]['mappings'][0]['target_role_id'], 'warrior')
            self.assertIn('片段_A/参考图/图1_warrior.png', archive.namelist())
        preview = self.success('assemble_preview')
        self.assertEqual(preview['report']['frame_count'], 24)
        self.assertTrue(preview['asset']['meta']['has_audio'])
        workflow.attach(self.pid, self.candidate, 'generated', segment_id=self.sid)
        # Importing a candidate is a deliberate new operation; repeat the
        # explicit plan review instead of relying on an earlier UI checkbox.
        self.approve_plan()
        validated = self.success('validate_candidate')
        self.assertTrue(validated['report']['ready'])
        p = store.get_project(self.pid)
        segment = p['segments'][0]
        self.success('review_candidate', approved=True, checks=self.checks(),
                     fingerprint=replacement_workflow.candidate_fingerprint(p, segment))
        assembled = self.success('assemble')
        self.assertEqual(assembled['report']['frame_count'], 24)
        self.assertTrue(assembled['report']['video_copy'])
        self.assertTrue(assembled['report']['decoded_frames_verified'])
        self.assertTrue(assembled['report']['video_packets_verified'])
        self.assertIsNone(store.get_project(self.pid).get('final_master'))
        self.assertNotIn('master', assembled)
        self.assertEqual(assembled['report']['audio']['mode'], 'single_continuous_original')
        self.assertFalse(assembled['report']['audio']['candidate_audio_used'])
        self.assertIsNotNone(store.get_project(self.pid)['final'])
        self.assert_no_cloud()

    def test_changed_prepared_reference_bytes_or_asset_block_package_and_preview(self):
        for mutation in ('rewrite_bytes', 'replace_asset'):
            with self.subTest(mutation=mutation):
                self.success('prepare_replacement')
                p = store.get_project(self.pid)
                if mutation == 'rewrite_bytes':
                    path = Path(p['segments'][0]['reference']['path'])
                    path.write_bytes(path.read_bytes()+b'changed-after-preparation')
                else:
                    different = store.add_asset(self.pid, self.candidate, 'source_reference', meta=media.probe(self.candidate))
                    p = store.get_project(self.pid)
                    p['segments'][0]['reference'] = different
                    store.put_project(p)
                for action in ('package', 'assemble_preview'):
                    result = self.job(action)
                    self.assertEqual(result['status'], 'FAILED')
                    self.assertIn('原片参考已变化', result['message'])
                self.assertIsNone(store.get_project(self.pid).get('local_preview'))
        self.assert_no_cloud()

    def test_known_derived_video_cannot_be_reimported_as_original_candidate(self):
        derived = store.add_asset(self.pid, self.candidate, 'composited', meta=media.probe(self.candidate))
        copied = self.data / 'different-name.mp4'
        copied.write_bytes(Path(derived['path']).read_bytes())
        workflow.attach(self.pid, copied, 'generated', segment_id=self.sid)
        self.success('prepare_replacement')
        self.approve_plan()
        state = replacement_workflow.result_states(store.get_project(self.pid))['segments'][self.sid]
        self.assertFalse(state['candidate_current'])
        self.assertIsNone(state['candidate_asset_id'])
        result = self.job('validate_candidate')
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('历史适配、合成或导出', result['message'])
        self.assertIsNone(store.get_project(self.pid)['segments'][0]['candidate_review'])
        self.assert_no_cloud()

    def test_candidate_review_requires_current_person_and_region_plan_review(self):
        self.prepare_raw(approve_plan=False)
        p = store.get_project(self.pid)
        self.assertIsNone(p['generic_review'])
        review = self.job('review_candidate', approved=True, checks=self.checks(),
            fingerprint=replacement_workflow.candidate_fingerprint(p, p['segments'][0]))
        self.assertEqual(review['status'], 'FAILED')
        self.assertIn('材料尚未确认', review['message'])
        self.assertIsNone(store.get_project(self.pid)['segments'][0]['candidate_review'])
        self.assert_no_cloud()

    def test_revoking_candidate_review_during_real_assembly_does_not_publish_old_final(self):
        self.prepare_raw()
        self.approve_candidate()
        assemble = raw_delivery.assemble_original
        called = []
        def render_then_revoke(*args, **kwargs):
            receipt = assemble(*args, **kwargs)
            self.assertTrue(Path(receipt['path']).is_file())
            self.success('review_candidate', approved=False)
            called.append(receipt['path'])
            return receipt
        with patch.object(raw_delivery, 'assemble_original', side_effect=render_then_revoke):
            result = self.job('assemble')
        self.assertEqual(len(called), 1)
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('验收发生变化', result['message'])
        p = store.get_project(self.pid)
        self.assertIsNone(p['final'])
        self.assertIsNone(p['segments'][0]['candidate_review'])
        self.assertTrue(Path(called[0]).is_file(), 'Unpublished output remains available for explicit inspection')
        self.assert_no_cloud()

    def test_result_states_follow_raw_check_review_and_final_without_writing(self):
        workflow.attach(self.pid, self.candidate, 'generated', segment_id=self.sid)
        self.success('prepare_replacement')
        self.approve_plan()
        initial = replacement_workflow.result_states(store.get_project(self.pid))['segments'][self.sid]
        self.assertTrue(initial['candidate_current'])
        self.assertFalse(initial['validation_current'])
        self.assertNotIn('composite_current', initial)
        self.assertEqual(self.job('review_candidate', approved=True, checks=self.checks(),
                                  fingerprint=initial['candidate_fingerprint'])['status'], 'FAILED')
        self.success('validate_candidate')
        p = store.get_project(self.pid)
        unchanged = copy.deepcopy(p)
        state = replacement_workflow.result_states(p)
        self.assertEqual(p, unchanged)
        row = state['segments'][self.sid]
        self.assertTrue(row['validation_current'])
        self.assertEqual(row['candidate_asset_id'], p['segments'][0]['generated']['id'])
        self.assertFalse(row['review_current'])
        self.approve_candidate()
        self.assertTrue(replacement_workflow.result_states(store.get_project(self.pid))['segments'][self.sid]['review_current'])
        self.success('assemble')
        p = store.get_project(self.pid)
        self.assertEqual(p['assembly_report']['delivery_policy'], replacement_workflow.DELIVERY_POLICY)
        self.success('assemble_preview')
        self.assertEqual(store.get_project(self.pid)['assembly_report'], p['assembly_report'])
        before = store.project_path(self.pid).read_bytes()
        public = generation.state(self.pid)['result_states']
        self.assertTrue(public['final_current'])
        self.assertIsNone(public['final_reason'])
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assert_no_cloud()

    def test_successful_raw_recheck_clears_acceptance_but_preserves_export(self):
        self.prepare_raw()
        self.approve_candidate()
        self.success('assemble')
        before = store.get_project(self.pid)
        final = before['final']
        fingerprint = replacement_workflow.candidate_fingerprint(before, before['segments'][0])
        report = self.success('validate_candidate')['report']
        self.assertEqual(report['fingerprint'], fingerprint)
        checked = store.get_project(self.pid)
        self.assertEqual(replacement_workflow.candidate_fingerprint(checked, checked['segments'][0]), fingerprint)
        state = generation.state(self.pid)['result_states']
        self.assertTrue(state['segments'][self.sid]['validation_current'])
        self.assertFalse(state['segments'][self.sid]['review_current'])
        self.assertFalse(state['final_current'])
        self.assertEqual(checked['final']['id'], final['id'])
        self.assertTrue(Path(final['path']).exists())
        self.assert_no_cloud()

    def test_late_raw_recheck_preserves_a_newer_acceptance_of_the_same_file(self):
        self.prepare_raw()
        old = self.approve_candidate()['review']
        validate = replacement_media.validate_candidate
        newer = []

        def validate_then_review(*args, **kwargs):
            report = validate(*args, **kwargs)
            if Path(args[1]).resolve() == self.candidate.resolve() and not newer:
                newer.append(self.approve_candidate()['review'])
            return report

        with patch.object(replacement_media, 'validate_candidate', side_effect=validate_then_review):
            self.success('validate_candidate')
        self.assertNotEqual(newer[0]['review_id'], old['review_id'])
        p = store.get_project(self.pid)
        self.assertEqual(p['segments'][0]['candidate_review'], newer[0])
        self.assertTrue(replacement_workflow.result_states(p)['segments'][self.sid]['review_current'])

    def test_validation_job_succeeds_with_ready_false_without_changing_input_fingerprint(self):
        short = self.data/'short-validation-candidate.mkv'
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', self.candidate,
                    '-vf', 'trim=end_frame=12,setpts=PTS-STARTPTS', '-an', '-c:v', 'ffv1', '-threads', '2', short])
        workflow.attach(self.pid, short, 'generated', segment_id=self.sid)
        p = store.get_project(self.pid)
        p['segments'][0]['candidate_review'] = {'approved': True, 'fingerprint': 'previous-version'}
        store.put_project(p)
        fingerprint = replacement_workflow.candidate_fingerprint(p, p['segments'][0])
        job = self.job('validate_candidate')
        self.assertEqual(job['status'], 'SUCCEEDED', 'job completion is not a passing technical result')
        report = job['result']['report']
        self.assertFalse(report['ready'])
        self.assertEqual(report['fingerprint'], fingerprint)
        self.assertEqual(report['segment_id'], self.sid)
        self.assertIn('FRAME_COUNT_MISMATCH', {issue['code'] for issue in report['issues']})
        after = store.get_project(self.pid)
        self.assertEqual(replacement_workflow.candidate_fingerprint(after, after['segments'][0]), fingerprint)
        self.assertEqual(after['segments'][0]['candidate_validation'], report)
        self.assertEqual(after['segments'][0]['candidate_review'], p['segments'][0]['candidate_review'])
        self.assertFalse(generation.state(self.pid)['result_states']['segments'][self.sid]['validation_current'])
        self.assert_no_cloud()

    def test_legacy_composite_and_approval_are_preserved_without_promoting_them(self):
        self.prepare_raw()
        history_path = self.data / 'legacy-composite.mkv'
        history_path.write_bytes(b'old composite artifact')
        composite = store.add_asset(self.pid, history_path, 'composited')
        p = store.get_project(self.pid)
        segment = p['segments'][0]
        legacy_review = {'approved': True, 'fingerprint': replacement_workflow.candidate_fingerprint(p, segment), 'checks': self.checks()}
        segment.update(composited=composite, candidate_review=legacy_review)
        store.put_project(p)
        self.assertFalse(replacement_workflow.result_states(store.get_project(self.pid))['segments'][self.sid]['review_current'])
        self.assertEqual(self.job('assemble')['status'], 'FAILED')
        before = store.project_path(self.pid).read_bytes()
        with patch.object(replacement_media, 'composite_region', side_effect=AssertionError('removed path')) as renderer:
            for action in ('composite_replacement', 'adapt_candidate'):
                blocked = self.job(action)
                self.assertEqual(blocked['status'], 'FAILED')
                self.assertIn('已停用生成画面的二次处理', blocked['message'])
            renderer.assert_not_called()
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assertEqual(history_path.read_bytes(), b'old composite artifact')
        self.approve_candidate()
        self.success('assemble')
        self.assertEqual(store.get_project(self.pid)['segments'][0]['composited'], composite)
        self.assert_no_cloud()

    def test_missing_files_and_legacy_final_are_safe_unverified_states(self):
        self.prepare_raw()
        self.approve_candidate()
        self.success('assemble')
        p = store.get_project(self.pid)
        for group, field in [('segment', 'generated'), ('segment', 'edit_mask'),
                             ('project', 'source'), ('project', 'final')]:
            with self.subTest(field=field):
                broken = copy.deepcopy(p)
                owner = broken['segments'][0] if group == 'segment' else broken
                owner[field]['path'] = str(self.data/(field+'-missing.mp4'))
                original = copy.deepcopy(broken)
                state = replacement_workflow.result_states(broken)
                self.assertFalse(state['final_current'])
                self.assertEqual(broken, original)
        legacy = copy.deepcopy(p)
        legacy['assembly_report'].pop('delivery_policy')
        state = replacement_workflow.result_states(legacy)
        self.assertFalse(state['final_current'])
        self.assertEqual(state['final_reason'], '历史成片 · 按原始回片重新验收后可导出')
        self.assertEqual(legacy['final']['url'], p['final']['url'])
        self.assert_no_cloud()

    def test_reference_current_requires_current_preparation_asset_and_actual_bytes(self):
        self.success('prepare_replacement')
        p = store.get_project(self.pid)
        self.assertTrue(replacement_workflow.result_states(p)['segments'][self.sid]['reference_current'])
        for change in ('version', 'asset_id', 'sha256', 'missing'):
            with self.subTest(change=change):
                old = copy.deepcopy(p)
                if change == 'version':
                    old['preparation']['fingerprint'] = 'previous-material-version'
                elif change == 'asset_id':
                    old['preparation']['references'][self.sid]['id'] = 'different-reference'
                elif change == 'sha256':
                    old['preparation']['references'][self.sid]['sha256'] = 'different-content'
                else:
                    old['segments'][0]['reference']['path'] = str(self.data/'missing-reference.mp4')
                self.assertFalse(replacement_workflow.result_states(old)['segments'][self.sid]['reference_current'])
        self.assert_no_cloud()

    def test_manual_polygon_render_feeds_real_check_prepare_and_versioned_review(self):
        p = store.get_project(self.pid)
        original_mask_id = p['segments'][0]['edit_mask']['id']
        region = {'id': 'head_outline', 'label': '人物一头部', 'mode': 'edit', 'actor_id': 'actor_01',
                  'points': [[.1, .1], [.4, .1], [.4, .5], [.1, .5]]}
        p['segments'][0]['mask_keyframes'] = [
            {'time': 0, 'confirmed': True, 'regions': [region]},
            {'time': 23/24, 'confirmed': True, 'regions': [region]}]
        store.update_project(self.pid, {'expected_revision': p['revision'], 'segments': p['segments']})
        rendered = self.success('render_masks')
        self.assertTrue(rendered['report']['binary_masks_verified'])
        self.assertEqual(rendered['report']['frame_count'], 24)
        self.assertFalse(rendered['report']['automatic_tracking'])
        p = store.get_project(self.pid)
        self.assertNotEqual(p['segments'][0]['edit_mask']['id'], original_mask_id)
        self.assertTrue(Path(p['segments'][0]['mask_preview']['path']).is_file())
        self.assertEqual(p['source_analysis']['cuts'], [])
        checked = self.success('replacement_check')['plan']
        self.assertTrue(checked['ready'], checked['issues'])
        self.success('prepare_replacement')
        self.approve_plan()
        p = store.get_project(self.pid)
        self.assertEqual(p['generic_review']['fingerprint'], replacement.fingerprint(p))
        self.assertTrue(p['generic_review']['approved'])
        self.assert_no_cloud()

    def test_changed_prompt_invalidates_review_and_stale_fingerprint_is_rejected(self):
        self.success('prepare_replacement')
        p = store.get_project(self.pid)
        old_fingerprint = replacement.fingerprint(p)
        self.success('review_replacement', approved=True, checks=self.checks(), fingerprint=old_fingerprint)
        self.success('save_prompt', prompt='编辑视频1，仅替换人物一头部，所有动作和镜头保持原片。')
        p = store.get_project(self.pid)
        self.assertIsNone(p['generic_review'])
        self.assertIsNone(p['preparation'])
        job = self.job('review_replacement', approved=True, checks=self.checks(), fingerprint=old_fingerprint)
        self.assertEqual(job['status'], 'FAILED')
        self.assertIn('变化', job['message'])
        self.assert_no_cloud()

    def test_unchecked_identity_cannot_approve_and_cloud_hold_cannot_be_bypassed(self):
        self.success('prepare_replacement')
        p = store.get_project(self.pid)
        job = self.job('review_replacement', approved=True, checks={'identity': True}, fingerprint=replacement.fingerprint(p))
        self.assertEqual(job['status'], 'FAILED')
        for action in ['submit', 'poll', 'download']:
            blocked = self.job(action, budget_yuan=1000, operator='agent', task_id='fake-task')
            self.assertEqual(blocked['status'], 'FAILED')
            self.assertIn('等待用户', blocked['message'])
        self.assert_no_cloud()

    def test_submission_input_uses_real_short_reference_not_preview_source(self):
        self.success('prepare_replacement')
        p = store.get_project(self.pid)
        s = p['segments'][0]
        reference = replacement_workflow.input_reference(p, s)
        self.assertEqual(reference['name'], s['reference']['name'])
        self.assertAlmostEqual(reference['duration_seconds'], 1, places=3)
        self.assertFalse(reference['uses_full_source_file'])
        self.assertNotEqual(Path(s['reference']['path']), Path(p['source']['path']))
        # Even malformed internal bookkeeping cannot label the original as a clip.
        s['reference'] = {**p['source'], 'kind': 'source_reference'}
        p['preparation']['references'][s['id']] = {'id': s['reference']['id'], 'sha256': s['reference']['sha256']}
        with self.assertRaisesRegex(ValueError, '原片全片'):
            replacement_workflow.input_reference(p, s)
        self.assert_no_cloud()


if __name__ == '__main__':
    unittest.main()
