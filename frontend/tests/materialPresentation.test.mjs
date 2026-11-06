import test from 'node:test';
import assert from 'node:assert/strict';
import {groupMaterialIssues,materialState} from '../src/materialPresentation.js';

const segments=[{id:'A',start:0,end:10,reference:{id:'clip-a',sha256:'sha-a',name:'A.mp4',meta:{video_duration_seconds:10}}},{id:'B',start:10,end:20,reference:{id:'clip-b',sha256:'sha-b',name:'B.mp4',meta:{video_duration_seconds:10}}}];
const plan={fingerprint:'current-version',ready:true,issues:[{code:'LOCATORS_ONLY',blocking:false,message:'定位框用于认人'}],references:[{reference:'图9',asset_id:'fallback'}],segment_plans:{A:{references:[{reference:'图2',asset_id:'role-a'}]},B:{references:[]}},wan_compatibility:{ready:true,issues:[]}};
const project={id:'project',source:{id:'source'},segments,replacement_plan:plan,preparation:{fingerprint:plan.fingerprint,plan,references:{A:{id:'clip-a',sha256:'sha-a'},B:{id:'clip-b',sha256:'sha-b'}}},generic_review:null};
const inputs=segments.map(segment=>({segment_id:segment.id,asset_id:segment.reference.id,sha256:segment.reference.sha256,duration_seconds:10}));
const successful={id:'preflight',project_id:'project',action:'preflight',status:'SUCCEEDED',created_at:'2026-11-03T01:00:00Z',segment_id:null,result:{plan,estimate_complete:true,total_reserved_yuan:72,reports:inputs.map(input=>({segment_id:input.segment_id,input_reference:input})),submission_inputs:inputs,blockers:['上传与付费生成尚未授权']}};

test('without a current plan, old references and amounts never become current material state',()=>{
 const state=materialState({...project,replacement_plan:null,preparation:null},[successful]);
 assert.equal(state.plan,null);
 assert.equal(state.status,'unchecked');
 assert.equal(state.currentPreflight,null);
 assert.equal(state.currentEstimate,null);
 assert.equal(state.partialEstimate,null);
 assert.equal(state.oldPreflights.length,1);
 assert.equal(state.files[0].asset,segments[0].reference);
 assert.equal(state.files[0].state,'outdated');
 assert.equal(state.files[0].verified,false);
 assert.equal(state.primaryAction.action,'replacement_check');
});

test('local material readiness and local review remain separate from Wan compatibility and fees',()=>{
 const incompatible={...plan,wan_compatibility:{ready:false,issues:[{code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message:'帧率不兼容'}]}};
 const state=materialState({...project,replacement_plan:incompatible,generic_review:{approved:true,fingerprint:plan.fingerprint}});
 assert.equal(state.localReady,true);
 assert.equal(state.wanReady,false);
 assert.equal(state.approved,true);
 assert.equal(state.status,'reviewed');
 assert.equal(state.currentEstimate,null);
 assert.equal(state.localBlockers.length,0);
 assert.equal(state.blockers.length,1);
 assert.equal(state.primaryAction.action,'package');
 assert.equal('canReview' in state,false,'presentation does not introduce an approval gate');
});

test('missing or older preparation cannot make existing references current or verified',()=>{
 for(const preparation of [null,{...project.preparation,fingerprint:'older'}]){
  const state=materialState({...project,preparation},[successful]);
  assert.equal(state.prepared,false);
  assert.equal(state.localReady,true);
  assert.equal(state.status,'needs-preparation');
  assert.equal(state.files[0].state,'outdated');
  assert.equal(state.files[0].verified,false);
  assert.equal(state.primaryAction.action,'prepare_replacement');
 }
 const fallback=materialState({...project,replacement_plan:null});
 assert.equal(fallback.plan,project.preparation.plan);
 assert.equal(fallback.prepared,true);
});

test('review must explicitly approve the same nonempty material fingerprint',()=>{
 for(const generic_review of [{approved:true,fingerprint:'older'},{approved:false,fingerprint:plan.fingerprint},{approved:'true',fingerprint:plan.fingerprint}]){
  assert.equal(materialState({...project,generic_review}).approved,false);
 }
 const noFingerprint=materialState({...project,replacement_plan:{...plan,fingerprint:null},generic_review:{approved:true,fingerprint:null}});
 assert.equal(noFingerprint.approved,false);
 assert.equal(noFingerprint.currentEstimate,null);
 assert.equal(materialState(project).primaryAction.targetId,'material-review');
});

test('incomplete current preflight keeps partial reservations and all blockers even without preparation',()=>{
 const job={...successful,result:{...successful.result,estimate_complete:false,total_reserved_yuan:36,reports:[successful.result.reports[0]],submission_inputs:[inputs[0]],blockers:['B段素材待处理','上传未授权']}};
 const state=materialState({...project,preparation:null},[job]);
 assert.equal(state.currentPreflight.job,job);
 assert.equal(state.currentEstimate,null);
 assert.equal(state.partialEstimate.reservedYuan,36);
 assert.deepEqual(state.partialEstimate.blockers,['B段素材待处理','上传未授权']);
 const none={...job,result:{...job.result,total_reserved_yuan:0,reports:[],submission_inputs:[]}};
 assert.equal(materialState({...project,preparation:null},[none]).partialEstimate.reservedYuan,0);
});

test('complete current estimates require global scope, complete coverage, and actual reference IDs and hashes',()=>{
 const complete=materialState(project,[successful]);
 assert.equal(complete.currentEstimate.reservedYuan,72);
 assert.equal(complete.currentEstimate.allSegmentsCovered,true);
 assert.equal(complete.partialEstimate,null);
 assert.equal(complete.files[0].verified,true);
 assert.equal(complete.files[0].asset,segments[0].reference);
 const variants=[
  {...successful,segment_id:'A'},
  {...successful,result:{...successful.result,reports:[successful.result.reports[0]]}},
  {...successful,result:{...successful.result,submission_inputs:[inputs[0]]}},
  {...successful,result:{...successful.result,submission_inputs:[{...inputs[0],sha256:'changed'},inputs[1]]}},
  {...successful,result:{...successful.result,submission_inputs:[{...inputs[0],asset_id:undefined},inputs[1]]}},
  {...successful,result:{...successful.result,reports:[{...successful.result.reports[0],input_reference:{...inputs[0],asset_id:'older-reference'}},successful.result.reports[1]]}},
 ];
 for(const job of variants){const state=materialState(project,[job]);assert.equal(state.currentEstimate,null);assert.ok(state.partialEstimate);}
});

test('a missing hash leaves a prepared file waiting for preflight rather than calling it an old file',()=>{
 const noHash={...project,segments:[{...segments[0],reference:{...segments[0].reference,sha256:null}},segments[1]]};
 const state=materialState(noHash,[successful]);
 assert.equal(state.files[0].exists,true);
 assert.equal(state.files[0].prepared,true);
 assert.equal(state.files[0].idMatched,true);
 assert.equal(state.files[0].verified,false);
 assert.equal(state.files[0].state,'prepared');
 assert.equal(state.files[0].needsUpdate,false);
 assert.doesNotMatch(state.files[0].label,/旧/);
 assert.equal(state.currentEstimate,null);
 const noIdProject={...project,segments:[{...segments[0],reference:{...segments[0].reference,id:undefined}},segments[1]]};
 const noIdInputs=[{...inputs[0],asset_id:undefined},inputs[1]];
 const noIdJob={...successful,result:{...successful.result,reports:noIdInputs.map(input=>({segment_id:input.segment_id,input_reference:input})),submission_inputs:noIdInputs}};
 const noIdState=materialState(noIdProject,[noIdJob]);
 assert.equal(noIdState.currentEstimate,null,'two missing IDs must not be treated as a real asset match');
 assert.equal(noIdState.files[0].verified,false);
 assert.equal(noIdState.files[0].idMatched,false);
});

test('failed and unknown attempts do not count as completed preflight results',()=>{
 for(const status of ['FAILED','UNKNOWN','RUNNING','QUEUED']){
  const job={...successful,status};
  const state=materialState(project,[job]);
  assert.equal(state.latestPreflightAttempt,job);
  assert.equal(state.currentPreflight,null);
  assert.equal(state.currentEstimate,null);
  assert.equal(state.partialEstimate,null);
 }
 const failure={...successful,id:'failed',created_at:'2026-11-03T02:00:00Z',status:'FAILED',result:null};
 const state=materialState(project,[successful,failure]);
 assert.equal(state.latestPreflightAttempt,failure);
 assert.equal(state.currentEstimate.isLatestAttempt,false,'a prior successful record cannot stand in for the failed latest attempt');
 const legacy={...successful,result:{...successful.result,plan:null}};
 assert.equal(materialState(project,[legacy]).currentPreflight,null);
 assert.equal(materialState(project,[legacy]).oldPreflights.length,1);
});

test('reference numbering comes from the exact segment plan with the existing fallback',()=>{
 const state=materialState(project);
 assert.equal(state.referencesBySegment.A,plan.segment_plans.A.references);
 assert.deepEqual(state.referencesBySegment.A.map(reference=>reference.reference),['图2']);
 assert.deepEqual(state.referencesBySegment.B,[],'an explicitly empty segment reference list does not fall back');
 const fallback=materialState({...project,segments:[...segments,{id:'C',start:20,end:25}]});
 assert.equal(fallback.referencesBySegment.C,plan.references);
 assert.equal(fallback.files[2].state,'missing');
});

test('issue groups retain warning severity and real actor and segment navigation metadata',()=>{
 const issues={issues:[{code:'ACTOR_NOT_LOCATED',message:'缺记录',blocking:true,actor_id:'actor-a',segment_id:'B'},{code:'OCCLUSION_REVIEW',message:'遮挡提示',blocking:false,actor_id:'actor-b'},{code:'EDIT_MASK_INVALID',message:'遮罩待处理',blocking:true,segment_id:'A'},{code:'LOCATORS_ONLY',message:'定位提示',blocking:false}],wan_compatibility:{issues:[{code:'MODEL_FPS_INCOMPATIBLE',message:'模型限制',blocking:true}]}};
 const groups=groupMaterialIssues(issues);
 const identity=groups.find(group=>group.id==='identity');
 assert.deepEqual(identity.issues[0].target,{step:1,targetId:'identity-mapping',segmentId:'B',actorId:'actor-a'});
 assert.equal(identity.issues[0].segment_id,'B');
 assert.equal(groups.find(group=>group.id==='shots').warningCount,1);
 assert.equal(groups.find(group=>group.id==='range').blockerCount,1);
 const source=groups.find(group=>group.id==='source');
 assert.equal(source.blockerCount,1);
 assert.equal(source.issues[0].target.actorId,null);
 assert.equal(source.issues[0].target.segmentId,null);
});

test('material presentation never changes frozen projects, job order, records, or exact timestamps',()=>{
 const input=structuredClone({project,jobs:[{...successful,created_at:'2026-11-03T02:00:00Z'},successful]});
 const before=structuredClone(input);
 function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
 freeze(input);
 const state=materialState(input.project,input.jobs);
 assert.deepEqual(input,before);
 assert.equal(state.plan,input.project.replacement_plan);
 assert.equal(state.files[0].asset,input.project.segments[0].reference);
 assert.equal(state.latestPreflightAttempt,input.jobs[0]);
});
