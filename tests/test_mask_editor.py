"""Offline native-cadence polygon rendering and conservative break handling."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from backend import mask_editor, media, replacement


class ManualMaskEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root/'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=8:d=0.75',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.source)], check=True)
        cls.source_sha = media._hash(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.out = tempfile.TemporaryDirectory()
        self.p = {'id': 'polygon_test', 'workflow': 'replacement',
                  'source': {'id': 'source', 'path': str(self.source), 'sha256': self.source_sha},
                  'source_meta': media.probe(self.source), 'timeline_duration': .75,
                  'source_actors': [{'id': 'person_a', 'scope': 'person'}], 'roles': [],
                  'scene': {'mode': 'preserve', 'images': [], 'regions': []}, 'prompts': {},
                  'settings': {'model': 'wan3.0-video'}}
        self.s = {'id': 'A', 'start': 0, 'end': .75, 'mask_keyframes': [self.key(0), self.key(5/8, shift=.3)]}
        self.p['segments'] = [self.s]

    def tearDown(self):
        self.out.cleanup()

    def region(self, shift=0, mode='edit', rid='person_outline', aid='person_a'):
        return {'id': rid, 'label': '人工轮廓', 'actor_id': aid, 'mode': mode,
                'points': [[.1+shift, .15], [.3+shift, .15], [.3+shift, .85], [.1+shift, .85]]}

    def key(self, stamp, shift=0, regions=None, **extra):
        return {'time': stamp, 'confirmed': True, 'regions': [self.region(shift)] if regions is None else regions, **extra}

    def frames(self, path):
        result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-an', '-pix_fmt', 'gray', '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'],
                                check=True, stdout=subprocess.PIPE)
        return np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 64, 96)

    def test_rendered_masks_are_binary_and_keep_native_frames_and_rate(self):
        result = mask_editor.render_masks(self.p, self.s, self.out.name, 'native')
        edit, protect = self.frames(result['edit_mask_path']), self.frames(result['protection_mask_path'])
        self.assertEqual(edit.shape, (6, 64, 96))
        self.assertEqual(set(np.unique(edit)), {0, 255})
        self.assertFalse(protect.any())
        centres = [np.where(frame == 255)[1].mean() for frame in edit]
        self.assertTrue(all(a < b for a, b in zip(centres, centres[1:])))
        for key in ('edit_mask_path', 'protection_mask_path', 'preview_path'):
            probe = media.probe(result[key])
            self.assertEqual(probe['fps_rational'], '8/1')
            self.assertEqual((probe['width'], probe['height']), (96, 64))
        report = result['report']
        self.assertEqual(report['first_frame'], 1)
        self.assertEqual(report['last_frame'], 6)
        self.assertEqual(report['interpolated_frames'], 4)
        self.assertTrue(report['binary_masks_verified'])
        self.assertTrue(report['review_required'])
        self.assertFalse(report['automatic_tracking'])
        self.assertFalse(report['cloud_upload_performed'])
        self.assertEqual(report['motion_quality'], 'UNVERIFIED')
        self.assertEqual(json.loads(Path(result['report_path']).read_text())['edit_mask_sha256'], media._hash(result['edit_mask_path']))

    def test_protection_layer_is_independent_and_binary(self):
        protect = self.region(mode='protect', rid='hand', aid=None)
        protect['points'] = [[.15, .25], [.2, .25], [.2, .6], [.15, .6]]
        for key in self.s['mask_keyframes']:
            key['regions'].append(copy.deepcopy(protect))
        result = mask_editor.render_masks(self.p, self.s, self.out.name, 'protected')
        frames = self.frames(result['protection_mask_path'])
        self.assertEqual(set(np.unique(frames)), {0, 255})
        self.assertTrue(np.array_equal(frames[0], frames[-1]))
        self.assertFalse(result['report']['protection_empty'])
        self.assertGreater(result['report']['protected_pixel_samples'], 0)

    def test_visibility_change_across_unobserved_frames_is_blocked(self):
        self.s['mask_keyframes'][-1]['regions'] = []
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertFalse(result['ready'])
        self.assertIn('VISIBILITY_BREAK_REQUIRES_ADJACENT_FRAMES', {i['code'] for i in result['issues']})
        with self.assertRaisesRegex(ValueError, '相邻帧'):
            mask_editor.render_masks(self.p, self.s, self.out.name, 'blocked')
        self.assertEqual(list(Path(self.out.name).iterdir()), [])

    def test_explicit_adjacent_exit_has_no_phantom_polygon_after_exit(self):
        self.s['mask_keyframes'] = [self.key(0), self.key(2/8, break_after=True),
                                   self.key(3/8, regions=[], break_before=True), self.key(5/8, regions=[])]
        result = mask_editor.render_masks(self.p, self.s, self.out.name, 'exit')
        frames = self.frames(result['edit_mask_path'])
        self.assertTrue(all(frame.any() for frame in frames[:3]))
        self.assertFalse(frames[3:].any())

    def test_topology_or_actor_change_requires_adjacent_keyframes(self):
        self.s['mask_keyframes'][-1]['regions'][0]['points'].append([.35, .5])
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertFalse(result['ready'])
        self.assertIn('VISIBILITY_BREAK_REQUIRES_ADJACENT_FRAMES', {i['code'] for i in result['issues']})

    def test_unknown_actor_and_preserved_actor_cannot_be_edited(self):
        self.s['mask_keyframes'][0]['regions'][0]['actor_id'] = 'missing_person'
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('ACTOR_REFERENCE_INVALID', {i['code'] for i in result['issues']})
        self.s['mask_keyframes'][0]['regions'][0]['actor_id'] = 'person_a'
        self.p['source_actors'][0]['scope'] = 'preserve'
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('PRESERVED_ACTOR_EDITED', {i['code'] for i in result['issues']})

    def test_scene_only_polygons_need_explicit_scene_replacement(self):
        self.p['source_actors'] = []
        for key in self.s['mask_keyframes']:
            key['regions'][0]['actor_id'] = None
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('SCENE_NOT_SELECTED', {i['code'] for i in result['issues']})
        self.p['scene']['mode'] = 'replace'
        self.assertTrue(mask_editor.validate_keyframes(self.p, self.s)['ready'])

    def test_endpoint_confirmation_and_simple_polygon_are_required(self):
        self.s['mask_keyframes'] = [self.key(1/8), self.key(5/8, confirmed=False)]
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('ENDPOINTS_REQUIRED', {i['code'] for i in result['issues']})
        self.assertIn('KEYFRAME_UNCONFIRMED', {i['code'] for i in result['issues']})
        self.s['mask_keyframes'] = [self.key(0), self.key(5/8)]
        self.s['mask_keyframes'][0]['regions'][0]['points'] = [[.1, .1], [.9, .9], [.1, .9], [.9, .1]]
        self.assertIn('POLYGON_INVALID', {i['code'] for i in mask_editor.validate_keyframes(self.p, self.s)['issues']})

    def test_keyframe_break_stops_interpolation(self):
        self.s['mask_keyframes'][0]['break_after'] = True
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('VISIBILITY_BREAK_REQUIRES_ADJACENT_FRAMES', {i['code'] for i in result['issues']})

    def test_detected_cut_requires_immediately_adjacent_boundary_keys(self):
        self.p['source_analysis'] = {'source_sha256': self.source_sha, 'cuts': [{'frame1based': 4, 'time': 3/8, 'score': .8}]}
        result = mask_editor.validate_keyframes(self.p, self.s)
        self.assertIn('SHOT_CUT_REQUIRES_BOUNDARY_KEYFRAMES', {i['code'] for i in result['issues']})
        self.s['mask_keyframes'] = [self.key(0), self.key(2/8), self.key(3/8, shift=.3), self.key(5/8, shift=.3)]
        self.assertTrue(mask_editor.validate_keyframes(self.p, self.s)['ready'])

    def test_annotations_are_part_of_business_fingerprint_even_as_draft(self):
        before = replacement.fingerprint(self.p)
        self.s['mask_keyframes'][0]['confirmed'] = False
        self.assertNotEqual(before, replacement.fingerprint(self.p))
        before = replacement.fingerprint(self.p)
        self.s['mask_keyframes'] = [{'time': .1, 'regions': [{'points': []}]}]
        self.assertNotEqual(before, replacement.fingerprint(self.p))


if __name__ == '__main__':
    unittest.main()
