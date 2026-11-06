from __future__ import annotations
from pathlib import Path
import copy, hashlib, json, math, os, re, threading, time, uuid

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.environ.get('FRAME_STUDIO_DATA',ROOT/'data')).resolve()
DATA.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()

class RevisionConflict(ValueError):
 """The visible project changed since the browser began this edit."""
 def __init__(self,current_revision):
  self.current_revision=current_revision
  super().__init__('项目已被其他操作更新，请刷新后核对修改；本次未覆盖较新内容')

def now(): return time.strftime('%Y-%m-%dT%H:%M:%S%z')
def uid(): return uuid.uuid4().hex[:16]
def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def save_json(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+uid()+'.tmp')
 with tmp.open('w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
 os.replace(tmp,path)
def project_path(pid):
 if not pid.isalnum():raise ValueError('无效项目编号')
 return DATA/'projects'/pid/'project.json'
def get_project(pid):
 from .replacement import normalize_project
 from .replacement_workflow import refresh_adaptation_state
 with LOCK:return refresh_adaptation_state(normalize_project(json.loads(project_path(pid).read_text())))
def put_project(p):
 from .replacement import normalize_project,fingerprint
 with LOCK:
  normalize_project(p)
  path=project_path(p['id'])
  prior=json.loads(path.read_text()) if path.exists() else None
  if prior and p.get('workflow')=='replacement' and prior.get('workflow')=='replacement':
   if 'delivery_revision' in prior:p['delivery_revision']=prior['delivery_revision']
   if any(prior.get('settings', {}).get(k)!=p['settings'].get(k) for k in ('model','resolution','ratio')):
    p['delivery_revision']=prior.get('delivery_revision',0)+1
   # Long-running actions must re-read before committing their own result.
   # Material changes always discard approval tied to an older input snapshot.
   if fingerprint(prior)!=fingerprint(p):
    p['generic_review']=None;p['preparation']=None;p['replacement_plan']=None
    for segment in p.get('segments',[]):
     segment['candidate_review']=None
     if any(segment.get(k) for k in ['generated','composited','composite_preview']):segment['candidate_stale']=True
  from .replacement_workflow import refresh_adaptation_state
  refresh_adaptation_state(p)
  p['revision']=int((prior or {}).get('revision',0))+1
  p['updated_at']=now();save_json(project_path(p['id']),p)
 return p
def update_project(pid,changes):
 with LOCK:
  p=get_project(pid)
  if 'expected_revision' in changes and changes['expected_revision']!=p.get('revision',0):raise RevisionConflict(p.get('revision',0))
  registered={a['id']:a for a in p.get('assets',[])}
  def registered_asset(a):
   if a is None:return None
   if not isinstance(a,dict) or a.get('id') not in registered:raise ValueError('素材必须先上传或导入登记')
   return copy.deepcopy(registered[a['id']])
  prior_actors={a['id']:copy.deepcopy(a) for a in p.get('source_actors',[])}
  for k in ['name','segments','settings','prompts','roles','scene','source_actors','authorization','music','pose_data']:
   if k in changes:p[k]=copy.deepcopy(changes[k])
  # The catalogue action owns cross-episode identity confirmation. A regular
  # form save cannot forge it, and identity edits require fresh confirmation.
  if 'source_actors' in changes:
   for actor in p.get('source_actors',[]):
    prior=prior_actors.get(actor.get('id'),{})
    binding=copy.deepcopy(prior.get('series_binding'))
    if binding:
     if any(actor.get(k)!=prior.get(k) for k in ['label','source_description','target_role_id','scope']):
      binding['confirmed']=False;binding.pop('confirmed_at',None)
     actor['series_binding']=binding
    else:actor.pop('series_binding',None)
  from .replacement import normalize_project,validate_project
  normalize_project(p)
  p['settings']['watermark']=False
  if p.get('workflow')=='replacement':
   p['settings']['audio']=False;p['settings']['prompt_extend']=False;p['settings']['duration']=-1
  # A changed reference/timing requires a fresh preview review.
  if 'segments' in changes:
   old=get_project(pid)
   ids=set()
   for s in p['segments']:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',str(s.get('id',''))) or s['id'] in ids:raise ValueError('片段编号须唯一')
    ids.add(s['id'])
    start,end=float(s['start']),float(s['end'])
    if not all(map(math.isfinite,[start,end])) or start<0 or not 0<end-start<=15.00001 or end>p.get('source_meta',{}).get('duration',float('inf'))+.001:raise ValueError('片段时间范围无效，每段须在原片范围内且不超过15秒')
    prior=next((x for x in old['segments'] if x['id']==s['id']),{})
    for key in ['reference','generated','legacy_reference','pose_asset','task','reviewed','reviewed_sha256','status',
                'edit_mask','protection_mask','candidate_review','candidate_stale','candidate_validation',
                'composite','composited','composite_preview','composite_report','prepared_source','reference_timeline',
                'mask_preview','mask_render_report','generation_provenance','raw_candidate',
                'sample_candidate','sample_provenance','sample_preview','sample_preview_report',
                'candidate_adaptation','candidate_adaptation_history']:
     if key in prior:s[key]=copy.deepcopy(prior[key])
     else:s.pop(key,None)
    if any(s.get(k)!=prior.get(k) for k in ['start','end','reference','pose_sha256']):
     s['reviewed']=False;s['status']='NEEDS_REVIEW'
  for r in p.get('roles',[]):r['images']=[registered_asset(a) for a in r.get('images',[])]
  p['scene']['images']=[registered_asset(a) for a in p['scene'].get('images',[])]
  if 'music' in changes:
   mode=p['music'].get('mode','original');oldmusic=get_project(pid).get('music',{})
   variants=oldmusic.get('assets',{})
   if mode!=oldmusic.get('mode') and mode not in variants:raise ValueError('请先提取或导入对应音轨，再选择使用')
   if mode in variants:p['music']['asset']=registered_asset(variants[mode])
   elif p['music'].get('asset'):p['music']['asset']=registered_asset(p['music']['asset'])
   p['music']['assets']=variants
  validate_project(p,registered)
  return put_project(p)
def list_projects():
 return sorted([{'id':p['id'],'name':p['name'],'updated_at':p.get('updated_at')} for f in (DATA/'projects').glob('*/project.json') if (p:=json.loads(f.read_text()))],key=lambda p:p.get('updated_at',''),reverse=True)
def create_project(name):
 p={'id':uid(),'name':str(name or '未命名项目')[:100],'created_at':now(),'source':None,'source_meta':{},'segments':[],
 'workflow':'replacement','source_actors':[],'roles':[],
 'scene':{'mode':'preserve','images':[],'description':'保留原片场景、未选人物和道具','regions':[]},'prompts':{},
 'settings':{'model':'wan3.0-video','resolution':None,'ratio':'adaptive','watermark':False,'audio':False,'prompt_extend':False,'duration':-1},
 'cloud_gate':{'upload_allowed':False,'generation_allowed':False},'generic_review':None,'preparation':None,
 'authorization':{'mode':'manual','budget_yuan':0},'music':{'asset':None,'mode':'original'},'final':None,'assets':[]}
 return put_project(p)
def add_asset(pid,path,kind,name=None,meta=None):
 path=Path(path).resolve()
 if not path.is_file():raise ValueError('文件不存在')
 a={'id':uid(),'name':name or path.name,'path':str(path),'kind':kind,'sha256':digest(path),'bytes':path.stat().st_size,'meta':meta or {}}
 a['url']=f"/api/media/{pid}/{a['id']}"
 with LOCK:
  p=get_project(pid);p.setdefault('assets',[]).append(a);put_project(p)
 return a
def asset_by_id(pid,aid):
 return next((a for a in get_project(pid).get('assets',[]) if a['id']==aid),None)
def job_path(jid):
 if not re.fullmatch(r'[a-f0-9]{16}',str(jid)):raise ValueError('无效任务编号')
 return DATA/'jobs'/f'{jid}.json'
def list_jobs(pid=None,limit=40):
 jobs=[json.loads(f.read_text()) for f in (DATA/'jobs').glob('*.json')]
 result=sorted([j for j in jobs if pid is None or j['project_id']==pid],key=lambda j:j['created_at'],reverse=True)
 return result if limit is None else result[:limit]
def update_job(jid,**changes):
 with LOCK:
  p=job_path(jid);j=json.loads(p.read_text());j.update(changes,updated_at=now());save_json(p,j)
 return j
def start_job(pid,action,fn,*,segment_id=None,input_candidate_id=None,input_material_fingerprint=None):
 jid=uid();j={'id':jid,'project_id':pid,'action':action,'status':'QUEUED','message':'等待处理','result':None,'created_at':now(),'updated_at':now()}
 # Scope belongs to the first durable QUEUED record, before any worker starts.
 for key,value in [('segment_id',segment_id),('input_candidate_id',input_candidate_id),('input_material_fingerprint',input_material_fingerprint)]:
  if value is not None:j[key]=value
 save_json(job_path(jid),j)
 def run():
  update_job(jid,status='RUNNING',message='正在处理')
  try:
   result=fn(jid) or {};state=result.pop('_job_status','SUCCEEDED')
   update_job(jid,status=state,message=result.get('message','处理完成'),result=result)
  except Exception as e:
   # Never return tracebacks or backend configuration/credentials.
   msg=str(e)
   for key in ['DASHSCOPE_API_KEY','BAILIAN_API_KEY']:
    secret=os.environ.get(key)
    if secret:msg=msg.replace(secret,'[已隐藏]')
   update_job(jid,status='FAILED',message=msg[:1000],result=None)
 threading.Thread(target=run,daemon=True).start();return {'job_id':jid,'status':'QUEUED'}
def recover_jobs():
 for j in list_jobs(limit=None):
  if j['status'] in ['RUNNING','QUEUED']:update_job(j['id'],status='UNKNOWN',message='工具上次处理时退出；请检查结果后恢复。生成请求不会自动重发。')
