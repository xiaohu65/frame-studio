import {request} from './api.js';

function aborted(){return new DOMException('读取状态已取消','AbortError');}
function delay(ms,signal){
 return new Promise((resolve,reject)=>{
  if(signal?.aborted){reject(aborted());return;}
  const cancel=()=>{clearTimeout(timer);reject(aborted());};
  const timer=setTimeout(()=>{signal?.removeEventListener('abort',cancel);resolve();},ms);
  signal?.addEventListener('abort',cancel,{once:true});
 });
}

// Only this known read-only endpoint retries. No mutation can enter this path.
export async function readState(projectId,{signal,read=request,wait=delay,timeoutMs=5000}={}){
 const path='/api/state'+(projectId?'?project_id='+encodeURIComponent(projectId):'');
 for(let attempt=0;attempt<3;attempt++){
  if(signal?.aborted)throw aborted();
  if(attempt)await wait([400,1200][attempt-1],signal);
  const controller=new AbortController();let timedOut=false;
  const cancel=()=>controller.abort();
  signal?.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);
  try{return await read(path,{method:'GET',signal:controller.signal});}
  catch(cause){
   if(signal?.aborted)throw aborted();
   const error=timedOut?Object.assign(new Error('读取本机状态超时。当前内容和草稿仍保留，请稍后重新读取状态。'),
    {method:'GET',path:'/api/state',status:0,kind:'network',code:'STATE_READ_TIMEOUT',retryableRead:true}):cause;
   if(attempt===2||!error.retryableRead)throw error;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
 }
}

// Schedule after settlement, preventing overlapping polls during a slow restart.
// A read burst is capped at three requests; ongoing observation backs off to 30s.
export function startStatePolling({read,onError,interval=3000,maxDelay=30000,schedule=setTimeout,cancel=clearTimeout}){
 let stopped=false,timer,failures=0,controller;
 async function tick(){
  if(stopped)return;
  controller=new AbortController();
  try{await read(controller.signal);failures=0;}
  catch(error){if(!stopped&&error.name!=='AbortError'){failures++;onError(error);}}
  if(!stopped)timer=schedule(tick,Math.min(maxDelay,interval*2**Math.min(failures,4)));
 }
 timer=schedule(tick,interval);
 return()=>{stopped=true;cancel(timer);controller?.abort();};
}
