"""Native decoded samples and original AV offsets; no cloud access."""
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
import wave

from backend import media, generation_audio as ga


class OriginalAudioContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.sources = {}
        for name, video_offset, audio_offset in [('video_late', '0.041', '0'), ('audio_late', '0', '0.041'), ('zero', '0', '0')]:
            path = cls.root / (name + '.mov')
            media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=blue:s=96x64:r=24000/1001:d=3',
                '-itsoffset', audio_offset, '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
                '-map', '0:v:0', '-map', '1:a:0', '-vf', f'settb=1/24000,setpts=PTS+{round(float(video_offset)*24000)}',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', '-fps_mode', 'passthrough',
                '-video_track_timescale', '24000', '-c:a', 'pcm_s32le', path])
            cls.sources[name] = path
        cls.full_source = cls.root / 'full_B.mov'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=blue:s=96x64:r=24000/1001:d=33',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=34', '-map', '0:v:0', '-map', '1:a:0',
            '-vf', 'settb=1/24000,setpts=PTS+984', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2',
            '-fps_mode', 'passthrough', '-video_track_timescale', '24000', '-c:a', 'pcm_s32le', cls.full_source])
        cls.full_source_44100 = cls.root / 'full_B_44100.mov'
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', cls.full_source, '-map', '0:v:0', '-map', '0:a:0',
                    '-c:v', 'copy', '-c:a', 'pcm_s32le', '-ar', '44100', cls.full_source_44100])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_audio_before_video_retains_preroll_and_samples_for_each_segment(self):
        source = self.sources['video_late']
        before = media._hash(source)
        for first in (0, 24):
            report = ga.prepare_original_audio(source, self.root / f'early_{first}.wav', first * 1001 / 24000, (first + 24) * 1001 / 24000)
            self.assertEqual(report['original_audio_video_offset_rational'], '-41/1000')
            self.assertEqual(report['video_first_in_audio_rational'], '41/1000')
            self.assertEqual(report['sample_count'], 48048 + 1968)
            self.assertEqual(report['source_first_sample'], first * 2002)
            self.assertEqual(report['leading_original_gap_samples'], 0)
            self.assertTrue(report['retained_source_samples_exact'])
            self.assertFalse(report['resampled'])
        self.assertEqual(media._hash(source), before)

    def test_audio_after_video_preserves_only_the_real_initial_gap(self):
        source = self.sources['audio_late']
        report = ga.prepare_original_audio(source, self.root / 'late.wav', 0, 1.001, reference=True)
        self.assertEqual(report['original_audio_video_offset_rational'], '41/1000')
        self.assertEqual(report['leading_original_gap_samples'], 1968)
        self.assertEqual(report['sample_count'], 48048)
        with wave.open(report['path'], 'rb') as reader:
            self.assertEqual(reader.readframes(1968), bytes(1968 * 4))
        later = ga.plan_original_audio(source, 1.001, 2.002)
        self.assertEqual(later['leading_original_gap_samples'], 0)
        self.assertEqual(later['source_first_sample'], 48048 - 1968)

    def test_zero_pts_audition_uses_only_common_coverage_without_stretching(self):
        report = ga.prepare_original_audio(self.sources['zero'], self.root / 'audition.wav', 0, 2.002, coverage_seconds=1.4)
        self.assertEqual(report['sample_count'], 67200)
        self.assertEqual(Fraction(report['coverage_duration_rational']), Fraction(7, 5))
        self.assertFalse(report['terminal_silence_added'])
        self.assertFalse(report['strict_final_approved'])

    def test_B_nonzero_selection_keeps_331_frames_and_audio_preroll_at_native_sample_positions(self):
        report = ga.prepare_original_audio(self.full_source, self.root / 'B_original.wav', 250 * 1001 / 24000, 581 * 1001 / 24000, reference=True)
        self.assertEqual(report['source_first_frame'], 251)
        self.assertEqual(report['source_last_frame'], 581)
        self.assertEqual(report['source_first_sample'], 250 * 2002 + 1968)
        self.assertEqual(report['sample_count'], 331 * 2002)
        self.assertEqual(Fraction(report['source_window_start_rational']), Fraction(250 * 1001, 24000) + Fraction(41, 1000))
        self.assertEqual(Fraction(report['selected_video_first_pts_rational']), Fraction(250 * 1001, 24000) + Fraction(41, 1000))
        self.assertAlmostEqual(report['duration_seconds'], 13.805458333333, places=9)
        self.assertEqual(report['video_first_in_audio_rational'], '0')
        self.assertEqual(report['timeline_contract'], 'REFERENCE_VIDEO_ZERO')
        self.assertTrue(report['retained_source_samples_exact'])
        self.assertFalse(report['resampled'])
        audition = ga.plan_original_audio(self.full_source, 250 * 1001 / 24000, 581 * 1001 / 24000)
        self.assertEqual(audition['source_first_sample'], 250 * 2002)
        self.assertEqual(audition['sample_count'], 331 * 2002 + 1968)
        self.assertEqual(audition['video_first_in_audio_rational'], '41/1000')
        self.assertNotEqual(report['method'], audition['method'])

    def test_B_reference_44100_uses_independently_rounded_source_endpoints(self):
        report = ga.prepare_original_audio(self.full_source_44100, self.root / 'B_reference_44100.wav',
            250 * 1001 / 24000, 581 * 1001 / 24000, reference=True)
        self.assertEqual(report['sample_rate'], 44100)
        self.assertEqual(report['source_first_sample'], 461642)
        self.assertEqual(report['source_stop_sample'], 1070463)
        self.assertEqual(report['sample_count'], 1070463 - 461642)
        self.assertEqual(report['video_first_in_audio_rational'], '0')
        self.assertLessEqual(report['start_quantization_error_seconds'], .5 / 44100)
        self.assertLessEqual(report['end_quantization_error_seconds'], .5 / 44100)
        self.assertFalse(report['resampled'])
        self.assertFalse(report['final_audio_source'])

    def test_absent_audio_and_uncovered_audio_fail_without_faking_content(self):
        silent = self.root / 'silent.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', self.sources['zero'], '-map', '0:v:0', '-c:v', 'copy', '-an', silent])
        with self.assertRaisesRegex(media.MediaError, '没有音轨'):
            ga.prepare_original_audio(silent, self.root / 'must-not-exist.wav', 0, 1.001, reference=True)
        with self.assertRaisesRegex(media.MediaError, '1–15秒'):
            ga.prepare_original_audio(self.sources['zero'], self.root / 'too-short.wav', 0, .5005, reference=True)
        self.assertFalse((self.root / 'must-not-exist.wav').exists())


if __name__ == '__main__':
    unittest.main()
