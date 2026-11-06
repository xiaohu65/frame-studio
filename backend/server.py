from __future__ import annotations
import argparse,json,mimetypes,os,re,shutil,subprocess
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs,unquote,quote
from . import store,workflow,series,generation
from .store import ROOT,DATA

class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,code,obj):
  data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def allowed(self):
  host=self.headers.get('Host','').split(':')[0]
  origin=self.headers.get('Origin')
  return host in ['127.0.0.1','localhost','['] and (not origin or urlparse(origin).hostname in ['localhost','127.0.0.1']) and self.headers.get('Sec-Fetch-Site')!='cross-site'
 def body(self):
  n=int(self.headers.get('Content-Length','0'))
  if not 0<=n<=2*1024*1024:raise ValueError('请求内容过大')
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('请求须为JSON')
  return json.loads(self.rfile.read(n))
 def do_GET(self):
  try:
   if not self.allowed():return self.reply(403,{'error':'仅允许本机访问'})
   u=urlparse(self.path);parts=u.path.strip('/').split('/');qs=parse_qs(u.query)
   if u.path=='/api/state':
    projects=store.list_projects();pid=(qs.get('project_id') or [projects[0]['id'] if projects else None])[0]
    p=store.get_project(pid) if pid else None
    return self.reply(200,{'projects':projects,'project':p,'config':workflow.config(),'jobs':store.list_jobs(pid),'system':self.system(),'generation':generation.state(pid) if p and p.get('workflow')=='replacement' else None})
   if u.path=='/api/projects':return self.reply(200,store.list_projects())
   if u.path=='/api/series':return self.reply(200,series.list_series())
   if len(parts)==3 and parts[:2]==['api','series']:return self.reply(200,series.get_series(parts[2]))
   if len(parts)==4 and parts[:2]==['api','series'] and parts[3]=='dashboard':return self.reply(200,series.dashboard(parts[2]))
   if len(parts)==4 and parts[:2]==['api','series-media']:
    asset=series.series_asset(parts[2],parts[3])
    if not asset:return self.reply(404,{'error':'全剧素材未登记'})
    return self.file(Path(asset['path']),asset.get('name'))
   if len(parts)==3 and parts[:2]==['api','projects']:return self.reply(200,store.get_project(parts[2]))
   if len(parts)==4 and parts[:2]==['api','projects'] and parts[3]=='generation':return self.reply(200,generation.state(parts[2]))
   if len(parts) in (5,6) and parts[:2]==['api','projects'] and parts[3]=='mask-review':
    from . import mask_review
    project=store.get_project(parts[2]);segment=mask_review._segment(project,parts[4])
    if len(parts)==5:return self.reply(200,mask_review.status(project,segment))
    if parts[5]=='frame':return self.reply(200,mask_review.frame_data(project,segment,int(qs.get('frame',['0'])[0]),qs.get('fingerprint',[''])[0],qs.get('candidate_id',[None])[0],qs.get('candidate_fingerprint',[None])[0]))
    if parts[5] in ['support-status','support-frame']:
     from . import mask_support
     body={key:qs.get(key,[None])[0] for key in ['candidate_id','candidate_fingerprint','mask_fingerprint','version_id']}
     if parts[5]=='support-status':return self.reply(200,mask_support.status(project,segment,body['candidate_id'],body['candidate_fingerprint'],body['mask_fingerprint']))
     return self.reply(200,mask_support.frame_data(project,segment,int(qs.get('frame',['0'])[0]),body))
   if len(parts)==3 and parts[:2]==['api','jobs']:
    return self.reply(200,json.loads(store.job_path(parts[2]).read_text()))
   if len(parts)==4 and parts[:2]==['api','media']:
    a=store.asset_by_id(parts[2],parts[3])
    if not a:return self.reply(404,{'error':'素材未登记'})
    return self.file(Path(a['path']),a.get('name'))
   root=ROOT/'frontend/dist';path=(root/unquote(u.path).lstrip('/')).resolve()
   if root.resolve() not in path.parents and path!=root.resolve():return self.reply(403,{'error':'路径无效'})
   if not path.is_file():path=root/'index.html'
   if not path.exists():return self.reply(503,{'error':'页面尚未构建，请先运行启动工具'})
   return self.file(path)
  except FileNotFoundError:return self.reply(404,{'error':'记录不存在'})
  except Exception as e:return self.reply(400,{'error':str(e)[:500]})
 def system(self):
  from .media import separation_capability
  return {'ffmpeg':bool(shutil.which('ffmpeg')),'blender':Path('/Applications/Blender.app').exists(),'pose_model':(DATA/'models/pose_landmarker_full.task').exists(),'music_separation':separation_capability()['available'],'local_only':True,'generation_api':'Wan 视频','watermark':False,'cloud_upload_enabled':False,'cloud_generation_enabled':False,'replacement_generation_available':True,'generation_requires_explicit_confirmation':True}
 def file(self,p,display_name=None):
  size=p.stat().st_size;start=0;end=size-1;status=200
  rng=self.headers.get('Range','')
  if rng:
   m=re.fullmatch(r'bytes=(\d*)-(\d*)',rng)
   if not m:return self.reply(416,{'error':'无效分段请求'})
   if m[1]:start=int(m[1]);end=min(int(m[2]) if m[2] else end,end)
   elif m[2]:start=max(0,size-int(m[2]))
   if start>end or start>=size:return self.reply(416,{'error':'分段超出文件范围'})
   status=206
  self.send_response(status);self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('X-Content-Type-Options','nosniff')
  if display_name:self.send_header('Content-Disposition',"inline; filename*=UTF-8''"+quote(display_name,safe=''))
  if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
  self.end_headers()
  with p.open('rb') as f:
   f.seek(start);remaining=end-start+1
   while remaining:
    chunk=f.read(min(1024*1024,remaining))
    if not chunk:break
    self.wfile.write(chunk);remaining-=len(chunk)
 def do_PUT(self):return self.mutate('PUT')
 def do_POST(self):return self.mutate('POST')
 def mutate(self,method):
  try:
   if not self.allowed():return self.reply(403,{'error':'仅允许本机访问'})
   u=urlparse(self.path);parts=u.path.strip('/').split('/');qs=parse_qs(u.query)
   if u.path=='/api/upload':
    pid=qs.get('project_id',[''])[0];store.get_project(pid)
    name=Path(qs.get('filename',['upload'])[0]).name
    ext=Path(name).suffix.lower()
    if ext not in ['.mp4','.mov','.m4v','.mkv','.avi','.png','.jpg','.jpeg','.webp','.bmp','.wav','.mp3','.m4a','.flac','.json']:raise ValueError('暂不支持这种文件格式')
    size=int(self.headers.get('Content-Length','0'))
    if not 0<size<=2*1024**3:raise ValueError('文件须在2GB以内')
    dest=DATA/'projects'/pid/'uploads'/f'{store.uid()}{ext}';dest.parent.mkdir(exist_ok=True)
    with dest.open('xb') as f:
     remaining=size
     while remaining:
      chunk=self.rfile.read(min(1024*1024,remaining))
      if not chunk:raise ValueError('上传中断')
      f.write(chunk);remaining-=len(chunk)
    return self.reply(200,workflow.attach(pid,dest,qs.get('kind',['source'])[0],qs.get('role',[None])[0],qs.get('segment_id',[None])[0],original_name=name))
   b=self.body()
   if u.path=='/api/config':return self.reply(200,workflow.set_config(b))
   if u.path=='/api/projects' and method=='POST':return self.reply(201,store.create_project(b.get('name')))
   if u.path=='/api/series' and method=='POST':return self.reply(201,series.create_series(b.get('name')))
   if len(parts)==3 and parts[:2]==['api','series'] and method=='PUT':return self.reply(200,series.update_series(parts[2],b))
   if len(parts)==4 and parts[:2]==['api','series'] and parts[3]=='actions':return self.reply(200,series.action(parts[2],b))
   if len(parts)>=3 and parts[:2]==['api','projects']:
    pid=parts[2];current=store.get_project(pid)
    if len(parts)==4 and parts[3]=='generation' and method=='POST':return self.reply(202,generation.dispatch(pid,b))
    if 'expected_revision' in b and b['expected_revision']!=current.get('revision',0):raise store.RevisionConflict(current.get('revision',0))
    if len(parts)==3 and method=='PUT':return self.reply(200,store.update_project(pid,b))
    if len(parts)==4 and parts[3]=='import':return self.reply(200,workflow.attach(pid,Path(b['path']).expanduser(),b.get('kind','source'),b.get('role'),b.get('segment_id')))
    if len(parts)==4 and parts[3]=='actions':
     a=b.get('action')
     workflow.guard_generated_video_action(current,a)
     scope={}
     if current.get('workflow')=='replacement' and a in {'render_mask_preview','patch_masks','restore_mask_version','save_mask_support'}:
      scope={'segment_id':b.get('segment_id'),'input_material_fingerprint':b.get('mask_fingerprint')}
     if current.get('workflow')=='replacement' and a in {'validate_candidate','review_candidate'}:
      segment=next((item for item in current['segments'] if item['id']==b.get('segment_id')),{})
      candidate=segment.get('generated') or {}
      scope={'segment_id':b.get('segment_id'),'input_candidate_id':candidate.get('id')}
      try:
       from .replacement import fingerprint
       scope['input_material_fingerprint']=fingerprint(current)
      except (OSError,ValueError,KeyError,TypeError):pass
     return self.reply(202,store.start_job(pid,a,lambda jid:workflow.action(pid,b,jid),**scope))
   return self.reply(404,{'error':'操作不存在'})
  except store.RevisionConflict as e:return self.reply(409,{'error':str(e),'code':'REVISION_CONFLICT','current_revision':e.current_revision})
  except Exception as e:return self.reply(400,{'error':str(e)[:600]})

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=8765);a=ap.parse_args()
 store.recover_jobs();generation.recover();http=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
 print(f'帧工坊已启动：http://127.0.0.1:{a.port}',flush=True)
 try:http.serve_forever()
 except KeyboardInterrupt:pass
 finally:http.server_close()
if __name__=='__main__':main()
