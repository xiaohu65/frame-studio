"""Final-frame natural audio EOF is an explicit upload contract, never padding."""
import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from backend import generation_audio as ga, media


class FinalReferenceAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.sources = {}
        for name, frames, audio_seconds in [('tail', 120, '4.02'), ('full', 120, '5'),
                                            ('long_gap', 120, '3.9'), ('one_frame_gap', 120, '4.007666666666667'), ('middle_gap', 120, '1.99'),
                                            ('short', 36, '1.22')]:
            dest = cls.root / (name + '.mov')
            media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', f'color=blue:s=96x64:r=30:d={frames/30}',
                '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={audio_seconds}',
                '-map', '0:v:0', '-map', '1:a:0', '-vf', 'settb=1/30000,setpts=PTS+1230',
                '-c:v', 'libx264', '-threads', '2', '-enc_time_base', '1/30000', '-fps_mode', 'passthrough', '-video_track_timescale', '30000',
                '-c:a', 'pcm_s32le', '-ac', '2', dest])
            cls.sources[name] = dest

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_only_real_source_last_frame_can_end_at_original_audio_eof(self):
        source = self.sources['tail']
        report = ga.prepare_reference_audio(source, self.root / 'tail_reference.wav', 2, 4)
        self.assertEqual(report['source_first_frame'], 61)
        self.assertEqual(report['source_last_frame'], 120)
        self.assertEqual(report['source_video_frame_count'], 120)
        self.assertEqual(report['selected_video_duration_rational'], '2')
        self.assertEqual(report['coverage_duration_rational'], '1979/1000')
        self.assertEqual(report['natural_tail_gap_rational'], '21/1000')
        self.assertEqual(report['source_first_sample'], 97968)
        self.assertEqual(report['source_stop_sample'], 192960)
        self.assertEqual(report['sample_count'], 94992)
        self.assertEqual(report['video_first_in_audio_rational'], '0')
        self.assertEqual(report['reference_coverage_policy'], ga.FINAL_TAIL_POLICY)
        for flag in ('resampled', 'stretched', 'looped', 'terminal_silence_added', 'strict_final_approved'):
            self.assertFalse(report[flag])
        decoded = self.root / 'source_pcm.wav'
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', source, '-map', '0:a:0', '-vn', '-c:a', 'pcm_s32le', decoded])
        with wave.open(str(decoded), 'rb') as reader:
            reader.setpos(97968)
            expected = reader.readframes(94992)
        with wave.open(report['path'], 'rb') as reader:
            self.assertEqual(reader.readframes(reader.getnframes()), expected)
        self.assertEqual(report['pcm_sha256'], hashlib.sha256(expected).hexdigest())
        ga.validate_reference_audio(source, 2, 4, report, report['path'])

    def test_complete_v1_reference_plan_receipt_and_note_stay_compatible(self):
        source = self.sources['full']
        old = ga.plan_original_audio(source, 0, 2, reference=True)
        self.assertEqual(ga.plan_reference_audio(source, 0, 2), old)
        report = ga.prepare_original_audio(source, self.root / 'legacy_full.wav', 0, 2, reference=True)
        ga.validate_reference_audio(source, 0, 2, report, report['path'])
        self.assertNotIn('reference_coverage_policy', report)
        self.assertEqual(ga.REFERENCE_METHOD, 'SOURCE_VIDEO_ZERO_ALIGNED_ORIGINAL_PCM_V1')
        self.assertNotIn('最后一帧', ga.reference_prompt_note(report))

    def test_arbitrary_extra_truncation_and_wrong_actual_wav_are_rejected(self):
        source = self.sources['tail']
        arbitrary = ga.prepare_original_audio(source, self.root / 'arbitrary_clip.wav', 2, 4, reference=True, coverage_seconds=1.2)
        with self.assertRaisesRegex(ValueError, '合同'):
            ga.validate_reference_audio(source, 2, 4, arbitrary, arbitrary['path'])
        report = ga.prepare_reference_audio(source, self.root / 'valid_for_tamper.wav', 2, 4)
        bad = copy.deepcopy(report); bad['source_stop_sample'] -= 1
        with self.assertRaisesRegex(ValueError, '合同'):
            ga.validate_reference_audio(source, 2, 4, bad, report['path'])
        path = Path(report['path']); content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
        with self.assertRaisesRegex(ValueError, '指纹'):
            ga.validate_reference_audio(source, 2, 4, report, path)

    def test_middle_segment_long_gap_inexact_source_endpoint_and_under_one_second_stay_blocked(self):
        cases = [('middle_gap', 0, 2, '末段'), ('long_gap', 2, 4, '最后一帧'), ('one_frame_gap', 2, 4, '最后一帧'),
                 ('tail', 2, 3.99, '实际终点'), ('short', .3, 1.2, '1–15秒')]
        for name, start, end, reason in cases:
            with self.subTest(name=name, end=end):
                path = self.root / f'blocked_{name}_{end}.wav'
                with self.assertRaisesRegex(media.MediaError, reason):
                    ga.prepare_reference_audio(self.sources[name], path, start, end)
                self.assertFalse(path.exists())

    def test_same_length_non_original_pcm_cannot_self_certify_a_cache_receipt(self):
        source = self.sources['full']
        report = ga.prepare_reference_audio(source, self.root / 'forged_same_length.wav', 0, 2)
        pcm = bytes(report['sample_count'] * report['channels'] * report['sample_width'])
        with wave.open(report['path'], 'wb') as writer:
            writer.setnchannels(report['channels']); writer.setsampwidth(report['sample_width'])
            writer.setframerate(report['sample_rate']); writer.writeframes(pcm)
        report.update(sha256=media._hash(report['path']), pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                      retained_source_pcm_sha256=hashlib.sha256(pcm).hexdigest(), retained_source_samples_exact=True)
        with self.assertRaisesRegex(ValueError, 'PCM'):
            ga.validate_reference_audio(source, 0, 2, report, report['path'])

    def test_abnormal_audio_pts_are_not_relaxed_by_final_tail_policy(self):
        frames = {'streams': [{'sample_rate': '48000', 'channels': 2, 'time_base': '1/48000'}],
                  'frames': [{'pts': 0, 'nb_samples': 48000}, {'pts': 48100, 'nb_samples': 48000}]}
        with patch.object(media, '_run', return_value=json.dumps(frames)):
            with self.assertRaisesRegex(media.MediaError, '连续采样'):
                ga._audio_timing(self.sources['tail'])
        with patch.object(ga, '_audio_timing', side_effect=media.MediaError('异常连续采样时间戳')):
            with self.assertRaisesRegex(media.MediaError, '连续采样'):
                ga.plan_reference_audio(self.sources['tail'], 2, 4)

    def test_natural_tail_requires_real_certified_video_pts_not_container_duration(self):
        from backend import candidate_adaptation
        with patch.object(candidate_adaptation, '_timestamps', side_effect=media.MediaError('原片时间戳异常')):
            with self.assertRaisesRegex(media.MediaError, '时间戳异常'):
                ga.plan_reference_audio(self.sources['tail'], 2, 4)


if __name__ == '__main__':
    unittest.main()
