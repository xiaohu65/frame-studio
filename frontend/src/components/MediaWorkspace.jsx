import React,{useId,useRef,useEffect,useState} from 'react';
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {Film,Music2,Maximize2,Minimize2} from 'lucide-react';
import {assetUrl,time} from '../api';
import {Empty,Status} from './Common';
import {previewModel,clampToWindow,comparisonTimes,durationCheck,selectionPlayback} from '../previewModel';
import './MediaWorkspace.css';

export function MediaPreview({project,segment,mode,setMode,onTime,compact=false,savedVersion=false,sourceUi=false}){
 const main=useRef(),other=useRef(),ticker=useRef(null),activeKey=useRef(''),selection=useRef({key:'',controller:null});
 const sourcePanelId=useId();
 const [errorState,setErrorState]=useState({key:'',message:''}),[metadata,setMetadata]=useState({key:'',duration:null}),[expanded,setExpanded]=useState(false);
 const model=previewModel(project,segment,mode);
 const identity=`${project.id}:${segment?.id||''}:${mode}:${model.currentUrl}:${model.reference}:${segment?.start}:${segment?.end}`;
 activeKey.current=identity;
 if(selection.current.key!==identity)selection.current={key:identity,controller:selectionPlayback(model.window)};
 const control=selection.current.controller;
 const error=errorState.key===identity?errorState.message:'';
 const setError=message=>setErrorState({key:identity,message});
 const referenceStale=model.stale&&!model.full&&!model.candidate;
 const referenceName=model.replacement?(model.stale?'旧分段参考 · 需要更新':'当前分段参考'):'人偶运动参考';
 const actual=metadata.key===identity?metadata.duration:null;
 const check=durationCheck(model.expectedDuration,actual,model.fps);
 const validateDuration=!model.full&&!model.regionOnly;
 const mismatch=validateDuration&&check.mismatch;
 const hasFile=!!model.currentUrl;
 const stopTicker=()=>{if(ticker.current!==null){cancelAnimationFrame(ticker.current);ticker.current=null;}};
 useEffect(()=>stopTicker,[identity]);
 function validEvent(event,side='source'){
  return activeKey.current===identity && event.currentTarget===(side==='clip'?other.current:main.current);
 }
 function seek(video,stamp){
  control.seek(video,stamp);
 }
 function stopAtSelection(completed=true){
  stopTicker();control.finish(main.current,model.compare?other.current:null,completed);
  onTime?.(Math.max(0,model.window.last-model.offset));
 }
 function startTicker(){
  stopTicker();
  if(!model.bounded)return;
  const tick=()=>{
   if(activeKey.current!==identity||!main.current||main.current.paused)return;
   if(main.current.currentTime>=model.window.end-.001){stopAtSelection();return;}
   ticker.current=requestAnimationFrame(tick);
  };
  ticker.current=requestAnimationFrame(tick);
 }
 function syncSeek(event,side='source'){
  if(!validEvent(event,side))return;
  if(event.type==='seeking')control.observeSeek(event.currentTarget);
  if(model.compare){
   const stamp=comparisonTimes(side,event.currentTarget.currentTime,model.window);
   seek(main.current,stamp.source);seek(other.current,stamp.clip);
   onTime?.(Math.max(0,stamp.source-model.offset));
  }else if(model.regionOnly){
   const current=event.currentTarget.currentTime;
   if(current>=model.window.end){stopAtSelection(false);return;}
   seek(main.current,clampToWindow(current,model.window));
  }
 }
 function playing(event,side='source'){
  if(!validEvent(event,side))return;
  if(model.bounded&&control.restartOnPlay(main.current,model.compare?other.current:null))onTime?.(Math.max(0,model.window.start-model.offset));
  if(model.compare){
   syncSeek(event,side);
   const paired=side==='source'?other.current:main.current;
   if(paired?.paused)paired.play().catch(()=>{if(activeKey.current===identity)setError('对照视频未开始播放，请点击对应播放器的播放按钮。');});
  }
  startTicker();
 }
 function paused(event,side='source'){
  if(!validEvent(event,side))return;
  stopTicker();
  if(model.compare)(side==='source'?other.current:main.current)?.pause();
 }
 function rateChanged(event,side='source'){
  if(!validEvent(event,side)||!model.compare)return;
  const paired=side==='source'?other.current:main.current;
  if(paired&&paired.playbackRate!==event.currentTarget.playbackRate)paired.playbackRate=event.currentTarget.playbackRate;
 }
 function updated(event){
  if(!validEvent(event))return;
  const video=event.currentTarget;
  if(model.bounded){
   if(video.currentTime>=model.window.end-.001){stopAtSelection();return;}
   if(video.currentTime<model.window.start-.015)seek(video,model.window.start);
  }
  const sourceTime=model.full||model.bounded?video.currentTime-model.offset:video.currentTime+(segment?.start||0);
  onTime?.(Math.max(0,sourceTime));
  if(model.compare&&other.current&&!video.seeking){
   const stamp=comparisonTimes('source',video.currentTime,model.window);
   if(Math.abs(other.current.currentTime-stamp.clip)>.14)seek(other.current,stamp.clip);
  }
 }
 function loaded(event,side='source'){
  if(!validEvent(event,side))return;
  const video=event.currentTarget;
  if(side==='clip'){
   setMetadata({key:identity,duration:video.duration});
   seek(video,Math.max(0,(main.current?.currentTime||model.window.start)-model.window.start));
   if(main.current&&!main.current.paused)video.play().catch(()=>{});
  }else{
   seek(video,model.initial);
   if(!model.compare)setMetadata({key:identity,duration:video.duration});
  }
 }
 const tabs=[['puppet',model.replacement?(savedVersion?(model.stale?'旧参考文件':'已保存参考文件'):(model.stale?'分段参考（需更新）':'当前分段参考')):'人偶参考'],['original','原片全片（不提交）'],['compare','原片与分段对照']];
 if(model.generated)tabs.push(['generated','生成结果（待复核）']);
 const selectedMode=mode==='segment-source'?'puppet':mode;
 const savedHeading=model.full?'完整原片':model.compare?'已保存范围与参考文件对照':model.regionOnly?'按已保存范围播放原片':model.candidate?'已保存生成视频':referenceStale?'旧参考文件 · 需要重新准备':'已保存分段参考文件';
 const savedHint=referenceStale?'文件来自上次准备，已过期，仅供查看。它不会随当前未保存的起止时间变化。':model.full?'此处播放完整原片。':'此处使用已保存版本，不使用当前未保存的起止时间。';
 const videoProps={controls:true,playsInline:true,preload:'metadata',onTimeUpdate:updated,onSeeking:syncSeek,onPlay:playing,onPause:paused,onRateChange:rateChanged,onLoadedMetadata:loaded,onEnded:event=>{if(validEvent(event)&&model.bounded)stopAtSelection();},onError:event=>{if(validEvent(event))setError('视频暂时无法播放，请检查本机文件。');}};
 const expandLabel=expanded?'收起放大预览':'放大预览';
 const expandIcon=expanded?<Minimize2 size={15} />:<Maximize2 size={15} />;
 const toggleExpanded=()=>setExpanded(value=>!value);
 const previewDetails=<>
  <div className={`playback-purpose ${model.full?'full-source':model.regionOnly||referenceStale?'source-selection':'submission-file'}`} role="status" data-testid="playback-purpose">
   <div><strong>{savedVersion?savedHeading:referenceStale?'旧分段文件 · 需要重新准备':model.candidate?'生成结果 · 待复核':model.heading}{!model.full&&segment?` · ${segment.name||segment.id}`:''}</strong><span>{savedVersion?savedHint:model.full?'此处播放完整原片；实际交给 Wan 的文件按分段单独准备。':model.regionOnly?'尚无独立分段文件，当前仅播放原片中的这一段。请在材料检查步骤制作分段参考。':model.candidate?'生成结果仅用于本地对照，仍需核对人物、动作和遮挡。':referenceStale?'仅供查看：这些文件来自上次准备。请按当前材料重新制作分段参考，再进行检查与审核。':model.compare?'两侧同步播放、定位和暂停；左侧到选区末尾即停止。':'这里播放已准备的独立短片。是否可提交仍以当前材料检查与授权状态为准。'}</span></div>
   <span className="purpose-duration">{model.full?`全片 ${model.sourceDuration.toFixed(2)} 秒`:`${savedVersion?'已保存片段':'当前片段'} ${model.expectedDuration.toFixed(2)} 秒`}</span>
  </div>
  {segment&&<dl className="preview-file-facts" data-testid="preview-file-facts"><div><dt>原片区间</dt><dd>{time(segment.start)} — {time(segment.end)}</dd></div><div><dt>{model.full||model.regionOnly?'正在查看':model.candidate?'生成结果文件':referenceStale?'旧参考文件':'分段参考文件'}</dt><dd title={model.filename}>{model.filename||'尚未准备'}</dd></div><div><dt>{validateDuration?'文件时长':'播放范围'}</dt><dd>{validateDuration?(actual!==null?(check.readable?`${actual.toFixed(3)} 秒（播放器读取）`:'无法读取有效时长'):model.recordedDuration?`${model.recordedDuration.toFixed(3)} 秒（本地记录，等待播放器读取）`:'等待播放器读取'):(model.full?'完整原片':'仅当前片段选区')}</dd></div></dl>}
  <div id={sourceUi?undefined:'video-preview-panel'} className={`preview ${model.compare?'compare':''}`}>
   {model.compare?(model.source&&model.reference?<><div><span className="preview-label">原片选区 · {segment?.id}</span><video key={identity+'-left'} ref={main} src={model.source} aria-label="原片当前选区对照播放器" {...videoProps}/></div><div><span className="preview-label">{referenceName} · {segment?.id}</span><video key={identity+'-right'} ref={other} src={model.reference} controls muted playsInline preload="metadata" aria-label="实际参考片段对照播放器" onLoadedMetadata={e=>loaded(e,'clip')} onSeeking={e=>syncSeek(e,'clip')} onPlay={e=>playing(e,'clip')} onPause={e=>paused(e,'clip')} onRateChange={e=>rateChanged(e,'clip')} onEnded={e=>{if(validEvent(e,'clip'))stopAtSelection();}} onError={e=>{if(validEvent(e,'clip'))setError('参考片段无法播放，请检查实际文件。');}}/></div></>:<Empty variant="media">先准备当前片段的实际参考文件，再查看同区间对照。</Empty>):hasFile?<video key={identity} ref={main} src={model.currentUrl} aria-label={model.full?'原片全片播放器，不提交':model.regionOnly?'原片选区预览，非提交文件':model.candidate?'当前段生成结果播放器':`${segment?.id||''}段${referenceStale?'旧分段参考，仅供查看':'实际参考文件'}播放器`} {...videoProps}/>:<Empty variant="media">{model.full?'导入原片后可查看完整视频。':model.candidate?'导入或保存当前片段的生成结果后可查看。':'先导入原片并保存分段，再准备实际参考文件。'}</Empty>}
  </div>
  {mismatch&&<Alert variant="destructive" data-tone="destructive" data-testid="preview-duration-mismatch"><AlertDescription>{model.candidate?'候选':'参考文件'}实际时长 {actual.toFixed(3)} 秒，与当前选区 {model.expectedDuration.toFixed(3)} 秒不一致，已超过一帧容差。请重新准备或检查文件，勿据此提交。</AlertDescription></Alert>}
  {error&&<Alert variant="destructive" data-tone="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
 </>;
 return <section className={`media-section submission-preview ${compact?'preview-compact':''} ${expanded?'preview-expanded':''}`} aria-label="视频预览与分段参考" data-testid="submission-preview">
  {compact&&<div className="preview-size-controls">
   <span>预览仅用于查看，播放不会提交任务。</span>
   <Button type="button" className="source-media-expand" aria-expanded={expanded} aria-controls={sourcePanelId} onClick={toggleExpanded} variant="outline">{expandIcon} {expandLabel}</Button>
  </div>}
  <Tabs value={selectedMode} onValueChange={setMode}>
   <TabsList className="source-preview-tabs preview-mode-tabs" aria-label="选择播放对象">
    {tabs.map(([key,label])=><TabsTrigger key={key} value={key} data-testid={`preview-mode-${key}`} onClick={()=>{
     if(mode==='segment-source'&&key==='puppet')setMode('puppet');
    }}>{label}</TabsTrigger>)}
   </TabsList>
   <div id={sourcePanelId}>
    {tabs.map(([key])=><TabsContent key={key} value={key}>{selectedMode===key&&previewDetails}</TabsContent>)}
   </div>
  </Tabs>
 </section>;
}
export function SegmentList({segments=[],selectedId,onSelect,onEdit,project,compact=false,sourceUi=false}){
 const replacement=project?.workflow==='replacement';
 const stale=replacement&&Object.hasOwn(project,'preparation')&&!project.preparation;
 const radioPrefix=useId();
 return <RadioGroup className={`segment-list segment-single-select ${compact?'segment-list-compact':''}`} value={selectedId??''} onValueChange={onSelect} aria-label="选择当前处理片段">{segments.length?segments.map(s=>{
  const selected=selectedId===s.id;
  const reference=s.reference?(stale?'参考文件需更新':'参考文件已制作'):'尚未制作参考文件';
  const result=s.candidate_review?.approved?'替换结果已复核':s.composited?'合成结果待复核':s.generated?'生成结果待复核':'尚无生成结果';
  return <label htmlFor={`${radioPrefix}-${s.id}`} className={`segment-row ${selected?'selected':''}`} key={s.id} data-testid={`select-segment-${s.id}`}>
   <RadioGroupItem id={`${radioPrefix}-${s.id}`} value={s.id} className="segment-radio" onClick={selected?()=>onSelect(s.id):undefined} aria-label={`${selected?'当前片段：':'选择片段：'}${s.name||s.id+'段'}，${(s.end-s.start).toFixed(2)}秒${replacement?`，${reference}，${result}`:''}`} />
   <span className="segment-thumb">{s.reference?.thumbnail_url||s.thumbnail_url?<img src={s.reference?.thumbnail_url||s.thumbnail_url} alt=""/>:s.reference?.url?<video src={s.reference.url+'#t=0.1'} muted playsInline preload="metadata" tabIndex={-1}/>:<Film size={21} />}</span>
   <span className="segment-copy"><span className="people-reference-segment-title flex flex-wrap items-center gap-x-4"><strong>{s.name||`${s.id}段`}</strong><span>{(s.end-s.start).toFixed(2)} 秒 · 原片 {time(s.start)} — {time(s.end)}</span></span><small>{selected?'正在处理此片段':'选择后查看或处理此片段'}{s.review_notes?` · ${s.review_notes}`:''}</small></span>
   {replacement?<span className="segment-file-states"><Badge variant={s.reference&&stale?'warning':'secondary'}>{reference}</Badge><Badge variant={s.candidate_review?.approved?'success':'secondary'}>{result}</Badge></span>:<Status value={s.status} />}
  </label>;
 }):<Empty>保存分段后，可在这里选择当前处理的片段。</Empty>}</RadioGroup>;
}
export function Timeline({project,selectedId,onSelect,currentTime=0,sourceUi=false}){
 const duration=Math.max(project.timeline_duration||project.source_meta?.duration||0,...(project.segments||[]).map(s=>s.end),1);
 const ticks=Array.from({length:6},(_,i)=>i*duration/5);
 const ClipButton=Button;
 return <section className="timeline" aria-label="原片时间轴">
  <div className="time-ruler"><span></span><div>{ticks.map(t=><span key={t}>{time(t)}</span>)}</div></div>
  <div className="timeline-track">
   <span><Film size={16} />视频片段</span>
   <div className="track-area">
    {project.segments?.map((segment,index)=>{
     const selected=selectedId===segment.id;
     const style={left:`${segment.start/duration*100}%`,width:`${(segment.end-segment.start)/duration*100}%`,top:index%2?19:0};
     return <ClipButton key={segment.id} type="button" variant={selected?'default':'secondary'} aria-pressed={selected} className={`clip ${selected?'active':''}`} style={style} onClick={()=>onSelect(segment.id)} title={`${segment.name} ${time(segment.start)}—${time(segment.end)}`}>
      <strong>{segment.name||segment.id+'段'}</strong>
      <small>{time(segment.start)} — {time(segment.end)}</small>
     </ClipButton>;
    })}
    <span className="playhead" style={{left:`${Math.min(100,currentTime/duration*100)}%`}}/>
   </div>
  </div>
  <div className="timeline-track audio-track"><span><Music2 size={16} /><span>原片声音<small>保持原时间轴</small></span></span><div className="audio-area">{project.music?.asset?<Waveform asset={project.music.asset} />:<span className="track-placeholder">提取独立音轨可查看波形；成片声音以原片为准</span>}</div></div>
 </section>;
}
function Waveform({asset}){const canvas=useRef();const [error,setError]=useState(false);useEffect(()=>{let stop=false;let context;async function draw(){try{const response=await fetch(assetUrl(asset));if(!response.ok)throw new Error();context=new AudioContext();const buffer=await context.decodeAudioData(await response.arrayBuffer());if(stop)return;const el=canvas.current;if(!el)return;el.width=1200;el.height=70;const ctx=el.getContext('2d');ctx.strokeStyle=getComputedStyle(el).getPropertyValue('--waveform').trim()||getComputedStyle(el).color;ctx.lineWidth=1.7;const samples=buffer.getChannelData(0),step=Math.ceil(samples.length/600);for(let x=0;x<600;x++){let peak=0;for(let i=x*step;i<Math.min(samples.length,(x+1)*step);i++)peak=Math.max(peak,Math.abs(samples[i]));ctx.beginPath();ctx.moveTo(x*2,35-peak*30);ctx.lineTo(x*2,35+peak*30);ctx.stroke();}}catch{if(!stop)setError(true);}finally{context?.close();}}draw();return()=>{stop=true;};},[asset.id]);return error?<span className="track-placeholder">连续音轨已接入 · {asset.name}</span>:<canvas ref={canvas} aria-label="原片音频实际波形"/>;}
