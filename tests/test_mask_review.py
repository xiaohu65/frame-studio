"""Native-frame imported-mask review, lossless local patches, and stale jobs.

All files and project records live in temporary roots; no supplier is used.
"""
import base64
import copy
from fractions import Fraction
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from backend import mask_editor, mask_review, media, replacement_media, server, store, workflow


def encode_mask(path, frames, rate='24000/1001'):
    height, width = frames.shape[1:]
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'gray',
                    '-s', f'{width}x{height}', '-r', rate, '-i', 'pipe:0', '-an', '-c:v', 'ffv1',
                    '-level', '3', '-pix_fmt', 'gray', '-threads', '2', str(path)],
                   input=frames.tobytes(), check=True, stderr=subprocess.PIPE)


def decode_mask(path):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-pix_fmt', 'gray',
                             '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'], check=True, stdout=subprocess.PIPE)
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 64, 96)


class ImportedMaskReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.root = Path(cls.fixture.name)
        cls.source = cls.root/'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24000/1001',
                        '-frames:v', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '2', str(cls.source)], check=True)
        cls.edit = np.zeros((8, 64, 96), np.uint8)
        cls.protect = np.zeros_like(cls.edit)
        for index in range(8):
            cls.edit[index, 5:55, 5+index:45+index] = 255
            cls.edit[index, 60, 90-index] = 255  # Isolated distant detail must survive.
            cls.protect[index, 15:25, 20:30] = 255
        cls.edit_path, cls.protect_path = cls.root/'edit.mkv', cls.root/'protect.mkv'
        encode_mask(cls.edit_path, cls.edit)
        encode_mask(cls.protect_path, cls.protect)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [patch.object(store, 'DATA', self.data), patch.object(workflow, 'DATA', self.data),
                        patch.object(store, 'start_job', return_value={'job_id': 'queued'})]
        self.mocks = [p.start() for p in self.patches]
        self.pid = store.create_project('临时遮罩测试')['id']
        workflow.attach(self.pid, self.source, 'source')
        p = store.get_project(self.pid)
        s = p['segments'][0]
        self.sid = s['id']
        rate = float(Fraction('24000/1001'))
        s.update(start=2/rate, end=10/rate)
        store.put_project(p)
        workflow.attach(self.pid, self.edit_path, 'edit_mask', segment_id=self.sid)
        workflow.attach(self.pid, self.protect_path, 'protection_mask', segment_id=self.sid)
        self.out = self.data/'outputs'
        self.out.mkdir()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def project(self):
        p = store.get_project(self.pid)
        return p, p['segments'][0]

    def stroke(self, **extra):
        return {'layer': 'edit', 'tool': 'erase', 'radius': 3, 'first_frame': 5, 'last_frame': 5,
                'points': [[.2, .5], [.28, .5]], **extra}

    def action(self, name, **extra):
        p, s = self.project()
        return workflow.action(self.pid, {'action': name, 'segment_id': self.sid,
            'mask_fingerprint': mask_review.context(p, s)['fingerprint'], **extra}, store.uid())

    def test_exact_frames_use_source_indices_and_hide_stale_previews(self):
        p, s = self.project()
        ctx = mask_review.context(p, s)
        self.assertEqual((ctx['first_frame'], ctx['last_frame'], ctx['frame_count']), (3, 10, 8))
        frame = mask_review.frame_data(p, s, 5, ctx['fingerprint'])
        self.assertEqual((frame['source_frame'], frame['segment_frame']), (5, 3))
        loaded = np.array(Image.open(BytesIO(base64.b64decode(frame['images']['edit_mask'].split(',')[1]))))
        self.assertTrue(np.array_equal(loaded, self.edit[2]))
        result = self.action('render_mask_preview')
        p, s = self.project()
        self.assertTrue(mask_review.status(p, s)['technical_ready'])
        self.assertEqual(media.probe(result['asset']['path'])['fps_rational'], '24000/1001')
        with self.assertRaisesRegex(ValueError, '超出'):
            mask_review.frame_data(p, s, 2, ctx['fingerprint'])
        s['end'] -= 1/float(Fraction('24000/1001'))
        self.assertIsNone(mask_review.status(p, s)['preview'])
        with self.assertRaisesRegex(ValueError, '已改变'):
            mask_review.frame_data(p, s, 5, ctx['fingerprint'])

    def test_local_pixel_patch_preserves_every_unselected_frame_and_other_layer(self):
        p, s = self.project()
        originals = {a: media._hash(path) for a, path in [('source', self.source), ('edit', self.edit_path), ('protect', self.protect_path)]}
        result = mask_editor.apply_mask_patch(p, s, self.out, 'one_frame', [self.stroke()], mask_review.context(p, s)['fingerprint'])
        edit, protect = decode_mask(result['edit_mask_path']), decode_mask(result['protection_mask_path'])
        self.assertEqual(edit.shape, self.edit.shape)
        self.assertTrue(np.array_equal(protect, self.protect))
        for index in range(8):
            if index != 2:
                self.assertTrue(np.array_equal(edit[index], self.edit[index]), f'untouched frame {index}')
        changed = edit[2] != self.edit[2]
        self.assertTrue(changed.any())
        self.assertFalse(changed[:25].any())
        self.assertFalse(changed[:, 35:].any())
        self.assertTrue(np.array_equal(edit[:, 60], self.edit[:, 60]))
        self.assertEqual(set(np.unique(edit)), {0, 255})
        self.assertEqual(result['report']['changed_source_frames'], [5])
        self.assertTrue(result['report']['protection_priority'])
        for a, path in [('source', self.source), ('edit', self.edit_path), ('protect', self.protect_path)]:
            self.assertEqual(originals[a], media._hash(path))
        for key in ('edit_mask_path', 'protection_mask_path', 'preview_path'):
            check = replacement_media.validate_candidate(self.source, result[key], s['start'], s['end'])
            self.assertTrue(check['ready'], check['issues'])

    def test_short_interval_and_protection_layer_are_independent(self):
        p, s = self.project()
        operations = [self.stroke(layer='protect', first_frame=6, last_frame=7, points=[[.75, .75]], tool='brush')]
        result = mask_editor.apply_mask_patch(p, s, self.out, 'protect_range', operations, mask_review.context(p, s)['fingerprint'])
        edit, protect = decode_mask(result['edit_mask_path']), decode_mask(result['protection_mask_path'])
        self.assertTrue(np.array_equal(edit, self.edit))
        self.assertEqual(result['report']['changed_source_frames'], [6, 7])
        for index in (0, 1, 2, 5, 6, 7):
            self.assertTrue(np.array_equal(protect[index], self.protect[index]))
        original = np.zeros((1, 1, 3), np.uint8)
        tinted = mask_review._overlay(original, np.full((1, 1), 255, np.uint8), np.full((1, 1), 255, np.uint8))
        self.assertTrue(np.array_equal(tinted[0, 0], [32, 86, 128]))

    def test_rejected_patch_does_not_replace_files_or_leave_partial_outputs(self):
        p, s = self.project()
        ctx = mask_review.context(p, s)
        cases = [self.stroke(first_frame=2), self.stroke(radius=0), self.stroke(layer='both'),
                 self.stroke(points=[[float('nan'), .5]]), self.stroke(first_frame=True), self.stroke(last_frame=11)]
        for index, invalid in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                mask_editor.apply_mask_patch(p, s, self.out, 'invalid_'+str(index), [invalid], ctx['fingerprint'])
        with self.assertRaisesRegex(ValueError, '没有改变'):
            mask_editor.apply_mask_patch(p, s, self.out, 'noop', [self.stroke(points=[[.9, .1]])], ctx['fingerprint'])
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(self.project()[1]['edit_mask']['id'], s['edit_mask']['id'])
        long_context = {**ctx, 'last_frame': 100}
        with self.assertRaisesRegex(ValueError, '最多2秒'):
            mask_editor.validate_patch_operations([self.stroke(first_frame=3, last_frame=53)], long_context)

    def test_binary_validation_and_live_digest_override_catalogue_claims(self):
        p, s = self.project()
        ctx = mask_review.context(p, s)
        gray = self.edit.copy()
        gray[0, 0, 0] = 123
        path = self.data/'bad.mkv'
        encode_mask(path, gray)
        s['edit_mask']['path'] = str(path)  # Keep deliberately stale id/hash.
        self.assertNotEqual(mask_review.context(p, s)['fingerprint'], ctx['fingerprint'])
        with self.assertRaisesRegex(ValueError, '已改变'):
            mask_review.frame_data(p, s, 3, ctx['fingerprint'])
        with self.assertRaisesRegex(ValueError, '灰色|二值'):
            mask_review.render_preview(p, s, self.out, 'gray')
        self.assertEqual(list(self.out.iterdir()), [])

    def test_patch_is_a_separate_version_and_undo_keeps_old_master(self):
        p, s = self.project()
        original_edit, original_protect = copy.deepcopy(s['edit_mask']), copy.deepcopy(s['protection_mask'])
        p['generic_review'] = {'approved': True}
        p['preparation'] = {'old': True}
        s.update(candidate_review={'approved': True}, candidate_validation={'ready': True}, composite={'old': True})
        store.put_project(p)
        result = self.action('patch_masks', operations=[self.stroke()])
        p, s = self.project()
        self.assertNotEqual(s['edit_mask']['id'], original_edit['id'])
        self.assertIsNone(s['candidate_review'])
        self.assertIsNone(s['candidate_validation'])
        self.assertIsNone(s['composite'])
        self.assertIsNone(p['generic_review'])
        self.assertIsNone(p['preparation'])
        versions = mask_review.status(p, s)['versions']
        self.assertEqual(len(versions), 1)
        self.assertTrue(versions[0]['is_current'])
        self.assertTrue(mask_review.status(p, s)['technical_ready'])
        derived_id = s['edit_mask']['id']
        self.action('restore_mask_version', version_id=versions[0]['id'], target='base')
        p, s = self.project()
        self.assertEqual(s['edit_mask'], original_edit)
        self.assertEqual(s['protection_mask'], original_protect)
        self.assertIsNone(s['mask_preview'])
        self.assertTrue(Path(result['asset']['path']).exists())
        self.action('restore_mask_version', version_id=versions[0]['id'], target='derived')
        self.assertEqual(self.project()[1]['edit_mask']['id'], derived_id)

    def test_older_async_result_cannot_register_or_overwrite_new_import(self):
        old_p, old_s = self.project()
        renderer = mask_review.render_preview
        replacement_path = self.data/'replacement.mkv'
        encode_mask(replacement_path, np.flip(self.edit, axis=2).copy())
        def change_after_render(*args, **kwargs):
            result = renderer(*args, **kwargs)
            workflow.attach(self.pid, replacement_path, 'edit_mask', segment_id=self.sid)
            return result
        with patch.object(mask_review, 'render_preview', side_effect=change_after_render):
            with self.assertRaisesRegex(ValueError, '已改变'):
                self.action('render_mask_preview')
        p, s = self.project()
        self.assertEqual(s['edit_mask']['path'], str(replacement_path.resolve()))
        self.assertIsNone(s['mask_preview'])
        self.assertFalse(any(asset['kind'] == 'mask_preview' for asset in p['assets']))

    def test_optional_protection_can_be_patched_and_undone_without_changing_base(self):
        p, s = self.project()
        s['protection_mask'] = None
        store.put_project(p)
        self.action('patch_masks', operations=[self.stroke(layer='protect', tool='brush', points=[[.75, .75]])])
        p, s = self.project()
        self.assertTrue(np.array_equal(decode_mask(s['edit_mask']['path']), self.edit))
        version = mask_review.status(p, s)['versions'][0]
        derived_path = Path(s['protection_mask']['path'])
        self.action('restore_mask_version', version_id=version['id'], target='base')
        self.assertIsNone(self.project()[1]['protection_mask'])
        derived_path.write_bytes(b'corrupted temporary derived file')
        with self.assertRaisesRegex(ValueError, '改变或缺失'):
            self.action('restore_mask_version', version_id=version['id'], target='derived')
        self.assertIsNone(self.project()[1]['protection_mask'])

    def test_import_invalidates_preview_and_queues_bound_local_job(self):
        self.action('render_mask_preview')
        workflow.attach(self.pid, self.edit_path, 'edit_mask', segment_id=self.sid)
        p, s = self.project()
        self.assertIsNone(s['mask_preview'])
        self.assertIsNone(s['mask_render_report'])
        args, kwargs = self.mocks[-1].call_args
        self.assertEqual(args[:2], (self.pid, 'render_mask_preview'))
        self.assertEqual(kwargs['segment_id'], self.sid)
        args[2](store.uid())
        self.assertTrue(mask_review.status(*self.project())['technical_ready'])

    def test_registered_frame_api_rejects_old_fingerprint_and_cross_site(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            root = f'http://127.0.0.1:{httpd.server_port}/api/projects/{self.pid}/mask-review/{self.sid}'
            with urlopen(root) as response:
                status = json.load(response)
            with urlopen(root+'/frame?frame=5&fingerprint='+status['fingerprint']) as response:
                frame = json.load(response)
            self.assertEqual(frame['source_frame'], 5)
            self.assertEqual(frame['fingerprint'], status['fingerprint'])
            with self.assertRaises(HTTPError) as caught:
                urlopen(root+'/frame?frame=5&fingerprint=old')
            self.assertEqual(caught.exception.code, 400)
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(root, headers={'Origin': 'https://outside.example', 'Sec-Fetch-Site': 'cross-site'}))
            self.assertEqual(caught.exception.code, 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
