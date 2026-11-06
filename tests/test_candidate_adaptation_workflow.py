"""Archived adaptation and unchanged strict payment-gate contracts."""
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from backend import generation, media, provider, replacement, replacement_workflow as rw, store, workflow


class CandidateAdaptationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.root = Path(cls.fixtures.name)
        cls.source = cls.root / 'source.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24000/1001',
                    '-frames:v', '48', '-an', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', cls.source])
        for name, frames, rate in [('raw', 60, '30'), ('short', 45, '30'), ('native', 48, '24000/1001')]:
            media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', f'testsrc2=s=96x64:r={rate}',
                        '-frames:v', str(frames), '-an', '-c:v', 'ffv1', '-threads', '2', '-pix_fmt', 'bgr0', cls.root / f'{name}.mkv'])
        mask = np.zeros((48, 64, 96), np.uint8)
        mask[:, :, :48] = 255
        import subprocess
        result = subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'gray',
            '-s', '96x64', '-r', '24000/1001', '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-pix_fmt', 'gray',
            '-threads', '2', str(cls.root / 'mask.mkv')], input=mask.tobytes(), capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())
        Image.new('RGB', (96, 96), '#db7d35').save(cls.root / 'target.png')

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patches = [patch.object(store, 'DATA', self.data), patch.object(workflow, 'DATA', self.data),
            patch.object(workflow, 'CONFIG', self.data / 'private-config.json'), patch.object(workflow, 'RUNTIME_KEY', None),
            patch.object(provider.WanClient, '_upload', side_effect=AssertionError('network forbidden')),
            patch.object(provider.WanClient, '_request_json', side_effect=AssertionError('network forbidden')),
            patch.object(workflow, 'config', return_value={'api_key': 'offline-test-key-00000000',
                'base_url': 'https://dashscope.aliyuncs.com/api/v1'})]
        self.mocks = [item.start() for item in self.patches]
        self.pid = store.create_project('独立本地适配验证')['id']
        self.local_source = self.data / 'source.mp4'
        shutil.copyfile(self.source, self.local_source)
        workflow.attach(self.pid, self.local_source, 'source')
        p = store.get_project(self.pid)
        self.sid = p['segments'][0]['id']
        store.update_project(self.pid, {'roles': [{'id': 'person', 'name': '角色', 'images': [], 'color': '#db7d35'}],
            'source_actors': [{'id': 'actor', 'label': '人物', 'scope': 'head', 'source_description': '测试人物',
                'target_role_id': 'person', 'observations': [{'segment_id': self.sid, 'time': 0,
                    'box': [.1, .1, .4, .4], 'visibility': 'visible', 'confirmed': True}]}],
            'prompts': {self.sid: '保持原片动作与镜头，仅替换图1头部'}})
        workflow.attach(self.pid, self.root / 'target.png', 'reference', role='person')
        workflow.attach(self.pid, self.root / 'mask.mkv', 'edit_mask', segment_id=self.sid)
        self.success('prepare_replacement')
        self.local_raw = self.data / 'raw.mkv'
        shutil.copyfile(self.root / 'raw.mkv', self.local_raw)
        workflow.attach(self.pid, self.local_raw, 'raw_candidate', segment_id=self.sid)

    def tearDown(self):
        self.mocks[4].assert_not_called()
        self.mocks[5].assert_not_called()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def job(self, action, **body):
        created = store.start_job(self.pid, action, lambda jid: workflow.action(self.pid,
            {'action': action, 'segment_id': self.sid, **body}, jid))
        return self.wait(created['job_id'])

    def wait(self, jid):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = json.loads(store.job_path(jid).read_text())
            if job['status'] not in ('QUEUED', 'RUNNING'):
                return job
            time.sleep(.01)
        self.fail('offline job timed out')

    def success(self, action, **body):
        job = self.job(action, **body)
        self.assertEqual(job['status'], 'SUCCEEDED', job['message'])
        return job['result']

    def segment(self):
        return store.get_project(self.pid)['segments'][0]

    def test_old_adaptation_keeps_history_and_cannot_be_checked_or_recreated(self):
        historical = store.add_asset(self.pid, self.root / 'native.mkv', 'adapted_candidate')
        p = store.get_project(self.pid)
        s = p['segments'][0]
        s.update(generated=historical, candidate_adaptation={'asset_id': historical['id'], 'current': True},
                 candidate_review={'approved': True, 'fingerprint': 'old-composite-approval'})
        store.put_project(p)
        before = store.project_path(self.pid).read_bytes()
        raw_hash = store.digest(self.local_raw)
        row = rw.result_states(store.get_project(self.pid))['segments'][self.sid]
        self.assertFalse(row['candidate_current'])
        self.assertFalse(row['review_current'])
        self.assertIsNone(row['candidate_asset_id'])
        self.assertIn('历史适配', row['reasons']['candidate'])
        for action, message in [('adapt_candidate', '已停用'), ('validate_candidate', '历史适配'), ('composite_replacement', '已停用')]:
            with self.subTest(action=action):
                failed = self.job(action)
                self.assertEqual(failed['status'], 'FAILED')
                self.assertIn(message, failed['message'])
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assertEqual(store.digest(self.local_raw), raw_hash)
        self.assertTrue(Path(historical['path']).is_file())

    def test_sample_candidate_does_not_enter_delivery(self):
        sample = store.add_asset(self.pid, self.local_raw, 'generated')
        p = store.get_project(self.pid)
        p['segments'][0]['sample_candidate'] = sample
        store.put_project(p)
        row = rw.result_states(store.get_project(self.pid))['segments'][self.sid]
        self.assertFalse(row['candidate_current'])
        self.assertIsNone(row['candidate_asset_id'])
        self.assertEqual(self.job('validate_candidate')['status'], 'FAILED')
        self.assertEqual(self.segment()['sample_candidate'], sample)
        self.assertIsNone(self.segment().get('generated'))

    def test_paid_gates_remain_closed_including_retry_and_legacy_flags(self):
        initial = store.get_project(self.pid)
        started = generation.request_prepare(self.pid, {'mode': 'api', 'material_confirmed': True})
        self.wait(started['preparation']['job_id'])
        state = generation.state(self.pid)
        self.assertEqual(state['preparation']['status'], 'NEEDS_ATTENTION')
        self.assertFalse(state['compatibility']['ready'])
        self.assertIn('MODEL_FPS_INCOMPATIBLE', {item['code'] for item in state['compatibility']['issues']})
        self.assertFalse(state.get('quote'))
        p = store.get_project(self.pid)
        p['cloud_gate'] = {'upload_allowed': True, 'generation_allowed': True}
        store.put_project(p)
        for action in ('submit', 'poll', 'download'):
            with self.subTest(action=action):
                failed = self.job(action, budget_yuan=999, operator='user', task_id='forged')
                self.assertEqual(failed['status'], 'FAILED')
                self.assertIn('23.976', failed['message'])
        fingerprint = replacement.fingerprint(store.get_project(self.pid))
        quote = {'id': 'fake-quote', 'fingerprint': fingerprint, 'expires_at': time.time() + 60,
                 'base_url': 'https://dashscope.aliyuncs.com/api/v1', 'media': [], 'reserved_yuan': 1}
        store.save_json(generation._root(self.pid) / 'preparation.json', {'quote': quote})
        with self.assertRaisesRegex(ValueError, '范围或费用绑定'):
            generation.confirm(self.pid, {'quote_id': quote['id'], 'confirm_upload': True,
                'confirm_generation': True, 'material_confirmed': True, 'approved_budget_yuan': 1})
        run_id = 'offlinefailedrun'
        store.save_json(generation._run_path(self.pid, run_id), {'id': run_id, 'fingerprint': fingerprint,
            'created_at': time.time(), 'segments': [{'segment_id': self.sid, 'status': 'FAILED'}]})
        retry = generation.request_prepare(self.pid, {'mode': 'api', 'retry_of': run_id, 'segment_ids': [self.sid]})
        self.wait(retry['preparation']['job_id'])
        self.assertEqual(generation.state(self.pid)['preparation']['status'], 'NEEDS_ATTENTION')
        self.assertFalse(generation.state(self.pid)['quote'])
        self.assertEqual(store.digest(initial['source']['path']), initial['source']['sha256'])

    def test_configured_key_allows_local_package_but_not_paid_path(self):
        started = generation.request_prepare(self.pid, {'mode': 'export', 'material_confirmed': True})
        self.assertEqual(self.wait(started['preparation']['job_id'])['status'], 'SUCCEEDED')
        state = generation.state(self.pid)
        self.assertEqual(state['preparation']['status'], 'EXPORTED')
        self.assertTrue(state['material_confirmation']['current'])
        self.assertFalse(state['compatibility']['ready'])
        self.assertIsNone(self.segment().get('generated'))


if __name__ == '__main__':
    unittest.main()
