import {Slider} from "./ui/slider";
import React, { useEffect, useId, useRef, useState } from 'react';

import { Button as UiButton } from './ui/button';
import { Alert, AlertDescription } from './ui/alert';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { Separator } from './ui/separator';
import { ResultDisclosure } from './ResultControls';
import { Textarea } from './ui/textarea';


import { AlertCircle, Captions, Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { assetUrl, time } from '../api';
import { rawResultComparisonTiming, sourceToPreview, shouldCorrectPlayback } from '../candidateComparisonTiming';
import { readComparisonNotes, writeComparisonNotes } from '../resultPresentation';
import './ResultComparison.css';

export default function CandidateComparison({project,segment,evidence}){
 const original=useRef(null),candidate=useRef(null),subtitleTrack=useRef(null),animation=useRef(null),lastCorrection=useRef(-Infinity);
 const subtitleInput=useRef(null);
 const detailsId=useId();
 const noteId=`${detailsId}-note`;
 const sourceMeta={...project.source?.meta,...project.source_meta};
 const timing=rawResultComparisonTiming(sourceMeta,{...segment,candidate_validation:evidence.report},evidence.actual),duration=timing.duration;
 const fps=evidence.actual?.fps_rational||segment.generated?.meta?.fps_rational;
 const notesKey=[project.id,segment.id,segment.generated.id].join(':');
 const [playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[missing,setMissing]=useState(false),[muted,setMuted]=useState(false),[playError,setPlayError]=useState('');
 const [notes,setNotes]=useState(()=>readComparisonNotes(notesKey));
 const [subtitle,setSubtitle]=useState(null),[subtitleVisible,setSubtitleVisible]=useState(false),[subtitleError,setSubtitleError]=useState('');
 const subtitleUrl=subtitle?.url;
 useEffect(()=>()=>{if(animation.current!==null)cancelAnimationFrame(animation.current);},[]);
 useEffect(()=>()=>{if(subtitleUrl)URL.revokeObjectURL(subtitleUrl);},[subtitleUrl]);
 useEffect(()=>{if(subtitleTrack.current)subtitleTrack.current.track.mode=subtitleVisible?'showing':'disabled';},[subtitleUrl,subtitleVisible]);
 function selectSubtitleFile(file){
  if(!file)return;
  setSubtitleError('');
  setSubtitle({name:file.name,url:URL.createObjectURL(new Blob([file],{type:'text/vtt'}))});
  setSubtitleVisible(true);
 }
 function stopPair(){
  if(animation.current!==null)cancelAnimationFrame(animation.current);
  animation.current=null;
  candidate.current?.pause();
  setPlaying(false);
 }
 function pause(){original.current?.pause();stopPair();}
 function playbackError(){pause();setPlayError('视频暂时无法播放，请查看原始文件。');}
 function sync(force=false,showPosition=true){
  const source=original.current,raw=candidate.current;
  if(!source||!raw)return;
  const target=sourceToPreview(source.currentTime,timing);
  if(showPosition||target.ended)setPosition(target.position);
  setMissing(target.missing);
  const running=!source.paused,now=performance.now();
  if(shouldCorrectPlayback({currentTime:raw.currentTime,targetTime:target.time,playing:running,force,starting:running&&target.active&&raw.paused,seeking:raw.seeking,fps,now,lastCorrectionAt:lastCorrection.current})){
   raw.currentTime=target.time;
   lastCorrection.current=now;
  }
  if(target.ended){pause();return;}
  if(running&&target.active){if(raw.paused&&!raw.ended)raw.play().catch(playbackError);}
  else if(!raw.paused)raw.pause();
 }
 function startPair(){
  if(animation.current!==null)cancelAnimationFrame(animation.current);
  setPlaying(true);
  sync(true);
  function tick(){
   sync(false,false);
   animation.current=original.current&&!original.current.paused?requestAnimationFrame(tick):null;
  }
  if(!original.current?.paused)animation.current=requestAnimationFrame(tick);
 }
 async function toggle(){
  const source=original.current;
  if(!source||!candidate.current)return;
  if(!source.paused){pause();return;}
  setPlayError('');
  if(position>=duration-.01||source.currentTime<timing.sourcePlaybackStart)source.currentTime=timing.sourcePlaybackStart;
  try{await source.play();}catch{playbackError();}
 }
 function seek(value){
  if(!original.current||!candidate.current)return;
  const sourceTime=timing.sourceVideoStart+value,target=sourceToPreview(sourceTime,timing);
  original.current.currentTime=sourceTime;
  candidate.current.currentTime=target.time;
  setMissing(target.missing);
  setPosition(value);
 }
 return (
  <div className="result-comparison">
   <div className="result-comparison-pictures">
    <div className="result-comparison-picture">
     <span className="text-sm font-medium">原片{sourceMeta.has_audio!==false?' · 连续原声':''}</span>
     <video ref={original} src={assetUrl(project.source)||undefined} muted={muted} playsInline preload="metadata"
      aria-label="候选对照原片" onLoadedMetadata={event=>{event.currentTarget.currentTime=timing.sourcePlaybackStart;}}
      onTimeUpdate={()=>sync()} onSeeked={()=>sync(true)} onPlay={startPair} onPause={stopPair}
      onEnded={pause} onError={playbackError}/>
    </div>
    <div className="result-comparison-picture">
     <span className="text-sm font-medium">原始候选画面</span>
     <div className="result-comparison-candidate">
      <video ref={candidate} src={assetUrl(segment.generated)||undefined} muted playsInline preload="metadata"
       aria-label="候选对照原始回片" onLoadedMetadata={event=>{event.currentTarget.currentTime=timing.previewOrigin;}}
       onError={playbackError}>
       {subtitleUrl&&<track key={subtitleUrl} ref={subtitleTrack} kind="subtitles" label="独立预览字幕"
        srcLang="zh" src={subtitleUrl} onError={event=>{
         event.stopPropagation();
         setSubtitleError('字幕无法读取，请核对 WebVTT 文件。');
        }}/>}
      </video>
      {missing&&<span role="status" className="text-sm result-comparison-missing">此处无原始回片画面</span>}
     </div>
    </div>
   </div>

   <div className="result-comparison-playback">
    <UiButton type="button" disabled={!duration} onClick={toggle} aria-label={playing?'暂停对照':'同步播放对照'} variant="outline" size="default">
     {playing?<Pause aria-hidden="true" data-icon="inline-start" />:<Play aria-hidden="true" data-icon="inline-start" />}{playing?'暂停':'同步播放'}
    </UiButton>
    {sourceMeta.has_audio!==false&&<UiButton type="button" aria-pressed={muted} onClick={()=>setMuted(value=>!value)} variant="outline" size="default">
     {muted?<VolumeX aria-hidden="true" data-icon="inline-start" />:<Volume2 aria-hidden="true" data-icon="inline-start" />}{muted?'开启原声':'静音对照'}
    </UiButton>}
    <Slider className="result-comparison-slider" min={0} max={duration||1} step={.001} value={[position]} disabled={!duration} onValueChange={([value])=>seek(value)} aria-label="对照播放位置" aria-valuetext={`${time(position)} / ${time(duration)}`} />
    <span className="text-sm text-muted-foreground result-comparison-time">{time(position)} / {time(duration)}</span>
   </div>

   {playError&&<Alert role="alert" data-tone={"destructive"} variant="destructive">
    <AlertCircle size={16} aria-hidden="true"/>
    <AlertDescription>{playError}</AlertDescription>
   </Alert>}

   <ResultDisclosure label="字幕与对照笔记" className="result-comparison-tools" contentClassName="result-comparison-tools-content">
    <section className="result-comparison-tool" aria-labelledby={`${detailsId}-subtitles`}>
     <h3 id={`${detailsId}-subtitles`} className="font-semibold leading-snug tracking-tight text-base mb-0">独立字幕轨</h3>
     <p className="text-muted-foreground text-sm mb-0">WebVTT 仅用于预览，时间以候选视频为准。</p>
     <input ref={subtitleInput} type="file" hidden accept=".vtt,text/vtt" onChange={event=>{
      selectSubtitleFile(event.currentTarget.files[0]);
      event.currentTarget.value='';
     }}/>
     <div className="flex gap-3 items-center flex-wrap">
      <UiButton type="button" onClick={()=>subtitleInput.current?.click()} variant="outline" size="default">
       <Captions aria-hidden="true" data-icon="inline-start" />选择预览字幕
      </UiButton>
      {subtitle&&<UiButton type="button" aria-pressed={subtitleVisible} onClick={()=>setSubtitleVisible(value=>!value)} variant="outline" size="default">{subtitleVisible?'隐藏字幕':'显示字幕'}</UiButton>}
     </div>
     {subtitle&&<span className="text-muted-foreground text-sm result-comparison-filename">{subtitle.name}</span>}
     {subtitleError&&<Alert role="alert" data-tone={"destructive"} variant="destructive">
      <AlertCircle size={16} aria-hidden="true"/>
      <AlertDescription>{subtitleError}</AlertDescription>
     </Alert>}
    </section>
    <Separator decorative />
    <section className="result-comparison-tool" aria-labelledby={`${detailsId}-notes`}>
     <h3 id={`${detailsId}-notes`} className="font-semibold leading-snug tracking-tight text-base mb-0">动作偏差记录</h3>
     {evidence.notes.length>0&&<ul className="result-comparison-notes">
      {evidence.notes.map(note=><li key={note} className="text-sm">{note}</li>)}
     </ul>}
     <FieldGroup>
      <Field>
       <FieldLabel htmlFor={noteId}>本次会话笔记</FieldLabel>
       <Textarea id={noteId} aria-describedby={`${noteId}-hint`} rows={3} value={notes} onChange={event=>{setNotes(event.target.value);writeComparisonNotes(notesKey,event.target.value);}} />
       <FieldDescription id={`${noteId}-hint`}>仅保留在当前浏览会话；可记录帧号、时间和画面差异。</FieldDescription>
      </Field>
     </FieldGroup>
    </section>
   </ResultDisclosure>
  </div>
 );
}
