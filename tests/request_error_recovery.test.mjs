import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {request,upload} from '../scripts/request-error-recovery/api.js';
import {readState,startStatePolling} from '../scripts/request-error-recovery/stateRead.js';
import useDraft from '../frontend/src/useDraft.js';
import {DRAFTS} from '../frontend/src/draftState.js';

const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');

const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json'}});
const blank=()=>new Response('',{status:500,headers:{'content-type':'text/plain'}});
async function withFetch(fn,body){const prior=globalThis.fetch;globalThis.fetch=fn;try{return await body();}finally{globalThis.fetch=prior;}}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('classify the blank proxy 500 response without hiding its HTTP status or claiming all 500s are proxy failures',async()=>{
 await withFetch(async()=>blank(),async()=>{
  await assert.rejects(request('/api/state?project_id=fixture'),error=>{
   assert.equal(error.kind,'empty-response');assert.equal(error.status,500);assert.equal(error.retryableRead,true);
   assert.match(error.message,/可能正在重载/);assert.match(error.diagnostic,/GET \/api\/state/);return true;
  });
 });
});

test('connection failures carry endpoint context without query/body details and never repeat mutation',async()=>{
 let calls=0;
 await withFetch(async()=>{calls++;throw new TypeError('fetch failed');},async()=>{
  await assert.rejects(request('/api/state?project_id=private-value'),error=>{
   assert.equal(error.code,'SERVICE_UNREACHABLE');assert.equal(error.retryableRead,true);
   assert.ok(!error.diagnostic.includes('private-value'));return true;
  });
  for(const method of ['POST','PUT']){
   await assert.rejects(request('/api/projects/fixture/generation',{method,body:{action:'confirm',private:'body-value'}}),error=>{
    assert.equal(error.retryableRead,false);assert.match(error.message,/操作结果尚不能确认/);
    assert.ok(!error.message.includes('body-value'));return true;
   });
  }
 });
 assert.equal(calls,3);
});

test('business errors and revision conflicts preserve their useful payload; backend JSON 500 is not retried',async()=>{
 for(const status of [400,409,500]){
  let calls=0;
  await withFetch(async()=>{calls++;return json({error:'真实业务错误',code:'REVISION_CONFLICT',current_revision:72},status);},async()=>{
   await assert.rejects(readState('fixture',{wait:async()=>{throw new Error('must not retry business errors');}}),error=>{
    assert.equal(error.kind,'backend');assert.equal(error.serverMessage,'真实业务错误');assert.match(error.message,/真实业务错误/);
    assert.equal(error.status,status);assert.equal(error.code,'REVISION_CONFLICT');assert.equal(error.current_revision,72);
    assert.equal(error.retryableRead,false);return true;
   });
  });
  assert.equal(calls,1);
 }
});

test('non-JSON pages and malformed data are diagnosed instead of exposing HTML or parsing exceptions',async()=>{
 for(const response of [new Response('<html><script>private script</script></html>',{headers:{'content-type':'text/html'}}),
                        new Response('{broken',{headers:{'content-type':'application/json'}})]){
  await withFetch(async()=>response,()=>assert.rejects(request('/api/state'),error=>{
   assert.equal(error.kind,'invalid-response');assert.ok(!error.message.includes('<html>'));assert.ok(!error.message.includes('SyntaxError'));return true;
  }));
 }
 await withFetch(async()=>new Response('disk full',{status:500}),()=>assert.rejects(request('/api/state'),error=>{
  assert.match(error.message,/disk full/);assert.equal(error.retryableRead,false);return true;
 }));
});

test('only GET state reads have bounded retry with backoff and recover the same project',async()=>{
 const calls=[],delays=[];
 await withFetch(async(path,options)=>{
  calls.push([path,options.method]);
  return calls.length<3?blank():json({project:{id:'fixture',revision:3}});
 },async()=>{
  const result=await readState('fixture',{wait:async ms=>delays.push(ms)});
  assert.equal(result.project.id,'fixture');assert.equal(result.project.revision,3);
 });
 assert.deepEqual(delays,[400,1200]);assert.equal(calls.length,3);
 assert.ok(calls.every(([path,method])=>path==='/api/state?project_id=fixture'&&method==='GET'));
 let count=0;
 await withFetch(async()=>{count++;return blank();},async()=>{
  await assert.rejects(readState('fixture',{wait:async()=>{}}),{code:'EMPTY_ERROR_RESPONSE'});
  for(const method of ['POST','PUT'])await assert.rejects(request('/api/projects/fixture/generation',{method,body:{action:'confirm'}}),error=>!error.retryableRead);
 });
 assert.equal(count,5); // Three read attempts and exactly one of each mutation.
});

test('uploads keep their binary body and are attempted exactly once',async()=>{
 const file=new File(['fixture bytes'],'fixture.mp4',{type:'video/mp4'});let calls=0;
 await withFetch(async(path,options)=>{
  calls++;assert.equal(options.method,'POST');assert.equal(options.body,file);
  assert.equal(options.headers['Content-Type'],'application/octet-stream');return blank();
 },()=>assert.rejects(upload('fixture','source',file),error=>!error.retryableRead));
 assert.equal(calls,1);
});

test('state timeout is bounded, caller cancellation interrupts the backoff',async()=>{
 let calls=0;
 const read=async(path,{signal})=>{calls++;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));};
 await assert.rejects(readState('fixture',{read,wait:async()=>{},timeoutMs:5}),{code:'STATE_READ_TIMEOUT'});
 assert.equal(calls,3);
 const controller=new AbortController();calls=0;
 await withFetch(async()=>{calls++;setImmediate(()=>controller.abort());return blank();},()=>assert.rejects(readState('fixture',{signal:controller.signal}),{name:'AbortError'}));
 assert.equal(calls,1);
});

test('slow polling never overlaps, backs off after failure and aborts on cleanup',async()=>{
 const timers=[],errors=[];let resolveRead,rejectRead,signal,calls=0;
 const stop=startStatePolling({schedule:(fn,ms)=>{timers.push({fn,ms});return fn;},cancel:()=>{},onError:error=>errors.push(error),
  read:currentSignal=>{signal=currentSignal;calls++;return new Promise((resolve,reject)=>{resolveRead=resolve;rejectRead=reject;});}});
 assert.equal(timers[0].ms,3000);timers.shift().fn();
 assert.equal(calls,1);assert.equal(timers.length,0);
 rejectRead(new Error('connection lost'));await tick();assert.equal(timers[0].ms,6000);assert.equal(errors.length,1);
 timers.shift().fn();resolveRead({});await tick();assert.equal(timers[0].ms,3000);
 timers.shift().fn();stop();assert.equal(signal.aborted,true);resolveRead({});await tick();assert.equal(timers.length,0);
});

test('a canceled old-project poll cannot publish a late failure into the new project',async()=>{
 let task,rejectRead;const errors=[];
 const stop=startStatePolling({schedule:fn=>(task=fn),cancel:()=>{},onError:error=>errors.push(error),
  read:()=>new Promise((resolve,reject)=>{rejectRead=reject;})});
 task();stop();rejectRead(new Error('old project failed'));await tick();assert.equal(errors.length,0);
});

test('an offline read and recovered newer project do not discard a dirty draft or retry its failed save',async()=>{
 DRAFTS.clear();let controls,saves=0;
 const actor={id:'singer',label:'saved singer'};
 function Harness({value,revision}){controls=useDraft(value,'fixture-singer',revision);return React.createElement('output',null,controls[0].label);}
 const render=(value,revision)=>renderToStaticMarkup(React.createElement(Harness,{value,revision}));
 try{
  render(actor,1);controls[1](value=>({...value,label:'unsaved singer'}));render(actor,1);
  let saveError;
  await withFetch(async()=>{throw new TypeError('simulated offline');},async()=>{
   await assert.rejects(controls[3](async()=>{saves++;return request('/api/projects/fixture',{method:'PUT',body:{label:'unsaved singer'}});},{}),error=>{saveError=error;return error.method==='PUT'&&!error.retryableRead;});
   await assert.rejects(readState('fixture',{wait:async()=>{}}),{code:'SERVICE_UNREACHABLE'});
  });
  const latest=await withFetch(async()=>json({project:{id:'fixture',revision:2,source_actors:[{...actor,label:'new saved singer'}]}}),()=>readState('fixture'));
  render(latest.project.source_actors[0],latest.project.revision);
  assert.equal(controls[0].label,'unsaved singer');assert.equal(controls[2],true);
  assert.equal(DRAFTS.get('fixture-singer').base.label,'saved singer');
  assert.equal(saveError.method,'PUT');assert.match(saveError.message,/未自动重发/);assert.equal(saves,1);
 }finally{DRAFTS.clear();}
});
