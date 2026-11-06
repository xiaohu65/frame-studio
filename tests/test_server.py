import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

class LocalServerTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.port=18765
  env={**os.environ,'FRAME_STUDIO_DATA':str(cls.root/'data')}
  env.pop('DASHSCOPE_API_KEY',None);env.pop('BAILIAN_API_KEY',None)
  cls.proc=subprocess.Popen([sys.executable,'-m','backend.server','--port',str(cls.port)],env=env,stdout=subprocess.DEVNULL)
  for _ in range(50):
   try:cls.request('/api/state');break
   except Exception:time.sleep(.1)
 @classmethod
 def tearDownClass(cls):cls.proc.terminate();cls.proc.wait(timeout=5);cls.tmp.cleanup()
 @classmethod
 def request(cls,path,data=None,method=None,headers=None,raw=None):
  h={'Content-Type':'application/json',**(headers or {})};body=raw if raw is not None else json.dumps(data).encode() if data is not None else None
  with urlopen(Request(f'http://127.0.0.1:{cls.port}'+path,data=body,method=method,headers=h)) as r:
   b=r.read();return r.status,json.loads(b) if 'json' in r.headers.get('Content-Type','') else b,r.headers
 def test_cross_origin_mutation_rejected(self):
  with self.assertRaises(HTTPError) as e:self.request('/api/projects',{'name':'no'},headers={'Origin':'https://example.com'})
  self.assertEqual(e.exception.code,403)
 def test_upload_range_and_source_review_lifecycle(self):
  _,p,_=self.request('/api/projects',{'name':'上传验证'})
  f=self.root/'sample.mp4'
  subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=blue:s=320x240:r=24:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(f)],check=True)
  data=f.read_bytes()
  _,p,_=self.request(f'/api/upload?project_id={p["id"]}&kind=source&filename=sample.mp4',method='POST',headers={'Content-Type':'application/octet-stream'},raw=data)
  self.assertEqual(len(p['segments']),1);self.assertFalse(p['segments'][0]['reviewed'])
  self.assertEqual(p['source']['name'],'sample.mp4')
  status,part,h=self.request(p['source']['url'],headers={'Range':'bytes=10-99'})
  self.assertEqual(status,206);self.assertEqual(part,data[10:100]);self.assertEqual(h['Content-Length'],'90')
  p['settings']['watermark']=True
  _,p,_=self.request('/api/projects/'+p['id'],{'settings':p['settings']},method='PUT')
  self.assertIs(p['settings']['watermark'],False)
  _,state,_=self.request('/api/state?project_id='+p['id']);self.assertNotIn('api_key',state['config'])
 def test_unknown_action_fails_job_not_process(self):
  _,p,_=self.request('/api/projects',{'name':'失败恢复'})
  _,j,_=self.request('/api/projects/'+p['id']+'/actions',{'action':'invalid'})
  for _ in range(40):
   _,result,_=self.request('/api/jobs/'+j['job_id'])
   if result['status']=='FAILED':break
   time.sleep(.05)
  self.assertEqual(result['status'],'FAILED')
  self.assertEqual(result['message'],'未知操作')
 def wait_job(self,jid):
  for _ in range(100):
   _,job,_=self.request('/api/jobs/'+jid)
   if job['status'] not in ['QUEUED','RUNNING']:return job
   time.sleep(.03)
  self.fail('local action did not finish')
 def test_replacement_step5_routes_scope_jobs_to_the_real_input_candidate(self):
  _,p,_=self.request('/api/projects',{'name':'片段任务范围验证'})
  video=self.root/'scope-source.mp4'
  subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=blue:s=64x64:r=24:d=1',
                  '-c:v','libx264','-threads','2','-pix_fmt','yuv420p',str(video)],check=True)
  route='/api/projects/'+p['id']
  self.request(route+'/import',{'kind':'source','path':str(video)})
  _,p,_=self.request(route+'/import',{'kind':'generated','path':str(video),'segment_id':'A'})
  generated=p['segments'][0]['generated']['id']
  _,state,_=self.request('/api/state?project_id='+p['id'])
  material=state['generation']['material_confirmation']['fingerprint']
  for action in ['validate_candidate','review_candidate']:
   with self.subTest(action=action):
    _,queued,_=self.request(route+'/actions',{'action':action,'segment_id':'A','approved':False})
    job=self.wait_job(queued['job_id'])
    self.assertEqual(job['segment_id'],'A')
    self.assertEqual(job['input_candidate_id'],generated)
    self.assertEqual(job['input_material_fingerprint'],material)
  for action in ['composite_replacement','adapt_candidate','render_mask_support']:
   with self.subTest(retired_action=action):
    with self.assertRaises(HTTPError) as retired:
     self.request(route+'/actions',{'action':action,'segment_id':'A'})
    self.assertEqual(retired.exception.code,400)
    with retired.exception as rejected:
     self.assertIn('已停用生成画面的二次处理',json.load(rejected)['error'])
  for action in ['assemble','replacement_check']:
   with self.subTest(project_action=action):
    _,queued,_=self.request(route+'/actions',{'action':action,'segment_id':'A'})
    job=self.wait_job(queued['job_id'])
    for field in ['segment_id','input_candidate_id','input_material_fingerprint']:
     self.assertNotIn(field,job)
 def test_legacy_actions_do_not_gain_replacement_step5_scope(self):
  _,p,_=self.request('/api/projects',{'name':'保留旧任务范围'})
  path=self.root/'data/projects'/p['id']/'project.json'
  fixture=json.loads(path.read_text());fixture['workflow']='legacy';path.write_text(json.dumps(fixture))
  _,queued,_=self.request('/api/projects/'+p['id']+'/actions',{'action':'validate_candidate','segment_id':'A'})
  job=self.wait_job(queued['job_id'])
  self.assertEqual(job['status'],'FAILED')
  for field in ['segment_id','input_candidate_id','input_material_fingerprint']:
   self.assertNotIn(field,job)
 def test_new_generic_project_conflict_and_cloud_hold(self):
  _,p,_=self.request('/api/projects',{'name':'通用角色替换'})
  self.assertEqual(p['workflow'],'replacement');self.assertEqual(p['roles'],[])
  self.assertFalse(p['cloud_gate']['generation_allowed'])
  _,updated,_=self.request('/api/projects/'+p['id'],{'name':'人工修改','expected_revision':p['revision']},method='PUT')
  with self.assertRaises(HTTPError) as conflict:
   self.request('/api/projects/'+p['id'],{'name':'旧页面覆盖','expected_revision':p['revision']},method='PUT')
  self.assertEqual(conflict.exception.code,409)
  body=json.loads(conflict.exception.read());self.assertEqual(body['code'],'REVISION_CONFLICT')
  _,current,_=self.request('/api/projects/'+p['id']);self.assertEqual(current['name'],'人工修改')
  _,j,_=self.request('/api/projects/'+p['id']+'/actions',{'action':'submit','budget_yuan':1000})
  for _ in range(50):
   _,job,_=self.request('/api/jobs/'+j['job_id'])
   if job['status']=='FAILED':break
   time.sleep(.02)
  self.assertEqual(job['status'],'FAILED');self.assertIn('等待用户',job['message'])
 def test_series_http_creates_100_empty_episodes_without_approving_them(self):
  _,series,_=self.request('/api/series',{'name':'长剧本地管理验证'})
  sid=series['id']
  _,result,_=self.request('/api/series/'+sid+'/actions',{'action':'create_episodes','start':1,'count':100,'expected_revision':series['revision']})
  self.assertEqual(len(result['projects']),100)
  _,dashboard,_=self.request('/api/series/'+sid+'/dashboard')
  self.assertEqual(dashboard['counts']['episodes'],100)
  self.assertEqual(dashboard['counts']['without_source'],100)
  self.assertTrue(all(e['status']=='NEEDS_SOURCE' for e in dashboard['episodes']))
  first=result['projects'][0]
  _,project,_=self.request('/api/projects/'+first['id'])
  self.assertEqual(project['series_link']['episode_number'],1)
  self.assertFalse(project['cloud_gate']['generation_allowed'])
  _,updated,_=self.request('/api/projects/'+first['id'],{'series_link':{'series_id':'fake','episode_number':999},'expected_revision':project['revision']},method='PUT')
  self.assertEqual(updated['series_link'],project['series_link'])
  with self.assertRaises(HTTPError) as stale:
   self.request('/api/series/'+sid+'/actions',{'action':'create_episodes','start':101,'count':1,'expected_revision':series['revision']})
  self.assertEqual(stale.exception.code,409)
  with self.assertRaises(HTTPError) as missing:
   self.request('/api/series-media/'+sid+'/'+'a'*64)
  self.assertEqual(missing.exception.code,404)
if __name__=='__main__':unittest.main()
