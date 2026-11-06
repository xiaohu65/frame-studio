import test from 'node:test';
import assert from 'node:assert/strict';
import {ORIGINAL_DELIVERY_POLICY,resultSegmentState,resultDraftKey,readResultDraft,writeResultDraft,clearResultDraft,captureResultRecheck,trackResultRecheck,reconcileResultRechecks} from '../src/resultPresentation.js';
let sequence=0;
function fixture(checks={identity:true,action:true}){
 const number=++sequence,project={id:'recheck-project-'+number,segments:[{id:'A',generated:{id:'candidate-a'}},{id:'B',generated:{id:'candidate-b'}}]};
 const row={candidate_current:true,candidate_asset_id:'candidate-a',validation_current:true,candidate_fingerprint:'candidate-version-a'};
 const context={project,resultStates:{delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:row,B:{...row,candidate_asset_id:'candidate-b',candidate_fingerprint:'candidate-version-b'}}},materialConfirmation:{current:true,fingerprint:'materials-version'}};
 const state=resultSegmentState(project.segments[0],context.resultStates);
 const key=resultDraftKey(project.id,project.segments[0],state);writeResultDraft(key,{checks,editing:true});
 const token=captureResultRecheck(project,project.segments[0],state,'materials-version');
 const job={id:'recheck-job-'+number,project_id:project.id,action:'validate_candidate',segment_id:'A',input_candidate_id:'candidate-a',input_material_fingerprint:'materials-version',status:'QUEUED',result:null};
 const passed={...job,status:'SUCCEEDED',result:{report:{ready:true,fingerprint:row.candidate_fingerprint,segment_id:'A'}}};
 return {project,row,context,key,token,job,passed};
}
test('capture, task id, queued and running states retain partial and full checks until a real pass',()=>{
 for(const checks of [{identity:true},{identity:true,action:true,camera:true,timing:true,audio:true,occlusion:true}]){
  const f=fixture(checks);assert.deepEqual(readResultDraft(f.key).checks,checks);trackResultRecheck(f.token,f.job.id);
  for(const status of ['QUEUED','RUNNING']){assert.deepEqual(reconcileResultRechecks([{...f.job,status}],f.context),[]);assert.deepEqual(readResultDraft(f.key),{checks,editing:true});}
  assert.equal(reconcileResultRechecks([f.passed],f.context)[0].key,f.key);assert.deepEqual(readResultDraft(f.key),{checks:{},editing:true});
 }
});
test('failed requests and cancelled confirmations never register or clear checks',()=>{
 const f=fixture();trackResultRecheck(f.token,null);assert.deepEqual(reconcileResultRechecks([f.passed],f.context),[]);assert.deepEqual(readResultDraft(f.key).checks,{identity:true,action:true});
});
test('job failure and technically failed SUCCEEDED preserve both checks and editing',()=>{
 for(const completion of [{status:'FAILED'},{status:'SUCCEEDED',result:{report:{ready:false}}}]){const f=fixture();trackResultRecheck(f.token,f.job.id);assert.deepEqual(reconcileResultRechecks([{...f.job,...completion}],f.context),[]);assert.deepEqual(readResultDraft(f.key),{checks:{identity:true,action:true},editing:true});}
});
test('UNKNOWN and incomplete reports remain pending until this exact task is confirmed passed',()=>{
 const f=fixture();trackResultRecheck(f.token,f.job.id);
 for(const update of [{status:'UNKNOWN'},{status:'SUCCEEDED',result:null},{status:'SUCCEEDED',result:{report:{ready:'true'}}}]){assert.deepEqual(reconcileResultRechecks([{...f.job,...update}],f.context),[]);assert.equal(readResultDraft(f.key).checks.identity,true);}
 assert.equal(reconcileResultRechecks([f.passed],f.context).length,1);assert.deepEqual(readResultDraft(f.key).checks,{});
});
test('completion while editing another segment clears only the submitted draft',()=>{
 const f=fixture(),otherKey=resultDraftKey(f.project.id,f.project.segments[1],resultSegmentState(f.project.segments[1],f.context.resultStates));
 writeResultDraft(otherKey,{checks:{camera:true},editing:true});trackResultRecheck(f.token,f.job.id);reconcileResultRechecks([f.passed],f.context);
 assert.deepEqual(readResultDraft(f.key).checks,{});assert.deepEqual(readResultDraft(otherKey).checks,{camera:true});
});
test('switching projects or unmounting defers clearing until the original context returns',()=>{
 const f=fixture(),other=fixture({audio:true});trackResultRecheck(f.token,f.job.id);
 assert.deepEqual(reconcileResultRechecks([f.passed],other.context),[]);assert.equal(readResultDraft(f.key).checks.identity,true);assert.equal(readResultDraft(other.key).checks.audio,true);
 assert.equal(reconcileResultRechecks([f.passed],f.context).length,1);assert.deepEqual(readResultDraft(f.key).checks,{});
});
test('changed candidate, material or fingerprint protects newer-version drafts from late results',()=>{
 for(const kind of ['candidate','material','fingerprint']){const f=fixture();trackResultRecheck(f.token,f.job.id);const context=structuredClone(f.context);
  if(kind==='candidate')context.project.segments[0].generated.id='new-candidate';if(kind==='material')context.materialConfirmation.fingerprint='new-materials';if(kind==='fingerprint')context.resultStates.segments.A.candidate_fingerprint='new-fingerprint';
  const newKey=resultDraftKey(context.project.id,context.project.segments[0],resultSegmentState(context.project.segments[0],context.resultStates));writeResultDraft(newKey,{checks:{occlusion:true},editing:true});
  assert.deepEqual(reconcileResultRechecks([f.passed],context),[]);assert.deepEqual(readResultDraft(newKey).checks,{occlusion:true});
 }
});
test('later edits or clearing and recreating the same draft identity are protected',()=>{
 for(const recreate of [false,true]){const f=fixture();trackResultRecheck(f.token,f.job.id);if(recreate)clearResultDraft(f.key);writeResultDraft(f.key,{checks:{timing:true},editing:true});assert.deepEqual(reconcileResultRechecks([f.passed],f.context),[]);assert.deepEqual(readResultDraft(f.key).checks,{timing:true});}
});
test('request-time token protects edits made before the task id arrives',()=>{
 const f=fixture();writeResultDraft(f.key,{checks:{audio:true},editing:true});trackResultRecheck(f.token,f.job.id);assert.deepEqual(reconcileResultRechecks([f.passed],f.context),[]);assert.deepEqual(readResultDraft(f.key).checks,{audio:true});
});
test('duplicate polling cannot clear checks made after the first handled completion',()=>{
 const f=fixture();trackResultRecheck(f.token,f.job.id);assert.equal(reconcileResultRechecks([f.passed],f.context).length,1);writeResultDraft(f.key,{checks:{identity:true},editing:true});
 for(let i=0;i<3;i++)assert.deepEqual(reconcileResultRechecks([f.passed],f.context),[]);assert.deepEqual(readResultDraft(f.key).checks,{identity:true});
});
test('job scope, report scope and exact current evidence must all agree',()=>{
 const mutations=[{action:'composite_replacement'},{project_id:'other'},{segment_id:'B'},{input_candidate_id:'other'},{input_material_fingerprint:'other'},{result:{report:{ready:true,fingerprint:'other',segment_id:'A'}}},{result:{report:{ready:true,fingerprint:'candidate-version-a',segment_id:'B'}}}];
 for(const mutation of mutations){const f=fixture();trackResultRecheck(f.token,f.job.id);assert.deepEqual(reconcileResultRechecks([{...f.passed,...mutation}],f.context),[]);assert.equal(readResultDraft(f.key).checks.identity,true);}
 const f=fixture();trackResultRecheck(f.token,f.job.id);f.context.resultStates.segments.A.validation_current=false;assert.deepEqual(reconcileResultRechecks([f.passed],f.context),[]);assert.equal(readResultDraft(f.key).checks.identity,true);
});

test('preserving all six draft checks after technical failure never restores saved acceptance or export eligibility',async()=>{
 const {resultProjectState}=await import('../src/resultPresentation.js');
 const checks={identity:true,action:true,camera:true,timing:true,audio:true,occlusion:true},f=fixture(checks);
 f.context.resultStates.segments.A={...f.row,validation_current:false,review_current:false};
 f.context.resultStates.segments.B={...f.context.resultStates.segments.B,review_current:true};
 const serverEvidence=structuredClone(f.context);trackResultRecheck(f.token,f.job.id);
 assert.deepEqual(reconcileResultRechecks([{...f.passed,result:{report:{ready:false,fingerprint:f.row.candidate_fingerprint,segment_id:'A'}}}],f.context),[]);
 assert.deepEqual(readResultDraft(f.key).checks,checks);assert.deepEqual(f.context,serverEvidence);
 assert.equal(resultProjectState(f.project,f.context.resultStates,f.context.materialConfirmation).exportReady,false);
});
