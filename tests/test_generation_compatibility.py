import copy
import unittest
from unittest.mock import patch

from backend import generation_compatibility as compatibility


class GenerationCompatibilityTests(unittest.TestCase):
    def project(self, width=1280, height=720, fps='30/1', resolution='720P'):
        return {'source': {'id': 'source'}, 'source_meta': {'width': width, 'height': height, 'fps_rational': fps},
                'settings': {'model': 'wan3.0-video', 'resolution': resolution, 'ratio': 'adaptive', 'duration': -1, 'watermark': False, 'audio': False},
                'segments': [{'id': 'A', 'name': '甲段', 'start': 0, 'end': 2}]}

    def test_source_rate_is_tool_conflict_even_though_api_accepts_this_input_rate(self):
        project = self.project(fps='24000/1001')
        result = compatibility.inspect(project)
        self.assertFalse(result['ready'])
        issue = next(item for item in result['issues'] if item['code'] == 'MODEL_FPS_INCOMPATIBLE')
        self.assertIn('23.976', issue['message'])
        self.assertNotIn('24000/1001', issue['message'])
        self.assertIn('≥16fps', issue['details'])
        self.assertIn('24000/1001', issue['details'])
        self.assertEqual(issue['scope'], 'tool_strict_compatibility')

    def test_unverified_cross_size_combinations_block_before_payment(self):
        for width, height, expected in [(640, 480, '480P'), (1080, 1920, '1080P')]:
            for requested in ('480P', '720P', '1080P'):
                with self.subTest(size=(width, height), requested=requested):
                    result = compatibility.inspect(self.project(width, height, resolution=requested))
                    self.assertEqual(result['ready'], requested == expected)
                    if requested != expected:
                        issue = next(item for item in result['issues'] if item['code'] == 'OUTPUT_SIZE_TIER_MISMATCH')
                        self.assertEqual(issue['suggested_resolution'], expected)
                        self.assertIn('不是API输入限制', issue['details'])

    def test_adaptive_never_promises_exact_output_pixels_or_result_acceptance(self):
        result = compatibility.inspect(self.project())
        self.assertTrue(result['ready'])
        self.assertFalse(result['exact_output_pixels_guaranteed'])
        self.assertTrue(result['result_requires_validation'])
        self.assertTrue(result['verified_submission_conditions_only'])
        self.assertFalse(compatibility.inspect(self.project(320, 240, resolution='480P'))['ready'])

    def test_observed_native_720p_delivery_is_independent_of_source_and_mask_scope(self):
        project = self.project(1920, 1080)
        project.update(workflow='replacement', source_actors=[{'scope': 'person'}], scene={'mode': 'replace'})
        project['settings']['ratio'] = '16:9'
        result = compatibility.inspect(project)
        self.assertTrue(result['ready'], result['issues'])
        self.assertEqual((result['delivery_width'], result['delivery_height']), (1280, 720))
        self.assertEqual((result['source_width'], result['source_height']), (1920, 1080))
        self.assertEqual(result['delivery_spec']['dimension_basis'], 'observed')
        project['segments'].append({'id': 'B', 'start': 2, 'end': 4})
        self.assertTrue(compatibility.inspect(project)['ready'])

        report = {'model': project['settings']['model'], 'parameters': project['settings'],
                  'media': [{'type': 'reference_video', 'path': '/independent/reference.mp4'}]}
        actual = {**project['source_meta'], 'duration_seconds': 2}
        with patch.object(compatibility.media, 'probe', return_value=actual):
            self.assertTrue(compatibility.assert_preflight(project, project['segments'][0], report)['ready'])
        with patch.object(compatibility.media, 'probe', return_value={**actual, 'width': 1280, 'height': 720}):
            with self.assertRaisesRegex(ValueError, '原片不同'):
                compatibility.assert_preflight(project, project['segments'][0], report)

    def test_delivery_specs_keep_unknown_targets_separate_from_observed_sizes(self):
        project = self.project(1920, 1080, resolution='480P')
        project['segments'][0]['end'] = 10.033333
        spec = compatibility.inspect(project)['delivery_spec']
        self.assertEqual((spec['width'], spec['height'], spec['dimension_basis']), (None, None, 'unverified'))
        self.assertEqual(spec['frame_count'], 301)
        self.assertAlmostEqual(spec['duration_seconds'], 301 / 30)
        self.assertIn('固定时长只能填整数秒', spec['duration_warnings'][0]['message'])
        project['settings']['resolution'] = '1080P'
        self.assertEqual(compatibility.delivery_dimensions(project), (1920, 1080))
        project['settings'].update(model='wan3.0-video-prime', resolution='720P')
        self.assertIsNone(compatibility.delivery_dimensions(project))
        project['settings']['resolution'] = None
        self.assertIn('OUTPUT_SIZE_SETTING_INVALID', {i['code'] for i in compatibility.inspect(project)['issues']})

    def test_explicit_ratio_and_input_duration_conflicts_are_not_user_overrideable(self):
        project = self.project()
        project['settings']['ratio'] = '4:3'
        project['segments'][0]['end'] = .5
        result = compatibility.inspect(project)
        self.assertEqual({issue['code'] for issue in result['issues']}, {'OUTPUT_RATIO_MISMATCH', 'MODEL_INPUT_DURATION_UNSUPPORTED'})
        self.assertTrue(all(issue['blocking'] for issue in result['issues']))
        project['settings'].update(ratio='adaptive', duration=5)
        self.assertIn('OUTPUT_DURATION_MISMATCH', {issue['code'] for issue in compatibility.inspect(project)['issues']})

    def test_actual_reference_and_request_settings_are_rechecked(self):
        project = self.project()
        report = {'model': project['settings']['model'], 'parameters': {key: value for key, value in project['settings'].items() if key != 'model'},
                  'media': [{'type': 'reference_video', 'path': '/independent/reference.mp4'}]}
        actual = {**project['source_meta'], 'duration_seconds': 2}
        with patch.object(compatibility.media, 'probe', return_value=actual):
            self.assertTrue(compatibility.assert_preflight(project, project['segments'][0], report)['ready'])
        for changes in ({'width': 640}, {'fps_rational': '24000/1001'}, {'duration_seconds': 4}):
            with self.subTest(changes=changes), patch.object(compatibility.media, 'probe', return_value={**actual, **changes}):
                with self.assertRaises(ValueError):
                    compatibility.assert_preflight(project, project['segments'][0], report)
        altered = copy.deepcopy(report)
        altered['parameters']['resolution'] = '480P'
        with self.assertRaisesRegex(ValueError, '实际请求参数'):
            compatibility.assert_preflight(project, project['segments'][0], altered)

    def test_missing_resolution_uses_480p_for_compatibility_and_request(self):
        project = self.project(640, 480)
        project['settings'].pop('resolution')
        result = compatibility.inspect(project)
        self.assertTrue(result['ready'])
        self.assertEqual(result['resolution'], '480P')
        report = {'model': project['settings']['model'],
                  'parameters': {key: value for key, value in project['settings'].items() if key != 'model'},
                  'media': [{'type': 'reference_video', 'path': '/independent/reference.mp4'}]}
        report['parameters']['resolution'] = '480P'
        with patch.object(compatibility.media, 'probe', return_value={**project['source_meta'], 'duration_seconds': 2}):
            self.assertTrue(compatibility.assert_preflight(project, project['segments'][0], report)['ready'])
        report['parameters']['resolution'] = '720P'
        with self.assertRaisesRegex(ValueError, '实际请求参数'):
            compatibility.assert_preflight(project, project['segments'][0], report)

    def test_sample_separates_api_acceptance_from_strict_final_eligibility(self):
        project = self.project(1080, 1920, '24000/1001', '720P')
        sample = compatibility.inspect(project, 'candidate_sample', ['A'])
        self.assertTrue(sample['ready'])
        self.assertTrue(sample['sample_only'])
        self.assertFalse(sample['strict_final_ready'])
        self.assertEqual({i['code'] for i in sample['strict_final_issues']}, {'MODEL_FPS_INCOMPATIBLE', 'OUTPUT_SIZE_TIER_MISMATCH'})
        self.assertFalse(compatibility.inspect(project)['ready'])
        self.assertFalse(compatibility.inspect(self.project(fps='15'), 'candidate_sample')['ready'])
        project['segments'].append({'id': 'B', 'start': 3, 'end': 3.5})
        self.assertTrue(compatibility.inspect(project, 'candidate_sample', ['A'])['ready'])
        self.assertFalse(compatibility.inspect(project, 'candidate_sample', ['A', 'B'])['ready'])

    def test_sample_preflight_does_not_downscale_reference_or_expand_selection(self):
        project = self.project(1920, 1080, '24000/1001', '720P')
        report = {'model': project['settings']['model'], 'parameters': {k: v for k, v in project['settings'].items() if k != 'model'},
                  'media': [{'type': 'reference_video', 'path': '/separate/original-reference.mp4'}]}
        actual = {**project['source_meta'], 'duration_seconds': 2}
        with patch.object(compatibility.media, 'probe', return_value=actual):
            self.assertTrue(compatibility.assert_preflight(project, project['segments'][0], report, 'candidate_sample', ['A'])['ready'])
            with self.assertRaisesRegex(ValueError, '授权范围'):
                compatibility.assert_preflight(project, project['segments'][0], report, 'candidate_sample', ['B'])
        with patch.object(compatibility.media, 'probe', return_value={**actual, 'width': 1280, 'height': 720}):
            with self.assertRaisesRegex(ValueError, '原片不同'):
                compatibility.assert_preflight(project, project['segments'][0], report, 'candidate_sample', ['A'])


if __name__ == '__main__':
    unittest.main()
