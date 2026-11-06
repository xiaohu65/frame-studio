"""Free Blender real-mesh character pass + source-plate compositing.

Animation is calibrated to source screen coordinates. It is not a recovered
world-space camera or exact hidden anatomy. Rendering and quality approval stay
separate, and source props are composited once from the original source pixels.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time
from .store import digest,save_json

def capabilities():
 blender=Path(os.environ.get('FRAME_STUDIO_BLENDER','/Applications/Blender.app/Contents/MacOS/Blender'))
 return {'available':blender.is_file(),'path':str(blender),'geometry':'real_3D_meshes','motion':'source-calibrated screen-space joints with estimated depth','paid':False}

def _plan_dependencies(plan):
 paths=set()
 for row in plan['frames']:
  if row.get('prop_mask'):paths.add(row['prop_mask'])
  for actor in row['actors']:
   for key in ['removal_mask','remove_mask_path','visibility_mask']:
    if actor.get(key):paths.add(actor[key])
 return sorted(paths)

def plan_fingerprint(plan_path):
 plan=json.loads(Path(plan_path).read_text())
 assets=[(path,digest(path)) for path in _plan_dependencies(plan)]
 return hashlib.sha256((digest(plan_path)+json.dumps(assets,sort_keys=True)).encode()).hexdigest()

def freeze_plan(plan_path,out_dir):
 plan_path=Path(plan_path);out=Path(out_dir);out.mkdir(parents=True,exist_ok=True);frozen=out/'frozen_plan.json'
 fingerprint=plan_fingerprint(plan_path)
 if frozen.exists():
  old=json.loads(frozen.read_text())
  if old.get('frozen_dependencies',{}).get('input_fingerprint')!=fingerprint:raise ValueError('姿态或依赖蒙版已改变，请使用新渲染目录')
  manifest=json.loads((out/'dependencies.json').read_text())
  if any(digest(item['stored_path'])!=item['sha256'] for item in manifest):raise ValueError('已冻结的蒙版发生变化，不能复用此渲染')
  return frozen
 plan=json.loads(plan_path.read_text());assets=out/'assets';assets.mkdir(exist_ok=True);mapping={};manifest=[]
 for original in _plan_dependencies(plan):
  checksum=digest(original);dest=assets/(checksum[:24]+Path(original).suffix)
  if not dest.exists():shutil.copy2(original,dest)
  mapping[original]=str(dest);manifest.append({'original_path':original,'stored_path':str(dest),'sha256':checksum})
 for row in plan['frames']:
  if row.get('prop_mask'):row['prop_mask']=mapping[row['prop_mask']]
  for actor in row['actors']:
   for key in ['removal_mask','remove_mask_path','visibility_mask']:
    if actor.get(key):actor[key]=mapping[actor[key]]
 plan['frozen_dependencies']={'input_fingerprint':fingerprint,'original_plan_sha256':digest(plan_path),'mask_assets':len(manifest)}
 save_json(out/'dependencies.json',manifest);save_json(frozen,plan);return frozen

def composite_frames(source,plan_path,out_dir,frames=None,progress=None):
 import cv2,numpy as np
 from PIL import Image
 from .puppet import clean_plate,compose_with_props
 plan=json.loads(Path(plan_path).read_text());out=Path(out_dir);dest=out/'composited';dest.mkdir(exist_ok=True)
 rows=[r for r in plan['frames'] if frames is None or r['frame'] in frames]
 cap=cv2.VideoCapture(str(source));records=[];last_frame=0
 for ix,row in enumerate(rows):
  f=row['frame'];path=out/'transparent'/f'{f:05d}.png'
  if not path.exists():raise ValueError(f'缺少第{f}帧3D渲染')
  if f!=last_frame+1:cap.set(cv2.CAP_PROP_POS_FRAMES,f-1)
  ok,bgr=cap.read();last_frame=f
  if not ok:raise ValueError(f'源帧{f}无法读取')
  src=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);masks=[];valid=[];missing=[]
  for a in row['actors']:
   mp=a.get('removal_mask',a.get('remove_mask_path'))
   if a.get('joints') and mp and Path(mp).is_file():masks.append(np.asarray(Image.open(mp).convert('L')));valid.append(a)
   else:missing.append(a['id'])
  rgba=np.asarray(Image.open(path).convert('RGBA'))
  if masks:
   plate,support=clean_plate(src,masks,valid)
   pm=np.asarray(Image.open(row['prop_mask']).convert('L')) if row.get('prop_mask') else np.zeros(src.shape[:2],np.uint8)
   result,qa=compose_with_props(src,plate,rgba,pm,frame=f,effect_polygon=row.get('white_effect_polygon'),change_support=support)
  else:result=src;qa={'opaque_props_changed_pixels':0,'outside_registered_support_changed_pixels':0,'source_prop_pixels_translated':0}
  assert qa['opaque_props_changed_pixels']==0 and qa['outside_registered_support_changed_pixels']==0
  op=dest/f'{f:05d}.png';Image.fromarray(result).save(op,compress_level=2)
  records.append({'source_frame':f,'path':str(op),'sha256':digest(op),'missing_actor_ids':missing,**qa})
  if progress:progress((ix+1)/len(rows),f'合成原背景与道具 {ix+1}/{len(rows)}')
 from .pose_quality import check_pose_plan
 diagnostics=check_pose_plan(plan);save_json(out/'pose_diagnostics.json',diagnostics)
 cap.release();report={'status':'PIXEL_PROTECTION_PASS_VISUAL_REVIEW_REQUIRED','geometry':'REAL_BLENDER_3D_MESHES','motion_depth':'estimated screen-space reconstruction','frames':len(records),'frames_with_missing_actors':sum(bool(r['missing_actor_ids']) for r in records),'records':records,'source_sha256':digest(source),'plan_sha256':digest(plan_path),'pose_diagnostics':diagnostics,'generation_submitted':False};save_json(out/'composite_validation.json',report);return report

def render_puppet(source,plan_path,out_dir,progress=None,preview=True,frames=None):
 import fcntl
 out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
 with (out/'.render.lock').open('a') as handle:
  if progress:progress(0,'检查并等待同一姿态的渲染任务')
  fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
  try:return _render_puppet(source,plan_path,out,progress,preview,frames)
  finally:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def _render_puppet(source,plan_path,out_dir,progress=None,preview=True,frames=None):
 source=Path(source).resolve();plan_path=Path(plan_path).resolve();out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
 plan=json.loads(plan_path.read_text())
 if digest(source)!=plan['source_sha256']:raise ValueError('原片已改变，请重新分析动作')
 if not preview and not plan.get('reviewed'):raise ValueError('正式导出前请复核姿态')
 # Bind this output directory to a frozen plan; stale renders never silently mix.
 frozen=freeze_plan(plan_path,out)
 recipe={'source_sha256':digest(source),'plan_sha256':digest(frozen),'scene_script_sha256':digest(Path(__file__).with_name('blender_scene.py')),'compositor_sha256':digest(Path(__file__).with_name('puppet.py')),'engine':'BLENDER_EEVEE'}
 recipe_path=out/'render_recipe.json'
 if recipe_path.exists() and json.loads(recipe_path.read_text())!=recipe:raise ValueError('渲染代码或合成方法已更新，请用新目录保留旧版本')
 if not recipe_path.exists():save_json(recipe_path,recipe)
 if frames is None and (out/'精致3D关节人偶参考.mp4').exists() and (out/'composite_validation.json').exists():
  prior=json.loads((out/'composite_validation.json').read_text())
  if prior.get('frames')==len(plan['frames']):
    from .pose_quality import check_pose_plan
    diagnostics=check_pose_plan(plan);save_json(out/'pose_diagnostics.json',diagnostics)
    return {'pose_path':str(frozen),'video_path':str(out/'精致3D关节人偶参考.mp4'),'blend_path':str(out/'关节人偶_真实3D可编辑.blend'),'validation_path':str(out/'composite_validation.json'),'diagnostics_path':str(out/'pose_diagnostics.json'),'pose_warning_frames':diagnostics['frames'],'frames_with_missing_actors':prior['frames_with_missing_actors'],'status':'VISUAL_REVIEW_REQUIRED','message':'复用同一姿态已完成的3D渲染，请复核所选片段'}
 cap=capabilities()
 if not cap['available']:raise ValueError('未找到本机Blender，无法渲染真实3D人偶')
 selection='all' if frames is None else ','.join(str(f) for f in frames)
 cmd=[cap['path'],'--background','--factory-startup','--python',str(Path(__file__).with_name('blender_scene.py')),'--',str(frozen),str(out),selection,'BLENDER_EEVEE']
 log=out/'blender.log'
 with log.open('w') as fh:
  p=subprocess.Popen(cmd,stdout=fh,stderr=subprocess.STDOUT)
  while p.poll() is None:
   if progress:
    count=len(list((out/'transparent').glob('*.png')));total=len(plan['frames']) if frames is None else len(frames);progress(count/max(total,1),f'Blender渲染 {count}/{total}')
   time.sleep(.5)
  if p.returncode:raise ValueError('Blender渲染未完成，请查看本机渲染日志')
 report=composite_frames(source,frozen,out,frames=frames,progress=progress)
 if frames is None:
  rows=plan['frames'];video=out/'精致3D关节人偶参考.mp4'
  subprocess.run(['ffmpeg','-v','error','-n','-framerate',plan['fps'],'-start_number',str(rows[0]['frame']),'-i',str(out/'composited/%05d.png'),'-frames:v',str(len(rows)),'-an','-c:v','libx264','-crf','16','-preset','medium','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True)
 else:video=None
 return {'pose_path':str(frozen),'video_path':str(video) if video else None,'blend_path':str(out/'关节人偶_真实3D可编辑.blend'),'validation_path':str(out/'composite_validation.json'),'diagnostics_path':str(out/'pose_diagnostics.json'),'pose_warning_frames':report['pose_diagnostics']['frames'],'frames_with_missing_actors':report['frames_with_missing_actors'],'status':'VISUAL_REVIEW_REQUIRED','message':'真实3D模型已渲染；动作、握拍和遮挡仍须复核，未自动通过'}
