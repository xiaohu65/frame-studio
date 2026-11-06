"""Independent workflow integration checks; never perform external requests."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend import media, provider, store, workflow
REAL_CONFIG=workflow.config


class OfflineWan(provider.WanClient):
    attempted_posts=0
    def _upload(self,path):return 'oss://dashscope-instant/offline/'+path.name
    def _request_json(self,method,url,body=None,extra_headers=None):
        if method=='POST':
            type(self).attempted_posts+=1
            raise provider.ProviderError('测试：提交响应丢失，状态未知')
        raise AssertionError('测试禁止外网请求')


class WorkflowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir=tempfile.TemporaryDirectory();cls.source=Path(cls.media_dir.name)/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=c=gold:s=320x240:r=30:d=2','-f','lavfi','-i','sine=frequency=600:sample_rate=48000:duration=2.1',
                        '-filter:v','setpts=PTS+0.041/TB','-fps_mode','vfr','-c:v','libx264','-c:a','aac',str(cls.source)],check=True)

    @classmethod
    def tearDownClass(cls):cls.media_dir.cleanup()

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.data=Path(self.folder.name)
        self.patches=[patch.object(store,'DATA',self.data),patch.object(workflow,'DATA',self.data),patch.object(workflow,'CONFIG',self.data/'private-config.json'),
                      patch.object(workflow,'RUNTIME_KEY',None),patch('backend.provider.WanClient',OfflineWan),
                      patch.object(workflow,'config',lambda private=False:{'api_key':'offline-test-only',
                          'base_url':'https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1'} if private else {'configured':True})]
        for p in self.patches:p.start()
        OfflineWan.attempted_posts=0
        p=store.create_project('集成测试');self.pid=p['id']
        # These fixtures exercise the older puppet adapter with a fake provider.
        p['workflow']='legacy';p['settings'].pop('model',None)
        p['settings']['resolution']='480P'
        p['cloud_gate']={'upload_allowed':True,'generation_allowed':True}
        store.put_project(p)
        workflow.attach(self.pid,self.source,'source')
        p=store.get_project(self.pid)
        p['segments']=[{'id':'A','name':'A段','start':0,'end':2,'reference':None,'generated':None,'reviewed':False,'status':'NEEDS_REVIEW'}]
        p['timeline_duration']=2;p['authorization']['budget_yuan']=100
        store.put_project(p)
        workflow.attach(self.pid,self.source,'puppet',segment_id='A')

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.folder.cleanup()

    def approve(self,kind='puppet'):
        p=store.get_project(self.pid);s=p['segments'][0];s['reference']['kind']=kind
        s['reviewed']=True;s['reviewed_sha256']=store.digest(self.source);store.put_project(p)

    def test_preflight_is_local_and_does_not_approve_or_submit(self):
        result=workflow.action(self.pid,{'action':'preflight','segment_id':'A'},'preflight-only')
        self.assertEqual(OfflineWan.attempted_posts,0)
        self.assertFalse(store.get_project(self.pid)['segments'][0]['reviewed'])
        self.assertIn('关节人偶尚待复核',result['blockers'])
        self.assertTrue(result['total_reserved_yuan']>0)
        self.assertEqual(list((self.data/'wan-tasks').glob('request-*.json')),[])

    def test_unreviewed_reference_cannot_submit(self):
        with self.assertRaises(ValueError):workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':100},'blocked')
        self.assertEqual(OfflineWan.attempted_posts,0)

    def test_legacy_reference_cannot_submit_even_if_client_sets_review_flags(self):
        self.approve('legacy_reference')
        with self.assertRaises(ValueError):workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':100},'legacy-bypass')
        self.assertEqual(OfflineWan.attempted_posts,0)

    def test_budget_rejection_occurs_before_upload_or_post(self):
        self.approve()
        with self.assertRaises(provider.ProviderError):workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':.1},'too-small')
        self.assertEqual(OfflineWan.attempted_posts,0)

    def test_unknown_submission_is_not_repeated_on_second_action(self):
        self.approve()
        first=workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':100},'first-ui-job')
        self.assertEqual(first['status'],'UNKNOWN')
        try:
            workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':100},'second-ui-job')
        except (ValueError,provider.ProviderError):pass
        self.assertEqual(OfflineWan.attempted_posts,1,'UNKNOWN cannot be retried with a new job ID')

    def test_audio_extraction_aligns_to_video_first_pts(self):
        result=workflow.action(self.pid,{'action':'extract_audio'},'extract')
        self.assertAlmostEqual(result['report']['source_start_seconds'],media.probe(self.source)['video_start_time'])
        self.assertAlmostEqual(result['report']['source_duration_seconds'],2)
        p=store.get_project(self.pid)
        self.assertEqual(p['music']['mode'],'original')
        self.assertTrue(media.probe(p['music']['asset']['path'])['has_audio'])

    def test_legacy_generated_assemble_cannot_retime_footage(self):
        p=store.get_project(self.pid);p['segments'][0]['generated']={'path':str(self.source)};store.put_project(p)
        anchors={'A':[{'generated':0,'source':0},{'generated':1,'source':.8},{'generated':media.probe(self.source)['duration_seconds'],'source':2}]}
        with patch.object(media,'assemble') as assemble:
            with self.assertRaisesRegex(ValueError,'旧成片导出已停用'):
                workflow.action(self.pid,{'action':'assemble','anchors':anchors},'assembled')
        assemble.assert_not_called()
        self.assertIsNone(store.get_project(self.pid)['final'])

    def test_reference_change_invalidates_review_before_submit(self):
        self.approve()
        changed=Path(self.folder.name)/'changed.mp4';changed.write_bytes(self.source.read_bytes()+b'changed')
        workflow.attach(self.pid,changed,'puppet',segment_id='A')
        self.assertFalse(store.get_project(self.pid)['segments'][0]['reviewed'])
        with self.assertRaises(ValueError):workflow.action(self.pid,{'action':'submit','segment_id':'A','budget_yuan':100},'changed-source')
        self.assertEqual(OfflineWan.attempted_posts,0)

    def test_local_preview_assembles_references_without_generation_or_final(self):
        result=workflow.action(self.pid,{'action':'assemble_preview'},'local-preview')
        p=store.get_project(self.pid)
        self.assertEqual(OfflineWan.attempted_posts,0)
        self.assertFalse(result['generation_submitted'])
        self.assertTrue(Path(p['local_preview']['path']).is_file())
        self.assertIsNone(p['final'])
        self.assertIsNone(p['segments'][0]['generated'])
        self.assertEqual(Path(result['report']['plan'][0]['path']),self.source.resolve())

    def test_client_cannot_inject_review_flags_or_unregistered_reference_path(self):
        p=store.get_project(self.pid);s=p['segments'][0]
        s['reviewed']=True;s['reviewed_sha256']=store.digest(self.source)
        s['reference']={'id':'not-registered','path':'/arbitrary/private/file','kind':'puppet'}
        store.update_project(self.pid,{'segments':[s]})
        current=store.get_project(self.pid)['segments'][0]
        self.assertFalse(current['reviewed'])
        self.assertEqual(Path(current['reference']['path']).resolve(),self.source.resolve())

    def test_music_mode_selects_corresponding_registered_asset(self):
        workflow.action(self.pid,{'action':'extract_audio'},'extract-for-mode')
        p=store.get_project(self.pid);original=p['music']['asset']
        with self.assertRaises(ValueError):store.update_project(self.pid,{'music':{**p['music'],'mode':'separated'}})
        custom=Path(self.folder.name)/'custom.wav'
        media.extract_audio(self.source,custom,start=0,duration=2)
        workflow.attach(self.pid,custom,'music')
        p=store.get_project(self.pid);self.assertNotEqual(p['music']['asset']['id'],original['id'])
        switched=store.update_project(self.pid,{'music':{**p['music'],'mode':'original'}})
        self.assertEqual(switched['music']['asset']['id'],original['id'])

    def test_private_config_api_key_is_not_returned_and_file_is_private(self):
        secret='offline-test-key-never-visible-1234'
        workflow.set_config({'api_key':secret,'persist_key':True,'base_url':'https://dashscope.aliyuncs.com/api/v1'})
        public=REAL_CONFIG()
        self.assertTrue(public['configured'])
        self.assertNotIn(secret,json.dumps(public))
        self.assertNotIn('api_key',public)
        self.assertEqual(os.stat(workflow.CONFIG).st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
