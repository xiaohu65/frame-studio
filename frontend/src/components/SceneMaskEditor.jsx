import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import {cn} from "@/lib/utils";
import {Slider} from "./ui/slider";
import React,{useEffect,useId,useRef,useState} from 'react';
import { Button as UiButton } from './ui/button';

import { Input } from './ui/input';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from './ui/select';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Checkbox } from './ui/checkbox';
import { Textarea } from './ui/textarea';
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from './ui/table';


import {AlertCircle,ChevronDown,Download,Plus,Save,Trash2} from 'lucide-react';
import {assetUrl,time,request} from '../api';
import {DRAFTS,draftChanged} from '../draftState';

const clone=value=>JSON.parse(JSON.stringify(value));
const makeId=prefix=>`${prefix}_${crypto.randomUUID().replaceAll('-','').slice(0,12)}`;

function MaskButton({variant='outline',...props}){
 return <UiButton type="button" {...props} variant={variant} size="default" />;
}

function MaskInput({label,...props}){
 const id=useId();
 return <div className="mask-editor-field">
  <label htmlFor={id} className="text-sm font-medium">{label}</label>
  <Input id={id} {...props} />
 </div>;
}

function MaskSelect({label,ariaLabel,value,onValueChange,disabled,options}){
 const id=useId();
 return <div className="mask-editor-field">
  <label htmlFor={id} className="text-sm font-medium">{label}</label>
  <Select value={value} onValueChange={onValueChange} disabled={disabled}>
   <SelectTrigger id={id} aria-label={ariaLabel}><SelectValue>{options.find(([key]) => key === value)?.[1]}</SelectValue></SelectTrigger>
   <SelectContent position="popper"><SelectGroup>
    {options.map(([key,text])=><SelectItem key={key} value={key}>{text}</SelectItem>)}
   </SelectGroup></SelectContent>
  </Select>
 </div>;
}

function MaskDisclosure({label,children,disabled}){
 return <CollapsibleRoot className="mask-editor-disclosure" disabled={disabled}>
  <CollapsibleTrigger asChild>
   <MaskButton variant="ghost" className="mask-editor-disclosure-trigger h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3">{label}<ChevronDown aria-hidden="true" data-icon="inline-start" /></MaskButton>
  </CollapsibleTrigger>
  <CollapsibleContent forceMount className="mask-editor-disclosure-content">{children}</CollapsibleContent>
 </CollapsibleRoot>;
}

function MaskNotice({children,tone='destructive',role='alert'}){
 return <Alert role={role} className="mask-editor-notice" data-tone={tone} variant={tone === 'destructive' ? 'destructive' : 'default'}>
  <AlertCircle size={16} aria-hidden="true"/>
  <AlertDescription>{children}</AlertDescription>
 </Alert>;
}

const imageFrom=src=>new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('本帧图像无法显示，请重新载入'));image.src=src;});
const layerCanvas=(image,width,height)=>{const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;canvas.getContext('2d').drawImage(image,0,0);return canvas;};
const PATCH_JOBS=new Map();
export function importedMaskDraftTargets(project){
 return (project?.segments||[]).flatMap(segment=>[['mask-patch','局部遮罩修补'],['mask-support','旧支持域草稿']].filter(([key])=>DRAFTS.has(`${project.id}${key}${segment.id}`)).map(([,label])=>({label:`${segment.name||segment.id}的${label}`,step:2,targetId:'mask-panel',segmentId:segment.id,maskKind:'edit_mask'})));
}
function watchMaskJob(identity,id){
 if(PATCH_JOBS.get(identity)?.id===id)return PATCH_JOBS.get(identity).promise;
 const promise=new Promise((resolve,reject)=>{
  const poll=()=>request(`/api/jobs/${id}`).then(job=>{
   if(['QUEUED','RUNNING'].includes(job.status)){setTimeout(poll,800);return;}
   if(job.status==='SUCCEEDED'&&DRAFTS.get(identity)?.working){DRAFTS.delete(identity);draftChanged();}
   resolve(job);
  }).catch(reject);
  poll();
 }).finally(()=>{if(PATCH_JOBS.get(identity)?.id===id)PATCH_JOBS.delete(identity);});
 PATCH_JOBS.set(identity,{id,promise});return promise;
}

function maskInspectionPalette(element){
 const style=getComputedStyle(element),sample=document.createElement('canvas');
 sample.width=sample.height=1;
 const context=sample.getContext('2d',{willReadFrequently:true});
 const readColor=property=>{
  context.clearRect(0,0,1,1);
  context.fillStyle=style.getPropertyValue(property).trim();
  context.fillRect(0,0,1,1);
  return Array.from(context.getImageData(0,0,1,1).data).slice(0,3);
 };
 return {editColor:readColor('--mask-edit'),protectColor:readColor('--mask-protect')};
}

export function maskInspectionPixels(source,edit,protect,{editColor,protectColor}){
 const pixels=new Uint8ClampedArray(source);
 for(let index=0;index<pixels.length;index+=4){
  const protectedPixel=protect[index]>=128;
  if(!protectedPixel&&edit[index]<128)continue;
  const color=protectedPixel?protectColor:editColor,alpha=protectedPixel ? .5 : .42;
  for(let channel=0;channel<3;channel++)pixels[index+channel]=Math.round(pixels[index+channel]*(1-alpha)+color[channel]*alpha);
 }
 return pixels;
}

export function ImportedMaskEditor({project,segment,onAction,busy=false}){
 const canvas=useRef(null),stroke=useRef(null),loaded=useRef(null);
 const [review,setReview]=useState(null),[frame,setFrame]=useState(null),[frameData,setFrameData]=useState(null);
 const [loading,setLoading]=useState(true),[error,setError]=useState(''),[reload,setReload]=useState(0);
 const [repair,setRepair]=useState(false),[layer,setLayer]=useState('edit'),[tool,setTool]=useState('brush'),[radius,setRadius]=useState(12);
 const [range,setRange]=useState(false),[lastFrame,setLastFrame]=useState(null),[original,setOriginal]=useState(false);
 const draftIdentity=JSON.stringify([project.id,segment.id,segment.start,segment.end,project.source?.id,project.source?.sha256,segment.edit_mask?.id,segment.edit_mask?.sha256,segment.protection_mask?.id,segment.protection_mask?.sha256]);
 const draftKey=`${project.id}mask-patch${segment.id}`;
 const [operations,setOperations]=useState(()=>DRAFTS.get(draftKey)?.value?.operations||[]),[patchFingerprint,setPatchFingerprint]=useState(()=>DRAFTS.get(draftKey)?.value?.fingerprint||null);
 const [patchCandidate,setPatchCandidate]=useState(()=>DRAFTS.get(draftKey)?.value?.candidateReference||null);
 const [activeStroke,setActiveStroke]=useState(null),[pendingJob,setPendingJob]=useState(()=>PATCH_JOBS.get(draftKey)?.id||null);
 const endpoint=`/api/projects/${project.id}/mask-review/${segment.id}`;
 const locked=busy||!!pendingJob;
 const staleCandidate=!!patchCandidate&&!!review&&!review.diagnostic_candidates?.some(item=>item.id===patchCandidate.id&&item.fingerprint===patchCandidate.fingerprint);
 const staleDraft=operations.length>0&&review&&(patchFingerprint&&patchFingerprint!==review.fingerprint||staleCandidate);
 const fps=review?Number(review.fps_rational.split('/')[0])/Number(review.fps_rational.split('/')[1]||1):30;
 const frameReady=!!review&&!!frameData&&frameData.fingerprint===review.fingerprint&&frameData.source_frame===frame;
 useEffect(()=>{
  const prior=DRAFTS.get(draftKey),value={operations,fingerprint:patchFingerprint,materialIdentity:draftIdentity,candidateReference:patchCandidate};
  if(operations.length&&JSON.stringify(prior?.value)!==JSON.stringify(value)){DRAFTS.set(draftKey,{value,working:true});draftChanged();}
  else if(!operations.length&&prior?.working){DRAFTS.delete(draftKey);draftChanged();}
 },[draftKey,draftIdentity,operations,patchFingerprint,patchCandidate]);
 useEffect(()=>{
  let live=true,timer;const abort=new AbortController();
  const refresh=()=>request(endpoint,{signal:abort.signal}).then(result=>{if(live){setReview(result);setFrame(value=>Math.max(result.first_frame,Math.min(result.last_frame,value??result.first_frame)));timer=setTimeout(refresh,['QUEUED','RUNNING'].includes(result.preview_job?.status)?1500:5000);}}).catch(err=>{if(live){setError(err.message);setReview(null);setFrameData(null);setLoading(false);loaded.current=null;}});
  refresh();return()=>{live=false;clearTimeout(timer);abort.abort();};
 },[endpoint,project.revision,reload]);
 useEffect(()=>{
  if(!review||frame===null)return;
  let live=true;const abort=new AbortController();setLoading(true);setFrameData(null);setError('');loaded.current=null;
  const timer=setTimeout(()=>request(`${endpoint}/frame?${new URLSearchParams({frame,fingerprint:review.fingerprint})}`,{signal:abort.signal}).then(async result=>{
   const images=await Promise.all(['source','edit_mask','protection_mask'].map(key=>imageFrom(result.images[key])));
   if(live&&result.fingerprint===review.fingerprint&&result.source_frame===frame){loaded.current=images;setFrameData(result);setLoading(false);}
  }).catch(err=>{if(live){setError(err.message);setLoading(false);}}),100);
  return()=>{live=false;clearTimeout(timer);abort.abort();};
 },[endpoint,review?.fingerprint,frame,reload]);
 useEffect(()=>{
  if(!frameReady||!canvas.current||!loaded.current)return;
  const [source,editImage,protectImage]=loaded.current,{width,height}=review;
  const target=canvas.current;target.width=width;target.height=height;
  const ctx=target.getContext('2d');ctx.drawImage(source,0,0);
  if(original)return;
  const layers={edit:layerCanvas(editImage,width,height),protect:layerCanvas(protectImage,width,height)};
  for(const operation of [...(staleDraft?[]:operations),...(activeStroke?[activeStroke]:[])]){
   if(frame<operation.first_frame||frame>operation.last_frame)continue;
   const draw=layers[operation.layer].getContext('2d'),points=operation.points.map(([x,y])=>[x*(width-1),y*(height-1)]);
   draw.fillStyle=operation.tool==='brush'?'#fff':'#000';draw.strokeStyle=draw.fillStyle;draw.lineWidth=operation.radius*2;draw.lineCap='round';draw.lineJoin='round';
   draw.beginPath();points.forEach(([x,y],index)=>index?draw.lineTo(x,y):draw.moveTo(x,y));draw.stroke();
   points.forEach(([x,y])=>{draw.beginPath();draw.arc(x,y,operation.radius,0,Math.PI*2);draw.fill();});
  }
  const pixels=ctx.getImageData(0,0,width,height),edit=layers.edit.getContext('2d').getImageData(0,0,width,height).data,protect=layers.protect.getContext('2d').getImageData(0,0,width,height).data;
  pixels.data.set(maskInspectionPixels(pixels.data,edit,protect,maskInspectionPalette(canvas.current)));
  ctx.putImageData(pixels,0,0);
 },[frameReady,frameData,review?.width,review?.height,frame,original,operations,activeStroke,staleDraft]);
 useEffect(()=>{
  if(!pendingJob)return;
  let live=true;
  watchMaskJob(draftKey,pendingJob).then(job=>{
   if(!live)return;
   setPendingJob(null);
   if(job.status==='SUCCEEDED'){setOperations([]);setReload(value=>value+1);setRepair(false);}
   else setError(job.message||'修补未保存，原文件已保留');
  }).catch(err=>{if(live){setPendingJob(null);setError(err.message);}});
  return()=>{live=false;};
 },[pendingJob]);
 function seek(value){if(!locked){setActiveStroke(null);stroke.current=null;setFrame(Math.max(review.first_frame,Math.min(review.last_frame,Math.round(Number(value)||review.first_frame))));}}
 function point(event){const box=canvas.current.getBoundingClientRect();return [(event.clientX-box.left)/box.width,(event.clientY-box.top)/box.height].map(value=>Math.max(0,Math.min(1,value)));}
 function start(event){
  if(event.button!==0||locked||loading||!frameReady||!repair||original||staleDraft)return;
  const stop=range?Number(lastFrame??frame):frame;
  if(!Number.isInteger(stop)||stop<frame||stop>review.last_frame||stop-frame+1>Math.ceil(fps*2)){setError('请明确选择当前帧，或从当前帧开始、不超过2秒的短区间');return;}
  if(operations.length>=200){setError('每次最多200笔，请先保存');return;}
  event.preventDefault();canvas.current.setPointerCapture(event.pointerId);
  if(!operations.length){setPatchFingerprint(review.fingerprint);setPatchCandidate(null);}
  stroke.current={layer,tool,radius:Number(radius),first_frame:frame,last_frame:stop,points:[point(event)]};setActiveStroke({...stroke.current});
 }
 function move(event){if(!stroke.current)return;const next=point(event),previous=stroke.current.points.at(-1);if(Math.hypot((next[0]-previous[0])*review.width,(next[1]-previous[1])*review.height)<Math.max(1,radius/5))return;stroke.current.points.push(next);setActiveStroke({...stroke.current,points:[...stroke.current.points]});}
 function finish(event){if(!stroke.current)return;const finished=stroke.current;setOperations(list=>[...list,finished]);stroke.current=null;setActiveStroke(null);if(canvas.current.hasPointerCapture(event.pointerId))canvas.current.releasePointerCapture(event.pointerId);}
 async function run(action,extra={}){
  setError('');
  try{const response=await onAction(action,{segment_id:segment.id,mask_fingerprint:action==='patch_masks'?patchFingerprint:review.fingerprint,...(action==='patch_masks'&&patchCandidate?{diagnostic_candidate_id:patchCandidate.id,diagnostic_candidate_fingerprint:patchCandidate.fingerprint}:{}),...extra});if(response?.job_id){watchMaskJob(draftKey,response.job_id).catch(()=>{});setPendingJob(response.job_id);}}
  catch(err){setError(err.message);}
 }
 const currentVersion=review?.versions.find(version=>version.is_current);
 const retainedSupportDraft=DRAFTS.get(`${project.id}mask-support${segment.id}`)?.value;
 const selectedFrame=frame??review?.first_frame;
 const repairControlsLocked=locked||loading||!frameReady;
 return <section className="mask-editor-ui" aria-label="已有遮罩检查与局部修补" data-testid="imported-mask-review">
  <p className="text-sm text-muted-foreground">橙色：替换区域 · 蓝色：前景保护；重叠处保留。</p>
  {review&&<>
   <div className="mask-editor-workspace">
   <div className="mask-editor-navigation">
    <MaskInput label="原片帧号" type="number" min={review.first_frame} max={review.last_frame} value={selectedFrame}
     disabled={locked} onChange={event=>seek(event.target.value)}/>
    <span className="text-sm text-muted-foreground mask-editor-frame-facts">
     第 {selectedFrame-review.first_frame+1} / {review.frame_count} 帧
     {frameReady?` · 原片 ${time(frameData.source_time)}`:''}
    </span>
    <div className="mask-editor-actions">
     <MaskButton disabled={locked||frame<=review.first_frame} onClick={()=>seek(frame-1)}>上一帧</MaskButton>
     <MaskButton disabled={locked||frame>=review.last_frame} onClick={()=>seek(frame+1)}>下一帧</MaskButton>
     <MaskButton disabled={!frameReady} aria-pressed={original} variant={original?'default':'outline'}
      onClick={()=>setOriginal(value=>!value)}>{original?'显示遮罩叠加':'原图对照'}</MaskButton>
    </div>
   </div>
   {review.first_frame<review.last_frame&&<Slider className="mask-editor-slider" min={review.first_frame} max={review.last_frame} step={1} value={[selectedFrame]} disabled={locked} onValueChange={([value])=>seek(value)} aria-label="定位遮罩原片帧" />}
   <div className="mask-editor-canvas" data-repairing={repair&&!original} style={{aspectRatio:`${review.width}/${review.height}`}}>
    <canvas ref={canvas} hidden={!frameReady||loading} aria-label={original?'当前原片精确帧':'当前原片遮罩叠加帧，可用画笔局部修补'}
     onPointerDown={start} onPointerMove={move} onPointerUp={finish}
     onPointerCancel={event=>{stroke.current=null;setActiveStroke(null);if(canvas.current.hasPointerCapture(event.pointerId))canvas.current.releasePointerCapture(event.pointerId);}}/>
    {loading&&<span role="status" className="text-sm">正在读取当前原片帧与遮罩…</span>}
   </div>
   <div className="mask-editor-actions">
    <MaskButton disabled={locked||!frameReady} aria-pressed={repair} variant={repair?'default':'outline'}
     onClick={()=>{setRepair(value=>!value);setOriginal(false);}}>{repair?'收起画笔':'修补坏帧'}</MaskButton>
    {currentVersion&&<MaskButton disabled={locked||operations.length>0}
     onClick={()=>run('restore_mask_version',{version_id:currentVersion.id,target:'base'})}>撤回上次修补</MaskButton>}
   </div>
   </div>
   {repair&&<fieldset className="mask-editor-group mask-editor-repair" disabled={repairControlsLocked} aria-label="遮罩修补选项">
    <div className="mask-editor-grid" data-columns="3">
     <MaskSelect label="修补层" value={layer} onValueChange={setLayer} disabled={repairControlsLocked}
      options={ [['edit','替换层 · 橙色'],['protect','保护层 · 蓝色']] }/>
     <MaskSelect label="工具" value={tool} onValueChange={setTool} disabled={repairControlsLocked}
      options={ [['brush','画笔 · 补入区域'],['erase','橡皮 · 擦除区域']] }/>
     <MaskInput label="半径 / 原片像素" type="number" min="1" max={Math.floor(Math.min(review.width,review.height)/2)}
      value={radius} onChange={event=>setRadius(Math.max(1,Math.min(Math.min(review.width,review.height)/2,Number(event.target.value)||1)))}/>
    </div>
    <div className="mask-editor-grid">
     <MaskSelect label="应用范围" value={range?'range':'frame'} disabled={repairControlsLocked}
      onValueChange={value=>{setRange(value==='range');setLastFrame(frame);}}
      options={ [['frame',`仅当前原片第 ${frame} 帧`],['range','从当前帧开始的短区间（最多2秒）']] }/>
     {range&&<MaskInput label={`原片结束帧（包含本帧，起点 ${frame}）`} type="number" value={lastFrame??frame}
      min={frame} max={Math.min(review.last_frame,frame+Math.ceil(fps*2)-1)} onChange={event=>setLastFrame(Number(event.target.value))}/>}
    </div>
    <p className="text-sm text-muted-foreground">短区间重复同一笔迹，移动边缘请逐帧核对。</p>
   </fieldset>}
   {operations.length>0&&<div className="mask-editor-footer">
    <span role="status" className="text-sm text-warning">
     {operations.length} 笔待保存{patchCandidate?` · 参照候选 ${patchCandidate.name}`:''} · 涉及原片第 {Math.min(...operations.map(item=>item.first_frame))} — {Math.max(...operations.map(item=>item.last_frame))} 帧
    </span>
    <div className="mask-editor-actions">
     <MaskButton disabled={locked} onClick={()=>setOperations(list=>list.slice(0,-1))}>撤销一笔</MaskButton>
     <MaskButton disabled={locked} onClick={()=>setOperations([])}>放弃本次修补</MaskButton>
     <MaskButton disabled={locked||!!activeStroke||staleDraft} variant="default" onClick={()=>run('patch_masks',{operations})}>保存独立修补版本</MaskButton>
    </div>
   </div>}
   {review.preview_job?.status==='FAILED'&&<MaskNotice>连续预览未生成：{review.preview_job.message}。当前有效文件已保留。</MaskNotice>}
   {!review.preview&&['QUEUED','RUNNING'].includes(review.preview_job?.status)&&
    <span role="status" className="text-sm text-muted-foreground">正在本机生成连续预览，可先检查当前帧。</span>}
   {pendingJob&&<span role="status" className="text-sm text-muted-foreground">正在本机处理与校验，当前有效文件保留。</span>}
   <MaskDisclosure label="连续预览、文件与版本">
    <p className="text-sm text-muted-foreground">{review.technical_ready
     ?'技术检查通过，仍需核对轮廓与遮挡。'
     :'完整技术检查将在本机连续预览时进行。'}</p>
    {review.preview?<video className="mask-editor-preview" src={assetUrl(review.preview)} controls playsInline preload="metadata"
     aria-label="当前遮罩连续叠加预览"/>:<p className="text-sm text-muted-foreground">尚无当前素材的有效连续预览。</p>}
    <div className="mask-editor-actions">
     <MaskButton disabled={locked||operations.length>0} onClick={()=>run('render_mask_preview')}>生成连续预览</MaskButton>
     <UiButton asChild variant="outline"><a href={assetUrl(segment.edit_mask)} download>
      <Download aria-hidden="true" data-icon="inline-start" />下载替换遮罩
     </a></UiButton>
     {segment.protection_mask&&<UiButton asChild variant="outline"><a href={assetUrl(segment.protection_mask)} download>
      <Download aria-hidden="true" data-icon="inline-start" />下载保护遮罩
     </a></UiButton>}
    </div>
    {review.versions.map(version=><div className="mask-editor-footer" key={version.id}>
     <span className="text-sm text-muted-foreground">{version.created_at} · {version.changed_source_frames.length} 帧{version.is_current?' · 当前版本':''}</span>
     <div className="mask-editor-actions">
      <MaskButton disabled={locked||operations.length>0||version.is_current}
       onClick={()=>run('restore_mask_version',{version_id:version.id,target:'derived'})}>使用此修补版本</MaskButton>
      <MaskButton disabled={locked||operations.length>0}
       onClick={()=>run('restore_mask_version',{version_id:version.id,target:'base'})}>使用修补前版本</MaskButton>
     </div>
    </div>)}
   </MaskDisclosure>
  </>}
  {retainedSupportDraft&&!loading&&<div className="mask-editor-retained">
   <MaskNotice tone="warning" role="status">本会话有旧支持域草稿，入口已停用。可明确放弃这份草稿。</MaskNotice>
   <MaskButton disabled={locked} onClick={()=>{DRAFTS.delete(`${project.id}mask-support${segment.id}`);draftChanged();setReload(value=>value+1);}}>
    放弃这份旧支持域草稿
   </MaskButton>
  </div>}
  {staleDraft&&<MaskNotice>原片、遮罩或参照候选已改变，当前笔迹不能保存。请放弃本次修补后重新编辑。</MaskNotice>}
  {error&&<><MaskNotice>{error}</MaskNotice>
   <div className="mask-editor-actions"><MaskButton disabled={locked} onClick={()=>setReload(value=>value+1)}>重新载入当前素材</MaskButton></div>
  </>}
 </section>;
}

export default function SceneMaskEditor({project,segment,onPatch,onAction,busy=false,error,useDraft,onWorkingDraft,workingDraft}){
 const [keyframes,setKeyframes,dirty,save,reset]=useDraft(segment.mask_keyframes||[],`${project.id}mask${segment.id}`,project.revision);
 const video=useRef(null),surface=useRef(null),reportWorking=useRef(onWorkingDraft);
 reportWorking.current=onWorkingDraft;
 const identity=`${project.id}mask-work${segment.id}`;
 const fpsValue=project.source_meta.fps_rational||project.source_meta.fps||30;
 const fps=typeof fpsValue==='string'&&fpsValue.includes('/')?Number(fpsValue.split('/')[0])/Number(fpsValue.split('/')[1]):Number(fpsValue);
 const report=segment.mask_render_report||{};
 const minFrame=Number.isInteger(report.first_frame)?report.first_frame-1:Math.ceil(segment.start*fps-.000002*fps);
 const maxFrame=Number.isInteger(report.last_frame)?report.last_frame-1:Math.max(minFrame,Math.ceil(segment.end*fps-.000002*fps)-1);
 const offset=Number(project.source_meta.video_start_time||0);
 const [frame,setFrame]=useState(()=>Number.isFinite(workingDraft?.frame)?Math.max(minFrame,Math.min(maxFrame,Math.round(workingDraft.frame))):minFrame);
 const [drawing,setDrawing]=useState(false);
 const [regionId,setRegionId]=useState(()=>workingDraft?.regionId??makeId('region'));
 const [label,setLabel]=useState(()=>workingDraft?.label??'人物轮廓');
 const [actorId,setActorId]=useState(()=>workingDraft?.actorId??project.source_actors?.[0]?.id??'');
 const [mode,setMode]=useState(()=>workingDraft?.mode??'edit');
 const [pointsText,setPointsText]=useState(()=>workingDraft?.pointsText??'[]');
 const currentTime=Number((frame/fps).toFixed(8));
 const current=keyframes.find(key=>Math.round(key.time*fps)===frame);
 let points=[],pointError='';
 try{
  const parsed=JSON.parse(pointsText);
  if(!Array.isArray(parsed)||parsed.some(point=>!Array.isArray(point)||point.length!==2||point.some(value=>!Number.isFinite(value)||value<0||value>1)))throw new Error();
  points=parsed;
 }catch{pointError='坐标须为有效数组，例如 [[0.2,0.2],[0.5,0.2],[0.5,0.7]]，每个数值在 0 到 1 之间。';}
 const activeRegion=current?.regions.find(region=>region.id===regionId);
 const polygonDirty=points.length>0&&(!activeRegion||JSON.stringify(activeRegion.points)!==JSON.stringify(points)||activeRegion.label!==label||activeRegion.mode!==mode||(activeRegion.actor_id||'')!==actorId);
 useEffect(()=>{
  reportWorking.current?.(identity,{frame,regionId,label,actorId,mode,pointsText},polygonDirty);
 },[identity,frame,regionId,label,actorId,mode,pointsText,polygonDirty]);
 useEffect(()=>{setFrame(value=>Math.max(minFrame,Math.min(maxFrame,value)));setDrawing(false);},[minFrame,maxFrame]);
 useEffect(()=>{if(video.current){video.current.pause();video.current.currentTime=currentTime+offset;}},[currentTime,project.source?.id,offset]);
 useEffect(()=>{if(busy){setDrawing(false);video.current?.pause();}},[busy]);
 const seekFrame=value=>{if(!busy)setFrame(Math.max(minFrame,Math.min(maxFrame,Math.round(Number(value)||0))));};
 function loadRegion(region){
  if(busy)return;
  setRegionId(region.id);setLabel(region.label||'轮廓');setActorId(region.actor_id||'');setMode(region.mode);setPointsText(JSON.stringify(region.points));
 }
 function loadFrame(key){
  if(busy)return;
  seekFrame(key.time*fps);
  if(key.regions[0])loadRegion(key.regions[0]);else{setRegionId(makeId('region'));setPointsText('[]');}
 }
 function writeFrame(next){
  if(!busy)setKeyframes(list=>[...list.filter(key=>Math.round(key.time*fps)!==frame),next].sort((a,b)=>a.time-b.time));
 }
 function recordRegion(){
  if(busy||pointError||points.length<3||!label.trim())return;
  const region={id:regionId,label:label.trim(),actor_id:actorId||null,mode,points};
  writeFrame({...current,time:currentTime,confirmed:false,regions:[...(current?.regions||[]).filter(item=>item.id!==regionId),region]});
 }
 function addPoint(event){
  if(busy||!drawing)return;
  const bounds=surface.current.getBoundingClientRect();
  const point=[(event.clientX-bounds.left)/bounds.width,(event.clientY-bounds.top)/bounds.height].map(value=>Number(Math.max(0,Math.min(1,value)).toFixed(5)));
  setPointsText(JSON.stringify([...points,point]));
 }
 function copyPrevious(){
  if(busy)return;
  const previous=keyframes.filter(key=>key.time<currentTime-.25/fps).at(-1);
  if(!previous)return;
  writeFrame({time:currentTime,confirmed:false,regions:clone(previous.regions)});
  if(previous.regions[0])loadRegion(previous.regions[0]);
 }
 function copyBoundary(toFrame){
  if(busy||!current)return;
  const stamp=Number((toFrame/fps).toFixed(8));
  setKeyframes(list=>[...list.filter(key=>Math.round(key.time*fps)!==toFrame),{time:stamp,confirmed:false,regions:clone(current.regions)}].sort((a,b)=>a.time-b.time));
 }
 function newRegion(){if(!busy){setRegionId(makeId('region'));setPointsText('[]');setLabel('新轮廓');}}
 const actors=project.source_actors||[];
 const selectedActor=actors.some(actor=>actor.id===actorId)?`actor:${actorId}`:'scene';
 const pendingFrames=keyframes.filter(key=>!key.confirmed).length;
 const canRender=!dirty&&!polygonDirty&&keyframes.length>0&&pendingFrames===0;
 const detailsId=useId();
 return <section className="mask-editor-ui" data-testid="polygon-mask-editor" aria-label="当前片段的手工轮廓编辑">
  <fieldset className="mask-editor-group" disabled={busy} aria-label="手工轮廓设置">
   <div className="mask-editor-grid">
    <MaskSelect label="对应人物或场景" ariaLabel="当前轮廓对应人物或场景" value={selectedActor} disabled={busy}
     onValueChange={value=>setActorId(value==='scene'?'':value.slice('actor:'.length))}
     options={ [['scene','场景或其它保护区域'],...actors.map(actor=>[`actor:${actor.id}`,actor.label])] }/>
    <MaskSelect label="轮廓用途" ariaLabel="当前轮廓用途" value={mode} onValueChange={setMode} disabled={busy}
     options={ [['edit','替换区域（允许改变外观）'],['protect','遮挡保护（保留原片前景）']] }/>
   </div>
   <div className="mask-editor-workspace">
   <div className="mask-editor-navigation">
    <MaskInput label="原片帧号" type="number" min={minFrame+1} max={maxFrame+1} value={frame+1} aria-label="遮罩原片帧号"
     onChange={event=>seekFrame(Number(event.target.value)-1)}/>
    <div className="mask-editor-field mask-editor-frame-facts">
     <span className="text-sm text-muted-foreground">原片时间</span>
     <span className="text-base font-medium">{time(currentTime)}</span>
    </div>
    <div className="mask-editor-actions">
     <MaskButton onClick={()=>seekFrame(minFrame)}>首帧</MaskButton>
     <MaskButton disabled={frame<=minFrame} onClick={()=>seekFrame(frame-1)}>上一帧</MaskButton>
     <MaskButton disabled={frame>=maxFrame} onClick={()=>seekFrame(frame+1)}>下一帧</MaskButton>
     <MaskButton onClick={()=>seekFrame(maxFrame)}>末帧</MaskButton>
    </div>
   </div>
   <div className="mask-editor-video" style={{aspectRatio:`${project.source_meta.width||16}/${project.source_meta.height||9}`}}>
    <video ref={video} src={assetUrl(project.source)} controls playsInline preload="metadata" aria-label="遮罩制作原片预览"
     onLoadedMetadata={()=>{if(video.current)video.current.currentTime=currentTime+offset;}}/>
    <div ref={surface} className="mask-editor-polygon-surface" data-drawing={drawing&&!busy} onPointerDown={addPoint} aria-hidden="true">
     <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" className="mask-editor-polygon-svg">
      {current?.regions.filter(region=>region.id!==regionId).map(region=><polygon key={region.id}
       points={region.points.map(([x,y])=>`${x*1000},${y*1000}`).join(' ')}
       fill={region.mode==='protect'?'var(--mask-protect-fill)':'var(--mask-edit-fill)'} stroke={region.mode==='protect'?'var(--mask-protect)':'var(--mask-edit)'} strokeWidth="2"/>)}
      {points.length>0&&<>
       <polygon points={points.map(([x,y])=>`${x*1000},${y*1000}`).join(' ')}
        fill={mode==='protect'?'var(--mask-protect-fill)':'var(--mask-edit-fill)'} stroke={mode==='protect'?'var(--mask-protect)':'var(--mask-edit)'} strokeWidth="3"/>
       {points.map(([x,y],index)=><g key={index}>
        <circle cx={x*1000} cy={y*1000} r="5" fill="var(--mask-handle)"/>
        <text x={x*1000+8} y={y*1000-7} fill="var(--mask-handle)" fontSize="18">{index+1}</text>
       </g>)}
      </>}
     </svg>
    </div>
   </div>
   <div className="mask-editor-actions">
    <MaskButton aria-pressed={drawing} variant={drawing?'default':'outline'}
     onClick={()=>{video.current?.pause();setDrawing(value=>!value);}}>{drawing?'结束点选':'点选轮廓'}</MaskButton>
    <MaskButton variant="default" disabled={!!pointError||points.length<3||!label.trim()} onClick={recordRegion}>记录本帧轮廓</MaskButton>
    <MaskButton onClick={newRegion}><Plus aria-hidden="true" data-icon="inline-start" />新建轮廓</MaskButton>
    <MaskButton onClick={()=>{setDrawing(false);seekFrame(((video.current?.currentTime??currentTime+offset)-offset)*fps);}}>使用当前播放帧</MaskButton>
   </div>
   {(points.length>0||drawing)&&<div className="mask-editor-actions">
    <MaskButton disabled={!points.length} onClick={()=>setPointsText(JSON.stringify(points.slice(0,-1)))}>撤销顶点</MaskButton>
    <MaskButton onClick={()=>setPointsText('[]')}>清空顶点</MaskButton>
    <span className="text-sm text-muted-foreground">{drawing?'沿画面边缘依次点选，至少 3 个顶点。':`当前轮廓 ${points.length} 个顶点。`}</span>
   </div>}
   </div>
   {polygonDirty&&<MaskNotice tone="warning" role="status">当前轮廓未记录。切换关键帧或新建前请先记录，再保存本段关键帧。</MaskNotice>}
   {pointError&&<MaskNotice>{pointError}</MaskNotice>}
   {current&&<div className="mask-editor-records">
    <div className="mask-editor-record-heading">
     <h4 className="font-semibold leading-snug tracking-tight text-base">本帧轮廓 · {current.regions.length} 条</h4>
     <Badge variant={current.confirmed?'secondary':'warning'}>{current.confirmed?'已确认':'待确认'}</Badge>
    </div>
    {current.regions.map(region=><div className="mask-editor-region" key={region.id}>
     <MaskButton variant={regionId===region.id?'default':'outline'} aria-pressed={regionId===region.id} onClick={()=>loadRegion(region)}>
      {region.label} · {region.mode==='edit'?'替换':'保护'}
     </MaskButton>
     <UiButton type="button" aria-label={`删除轮廓${region.label}`} onClick={()=>writeFrame({...current,confirmed:false,regions:current.regions.filter(item=>item.id!==region.id)})} variant={"outline"} size={"icon"}>
      <Trash2 aria-hidden="true" data-icon="inline-start" />
     </UiButton>
    </div>)}
    <div className="mask-editor-confirmations">
     <label className="text-sm">
      <Checkbox checked={!!current.confirmed} disabled={busy||polygonDirty} onCheckedChange={checked=>writeFrame({...current,confirmed:checked===true})} aria-label="确认当前遮罩关键帧轮廓" />
      我已确认此帧的替换轮廓与前景遮挡
     </label>
     {[['break_before','此帧之前存在遮挡或镜头断点'],['break_after','此帧之后存在遮挡或镜头断点']].map(([key,label])=>
      <label key={key} className="text-sm">
       <Checkbox checked={!!current[key]} disabled={busy} onCheckedChange={checked=>writeFrame({...current,[key]:checked===true,confirmed:false})} aria-label={label} />
       {label}
      </label>)}
    </div>
   </div>}
   <MaskDisclosure label="轮廓详情与复制" disabled={busy}>
    <div className="mask-editor-grid">
     <MaskInput label="当前轮廓名称" value={label} onChange={event=>setLabel(event.target.value)}/>
     <MaskInput label="原片时间 / 秒" type="number" step={1/fps} min={segment.start} max={maxFrame/fps} value={currentTime}
      aria-label="遮罩原片时间秒" onChange={event=>seekFrame(Number(event.target.value)*fps)}/>
    </div>
    <MaskInput label="轮廓固定编号" value={regionId} aria-label="当前轮廓固定编号" readOnly/>
    <div className="mask-editor-field">
     <label htmlFor={detailsId} className="text-sm font-medium">轮廓顶点坐标</label>
     <Textarea id={detailsId} className="mask-editor-code" rows={4} value={pointsText} onChange={event=>setPointsText(event.target.value)} aria-label="轮廓顶点坐标 JSON" aria-describedby={`${detailsId}-hint`} />
     <span id={`${detailsId}-hint`} className="text-sm text-muted-foreground">相邻关键帧须保持同一编号、顶点数和点序；不跨遮挡或镜头断点插值。</span>
    </div>
    <div className="mask-editor-actions">
     <MaskButton disabled={!keyframes.some(key=>key.time<currentTime-.25/fps)} onClick={copyPrevious}>复制上一关键帧</MaskButton>
     <MaskButton disabled={!current||frame===minFrame} onClick={()=>copyBoundary(minFrame)}>复制到首帧（待校正）</MaskButton>
     <MaskButton disabled={!current||frame===maxFrame} onClick={()=>copyBoundary(maxFrame)}>复制到末帧（待校正）</MaskButton>
     <MaskButton onClick={()=>writeFrame({time:currentTime,confirmed:false,regions:[]})}>本帧全部保留（空轮廓）</MaskButton>
    </div>
    <p className="text-sm text-muted-foreground">复制会覆盖目标帧记录；校正、确认后再保存。</p>
   </MaskDisclosure>
   <div className="mask-editor-keyframes">
    <div className="mask-editor-record-heading">
     <h4 className="font-semibold leading-snug tracking-tight text-base">本段关键帧 · {keyframes.length} 帧</h4>
     {pendingFrames>0&&<Badge variant={"warning"}>{pendingFrames} 帧待确认</Badge>}
    </div>
    {keyframes.length?<Table className="mask-editor-table">
     <caption className="visually-hidden">此片段的遮罩关键帧</caption>
     <TableHeader><TableRow>
      <TableHead>原片帧号</TableHead>
      <TableHead>原片时间</TableHead>
      <TableHead>轮廓</TableHead>
      <TableHead>确认状态</TableHead>
      <TableHead>操作</TableHead>
     </TableRow></TableHeader>
     <TableBody>{keyframes.map(key=>{
      const sourceFrame=Math.round(key.time*fps)+1;
      return <TableRow key={key.time} aria-current={sourceFrame===frame+1?'true':undefined}>
       <TableHead scope="row">{sourceFrame}</TableHead>
       <TableCell>{time(key.time)}</TableCell>
       <TableCell>{key.regions.length} 条</TableCell>
       <TableCell><Badge variant={key.confirmed?'secondary':'warning'}>{key.confirmed?'已人工确认':'待确认'}</Badge></TableCell>
       <TableCell><div className="mask-editor-actions">
        <MaskButton onClick={()=>loadFrame(key)}>查看第{sourceFrame}帧</MaskButton>
        <UiButton type="button" aria-label={`删除第${sourceFrame}帧遮罩关键帧`} onClick={()=>setKeyframes(list=>list.filter(item=>item.time!==key.time))} variant={"outline"} size={"icon"}><Trash2 aria-hidden="true" data-icon="inline-start" /></UiButton>
       </div></TableCell>
      </TableRow>;
     })}</TableBody>
    </Table>:<p className="text-sm text-muted-foreground">从首帧记录轮廓，或导入已有遮罩。</p>}
   </div>
   {error&&<MaskNotice>{error}</MaskNotice>}
   {keyframes.length>0&&<p className="text-sm text-muted-foreground">制作将按已保存关键帧更新本段替换、保护遮罩与预览。</p>}
   <footer className="mask-editor-footer">
    <div className="mask-editor-field" role="status">
     <span className={cn("text-sm font-medium", polygonDirty||dirty?'text-warning':'text-muted-foreground')}>
      {polygonDirty?'当前轮廓未记录':dirty?'关键帧有未保存修改':keyframes.length?'关键帧已保存':'尚无手工关键帧'}
     </span>
    </div>
    <div className="mask-editor-actions">
     {dirty&&<MaskButton onClick={reset}>放弃本段关键帧修改</MaskButton>}
     <MaskButton variant={dirty&&!polygonDirty?'default':'outline'} disabled={!dirty||polygonDirty}
      onClick={()=>save(onPatch,{segments:project.segments.map(item=>item.id===segment.id?{...item,mask_keyframes:keyframes}:item)})}
      data-testid="save-mask-keyframes"><Save aria-hidden="true" data-icon="inline-start" />保存本段关键帧</MaskButton>
     <MaskButton variant={canRender?'default':'outline'} disabled={!canRender} onClick={()=>onAction('render_masks')}
      data-testid="render-masks">制作遮罩与预览</MaskButton>
    </div>
   </footer>
  </fieldset>
 </section>;
}
