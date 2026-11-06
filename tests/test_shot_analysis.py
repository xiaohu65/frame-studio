"""Scene-score detection uses local media only, never inference APIs."""
from pathlib import Path
import tempfile
import unittest

from backend import media, shot_analysis


class ShotAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hard_cut_keeps_original_frame_and_normalized_pts(self):
        source = self.root/'hard_cut.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=24:d=0.5',
            '-f', 'lavfi', '-i', 'color=blue:s=96x64:r=24:d=0.5',
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS+1/TB[v]',
            '-map', '[v]', '-fps_mode', 'passthrough', '-c:v', 'libx264', '-threads', '2', source])
        before = media._hash(source)
        result = shot_analysis.analyze(source)
        self.assertEqual(result['frame_count'], 24)
        self.assertEqual(result['source_sha256'], before)
        self.assertEqual(media._hash(source), before)
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['automatic_identity_assignment'])
        self.assertEqual(len(result['cuts']), 1)
        self.assertEqual(result['cuts'][0]['frame'], 13)
        self.assertAlmostEqual(result['cuts'][0]['time'], .5, places=5)
        self.assertGreaterEqual(result['cuts'][0]['score'], .30)

    def test_static_clip_has_no_cut_candidates(self):
        source = self.root/'static.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=24000/1001:d=1.001',
            '-c:v', 'libx264', '-threads', '2', source])
        result = shot_analysis.analyze(source)
        self.assertEqual(result['frame_count'], 24)
        self.assertEqual(result['cuts'], [])
        self.assertTrue(result['requires_review'])

    def test_invalid_threshold_is_rejected(self):
        source = self.root/'static.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=red:s=96x64:r=24:d=0.1',
            '-c:v', 'libx264', '-threads', '2', source])
        for threshold in [0, -1, 2, float('nan'), True]:
            with self.assertRaises(ValueError):
                shot_analysis.analyze(source, threshold=threshold)


if __name__ == '__main__':
    unittest.main()
