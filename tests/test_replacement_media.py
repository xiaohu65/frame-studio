"""Small, offline media cases for strict source-timeline replacement."""
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from backend import media, replacement_media as rm


class ReplacementMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root/'source.mp4'
        media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24000/1001:d=1.001',
            '-f', 'lavfi', '-i', 'sine=frequency=700:sample_rate=48000:duration=1.1',
            '-filter:v', 'setpts=PTS+0.041/TB', '-fps_mode', 'passthrough', '-c:v', 'libx264',
            '-threads', '2', '-crf', '16', '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.source])
        cls.candidate = cls.root/'candidate.mkv'
        cls.mask = cls.root/'mask.mkv'
        cls.protection = cls.root/'protection.mkv'
        cls.gray_mask = cls.root/'gray.mkv'
        red = np.zeros((24, 64, 96, 3), np.uint8)
        red[..., 0] = 255
        white_left = np.zeros((24, 64, 96), np.uint8)
        white_left[..., :48] = 255
        protection = np.zeros_like(white_left)
        protection[..., :16] = 255
        cls.write_rgb(cls.candidate, red)
        cls.write_gray(cls.mask, white_left)
        cls.write_gray(cls.protection, protection)
        cls.write_gray(cls.gray_mask, np.full_like(white_left, 128))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def write_rgb(cls, path, frames, fps='24000/1001'):
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '96x64', '-r', fps, '-i', 'pipe:0',
            '-an', '-c:v', 'ffv1', '-pix_fmt', 'bgr0', '-threads', '2', str(path)],
            input=frames.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())

    @classmethod
    def write_gray(cls, path, frames, fps='24000/1001'):
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', '96x64', '-r', fps, '-i', 'pipe:0',
            '-an', '-c:v', 'ffv1', '-pix_fmt', 'gray', '-threads', '2', str(path)],
            input=frames.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())

    def destination(self, suffix):
        return self.root/(self.id().split('.')[-1]+suffix)

    def test_prepare_retains_native_rational_rate_count_and_dimensions(self):
        result = rm.prepare_source_clip(self.source, self.destination('.mp4'), 0, 12*1001/24000)
        self.assertEqual(Fraction(result['media']['fps_rational']), Fraction(24000, 1001))
        self.assertEqual(result['timeline']['frame_count'], 12)
        self.assertEqual((result['media']['width'], result['media']['height']), (96, 64))
        self.assertEqual(result['timeline']['frames_retimed'], 0)
        self.assertEqual(result['audio_policy'], 'RESTORE_ORIGINAL_ONCE_AT_ASSEMBLY')

    def test_candidate_with_wan_30fps_is_blocked_in_strict_mode(self):
        bad = self.destination('.mkv')
        self.write_rgb(bad, np.zeros((30, 64, 96, 3), np.uint8), fps='30')
        result = rm.validate_candidate(self.source, bad, 0, 1.001)
        self.assertFalse(result['ready'])
        self.assertIn('FPS_MISMATCH', [i['code'] for i in result['issues']])
        with self.assertRaises(media.MediaError):
            rm.composite_region(self.source, bad, self.mask, self.destination('_composite.mkv'), 0, 1.001)

    def test_variable_frame_rate_source_is_not_silently_resampled(self):
        vfr = self.destination('_vfr.mp4')
        media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-f', 'lavfi',
            '-i', 'testsrc2=s=96x64:r=24:d=1', '-vf', 'setpts=if(lt(N\\,12)\\,N/(24*TB)\\,(N+2)/(24*TB))',
            '-fps_mode', 'vfr', '-c:v', 'libx264', '-threads', '2', str(vfr)])
        self.assertFalse(rm._timing(vfr)['constant_frame_rate'])
        with self.assertRaises(media.MediaError):
            rm.prepare_source_clip(vfr, self.destination('_rejected.mp4'), 0, 1)
        self.assertFalse(self.destination('_rejected.mp4').exists())

    def test_mask_actual_binary_pixels_are_checked(self):
        good = rm.validate_mask(self.source, self.mask, 0, 1.001)
        self.assertTrue(good['ready'])
        self.assertTrue(good['binary'])
        bad = rm.validate_mask(self.source, self.gray_mask, 0, 1.001)
        self.assertFalse(bad['ready'])
        self.assertIn('MASK_NOT_BINARY', [i['code'] for i in bad['issues']])

    def test_composite_protects_original_pixels_after_encoding(self):
        result = rm.composite_region(self.source, self.candidate, self.mask, self.destination('.mkv'),
                                     0, 1.001, protection_mask=self.protection)
        self.assertEqual(result['protected_pixel_max_error'], 0)
        self.assertTrue(result['protected_pixels_verified_after_decode'])
        self.assertFalse(result['candidate_audio_used'])
        self.assertTrue(result['original_audio_available'])
        out = rm._frame(result['path'], 0)
        original = rm._frame(self.source, 0)
        out_array, source_array = np.asarray(out), np.asarray(original)
        np.testing.assert_array_equal(out_array[:, :16], source_array[:, :16])
        np.testing.assert_array_equal(out_array[:, 48:], source_array[:, 48:])
        self.assertTrue(np.all(out_array[:, 16:48, 0] == 255))

    def test_composite_rejects_gray_mask_and_cleans_partial_output(self):
        out = self.destination('.mkv')
        with self.assertRaises(media.MediaError):
            rm.composite_region(self.source, self.candidate, self.gray_mask, out, 0, 1.001)
        self.assertFalse(out.exists())

    def test_contact_sheet_is_evidence_not_tracking_or_masks(self):
        p = {'source': {'path': str(self.source)}, 'segments': [{'id': 'A', 'start': 0, 'end': 1.001}],
             'source_actors': [{'id': 'actor_one', 'label': '人物一', 'target_role_id': 'role_warrior', 'scope': 'head',
                  'observations': [{'segment_id': 'A', 'time': .5, 'box': [.1, .1, .4, .4], 'visibility': 'visible', 'confirmed': True}]}],
             'roles': [{'id': 'role_warrior', 'name': '武者', 'images': []}]}
        result = rm.build_contact_sheet(p, self.destination('.png'))
        self.assertGreaterEqual(result['sample_count'], 3)
        self.assertFalse(result['automatic_tracking'])
        self.assertFalse(result['pixel_mask'])
        self.assertEqual(result['motion_quality'], 'UNVERIFIED')
        self.assertTrue(any(s['actors'] for s in result['samples']))

    def test_strict_assembly_removes_overlap_and_restores_original_audio_once(self):
        a = self.destination('_A.mp4')
        b = self.destination('_B.mp4')
        rm.prepare_source_clip(self.source, a, 0, 16*1001/24000)
        rm.prepare_source_clip(self.source, b, 12*1001/24000, 1.001)
        result = rm.assemble_strict(self.source, [
            {'path': str(a), 'source_start': 0, 'source_end': 16*1001/24000},
            {'path': str(b), 'source_start': 12*1001/24000, 'source_end': 1.001}],
            self.destination('.mkv'), target_end=1.001, preview_path=self.destination('_preview.mp4'))
        self.assertEqual(result['frame_count'], 24)
        self.assertEqual(result['plan'][1]['overlap_removed_frames'], 4)
        self.assertEqual(result['frames_retimed'], 0)
        self.assertTrue(result['media']['has_audio'])
        self.assertEqual(result['audio']['mode'], 'single_continuous_original')
        self.assertFalse(result['audio']['per_clip_audio_joined'])
        self.assertEqual(Fraction(result['preview_media']['fps_rational']), Fraction(24000, 1001))
        self.assertTrue(Path(result['preview_path']).is_file())
        # Compare decoded continuous sound at the visual first-frame origin;
        # equal lengths or container durations alone do not prove beat sync.
        source_audio = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.source), '-map', '0:a:0',
            '-af', f"asetpts=PTS-STARTPTS,atrim=start={result['audio']['source_start_seconds']:.9f}:duration=1.001,asetpts=PTS-STARTPTS",
            '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'], stdout=subprocess.PIPE, check=True).stdout
        assembled_audio = subprocess.run(['ffmpeg', '-v', 'error', '-i', result['path'], '-map', '0:a:0',
            '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1'], stdout=subprocess.PIPE, check=True).stdout
        expected_audio = np.frombuffer(source_audio, np.float32)
        actual_audio = np.frombuffer(assembled_audio, np.float32)
        self.assertEqual(len(actual_audio), len(expected_audio))
        self.assertGreater(float(np.corrcoef(expected_audio, actual_audio)[0, 1]), .999999)

    def test_strict_assembly_gap_and_retiming_are_rejected(self):
        clip = self.destination('_clip.mp4')
        rm.prepare_source_clip(self.source, clip, .25, 1.001)
        with self.assertRaises(media.MediaError):
            rm.assemble_strict(self.source, [{'path': str(clip), 'source_start': .25, 'source_end': 1.001}], self.destination('_gap.mkv'), target_end=1.001)
        with self.assertRaises(media.MediaError):
            rm.assemble_strict(self.source, [{'path': str(clip), 'source_start': .25, 'source_end': 1.001,
                'anchors': [{'generated': 0, 'source': .25}]}], self.destination('_anchors.mkv'), target_end=1.001)


if __name__ == '__main__':
    unittest.main()
