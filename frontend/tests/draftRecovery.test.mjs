import test from 'node:test';
import assert from 'node:assert/strict';
import {copy,mergeDraft,mergeProjectPatch,newerProject} from '../src/draftMerge.js';
import {DRAFTS,changeDraft,finishDraftSave} from '../src/draftState.js';
import {saveWithRecovery} from '../src/saveRecovery.js';
import {request} from '../src/api.js';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import useDraft from '../src/useDraft.js';

const position=(time,visibility='visible')=>({segment_id:time<10?'A':'C',time,visibility,confirmed:true,box:[.1,.1,.4,.8]});
const actor={id:'singer',label:'主唱',scope:'person',observations:Array.from({length:11},(_,i)=>position(i,i%3===0?'out':'visible'))};
const project={id:'project',revision:1,source_actors:[actor,{id:'bystander',label:'路人',observations:[]}],segments:[{id:'A',start:0,end:10},{id:'C',start:10,end:20}]};
const conflictError=()=>Object.assign(new Error('updated'),{status:409,code:'REVISION_CONFLICT',current_revision:2});

test('old 11-record draft deletes only known unchanged rows and retains 81 concurrent additions and other actors',()=>{
 const mine={...copy(actor),observations:actor.observations.filter(row=>row.visibility!=='out')};
 const latest=copy(project);latest.revision=9;
 latest.source_actors[0].observations.push(...Array.from({length:81},(_,i)=>position(11+i/100)));
 latest.source_actors[1].label='已更新的路人';latest.source_actors.push({id:'reflection',label:'倒影',observations:[]});
 const before=copy(latest),partial={source_actors:[mine,project.source_actors[1]]};
 const result=mergeProjectPatch(project,partial,latest,{identity:'projectsinger',base:actor,value:mine});
 assert.deepEqual(result.conflicts,[]);
 assert.equal(result.patch.source_actors[0].observations.length,88);
 assert.equal(result.patch.source_actors[1].label,'已更新的路人');
 assert.equal(result.patch.source_actors[2].id,'reflection');
 assert.deepEqual(latest,before);
});

test('same field conflicts are explicit and every choice is required; delete versus changed record is a conflict',()=>{
 const base={label:'旧名',observations:[position(1)]},mine={label:'我的名字',observations:[]},latest={label:'新名',observations:[{...position(1),visibility:'occluded'}]};
 const result=mergeDraft(base,mine,latest);
 assert.equal(result.conflicts.length,2);
 assert.ok(result.conflicts.some(row=>row.label.includes('出现位置')));
 const resolved=mergeDraft(base,mine,latest,Object.fromEntries(result.conflicts.map(row=>[row.key,'mine'])));
 assert.deepEqual(resolved.conflicts,[]);assert.deepEqual(resolved.value,mine);
 const remote=mergeDraft(base,mine,latest,Object.fromEntries(result.conflicts.map(row=>[row.key,'latest'])));
 assert.deepEqual(remote.value,latest);
});

test('position time/segment moves, atomic boxes, and concurrent identical-position additions do not lose edits',()=>{
 const b={observations:[position(1)]},m={observations:[position(12)]},r={observations:[{...position(1),confirmed:false}]};
 const result=mergeDraft(b,m,r);
 assert.equal(result.conflicts.length,1);
 assert.ok(result.value.observations.some(row=>row.segment_id==='C'));
 assert.equal(mergeDraft({box:[0,0,1,1]},{box:[.1,0,1,1]},{box:[0,.1,1,1]}).conflicts.length,1);
 assert.equal(mergeDraft([],[position(1)],[{...position(1),confirmed:false}]).conflicts.length,1);
});

test('mask draft recovery applies only to its own segment and keeps latest material links',()=>{
 const latest=copy(project);latest.segments[0].mask_keyframes=[{time:0,confirmed:false}];latest.segments[0].edit_mask={id:'new-mask'};
 latest.segments[1].mask_keyframes=[{time:10,confirmed:true}];
 const partial={segments:project.segments.map(row=>row.id==='C'?{...row,mask_keyframes:[{time:11,confirmed:false}]}:row)};
 const result=mergeProjectPatch(project,partial,latest,{identity:'projectmaskC',base:[],value:partial.segments[1].mask_keyframes});
 assert.deepEqual(result.conflicts,[]);
 assert.deepEqual(result.patch.segments[0],latest.segments[0]);
 assert.equal(result.patch.segments[1].mask_keyframes.length,2);
});

test('recovery fetches latest, merges safe edits, and writes with that revision',async()=>{
 const latest=copy(project);latest.revision=4;latest.source_actors[0].observations.push(position(12));
 const mine={...copy(actor),label:'新名称'},writes=[],seen=[];
 const result=await saveWithRecovery({project,partial:{source_actors:[mine],expected_revision:1},draft:{identity:'projectsinger',base:actor,value:mine},read:async()=>({project:latest}),write:async body=>{writes.push(copy(body));if(writes.length===1)throw conflictError();return {project:{...latest,...body,revision:5}};},onLatest:p=>seen.push(p.revision),choose:()=>assert.fail('safe merge must not ask for conflict choices')});
 assert.deepEqual(seen,[4]);assert.equal(writes[1].expected_revision,4);
 assert.equal(writes[1].source_actors[0].observations.length,12);assert.equal(writes[1].source_actors[1].id,'bystander');
 assert.equal(result.project.revision,5);
});

test('true conflict waits for explicit choices; cancel writes nothing further and leaves the draft',async()=>{
 const latest=copy(project);latest.revision=2;latest.source_actors[0].label='后台名称';
 const mine={...copy(actor),label:'草稿名称'};let writes=0,choices=0;
 const result=await saveWithRecovery({project,partial:{source_actors:[mine],expected_revision:1},draft:{identity:'projectsinger',base:actor,value:mine},read:async()=>latest,write:async()=>{writes++;throw conflictError();},onLatest:()=>{},choose:async conflicts=>{choices++;assert.equal(writes,1);assert.equal(conflicts.length,1);return null;}});
 assert.equal(result,null);assert.equal(writes,1);assert.equal(choices,1);
});

test('another remote update after resolving a conflict is checked again before saving',async()=>{
 let writes=0,reads=0,choices=0;
 const mine={...copy(actor),label:'草稿名称'};
 const result=await saveWithRecovery({project,partial:{source_actors:[mine],expected_revision:1},draft:{identity:'projectsinger',base:actor,value:mine},read:async()=>({...copy(project),revision:++reads+1,source_actors:[{...copy(actor),label:'后台名称'+reads}]}),write:async body=>{writes++;if(writes<3)throw conflictError();return {...body,revision:4};},onLatest:()=>{},choose:async conflicts=>{choices++;return Object.fromEntries(conflicts.map(row=>[row.key,'mine']));}});
 assert.equal(choices,2);assert.equal(writes,3);assert.equal(result.source_actors[0].label,'草稿名称');
});

test('navigation, in-flight edits, discarded drafts and late responses keep the correct draft identity',()=>{
 DRAFTS.clear();
 const original=changeDraft('pactor',actor,1,{...actor,label:'提交名'});
 const submitted={...original,saving:Symbol('save')};DRAFTS.set('pactor',submitted);
 changeDraft('pmaskC',[],2,[{time:12,confirmed:false}]);
 changeDraft('pactor',actor,1,d=>({...d,label:'提交后继续输入'}));
 finishDraftSave('pactor',submitted,{...actor,label:'提交名',color:'#123456'},3);
 assert.equal(DRAFTS.get('pactor').value.label,'提交后继续输入');
 assert.equal(DRAFTS.get('pactor').value.color,'#123456');
 assert.equal(DRAFTS.get('pactor').revision,3);assert.equal(DRAFTS.get('pmaskC').value[0].time,12);
 DRAFTS.delete('pactor');changeDraft('pactor',actor,4,{...actor,label:'重新编辑'});
 finishDraftSave('pactor',submitted,{...actor,label:'旧响应'},3);
 assert.equal(DRAFTS.get('pactor').value.label,'重新编辑');
 const newest={id:'p',revision:7};assert.equal(newerProject(newest,{id:'p',revision:6}),newest);
 assert.equal(newerProject(newest,{id:'other',revision:1}).id,'other');
 assert.equal(newerProject(null,null),null);assert.equal(newerProject(null,newest),newest);
 DRAFTS.clear();
});

test('API errors retain conflict code/current revision and unrelated errors do not enter recovery',async()=>{
 const originalFetch=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'版本冲突',code:'REVISION_CONFLICT',current_revision:92}),{status:409,headers:{'Content-Type':'application/json'}});
  await assert.rejects(request('/test'),error=>error.status===409&&error.code==='REVISION_CONFLICT'&&error.current_revision===92);
 }finally{globalThis.fetch=originalFetch;}
 await assert.rejects(saveWithRecovery({project,partial:{},write:async()=>{throw new Error('offline');},read:()=>assert.fail('no conflict'),onLatest:()=>{},choose:()=>assert.fail('no conflict')}),/offline/);
});

test('useDraft keeps its base after remount/revision changes and late saves preserve edits in the correct role scope',async()=>{
 DRAFTS.clear();let controls;
 function Harness({value,identity,revision}){controls=useDraft(value,identity,revision);return React.createElement('output',null,controls[0].label||controls[0].name||'');}
 const render=(value,identity,revision)=>renderToStaticMarkup(React.createElement(Harness,{value,identity,revision}));
 render(actor,'pactor',1);controls[1](value=>({...value,label:'草稿名'}));
 render({...actor,label:'后台名'},'pactor',5);
 assert.equal(controls[0].label,'草稿名');assert.equal(controls[2],true);
 let resolve,received;
 const pending=controls[3](async(partial,context)=>{received={partial,context};return new Promise(done=>{resolve=done;});},{source_actors:[controls[0]]});
 assert.equal(received.partial.expected_revision,1);assert.equal(received.context.base.label,actor.label);
 render([],'pmaskC',5);controls[1]([{time:12,confirmed:false}]);
 render({...actor,label:'后台名'},'pactor',5);controls[1](value=>({...value,label:'继续输入'}));
 resolve({project:{id:'p',revision:6,source_actors:[{...actor,label:'已保存',color:'#ffffff'}]}});await pending;
 render({...actor,label:'已保存'},'pactor',6);assert.equal(controls[0].label,'继续输入');assert.equal(controls[0].color,'#ffffff');
 render([],'pmaskC',6);assert.equal(controls[0][0].time,12);
 const role={id:actor.id,name:'旧角色',description:''};render(role,'prole',6);controls[1](value=>({...value,name:'新角色'}));
 render(role,'prole',6);let resolveRole;
 const roleSave=controls[3](()=>new Promise(done=>{resolveRole=done;}),{roles:[controls[0]]});
 controls[1](value=>({...value,description:'保存期间输入'}));
 resolveRole({project:{revision:7,source_actors:[actor],roles:[{...role,name:'新角色',color:'#123456'}]}});await roleSave;
 assert.equal(DRAFTS.get('prole').value.name,'新角色');assert.equal(DRAFTS.get('prole').value.description,'保存期间输入');assert.equal(DRAFTS.get('prole').value.color,'#123456');
 DRAFTS.clear();
});
