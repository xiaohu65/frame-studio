"""Read-only, PTS-bounded still inspection of changed candidate silhouettes."""
import base64
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
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
from PIL import Image

from backend import candidate_adaptation, mask_editor, mask_review, media, server, store, workflow
from tests.test_mask_review import decode_mask, encode_mask


def encode_rgb(path, frames, rate, first_pts_ticks=0):
    height, width = frames.shape[1:3]
    codec = ['-c:v', 'libx264rgb', '-crf', '0', '-pix_fmt', 'rgb24', '-video_track_timescale', '24000'] if path.suffix == '.mp4' else ['-c:v', 'ffv1', '-pix_fmt', 'bgr0']
    timing = ['-vf', f'settb=1/24000,setpts=PTS+{first_pts_ticks}', '-enc_time_base', '1/24000', '-fps_mode', 'passthrough'] if first_pts_ticks else []
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                    '-s', f'{width}x{height}', '-r', rate, '-i', 'pipe:0', '-an', *timing, *codec,
                    '-threads', '2', str(path)], input=frames.tobytes(), check=True, stderr=subprocess.PIPE)


def png_array(data):
    return np.array(Image.open(BytesIO(base64.b64decode(data.split(',')[1]))))


class CandidateMaskDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        root = Path(cls.fixtures.name)
        cls.source, cls.candidate = root/'source.mp4', root/'large_crown_candidate.mkv'
        source = np.empty((16, 64, 96, 3), np.uint8)
        source[:] = [26, 30, 44]
        source[:, 25:61, 30:54] = [140, 90, 70]
        source[:, 18:31, 36:49] = [200, 145, 100]
        source[:, 34:42, 48:64] = [80, 160, 210]  # Original foreground occluder.
        candidate = np.empty((13, 32, 48, 3), np.uint8)
        candidate[:] = [38, 85, 105]  # Deliberately redrawn background/lighting.
        for frame in range(13):
            shift = frame//4
            candidate[frame, 14:31, 12+shift:30+shift] = [210, 110, 40]
            candidate[frame, 4:15, 17+shift:24+shift] = [250, 215, 60]  # Taller crown.
            candidate[frame, 17:26, 5+shift:39+shift] = [205, 125, 45]  # Wider sleeves.
        encode_rgb(cls.source, source, '24000/1001', first_pts_ticks=984)
        encode_rgb(cls.candidate, candidate, '30')
        cls.edit, cls.protect = np.zeros((11, 64, 96), np.uint8), np.zeros((11, 64, 96), np.uint8)
        cls.edit[:, 18:61, 29:55] = 255
        cls.protect[:, 34:42, 48:64] = 255
        cls.edit_path, cls.protect_path = root/'edit.mkv', root/'protect.mkv'
        encode_mask(cls.edit_path, cls.edit)
        encode_mask(cls.protect_path, cls.protect)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(store, 'DATA', self.root), patch.object(workflow, 'DATA', self.root),
                        patch.object(store, 'start_job', return_value={'job_id': 'automatic-preview-not-started'})]
        for item in self.patches:
            item.start()
        self.pid = store.create_project('仅隔离候选诊断')['id']
        workflow.attach(self.pid, self.source, 'source')
        p, s = self.project()
        self.sid = s['id']
        rate = float(Fraction('24000/1001'))
        s.update(start=2/rate, end=13/rate)
        store.put_project(p)
        workflow.attach(self.pid, self.edit_path, 'edit_mask', segment_id=self.sid)
        workflow.attach(self.pid, self.protect_path, 'protection_mask', segment_id=self.sid)
        workflow.attach(self.pid, self.candidate, 'raw_candidate', segment_id=self.sid)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def project(self):
        p = store.get_project(self.pid)
        return p, p['segments'][0]

    def frame(self, frame):
        p, s = self.project()
        diagnostic = mask_review.diagnostic_candidate(p, s, s['raw_candidate']['id'])
        return mask_review.frame_data(p, s, frame, mask_review.context(p, s)['fingerprint'], diagnostic['id'], diagnostic['fingerprint'])

    def patch_request(self):
        p, s = self.project()
        diagnostic = mask_review.diagnostic_candidate(p, s, s['raw_candidate']['id'])
        return {'action': 'patch_masks', 'segment_id': self.sid, 'mask_fingerprint': mask_review.context(p, s)['fingerprint'],
                'diagnostic_candidate_id': diagnostic['id'], 'diagnostic_candidate_fingerprint': diagnostic['fingerprint'],
                'operations': [{'layer': 'edit', 'tool': 'brush', 'radius': 3, 'points': [[.44, .16]], 'first_frame': 3, 'last_frame': 3}]}

    def test_read_only_diagnostics_show_exact_legal_range_without_approving_adaptation(self):
        before = store.project_path(self.pid).read_bytes()
        p, s = self.project()
        choices = mask_review.status(p, s)['diagnostic_candidates']
        self.assertEqual(len(choices), 1)
        item = choices[0]
        self.assertEqual(item['available_source_ranges'][0]['first_frame'], 3)
        self.assertEqual(item['available_source_ranges'][0]['last_frame'], 12)
        self.assertFalse(item['full_timeline_check']['ready'])
        self.assertIn('末端覆盖不足', item['full_timeline_check']['reason'])
        result = self.frame(3)
        self.assertEqual(result['candidate']['candidate_frame'], 1)
        self.assertEqual(result['candidate']['target_time_rational'], '0')
        self.assertEqual(result['candidate']['candidate_native_size'], [48, 32])
        self.assertEqual(png_array(result['images']['candidate']).shape, (64, 96, 3))
        for name in ('strict_final_approved', 'composite_ready', 'cloud_mask_constraint', 'candidate_audio_used'):
            self.assertFalse(result['candidate'][name])
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        self.assertIsNone(self.project()[1].get('candidate_validation'))

    def test_uncovered_endpoint_is_not_frozen_or_given_a_candidate_image(self):
        result = self.frame(13)
        self.assertFalse(result['candidate']['available'])
        self.assertNotIn('candidate', result['images'])
        self.assertNotIn('candidate_frame', result['candidate'])
        self.assertIn('不冻结末帧', result['candidate']['unavailable_reason'])
        p, s = self.project()
        with self.assertRaisesRegex(media.MediaError, '末端覆盖不足'):
            candidate_adaptation.plan_adaptation(self.source, self.candidate, s['start'], s['end'], sample_only=True)

    def test_large_crown_sleeves_shift_and_changed_background_remain_visible(self):
        first, later = self.frame(3), self.frame(10)
        source, candidate = png_array(first['images']['source']), png_array(first['images']['candidate'])
        self.assertGreater(int(candidate[12, 42, 0]), 230)  # Crown lies outside old head boundary.
        self.assertEqual(self.edit[0, 12, 42], 0)
        self.assertGreater(int(candidate[44, 16, 0]), 180)  # Sleeve outside original body.
        self.assertEqual(self.edit[0, 44, 16], 0)
        self.assertFalse(np.array_equal(source[3, 3], candidate[3, 3]))
        self.assertFalse(np.array_equal(candidate, png_array(later['images']['candidate'])))
        self.assertEqual(first['candidate']['source_frame'], 3)
        self.assertEqual(later['candidate']['source_frame'], 10)

    def test_deliberate_patch_keeps_untouched_frames_protection_and_original_candidate(self):
        p, s = self.project()
        source_hash, mask_hash, candidate_hash = media._hash(self.source), media._hash(self.edit_path), media._hash(self.candidate)
        old_mask, candidate_id = s['edit_mask']['id'], s['raw_candidate']['id']
        result = workflow.action(self.pid, self.patch_request(), store.uid())
        p, s = self.project()
        edit, protect = decode_mask(s['edit_mask']['path']), decode_mask(s['protection_mask']['path'])
        self.assertTrue(np.array_equal(protect, self.protect))
        self.assertTrue(np.array_equal(edit[1:], self.edit[1:]))
        self.assertTrue((edit[0] != self.edit[0]).any())
        self.assertEqual(s['raw_candidate']['id'], candidate_id)
        self.assertIn(old_mask, [asset['id'] for asset in p['assets']])
        self.assertFalse(result['report']['diagnostic_candidate']['strict_final_approved'])
        self.assertEqual(result['report']['diagnostic_candidate']['id'], candidate_id)
        self.assertIsNone(s.get('candidate_validation'))
        self.assertIsNone(s.get('composite'))
        self.assertEqual((media._hash(self.source), media._hash(self.edit_path), media._hash(self.candidate)), (source_hash, mask_hash, candidate_hash))
        version = mask_review.status(p, s)['versions'][0]
        workflow.action(self.pid, {'action': 'restore_mask_version', 'segment_id': self.sid, 'mask_fingerprint': mask_review.context(p, s)['fingerprint'], 'version_id': version['id'], 'target': 'base'}, store.uid())
        self.assertEqual(self.project()[1]['edit_mask']['id'], old_mask)
        self.assertEqual(self.project()[1]['raw_candidate']['id'], candidate_id)

    def test_stale_candidate_bytes_are_rejected_even_with_unchanged_catalogue_hash(self):
        p, s = self.project()
        diagnostic = mask_review.diagnostic_candidate(p, s, s['raw_candidate']['id'])
        other = self.root/'other.mkv'
        encode_rgb(other, np.full((13, 32, 48, 3), 90, np.uint8), '30')
        s['raw_candidate']['path'] = str(other)
        with self.assertRaisesRegex(ValueError, '已改变'):
            mask_review.frame_data(p, s, 3, mask_review.context(p, s)['fingerprint'], diagnostic['id'], diagnostic['fingerprint'])

    def test_candidate_replaced_during_patch_cannot_commit_old_diagnostic_reference(self):
        request = self.patch_request()
        original_id = self.project()[1]['edit_mask']['id']
        patcher = mask_editor.apply_mask_patch
        def replace_after_render(*args, **kwargs):
            result = patcher(*args, **kwargs)
            workflow.attach(self.pid, self.candidate, 'raw_candidate', segment_id=self.sid)
            return result
        with patch.object(mask_editor, 'apply_mask_patch', side_effect=replace_after_render):
            with self.assertRaisesRegex(ValueError, '已更换'):
                workflow.action(self.pid, request, store.uid())
        self.assertEqual(self.project()[1]['edit_mask']['id'], original_id)
        self.assertFalse(any(asset['kind'] == 'mask_patch_version' for asset in self.project()[0]['assets']))

    def test_aspect_mismatch_is_visible_and_never_stretched_to_fit(self):
        mismatch = self.root/'mismatch.mkv'
        encode_rgb(mismatch, np.zeros((13, 28, 48, 3), np.uint8), '30')
        workflow.attach(self.pid, mismatch, 'raw_candidate', segment_id=self.sid)
        p, s = self.project()
        state = mask_review.status(p, s)
        self.assertIn('宽高比', state['diagnostic_candidates'][0]['error'])
        self.assertNotIn('fingerprint', state['diagnostic_candidates'][0])
        result = mask_review.frame_data(p, s, 3, state['fingerprint'])
        self.assertIn('source', result['images'])
        self.assertNotIn('candidate', result['images'])

    def test_frame_api_requires_candidate_binding_and_returns_diagnostic_only(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            p, s = self.project()
            diagnostic = mask_review.status(p, s)['diagnostic_candidates'][0]
            root = f'http://127.0.0.1:{httpd.server_port}/api/projects/{self.pid}/mask-review/{self.sid}/frame?'
            query = {'frame': 3, 'fingerprint': mask_review.context(p, s)['fingerprint'], 'candidate_id': diagnostic['id']}
            with self.assertRaises(HTTPError) as caught:
                urlopen(root+urlencode(query))
            self.assertEqual(caught.exception.code, 400)
            query['candidate_fingerprint'] = diagnostic['fingerprint']
            with urlopen(root+urlencode(query)) as response:
                result = json.load(response)
            self.assertTrue(result['candidate']['diagnostic_only'])
            self.assertFalse(result['candidate']['strict_final_approved'])
            self.assertFalse(result['candidate']['full_timeline_check']['ready'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_segment_b_nonzero_origin_and_source_pts_offset_never_use_segment_a_candidate(self):
        source = self.root/'long_source.mp4'
        source_pixels = np.full((270, 64, 96, 3), [26, 30, 44], np.uint8)
        encode_rgb(source, source_pixels, '24000/1001', first_pts_ticks=984)
        pid = store.create_project('仅临时B段映射')['id']
        workflow.attach(pid, source, 'source')
        p = store.get_project(pid)
        rate = Fraction('24000/1001')
        p['segments'] = [{'id': 'A', 'start': 0, 'end': float(250/rate)},
                         {'id': 'B', 'start': float(250/rate), 'end': float(261/rate)}]
        store.put_project(p)
        workflow.attach(pid, self.candidate, 'raw_candidate', segment_id='A')
        workflow.attach(pid, self.edit_path, 'edit_mask', segment_id='B')
        workflow.attach(pid, self.protect_path, 'protection_mask', segment_id='B')
        p = store.get_project(pid)
        a, b = p['segments']
        self.assertEqual(mask_review.status(p, b)['diagnostic_candidates'], [])
        with self.assertRaisesRegex(ValueError, '不属于当前片段'):
            mask_review.diagnostic_candidate(p, b, a['raw_candidate']['id'])
        candidate_b = self.root/'candidate_b.mkv'
        encode_rgb(candidate_b, np.full((13, 32, 48, 3), [180, 40, 220], np.uint8), '30')
        workflow.attach(pid, candidate_b, 'raw_candidate', segment_id='B')
        p = store.get_project(pid)
        b = p['segments'][1]
        diagnostic = mask_review.diagnostic_candidate(p, b, b['raw_candidate']['id'])
        self.assertEqual(Fraction(diagnostic['source_first_pts_rational']), Fraction(41, 1000))
        self.assertEqual(diagnostic['selected_source_first_frame'], 251)
        self.assertAlmostEqual(diagnostic['selected_source_first_seconds'], 250*1001/24000)
        frame = mask_review.frame_data(p, b, 251, mask_review.context(p, b)['fingerprint'], diagnostic['id'], diagnostic['fingerprint'])
        self.assertEqual(frame['candidate']['candidate_frame'], 1)
        self.assertEqual(frame['candidate']['target_time_rational'], '0')
        self.assertEqual(frame['segment_frame'], 1)
        self.assertTrue(np.array_equal(png_array(frame['images']['candidate'])[0, 0], [180, 40, 220]))
        with self.assertRaisesRegex(ValueError, '不属于当前片段'):
            mask_review.frame_data(p, b, 251, mask_review.context(p, b)['fingerprint'], a['raw_candidate']['id'], diagnostic['fingerprint'])


if __name__ == '__main__':
    unittest.main()
