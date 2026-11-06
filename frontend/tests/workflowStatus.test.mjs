import test from 'node:test';
import assert from 'node:assert/strict';
import {generationWorkflowStatus,resultWorkflowStatus} from '../src/workflowStatus.js';
import {ORIGINAL_DELIVERY_POLICY} from '../src/resultPresentation.js';

test('received sample with failed timeline coverage never presents itself as final results or a new paid retry',()=>{
 const plan={fingerprint:'sample-materials',purpose:'candidate_sample',ready:true,issues:[],wan_compatibility:{ready:true,issues:[]}};
 const run={id:'sample-run',purpose:'candidate_sample',fingerprint:plan.fingerprint,status:'COMPLETED',segments:[{segment_id:'A',purpose:'candidate_sample',status:'COMPLETED',asset:{id:'raw'},attached:true,return_inspection:{timeline_adaptation:{status:'BLOCKED',ready:false}}}]};
 const project={id:'sample',source:{id:'source'},source_meta:{fps_rational:'24000/1001'},segments:[{id:'A',start:0,end:10.427083333}],preparation:{fingerprint:plan.fingerprint},replacement_plan:plan};
 const data={preparation:{status:'READY',purpose:'candidate_sample',plan},quote:{id:'q',purpose:'candidate_sample',fingerprint:plan.fingerprint,confirmed_run:run.id},
  runs:[run],current_run:run,material_confirmation:{current:false,fingerprint:plan.fingerprint}};
 const result=generationWorkflowStatus(project,data,{configured:true});
 assert.equal(result.code,'sample-blocked');
 assert.match(result.label,/时间轴覆盖未通过/);
 assert.match(result.reason,/原声试听只覆盖已有画面范围/);
 assert.equal(result.state.resultsReady,false);
 assert.notEqual(result.primary.kind,'confirm');
 assert.notEqual(result.primary.kind,'retry');
});

test('navigation describes the current raw sample and ignores old local derivation flags',()=>{
 const inspection={timeline_adaptation:{ready:true,status:'PLAN_PASS'}};
 const local={input_current:true,local_binding_current:true,preview_current:true,adaptation_current:true};
 const segment={segment_id:'B',purpose:'candidate_sample',status:'COMPLETED',asset:{id:'raw'},attached:true,sample_state:local,return_inspection:inspection};
 const run={id:'paid-B',purpose:'candidate_sample',stale:true,local_current:true,status:'COMPLETED',segments:[segment]};
 const p={id:'p',source:{id:'src'},source_meta:{fps_rational:'24000/1001'},segments:[{id:'B'}]};
 const data={preparation:{status:'IDLE',purpose:'candidate_sample',stale:true},quote:null,runs:[run],current_run:run,
  sample_compatibility:{ready:true,issues:[]},material_confirmation:{fingerprint:'new-mask-version',current:false}};
 const state=generationWorkflowStatus(p,data,{configured:true,selectedSegmentId:'B'});
 assert.equal(state.code,'sample-inspected');assert.equal(state.label,'原始候选已接收 · 时间轴覆盖通过');
 assert.equal(state.primary.kind,'none');assert.equal(state.state.resultsReady,false);
 const reuse={...segment,sample_state:{...local,local_binding_current:false,preview_current:false,reuse_available:true}};
 const pending=generationWorkflowStatus(p,{...data,runs:[{...run,segments:[reuse]}],current_run:{...run,segments:[reuse]}},{configured:true});
 assert.equal(pending.code,state.code);assert.equal(pending.primary.kind,'none');
});
import {workflowRecordLabels} from '../src/workflowPresentation.js';

const plan={fingerprint:'current',ready:true,issues:[],wan_compatibility:{ready:true,issues:[]}};
const project={id:'status-project',source:{id:'source'},source_meta:{fps_rational:'30/1'},segments:[{id:'A',name:'A片段',generated:{id:'candidate-a'}},{id:'B',name:'B片段'}],
 source_actors:[{id:'person'}],scene:{mode:'preserve'},replacement_plan:plan,preparation:{fingerprint:'current'}};
const quote={id:'quote',fingerprint:'current',mode:'api',reserved_yuan:12};
const generation={preparation:{status:'READY',plan},quote,runs:[],compatibility:plan.wan_compatibility,
 material_confirmation:{fingerprint:'current',current:false},result_states:{segments:{A:{candidate_current:true,validation_current:false},B:{candidate_current:false}}}};

test('unloaded state never becomes ready from saved preparation, candidates or final files',()=>{
 const withFiles={...project,final:{id:'old-final'}};
 for(const unavailable of [null,undefined,{}]){
  for(const helper of [generationWorkflowStatus,resultWorkflowStatus]){
   const result=helper(withFiles,unavailable,{configured:true});
   assert.equal(result.code,'loading');assert.equal(result.label,'正在读取状态');
   assert.equal(result.loading,true);
  }
  assert.deepEqual(workflowRecordLabels(withFiles,unavailable).slice(3),['正在读取状态','正在读取状态']);
 }
 assert.equal(generationWorkflowStatus(project,generation,{configured:true,loading:true}).primary.kind,'loading');
 assert.equal(resultWorkflowStatus(project,generation,{loading:true}).code,'loading');
});

test('the first three workflow records remain unchanged by generation or result statuses',()=>{
 assert.deepEqual(workflowRecordLabels(project,null).slice(0,3),['2 段已保存','已记录 1 人','保留原场景']);
 assert.deepEqual(workflowRecordLabels({...project,source:null,segments:[],source_actors:[],scene:{mode:'replace'}},null).slice(0,3),['等待导入原片','等待指定人物','替换场景外观']);
});

test('material problems and local-only fps restrictions stay separate',()=>{
 const local={...project,source_meta:{fps_rational:'24000/1001'}};
 const fps={code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message:'原片仅开放本地候选适配'};
 const localGeneration={...generation,compatibility:{ready:false,issues:[fps]}};
 const onlyApi=generationWorkflowStatus(local,localGeneration,{configured:true});
 assert.equal(onlyApi.code,'local-only');assert.equal(onlyApi.primary.kind,'local');
 assert.match(onlyApi.label,/API .*未开放/);assert.doesNotMatch(onlyApi.label,/材料.*待处理|待生成|已准备/);
 const mask={code:'EDIT_MASK_MISSING',blocking:true,message:'当前段缺少替换范围',segment_id:'A'};
 const blockedPlan={...plan,ready:false,issues:[mask]};
 const blocked=generationWorkflowStatus({...local,replacement_plan:blockedPlan},{...localGeneration,preparation:{status:'NEEDS_ATTENTION',plan:blockedPlan}},{configured:true});
 assert.equal(blocked.code,'materials-blocked');assert.equal(blocked.reason,mask.message);
 const noKey=generationWorkflowStatus({...local,replacement_plan:blockedPlan},{...localGeneration,preparation:{status:'NEEDS_ATTENTION',plan:blockedPlan}},{configured:false});
 assert.equal(noKey.code,'materials-blocked','local material blockers also matter to export');
});

test('current compatibility defeats a saved local-preparation record without inventing readiness',()=>{
 const size={code:'OUTPUT_SIZE_TIER_MISMATCH',blocking:true,message:'输出质量与原片不匹配'};
 const result=generationWorkflowStatus(project,{...generation,compatibility:{ready:false,issues:[size]}},{configured:true});
 assert.equal(result.code,'materials-blocked');assert.equal(result.reason,size.message);
 assert.notEqual(result.primary.kind,'confirm');assert.doesNotMatch(result.label,/已准备.*待生成/);
});

test('stale, missing and wrong-version preparation never reuse a current-looking quote',()=>{
 for(const preparation of [null,{fingerprint:'older'}]){
  const result=generationWorkflowStatus({...project,preparation},generation,{configured:true});
  assert.equal(result.code,'preparation-invalid');assert.equal(result.label,'准备需要更新');
  assert.notEqual(result.primary.kind,'confirm');
 }
 const stale=generationWorkflowStatus(project,{...generation,preparation:{status:'IDLE',stale:true,plan:null},quote:null},{configured:true});
 assert.equal(stale.code,'preparation-stale');assert.match(stale.label,/失效/);
 const oldMask={code:'EDIT_MASK_MISSING',blocking:true,message:'旧版本曾缺遮罩'};
 const oldPlan={...plan,fingerprint:'older',ready:false,issues:[oldMask]};
 const changed=generationWorkflowStatus({...project,replacement_plan:oldPlan,preparation:null},{...generation,preparation:{status:'IDLE',stale:true,plan:oldPlan},quote:null},{configured:true});
 assert.equal(changed.code,'preparation-stale');assert.equal(changed.problems.length,0,'stale plan issues do not become current repairs');
});

test('local files, an API quote and an exported platform package have distinct statuses',()=>{
 const local=generationWorkflowStatus(project,{...generation,preparation:{status:'IDLE',plan},quote:null},{configured:true});
 assert.equal(local.code,'local-prepared');assert.equal(local.label,'本地已准备 · 尚未可生成');
 const ready=generationWorkflowStatus(project,generation,{configured:true});
 assert.equal(ready.code,'quote');assert.equal(ready.primary.kind,'confirm');
 const expired=generationWorkflowStatus(project,{...generation,quote:{...quote,fingerprint:'older'}},{configured:true});
 assert.equal(expired.code,'quote-invalid');assert.notEqual(expired.primary.kind,'confirm');
 const noKey=generationWorkflowStatus(project,generation,{configured:false});
 assert.equal(noKey.label,'本地已准备 · 待导出生成包');assert.equal(noKey.primary.kind,'export');
 const exported=generationWorkflowStatus(project,{...generation,quote:null,preparation:{status:'EXPORTED',plan,package_asset:{id:'package'}}},{configured:false});
 assert.equal(exported.code,'exported');assert.match(exported.label,/材料待确认/);assert.equal(exported.primary.kind,'download');
});

test('drafts, preparation, unresolved tasks and account repair keep their actual next actions',()=>{
 const draft=generationWorkflowStatus(project,generation,{configured:true,hasDrafts:true});
 assert.equal(draft.code,'draft');assert.equal(draft.primary.kind,'draft');
 const preparing=generationWorkflowStatus(project,{...generation,preparation:{status:'PREPARING',plan,message:'正在本机检查'}},{configured:true});
 assert.equal(preparing.code,'preparing');assert.equal(preparing.primary.kind,'preparing');
 for(const [status,code] of [['UNKNOWN','task-unknown'],['SUBMITTING','task-unknown'],['DOWNLOAD_FAILED','task-download'],['SUCCEEDED','task-saving'],['RUNNING','task-running']]){
  const run={id:'old-task',stale:true,segments:[{segment_id:'A',status}]};
  const result=generationWorkflowStatus(project,{...generation,runs:[run],current_run:null},{configured:true});
  assert.equal(result.code,code);assert.notEqual(result.primary.kind,'confirm');
 }
 const auth=generationWorkflowStatus(project,generation,{configured:true,errors:['HTTP 401 Unauthorized']});
 assert.equal(auth.code,'configuration');assert.equal(auth.primary.kind,'config');
});

test('result status follows the selected segment and current server evidence',()=>{
 const row={candidate_current:true,candidate_asset_id:'candidate-a',candidate_fingerprint:'version-a',validation_current:true,review_current:true};
 const records={delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:row,B:{candidate_current:false,validation_current:false,review_current:false}}};
 const data={...generation,result_states:records,material_confirmation:{current:true,fingerprint:'current'}};
 const a=resultWorkflowStatus(project,data,{selectedSegmentId:'A'});
 assert.equal(a.code,'reviewed');assert.equal(a.segment.id,'A');assert.equal(a.segmentState.reviewed,true);
 const b=resultWorkflowStatus(project,data,{selectedSegmentId:'B'});
 assert.equal(b.code,'missing');assert.equal(b.label,'当前段待导入');
 assert.equal(workflowRecordLabels(project,data,{configured:true,selectedSegmentId:'B'})[4],b.label);
 const missingRecord=resultWorkflowStatus(project,{...data,result_states:{segments:{A:records.segments.A}}},{selectedSegmentId:'B'});
 assert.equal(missingRecord.code,'loading');
 for(const [record,code] of [
  [{candidate_current:false},'candidate-stale'],
  [{...row,validation_current:false},'validation'],
  [{...row,composite_current:false,review_current:false},'review'],
 ])assert.equal(resultWorkflowStatus(project,{...data,result_states:{...records,segments:{A:record}}},{selectedSegmentId:'A'}).code,code);
});

test('history is readable while export readiness requires all current reviews and material confirmation',()=>{
 const one={...project,segments:[project.segments[0]],final:{id:'old-final'}};
 const row={candidate_current:true,candidate_asset_id:'candidate-a',candidate_fingerprint:'version-a',validation_current:true,review_current:true};
 const data={...generation,result_states:{delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:row},final_current:false},material_confirmation:{current:true,fingerprint:'current'}};
 const ready=resultWorkflowStatus(one,data);
 assert.equal(ready.code,'export');assert.equal(ready.projectState.exportReady,true);assert.equal(ready.historicalFinal,true);
 const unconfirmed=resultWorkflowStatus(one,{...data,material_confirmation:{current:false,fingerprint:'current'}});
 assert.equal(unconfirmed.code,'material-confirmation');assert.equal(unconfirmed.projectState.exportReady,false);
 const final=resultWorkflowStatus(one,{...data,result_states:{...data.result_states,final_current:true}});
 assert.equal(final.code,'final');assert.equal(final.label,'成片已导出');assert.equal(final.historicalFinal,false);
 const stale=resultWorkflowStatus({...one,segments:[{...one.segments[0],candidate_stale:true}]},data);
 assert.equal(stale.code,'candidate-stale');assert.equal(stale.projectState.exportReady,false);
 assert.notEqual(stale.code,'final');
 assert.equal(resultWorkflowStatus({...one,segments:[]},{...data,result_states:{segments:{},final_current:false}}).code,'segments');
});

test('summaries preserve source records and draft flags do not rewrite saved acceptance',()=>{
 const data={...generation,result_states:{delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:{candidate_current:true,candidate_asset_id:'candidate-a',candidate_fingerprint:'version-a',validation_current:true,review_current:true}}},material_confirmation:{current:true,fingerprint:'current'}};
 const frozenProject=structuredClone({...project,segments:[project.segments[0]]});
 const frozenData=structuredClone(data),before=structuredClone({project:frozenProject,generation:frozenData});
 function freeze(value){if(value&&typeof value==='object'){Object.freeze(value);Object.values(value).forEach(freeze);}return value;}
 freeze(frozenProject);freeze(frozenData);
 generationWorkflowStatus(frozenProject,frozenData,{configured:true});
 const result=resultWorkflowStatus(frozenProject,frozenData,{hasDrafts:true});
 assert.equal(result.code,'draft');assert.equal(result.segmentState.reviewed,true);assert.equal(result.projectState.exportReady,true);
 assert.deepEqual({project:frozenProject,generation:frozenData},before);
});

test('an attached successful task does not override invalid actual candidate evidence',()=>{
 const run={id:'completed',segments:[{segment_id:'A',status:'COMPLETED',asset:{id:'candidate-a'},attached:true}]};
 const data={...generation,current_run:run,runs:[run],material_confirmation:{current:true,fingerprint:'current'},
  result_states:{segments:{A:{candidate_current:false},B:{candidate_current:false}}}};
 const result=generationWorkflowStatus(project,data,{configured:true});
 assert.equal(result.code,'results-stale');assert.match(result.label,/待更新/);
});
test('missing API compatibility evidence cannot present a fee confirmation as ready',()=>{
 const data={...generation,compatibility:null,preparation:{status:'READY',plan:{...plan,wan_compatibility:null}}};
 const result=generationWorkflowStatus({...project,replacement_plan:null},data,{configured:true});
 assert.equal(result.code,'compatibility');assert.notEqual(result.primary.kind,'confirm');
});

test('main and sidebar both wait for missing candidate evidence and both flag stale files',()=>{
 const missing={...generation,preparation:{status:'IDLE',plan},quote:null,result_states:null,material_confirmation:{fingerprint:'current',current:true}};
 assert.equal(generationWorkflowStatus(project,missing,{configured:true}).code,'loading');
 assert.equal(generationWorkflowStatus(project,missing,{configured:true,hasCandidates:true}).code,'loading');
 const stale={...missing,result_states:{segments:{A:{candidate_current:false},B:{candidate_current:false}}}};
 assert.equal(generationWorkflowStatus(project,stale,{configured:true}).code,'results-stale');
 assert.equal(generationWorkflowStatus(project,stale,{configured:true,hasCandidates:true}).label,generationWorkflowStatus(project,stale,{configured:true}).label);
});
