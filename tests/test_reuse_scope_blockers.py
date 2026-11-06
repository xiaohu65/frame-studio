"""Real mask and shot scope gates remain separate from raw sample validity."""
import copy
from fractions import Fraction
import unittest
from unittest.mock import patch

import test_completed_sample_reuse as reuse_fixtures
from backend import generation, media, replacement_media, replacement_workflow as rw, shot_analysis, store, workflow


_REAL_VALIDATE_MASK = replacement_media.validate_mask


class ReuseScopeBlockerContracts(unittest.TestCase):
    tearDownClass = classmethod(reuse_fixtures.CompletedSampleReuseContracts.tearDownClass.__func__)
    seed_preparation = reuse_fixtures.CompletedSampleReuseContracts.seed_preparation
    run_jobs = reuse_fixtures.CompletedSampleReuseContracts.run_jobs
    prepare = reuse_fixtures.CompletedSampleReuseContracts.prepare
    confirmation = reuse_fixtures.CompletedSampleReuseContracts.confirmation
    drive = reuse_fixtures.CompletedSampleReuseContracts.drive
    completed = reuse_fixtures.CompletedSampleReuseContracts.completed

    @classmethod
    def setUpClass(cls):
        reuse_fixtures.CompletedSampleReuseContracts.setUpClass.__func__(cls)
        cls.scope_fixtures = {}
        for name, fps, first_count in [('native30', '30', 30), ('native23976', '24000/1001', 24)]:
            first_duration = Fraction(first_count) / Fraction(fps)
            source = cls.material_root / (name + '_two_shots.mp4')
            media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                f'color=blue:s=640x480:r={fps}:d={float(first_duration):.9f}', '-f', 'lavfi', '-i',
                f'color=red:s=640x480:r={fps}:d={float(first_duration * 3):.9f}',
                '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', '-fps_mode', 'passthrough',
                '-enc_time_base', '1/24000', '-video_track_timescale', '24000', source])
            masks = {}
            for label, enable in [('black_second_shot', f":enable='lt(n,{first_count})'"), ('white_second_shot', '')]:
                path = cls.material_root / (name + '_' + label + '.mkv')
                media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                    f'color=black:s=640x480:r={fps}', '-frames:v', str(first_count * 2),
                    '-vf', 'drawbox=x=0:y=0:w=160:h=480:color=white:t=fill' + enable + ',format=gray',
                    '-c:v', 'ffv1', '-threads', '2', path])
                masks[label] = path
            analysis = shot_analysis.analyze(source, threshold=.05)
            if not any(cut['frame'] == first_count + 1 for cut in analysis['cuts']):
                raise AssertionError('The physical blue/red transition was not detected at its actual frame')
            cls.scope_fixtures[name] = {'source': source, 'masks': masks, 'analysis': analysis,
                'first_count': first_count, 'fps': fps, 'first_duration': first_duration}

    def setUp(self):
        reuse_fixtures.CompletedSampleReuseContracts.setUp(self)
        # The generation fixture normally stubs binary-mask validation. These
        # tests deliberately run the actual decode, binary and black-range checks.
        real = patch.object(replacement_media, 'validate_mask', side_effect=_REAL_VALIDATE_MASK)
        real.start(); self.addCleanup(real.stop)

    def install_shots(self, name='native30', *, confirm_second=False):
        fixture = self.scope_fixtures[name]
        asset = store.add_asset(self.pid, fixture['source'], 'source', meta=media.probe(fixture['source']))
        project = store.get_project(self.pid)
        midpoint = float(fixture['first_duration'] * 2)
        duration = float(fixture['first_duration'] * 4)
        project.update(source=asset, source_meta={**asset['meta'], 'duration': duration},
            timeline_duration=duration, source_analysis=copy.deepcopy(fixture['analysis']))
        project['segments'][0].update(start=0, end=midpoint)
        project['segments'][1].update(start=midpoint, end=duration)
        observations = project['source_actors'][0]['observations']
        observations[1]['time'] = midpoint
        if confirm_second:
            observations.append({**copy.deepcopy(observations[0]), 'time': float(fixture['first_duration'])})
        store.put_project(project)
        workflow.attach(self.pid, fixture['masks']['black_second_shot'], 'edit_mask', segment_id='A')
        return fixture

    def enable_second_shot(self, fixture):
        workflow.attach(self.pid, fixture['masks']['white_second_shot'], 'edit_mask', segment_id='A')
        return rw.inspect_plan(store.get_project(self.pid), 'A')

    def test_unconfirmed_editable_shot_blocks_material_plan_but_keeps_raw_sample(self):
        fixture = self.install_shots()
        before = rw.inspect_plan(store.get_project(self.pid), 'A')
        self.assertTrue(before['ready'], before['issues'])
        self.assertEqual(before['preserved_intervals']['A']['black_intervals'], [{'first_frame': 31, 'last_frame': 60}])
        run_id = self.completed()
        run_bytes = generation._run_path(self.pid, run_id).read_bytes()
        raw = store.get_project(self.pid)['segments'][0]['sample_candidate']
        after = self.enable_second_shot(fixture)
        self.assertFalse(after['ready'])
        blockers = [issue for issue in after['issues'] if issue.get('blocking')]
        self.assertEqual([issue['code'] for issue in blockers], ['SHOT_IDENTITY_UNCONFIRMED'])
        self.assertEqual((blockers[0]['first_frame'], blockers[0]['last_frame']), (31, 60))
        generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        self.run_jobs()
        sample = generation.state(self.pid)['sample_states']['A']
        self.assertTrue(sample['input_current'])
        self.assertEqual(sample['candidate_asset_id'], raw['id'])
        self.assertFalse(sample['final_approved'])
        self.assertFalse(store.get_project(self.pid)['segments'][0].get('sample_preview'))
        self.assertEqual(generation._run_path(self.pid, run_id).read_bytes(), run_bytes)
        self.assertEqual(len(self.cloud.posts), 1)

    def test_23976_raw_sample_does_not_inherit_strict_final_compatibility_block(self):
        fixture = self.install_shots('native23976', confirm_second=True)
        ready = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(ready))['current_run']['id']
        self.cloud.default_status = 'PENDING'; self.drive(run_id)
        self.cloud.remote[self.cloud.posts[0]['task_id']]['source'] = str(self.reference)
        self.cloud.default_status = 'SUCCEEDED'; self.drive(run_id, allow_submit=False)
        plan = self.enable_second_shot(fixture)
        self.assertTrue(plan['ready'], plan['issues'])
        self.assertEqual([issue for issue in plan['issues'] if issue.get('blocking')], [])
        self.assertFalse(plan['wan_compatibility']['ready'])
        self.assertIn('MODEL_FPS_INCOMPATIBLE', [issue['code'] for issue in plan['wan_compatibility']['issues']])
        generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        self.run_jobs()
        project = store.get_project(self.pid)
        segment = project['segments'][0]
        sample = generation.state(self.pid)['sample_states']['A']
        self.assertTrue(sample['input_current'])
        self.assertFalse(sample['final_approved'])
        self.assertEqual(segment['sample_provenance']['return_inspection']['actual']['frame_count'], 60)
        self.assertFalse(segment.get('sample_preview'))
        self.assertFalse(segment.get('generated'))
        self.assertEqual(len(self.cloud.posts), 1)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ReuseScopeBlockerContracts(name) for name in ReuseScopeBlockerContracts.__dict__ if name.startswith('test_'))
