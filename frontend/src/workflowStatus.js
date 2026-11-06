import {generationState,generationProblemRows,generationPrimaryAction,generationSegmentState,generationErrorText,generationNeedsConfiguration} from './generationPresentation.js';
import {resultSegmentState,resultProjectState} from './resultPresentation.js';

// Presentation only: these summaries do not authorize generation or acceptance.
function object(value){return !!value&&typeof value==='object'&&!Array.isArray(value);}
function generationLoaded(generation,options){
 return options.loading!==true&&object(generation)&&
  ['preparation','material_confirmation','compatibility','runs','result_states'].some(key=>Object.hasOwn(generation,key));
}
function status(code,label,heading,reason,details){return {code,label,heading,reason:reason||'',loading:code==='loading',...details};}
function currentCandidates(project,generation){
 return (project.segments||[]).filter(segment=>resultSegmentState(segment,generation?.result_states).candidateCurrent);
}

export function generationWorkflowStatus(project={},generation,options={}){
 project=project||{};
 const state=generationState(project,generation);
 const planCurrent=!!state.fingerprint&&state.plan?.fingerprint===state.fingerprint;
 const configured=options.configured===true,hasDrafts=options.hasDrafts===true;
 const problems=generationProblemRows(state,{configured}).filter(problem=>planCurrent||problem.localAdaptation||
  object(generation?.compatibility)&&(generation.compatibility.issues||[]).some(issue=>issue.code===problem.code&&issue.message===problem.message));
 const errors=Array.isArray(options.errors)?options.errors:
  !state.preparation.stale&&generationErrorText(state.preparation.error)?[generationErrorText(state.preparation.error)]:[];
 const configurationErrorIndex=Number.isInteger(options.configurationErrorIndex)?options.configurationErrorIndex:
  errors.findIndex(error=>generationNeedsConfiguration(error));
 const candidates=currentCandidates(project,generation);
 const candidateFiles=(project.segments||[]).filter(segment=>segment.generated);
 const staleCandidates=candidateFiles.filter(segment=>!resultSegmentState(segment,generation?.result_states).candidateCurrent);
 const primary=generationPrimaryAction(state,{configured,hasDrafts,draftTargets:options.draftTargets||[],errors,
  configurationErrorIndex,problems,hasCandidates:options.hasCandidates??(candidateFiles.length>0),
  materialConfirmed:options.materialConfirmed===true});
 const details={state,problems,primary,planCurrent,candidateCount:candidates.length};
 const preparationDetails={...details,primary:primary.kind==='confirm'?{kind:configured?'prepare':'export'}:primary};
 if(!generationLoaded(generation,options))return status('loading','正在读取状态','正在读取状态','尚未取得当前项目的状态。',{...details,primary:{kind:'loading'}});
 if(hasDrafts)return status('draft','有未保存修改','先处理未保存修改','准备与生成只使用已保存的内容。',details);
 if(state.preparing)return status('preparing','正在本机准备','正在准备',state.preparation.message,details);

 const runs=[state.currentRun,...state.runs].filter((run,index,list)=>run&&list.findIndex(item=>item?.id===run.id)===index);
 const unresolved=runs.flatMap(run=>(run.segments||[]).map(segment=>({run,segment,presentation:generationSegmentState(segment)})));
 if(state.hasUnresolvedTasks){
  const uncertain=unresolved.find(item=>['UNKNOWN','SUBMITTING'].includes(item.segment.status));
  if(uncertain)return status('task-unknown','任务状态待确认','恢复已有任务',uncertain.presentation.label,details);
  const download=unresolved.find(item=>item.segment.status==='DOWNLOAD_FAILED');
  if(download)return status('task-download','结果保存待恢复','恢复已有结果',download.presentation.label,details);
  const saved=unresolved.find(item=>item.segment.status==='SUCCEEDED');
  if(saved)return status('task-saving','生成完成 · 等待保存','保存生成结果',saved.presentation.label,details);
  return status('task-running','正在生成与保存','生成进度与结果','沿已有任务查询，不重新提交。',details);
 }
 if(primary.kind==='run-config'||primary.kind==='config')return status('configuration','API 设置待处理','核对 API 设置',errors[configurationErrorIndex]||'已有任务或本次请求需要核对连接与账户权限。',details);
 if(!project.source)return status('source','等待导入原片','先导入原片','',preparationDetails);
 if(!(project.segments||[]).length)return status('segments','等待保存分段','先保存分段','',preparationDetails);

 const sampleRows=state.currentRun?.purpose==='candidate_sample'&&(!state.currentRun.stale||state.currentRun.local_current)?(state.currentRun.segments||[]).filter(segment=>segment.asset):[];
 if(sampleRows.length&&!state.quoteReady){
  const selected=sampleRows.find(segment=>segment.segment_id===options.selectedSegmentId)||sampleRows[0];
  const presentation=generationSegmentState(selected),blocked=selected.return_inspection?.timeline_adaptation?.status==='BLOCKED';
  return status(presentation.code||'sample-received',presentation.label,'云端原始小样',
   blocked?'原声试听只覆盖已有画面范围。':'可播放、下载原始候选，或按原声试听。',details);
 }
 const localBlocker=problems.find(problem=>problem.urgent&&!problem.localAdaptation);
 if(localBlocker)return status('materials-blocked','材料有待处理项','需要处理',localBlocker.message,details);
 if(planCurrent&&!state.localReady)return status('materials-blocked','材料有待处理项','需要处理','当前版本的本地材料检查尚未通过。',preparationDetails);
 if(state.resultsReady||primary.kind==='results'){
  if(candidateFiles.some(segment=>!resultSegmentState(segment,generation?.result_states).loaded))return status('loading','正在读取状态','正在读取状态','正在核对已有候选与当前版本的关系。',{...details,primary:{kind:'loading'}});
  if(generation?.result_states&&staleCandidates.length)return status('results-stale',candidates.length?'部分结果待更新':'已有结果待更新','查看并更新结果','已有文件未被当前结果状态确认。',details);
  if(!candidateFiles.length)return status('task-saving','等待结果接入','等待结果接入','已有任务结果尚未进入当前项目记录。',details);
  return status('results','结果已保存 · 待验收','查看并验收结果','结果保存与第 5 步实际效果验收分别记录。',details);
 }
 if(state.localAdaptationOnly)return status('local-only',state.preparation.stale?'本地准备失效 · API 暂未开放':'API 付费生成未开放',configured?'原始候选检查与验收':'平台生成包','可准备本地生成包，并检查已有的原始候选。',details);
 if(state.preparation.stale)return status('preparation-stale','准备已失效 · 需更新','准备已失效','材料版本已经变化，旧准备不能作为当前完成状态。',preparationDetails);

 if(state.preparation.status==='FAILED')return status('preparation-failed','准备未完成','重新准备',errors[0]||state.preparation.message,details);
 if(state.preparation.status==='NEEDS_ATTENTION')return status('materials-attention','材料有待处理项','需要处理',state.preparation.message,details);
 if(state.preparation.status==='READY'&&!state.prepared)return status('preparation-invalid','准备需要更新','准备需要更新','当前版本的实际分段准备记录尚未匹配。',preparationDetails);
 if(state.preparation.status==='READY'&&!planCurrent)return status('check-invalid','材料检查需更新','材料检查需更新','尚无当前输入版本的材料检查记录。',preparationDetails);
 if(configured&&state.compatibility?.ready!==true)return status('compatibility','生成兼容性待处理','需要处理','本地文件已准备不代表当前 API 条件已经满足。',preparationDetails);
 if(configured&&state.quoteReady&&state.localReady&&planCurrent)return status('quote','待核对材料与费用','确认生成','尚未确认上传与本次费用。',details);
 if(state.exported&&!state.preparation.stale&&state.packageAsset){
  return status('exported',state.materialCurrent?'生成包已导出 · 材料已确认':'生成包已导出 · 材料待确认','平台生成包','导出生成包不会调用付费 API。',details);
 }
 const failedRun=state.currentRun&&!state.currentRun.stale?state.currentRun:null;
 const failed=(failedRun?.segments||[]).filter(segment=>generationSegmentState(segment).retryable);
 if(failed.length||['FAILED','PARTIAL','ATTENTION'].includes(failedRun?.status))return status('generation-failed',candidates.length?'部分片段未完成':'生成未完成','处理未完成片段','只重试明确失败或尚未提交的片段。',details);
 if(!configured)return state.prepared&&planCurrent&&state.localReady?
  status('local-prepared','本地已准备 · 待导出生成包','平台生成包','导出生成包不会调用付费 API。',details):
  status('idle','等待导出生成包','平台生成包','',details);
 if(state.preparation.status==='READY')return status('quote-invalid','费用确认需更新','费用确认需更新','旧费用或已使用的确认记录不代表新的付费授权。',details);
 if(state.prepared&&planCurrent&&state.localReady)return status('local-prepared','本地已准备 · 尚未可生成',configured?'准备生成':'平台生成包','仍需按当前路径完成材料与费用准备。',details);
 return status('idle',configured?'等待准备生成':'等待导出生成包',configured?'准备生成':'平台生成包','',details);
}

export function resultWorkflowStatus(project={},generation,options={}){
 project=project||{};
 const segments=project.segments||[];
 const segment=segments.find(item=>item.id===options.selectedSegmentId)||segments[0];
 const records=generation?.result_states;
 const segmentState=resultSegmentState(segment,records);
 const projectState=resultProjectState(project,records,generation?.material_confirmation);
 const details={segment,segmentState,projectState,historicalFinal:!!project.final&&!projectState.finalCurrent};
 const recordLoaded=segmentState.loaded??(object(records?.segments)&&!!segment&&Object.hasOwn(records.segments,segment.id));
 if(options.loading===true||!object(generation)||!object(records)||segment&&!recordLoaded)return status('loading','正在读取状态','正在读取状态','尚未取得当前片段的检查与验收状态。',details);
 if(!segment)return status('segments','等待保存分段','先保存分段','尚无可验收的片段。',details);
 if(options.hasDrafts===true)return status('draft','有未保存修改','先处理未保存修改','此页检查、验收与导出使用已保存版本。',details);
 if(projectState.finalCurrent)return status('final','成片已导出','完整成片已导出','当前成片与已验收的原始候选对应。',details);
 if(projectState.exportReady)return status('export','各段已验收 · 可导出','导出完整成片','',details);
 if(projectState.segments.length&&projectState.pending.length===0)return status('material-confirmation','各段已验收 · 材料待确认','确认当前材料','尚未取得当前输入版本的材料确认。',details);
 if(segmentState.reviewed)return status('reviewed','本段已验收 · 继续其他片段','本段验收已保存','',details);
 if(!segmentState.hasResult)return status('missing','当前段待导入','接收当前段结果',details.historicalFinal?'历史成片保留，不能作为当前段已验收的依据。':'',details);
 if(!segmentState.candidateCurrent)return status('candidate-stale','当前段结果待更新','更新当前段结果',segmentState.reasons?.candidate||'已有候选文件尚未通过当前版本核对。',details);
 if(!segmentState.validationCurrent)return status('validation','当前段原始候选待检查','检查原始候选',segmentState.reasons?.validation,details);
 return status('review','当前段待验收','对照验收',segmentState.reasons?.review,details);
}
