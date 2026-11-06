"""Isolated source-frame and black-mask identity checks; no project/cloud writes."""
import copy
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from backend import identity_evidence, media, replacement, replacement_workflow, shot_analysis


class FrameGridTests(unittest.TestCase):
    def project(self):
        rate = Fraction(24000, 1001)
        return {'source': {'sha256': 'source'}, 'source_meta': {'fps_rational': str(rate)},
                'segments': [{'id': 'A', 'start': 0, 'end': float(250/rate)},
                             {'id': 'B', 'start': float(250/rate), 'end': float(581/rate)},
                             {'id': 'C', 'start': float(581/rate), 'end': float(767/rate)}],
                'source_analysis': {'source_sha256': 'source', 'cuts': [
                    {'frame': 251, 'time': 10.427083}, {'frame': 582, 'time': 24.232542},
                    {'frame': 599, 'time': 24.941583}, {'frame': 617, 'time': 25.692333},
                    {'frame': 626, 'time': 26.067708}]}}

    def test_real_250_331_186_frame_segments_exclude_rounded_boundary_cuts(self):
        p = self.project()
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][0]), [(0, 250)])
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][1]), [(250, 581)])
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][2]), [(581, 598), (598, 616), (616, 625), (625, 767)])
        for cut in p['source_analysis']['cuts']:
            cut.pop('frame')
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][0]), [(0, 250)])
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][1]), [(250, 581)])

    def test_frame_numbers_win_over_rounded_times_and_real_single_frame_shots_survive(self):
        p = self.project();p['source_analysis']['cuts'].extend([
            {'frame': 583, 'time': 24.232542}, {'frame': 583, 'time': 24.27425}])
        intervals = shot_analysis.shot_intervals(p, p['segments'][2])
        self.assertEqual(intervals[:2], [(581, 582), (582, 598)])
        self.assertEqual(sum(b-a for a, b in intervals), 186)
        p['source_analysis']['source_sha256'] = 'old'
        self.assertEqual(shot_analysis.shot_intervals(p, p['segments'][2]), [(581, 767)])

    def test_inspector_has_no_boundary_identity_errors_but_keeps_real_shot_checks(self):
        p = self.project()
        p['source_actors'] = [{'id': 'singer', 'label': '主唱', 'scope': 'preserve', 'observations': [
            {'segment_id': segment['id'], 'time': segment['start'], 'confirmed': True, 'visibility': 'visible', 'box':[.1,.1,.4,.8]}
            for segment in p['segments']]}]
        p['source']['path'] = '/missing-isolated-fixture.mp4'
        result = replacement_workflow.inspect_plan(p)
        issues = [row for row in result['issues'] if row['code']=='SHOT_IDENTITY_UNCONFIRMED']
        self.assertEqual([(row['segment_id'],row['first_frame'],row['last_frame']) for row in issues],
                         [('C',599,616),('C',617,625),('C',626,767)])


class BlackMaskEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root/'source.mkv'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=2',
                    '-an', '-c:v', 'ffv1', '-threads', '2', cls.source])
        cls.frames = np.zeros((48, 64, 96), np.uint8)
        cls.frames[:12, :20, :20] = 255; cls.frames[36:, :20, :20] = 255
        cls.masks = {}
        for name in ('correct', 'one-white-pixel', 'wrong-count', 'wrong-rate', 'nonbinary', 'all-black'):
            frames = cls.frames.copy(); rate = '24'
            if name == 'one-white-pixel': frames[24, 30, 30] = 255
            if name == 'wrong-count': frames = frames[:-1]
            if name == 'wrong-rate': rate = '30'
            if name == 'nonbinary': frames[24, 30, 30] = 128
            if name == 'all-black': frames[:] = 0
            path = cls.root/(name+'.mkv')
            result = subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'gray',
                                     '-s', '96x64', '-r', rate, '-i', 'pipe:0', '-an', '-c:v', 'ffv1',
                                     '-pix_fmt', 'gray', '-threads', '2', str(path)], input=frames.tobytes(), capture_output=True)
            if result.returncode: raise RuntimeError(result.stderr.decode())
            cls.masks[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def project(self, mask='correct'):
        def asset(path): return {'id': path.stem, 'path': str(path), 'sha256': media._hash(path)}
        p = {'id': 'isolated', 'workflow': 'replacement', 'revision': 1,
             'source': asset(self.source), 'source_meta': media.probe(self.source), 'timeline_duration': 2,
             'segments': [{'id': 'C', 'name': 'C段', 'start': 0, 'end': 2, 'edit_mask': asset(self.masks[mask])}],
             'source_actors': [{'id': 'singer', 'label': '主唱', 'source_description': '白毛领主唱及倒影',
                                'scope': 'person', 'target_role_id': 'role', 'observations': [
                                    {'segment_id': 'C', 'time': 0, 'visibility': 'visible', 'confirmed': True, 'box': [.1,.1,.4,.8]},
                                    {'segment_id': 'C', 'time': 1.5, 'visibility': 'occluded', 'confirmed': True, 'box': [.1,.1,.4,.8]}]}],
             'roles': [{'id': 'role', 'name': '二郎神', 'images': [asset(self.source)]}],
             'scene': {'mode': 'preserve', 'images': [], 'regions': []}, 'settings': {'resolution': '720P'}}
        p['source_analysis'] = {'source_sha256': p['source']['sha256'], 'cuts': [{'frame':13,'time':.5}, {'frame':37,'time':1.5}]}
        return replacement.normalize_project(p)

    def identities(self, p):
        result = replacement_workflow.inspect_plan(p)
        return result, [row for row in result['issues'] if row['code'] in ('ACTOR_NOT_LOCATED', 'IDENTITY_UNCONFIRMED', 'SHOT_IDENTITY_UNCONFIRMED')]

    def test_all_black_middle_shot_needs_no_out_record_and_check_is_read_only(self):
        p = self.project();before = copy.deepcopy(p)
        result, issues = self.identities(p)
        self.assertEqual(issues, [])
        evidence = result['preserved_intervals']['C']
        self.assertEqual(evidence['black_intervals'], [{'first_frame':13,'last_frame':36}])
        self.assertEqual(evidence['fingerprint'], replacement.fingerprint(p))
        self.assertFalse(evidence['identity_approved'])
        self.assertTrue(result['ready'], result['issues'])
        self.assertEqual(p, before)
        self.assertEqual(len(p['source_actors'][0]['observations']), 2)
        self.assertEqual(p['settings']['resolution'], '720P')

    def test_one_actual_white_pixel_requires_identity_for_that_shot(self):
        result, issues = self.identities(self.project('one-white-pixel'))
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['code'], 'SHOT_IDENTITY_UNCONFIRMED')
        self.assertEqual((issues[0]['first_frame'], issues[0]['last_frame']), (13, 36))
        self.assertFalse(result['ready'])
        self.assertFalse(result['segment_plans']['C']['ready'])
        self.assertTrue(any(row['code']=='SHOT_IDENTITY_UNCONFIRMED' for row in result['segment_plans']['C']['issues']))

    def test_missing_stale_wrong_cadence_count_and_nonbinary_masks_cannot_excuse_identity(self):
        for name in ('wrong-count', 'wrong-rate', 'nonbinary', 'missing', 'stale', 'unversioned', 'source-stale'):
            with self.subTest(name=name):
                p = self.project(name if name in self.masks else 'correct')
                if name == 'missing': p['segments'][0]['edit_mask'] = None
                if name == 'stale': p['segments'][0]['edit_mask']['sha256'] = 'old'
                if name == 'unversioned': p['segments'][0]['edit_mask'].pop('sha256')
                if name == 'source-stale':
                    p['source']['sha256'] = 'old';p['source_analysis']['source_sha256'] = 'old'
                result, issues = self.identities(p)
                self.assertEqual(result['preserved_intervals'], {})
                self.assertTrue(any(row['code']=='SHOT_IDENTITY_UNCONFIRMED' for row in issues))

    def test_black_intervals_do_not_excuse_unconfirmed_visible_regions_or_unbound_roles(self):
        p = self.project();p['source_actors'][0]['observations'][0]['confirmed'] = False
        p['source_actors'][0]['target_role_id'] = ''
        result, issues = self.identities(p)
        self.assertTrue(any(row['code']=='IDENTITY_UNCONFIRMED' for row in issues))
        self.assertTrue(any(row['code']=='TARGET_NOT_BOUND' for row in result['issues']))

    def test_full_black_mask_and_cached_fingerprint_do_not_create_out_observations(self):
        p = self.project('all-black');p['source_actors'][0]['observations'] = []
        check, evidence = identity_evidence.inspect_mask(p, p['segments'][0])
        self.assertTrue(check['ready']);self.assertIsNotNone(evidence)
        self.assertFalse(any(row['code']=='ACTOR_NOT_LOCATED' for row in replacement.plan(p,preserved_evidence={'C':evidence})['issues']))
        p['source_actors'][0]['label'] = '修改后'
        self.assertTrue(any(row['code']=='ACTOR_NOT_LOCATED' for row in replacement.plan(p,preserved_evidence={'C':evidence})['issues']))
        self.assertEqual(p['source_actors'][0]['observations'], [])

    def test_file_changed_during_check_cannot_yield_preserved_evidence(self):
        p = self.project()
        original = replacement.fingerprint
        calls = 0
        def changing(project):
            nonlocal calls
            calls += 1
            return original(project) if calls == 1 else 'changed-during-read'
        with patch.object(replacement, 'fingerprint', side_effect=changing):
            check, evidence = identity_evidence.inspect_mask(p, p['segments'][0])
        self.assertTrue(check['ready']);self.assertIsNone(evidence)


if __name__ == '__main__': unittest.main()
