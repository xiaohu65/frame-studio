import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ORIGINAL_DELIVERY_POLICY} from '../src/resultPresentation.js';
import {generationState,generationConfirmation,generationProblemRows,generationPrimaryAction,generationDraftTargets,generationIssueTarget,generationIssueTargets,generationSegmentState,generationNeedsConfiguration,generationUsageRows,retryableGenerationSegments,sourceFrameRate,readGenerationReviewDraft,writeGenerationReviewDraft,readGenerationFeeConfirmation,writeGenerationFeeConfirmation} from '../src/generationPresentation.js';

const plan={fingerprint:'version-1',ready:true,issues:[],wan_compatibility:{ready:true,issues:[]}};
const quote={id:'quote-1',fingerprint:'version-1',mode:'api',reserved_yuan:72};
const project={id:'project',source_meta:{fps_rational:'30/1'},replacement_plan:plan,preparation:{fingerprint:'version-1'}};
const generation={preparation:{status:'READY',plan},quote,runs:[]};
const requirements={materialConfirmed:true,feeConfirmed:true,configured:true};

test('payment requires current preparation, one material confirmation and its own fee authorization',()=>{
 const state=generationState(project,generation);
 assert.equal(generationConfirmation(state,requirements).allowed,true);
 for(const changed of [{feeConfirmed:false},{materialConfirmed:false},{configured:false},{dirty:true},{draftCount:1},{busy:true}])assert.equal(generationConfirmation(state,{...requirements,...changed}).allowed,false);
 for(const changed of [{quote:{...quote,fingerprint:'older'}},{quote:{...quote,mode:'export'}},{quote:{...quote,confirmed_run:'run'}},{runs:[{id:'run',quote_id:quote.id}]},{preparation:{status:'PREPARING',plan}},{quote:{...quote,reserved_yuan:null}}])assert.equal(generationConfirmation(generationState(project,{...generation,...changed}),requirements).allowed,false);
 assert.equal(generationConfirmation(generationState({...project,preparation:null},generation),requirements).allowed,false);
 assert.equal(generationConfirmation(generationState(project,{...generation,runs:[{id:'old',stale:true,segments:[{segment_id:'A',status:'UNKNOWN'}]}]}),requirements).allowed,false);
});

test('explicit A sample scope uses sample compatibility while strict final and configuration gates remain separate',()=>{
 const samplePlan={...plan,purpose:'candidate_sample',segment_ids:['A'],wan_compatibility:{ready:true,issues:[],sample_only:true,strict_final_ready:false}};
 const sampleQuote={...quote,purpose:'candidate_sample',segment_ids:['A'],scope_fingerprint:'scope-A',submission_ready:true};
 const sampleGeneration={...generation,preparation:{status:'READY',purpose:'candidate_sample',plan:samplePlan},quote:sampleQuote,
  compatibility:{ready:false,issues:[{code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message:'strict final only'}]},configuration:{ready:true,authentication_verified:false},
  requested_scope:{purpose:'candidate_sample',segment_ids:['A']}};
 const sampleProject={...project,source_meta:{fps_rational:'24000/1001'}};
 const state=generationState(sampleProject,sampleGeneration);
 assert.equal(state.sampleOnly,true);
 assert.equal(state.localAdaptationOnly,false);
 assert.equal(state.wanReady,true);
 assert.equal(generationConfirmation(state,requirements).allowed,true);
 for(const requested_scope of [{purpose:'candidate_sample',segment_ids:['A','B']},{purpose:'candidate_sample',segment_ids:['B']},{purpose:'strict_final',segment_ids:['A']}]){
  const changed=generationState(sampleProject,{...sampleGeneration,requested_scope});
  assert.equal(changed.quoteReady,false);
  assert.equal(generationConfirmation(changed,requirements).allowed,false);
 }
 const broken=generationState(sampleProject,{...sampleGeneration,configuration:{ready:false,issues:[{message:'缺少业务空间'}]}});
 assert.equal(generationConfirmation(broken,requirements).allowed,false);
 assert.match(generationConfirmation(broken,requirements).reason,/业务空间/);
 assert.equal(generationConfirmation(generationState(sampleProject,{...sampleGeneration,quote:{...sampleQuote,submission_ready:false}}),requirements).allowed,false);
 const complete={id:'sample-run',purpose:'candidate_sample',segments:[{segment_id:'A',asset:{id:'sample'},attached:true}]};
 assert.equal(generationState(sampleProject,{...sampleGeneration,current_run:complete,runs:[complete]}).resultsReady,false,'a saved sample cannot claim final candidate acceptance');
});

test('new original-audio materials need their own quote and acknowledgement even when base materials were confirmed',()=>{
 const samplePlan={...plan,purpose:'candidate_sample',segment_ids:['B']};
 const sampleQuote={...quote,purpose:'candidate_sample',segment_ids:['B'],scope_fingerprint:'audio-B',materials_fingerprint:'audio-materials',input_policy:{include_source_audio:true},submission_ready:true};
 const sampleGeneration={...generation,preparation:{status:'READY',plan:samplePlan},quote:sampleQuote,material_confirmation:{current:true,fingerprint:plan.fingerprint},
  requested_scope:{purpose:'candidate_sample',segment_ids:['B'],include_source_audio:true}};
 const state=generationState(project,sampleGeneration);
 assert.equal(state.quoteReady,true);
 assert.equal(generationConfirmation(state,{...requirements,materialConfirmed:false}).allowed,false);
 assert.equal(generationConfirmation(state,requirements).allowed,true);
 assert.equal(generationState(project,{...sampleGeneration,requested_scope:{...sampleGeneration.requested_scope,include_source_audio:false}}).quoteReady,false);
 const changed=generationState(project,{...sampleGeneration,quote:{...sampleQuote,input_materials_current:false}});
 assert.match(generationConfirmation(changed,requirements).reason,/输入文件已变化/);
 assert.equal(generationSegmentState({purpose:'candidate_sample',status:'COMPLETED',asset:{id:'raw'},attached:true,return_inspection:{timeline_adaptation:{status:'BLOCKED'}}}).label,'原始候选已接收 · 时间轴覆盖未通过');
});

test('changing quality away and back cannot reuse a quote after local preparation was invalidated',()=>{
 const initial={...project,settings:{resolution:'480P'}};
 assert.equal(generationState(initial,generation).quoteReady,true);
 const away={...initial,settings:{resolution:'720P'},preparation:null,replacement_plan:null};
 const awayGeneration={...generation,preparation:{status:'IDLE',stale:true,plan},quote:null,material_confirmation:{current:false,fingerprint:'720p-version'}};
 assert.equal(generationState(away,awayGeneration).prepared,false);
 assert.equal(generationState(away,awayGeneration).quoteReady,false);
 const back={...away,settings:{resolution:'480P'}};
 const restoredGeneration={...generation,material_confirmation:{current:false,fingerprint:plan.fingerprint}};
 for(const preparation of [null,{fingerprint:'older-local-preparation'}]){
  const state=generationState({...back,preparation},restoredGeneration);
  assert.equal(state.quoteCurrent,true,'the original quote may match the restored settings');
  assert.equal(state.prepared,false);
  assert.equal(state.quoteReady,false,'the missing preparation must still be rebuilt');
  assert.equal(generationPrimaryAction(state,{configured:true}).kind,'prepare');
  assert.equal(generationConfirmation(state,requirements).allowed,false);
 }
 const rebuilt=generationState({...back,preparation:{fingerprint:plan.fingerprint}},restoredGeneration);
 assert.equal(rebuilt.quoteReady,true);
 assert.equal(generationPrimaryAction(rebuilt,{configured:true}).kind,'confirm');
});

test('server compatibility restrictions govern payment and local adaptation keeps its own paid gate closed',()=>{
 assert.equal(sourceFrameRate({fps_rational:'30000/1001'}),30000/1001);
 const incompatible={...plan,wan_compatibility:{ready:false,issues:[{code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message:'帧率不兼容'}]}};
 assert.equal(generationConfirmation(generationState(project,{...generation,preparation:{status:'READY',plan:incompatible}}),requirements).allowed,false);
 const compatibility={ready:false,issues:[{code:'OUTPUT_SIZE_MISMATCH',blocking:true,message:'输出质量与原片不匹配',details:'本工具原尺寸合成要求'}]};
 assert.equal(generationConfirmation(generationState(project,{...generation,compatibility}),requirements).allowed,false);
 const idle=generationState(project,{preparation:{status:'IDLE'},compatibility});
 assert.equal(idle.issues[0].code,'OUTPUT_SIZE_MISMATCH');
 assert.equal(idle.issues[0].target.targetId,'delivery-spec');
 for(const source_meta of [{fps:24},{}])assert.equal(generationConfirmation(generationState({...project,source_meta},{...generation,compatibility:{ready:true,issues:[]}}),requirements).allowed,true,'the backend remains the compatibility authority for existing paths');
 const local=generationState({...project,source_meta:{fps_rational:'24000/1001'}},{...generation,compatibility:{ready:true,issues:[]}});
 assert.equal(local.localAdaptationOnly,true);
 assert.equal(generationConfirmation(local,requirements).allowed,false,'local adaptation cannot open payment even with a ready compatibility response');
 assert.match(generationConfirmation(local,requirements).reason,/暂未开放/);
 assert.match(generationConfirmation(local,{...requirements,dirty:true}).reason,/未保存/,'drafts take precedence without weakening the paid gate');
});

test('real issue codes offer separate people and scene choices without treating missing selections as source errors',()=>{
 const targets=generationIssueTargets({code:'NOTHING_SELECTED',message:'当前片段未选择需要替换的人物或场景'});
 assert.deepEqual(targets,[
  {kind:'navigate',step:1,targetId:'identity-mapping',label:'选择替换人物'},
  {kind:'navigate',step:2,targetId:'scene-settings',label:'设置替换场景'},
 ]);
 assert.deepEqual(generationIssueTarget({code:'NOTHING_SELECTED'}),targets[0]);
 for(const code of ['NO_SOURCE','SOURCE_MISSING','SOURCE_CHANGED','OUTPUT_SIZE_UNKNOWN','MODEL_INPUT_SIZE_UNSUPPORTED'])assert.equal(generationIssueTarget({code}).targetId,'source-import');
 for(const code of ['NO_SEGMENTS','MODEL_INPUT_DURATION_UNSUPPORTED'])assert.equal(generationIssueTarget({code}).targetId,'source-segments');
 const fps=generationIssueTarget({code:'MODEL_FPS_INCOMPATIBLE'});
 assert.equal(fps.compatibility,true);
 assert.equal(fps.targetId,'source-import');
 assert.notEqual(fps.targetId,'generation-settings');
 const localPlan={...plan,ready:false,issues:[{code:'NOTHING_SELECTED',blocking:true,message:'尚未选择'}]};
 const state=generationState(project,{preparation:{status:'NEEDS_ATTENTION',plan:localPlan}});
 assert.deepEqual(state.issues[0].targets,targets);
 assert.deepEqual(state.issues[0].target,targets[0]);
});

test('structured issue locations preserve the exact actor, role and related segment',()=>{
 const localPlan={mappings:[{actor_id:'actor-a',target_role_id:'role-a'}],references:[{reference:'图1',asset_id:'global-scene',kind:'scene'}],segment_plans:{B:{mappings:[{actor_id:'actor-a',target_role_id:'role-b'}],references:[{reference:'图1',asset_id:'person-b',kind:'person',role_id:'role-b'}]}}};
 for(const code of ['ACTOR_DESCRIPTION_MISSING','ACTOR_NOT_LOCATED','IDENTITY_UNCONFIRMED','OCCLUSION_REVIEW','SHOT_IDENTITY_UNCONFIRMED','DUPLICATE_TARGET','TARGET_NOT_BOUND','SERIES_BINDING_UNCONFIRMED']){
  const target=generationIssueTarget({code,actor_id:'actor-a',role_id:'role-b',segment_id:'B'},localPlan);
  assert.equal(target.step,1);
  assert.equal(target.actorId,'actor-a');
  assert.equal(target.segmentId,'B');
  assert.equal(target.roleId,undefined,'person correspondence and identity issues should open the person rather than the role manager');
 }
 const role=generationIssueTarget({code:'TARGET_IMAGE_MISSING',actor_id:'actor-a',segment_id:'B'},localPlan);
 assert.equal(role.roleId,'role-b','the related segment plan can resolve the saved role binding');
 assert.equal(role.actorId,'actor-a');
 const explicitRole=generationIssueTarget({code:'TARGET_NAME_MISSING',actor_id:'actor-a',role_id:'actual-role'},localPlan);
 assert.equal(explicitRole.roleId,'actual-role','explicit issue metadata takes priority over a plan lookup');
 assert.equal('segmentId' in explicitRole,false,'an issue without segment metadata must retain the current selection');
 const reference=generationIssueTarget({code:'REFERENCE_CHANGED',reference:'图1',segment_id:'B'},localPlan);
 assert.equal(reference.step,1);
 assert.equal(reference.roleId,'role-b');
 assert.equal(reference.assetId,'person-b');
 assert.equal(reference.segmentId,'B');
 const scene=generationIssueTarget({code:'REFERENCE_MISSING',reference:'图1'},localPlan);
 assert.equal(scene.targetId,'scene-settings');
 assert.equal(scene.assetId,'global-scene');
 assert.equal('segmentId' in scene,false,'a global reference must not guess a segment');
 const mask=generationIssueTarget({code:'PROTECTION_MASK_INVALID',asset_id:'mask-b',segment_id:'B'},localPlan);
 assert.equal(mask.targetId,'mask-panel');
 assert.equal(mask.maskKind,'protection_mask');
 assert.equal(mask.assetId,'mask-b');
 assert.equal(mask.segmentId,'B');
 assert.equal(generationIssueTarget({code:'EDIT_MASK_INVALID'}).targetId,'mask-panel');
 assert.equal(generationIssueTarget({code:'SCENE_REGION_UNCONFIRMED',segment_id:'B'}).sceneDetails,true);
 assert.equal(generationIssueTarget({code:'PROMPT_MISSING',segment_id:'B'}).segmentId,'B');
 assert.equal(generationIssueTarget({code:'PROMPT_MISSING'}).targetId,'prompt-editor');
 assert.equal(generationIssueTarget({code:'TOO_MANY_REFERENCES'}).step,1);
 assert.equal(generationIssueTarget({code:'REFERENCE_MISSING',reference_kind:'scene'}).targetId,'scene-settings');
 assert.equal(generationIssueTarget({code:'REFERENCE_MISSING',asset_id:'scene'},{references:[{asset_id:'scene',kind:'scene_image'}]}).targetId,'scene-settings');
 assert.equal(generationIssueTarget({code:'ASSET_MISSING',asset_id:'person'},{references:[{asset_id:'person',role_id:'role'}]}).roleId,'role');
 assert.equal(generationIssueTarget({code:'SHARED_PERSON_REFERENCE',role_id:'shared-role',asset_id:'image',reference_kind:'person'}).roleId,'shared-role');
});

test('missing reference metadata and unknown issues remain local instead of parsing translated messages',()=>{
 const localPlan={references:[{reference:'图1',kind:'scene'},{reference:'图10',kind:'person',role_id:'role-ten'}]};
 const first=generationIssueTarget({code:'REFERENCE_MISSING',message:'参考素材图1无法读取'},localPlan);
 const translated=generationIssueTarget({code:'REFERENCE_MISSING',message:'Reference 10 cannot be read'},localPlan);
 assert.equal(first.kind,'local');
 assert.equal(first.targetId,'generation-settings');
 assert.deepEqual(first,translated,'message wording must not determine reference ownership');
 assert.equal('segmentId' in first,false);
 const explicit=generationIssueTarget({code:'REFERENCE_MISSING',reference:'图10',message:'参考素材图1无法读取'},localPlan);
 assert.equal(explicit.roleId,'role-ten','only the structured reference label selects a reference');
 for(const code of ['INVALID_PLAN','UNRECOGNIZED_LOCAL_PROBLEM','NO_REPLACEMENT_SELECTED']){
  const target=generationIssueTarget({code,message:'原片文件、人物或场景尚未设置'});
  assert.equal(target.kind,'details');
  assert.equal(target.step,undefined);
 }
 const before=structuredClone(localPlan);
 generationIssueTargets({code:'REFERENCE_MISSING',reference:'图10'},localPlan);
 assert.deepEqual(localPlan,before);
});

test('only definitive failed segments can enter a fresh paid attempt',()=>{
 const statuses=['FAILED','REJECTED','UPLOAD_FAILED','NOT_SUBMITTED','CANCELED','UNKNOWN','SUBMITTING','RUNNING','DOWNLOAD_FAILED','SUCCEEDED'];
 const run={segments:statuses.map(status=>({segment_id:status,status}))};
 assert.deepEqual(retryableGenerationSegments(run),statuses.slice(0,5));
 assert.deepEqual(retryableGenerationSegments({...run,stale:true}),[]);
 assert.equal(generationSegmentState({status:'DOWNLOAD_FAILED',task_id:'original-task'}).retryable,false);
 assert.equal(generationSegmentState({status:'UNKNOWN'}).canAdopt,true);
 assert.equal(generationSegmentState({status:'UNKNOWN',task_id:'original-task'}).canAdopt,false);
});

test('platform success alone never claims locally attached or reviewed results',()=>{
 const run={id:'run',segments:[{segment_id:'A',status:'SUCCEEDED'}]};
 assert.equal(generationState(project,{runs:[run],current_run:'run'}).resultsReady,false);
 const downloaded={...run,segments:[{...run.segments[0],asset:{id:'asset'},attached:false}]};
 assert.equal(generationState(project,{runs:[downloaded]}).resultsReady,false);
 const attached={...downloaded,segments:[{...downloaded.segments[0],attached:true}]};
 assert.equal(generationState(project,{runs:[attached]}).resultsReady,true);
 assert.equal(generationState(project,{runs:[{...attached,stale:true}],current_run:{...attached,stale:true}}).resultsReady,false);
 assert.equal(generationSegmentState(attached.segments[0]).label,'结果已保存 · 待复核');
 const stale=generationState({...project,replacement_plan:null,preparation:null},{preparation:{status:'IDLE',stale:true,plan},quote:null,runs:[]});
 assert.equal(stale.plan,null);
 assert.equal(stale.fingerprint,'');
});

test('platform success without an available result or local asset stays unresolved and blocks another payment',()=>{
 for(const result_ready of [false,true]){
  const segment={segment_id:'A',status:'SUCCEEDED',task_id:'existing-platform-task',result_ready,asset:null,attached:false};
  const run={id:'existing-run',quote_id:'earlier-quote',fingerprint:plan.fingerprint,status:'ATTENTION',segments:[segment]};
  const state=generationState(project,{...generation,runs:[run],current_run:run});
  assert.equal(state.quoteReady,true,'a new quote alone cannot resolve the existing result');
  assert.equal(state.hasUnresolvedTasks,true);
  assert.equal(state.resultsReady,false);
  assert.equal(generationConfirmation(state,requirements).allowed,false);
  assert.match(generationConfirmation(state,requirements).reason,/原批次/);
  assert.equal(generationSegmentState(segment).label,'生成完成 · 等待保存结果');
  assert.equal(generationSegmentState(segment).retryable,false);
  assert.deepEqual(retryableGenerationSegments(run),[]);
 }
});

test('changed materials retain unfinished old runs and block new payment until the original task is resolved',()=>{
 const updatedPlan={...plan,fingerprint:'version-2'};
 const updatedProject={...project,replacement_plan:updatedPlan,preparation:{fingerprint:updatedPlan.fingerprint}};
 const updatedQuote={...quote,id:'quote-version-2',fingerprint:updatedPlan.fingerprint};
 for(const status of ['UNKNOWN','SUCCEEDED','DOWNLOAD_FAILED']){
  const oldRun={id:'old-run',quote_id:'old-quote',fingerprint:plan.fingerprint,stale:true,status:'ATTENTION',segments:[{segment_id:'A',status,task_id:'original-task',asset:null,attached:false}]};
  const current={preparation:{status:'READY',plan:updatedPlan},quote:updatedQuote,runs:[oldRun],current_run:null};
  const before=structuredClone(current);
  const state=generationState(updatedProject,current);
  assert.equal(state.fingerprint,updatedPlan.fingerprint);
  assert.equal(state.quoteReady,true);
  assert.equal(state.currentRun,null);
  assert.equal(state.hasCurrentRun,false);
  assert.equal(state.hasUnresolvedTasks,true);
  assert.equal(state.resultsReady,false);
  assert.equal(state.history.length,1);
  assert.equal(state.history[0],oldRun,'the original task remains available for query or adoption');
  assert.equal(generationConfirmation(state,requirements).allowed,false);
  assert.deepEqual(current,before);
  const resolved={...oldRun,status:'FAILED',segments:[{...oldRun.segments[0],status:'FAILED'}]};
  const recovered=generationState(updatedProject,{...current,runs:[resolved]});
  assert.equal(recovered.hasUnresolvedTasks,false);
  assert.equal(generationConfirmation(recovered,requirements).allowed,true,'a definitive failure no longer blocks a separately confirmed new quote');
  assert.equal(recovered.history[0],resolved);
 }
});

test('key existence is separate from authentication errors and helpers leave data untouched',()=>{
 assert.equal(generationNeedsConfiguration('请求未完成',{http_status:401}),true);
 assert.equal(generationNeedsConfiguration('百炼 HTTP 403：Forbidden'),true);
 assert.equal(generationNeedsConfiguration({category:'quota',message:'余额不足'}),true);
 assert.equal(generationNeedsConfiguration('请求被拒绝',{category:'permission'}),true);
 assert.equal(generationNeedsConfiguration('网络响应丢失'),false);
 const input=structuredClone({project,generation});
 const before=structuredClone(input);
 generationState(input.project,input.generation);
 assert.deepEqual(input,before);
});

test('usage shows only numeric documented evidence and never calculates an actual bill',()=>{
 assert.deepEqual(generationUsageRows({input_video_duration:2,output_video_duration:2.1,duration:4.1,charged_yuan:99,secret:'ignored'}),[
  {key:'input_video_duration',label:'输入视频用量',seconds:2},
  {key:'output_video_duration',label:'输出视频用量',seconds:2.1},
  {key:'duration',label:'接口返回时长',seconds:4.1},
 ]);
 assert.deepEqual(generationUsageRows({duration:'4',input_video_duration:-1,output_video_duration:NaN}),[]);
});

test('one material draft survives remounts while material versions and fee quotes stay isolated',()=>{
 writeGenerationReviewDraft('draft-project','fingerprint-a',{material_confirmed:true});
 assert.deepEqual(readGenerationReviewDraft('draft-project','fingerprint-a'),{material_confirmed:true});
 const read=readGenerationReviewDraft('draft-project','fingerprint-a');read.material_confirmed=false;
 assert.equal(readGenerationReviewDraft('draft-project','fingerprint-a').material_confirmed,true);
 assert.deepEqual(readGenerationReviewDraft('draft-project','fingerprint-b'),{});
 assert.deepEqual(readGenerationReviewDraft('different-project','fingerprint-a'),{});
 writeGenerationFeeConfirmation('draft-project','quote-a',true);
 assert.equal(readGenerationFeeConfirmation('draft-project','quote-a'),true);
 assert.equal(readGenerationFeeConfirmation('draft-project','quote-b'),false);
 assert.equal(readGenerationFeeConfirmation('different-project','quote-a'),false);
});

test('a valid server material confirmation is reused while new fee quotes still need authorization',()=>{
 for(const source of ['api','export','legacy_review']){
  const state=generationState(project,{...generation,material_confirmation:{current:true,fingerprint:plan.fingerprint,source,legacy:source==='legacy_review'}});
  assert.equal(state.materialCurrent,true);
  assert.equal(generationConfirmation(state,{...requirements,materialConfirmed:false}).allowed,true);
  assert.equal(generationConfirmation(state,{...requirements,materialConfirmed:false,feeConfirmed:false}).allowed,false);
 }
 const changed=generationState(project,{...generation,material_confirmation:{current:false,fingerprint:'new-current-materials'}});
 assert.equal(changed.materialCurrent,false);
 assert.equal(changed.quoteCurrent,false);
 assert.equal(generationConfirmation(changed,requirements).allowed,false);
});

test('draft navigation preserves only precise entries and adds only the currently edited prompt',()=>{
 const actor={label:'人物一的角色对应',step:1,targetId:'identity-mapping',actorId:'person-a',segmentId:'B'};
 const role={label:'蓝甲角色参考资料',step:1,targetId:'identity-mapping',roleId:'blue-role'};
 const input=[actor,actor,role,{label:'',step:0,targetId:'source-segments'}];
 const before=structuredClone(input);
 assert.deepEqual(generationDraftTargets(input),[actor,role]);
 assert.deepEqual(generationDraftTargets([]),[],'a count without metadata must not invent destinations');
 const prompt={id:'A',name:'A段'};
 const targets=generationDraftTargets(input,{dirty:true,segment:prompt});
 assert.equal(targets.length,3);
 assert.deepEqual(targets[2],{label:'A段提示词',step:3,targetId:'prompt-editor',segmentId:'A'});
 assert.deepEqual(input,before);
});


test('historical failure entries cannot offer another paid retry after a later successful batch',()=>{
 const prior={segments:[{segment_id:'A',status:'COMPLETED'},{segment_id:'B',status:'REJECTED'}]};
 const success={segments:[{segment_id:'B',status:'COMPLETED'}]};
 assert.deepEqual(retryableGenerationSegments(prior,[prior,success]),[]);
 assert.deepEqual(retryableGenerationSegments(prior,[prior,{...success,stale:true}]),['B']);
});

test('one main action prioritizes drafts, repairs, existing tasks, confirmation and then saved results',()=>{
 const state=generationState(project,generation);
 const draft={label:'A段提示词',step:3,targetId:'prompt-editor',segmentId:'A'};
 assert.equal(generationPrimaryAction(state,{configured:true,hasDrafts:true,draftTargets:[draft]}).kind,'draft');
 assert.equal(generationPrimaryAction(state,{configured:true,errors:['HTTP 401'],configurationErrorIndex:0}).kind,'config');
 const blocked={...state,blockers:[{code:'MASK_REQUIRED',blocking:true,message:'缺少替换范围'}]};
 const problems=[{id:'mask',urgent:true,localAdaptation:false}];
 assert.deepEqual(generationPrimaryAction(blocked,{configured:true,problems}),{kind:'problem',problemId:'mask'});
 const unresolved={...state,hasUnresolvedTasks:true,runs:[{id:'old',segments:[{segment_id:'B',status:'UNKNOWN'}]}]};
 assert.deepEqual(generationPrimaryAction(unresolved,{configured:true}),{kind:'adopt',runId:'old',segmentId:'B'});
 assert.equal(generationPrimaryAction(state,{configured:true}).kind,'confirm');
 assert.equal(generationPrimaryAction({...state,quoteReady:false,materialCurrent:true},{configured:true,hasCandidates:true}).kind,'results');
 assert.equal(generationPrimaryAction({...state,quoteReady:false,exported:true,packageAsset:{id:'zip'},materialCurrent:false},{configured:false}).kind,'download');
 assert.equal(generationPrimaryAction({...state,quoteReady:false,exported:true,packageAsset:{id:'zip'},materialCurrent:false},{configured:false,materialConfirmed:true}).kind,'export');
});

test('current definitive account failures prioritize their row repair until a new quote is ready',()=>{
 for(const category of ['authentication','permission','quota']){
  const failed={id:'failed-run',status:'FAILED',quote_id:quote.id,stale:false,segments:[{segment_id:'A',status:'UPLOAD_FAILED',error:'素材上传未完成',error_details:{category}},{segment_id:'B',status:'NOT_SUBMITTED'}]};
  const prior=generationState(project,{...generation,quote:{...quote,confirmed_run:failed.id},runs:[failed],current_run:failed});
  assert.deepEqual(generationPrimaryAction(prior,{configured:true}),{kind:'run-config',runId:failed.id,segmentId:'A'});
  assert.deepEqual(retryableGenerationSegments(failed,[failed]),['A','B'],'repair does not remove the explicit retry route');
  const next=generationState(project,{...generation,quote:{...quote,id:'new-quote'},runs:[failed],current_run:failed});
  assert.equal(next.quoteReady,true);
  assert.equal(generationPrimaryAction(next,{configured:true}).kind,'confirm','the old failure must not permanently override a fresh quote');
  const historical=generationState(project,{...generation,quote:null,runs:[{...failed,stale:true}],current_run:{...failed,stale:true}});
  assert.notEqual(generationPrimaryAction(historical,{configured:true}).kind,'run-config');
 }
});

test('23.976 has a short default hold with reasons retained once as secondary material',()=>{
 const message='当前原片24000/1001，当前模型输出为30fps。';
 const state=generationState({...project,source_meta:{fps_rational:'24000/1001'}},{...generation,compatibility:{ready:false,issues:[{code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message,details:[message,'仅本地适配验证可继续。']}]}});
 const rows=generationProblemRows(state,{configured:true});
 assert.equal(rows.length,1);
 assert.equal(rows[0].message,'此帧率暂未开放 API 付费生成');
 assert.deepEqual(rows[0].explanation,[message,'仅本地适配验证可继续。']);
 assert.equal(generationPrimaryAction(state,{configured:true,problems:rows}).kind,'local');
 assert.equal(generationConfirmation(state,requirements).allowed,false);
});

test('rendered generation flow shows one material choice, avoids empty payment/progress, and preserves exact draft recovery',async()=>{
 const require=createRequire(import.meta.url);
 const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/components/GenerationStage.jsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,loader:{'.css':'empty'},external:['react','react-dom','lucide-react','radix-ui']});
 const module={exports:{}};
 new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
 const Stage=module.exports.default;
 const sample={...project,source:{id:'source'},settings:{model:'wan3.0-video',resolution:'1080P'},segments:[{id:'A',name:'A段',start:0,end:2}],source_actors:[],assets:[]};
 const render=overrides=>renderToStaticMarkup(React.createElement(Stage,{project:sample,segment:sample.segments[0],config:{configured:true},generation:{...generation,quote:{...quote,count:1,input_seconds:2,estimate_yuan:4.8}},onPatch:()=>{},onAction:()=>{},onGeneration:async()=>{},onStep:()=>{},onConfig:()=>{},onHistory:()=>{},useDraft:value=>[value,()=>{},false,()=>{},()=>{}],...overrides}));
 const api=render({});
 const fallback=render({project:{...sample,settings:{model:'wan3.0-video',resolution:null}}});
 assert.ok(fallback.includes('Wan 3.0 · 交付档位待选'));
 assert.doesNotMatch(fallback,/确认并付费生成/);
 assert.match(api,/Wan 3.0 · 1080P/);
 assert.match(api,/查看本批交付规格/);
 const explicit720=render({project:{...sample,settings:{model:'wan3.0-video',resolution:'720P'}}});
 assert.match(explicit720,/Wan 3.0 · 720P/);
 const deliverySpec={resolution:'720P',ratio:'16:9',width:1280,height:720,fps:'30',frame_count:301,duration_seconds:301/30,
  segments:[{segment_id:'A',first_source_frame:1,last_source_frame:301,frame_count:301,duration_seconds:301/30}],
  duration_warnings:[{segment_id:'A',message:'A 段要求完整覆盖 301 帧。'}]};
 const frozenGeneration={...generation,quote:{...quote,delivery_spec:deliverySpec,materials_fingerprint:'inputs-version'}};
 const frozen=render({project:{...sample,settings:{model:'wan3.0-video',resolution:'720P'}},generation:frozenGeneration});
 assert.match(frozen,/本次冻结的交付规格/);
 assert.match(frozen,/1280 × 720/);
 assert.match(frozen,/F1–F301/);
 assert.match(frozen,/10\.033333 秒/);
 assert.match(frozen,/智能时长覆盖风险/);
 assert.match(frozen,/A 段要求完整覆盖 301 帧/);
 assert.match(frozen,/inputs-version/);
 assert.doesNotMatch(render({generation:frozenGeneration}),/本次冻结的交付规格|确认并付费生成/);
 assert.equal((api.match(/role="checkbox"/g)||[]).length,2,'one material choice plus one fee authorization');
 assert.equal((api.match(/我已确认本次人物对应、参考图及替换与保护范围。/g)||[]).length,1);
 assert.equal((api.match(/¥72\.00/g)||[]).length,1,'the reserve is shown only in the payment confirmation');
 assert.ok(api.includes('API Key 已配置'));
 assert.ok(api.includes('密钥记录编号，不等于 Workspace ID'));
 assert.ok(api.includes('现有 Key 留空沿用，无需重新输入'));
 assert.ok(api.includes('/compatible-mode/v1'));
 assert.ok(api.includes('保留原声 · 无水印'));
 assert.equal((api.match(/>处理记录<\/button>/g)||[]).length,1,'the generation sidebar owns one log entry');
 assert.ok(!api.includes('generation-progress'),'no task means no empty progress module');
 const missingPreparation=render({project:{...sample,preparation:null,replacement_plan:null}});
 assert.ok(missingPreparation.includes('准备生成</button>'));
 assert.ok(!missingPreparation.includes('确认并付费生成'),'a matching old quote must not hide the preparation action');
 const reused=render({generation:{...generation,material_confirmation:{current:true,fingerprint:plan.fingerprint}}});
 assert.equal((reused.match(/role="checkbox"/g)||[]).length,1,'a valid material record is reused without another checkbox');
 assert.equal((reused.match(/当前材料已确认/g)||[]).length,1);
 assert.ok(reused.includes('id="generation-material-confirmation"'));
 const initialExport=render({config:{configured:false},project:{...sample,replacement_plan:null,preparation:null},generation:{preparation:{status:'IDLE'},material_confirmation:{current:false,fingerprint:plan.fingerprint}}});
 assert.equal((initialExport.match(/role="checkbox"/g)||[]).length,1);
 assert.ok(!/<button(?=[^>]*role="checkbox")(?=[^>]*\sdisabled(?:=|(?=\s|>)))[^>]*/.test(initialExport),'unchecked materials can be confirmed with the first export');
 assert.ok(!initialExport.includes('generation-quote'));
 assert.ok(!initialExport.includes('generation-progress'));
 const pendingExport=render({config:{configured:false},generation:{preparation:{status:'EXPORTED',plan,package_asset:{id:'zip',url:'/qa.zip'}},material_confirmation:{current:false,fingerprint:plan.fingerprint}}});
 assert.ok(pendingExport.includes('下载生成包（材料待确认）'));
 assert.ok(!pendingExport.includes('generation-quote'));
 assert.equal((pendingExport.match(/role="checkbox"/g)||[]).length,1);
 const dirty=render({draftCount:1,dirtyDraftTargets:[{label:'只修改了人物一',step:1,targetId:'identity-mapping',actorId:'person-a'}],generation:{...generation,preparation:{status:'READY',stale:true},material_confirmation:{current:true,fingerprint:plan.fingerprint}}});
 assert.ok(dirty.includes('先处理未保存修改'));
 assert.ok(dirty.includes('只修改了人物一'));
 assert.equal((dirty.match(/现有准备仅对应已保存版本/g)||[]).length,1);
 assert.ok(!dirty.includes('当前材料已确认'));
 assert.ok(!dirty.includes('generation-quote'));
 assert.ok(!dirty.includes('查看场景与范围修改'));
 const existing=render({project:{...sample,segments:[{...sample.segments[0],generated:{id:'manual-result'}}]},generation:{...generation,current_run:{id:'failed',status:'FAILED',segments:[{segment_id:'A',status:'FAILED'}]},runs:[]}});
 assert.ok(existing.includes('仅准备已有结果的验收材料</button>'),'material confirmation remains reachable without paying again');
 assert.equal((existing.match(/role="checkbox"/g)||[]).length,2,'the existing-result recovery does not duplicate the material checkbox');
 const local=render({project:{...sample,source_meta:{fps_rational:'24000/1001'},segments:[{...sample.segments[0],generated:{id:'local-adapted'}}]}});
 assert.ok(local.includes('此帧率暂未开放 API 付费生成'));
 assert.ok(local.includes('检查原始候选</button>'));
 assert.ok(local.includes('导出本地生成包'));
 assert.ok(!local.includes('确认并付费生成'));
 assert.ok(!local.includes('准备生成</button>'));
 assert.equal((local.match(/role="checkbox"/g)||[]).length,1,'only material confirmation remains for local verification');
 const samplePlan={...plan,purpose:'candidate_sample',segment_ids:['A'],wan_compatibility:{ready:true,issues:[]}};
 const sampleGeneration={...generation,preparation:{status:'READY',purpose:'candidate_sample',plan:samplePlan},
  quote:{...quote,purpose:'candidate_sample',segment_ids:['A'],scope_fingerprint:'scope-A',count:1,input_seconds:250*1001/24000,estimate_yuan:12.5125,reserved_yuan:18.6,submission_ready:true},
  compatibility:{ready:false,issues:[{code:'MODEL_FPS_INCOMPATIBLE',blocking:true,message:'strict only'}]},configuration:{ready:true}};
 const sampleHtml=render({project:{...sample,settings:{model:'wan3.0-video',resolution:'720P'},source_meta:{width:1920,height:1080,fps_rational:'24000/1001'}},generation:sampleGeneration});
 assert.ok(sampleHtml.includes('确认并付费生成'));
 assert.ok(sampleHtml.includes('A · 候选小样'));
 assert.ok(sampleHtml.includes('原片 1920×1080 保持原尺寸与原时间轴'));
 assert.ok(!sampleHtml.includes('此帧率暂未开放 API 付费生成'));
 assert.equal((sampleHtml.match(/¥18\.60/g)||[]).length,1);
 assert.match(sampleHtml,/Wan 3.0 · 720P/);
 const misconfiguredSample=render({generation:{...sampleGeneration,configuration:{ready:false,issues:[{message:'请补齐业务空间'}]},quote:{...sampleGeneration.quote,submission_ready:false}}});
 assert.ok(misconfiguredSample.includes('请补齐业务空间'));
 assert.ok(misconfiguredSample.includes('按当前配置重新准备报价'));
 const audioGeneration={...sampleGeneration,material_confirmation:{current:true,fingerprint:plan.fingerprint},quote:{...sampleGeneration.quote,
  input_policy:{include_source_audio:true},materials_fingerprint:'audio-version',material_manifest:[{segment_id:'A',media:[{type:'reference_video'},{type:'reference_image'},{type:'reference_audio'}]}],
  input_contract:{source_audio:{A:{duration_seconds:2,video_first_in_audio_seconds:0,original_audio_video_offset_rational:'-41/1000',submission_prompt_note:'已按参考视频归零对齐'}}}}};
 const audioHtml=render({generation:audioGeneration});
 assert.ok(audioHtml.includes('本次会上传原声参考'));
 assert.ok(audioHtml.includes('1段原声'));
 assert.ok(audioHtml.includes('原声参考仍需本次确认'));
 assert.ok(audioHtml.includes('上传 WAV 按归零参考视频的实际源PTS对齐'));
 assert.equal((audioHtml.match(/role="checkbox"/g)||[]).length,2,'valid base materials do not waive audio material acknowledgement');
 const raw={id:'raw312',sha256:'raw-sha',url:'/raw312.mp4'};
 const receipt={status:'RAW_CANDIDATE_RECEIVED',actual:{frame_count:312,decoded_duration_seconds:10.4},timeline_adaptation:{status:'BLOCKED',ready:false,error:'候选末端覆盖不足'}};
 const audition={material_fingerprint:plan.fingerprint,candidate_asset_id:raw.id,candidate_sha256:raw.sha256,visual_coverage_seconds:10.4,
  video_first_in_audio_seconds:.041,timeline_adaptation_status:'BLOCKED',audio_asset:{id:'audition',url:'/audition.wav'}};
 const sampleSegment={...sample.segments[0],sample_candidate:raw,sample_provenance:{purpose:'candidate_sample',fingerprint:plan.fingerprint,asset_id:raw.id,return_inspection:receipt,audition}};
 const blockedSample=render({project:{...sample,segments:[sampleSegment]},segment:sampleSegment,generation:{...sampleGeneration,quote:{...sampleGeneration.quote,confirmed_run:'sample-run'},runs:[{id:'sample-run',purpose:'candidate_sample',segments:[{segment_id:'A',purpose:'candidate_sample',status:'COMPLETED',asset:raw,attached:true,return_inspection:receipt}]}]}});
 assert.ok(blockedSample.includes('云端已完成 · 原始候选已接收'));
 assert.ok(blockedSample.includes('312 帧，文件画面时长 10.4 秒'));
 assert.ok(blockedSample.includes('时间轴覆盖：不通过'));
 assert.ok(blockedSample.includes('原声试听 · 画面覆盖'));
 assert.ok(blockedSample.includes('<audio'));
 assert.ok(blockedSample.includes('/audition.wav'));
 assert.ok(blockedSample.includes('候选末端覆盖不足'));
 assert.match(blockedSample,/<video\b[^>]*src="\/raw312\.mp4"[^>]*controls=""/);
 const scopedPreview={id:'scoped-preview',sha256:'scope-sha',url:'/scoped-preview.mp4'};
 const audioAsset={id:'audio',sha256:'audio-sha',url:'/original.wav'};
 const scopeLocal={input_current:true,run_id:'paid-B',candidate_asset_id:raw.id,candidate_sha256:raw.sha256,binding_fingerprint:'new-mask-binding',
  local_binding_current:true,preview_current:true,adaptation_current:true,preview_asset_id:scopedPreview.id,preview_sha256:scopedPreview.sha256,
  audition_current:true,audition_asset_id:audioAsset.id,audition_sha256:audioAsset.sha256,material_confirmation_current:false};
 const scopedSegment={...sampleSegment,sample_preview:scopedPreview,sample_preview_report:{material_fingerprint:'new-mask-version',local_binding_fingerprint:scopeLocal.binding_fingerprint,
  candidate_asset_id:raw.id,preview_asset_id:scopedPreview.id,preview_sha256:scopedPreview.sha256,timeline_size_validation:{ready:true}},
  sample_provenance:{...sampleSegment.sample_provenance,fingerprint:'paid-version',run_id:'paid-B',return_inspection:{timeline_adaptation:{ready:true,status:'PLAN_PASS'}},
   candidate_resolution:'720P',audition:{...audition,audio_asset:audioAsset}}};
 const localRun={id:'paid-B',purpose:'candidate_sample',stale:true,local_current:true,status:'COMPLETED',
  call_evidence:{succeeded:true,model:'wan3.0-video',region_label:'北京',scope:'THIS_RUN_ONLY'},
  segments:[{segment_id:'A',purpose:'candidate_sample',status:'COMPLETED',asset:raw,attached:true,sample_state:scopeLocal,return_inspection:scopedSegment.sample_provenance.return_inspection}]};
 const scopedGeneration={...sampleGeneration,preparation:{status:'IDLE',purpose:'candidate_sample',stale:true},quote:null,
  material_confirmation:{current:false,fingerprint:'new-mask-version'},sample_states:{A:scopeLocal},runs:[localRun],current_run:localRun};
 const scopedProps={project:{...sample,source:{id:'source',url:'/original.mp4'},segments:[scopedSegment]},segment:scopedSegment,generation:scopedGeneration};
 const scopedHtml=render(scopedProps);
 assert.match(scopedHtml,/<video\b[^>]*src="\/raw312\.mp4"[^>]*controls=""/);
 assert.match(scopedHtml,/<a\b[^>]*href="\/raw312\.mp4"[^>]*download=""/);
 assert.doesNotMatch(scopedHtml,/\/scoped-preview\.mp4|\/original\.mp4|制作当前范围合成预览|使用已有候选更新本地合成|确认本次本地范围/);
 assert.ok(scopedHtml.includes('本批次调用已成功 · wan3.0-video · 北京'));
 assert.ok(!scopedHtml.includes('尚未验证本次调用'));
 assert.ok(scopedHtml.includes('原始候选已接收 · 时间轴覆盖通过'));
 assert.equal((scopedHtml.match(/<audio\b/g)||[]).length,1,'the audition uses the same raw video and one audio element');
 assert.equal((scopedHtml.match(/<video\b/g)||[]).length,1);
 const staleHtml=render({...scopedProps,generation:{...scopedGeneration,sample_states:{A:{...scopeLocal,input_current:false}}}});
 assert.ok(staleHtml.includes('下载历史原始候选'));
 assert.doesNotMatch(staleHtml,/<video\b|<audio\b|\/scoped-preview\.mp4/);
 const packages=render({project:{...sample,assets:[{id:'old-zip',kind:'package',name:'历史生成包.zip',url:'/old.zip'}]}});
 assert.ok(packages.includes('历史生成包，请核对材料版本'));
 assert.ok(packages.includes('href="/old.zip"'));
 assert.ok(!packages.includes('导出生成包</button>'),'configured API mode preserves old downloads without offering another export');
 const visibleMarkup=html=>{
  const hidden=[];let result='';
  for(const part of html.split(/(<\/?(?:details|div)\b[^>]*>)/)){
   if(/^<(?:details|div)\b/.test(part)){
    const closedDetails=part.startsWith('<details')&&!/[\s]open(?:=|[\s>])/.test(part);
    const closedDisclosure=part.includes('data-generation-content')&&part.includes('data-state="closed"');
    const collapsed=hidden.at(-1)||closedDetails||closedDisclosure;
    hidden.push(collapsed);if(!collapsed)result+=part;
   }else if(/^<\/(?:details|div)\b/.test(part)){
    const collapsed=hidden.pop();if(!collapsed)result+=part;
   }else if(!hidden.at(-1))result+=part;
  }
  return result;
 };
 const primaryCount=html=>(visibleMarkup(html).match(/class="[^"]*\bgeneration-primary-action\b[^"]*"/g)||[]).length;
 for(const [name,html] of Object.entries({api,reused,initialExport,pendingExport,dirty,existing,local,packages}))assert.ok(primaryCount(html)<=1,name+' has at most one visible primary action');
 const blockedPlan={...plan,ready:false,issues:[{code:'MASK_REQUIRED',blocking:true,message:'请补齐A段替换范围',details:'上传逐帧黑白范围。'}]};
 const blocked=render({generation:{...generation,preparation:{status:'READY',plan:blockedPlan}}});
 assert.equal((blocked.match(/请补齐A段替换范围/g)||[]).length,1,'details do not repeat the problem list');
 assert.ok(!blocked.includes('确认并付费生成'));
 assert.ok(!blocked.includes('准备生成</button>'));
 assert.equal(primaryCount(blocked),1);
 const nothingSelected=render({generation:{preparation:{status:'NEEDS_ATTENTION',plan:{...plan,ready:false,issues:[{code:'NOTHING_SELECTED',blocking:true,message:'当前片段未选择需要替换的人物或场景'}]}}}});
 assert.ok(nothingSelected.includes('选择替换人物</button>'));
 assert.ok(nothingSelected.includes('设置替换场景</button>'));
 assert.ok(!nothingSelected.includes('查看原片与文件</button>'));
 assert.equal(primaryCount(nothingSelected),1,'two valid routes retain one primary action');
 const unknownProblem=render({generation:{preparation:{status:'NEEDS_ATTENTION',plan:{...plan,ready:false,issues:[{code:'INVALID_PLAN',blocking:true,message:'当前材料需要检查'}]}}}});
 assert.ok(unknownProblem.includes('查看问题详情</button>'));
 assert.ok(unknownProblem.includes('id="generation-problem-INVALID_PLAN-0"'));
 assert.ok(unknownProblem.includes('问题编号：INVALID_PLAN'));
 assert.ok(!unknownProblem.includes('查看原片与文件</button>'));
 assert.equal((unknownProblem.match(/当前材料需要检查/g)||[]).length,1);
 assert.equal(primaryCount(unknownProblem),1);
 const keyError=render({generation:{...generation,preparation:{status:'FAILED',error:{category:'authentication',message:'百炼 HTTP 401：InvalidApiKey'}}}});
 assert.ok(keyError.includes('核对 API 设置与账户权限'));
 assert.ok(keyError.includes('修复后重新准备</button>'),'saving configuration can be followed by another local preparation');
 assert.ok(!keyError.includes('确认并付费生成'));
 assert.equal(primaryCount(keyError),1);
 const completed={id:'complete',quote_id:quote.id,status:'COMPLETED',segments:[{segment_id:'A',status:'COMPLETED',asset:{id:'result'},attached:true}]};
 const saved=render({project:{...sample,segments:[{...sample.segments[0],generated:{id:'result'}}]},generation:{...generation,quote:{...quote,confirmed_run:'complete'},material_confirmation:{current:true,fingerprint:plan.fingerprint},runs:[completed],current_run:completed,project_result_count:1,result_states:{delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:{candidate_current:true,candidate_asset_id:'result',validation_current:false}}}}});
 assert.equal((saved.match(/查看并验收结果/g)||[]).length,1);
 assert.ok(!saved.includes('复核已有结果'));
 assert.equal(primaryCount(saved),1);
 assert.ok(saved.indexOf('generation-progress')<saved.indexOf('generation-results'));
 assert.ok(saved.indexOf('generation-results')<saved.indexOf('id="generation-settings"'));
 const invalidKeyRun={id:'invalid-key-run',status:'FAILED',quote_id:quote.id,error:null,segments:[{segment_id:'A',name:'A段',status:'UPLOAD_FAILED',error:'素材上传未完成',error_details:{category:'authentication'}},{segment_id:'B',name:'B段',status:'NOT_SUBMITTED'}]};
 const invalidKeyGeneration={...generation,quote:{...quote,confirmed_run:invalidKeyRun.id},material_confirmation:{current:true,fingerprint:plan.fingerprint},runs:[invalidKeyRun],current_run:invalidKeyRun};
 const invalidKey=render({generation:invalidKeyGeneration});
 assert.match(visibleMarkup(invalidKey),/<button[^>]*class="[^"]*\bgeneration-primary-action\b[^"]*"[^>]*>核对 API 设置与账户权限<\/button>/);
 assert.equal(primaryCount(invalidKey),1);
 assert.equal((invalidKey.match(/素材上传未完成/g)||[]).length,1,'repair stays in its row without a duplicate top error');
 assert.ok(invalidKey.includes('API Key 已配置'));
 const retryButton=[...invalidKey.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].find(match=>match[2].includes('重试失败片段'));
 assert.ok(retryButton);
 assert.match(retryButton[1],/\bdata-variant="outline"/,'retry remains a secondary outlined action');
 assert.ok(!/\sdisabled(?:=|(?=\s|>))/.test(retryButton[1]),'a repaired key can still enter a new locally prepared attempt');
 const newQuoteAfterFailure=render({generation:{...invalidKeyGeneration,quote:{...quote,id:'fresh-after-key-repair'}}});
 assert.ok(newQuoteAfterFailure.includes('确认并付费生成'));
 assert.equal(primaryCount(newQuoteAfterFailure),1);
 assert.ok(!/<button[^>]*class="[^"]*\bgeneration-primary-action\b[^"]*"[^>]*>核对 API 设置与账户权限<\/button>/.test(visibleMarkup(newQuoteAfterFailure)));
});
