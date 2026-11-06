"""Offline media integration tests for copying original candidate video."""
from fractions import Fraction
from pathlib import Path
import json
import struct
import subprocess
import tempfile
import unittest

from backend import media, raw_delivery, replacement_media


class RawDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        root = Path(cls.fixtures.name)
        cls.source = root / 'source.mov'
        cls.silent_source = root / 'silent-source.mp4'
        cls.late_audio_source = root / 'late-audio-source.mov'
        cls.short_audio_source = root / 'short-audio-source.mov'
        cls.left = root / 'left.mp4'
        cls.right = root / 'right.mp4'
        cls.different_encoding = root / 'different-encoding.mp4'
        cls.short = root / 'short.mp4'
        cls.ntsc = root / 'ntsc.mp4'
        cls.vfr = root / 'vfr.mp4'
        cls.make_video(cls.source, 24, video_offset=1230, audio_seconds='0.82')
        cls.make_video(cls.silent_source, 24)
        cls.make_video(cls.late_audio_source, 24, audio_seconds='0.75', audio_offset=2400)
        cls.make_video(cls.short_audio_source, 24, audio_seconds='0.7')
        cls.make_video(cls.left, 12, video_offset=300, audio_seconds='0.40', audio_frequency=960)
        cls.make_video(cls.right, 12, video_filter=',hflip')
        cls.make_video(cls.different_encoding, 12, extra=('-profile:v', 'baseline'))
        cls.make_video(cls.short, 11)
        cls.make_video(cls.ntsc, 12, rate='24000/1001')
        cls.make_video(cls.vfr, 12, video_filter=",setpts='PTS+eq(N,4)*30'")

    @staticmethod
    def make_video(path, frames, *, video_offset=0, audio_seconds=None, audio_offset=0, audio_frequency=440,
                   video_filter='', rate='30', size='96x64', extra=()):
        duration = max(2, frames / float(Fraction(rate)) + 1)
        command = ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', f'testsrc2=s={size}:r={rate}:d={duration}']
        if audio_seconds:
            command += ['-f', 'lavfi', '-i',
                        f'sine=frequency={audio_frequency}:sample_rate=48000:duration={audio_seconds}']
        timescale = 30000 if rate == '30' else 24000
        command += ['-map', '0:v:0', '-vf',
                    f'trim=end_frame={frames},settb=1/{timescale},setpts=PTS+{video_offset}{video_filter}',
                    '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough',
                    '-enc_time_base', f'1/{timescale}', '-video_track_timescale', str(timescale), '-movie_timescale', '240000',
                    *extra]
        if audio_seconds:
            command += ['-map', '1:a:0', '-af', f'asetpts=PTS+{audio_offset}',
                        '-c:a', 'pcm_s32le' if path.suffix == '.mov' else 'aac']
        media._run([*command, path])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name) / 'result.mp4'

    @staticmethod
    def segment(path, start, end):
        return {'path': path, 'source_start': start, 'source_end': end}

    @staticmethod
    def video_packets(path):
        data = json.loads(media._run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_packets',
                                      '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', path]))
        return [packet['data_hash'] for packet in data['packets']]

    @staticmethod
    def audio_samples(path):
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                              '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'],
                             check=True, capture_output=True).stdout
        return struct.unpack(f'<{len(pcm) // 2}h', pcm)

    def assert_audio_matches(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        self.assertLess(sum((a - b) ** 2 for a, b in zip(actual, expected)), sum(a * a for a in expected) / 100)

    def test_multiclip_copies_all_packets_and_retains_original_offset_and_natural_audio_end(self):
        segments = [self.segment(self.left, 0, .4), self.segment(self.right, .4, .8)]
        inputs = [media._hash(path) for path in [self.source, self.left, self.right]]
        report = raw_delivery.assemble_original(self.source, segments, self.out)
        self.assertEqual(report['status'], 'ORIGINAL_STREAM_COPY_PASS')
        self.assertTrue(report['video_copy'])
        self.assertFalse(report['video_reencoded'])
        self.assertEqual(report['frame_count'], 24)
        self.assertEqual(report['sha256'], media._hash(self.out))
        self.assertEqual(self.video_packets(self.out), self.video_packets(self.left) + self.video_packets(self.right))
        self.assertEqual(inputs, [media._hash(path) for path in [self.source, self.left, self.right]])
        data = json.loads(media._run(['ffprobe', '-v', 'error', '-show_streams', '-show_frames', '-of', 'json', self.out]))
        video = next(stream for stream in data['streams'] if stream['codec_type'] == 'video')
        clock = Fraction(video['time_base'])
        frames = [frame for frame in data['frames'] if frame['media_type'] == 'video']
        self.assertEqual([int(frame['pts']) * clock for frame in frames],
                         [Fraction(41, 1000) + Fraction(i, 30) for i in range(24)])
        audio = report['audio']
        self.assertEqual(audio['mode'], 'single_continuous_original')
        self.assertFalse(audio['candidate_audio_used'])
        self.assertFalse(audio['per_clip_audio_joined'])
        self.assertEqual(audio['sample_count'], 39360)
        self.assertEqual(audio['natural_tail_gap_rational'], '21/1000')
        self.assertEqual(sum(stream['codec_type'] == 'audio' for stream in data['streams']), 1)
        self.assert_audio_matches(self.audio_samples(self.out)[:39360], self.audio_samples(self.source))

    def test_preserves_late_original_audio_gap_without_using_candidate_audio(self):
        segments = [self.segment(self.left, 0, .4), self.segment(self.right, .4, .8)]
        report = raw_delivery.assemble_original(self.late_audio_source, segments, self.out)
        audio = report['audio']
        self.assertEqual(audio['leading_original_gap_samples'], 2400)
        self.assertEqual(audio['original_audio_video_offset_rational'], '1/20')
        output = self.audio_samples(self.out)
        self.assertLess(max(abs(sample) for sample in output[:2000]), 10)
        self.assert_audio_matches(output[2400:38400], self.audio_samples(self.late_audio_source))

    def test_single_complete_candidate_without_source_audio_ignores_candidate_audio(self):
        report = raw_delivery.assemble_original(self.silent_source, [self.segment(self.left, 0, .4)], self.out,
                                                target_end=.4)
        self.assertEqual(report['audio']['mode'], 'none')
        self.assertEqual(self.video_packets(self.out), self.video_packets(self.left))
        self.assertFalse(media.probe(self.out)['has_audio'])

    def test_authorized_output_size_preserves_native_packets_source_timing_and_audio(self):
        candidate = self.out.parent / 'small-candidate.mp4'
        incomplete = self.out.parent / 'incomplete-small-candidate.mp4'
        self.make_video(candidate, 24, size='72x48')
        self.make_video(incomplete, 23, size='72x48')
        segments = [self.segment(candidate, 0, .8)]

        default = replacement_media.validate_candidate(self.source, candidate, 0, .8)
        self.assertIn('SIZE_MISMATCH', [issue['code'] for issue in default['issues']])
        with self.assertRaisesRegex(media.MediaError, '原始尺寸'):
            raw_delivery.assemble_original(self.source, segments, self.out)

        override = {'output_size': (72, 48)}
        checked = replacement_media.validate_candidate(self.source, candidate, 0, .8, **override)
        self.assertTrue(checked['ready'], checked['issues'])
        checked_short = replacement_media.validate_candidate(self.source, incomplete, 0, .8, **override)
        self.assertIn('FRAME_COUNT_MISMATCH', [issue['code'] for issue in checked_short['issues']])
        with self.assertRaisesRegex(media.MediaError, '帧数'):
            raw_delivery.assemble_original(self.source, [self.segment(incomplete, 0, .8)], self.out, **override)

        report = raw_delivery.assemble_original(self.source, segments, self.out, **override)
        self.assertEqual((report['media']['width'], report['media']['height']), (72, 48))
        self.assertEqual((report['frame_count'], report['fps_rational']), (24, '30'))
        self.assertTrue(report['relative_pts_verified'])
        self.assertFalse(report['video_reencoded'])
        self.assertEqual(self.video_packets(self.out), self.video_packets(candidate))
        self.assertEqual(report['audio']['mode'], 'single_continuous_original')
        self.assert_audio_matches(self.audio_samples(self.out)[:39360], self.audio_samples(self.source))

    def test_gap_overlap_and_incomplete_cover_are_blocked(self):
        cases = [([self.segment(self.left, 0, .4), self.segment(self.right, .433333, .8)], '连续'),
                 ([self.segment(self.left, 0, .4), self.segment(self.right, .366667, .8)], '连续'),
                 ([self.segment(self.left, 0, .4)], '覆盖')]
        for segments, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(media.MediaError, error):
                raw_delivery.assemble_original(self.silent_source, segments, self.out)
            self.assertFalse(self.out.exists())

    def test_strict_check_locates_frame_301_from_decoded_timing(self):
        source = self.out.parent / '301-source.mp4'
        complete = self.out.parent / '301-candidate.mp4'
        truncated = self.out.parent / '300-candidate.mp4'
        self.make_video(source, 301, video_offset=1230)
        self.make_video(complete, 301, size='72x48')
        self.make_video(truncated, 300, size='72x48')
        options = {'output_size': (72, 48), 'verify_raw_pts': True}
        accepted = replacement_media.validate_candidate(source, complete, **options)
        self.assertTrue(accepted['ready'], accepted['issues'])
        self.assertEqual(accepted['source']['first_pts_rational'], '41/1000')
        self.assertEqual(accepted['candidate']['last_pts_rational'], '10')
        self.assertEqual(accepted['coverage']['missing_frame_count'], 0)
        rejected = replacement_media.validate_candidate(source, truncated, **options)
        self.assertFalse(rejected['ready'])
        self.assertFalse(rejected['usage_used_for_validation'])
        self.assertTrue(rejected['candidate']['cfr_pts_certified'])
        self.assertEqual(rejected['candidate']['decoded_duration_seconds'], 10.0)
        self.assertEqual(rejected['candidate']['last_pts_rational'], '299/30')
        self.assertEqual(rejected['coverage'], {'missing_frame_count': 1, 'frame_count_delta': -1, 'missing_ranges': [
            {'source_first_frame': 301, 'source_last_frame': 301, 'source_first_seconds': 10.0,
             'source_last_seconds': 10.0, 'segment_first_seconds': 10.0, 'segment_last_seconds': 10.0}]})

    def test_strict_check_locates_interior_gap_on_the_selected_source_range(self):
        skipped = self.out.parent / 'missing-interior.mp4'
        self.make_video(skipped, 12, video_filter=",select='not(eq(n,4))'")
        report = replacement_media.validate_candidate(self.silent_source, skipped, .4, .8, verify_raw_pts=True)
        self.assertFalse(report['ready'])
        self.assertIn('CANDIDATE_PTS_MISMATCH', {issue['code'] for issue in report['issues']})
        self.assertEqual(report['coverage']['missing_ranges'], [
            {'source_first_frame': 17, 'source_last_frame': 17, 'source_first_seconds': 16 / 30,
             'source_last_seconds': 16 / 30, 'segment_first_seconds': 4 / 30, 'segment_last_seconds': 4 / 30}])

    def test_strict_check_rejects_pts_drift_with_matching_count_and_duration(self):
        drift = self.out.parent / 'single-tick-drift.mp4'
        self.make_video(drift, 12, video_filter=",setpts='PTS+eq(N,3)'")
        legacy_check = replacement_media.validate_candidate(self.silent_source, drift, 0, .4)
        self.assertTrue(legacy_check['ready'], legacy_check['issues'])
        report = replacement_media.validate_candidate(self.silent_source, drift, 0, .4, verify_raw_pts=True)
        self.assertFalse(report['ready'])
        self.assertEqual(report['coverage']['frame_count_delta'], 0)
        self.assertIsNone(report['coverage']['missing_frame_count'])
        self.assertEqual({issue['code'] for issue in report['issues']}, {'CANDIDATE_PTS_MISMATCH'})

    def test_incompatible_codec_wrong_cadence_and_wrong_frame_count_are_blocked(self):
        cases = [(self.different_encoding, '不兼容'), (self.short, '帧数'), (self.ntsc, '30fps'), (self.vfr, '帧率')]
        for path, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(media.MediaError, error):
                raw_delivery.assemble_original(self.silent_source, [self.segment(self.left, 0, .4),
                                                                   self.segment(path, .4, .8)], self.out)
            self.assertFalse(self.out.exists())

    def test_missing_audio_beyond_last_frame_is_not_padded(self):
        with self.assertRaisesRegex(media.MediaError, '未覆盖'):
            raw_delivery.assemble_original(self.short_audio_source, [self.segment(self.left, 0, .4),
                                                                     self.segment(self.right, .4, .8)], self.out)
        self.assertFalse(self.out.exists())

    def test_existing_output_is_preserved(self):
        self.out.write_bytes(b'old edit')
        with self.assertRaisesRegex(media.MediaError, '已存在'):
            raw_delivery.assemble_original(self.silent_source, [self.segment(self.left, 0, .4)], self.out,
                                           target_end=.4)
        self.assertEqual(self.out.read_bytes(), b'old edit')


if __name__ == '__main__':
    unittest.main()
