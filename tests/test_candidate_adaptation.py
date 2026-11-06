"""Deterministic offline tests: exact frame timeline, grid limits, and failure safety."""
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from backend import candidate_adaptation as ca, media, replacement_media as rm


class CandidateAdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.source = cls.root / 'source.mov'
        cls.candidate = cls.root / 'candidate.mov'
        cls.write(cls.source, 240, '24000/1001', offset='3')
        cls.write(cls.candidate, 300, '30', offset='7')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def write(cls, path, count, fps, width=64, height=40, offset='0'):
        frames = np.zeros((count, height, width, 3), dtype=np.uint8)
        # Each frame has a lossless, independently decodable index label.
        for i in range(count):
            frames[i, :, :, 0] = i % 256
            frames[i, :, :, 1] = i // 256
            frames[i, :, (i % width), 2] = 255
        proc = subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{width}x{height}', '-r', fps, '-i', 'pipe:0', '-vf', f'setpts=PTS+({offset})/TB',
            '-an', '-c:v', 'png', '-threads', '1', '-fps_mode', 'passthrough',
            '-video_track_timescale', '24000' if fps == '24000/1001' else '30000', str(path)],
            input=frames.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode())

    def output(self, suffix='.mkv'):
        return self.root / (self.id().split('.')[-1] + suffix)

    def test_240_frames_1001_100_duration_nearest_pts_and_lossless_identity(self):
        out = self.output()
        result = ca.adapt_candidate(self.source, self.candidate, out, 0, 10.01)
        self.assertEqual(result['frame_count'], 240)
        self.assertEqual(result['fps_rational'], '24000/1001')
        self.assertEqual(Fraction(result['target_duration_rational']), Fraction(1001, 100))
        self.assertEqual(Fraction(result['max_sampling_error_rational']), Fraction(133, 8000))
        self.assertEqual(result['first_frame_mapping']['candidate_frame'], 1)
        self.assertEqual(result['last_frame_mapping']['candidate_frame'], 300)
        self.assertGreater(Fraction(result['file_time_baseline']['source_first_pts_rational']), 2)
        self.assertEqual(result['file_time_baseline']['candidate_first_pts_rational'], '7')
        self.assertTrue(result['strict_validation']['ready'])
        self.assertEqual(result['media']['video_codec'], 'ffv1')
        self.assertFalse(result['media']['has_audio'])
        self.assertEqual(result['output_sha256'], media._hash(out))
        for row in result['mapping']:
            target = Fraction(row['target_time_rational'])
            actual = row['candidate_frame'] - 1
            expected = min(range(300), key=lambda i: (abs(Fraction(i, 30) - target), i))
            self.assertEqual(actual, expected)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(out), '-pix_fmt', 'rgb24',
            '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True).stdout
        decoded = np.frombuffer(raw, np.uint8).reshape(240, 40, 64, 3)
        self.assertEqual(decoded[:, 0, 0, 0].tolist(), [(r['candidate_frame'] - 1) % 256 for r in result['mapping']])
        self.assertEqual(decoded[:, 0, 0, 1].tolist(), [(r['candidate_frame'] - 1) // 256 for r in result['mapping']])
        self.assertEqual(json.loads(out.with_suffix('.mkv.json').read_text())['method'], ca.METHOD)

    def test_nonzero_nonintegral_selection_follows_existing_source_frame_rule(self):
        # .105 falls between original F3/F4; existing selection chooses F4.
        # End .520 chooses through F13, 10 actual source frames, not duration*fps.
        candidate = self.output('_candidate.mov')
        self.write(candidate, 13, '30', offset='11')
        expected = rm._selection(rm._timing(self.source), .105, .520)
        result = ca.plan_adaptation(self.source, candidate, .105, .520)
        first, stop = expected[:2]
        self.assertEqual((result['timeline']['first_frame'], result['timeline']['last_frame']), (first + 1, stop))
        self.assertEqual(result['frame_count'], stop - first)
        self.assertEqual(result['first_frame_mapping']['target_time_rational'], '0')
        self.assertEqual(result['first_frame_mapping']['candidate_frame'], 1)
        self.assertEqual(Fraction(result['target_duration_rational']), Fraction(stop - first) / ca.SOURCE_RATE)

    def test_nearest_ties_choose_earlier_exactly(self):
        points = [Fraction(i, 30) for i in range(5)]
        self.assertEqual(ca._nearest(points, Fraction(1, 60)), 0)
        self.assertEqual(ca._nearest(points, Fraction(3, 60)), 1)

    def test_return_inspection_keeps_312_rejected_and_313_valid_without_trusting_usage(self):
        source = self.output('_source250.mov')
        self.write(source, 250, '24000/1001', offset='0.041')
        for count in (312, 313):
            candidate = self.output(f'_candidate{count}.mov')
            self.write(candidate, count, '30', offset='7')
            result = ca.inspect_candidate_return(source, candidate)
            self.assertEqual(result['actual']['frame_count'], count)
            self.assertEqual(result['actual']['first_pts_rational'], '7')
            self.assertTrue(result['pts_checked'])
            self.assertEqual(result['timeline_adaptation']['ready'], count == 313)
            self.assertFalse(result['usage_used_for_validation'])
            self.assertFalse(result['strict_composite_ready'])
            self.assertFalse(result['final_approved'])
            if count == 312:
                self.assertIn('末端覆盖不足', result['timeline_adaptation']['error'])
                self.assertEqual(result['audition_visual_coverage_seconds'], 10.4)

    def test_return_inspection_records_bad_pts_and_wrong_size_as_local_blocks(self):
        with patch.object(ca, '_timestamps', side_effect=media.MediaError('候选时间戳不严格单调')):
            result = ca.inspect_candidate_return(self.source, self.candidate)
        self.assertFalse(result['pts_checked'])
        self.assertFalse(result['timeline_adaptation']['ready'])
        result = ca.inspect_candidate_return(self.source, self.candidate, resolution='720P')
        self.assertTrue(result['pts_checked'])
        self.assertIn('尺寸', result['timeline_adaptation']['error'])

    def test_sample_spatial_mapping_is_separate_equal_aspect_and_keeps_strict_default(self):
        candidate = self.output('_small.mov')
        self.write(candidate, 300, '30', width=32, height=20)
        original_sha, candidate_sha = media._hash(self.source), media._hash(candidate)
        with self.assertRaisesRegex(media.MediaError, '尺寸'):
            ca.plan_adaptation(self.source, candidate, 0, 10.01)
        report = ca.adapt_candidate(self.source, candidate, self.output(), 0, 10.01, sample_only=True)
        self.assertEqual(report['status'], 'SAMPLE_ADAPTATION_TECHNICAL_PASS')
        self.assertEqual(report['spatial_mapping']['candidate_native_size'], [32, 20])
        self.assertEqual(report['spatial_mapping']['derived_size'], [64, 40])
        self.assertEqual(report['spatial_mapping']['scale_rational'], '2')
        self.assertFalse(report['spatial_mapping']['cropped'])
        self.assertFalse(report['spatial_mapping']['native_source_quality'])
        self.assertTrue(report['timeline_size_validation']['ready'])
        self.assertFalse(report['strict_final_approved'])
        self.assertNotIn('strict_validation', report)
        self.assertEqual(report['frame_count'], 240)
        self.assertEqual(media._hash(self.source), original_sha)
        self.assertEqual(media._hash(candidate), candidate_sha)

    def test_sample_rejects_aspect_change_and_missing_endpoint_without_padding(self):
        distorted = self.output('_distorted.mov')
        self.write(distorted, 300, '30', width=32, height=24)
        with self.assertRaisesRegex(media.MediaError, '宽高比'):
            ca.plan_adaptation(self.source, distorted, 0, 10.01, sample_only=True)
        source, short = self.output('_source.mov'), self.output('_short.mov')
        self.write(source, 3, '24000/1001')
        self.write(short, 3, '30', width=32, height=20)
        with self.assertRaisesRegex(media.MediaError, '覆盖不足'):
            ca.plan_adaptation(source, short, sample_only=True)

    def test_grid_delta_is_not_unconditional_permission_for_missing_terminal_sample(self):
        # Three source frames: duration .125125; 3 candidate frames end .1.
        # Difference 25.125ms is below one 30fps frame, but t=.0834167 needs
        # missing sample .1, which is nearer than the available .0666667.
        source, short = self.output('_source.mov'), self.output('_short.mov')
        self.write(source, 3, '24000/1001')
        self.write(short, 3, '30')
        self.assertLess(abs(Fraction(3, 30) - Fraction(3) / ca.SOURCE_RATE), Fraction(1, 30))
        with self.assertRaisesRegex(media.MediaError, '末端覆盖不足'):
            ca.adapt_candidate(source, short, self.output())
        self.assertFalse(self.output().exists())
        supported = self.output('_supported.mov')
        self.write(supported, 4, '30')
        self.assertTrue(ca.plan_adaptation(source, supported)['ready'])

    def test_short_long_size_and_unsupported_rate_are_rejected_without_output(self):
        cases = [('short', 297, '30', 64, 40), ('long', 303, '30', 64, 40),
                 ('size', 300, '30', 66, 40), ('rate', 300, '30000/1001', 64, 40)]
        for label, count, fps, width, height in cases:
            with self.subTest(label=label):
                candidate = self.output('_' + label + '.mov')
                self.write(candidate, count, fps, width, height)
                out = self.output('_' + label + '.mkv')
                with self.assertRaises(media.MediaError):
                    ca.adapt_candidate(self.source, candidate, out, 0, 10.01)
                self.assertFalse(out.exists())
                self.assertFalse(out.with_suffix('.mkv.json').exists())
        unsupported_source = self.output('_source24.mov')
        self.write(unsupported_source, 240, '24')
        with self.assertRaisesRegex(media.MediaError, '24000/1001'):
            ca.plan_adaptation(unsupported_source, self.candidate)

    def test_missing_duplicate_nonmonotonic_and_drifting_pts_are_rejected(self):
        def payload(timestamps, durations=None):
            return {'streams': [{'time_base': '1/30000'}],
                'frames': [{'pts': point, 'duration': (durations or [1000] * len(timestamps))[i]}
                           if point is not None else {'duration': 1000}
                           for i, point in enumerate(timestamps)]}
        for label, data in [('missing', payload([0, None, 2000])),
                            ('duplicate', payload([0, 1000, 1000])),
                            ('backward', payload([0, 2000, 1000])),
                            ('drift', payload([0, 1001, 2002])),
                            ('terminal_extension', payload([0, 1000, 2000], [1000, 1000, 1999]))]:
            with self.subTest(label=label), patch.object(media, '_run', return_value=json.dumps(data)), \
                 patch.object(media, 'probe', return_value={'has_video': True, 'fps_rational': '30/1'}):
                with self.assertRaises(media.MediaError):
                    ca._timestamps(self.candidate, Fraction(30), '候选')

    def test_coarse_clock_cannot_hide_a_different_frame_cadence(self):
        payload = {'streams': [{'time_base': '1/24'}],
                   'frames': [{'pts': i, 'duration': 1} for i in range(240)]}
        # Exact nominal metadata alone must not certify the actual 24fps grid.
        with patch.object(media, '_run', return_value=json.dumps(payload)), \
             patch.object(media, 'probe', return_value={'has_video': True, 'fps_rational': '24000/1001'}):
            with self.assertRaisesRegex(media.MediaError, '时间基准过粗'):
                ca._timestamps(self.source, ca.SOURCE_RATE, '原片')

    def test_coarse_clock_is_supported_when_frame_period_is_exactly_representable(self):
        for rate, tick, path in [(ca.CANDIDATE_RATE, '1/30', self.candidate),
                                 (ca.SOURCE_RATE, '1001/24000', self.source)]:
            payload = {'streams': [{'time_base': tick}],
                       'frames': [{'pts': i + 12, 'duration': 1} for i in range(24)]}
            with self.subTest(rate=rate), patch.object(media, '_run', return_value=json.dumps(payload)), \
                 patch.object(media, 'probe', return_value={'has_video': True, 'fps_rational': str(rate)}):
                result = ca._timestamps(path, rate, '测试视频')
                self.assertEqual(result['points'], [i / rate for i in range(24)])
                self.assertEqual(result['terminal_duration'], 1 / rate)

    def test_failure_keeps_existing_files_and_does_not_publish_partial(self):
        out = self.output()
        with patch.object(rm, '_read_exact', side_effect=media.MediaError('故障注入')):
            with self.assertRaisesRegex(media.MediaError, '故障注入'):
                ca.adapt_candidate(self.source, self.candidate, out, 0, 10.01)
        self.assertFalse(out.exists())
        self.assertFalse(out.with_suffix('.mkv.json').exists())
        self.assertEqual(list(out.parent.glob('.adapt-*')), [])
        out.write_bytes(b'valid previous version')
        with self.assertRaisesRegex(media.MediaError, '输出已存在'):
            ca.adapt_candidate(self.source, self.candidate, out, 0, 10.01)
        self.assertEqual(out.read_bytes(), b'valid previous version')
        self.assertTrue(self.candidate.is_file())

    def test_quantized_mkv_clock_is_supported_without_cumulative_drift(self):
        candidate = self.output('_quantized.mkv')
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', self.candidate, '-an', '-c:v', 'ffv1',
                    '-pix_fmt', 'bgr0', '-threads', '2', candidate])
        result = ca.plan_adaptation(self.source, candidate, 0, 10.01)
        self.assertEqual(result['frame_count'], 240)
        self.assertLessEqual(result['max_sampling_error_seconds'], 1 / 60 + .001)


if __name__ == '__main__':
    unittest.main()
