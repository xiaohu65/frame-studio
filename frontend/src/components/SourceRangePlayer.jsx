import React,{useEffect,useRef,useState} from 'react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle } from 'lucide-react';
import {clampToWindow,selectionPlayback} from '../previewModel';

export default function SourceRangePlayer({model,sourcePath,onTime=()=>{},paused}){
 const video=useRef(null),pauseRequested=useRef(paused);
 const [error,setError]=useState('');
 pauseRequested.current=paused;
 useEffect(()=>{if(paused)video.current?.pause();},[paused]);
 useEffect(()=>{
  const element=video.current;
  if(!element||!model.playable)return;
  const control=selectionPlayback(model.window);
  let active=true,ticker=null,blocked=false;
  const valid=()=>active&&element===video.current;
  const stopTicker=()=>{if(ticker!==null){cancelAnimationFrame(ticker);ticker=null;}};
  const report=()=>{if(valid())onTime(Math.max(0,element.currentTime-model.offset));};
  const finish=completed=>{stopTicker();control.finish(element,null,completed);report();};
  const fail=message=>{blocked=true;stopTicker();element.pause();if(valid())setError(message);};
  function loaded(){
   if(!valid())return;
   if(!Number.isFinite(element.duration)||element.duration<=0){fail('无法读取原片时长，请检查原片文件。');return;}
   if(!model.full&&model.window.end>element.duration+1/model.fps+.003){fail('当前范围超出了可播放的原片，请核对起止时间和文件。');return;}
   control.seek(element,model.initial);report();
  }
  function seeking(){
   if(!valid()||blocked)return;
   if(!model.full){control.observeSeek(element);if(element.currentTime>=model.window.end){finish(false);return;}control.seek(element,clampToWindow(element.currentTime,model.window));}
   report();
  }
  function tick(){
   if(!valid()||element.paused)return;
   if(!model.full&&element.currentTime>=model.window.end-.001){finish(true);return;}
   ticker=requestAnimationFrame(tick);
  }
  function playing(){
   if(!valid())return;
   if(blocked||pauseRequested.current){element.pause();return;}
   if(!model.full)control.restartOnPlay(element);
   stopTicker();if(!model.full)ticker=requestAnimationFrame(tick);report();
  }
  function updated(){
   if(!valid()||blocked)return;
   if(!model.full){if(element.currentTime>=model.window.end-.001){finish(true);return;}if(element.currentTime<model.window.start-.015)control.seek(element,model.window.start);}
   report();
  }
  function ended(){
   if(!valid())return;
   if(!model.full){if(element.currentTime<model.window.end-1/model.fps-.003)fail('原片在所选终点前已结束，请检查文件与分段范围。');else finish(true);}
   else report();
  }
  const failed=()=>{if(valid())fail('原片暂时无法播放，请检查本机文件。');};
  const listeners={loadedmetadata:loaded,seeking,play:playing,pause:stopTicker,timeupdate:updated,ended,error:failed};
  Object.entries(listeners).forEach(([name,handler])=>element.addEventListener(name,handler));
  if(element.readyState>=1)loaded();
  return()=>{active=false;stopTicker();Object.entries(listeners).forEach(([name,handler])=>element.removeEventListener(name,handler));element.pause();};
 },[model.identity]);
 return <>{model.playable?<div className="source-range-video"><video ref={video} src={model.sourceUrl} controls playsInline preload="metadata" aria-label={model.full?'完整原片播放器':'原片当前分段播放器'}/></div>:<div className="source-range-unavailable">{model.sourceUrl?'请先修正分段范围，播放器会随当前时间更新。':'导入原片后可预览。'}</div>}{error&&<Alert role="alert" variant="destructive">
  <AlertCircle aria-hidden="true" />
  <AlertDescription className="flex flex-col gap-3">
   <p>{error}</p>
   <Button asChild variant="outline" size="sm" className="self-start"><a href={model.sourceUrl} target="_blank" rel="noreferrer">打开原片</a></Button>
   {sourcePath&&<p className="min-w-0 text-xs break-all">本机文件：<code>{sourcePath}</code></p>}
  </AlertDescription>
 </Alert>}</>;
}
