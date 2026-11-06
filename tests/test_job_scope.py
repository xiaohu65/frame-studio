"""Job scope must exist in the atomic queued record before a worker runs."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import store


class JobScopeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.addCleanup(self.folder.cleanup)
        self.data_patch = patch.object(store, 'DATA', self.root)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.queued = []

    def immediate_thread(self, *, target, daemon):
        def start():
            paths = list((self.root/'jobs').glob('*.json'))
            self.assertEqual(len(paths), 1)
            self.queued.append(json.loads(paths[0].read_text()))
            target()
        return SimpleNamespace(start=start)

    def test_optional_scope_is_durable_before_thread_start_and_retained_by_worker(self):
        seen = []
        def worker(jid):
            seen.append(json.loads(store.job_path(jid).read_text()))
            return {'message': '完成'}
        with patch.object(store.threading, 'Thread', self.immediate_thread):
            result = store.start_job('project', 'composite_replacement', worker, segment_id='B',
                                     input_candidate_id='candidate-B', input_material_fingerprint='material-version')
        self.assertEqual(self.queued[0]['status'], 'QUEUED')
        for record in (self.queued[0], seen[0], json.loads(store.job_path(result['job_id']).read_text())):
            self.assertEqual(record['segment_id'], 'B')
            self.assertEqual(record['input_candidate_id'], 'candidate-B')
            self.assertEqual(record['input_material_fingerprint'], 'material-version')
        self.assertEqual(seen[0]['status'], 'RUNNING')
        self.assertEqual(set(result), {'job_id', 'status'})

    def test_failure_keeps_its_original_segment_and_candidate_scope(self):
        def fail(jid):
            raise ValueError('此片段检查失败')
        with patch.object(store.threading, 'Thread', self.immediate_thread):
            result = store.start_job('project', 'validate_candidate', fail, segment_id='A', input_candidate_id='original')
        job = json.loads(store.job_path(result['job_id']).read_text())
        self.assertEqual(job['status'], 'FAILED')
        self.assertEqual(job['segment_id'], 'A')
        self.assertEqual(job['input_candidate_id'], 'original')

    def test_default_project_jobs_keep_their_existing_unscoped_shape(self):
        with patch.object(store.threading, 'Thread', self.immediate_thread):
            result = store.start_job('project', 'assemble', lambda jid: {})
        for record in (self.queued[0], json.loads(store.job_path(result['job_id']).read_text())):
            for field in ('segment_id', 'input_candidate_id', 'input_material_fingerprint'):
                self.assertNotIn(field, record)


if __name__ == '__main__':
    unittest.main()
