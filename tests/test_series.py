"""Series identity contracts, using local images and no cloud calls."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend import store, series, replacement


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.override = patch.object(store, 'DATA', self.root / 'data')
        self.override.start()
        self.image = self.root / 'target.png'
        Image.new('RGB', (16, 24), '#a47533').save(self.image)
        self.s = series.create_series('原剧角色替换')
        self.p1 = self.project('第1集')
        self.p2 = self.project('第2集')
        self.attach(self.p1, 1)
        self.attach(self.p2, 2)
        result = series.publish_character(self.s['id'], self.p1['id'], 'person', name='原剧主角', aliases=['少年期'])
        self.cid = result['character']['id']

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def project(self, name):
        p = store.create_project(name)
        a = store.add_asset(p['id'], self.image, 'reference')
        p = store.get_project(p['id'])
        p['roles'] = [{'id': 'target', 'name': '新主角', 'description': '固定脸型与发型', 'color': '#a47533', 'images': [a]}]
        p['source_actors'] = [{'id': 'person', 'label': '蓝衣主角', 'source_description': '同一个剧中角色，蓝衣且持剑',
                               'scope': 'person', 'target_role_id': 'target', 'observations': []}]
        return store.put_project(p)

    def attach(self, p, number):
        return series.attach_episode(self.s['id'], p['id'], number,
                                     expected_project_revision=store.get_project(p['id'])['revision'])

    def bind(self, p, version=1):
        return series.bind_actor(self.s['id'], p['id'], 'person', self.cid, version,
                                 expected_project_revision=store.get_project(p['id'])['revision'])['project']

    def confirm(self, p):
        return series.confirm_binding(self.s['id'], p['id'], 'person', True,
                                      expected_project_revision=store.get_project(p['id'])['revision'])['project']

    def test_two_episodes_share_pinned_reference_version_without_auto_confirmation(self):
        a, b = self.bind(self.p1), self.bind(self.p2)
        self.assertEqual(a['source_actors'][0]['series_binding']['target_fingerprint'], b['source_actors'][0]['series_binding']['target_fingerprint'])
        self.assertFalse(a['source_actors'][0]['series_binding']['confirmed'])
        self.assertEqual(a['roles'][0]['images'][0]['path'], b['roles'][0]['images'][0]['path'])
        self.assertTrue(Path(a['roles'][0]['images'][0]['path']).is_file())
        self.assertNotEqual(a['roles'][0]['images'][0]['id'], b['roles'][0]['images'][0]['id'])
        self.confirm(self.p1)
        self.assertFalse(store.get_project(self.p2['id'])['source_actors'][0]['series_binding']['confirmed'])

    def test_publishing_version_does_not_replace_an_existing_episode(self):
        pinned = self.bind(self.p2)
        old = copy.deepcopy(pinned['roles'])
        p = store.get_project(self.p1['id'])
        p['roles'][0]['description'] = '另一版服装与发型，必须明确采用'
        store.update_project(p['id'], {'roles': p['roles']})
        result = series.publish_character(self.s['id'], self.p1['id'], 'person', self.cid)
        self.assertEqual(result['version']['version'], 2)
        current = store.get_project(self.p2['id'])
        self.assertEqual(current['roles'], old)
        self.assertEqual(current['source_actors'][0]['series_binding']['version'], 1)
        issue = next(i for i in series.project_issues(current) if i['code'] == 'SERIES_NEW_VERSION_AVAILABLE')
        self.assertFalse(issue['blocking'])

    def test_put_cannot_forge_series_binding_or_change_series_link(self):
        p = store.get_project(self.p2['id'])
        p['source_actors'][0]['series_binding'] = {'character_id': self.cid, 'version': 1, 'confirmed': True}
        edited = store.update_project(p['id'], {'source_actors': p['source_actors'], 'series_link': {'series_id': 'fake', 'episode_number': 999}})
        self.assertNotIn('series_binding', edited['source_actors'][0])
        self.assertEqual(edited['series_link']['episode_number'], 2)
        p = self.bind(self.p2)
        p['source_actors'][0]['series_binding']['confirmed'] = True
        saved = store.update_project(p['id'], {'source_actors': p['source_actors']})
        self.assertFalse(saved['source_actors'][0]['series_binding']['confirmed'])

    def test_identity_description_change_requires_reconfirmation(self):
        self.bind(self.p1)
        p = self.confirm(self.p1)
        p['source_actors'][0]['source_description'] = '修改为另一人物辨认依据'
        saved = store.update_project(p['id'], {'source_actors': p['source_actors']})
        self.assertFalse(saved['source_actors'][0]['series_binding']['confirmed'])
        self.assertIn('SERIES_BINDING_UNCONFIRMED', {i['code'] for i in series.project_issues(saved)})

    def test_changing_target_role_id_revokes_confirmation_and_blocks_wrong_version(self):
        self.bind(self.p1)
        p = self.confirm(self.p1)
        other = copy.deepcopy(p['roles'][0])
        other.update(id='different', name='另一个目标身份')
        p['roles'].append(other)
        p['source_actors'][0]['target_role_id'] = 'different'
        saved = store.update_project(p['id'], {'roles': p['roles'], 'source_actors': p['source_actors']})
        self.assertFalse(saved['source_actors'][0]['series_binding']['confirmed'])
        self.assertIn('SERIES_TARGET_MISMATCH', {i['code'] for i in series.project_issues(saved)})

    def test_series_metadata_put_cannot_rewrite_published_versions(self):
        before = series.get_series(self.s['id'])
        edited = series.update_series(self.s['id'], {'expected_revision': before['revision'],
            'characters': [{'id': self.cid, 'name': '原角色新显示名',
                            'versions': [{'version': 1, 'name': '伪造素材', 'images': []}]}],
            'episodes': []})
        self.assertEqual(edited['characters'][0]['versions'], before['characters'][0]['versions'])
        self.assertEqual(edited['episodes'], before['episodes'])

    def test_target_mismatch_and_frozen_asset_tamper_block_confirmation(self):
        p = self.bind(self.p1)
        p['roles'][0]['description'] = '偷偷更换外观设定'
        p = store.update_project(p['id'], {'roles': p['roles']})
        self.assertIn('SERIES_TARGET_MISMATCH', {i['code'] for i in series.project_issues(p)})
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.confirm(self.p1)
        p = self.bind(self.p1)
        Path(p['roles'][0]['images'][0]['path']).write_bytes(b'changed')
        self.assertIn('SERIES_ASSET_CHANGED', {i['code'] for i in series.project_issues(p)})
        with self.assertRaisesRegex(ValueError, '改写'):
            self.confirm(self.p1)

    def test_plan_has_series_blocks_and_fingerprint_tracks_series_episode(self):
        p = store.get_project(self.p1['id'])
        codes = {i['code'] for i in replacement.plan(p)['issues']}
        self.assertIn('SERIES_CHARACTER_NOT_BOUND', codes)
        before = replacement.fingerprint(p)
        p['series_link']['episode_number'] = 100
        self.assertNotEqual(replacement.fingerprint(p), before)

    def test_standalone_input_fingerprint_stays_compatible_with_pre_series_format(self):
        # Frozen pre-series fixture: adding the optional feature must not
        # invalidate prepared media in projects that never joined a series.
        p = {'workflow': 'replacement', 'source': None, 'source_meta': {}, 'segments': [],
             'source_actors': [], 'roles': [], 'scene': {'mode': 'preserve', 'images': [], 'description': '原片保留', 'regions': []},
             'settings': {'model': 'wan3.0-video', 'resolution': '1080P', 'watermark': False, 'audio': False,
                          'prompt_extend': False, 'ratio': 'adaptive', 'duration': -1},
             'prompts': {}, 'preservation': replacement.PRESERVATION}
        expected = '8c6f4649485320e7cd551ef97cbb7a04a4406193b98edd0488e35d9bad241ba7'
        self.assertEqual(replacement.fingerprint(p), expected)
        p['series_link'] = None
        self.assertEqual(replacement.fingerprint(p), expected)
        p['series_link'] = {}
        self.assertEqual(replacement.fingerprint(p), expected)

    def test_one_hundred_episode_creation_is_metadata_only_and_numbers_unique(self):
        s = series.create_series('百集测试')
        result = series.create_episodes(s['id'], 1, 100, expected_revision=s['revision'])
        self.assertEqual(len(result['projects']), 100)
        d = series.dashboard(s['id'])
        self.assertEqual(d['counts']['without_source'], 100)
        self.assertEqual([e['episode_number'] for e in d['episodes']], list(range(1, 101)))
        self.assertTrue(all(e['status'] == 'NEEDS_SOURCE' for e in d['episodes']))
        self.assertFalse(d['cloud_generation_authorized'])
        with self.assertRaisesRegex(ValueError, '已有项目'):
            series.create_episodes(s['id'], 50, 2)
        self.assertEqual(len(series.get_series(s['id'])['episodes']), 100)

    def test_stale_revision_rejected_before_project_is_changed(self):
        p = store.get_project(self.p1['id'])
        with self.assertRaises(store.RevisionConflict):
            series.bind_actor(self.s['id'], p['id'], 'person', self.cid, 1, expected_project_revision=p['revision'] - 1)
        self.assertNotIn('series_binding', store.get_project(p['id'])['source_actors'][0])

    def test_dashboard_uses_metadata_without_hashing_every_episode_video(self):
        self.bind(self.p1)
        with patch.object(store, 'digest', side_effect=AssertionError('dashboard must not hash media')):
            d = series.dashboard(self.s['id'])
        self.assertEqual(len(d['episodes']), 2)
        self.assertEqual(d['episodes'][0]['status'], 'NEEDS_SOURCE')

    def test_asset_lookup_returns_only_registered_series_reference(self):
        s = series.get_series(self.s['id'])
        a = s['characters'][0]['versions'][0]['images'][0]
        self.assertEqual(series.series_asset(self.s['id'], a['id'])['sha256'], a['sha256'])
        self.assertIsNone(series.series_asset(self.s['id'], 'unregistered'))


if __name__ == '__main__':
    unittest.main()
