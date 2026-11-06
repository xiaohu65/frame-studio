from __future__ import annotations
from pathlib import Path
import hashlib,json,math,re,shutil,subprocess,sys,zipfile
from . import store
from .store import DATA,ROOT,get_project,put_project,add_asset,digest,uid

CONFIG=DATA/'private-config.json'
RUNTIME_KEY=None

def config(private=False):
 c=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
 key=RUNTIME_KEY or c.get('api_key')
 if not key:
  import os
  key=os.environ.get('DASHSCOPE_API_KEY') or os.environ.get('BAILIAN_API_KEY')
 if private:return {**c,'api_key':key}
 return {'configured':bool(key),'source':'本地私有配置' if c.get('api_key') else ('本次会话' if RUNTIME_KEY else ('环境变量' if key else '尚未配置')),'base_url':c.get('base_url','https://dashscope.aliyuncs.com/api/v1'),'workspace_id':c.get('workspace_id','')}
def set_config(body):
 global RUNTIME_KEY
 c=json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
 from .provider import _api_base
 c.update({k:str(body[k]).strip() for k in ['base_url','workspace_id'] if k in body})
 _api_base(c)
 if body.get('api_key'):
  RUNTIME_KEY=str(body['api_key']).strip()
  if not re.fullmatch(r'[A-Za-z0-9._-]{16,256}',RUNTIME_KEY):
   RUNTIME_KEY=None;raise ValueError('API Key格式不正确，请输入完整密钥且不要包含空格或换行')
  if body.get('persist_key') is True:c['api_key']=RUNTIME_KEY
 CONFIG.parent.mkdir(parents=True,exist_ok=True)
 import os
 temp=CONFIG.with_name(CONFIG.name+'.'+uid()+'.tmp')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as handle:json.dump(c,handle,ensure_ascii=False,indent=2)
 os.replace(temp,CONFIG);CONFIG.chmod(0o600)
 return config()
def meta(path):
 from .media import probe
 try:return probe(path)
 except Exception:
  from PIL import Image
  with Image.open(path) as im:return {'width':im.width,'height':im.height,'has_video':False,'has_audio':False}
def attach(pid,path,kind,role=None,segment_id=None,original_name=None):
 if kind not in ['source','reference','music','generated','raw_candidate','pose','puppet','edit_mask','protection_mask']:raise ValueError('未知素材用途')
 current=get_project(pid)
 if current.get('workflow')=='replacement' and kind in ['puppet','pose']:raise ValueError('通用替换直接使用原片参考；人偶与关节数据请在历史人偶项目中保留')
 if kind=='raw_candidate' and current.get('workflow')!='replacement':raise ValueError('本地时间轴适配仅用于原片替换项目')
 if kind in ['edit_mask','protection_mask','generated','raw_candidate','pose','puppet'] and not any(s['id']==segment_id for s in current['segments']):raise ValueError('请选择片段')
 if kind=='reference' and role!='scene' and not any(r['id']==role for r in current['roles']):raise ValueError('请选择人物角色')
 a=add_asset(pid,path,kind,name=original_name,meta=meta(path) if kind!='pose' else {})
 with store.LOCK:
  p=get_project(pid)
  if kind=='source':
   p['source']=a;p['source_meta']=a['meta'];dur=float(a['meta'].get('video_duration_seconds') or (a['meta']['duration_seconds']-a['meta'].get('video_start_time',0)));p['source_meta']['duration']=dur;p['timeline_duration']=dur
   p['segments']=[]
   # <=15s model input, keep split ranges explicit and editable.
   t=0;fps=float(a['meta'].get('fps') or 30);chunk=max(1,int(14.5*fps))/fps
   while t<dur-.02:
    end=min(t+chunk,dur);n=len(p['segments']);sid=chr(65+n) if n<26 else f'S{n+1}'
    p['segments'].append({'id':sid,'name':sid+'段','start':round(t,6),'end':round(end,6),'reference':None,'generated':None,'status':'NEEDS_REVIEW','reviewed':False,'review_notes':'请先检查人物关节、身份和遮挡'})
    t=end
   if p.get('workflow')=='replacement':
    p['source_actors']=[];p['scene']['regions']=[]
    p['preparation']=None;p['generic_review']=None;p['local_preview']=None;p['final']=None
    p['final_master']=None;p['assembly_report']=None
    p['prompts']={};p['music']={'mode':'original','asset':None,'assets':{}}
  elif kind=='reference':
   if role=='scene':p['scene']['images'].append(a)
   else:
    target=next((r for r in p['roles'] if r['id']==role),None)
    if not target:raise ValueError('请选择人物角色')
    target['images'].append(a)
  elif kind=='music':p['music']['asset']=a;p['music']['mode']='custom';p['music'].setdefault('assets',{})['custom']=a
  elif kind in ['generated','raw_candidate','pose','puppet','edit_mask','protection_mask']:
   s=next((s for s in p['segments'] if s['id']==segment_id),None)
   if not s:raise ValueError('请选择片段')
   if kind=='generated':
    had_adaptation=bool(s.get('candidate_adaptation'))
    s['generated']=a;s.pop('generation_provenance',None)
    s.pop('candidate_adaptation',None)
    if p.get('workflow')=='replacement':
     s.update(candidate_stale=False,composite=None,composited=None,composite_preview=None,composite_report=None)
     if had_adaptation:p.update(final=None,final_master=None,assembly_report=None)
   elif kind=='raw_candidate':s['raw_candidate']=a
   elif kind=='puppet':s['reference']=a;s['reviewed']=False;s['status']='NEEDS_REVIEW'
   elif kind=='pose':s['pose_asset']=a;s['reviewed']=False
   else:
    s[kind]=a
    if p.get('workflow')=='replacement':
     from .mask_review import invalidate
     invalidate(s)
   if p.get('workflow')=='replacement' and kind!='raw_candidate':
    s['candidate_review']=None;s['candidate_validation']=None;s['composited']=None
  if p.get('workflow')=='replacement' and kind in ['source','reference','edit_mask','protection_mask']:
   p['generic_review']=None;p['preparation']=None
  put_project(p)
 if current.get('workflow')=='replacement' and kind in ['edit_mask','protection_mask']:
  # Local-only preview jobs capture their own byte binding. A later import
  # invalidates the old preview immediately and prevents stale job commits.
  from . import mask_review
  latest=get_project(pid);segment=next(x for x in latest['segments'] if x['id']==segment_id)
  if segment.get('edit_mask'):
   try:binding=mask_review.context(latest,segment)['fingerprint']
   except (OSError,ValueError,KeyError,TypeError):binding=None
   store.start_job(pid,'render_mask_preview',lambda jid:mask_review.action(pid,{'action':'render_mask_preview','segment_id':segment_id,'mask_fingerprint':binding or mask_review.context(latest,segment)['fingerprint']},jid),segment_id=segment_id,input_material_fingerprint=binding)
 return get_project(pid)
def generate_prompt(p,s):
 if p.get('workflow')=='replacement':
  from .replacement import generate_prompt as generic_prompt
  return generic_prompt(p,s)
 mapping=[];idx=1
 for r in p['roles']:
  labels=[]
  for _ in r['images']:labels.append('图'+str(idx));idx+=1
  mapping.append(f"{r['color']}衣身人偶对应{r['name']}（{'、'.join(labels)}）。{r.get('description','')}")
 scene=p['scene']
 scene_text='保留视频1的背景、观众、球桌、球网、球拍、球与照明，不改变其他人物。' if scene['mode']=='preserve' else f"场景外观使用图{idx}起的场景参考。{scene.get('description','')}，保留原运动空间与机位，不让场景物件挡住原动作。"
 return f"编辑视频1，仅替换已标记的动作人偶。输出约{s['end']-s['start']:.3f}秒，与参考视频同长同速。\n\n"+'\n'.join(mapping)+f"\n\n人偶只是动作占位：保持关节轨迹、身体转向、持物手、接触时点、遮挡顺序和镜头运动。人偶的椭球头、胶囊四肢、哑光颜色不进入最终外观。面孔、肤色、发型、服装仅依对应参考图，并跨镜保持同一身份。原有出画与被遮挡部位仍按原镜头遮挡，不为展示全身改变构图。\n\n{scene_text}\n\n不增加肢体、人物、道具或新镜头，不改变节奏，不增加停顿、慢放或片尾亮相。无对白、旁白、音乐、字幕、水印；音乐后期按原片时间轴接入。"
def package(pid,jid):
 p=get_project(pid);out=DATA/'projects'/pid/'exports'/f'素材与提示词_{jid}.zip';out.parent.mkdir(parents=True,exist_ok=True)
 if p.get('workflow')=='replacement':
  from .replacement_workflow import export_package
  return export_package(pid,jid)
 refs=[]
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  i=1
  for r in p['roles']:
   for a in r['images']:
    name=f"人物/图{i}_{r['name']}_{a['name']}";z.write(a['path'],name);refs.append({'reference':'图'+str(i),'role':r['name'],'file':name,'sha256':digest(a['path'])});i+=1
  for a in p['scene']['images']:
   name=f"场景/图{i}_{a['name']}";z.write(a['path'],name);refs.append({'reference':'图'+str(i),'role':'场景','file':name,'sha256':digest(a['path'])});i+=1
  for s in p['segments']:
   z.writestr('提示词/'+s['id']+'.txt',p['prompts'].get(s['id']) or generate_prompt(p,s))
   if s.get('reference'):z.write(s['reference']['path'],'动作参考/'+s['id']+'.mp4')
  z.writestr('素材引用与时间轴.json',json.dumps({'references':refs,'segments':p['segments'],'settings':p['settings'],'music':p['music'],'quality_note':'关节预览与生成结果须分别复核'},ensure_ascii=False,indent=2))
 a=add_asset(pid,out,'package');return {'asset':a,'message':'素材包已导出，包含实际图片、动作参考、提示词及引用清单'}
def provider_media(p,s):
 if not s.get('reference'):raise ValueError('片段还没有关节人偶参考视频')
 media=[{'type':'reference_video','path':s['reference']['path']}]
 for r in p['roles']:
  for a in r['images']:media.append({'type':'reference_image','path':a['path']})
 for a in p['scene']['images']:media.append({'type':'reference_image','path':a['path']})
 return media

RETIRED_GENERATED_VIDEO_ACTIONS = frozenset({'adapt_candidate', 'composite_replacement', 'render_mask_support'})

def guard_generated_video_action(project, action_name):
 if action_name in RETIRED_GENERATED_VIDEO_ACTIONS:
  raise ValueError('已停用生成画面的二次处理。请核对原始回片，通过后直接拼接并接入音频。')
 if action_name=='assemble' and project.get('workflow')!='replacement':
  raise ValueError('历史人偶流程的旧成片导出已停用。正式交付请使用当前帧工坊的原始回片拼接流程。')

def action(pid,b,jid):
 from . import media
 a=b['action'];p=get_project(pid);s=next((x for x in p['segments'] if x['id']==b.get('segment_id')),None)
 if 'expected_revision' in b and b['expected_revision']!=p.get('revision',0):raise store.RevisionConflict(p.get('revision',0))
 guard_generated_video_action(p,a)
 if a in ['submit','poll','download']:
  gate=p.get('cloud_gate',{})
  if not gate.get('upload_allowed') or not gate.get('generation_allowed'):
   raise ValueError('当前仅开放本地准备与验证；上传和云端任务等待用户另行确认')
  if p.get('workflow')=='replacement':
   from . import generation_compatibility
   compatibility=generation_compatibility.inspect(p)
   if not compatibility['ready']:raise ValueError(compatibility['issues'][0]['message'])
 if p.get('workflow')=='replacement':
  from . import mask_review
  if a in mask_review.ACTIONS:return mask_review.action(pid,b,jid)
  from . import replacement_workflow
  if a in replacement_workflow.ACTIONS:return replacement_workflow.action(pid,b,jid)
 outdir=DATA/'projects'/pid/'exports';outdir.mkdir(exist_ok=True)
 if a=='package':return package(pid,jid)
 if a=='save_prompt':
  if not s:raise ValueError('请选择片段')
  p['prompts'][s['id']]=str(b.get('prompt') or generate_prompt(p,s));put_project(p);return {'message':'提示词已保存','prompt':p['prompts'][s['id']]}
 if a=='save_pose':
  if not s:raise ValueError('请选择片段')
  pose=b.get('pose_json');pose=json.loads(pose) if isinstance(pose,str) else pose
  if not isinstance(pose,dict):raise ValueError('关节数据须为JSON对象')
  dest=outdir/f'{s["id"]}_pose_{jid}.json';store.save_json(dest,pose)
  asset=add_asset(pid,dest,'pose');p=get_project(pid);s=next(x for x in p['segments'] if x['id']==s['id']);s['pose_asset']=asset;s['reviewed']=False;put_project(p)
  return {'message':'关节数据已保存，需重新渲染复核','asset':asset}
 if a in ['extract_audio','separate_music']:
  if not p.get('source'):raise ValueError('请先上传原片')
  video_offset=float(p['source_meta'].get('video_start_time',0));duration=p.get('timeline_duration') or p['source_meta'].get('duration_seconds')
  raw=outdir/f'原片连续原声_{jid}.wav'
  result=media.extract_audio(p['source']['path'],raw,start=video_offset,duration=duration)
  if a=='separate_music':
   result=media.separate_music(raw,outdir/f'分离音乐_{jid}')
   musicpath=result.get('music_path') or result.get('path') or result.get('instrumental_path')
   if not musicpath:raise ValueError('音乐分离未返回伴奏文件')
  else:musicpath=raw
  asset=add_asset(pid,musicpath,'music',meta=media.probe(musicpath));p=get_project(pid);mode='separated' if a=='separate_music' else 'original';variants=p.get('music',{}).get('assets',{});variants[mode]=asset;p['music']={'asset':asset,'mode':mode,'assets':variants,'source_audio_offset':video_offset};put_project(p)
  return {'asset':asset,'message':'已按首帧时间提取连续音轨' if a=='extract_audio' else '已分离伴奏，需试听人声/音效残留','report':result}
 if a in ['preflight','submit','poll','download']:
  from .provider import WanClient
  client=WanClient(config(private=True),DATA/'projects'/pid/'wan-tasks')
  if a=='poll':
   result=client.poll(str(b['task_id']))
   p=get_project(pid)
   for seg in p['segments']:
    if seg.get('task',{}).get('task_id')==str(b['task_id']):seg['task']=result
   put_project(p);return result
  if a=='download':
   if not s:raise ValueError('请选择片段')
   result=client.download(str(b['task_id']),outdir/f'{s["id"]}_Wan1080P_{jid}.mp4')
   path=result.get('path') or result.get('output_path')
   if not path:raise ValueError('未找到任务结果文件')
   asset=add_asset(pid,path,'generated',meta=media.probe(path));p=get_project(pid);s=next(x for x in p['segments'] if x['id']==s['id']);s['generated']=asset;s['status']='GENERATED_NEEDS_REVIEW';put_project(p)
   return {**result,'asset':asset,'message':'结果已下载，仍须检查人物与动作'}
  targets=[s] if s else p['segments'];reports=[]
  for seg in targets:
   report=client.preflight(p['prompts'].get(seg['id']) or generate_prompt(p,seg),provider_media(p,seg),p['settings'],float(b.get('budget_yuan',p['authorization'].get('budget_yuan',0))))
   report['segment_id']=seg['id'];report['reviewed']=seg.get('reviewed',False);reports.append(report)
  if a=='preflight':
   total=sum(x['estimate']['reserved_yuan'] for x in reports)
   return {'reports':reports,'total_reserved_yuan':round(total,4),'message':f'预检完成；保守预留合计 ¥{total:.2f}，未提交生成','blockers':(['未配置API Key'] if not client.configured else [])+(['关节人偶尚待复核'] if any(not x['reviewed'] for x in reports) else [])}
  if not s:raise ValueError('生成时须明确选中一个片段')
  if s['reference'].get('kind')=='legacy_reference':raise ValueError('旧剪影不能作为新流程正式生成参考')
  if not s.get('reviewed'):raise ValueError('请先完成关节人偶的动作与遮挡复核')
  if s.get('reviewed_sha256')!=digest(s['reference']['path']):raise ValueError('参考视频已变化，请重新复核')
  if b.get('operator')=='agent' and p['authorization'].get('mode')!='agent':raise ValueError('本项目尚未开启代理授权')
  budget=float(b.get('budget_yuan',p['authorization'].get('budget_yuan',0)))
  prompt=p['prompts'].get(s['id']) or generate_prompt(p,s)
  fingerprint=hashlib.sha256(json.dumps({'prompt':prompt,'reference':digest(s['reference']['path']),'images':[digest(m['path']) for m in provider_media(p,s)[1:]],'settings':p['settings']},ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:32]
  prefix=f'{pid}:{s["id"]}:';stable_key=prefix+fingerprint
  active=next((t for t in client.list_tasks() if t.get('request_key','').startswith(prefix) and t.get('status') in ['UPLOADING','SUBMITTING','UNKNOWN','PENDING','RUNNING']),None)
  if active:return {**active,'message':'该片段已有处理中或状态未知的任务，请查询原任务，不会重复提交','_job_status':'UNKNOWN' if active['status'] in ['UNKNOWN','SUBMITTING'] else 'SUCCEEDED'}
  result=client.submit(prompt,provider_media(p,s),p['settings'],approved_budget_yuan=budget,request_key=stable_key)
  p=get_project(pid);s=next(x for x in p['segments'] if x['id']==s['id']);s['task']=result;put_project(p)
  if result.get('status')=='UNKNOWN':return {**result,'_job_status':'UNKNOWN','message':'提交状态未知，请核对原任务；不会自动重提'}
  if result.get('status') in ['UPLOAD_FAILED','REJECTED','FAILED']:return {**result,'_job_status':'FAILED','message':result.get('error','生成请求未完成')}
  return result
 if a=='review':
  if not s or not s.get('reference'):raise ValueError('没有可复核的参考视频')
  if s['reference'].get('kind')=='legacy_reference':raise ValueError('旧剪影仅作对照，请先制作或导入新关节人偶')
  if s.get('frames_with_missing_actors',0)>0 and b.get('approved') is True:raise ValueError('仍有未处理的人物帧，请校正关节并重新制作预览')
  s['reviewed']=b.get('approved') is True;s['reviewed_sha256']=digest(s['reference']['path']) if s['reviewed'] else None;s['status']='READY' if s['reviewed'] else 'NEEDS_REVIEW';put_project(p);return {'message':'复核已记录；生成结果仍需另行检查'}
 if a in ['analyze_pose','render_puppet']:
  from . import puppet
  if not p.get('source'):raise ValueError('请先上传原片')
  # Whole source timeline is analyzed once so independent segment edges stay consistent.
  if a=='analyze_pose':
   result=puppet.analyze_pose(p['source']['path'],outdir/f'pose_{jid}',role_regions=b.get('role_regions'),progress=lambda _,msg:store.update_job(jid,message=msg))
   if result.get('pose_path'):
    from .pose_quality import check_pose_plan
    diagnostics=check_pose_plan(json.loads(Path(result['pose_path']).read_text()));diag_path=outdir/f'pose_diagnostics_{jid}.json';store.save_json(diag_path,diagnostics)
    diag_asset=add_asset(pid,diag_path,'pose_diagnostics');result['pose_diagnostics']=diagnostics
    asset=add_asset(pid,result['pose_path'],'pose');p=get_project(pid)
    for seg in p['segments']:seg['pose_asset']=asset;seg['pose_diagnostics_asset']=diag_asset;seg['pose_warning_frames']=diagnostics['frames'];seg['reviewed']=False
    put_project(p);result['asset']=asset
  else:
   if not s or not s.get('pose_asset'):raise ValueError('请先分析并保存该片段的关节数据')
   from . import blender_puppet
   cache_key=hashlib.sha256((blender_puppet.plan_fingerprint(s['pose_asset']['path'])+digest(Path(blender_puppet.__file__).with_name('blender_scene.py'))+digest(Path(puppet.__file__))).encode()).hexdigest()[:20]
   result=blender_puppet.render_puppet(p['source']['path'],s['pose_asset']['path'],outdir/f'puppet3d_{cache_key}',progress=lambda _,msg:store.update_job(jid,message=msg))
   if result.get('video_path'):
    # Render in the source time domain, then trim the selected model reference.
    video=Path(result['video_path']);vmeta=media.probe(video)
    plan=json.loads(Path(s['pose_asset']['path']).read_text())
    first=int(plan.get('frames',[{'frame':1}])[0]['frame']);fps=vmeta['fps']
    plan_start=(first-1)/fps;start=s['start']-plan_start;duration=s['end']-s['start']
    dest=outdir/f'{s["id"]}_关节人偶_{jid}.mp4'
    media.trim_video(video,dest,start,duration,width=vmeta['width'],height=vmeta['height'],fps=vmeta['fps_rational'])
    asset=add_asset(pid,dest,'puppet',meta=media.probe(dest));blend=add_asset(pid,result['blend_path'],'editable_blend') if result.get('blend_path') else None
    diag_asset=add_asset(pid,result['diagnostics_path'],'pose_diagnostics') if result.get('diagnostics_path') else None
    p=get_project(pid);seg=next(x for x in p['segments'] if x['id']==s['id']);seg['reference']=asset;seg['blend_asset']=blend;seg['pose_diagnostics_asset']=diag_asset;seg['pose_warning_frames']=result.get('pose_warning_frames',[]);seg['reviewed']=False;seg['status']='NEEDS_REVIEW';seg['frames_with_missing_actors']=int(result.get('frames_with_missing_actors',0))
    seg['review_notes']=f"仍有{seg['frames_with_missing_actors']}帧未处理人体，须校正关节" if seg['frames_with_missing_actors'] else '真实3D模型：请连续检查关节、手掌与道具遮挡；深度与隐藏关节是估计'
    if seg['pose_warning_frames']:seg['review_notes']+=f"。另有{len(seg['pose_warning_frames'])}帧体型/腿部异常候选，须对照源片；未检出候选也不代表动作通过"
    put_project(p);result['asset']=asset
  return {**result,'_job_status':'NEEDS_REVIEW','message':'关节处理完成，请检查低置信部位与道具遮挡后复核'}
 if a=='assemble_preview':
  if not p['segments'] or any(not x.get('reference') for x in p['segments']):raise ValueError('请先准备全部片段的人偶参考')
  if any(x['reference'].get('kind')=='legacy_reference' for x in p['segments']):raise ValueError('请先换成新的关节人偶，旧剪影仅作对照')
  segments=[{'path':x['reference']['path'],'source_start':x['start'],'source_end':x['end'],'anchors':b.get('anchors',{}).get(x['id'],x.get('anchors'))} for x in p['segments']]
  music=p['music'].get('asset')
  dest=outdir/f'本地人偶拼接预演_1080P_原片节奏_{jid}.mp4'
  result=media.assemble(segments,dest,music_path=music['path'] if music else None,audio_source_start=0,target_duration=p.get('timeline_duration'))
  asset=add_asset(pid,dest,'local_preview',meta=media.probe(dest));p=get_project(pid);p['local_preview']=asset;put_project(p)
  return {'asset':asset,'report':result,'generation_submitted':False,'message':'本地人偶预演已导出，未调用生成API；请检查动作与音乐'}
 raise ValueError('未知操作')
