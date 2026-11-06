import {localAdaptationSource,sampleResultStatus} from './candidateAdaptationPresentation.js';

const RETRYABLE=new Set(['FAILED','REJECTED','UPLOAD_FAILED','NOT_SUBMITTED','CANCELED']);
const ACTIVE=new Set(['QUEUED','PREPARING','UPLOADING','SUBMITTING','SUBMITTED','PENDING','RUNNING','PROCESSING','DOWNLOADING']);
const AMBIGUOUS=new Set(['UNKNOWN','SUBMITTING']);
const REVIEW_DRAFTS=new Map();
const FEE_CONFIRMATIONS=new Map();

export function readGenerationReviewDraft(projectId,fingerprint){return fingerprint?{...(REVIEW_DRAFTS.get(`${projectId}:${fingerprint}`)||{})}:{};}
export function writeGenerationReviewDraft(projectId,fingerprint,checks){if(fingerprint)REVIEW_DRAFTS.set(`${projectId}:${fingerprint}`,{...checks});}
export function readGenerationFeeConfirmation(projectId,quoteId){return !!quoteId&&FEE_CONFIRMATIONS.get(`${projectId}:${quoteId}`)===true;}
export function writeGenerationFeeConfirmation(projectId,quoteId,confirmed){if(quoteId)FEE_CONFIRMATIONS.set(`${projectId}:${quoteId}`,confirmed===true);}

export function generationDraftTargets(targets=[],{dirty=false,segment}={}){
 const seen=new Set(),result=[];
 for(const target of targets||[]){
  if(!target||typeof target.label!=='string'||!target.label.trim()||!Number.isInteger(target.step)||target.step<0||target.step>4||typeof target.targetId!=='string'||!target.targetId)continue;
  const key=[target.step,target.targetId,target.segmentId||'',target.actorId||'',target.roleId||''].join('|');
  if(seen.has(key))continue;
  seen.add(key);result.push({...target});
 }
 if(dirty&&segment&&!result.some(target=>target.step===3&&target.targetId==='prompt-editor'&&target.segmentId===segment.id))result.push({label:`${segment.name||segment.id}提示词`,step:3,targetId:'prompt-editor',segmentId:segment.id});
 return result;
}

export function sourceFrameRate(meta={}){
 const value=meta.fps_rational||meta.fps;
 if(typeof value==='string'&&value.includes('/')){const [numerator,denominator]=value.split('/').map(Number);return denominator?numerator/denominator:NaN;}
 return Number(value);
}

export function generationIssueTargets(issue={},plan){
 const code=issue.code||'',segmentId=issue.segment_id||issue.segmentId;
 const segmentPlan=segmentId?plan?.segment_plans?.[segmentId]:null;
 const references=segmentPlan?.references||plan?.references||[];
 const referenceInfo=issue.reference&&typeof issue.reference==='object'?issue.reference:issue.reference_info||{};
 const assetId=issue.asset_id||referenceInfo.asset_id;
 const referenceLabel=typeof issue.reference==='string'?issue.reference:issue.reference_label||referenceInfo.reference;
 const reference=assetId?references.find(item=>item.asset_id===assetId):referenceLabel?references.find(item=>item.reference===referenceLabel):null;
 const actorId=issue.actor_id||issue.actorId||referenceInfo.actor_id;
 const mappings=segmentPlan?.mappings||plan?.mappings||[];
 const mapping=actorId?mappings.find(item=>item.actor_id===actorId):null;
 const roleId=issue.role_id||issue.roleId||referenceInfo.role_id||reference?.role_id;
 const referenceKind=issue.reference_kind||referenceInfo.reference_kind||referenceInfo.kind||reference?.kind||issue.kind;
 const resolvedAssetId=assetId||reference?.asset_id,resolvedReference=referenceLabel||reference?.reference;
 const context={...(segmentId?{segmentId}:{}),...(actorId?{actorId}:{}),...(resolvedAssetId?{assetId:resolvedAssetId}:{}),...(resolvedReference?{reference:resolvedReference}:{}),...(referenceKind?{referenceKind}:{})};
 const local=(targetId,label)=>({kind:'local',targetId,label,...context});
 const navigate=(step,targetId,label,extra={})=>({kind:'navigate',step,targetId,label,...context,...extra});
 if(code==='NOTHING_SELECTED')return [navigate(1,'identity-mapping','选择替换人物'),navigate(2,'scene-settings','设置替换场景')];
 if(['NO_SOURCE','SOURCE_MISSING','SOURCE_CHANGED','OUTPUT_SIZE_UNKNOWN','MODEL_INPUT_SIZE_UNSUPPORTED'].includes(code))return [navigate(0,'source-import','查看原片与文件')];
 if(['NO_SEGMENTS','MODEL_INPUT_DURATION_UNSUPPORTED'].includes(code))return [navigate(0,'source-segments','调整分段范围')];
 if(/^MODEL_FPS(?:_|$)/.test(code))return [navigate(0,'source-import','查看原片帧率',{compatibility:true})];
 if(/(?:^|_)PROMPT(?:_|$)/.test(code))return [local('prompt-editor','编辑本段提示词')];
 if(/^OUTPUT_SIZE_|RESOLUTION|ASPECT|^DELIVERY_/.test(code))return [navigate(0,'delivery-spec','核对本批交付规格')];
 if(/^SCENE_/.test(code)||/^scene(?:_|$)/i.test(referenceKind||''))return [navigate(2,'scene-settings','处理场景与参考图',{sceneDetails:code!=='SCENE_DESCRIPTION_MISSING'})];
 if(/^(?:EDIT_MASK_|PROTECTION_MASK_|MASK_|POLYGON_|REGION_)|^LOCATORS_ONLY$/.test(code))return [navigate(2,'mask-panel','处理替换与保护范围',/^PROTECTION_MASK_/.test(code)?{maskKind:'protection_mask'}:/^(?:EDIT_MASK_|MASK_)/.test(code)?{maskKind:'edit_mask'}:{})];
 if(/^TARGET_(?:NAME|IMAGE)_MISSING$/.test(code))return [navigate(1,'identity-mapping','处理角色与参考图',roleId||mapping?.target_role_id?{roleId:roleId||mapping.target_role_id}:{})];
 if(/^ACTOR_|^IDENTITY_|^TARGET_|^DUPLICATE_TARGET$|^OCCLUSION_|^SHOT_|^SERIES_/.test(code))return [navigate(1,'identity-mapping','处理人物对应与位置')];
 if(code==='TOO_MANY_REFERENCES')return [navigate(1,'identity-mapping','管理人物参考图')];
 if(roleId||/^person(?:_|$)/i.test(referenceKind||''))return [navigate(1,'identity-mapping','处理角色与参考图',roleId?{roleId}:{})];
 if(actorId)return [navigate(1,'identity-mapping','处理人物对应与位置')];
 if(/REFERENCE/.test(code))return [local('generation-settings','查看本段参考材料')];
 if(/^MODEL_|^WAN_|^OUTPUT_/.test(code))return [local('generation-model-settings','查看生成设置')];
 return [{kind:'details',label:'查看问题详情',...context}];
}

export function generationIssueTarget(issue,plan){return generationIssueTargets(issue,plan)[0];}

export function generationErrorText(error){
 if(typeof error==='string')return error;
 if(!error)return '';
 return String(error.message||error.error||error.code||'本次操作未完成，请查看任务记录。');
}

export function generationNeedsConfiguration(error,details){
 const status=Number(details?.http_status||details?.status_code||error?.http_status);
 return status===401||status===403||/\b(?:401|403)\b|AUTH|UNAUTHORIZED|FORBIDDEN|PERMISSION|QUOTA|API.?KEY|WORKSPACE|ENDPOINT|REGION|业务空间|生成地址|密钥|鉴权|权限|额度|配额|余额|欠费/i.test([generationErrorText(error),details?.category,error?.category,details?.code].filter(Boolean).join(' '));
}

export function generationUsageRows(usage={}){
 usage=usage||{};
 return [['input_video_duration','输入视频用量'],['output_video_duration','输出视频用量'],['duration','接口返回时长']].filter(([key])=>typeof usage[key]==='number'&&Number.isFinite(usage[key])&&usage[key]>=0).map(([key,label])=>({key,label,seconds:usage[key]}));
}

export function generationProblemRows(state,{configured=true}={}){
 const input=[...state.issues];
 if(state.localAdaptationOnly&&!input.some(issue=>/FPS/.test(issue.code||'')))input.push({code:'LOCAL_ADAPTATION_PAID_HOLD',message:'此帧率暂未开放 API 付费生成',blocking:true,target:{kind:'navigate',step:0,targetId:'source-import',label:'查看原片帧率'}});
 return input.map((issue,index)=>{
  const localAdaptation=state.localAdaptationOnly&&(/FPS/.test(issue.code||'')||issue.code==='LOCAL_ADAPTATION_PAID_HOLD');
  const message=localAdaptation?'此帧率暂未开放 API 付费生成':issue.message;
  const details=Array.isArray(issue.details)?issue.details:typeof issue.details==='string'?[issue.details]:[];
  const explanation=[...new Set([...(localAdaptation&&issue.message!==message?[issue.message]:[]),...details].filter(value=>typeof value==='string'&&value.trim()&&value!==message))];
  const local=(state.plan?.issues||[]).some(item=>item.code===issue.code&&item.message===issue.message);
  return {...issue,id:(issue.code||'issue')+'-'+index,message,explanation,localAdaptation,urgent:!!issue.blocking&&(configured||local)};
 });
}

export function generationPrimaryAction(state,{configured=false,hasDrafts=false,draftTargets=[],errors=[],configurationErrorIndex=-1,problems=[],hasCandidates=false,materialConfirmed=false}={}){
 if(hasDrafts)return {kind:'draft',target:draftTargets[0]||null};
 if(state.preparing)return {kind:'preparing'};
 if(configurationErrorIndex>=0)return {kind:'config',errorIndex:configurationErrorIndex};
 const runs=[state.currentRun,...state.runs].filter(Boolean);
 if(state.hasUnresolvedTasks){
  if(!configured)return {kind:'config',errorIndex:-1};
  for(const run of runs){const segment=(run.segments||[]).find(item=>AMBIGUOUS.has(item.status)&&!item.task_id);if(segment)return {kind:'adopt',runId:run.id,segmentId:segment.segment_id};}
  const run=runs.find(item=>(item.segments||[]).some(segment=>['QUEUED','UPLOADING','SUBMITTING','PENDING','RUNNING','SUCCEEDED','DOWNLOADING','UNKNOWN','DOWNLOAD_FAILED'].includes(segment.status)));
  return {kind:'refresh',runId:run?.id};
 }
 const canReview=hasCandidates&&state.materialCurrent;
 if(state.localAdaptationOnly&&canReview)return {kind:'results'};
 const blocker=problems.find(issue=>issue.urgent&&!issue.localAdaptation);
 if(blocker)return {kind:'problem',problemId:blocker.id};
 if(configured&&state.localAdaptationOnly)return {kind:'local'};
 if(errors.length)return {kind:configured?'prepare':'export'};
 if(configured&&state.quoteReady)return {kind:'confirm'};
 if(state.sampleOnly&&(state.currentRun?.segments||[]).some(segment=>segment.sample_state?.input_current))return {kind:'none'};
 const failedRun=state.currentRun&&!state.currentRun.stale?state.currentRun:null;
 const configurationFailure=(failedRun?.segments||[]).find(segment=>RETRYABLE.has(segment.status)&&generationNeedsConfiguration(segment.error,segment.error_details));
 if(configured&&configurationFailure)return {kind:'run-config',runId:failedRun.id,segmentId:configurationFailure.segment_id};
 if(canReview)return {kind:'results'};
 if(!configured){
  if(state.exported&&state.packageAsset&&!state.preparation.stale&&!(materialConfirmed&&!state.materialCurrent))return {kind:'download'};
  return {kind:'export'};
 }
 const retry=runs.find(run=>retryableGenerationSegments(run,state.runs).length>0);
 if(retry)return {kind:'retry',runId:retry.id};
 if(state.hasCurrentRun||state.quoteConsumed)return {kind:'none'};
 return {kind:'prepare'};
}

export function generationSegmentState(segment){
 const status=segment.status||'UNKNOWN';
 if(segment.asset&&segment.purpose==='candidate_sample'){
  const presentation=sampleResultStatus(segment.sample_state||{},segment.return_inspection);
  return {...presentation,active:false,retryable:false,canAdopt:false};
 }
 if(segment.asset&&segment.attached)return {label:'结果已保存 · 待复核',tone:'success',active:false,retryable:false,canAdopt:false};
 const labels={QUEUED:'等待处理',PREPARING:'正在准备',UPLOADING:'正在上传材料',SUBMITTING:'提交状态待确认',SUBMITTED:'已提交',PENDING:'平台排队中',RUNNING:'平台生成中',PROCESSING:'平台生成中',SUCCEEDED:'生成完成 · 等待保存结果',COMPLETED:'生成完成 · 等待保存结果',DOWNLOADING:'正在保存结果',DOWNLOADED:'结果已下载 · 待接入',ATTACHED:'结果已接入',DOWNLOAD_FAILED:'结果保存失败 · 可查询原任务重试',UNKNOWN:'任务状态待确认',FAILED:'生成失败',REJECTED:'请求被拒绝',UPLOAD_FAILED:'材料上传失败 · 未提交生成',NOT_SUBMITTED:'尚未提交生成',CANCELED:'已停止 · 本段未付费提交',CANCELLED:'任务已取消'};
 return {label:segment.asset?'结果已保存 · 未归入当前片段':labels[status]||status,tone:RETRYABLE.has(status)||status==='DOWNLOAD_FAILED'?'error':AMBIGUOUS.has(status)?'warning':ACTIVE.has(status)?'progress':'neutral',active:ACTIVE.has(status),retryable:RETRYABLE.has(status),canAdopt:AMBIGUOUS.has(status)&&!segment.task_id};
}

export function generationState(project,generation={}){
 generation=generation||{};
 const preparation=generation.preparation||{status:'IDLE'};
 const quote=generation.quote||preparation.quote||null;
 const requested=generation.requested_scope;
 const purpose=requested?.purpose||quote?.purpose||preparation.purpose||'strict_final';
 const sampleOnly=purpose==='candidate_sample';
 const plan=(!preparation.stale&&preparation.plan)||project.replacement_plan||project.preparation?.plan||null;
 const materialConfirmation=generation.material_confirmation||{};
 const fingerprint=materialConfirmation.fingerprint||plan?.fingerprint||quote?.fingerprint||'';
 const materialCurrent=materialConfirmation.current===true&&!!fingerprint;
 const prepared=!!project.preparation&&!!fingerprint&&project.preparation.fingerprint===fingerprint;
 const quotedDelivery=quote?.delivery_spec;
 const deliveryCurrent=project.settings?.resolution!==null&&(!quotedDelivery||quotedDelivery.resolution===project.settings?.resolution&&
  (quotedDelivery.revision??0)===(project.delivery_revision??0));
 const quoteCurrent=!!quote?.id&&!!quote.fingerprint&&quote.fingerprint===fingerprint&&deliveryCurrent;
 const runs=Array.isArray(generation.runs)?generation.runs:[];
 const quoteConsumed=!!quote?.confirmed_run||!!quote?.id&&runs.some(run=>run.quote_id===quote.id);
 const scopeCurrent=!requested||(quote?.purpose||'strict_final')===purpose&&JSON.stringify(quote?.segment_ids||project.segments?.map(item=>item.id))===JSON.stringify(requested.segment_ids)&&
  (requested.include_source_audio===undefined||!!quote?.input_policy?.include_source_audio===requested.include_source_audio);
 const quoteReady=prepared&&preparation.status==='READY'&&quoteCurrent&&scopeCurrent&&quote.mode!=='export'&&!quoteConsumed;
 const compatibility=sampleOnly?(plan?.purpose==='candidate_sample'?plan.wan_compatibility:generation.sample_compatibility)||null:generation.compatibility||plan?.wan_compatibility||null;
 const rawIssues=[...(plan?.issues||[]),...(compatibility?.issues||[])];
 const unique=new Set();
 const issues=rawIssues.filter(issue=>{const key=[issue.code,issue.segment_id,issue.actor_id,issue.message].join('|');if(unique.has(key))return false;unique.add(key);return true;}).map(issue=>{const targets=generationIssueTargets(issue,plan);return {...issue,targets,target:targets[0]};});
 const blockers=issues.filter(issue=>issue.blocking);
 const supplied=generation.current_run;
 const currentRun=typeof supplied==='string'?runs.find(run=>run.id===supplied)||null:supplied&&typeof supplied==='object'?supplied:runs.find(run=>!run.stale)||null;
 const currentSegments=currentRun?.segments||[];
 const currentRunStale=!!currentRun?.stale;
 const hasCurrentRun=!!currentRun&&(!currentRunStale||currentRun.local_current===true)&&currentSegments.length>0;
 const resultsReady=hasCurrentRun&&currentRun.purpose!=='candidate_sample'&&currentSegments.every(segment=>!!segment.asset&&segment.attached===true);
 const hasUnresolvedTasks=runs.some(run=>(run.segments||[]).some(segment=>['QUEUED','UPLOADING','SUBMITTING','PENDING','RUNNING','SUCCEEDED','DOWNLOADING','UNKNOWN','DOWNLOAD_FAILED'].includes(segment.status)));
 return {purpose,sampleOnly,scopeCurrent,configuration:generation.configuration||quote?.configuration||null,localAdaptationOnly:localAdaptationSource(project.source_meta)&&!sampleOnly,preparation,quote,plan,fingerprint,materialConfirmation,materialCurrent,compatibility,quoteCurrent,quoteReady,quoteConsumed,issues,blockers,preparing:preparation.status==='PREPARING',exported:preparation.status==='EXPORTED',packageAsset:preparation.package_asset||null,localReady:plan?.ready===true,prepared,wanReady:compatibility?.ready!==false,runs,currentRun,hasCurrentRun,resultsReady,hasUnresolvedTasks,history:runs.filter(run=>run.id!==currentRun?.id),projectResultCount:Number.isFinite(generation.project_result_count)?generation.project_result_count:0};
}

export function generationConfirmation(state,{materialConfirmed=false,feeConfirmed=false,configured=false,dirty=false,draftCount=0,busy=false}={}){
 let reason='';
 if(dirty||Number(draftCount)>0)reason='请先保存或放弃未保存修改。';
 else if(state.localAdaptationOnly)reason='此帧率暂未开放付费生成。';
 else if(busy||state.preparing)reason='请等待当前操作完成。';
 else if(!state.quoteReady)reason='请先按当前材料完成准备，取得新的费用确认。';
 else if(state.quote?.input_materials_current===false)reason='本次原声或其他输入文件已变化，请重新准备材料和报价。';
 else if(state.sampleOnly&&(!state.quote?.scope_fingerprint||state.quote.purpose!=='candidate_sample'||!state.quote.segment_ids?.length))reason='候选小样须取得绑定用途和选段范围的新报价。';
 else if(!state.localReady||!state.prepared)reason='当前版本材料尚未准备并检查通过。';
 else if(!state.wanReady||state.blockers.length)reason='请先处理上方待办。';
 else if(state.hasUnresolvedTasks)reason='已有未完成、状态待确认或待保存结果的任务，请先处理原批次。';
 else if(!configured)reason='请先配置 API Key。';
 else if(state.configuration?.ready===false||state.quote?.submission_ready===false)reason=state.configuration?.issues?.[0]?.message||'请先核对业务空间、地域和生成地址，再重新准备费用。';
 else if((state.quote?.input_policy?.include_source_audio||!state.materialCurrent)&&!materialConfirmed)reason='请确认本次材料与原声参考上传清单。';
 else if(!feeConfirmed)reason='请确认本次费用预留与付费生成。';
 else if(!Number.isFinite(state.quote?.reserved_yuan)||state.quote.reserved_yuan<=0)reason='本次费用尚未完整确认，请重新准备。';
 return {allowed:!reason,reason};
}

export function retryableGenerationSegments(run,runs=[]){
 const completed=new Set(runs.filter(item=>!item.stale).flatMap(item=>(item.segments||[]).filter(segment=>segment.status==='COMPLETED').map(segment=>segment.segment_id)));
 return run?.stale?[]:(run?.segments||[]).filter(segment=>RETRYABLE.has(segment.status)&&!completed.has(segment.segment_id)).map(segment=>segment.segment_id);
}
