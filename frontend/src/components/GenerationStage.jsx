import {cn} from "@/lib/utils";
import React,{useEffect,useRef,useState} from 'react';
import { Button as UiButton } from './ui/button';
import { Alert, AlertTitle, AlertDescription } from './ui/alert';

import { Checkbox } from './ui/checkbox';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card';
import { Badge } from './ui/badge';
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from './ui/table';
import { Input } from './ui/input';
import { FieldGroup } from './ui/field';
import { Separator } from './ui/separator';

import {AlertCircle,ArrowLeft,ArrowRight,Download,PanelRightClose,PanelRightOpen,Play,RefreshCw,Settings,ShieldCheck,Video} from 'lucide-react';
import {assetUrl,time} from '../api';
import {GenerationDisclosure,GenerationSelect,revealGenerationDisclosures} from './GenerationControls';
import MaterialPromptEditor from './MaterialPromptEditor';
import './GenerationSettings.css';
import {DeliveryDimensionNote,DeliverySpecFacts,specSeconds} from './DeliverySpec';
import {generationState,generationConfirmation,generationDraftTargets,generationErrorText,generationNeedsConfiguration,generationUsageRows,generationSegmentState,retryableGenerationSegments,readGenerationReviewDraft,writeGenerationReviewDraft,readGenerationFeeConfirmation,writeGenerationFeeConfirmation} from '../generationPresentation';
import {generationWorkflowStatus} from '../workflowStatus';
import {candidateSampleState} from '../candidateAdaptationPresentation';
import {resultSegmentState} from '../resultPresentation';
import {auditionToCandidate,shouldCorrectPlayback} from '../candidateComparisonTiming';
import './GenerationResults.css';
import './GenerationStage.css';
import './GenerationActions.css';

const MODELS={'wan3.0-video':'Wan 3.0','wan3.0-video-prime':'Wan 3.0 Prime'};
const RUN_LABELS={COMPLETED:'结果已保存',PARTIAL:'部分片段未完成',ATTENTION:'需要处理',FAILED:'生成未完成',RUNNING:'正在生成与保存',UNKNOWN:'状态待确认'};
const RESULT_STATUS_VARIANTS={progress:'secondary',success:'success',warning:'warning',error:'destructive',neutral:'secondary'};
const PREPARATION_STAGES={TEMPLATES:'整理提示词',CHECKING:'检查材料',PREPARING_FILES:'准备分段文件',ESTIMATING:'核对费用',PACKAGING:'导出生成包',AWAITING_CONFIRMATION:'准备完成',COMPLETE:'准备完成'};
const UNRESOLVED=['QUEUED','UPLOADING','SUBMITTING','PENDING','RUNNING','SUCCEEDED','DOWNLOADING','UNKNOWN','DOWNLOAD_FAILED'];
const MATERIAL_LABEL='我已确认本次人物对应、参考图及替换与保护范围。';

function GenerationActionButton({primary=false,children,...props}){
 return <UiButton type={props.asChild?undefined:'button'} className={primary?'generation-primary-action':undefined} {...props} variant={primary?'default':'outline'} size="default">{children}</UiButton>;
}

function openSection(id){
 const element=document.getElementById(id);
 if(!element)return;
 const focus=()=>{
  if(!element.isConnected)return;
  if(!element.hasAttribute('tabindex'))element.setAttribute('tabindex','-1');
  element.scrollIntoView({block:'start',behavior:'instant'});
  element.focus({preventScroll:true});
 };
 if(revealGenerationDisclosures(element))requestAnimationFrame(focus);
 else focus();
}

function GenerationResultError({children}){
 return <Alert role="alert" data-tone={"destructive"} variant="destructive">
  <AlertCircle size={16} aria-hidden="true"/>
  <AlertDescription>{children}</AlertDescription>
 </Alert>;
}

function SamplePlayback({raw,audition,fps}){
 const video=useRef(null),audio=useRef(null),lastCorrection=useRef(-Infinity);
 const [auditionPlaying,setAuditionPlaying]=useState(false);
 function syncAudition(playing,force=false){
  const player=video.current;
  if(!player||!audio.current)return;
  const target=auditionToCandidate(audio.current.currentTime,audition),now=performance.now();
  if(shouldCorrectPlayback({currentTime:player.currentTime,targetTime:target.time,playing,force,starting:playing&&target.active&&player.paused,seeking:player.seeking,fps,now,lastCorrectionAt:lastCorrection.current})){
   player.currentTime=target.time;lastCorrection.current=now;
  }
  if(playing&&target.active){if(player.paused)player.play().catch(()=>{});}else if(!player.paused)player.pause();
 }
 function changeAuditionPlayback(playing){
  if(video.current)video.current.muted=playing;
  setAuditionPlaying(playing);
  syncAudition(playing,true);
 }
 return <div className="generation-sample-playback">
  <video ref={video} src={assetUrl(raw)} aria-label="云端原始候选画面" controls={!auditionPlaying} muted={auditionPlaying} playsInline preload="metadata"/>
  {audition&&<div className="generation-audition-player">
   <p className="text-sm text-muted-foreground mb-0">
    原声试听 · 画面覆盖 {Number(audition.visual_coverage_seconds).toFixed(6)} 秒。{audition.timeline_adaptation_status==='BLOCKED'?'候选末端覆盖不足。':''}
   </p>
   <audio ref={audio} controls aria-label="原声试听" src={assetUrl(audition.audio_asset)} onPlay={()=>changeAuditionPlayback(true)} onPause={()=>changeAuditionPlayback(false)} onSeeking={()=>syncAudition(!audio.current?.paused,true)} onTimeUpdate={()=>{if(!audio.current?.paused)syncAudition(true);}} onEnded={()=>changeAuditionPlayback(false)}/>
  </div>}
 </div>;
}

export default function GenerationStage({project,segment,onPatch,onAction,onStep,onSelectSegment,onConfig,config,busy=false,error,useDraft,PreparationLinks,generation={},onGeneration,draftCount=0,dirtyDraftTargets=[],onOpenSupport,navigationRequest}){
 const [inspectorOpen,setInspectorOpen]=useState(true);
 const inspectorRef=useRef(null),inspectorToggle=useRef(null),appliedNavigation=useRef(null);
 const [prompt,setPrompt,dirty,save,reset]=useDraft(project.prompts?.[segment?.id]||'',project.id+(segment?.id||''),project.revision);
 const [purpose,setPurpose]=useState(()=>generation.quote?.purpose||generation.preparation?.purpose||'strict_final');
 const [range,setRange]=useState(()=>generation.quote?.purpose==='candidate_sample'?'current':'all');
 const [includeSourceAudio,setIncludeSourceAudio]=useState(()=>!!(generation.quote?.input_policy||generation.preparation?.input_policy)?.include_source_audio);
 const selectedIds=range==='current'?(segment?[segment.id]:[]):project.segments.map(item=>item.id);
 const scopedGeneration={...generation,requested_scope:{purpose,segment_ids:selectedIds,include_source_audio:includeSourceAudio}};
 const state=generationState(project,scopedGeneration),settings=project.settings||{},configured=config?.configured===true;
 const deliverySpec=state.compatibility?.delivery_spec;
 const quoteSpec=state.quoteCurrent&&state.scopeCurrent?state.quote?.delivery_spec:null;
 const durationWarnings=(quoteSpec?.duration_warnings||deliverySpec?.duration_warnings||[]).filter(item=>selectedIds.includes(item.segment_id));
 const sample=candidateSampleState(segment,state.fingerprint,generation.sample_states?.[segment?.id]);
 const sampleTimeline=sample.inspection?.timeline_adaptation;
 const callEvidence=state.currentRun?.call_evidence;
 const [requestPending,setRequestPending]=useState(''),[requestError,setRequestError]=useState(''),[adoptIds,setAdoptIds]=useState({});
 const materialVersion=includeSourceAudio?(state.scopeCurrent&&state.quote?.input_policy?.include_source_audio?state.quote.materials_fingerprint||state.fingerprint+':source-audio':state.fingerprint+':source-audio-pending'):state.fingerprint,materialKey=project.id+':'+materialVersion;
 const [materialDraft,setMaterialDraft]=useState(()=>({key:materialKey,confirmed:readGenerationReviewDraft(project.id,materialVersion).material_confirmed===true}));
 const materialConfirmed=materialDraft.key===materialKey?materialDraft.confirmed:readGenerationReviewDraft(project.id,materialVersion).material_confirmed===true;
 const quoteId=state.quote?.id||'',feeKey=project.id+':'+quoteId;
 const [feeDraft,setFeeDraft]=useState(()=>({key:feeKey,confirmed:readGenerationFeeConfirmation(project.id,quoteId)}));
 const feeConfirmed=feeDraft.key===feeKey?feeDraft.confirmed:readGenerationFeeConfirmation(project.id,quoteId);
 useEffect(()=>{setMaterialDraft({key:materialKey,confirmed:readGenerationReviewDraft(project.id,materialVersion).material_confirmed===true});},[materialKey,project.id,materialVersion]);
 useEffect(()=>{setFeeDraft({key:feeKey,confirmed:readGenerationFeeConfirmation(project.id,quoteId)});},[feeKey,project.id,quoteId]);
 useEffect(()=>{setRequestError('');},[project.id]);
 const pending=!!busy||!!requestPending;
 const draftTargets=generationDraftTargets(dirtyDraftTargets,{dirty,segment});
 const hasDrafts=dirty||Number(draftCount)>0||draftTargets.length>0;
 const hasCandidates=project.segments.some(item=>item.generated);
 const needsExistingMaterialConfirmation=configured&&(hasCandidates||state.localAdaptationOnly)&&!state.materialCurrent;
 const canReviewExisting=configured&&hasCandidates&&state.materialCurrent&&!state.resultsReady;
 const prepareDisabled=pending||state.preparing||hasDrafts||!project.source||settings.resolution===null;
 const confirmation=generationConfirmation(state,{materialConfirmed,feeConfirmed,configured,dirty:hasDrafts,draftCount,busy:pending});
 const refs=state.plan?.segment_plans?.[segment?.id]?.references||[],groups=[];
 for(const reference of refs){
  const key=reference.role_id||reference.kind||'reference';
  let group=groups.find(item=>item.key===key);
  if(!group){group={key,title:reference.role_name||reference.name||'参考素材',references:[]};groups.push(group);}
  group.references.push(reference);
 }
 const totalSeconds=project.segments.filter(item=>selectedIds.includes(item.id)).reduce((total,item)=>total+Math.max(0,item.end-item.start),0);
 const taskCount=state.quoteCurrent&&state.scopeCurrent?(state.quote?.count??state.quote?.segment_ids?.length??selectedIds.length):selectedIds.length;
 const inputSeconds=state.quoteCurrent&&state.scopeCurrent?(state.quote?.input_seconds??totalSeconds):totalSeconds;
 const expectedOutputSeconds=state.quote?.expected_output_seconds??inputSeconds;
 const visibleErrors=[...new Set([requestError,generationErrorText(error),!state.preparation.stale&&generationErrorText(state.preparation.error)].filter(Boolean))].filter(message=>!state.issues.some(issue=>issue.message===message)&&!(state.localAdaptationOnly&&/24000\/1001|23\.976|本地适配|付费生成.*开放/.test(message)));
 const selectedRun=state.runs.some(run=>(!run.stale||run.local_current)&&!!run.input_policy?.include_source_audio===includeSourceAudio&&(run.segments||[]).some(item=>selectedIds.includes(item.segment_id)&&item.status==='COMPLETED'));
 const sameInputRun=!!state.currentRun?.input_policy?.include_source_audio===includeSourceAudio;
 const apiStartBlocked=state.localAdaptationOnly||prepareDisabled||state.quoteReady||(state.sampleOnly?selectedRun:sameInputRun&&(state.quoteConsumed||state.hasCurrentRun))||state.hasUnresolvedTasks;
 const unresolvedHistory=state.history.filter(run=>(run.segments||[]).some(item=>UNRESOLVED.includes(item.status)));
 const settledHistory=state.history.filter(run=>!unresolvedHistory.includes(run));
 const localMaterialBlocked=state.plan?.ready===false&&(state.plan.issues||[]).some(issue=>issue.blocking);
 const hasAvailableResults=hasCandidates;
 const workflowStatus=generationWorkflowStatus(project,scopedGeneration,{configured,hasDrafts,draftTargets,errors:visibleErrors,configurationErrorIndex:visibleErrors.findIndex(needsApiFix),hasCandidates:hasAvailableResults,materialConfirmed,selectedSegmentId:segment?.id});
 const {primary,problems}=workflowStatus;
 const leadingProblems=[...problems.filter(issue=>issue.localAdaptation),...problems.filter(issue=>issue.urgent&&!issue.localAdaptation)].slice(0,3);
 const additionalProblems=problems.filter(issue=>!leadingProblems.includes(issue));
 const showQuote=state.quoteReady&&(primary.kind==='confirm'||state.sampleOnly);
 const standaloneMaterial=needsExistingMaterialConfirmation&&!state.localAdaptationOnly&&!showQuote;
 const canViewResults=hasAvailableResults&&state.materialCurrent&&!hasDrafts;
 const showAction=hasDrafts||state.preparing||visibleErrors.length>0||leadingProblems.length>0||standaloneMaterial||['prepare','confirm','export','download','config','problem','local'].includes(primary.kind);
 const hasTasks=!!state.currentRun||state.runs.length>0;
 const packages=(project.assets||[]).filter(asset=>asset.kind==='package');
 const pricingSeen=new Set();
 const pricing=(state.quote?.pricing||[]).filter(item=>{const key=[item.source,item.checked_date,item.unit_yuan_per_second,item.currency].join('|');if(pricingSeen.has(key))return false;pricingSeen.add(key);return true;});

 useEffect(()=>{
  if(workflowStatus.loading||!navigationRequest||navigationRequest.step!==3||appliedNavigation.current===navigationRequest)return;
  if(navigationRequest.segmentId&&navigationRequest.segmentId!==segment?.id)return;
  const targetId=navigationRequest.targetId;
  if(!targetId)return;
  const element=document.getElementById(targetId);
  if(!element)return;
  if(inspectorRef.current?.contains(element))setInspectorOpen(true);
  const frame=requestAnimationFrame(()=>{
   if(!document.getElementById(targetId))return;
   appliedNavigation.current=navigationRequest;
   openSection(targetId);
  });
  return()=>cancelAnimationFrame(frame);
 },[navigationRequest,project.id,segment?.id,workflowStatus.loading]);
 function revealSection(id){
  const element=document.getElementById(id);
  if(inspectorRef.current?.contains(element)){
   setInspectorOpen(true);
   requestAnimationFrame(()=>openSection(id));
  }else openSection(id);
 }
 function closeInspector(){
  if(inspectorRef.current?.contains(document.activeElement))inspectorToggle.current?.focus();
  setInspectorOpen(false);
 }

 async function perform(payload){
  if(pending||!onGeneration)return null;
  setRequestPending(payload.action);setRequestError('');
  try{return await onGeneration(payload);}
  catch(failure){setRequestError(generationErrorText(failure));return null;}
  finally{setRequestPending('');}
 }
 function prepare(mode,extra={}){
  const selectedPurpose=extra.purpose||purpose;
  if(prepareDisabled||mode==='api'&&state.localAdaptationOnly&&selectedPurpose!=='candidate_sample')return;
  const material=(mode==='export'&&(state.materialCurrent||materialConfirmed)||mode==='api'&&needsExistingMaterialConfirmation&&materialConfirmed)?{material_confirmed:true}:{};
  return perform({action:'prepare',mode,...(mode==='api'?{purpose:selectedPurpose,segment_ids:selectedIds,include_source_audio:includeSourceAudio}:{}),...extra,...material});
 }
 function changeMaterial(checked){
  writeGenerationReviewDraft(project.id,materialVersion,{material_confirmed:checked});
  setMaterialDraft({key:materialKey,confirmed:checked});
 }
 function changeFee(checked){
  writeGenerationFeeConfirmation(project.id,quoteId,checked);
  setFeeDraft({key:feeKey,confirmed:checked});
 }
 function confirm(){
  if(!confirmation.allowed)return;
  return perform({action:'confirm',quote_id:quoteId,purpose:state.quote.purpose,segment_ids:state.quote.segment_ids,scope_fingerprint:state.quote.scope_fingerprint,include_source_audio:includeSourceAudio,...(!state.materialCurrent||includeSourceAudio?{material_confirmed:materialConfirmed}:{}),confirm_upload:true,confirm_generation:true,approved_budget_yuan:state.quote.reserved_yuan});
 }
 function choosePurpose(value){setPurpose(value);setRange(value==='candidate_sample'?'current':'all');}
 function startCurrentSample(){setPurpose('candidate_sample');setRange('current');return prepare('api',{purpose:'candidate_sample',segment_ids:segment?[segment.id]:[]});}
 function draftAction(target){
  if(target.segmentId&&project.segments.some(item=>item.id===target.segmentId))onSelectSegment?.(target.segmentId);
  if(target.step===3){requestAnimationFrame(()=>revealSection(target.targetId));return;}
  onStep?.(target.step,target.targetId,target);
 }
 function issueAction(issue,target=issue.target){
  const relatedSegment=target.segmentId&&project.segments.some(item=>item.id===target.segmentId)?target.segmentId:null;
  if(relatedSegment)onSelectSegment?.(relatedSegment);
  if(target.kind==='details'){
   requestAnimationFrame(()=>revealSection('generation-problem-'+issue.id));return;
  }
  if(target.kind==='local'){
   requestAnimationFrame(()=>revealSection(target.targetId));return;
  }
  const navigation={...issue,...target};
  if(!relatedSegment)delete navigation.segmentId;
  onStep?.(target.step,target.targetId,navigation);
 }
 function viewResults(){
  const entries=state.currentRun?.segments||[];
  const attached=entries.find(item=>item.segment_id===segment?.id&&item.asset&&item.attached)||entries.find(item=>item.asset&&item.attached);
  const existing=project.segments.find(item=>item.id===segment?.id&&item.generated)||project.segments.find(item=>item.generated);
  const id=attached?.segment_id||existing?.id;
  if(id)onSelectSegment?.(id);
  const result=resultSegmentState(project.segments.find(item=>item.id===id),generation.result_states);
  onStep?.(4,result.validationCurrent?'candidate-review':result.candidateCurrent?'candidate-check':'candidate-import',{segmentId:id});
 }
 function needsApiFix(message){
  return generationNeedsConfiguration(message)||message===generationErrorText(error)&&generationNeedsConfiguration(error,error)||message===generationErrorText(state.preparation.error)&&generationNeedsConfiguration(state.preparation.error,state.preparation.error);
 }
 function materialCheckbox(disabled=false){
  return <label className="text-sm generation-control-check generation-material-confirm">
   <Checkbox checked={materialConfirmed} onCheckedChange={checked=>changeMaterial(checked===true)} disabled={pending||disabled} aria-label="确认本次材料" />
   <span>{MATERIAL_LABEL}{includeSourceAudio?'并确认原声参考窗口、原始音画偏移及本次音频上传清单。':''}</span>
  </label>;
 }
 function renderRun(run,historical=false){
  const failed=retryableGenerationSegments(run,state.runs),rows=run.segments||[];
  const complete=(!run.stale||run.purpose==='candidate_sample'&&run.local_current)&&rows.length>0&&rows.every(item=>item.asset&&item.attached===true);
  const runStatus=complete?(run.purpose==='candidate_sample'?'云端已完成 · 原始候选已接收':'结果已保存 · 待复核'):RUN_LABELS[run.status]||'状态待确认';
  return <Card key={run.id} className="generation-result-run" aria-label={historical?'较早的生成任务':'本次生成任务'} role="region"><CardHeader><div className="generation-result-heading">
     <CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl">{historical?'较早任务':'本次任务'} · {rows.length} 个片段</h2></CardTitle>
     <Badge variant={complete?'success':run.status==='FAILED'?'destructive':'secondary'}>{runStatus}</Badge>
    </div></CardHeader><CardContent>
   
    
    {run.purpose==='candidate_sample'&&<p className="text-muted-foreground text-sm mb-0">本批次{run.input_policy?.include_source_audio?'计划提供原声参考，实际提交见原任务记录':'未提供原声参考'}。候选规格以文件实测为准。</p>}
    {run.stale&&<p role="status" className="text-muted-foreground text-sm mb-0">{run.local_current?'原始候选仍对应当前生成输入。':'旧版本任务可查询、关联和下载，结果不计入当前片段。'}</p>}
    {run.error&&<GenerationResultError>{generationErrorText(run.error)}</GenerationResultError>}
    <Table className="generation-run-table table-fixed tabular-nums">
     <TableHeader><TableRow>
      <TableHead width="22%">片段</TableHead>
      <TableHead width="32%">进度</TableHead>
      <TableHead>结果与处理</TableHead>
     </TableRow></TableHeader>
     <TableBody>{rows.map(item=>{
      const status=generationSegmentState(item),adoptKey=run.id+':'+item.segment_id,taskId=(adoptIds[adoptKey]||'').trim();
      const name=item.name||item.segment_id;
      const primaryRow=primary.runId===run.id&&primary.segmentId===item.segment_id;
      return <TableRow key={item.segment_id}>
       <TableHead scope="row">{name}</TableHead>
       <TableCell><div className="generation-result-cell">
        <Badge variant={RESULT_STATUS_VARIANTS[status.tone]}>{status.label}</Badge>
        {item.error&&<GenerationResultError>{generationErrorText(item.error)}</GenerationResultError>}
        {generationNeedsConfiguration(item.error,item.error_details)&&<GenerationActionButton primary={primary.kind==='run-config'&&primaryRow} disabled={pending} onClick={onConfig}>核对 API 设置与账户权限</GenerationActionButton>}
       </div></TableCell>
       <TableCell><div className="generation-result-cell">
        {item.asset&&<GenerationActionButton asChild><a href={assetUrl(item.asset)} download><Download aria-hidden="true" data-icon="inline-start" />下载结果</a></GenerationActionButton>}
        {status.canAdopt&&<div className="generation-result-adopt">
         <label htmlFor={'generation-task-'+adoptKey} className="text-sm font-medium">平台任务编号</label>
         <div className="generation-result-adopt-controls">
          <Input id={'generation-task-'+adoptKey} value={adoptIds[adoptKey]||''} onChange={event=>setAdoptIds(values=>({...values,[adoptKey]:event.target.value}))} aria-label={name+'平台任务编号'} placeholder="粘贴已有任务编号" disabled={pending} />
          <GenerationActionButton primary={primary.kind==='adopt'&&primaryRow} disabled={pending||!/^[A-Za-z0-9_-]{1,150}$/.test(taskId)}
           onClick={()=>perform({action:'adopt',run_id:run.id,segment_id:item.segment_id,task_id:taskId})}>关联并查询</GenerationActionButton>
         </div>
        </div>}
        {!item.asset&&item.status==='DOWNLOAD_FAILED'&&<span className="text-muted-foreground text-sm">刷新原任务可重试保存。</span>}
       </div></TableCell>
      </TableRow>;
     })}</TableBody>
    </Table>
    <div className="generation-result-actions">
     <GenerationActionButton primary={primary.kind==='refresh'&&primary.runId===run.id} disabled={pending} onClick={()=>perform({action:'refresh',run_id:run.id})}><RefreshCw aria-hidden="true" data-icon="inline-start" />{run.stale?'查询旧任务':'刷新任务与保存结果'}</GenerationActionButton>
     {failed.length>0&&<GenerationActionButton primary={primary.kind==='retry'&&primary.runId===run.id} disabled={state.localAdaptationOnly||prepareDisabled||state.hasUnresolvedTasks||!configured}
      onClick={()=>{setPurpose(run.purpose||'strict_final');setIncludeSourceAudio(!!run.input_policy?.include_source_audio);return prepare('api',{retry_of:run.id,purpose:run.purpose||'strict_final',segment_ids:failed,include_source_audio:!!run.input_policy?.include_source_audio});}}>重试失败片段（可能新增费用）</GenerationActionButton>}
    </div>
    {failed.length>0&&state.hasUnresolvedTasks&&<p className="text-sm text-muted-foreground mb-0">先恢复未完成任务，再重试失败片段。</p>}
    {run.delivery_spec&&<GenerationDisclosure label={'本任务冻结的交付规格 · '+run.delivery_spec.resolution}>
     <DeliverySpecFacts spec={run.delivery_spec}/>
    </GenerationDisclosure>}
    {rows.some(item=>item.task_id||item.return_inspection?.actual)&&<GenerationDisclosure label="任务详情、平台计费用量和文件解码">
     <div className="generation-result-records">
      <Table className="generation-run-table table-fixed tabular-nums">
       <TableHeader><TableRow>
        <TableHead width="26%">片段 / 任务</TableHead>
        <TableHead width="30%">平台计费用量</TableHead>
        <TableHead>文件解码</TableHead>
       </TableRow></TableHeader>
       <TableBody>{rows.map(item=>{
        const actual=item.return_inspection?.actual,usage=generationUsageRows(item.usage);
        return <TableRow key={item.segment_id}>
         <TableHead scope="row"><div className="generation-result-cell">
          {item.name||item.segment_id}
          {item.task_id&&<code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">{item.task_id}</code>}
         </div></TableHead>
         <TableCell>{usage.length?usage.map(row=><div key={row.key}>{row.label} {row.seconds.toFixed(3)} 秒</div>):'暂无回执'}</TableCell>
         <TableCell>{actual?`${actual.width??'—'} × ${actual.height??'—'} · ${actual.fps_rational??'—'} 帧/秒 · ${actual.frame_count??'待检查'} 帧 · ${specSeconds(actual.decoded_duration_seconds)}`:'待检查'}</TableCell>
        </TableRow>;
       })}</TableBody>
      </Table>
      <p className="text-muted-foreground text-sm mb-0">实际计费以官方账单为准。技术验收以解码帧数和时间戳为准。</p>
     </div>
    </GenerationDisclosure>}
   
  </CardContent></Card>;
 }

 function renderProblem(problem){
  const selected=primary.kind==='problem'&&primary.problemId===problem.id;
  const targets=problem.targets||[problem.target],localDetails=targets.some(target=>target.kind==='details');
  return <li key={problem.id} className={problem.urgent?'generation-problem-blocking':'generation-problem-note'}>
   <p className={cn("text-sm", problem.urgent?'font-medium':'font-normal')}>{problem.message}</p>
   {problem.localAdaptation?<GenerationDisclosure label="原因与本地选项" className="generation-local-options" id="generation-local-options">
    <div className="generation-action-stack">
     {problem.explanation.map((detail,index)=><p key={index} className="text-sm text-muted-foreground">{detail}</p>)}
     <p className="text-sm text-muted-foreground">已有原始候选可在第 5 步检查和验收。</p>
     <div className="flex flex-wrap gap-3">
      {configured&&<GenerationActionButton disabled={prepareDisabled||state.hasUnresolvedTasks} onClick={startCurrentSample}>准备当前片段候选小样</GenerationActionButton>}
      <GenerationActionButton onClick={()=>onStep?.(4,'candidate-import')}>检查原始候选</GenerationActionButton>
     </div>
     {configured&&<>
      {!state.materialCurrent&&<fieldset className="generation-review-checks" disabled={pending}>{materialCheckbox(localMaterialBlocked)}</fieldset>}
      <div className="flex flex-wrap gap-3">
       <GenerationActionButton disabled={prepareDisabled} onClick={()=>prepare('export')}><Download aria-hidden="true" data-icon="inline-start" />{!state.materialCurrent&&materialConfirmed?'确认材料并导出本地生成包':'导出本地生成包'}</GenerationActionButton>
       {state.exported&&state.packageAsset&&!state.preparation.stale&&<GenerationActionButton asChild><a href={assetUrl(state.packageAsset)} download>{state.materialCurrent?'下载本地生成包':'下载本地生成包（材料待确认）'}</a></GenerationActionButton>}
      </div>
     </>}
    </div>
   </GenerationDisclosure>:<>
    <div className="flex flex-wrap gap-3">
     {targets.map((target,index)=><GenerationActionButton key={target.kind+'-'+target.targetId+'-'+index}
      primary={selected&&index===0} onClick={()=>issueAction(problem,target)}>{target.label}</GenerationActionButton>)}
    </div>
    {(problem.explanation.length>0||localDetails)&&<GenerationDisclosure className="generation-problem-explanation" id={'generation-problem-'+problem.id}
     label={localDetails?'问题详情':'原因与要求'}>
     <div className="generation-action-stack">
      {problem.explanation.map((detail,index)=><p key={index} className="text-sm text-muted-foreground">{detail}</p>)}
      {localDetails&&problem.code&&<p className="text-sm text-muted-foreground">问题编号：{problem.code}</p>}
     </div>
    </GenerationDisclosure>}
   </>}
  </li>;
 }
 function renderAdditionalProblems(){
  return additionalProblems.length>0?<GenerationDisclosure className="generation-issue-details" label={`其余问题与说明 · ${additionalProblems.length} 项`}>
   <ul className="generation-problem-list">{additionalProblems.map(renderProblem)}</ul>
  </GenerationDisclosure>:null;
 }

 const samplePreparationAction=state.sampleOnly&&!showQuote&&!state.preparing&&configured?<div className="flex">
  <GenerationActionButton disabled={apiStartBlocked} onClick={()=>prepare('api')}>准备选定片段小样</GenerationActionButton>
 </div>:null;
 if(workflowStatus.loading)return <div className="generation-stage" role="status">{workflowStatus.label}</div>;
 return <div className="generation-stage" data-testid="generation-stage" id="wan-materials" tabIndex="-1">
  <div className="generation-stage-toolbar">
   <div className="generation-review-context">
    {project.segments.length>1?<GenerationSelect label="核对片段" className="generation-material-picker" value={segment?.id||''}
     disabled={pending||!project.segments.length} placeholder="尚未保存分段"
     onValueChange={onSelectSegment}
     options={project.segments.map(item=>[item.id,`${item.name||item.id} · ${time(item.start)} — ${time(item.end)}`])}/>:<>
     <span className="text-sm font-medium">{segment?.name||segment?.id||'尚未保存分段'}</span>
     {segment&&<span className="text-sm text-muted-foreground tabular-nums">{time(segment.start)} — {time(segment.end)}</span>}
    </>}
   </div>
   <UiButton ref={inspectorToggle} type="button" aria-expanded={inspectorOpen} aria-controls="generation-inspector" onClick={()=>inspectorOpen?closeInspector():setInspectorOpen(true)} variant="outline" size="default">
    {inspectorOpen?<PanelRightClose aria-hidden="true" data-icon="inline-start" />:<PanelRightOpen aria-hidden="true" data-icon="inline-start" />}
    {inspectorOpen?'收起生成设置':'查看生成设置'}
   </UiButton>
  </div>
  <div className="generation-work-grid" data-inspector-open={inspectorOpen}>
   <div className="generation-main">
    {!showAction&&samplePreparationAction}
  <div id="generation-material-confirmation" className="generation-material-confirmation" tabIndex="-1" aria-label="本次材料确认">
   {state.materialCurrent&&!hasDrafts&&<div>
    <Badge className="generation-material-state" variant={"secondary"}>{includeSourceAudio?'基础人物材料已确认；原声参考仍需本次确认':'当前材料已确认'}</Badge>
   </div>}
   {showAction&&<Card className="generation-action-section generation-action-card" aria-label="当前操作" role="region"><CardHeader><div className="flex items-center justify-between flex-wrap gap-3">
      <CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl">{showQuote?'本次费用':workflowStatus.heading}</h2></CardTitle>
      {state.preparing&&!hasDrafts&&<Badge role="status" variant={"secondary"}>{PREPARATION_STAGES[state.preparation.stage]||'正在准备'}</Badge>}
     </div>
     {showQuote&&<CardDescription>{state.quote.segment_ids?.join('、')||taskCount+' 个片段'} · {state.quote.purpose==='candidate_sample'?'候选小样':'严格成片候选'}</CardDescription>}
    </CardHeader><CardContent>
     {samplePreparationAction}
     {hasDrafts?<Alert className="generation-draft-notice" role="status" data-tone={"warning"}>
      <AlertCircle size={17} aria-hidden="true"/>
      <AlertDescription className="generation-action-stack">
       <p>保存或放弃后继续。</p>
       {(state.preparation.fingerprint||project.preparation)&&<span className="text-sm">现有准备仅对应已保存版本</span>}
       <div className="flex flex-wrap gap-3 generation-draft-targets">
        {draftTargets.map((target,index)=><GenerationActionButton key={index} primary={index===0} onClick={()=>draftAction(target)}>{target.label}</GenerationActionButton>)}
       </div>
      </AlertDescription>
     </Alert>:<>
      {visibleErrors.map((message,index)=><Alert role="alert" key={message} data-tone={"destructive"} variant="destructive">
       <AlertCircle size={17} aria-hidden="true"/>
       <AlertDescription className="generation-action-stack">
        <p>{message}</p>
        {needsApiFix(message)&&<GenerationActionButton primary={primary.kind==='config'&&primary.errorIndex===index} onClick={onConfig}>核对 API 设置与账户权限</GenerationActionButton>}
       </AlertDescription>
      </Alert>)}
      {leadingProblems.length>0&&<ul className="generation-problem-list">{leadingProblems.map(renderProblem)}</ul>}
      {renderAdditionalProblems()}
      {primary.kind==='config'&&primary.errorIndex<0&&<div className="flex"><GenerationActionButton primary onClick={onConfig}>配置 API Key</GenerationActionButton></div>}
      {primary.kind==='config'&&configured&&!state.localAdaptationOnly&&!state.hasUnresolvedTasks&&<div className="flex">
       <GenerationActionButton disabled={prepareDisabled} onClick={()=>prepare('api')}>修复后重新准备</GenerationActionButton>
      </div>}
      {!state.preparing&&configured&&!state.localAdaptationOnly&&!state.sampleOnly&&primary.kind==='prepare'&&!standaloneMaterial&&<div className="flex">
       <GenerationActionButton primary disabled={apiStartBlocked} onClick={()=>prepare('api')}><Play aria-hidden="true" data-icon="inline-start" />{visibleErrors.length?'重新准备':'准备生成'}</GenerationActionButton>
      </div>}
      {!state.preparing&&standaloneMaterial&&<div className="generation-existing-material generation-action-stack">
       <fieldset className="generation-review-checks" disabled={pending}>{materialCheckbox(localMaterialBlocked)}</fieldset>
       <GenerationActionButton primary={primary.kind==='prepare'} disabled={prepareDisabled} onClick={()=>prepare('api')}><Play aria-hidden="true" data-icon="inline-start" />准备验收材料</GenerationActionButton>
      </div>}

      {!state.preparing&&!configured&&<div className="generation-export-result generation-action-stack">
       {!state.materialCurrent&&<fieldset className="generation-review-checks" disabled={pending}>{materialCheckbox(localMaterialBlocked)}</fieldset>}
       <div className="flex flex-wrap gap-3">
        {state.exported&&state.packageAsset&&!state.preparation.stale&&<GenerationActionButton asChild primary={primary.kind==='download'}>
         <a href={assetUrl(state.packageAsset)} download><Download aria-hidden="true" data-icon="inline-start" />{state.materialCurrent?'下载生成包':'下载生成包（材料待确认）'}</a>
        </GenerationActionButton>}
        {(!state.exported||!state.materialCurrent||state.preparation.stale)&&<GenerationActionButton primary={primary.kind==='export'} disabled={prepareDisabled} onClick={()=>prepare('export')}><Download aria-hidden="true" data-icon="inline-start" />{state.exported&&materialConfirmed?'确认材料并导出生成包':'导出生成包'}</GenerationActionButton>}
        {state.exported&&<GenerationActionButton onClick={()=>onStep?.(4,'candidate-import')}>导入平台结果</GenerationActionButton>}
        {primary.kind!=='config'&&<GenerationActionButton disabled={pending} onClick={onConfig}>配置 API Key</GenerationActionButton>}
       </div>
      </div>}

      {showQuote&&<section className="generation-quote" aria-label="本次付费生成确认">
       {quoteSpec&&<div className="generation-quote-contract generation-action-stack">
        <h3 className="font-semibold leading-snug tracking-tight text-base">本次冻结的交付规格</h3>
        <DeliverySpecFacts spec={quoteSpec} className="generation-parameters"/>
        <DeliveryDimensionNote spec={quoteSpec}/>
       </div>}
       <dl className="generation-quote-costs">
        <div><dt>预计费用</dt><dd>¥{Number(state.quote.estimate_yuan||0).toFixed(2)}</dd></div>
        <div><dt>预留上限</dt><dd>¥{Number(state.quote.reserved_yuan||0).toFixed(2)}</dd></div>
       </dl>
       <div className="generation-action-stack">
        <p className="text-sm text-muted-foreground">仅授权以上片段。实扣金额以官方账单为准。</p>
        <p className="text-sm text-muted-foreground">本次{state.quote.input_policy?.include_source_audio?'会上传原声参考':'不上传原声参考'}。模型输出声音关闭。输入清单：{(state.quote.material_manifest||[]).map(item=>`${item.segment_id}：${item.media.filter(m=>m.type==='reference_video').length}段视频、${item.media.filter(m=>m.type==='reference_image').length}张图、${item.media.filter(m=>m.type==='reference_audio').length}段原声`).join('；')||'以当前报价材料清单为准'}。</p>
       </div>
       {state.configuration?.ready===false&&<Alert role="alert" data-tone={"warning"}>
        <AlertCircle size={17} aria-hidden="true"/>
        <AlertDescription className="generation-action-stack">
         <p>{state.configuration.issues?.[0]?.message}</p>
         <GenerationActionButton onClick={onConfig}>核对业务空间与 API 地域</GenerationActionButton>
        </AlertDescription>
       </Alert>}
       {state.quote.submission_ready===false&&<div className="flex"><GenerationActionButton disabled={prepareDisabled} onClick={()=>prepare('api')}>{state.quote.input_materials_current===false?'按当前材料重新准备报价':'按当前配置重新准备报价'}</GenerationActionButton></div>}
       <GenerationDisclosure label="费用与材料详情" className="generation-pricing-details">
        <div className="generation-action-stack">
         <p className="text-sm text-muted-foreground">预计输出共 {Number(expectedOutputSeconds).toFixed(2)} 秒。预留按智能时长的最大输出计算，均非实扣金额。</p>
         {pricing.length>0&&<ul className="generation-pricing-list">{pricing.map((item,index)=><li key={index}>
          <span>{typeof item.source==='string'&&/^https:\/\//.test(item.source)?<a href={item.source} target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline">官方价格说明</a>:item.source||'本次预检价格表'}</span>
          <span>核查日期：{item.checked_date||'未提供'}</span>
          {Number.isFinite(item.unit_yuan_per_second)&&<span>输入与输出视频每秒 ¥{item.unit_yuan_per_second}</span>}
         </li>)}</ul>}
         <p className="text-sm text-muted-foreground">接口返回用量不代表实扣金额。</p>
        </div>
        <Separator decorative />
        <div className="generation-action-stack">
        {quoteSpec?.segments?.map(item=><p key={item.segment_id} className="text-sm text-muted-foreground">{item.segment_id} · 原片 F{item.first_source_frame}–F{item.last_source_frame} · {item.frame_count} 帧 · {specSeconds(item.duration_seconds)}</p>)}
        <p className="text-sm text-muted-foreground">材料版本 <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-muted-foreground">{state.quote.materials_fingerprint||state.quote.fingerprint}</code></p>
       {Object.entries(state.quote.input_contract?.source_audio||{}).map(([sid,audio])=><div key={sid} className="generation-action-stack">
        <p className="text-sm text-muted-foreground">{sid} 原声 {Number(audio.duration_seconds).toFixed(6)} 秒，首视频帧位于音频窗口 {Number(audio.video_first_in_audio_seconds).toFixed(6)} 秒。原始音画偏移 {audio.original_audio_video_offset_rational} 秒，保留原采样率与连续原声。</p>
        <p className="text-sm text-muted-foreground">{audio.submission_prompt_note}</p>
       </div>)}
        </div>
       </GenerationDisclosure>
       <fieldset className="generation-review-checks generation-quote-checks" disabled={pending}>
        {(!state.materialCurrent||includeSourceAudio)&&materialCheckbox()}
        <label className="text-sm font-medium generation-control-check generation-cost-confirm">
         <Checkbox checked={feeConfirmed} disabled={pending} onCheckedChange={checked=>changeFee(checked===true)} aria-label="授权本次上传和费用" />
         <span>同意上传本次材料，并授权本次预留上限内的付费生成。</span>
        </label>
       </fieldset>
       <div className="flex items-center flex-wrap gap-3 generation-confirm-actions">
        <GenerationActionButton primary disabled={!confirmation.allowed} onClick={confirm}><ShieldCheck aria-hidden="true" data-icon="inline-start" />确认并付费生成</GenerationActionButton>
        <p role="status" className="text-sm text-muted-foreground">{confirmation.allowed?'可提交 · 尚未发送':confirmation.reason||'提交条件待确认'}</p>
       </div>
      </section>}

      {showQuote&&needsExistingMaterialConfirmation&&<div className="flex"><GenerationActionButton disabled={prepareDisabled} onClick={()=>prepare('api')}>仅准备已有结果的验收材料</GenerationActionButton></div>}
     </>}
    
   </CardContent></Card>}
  </div>

  {hasTasks&&<section className="generation-progress" aria-label="生成进度">
   
    
    {state.currentRun&&renderRun(state.currentRun)}
    {unresolvedHistory.map(run=>renderRun(run,true))}
    {settledHistory.length>0&&<GenerationDisclosure label={`较早任务 · ${settledHistory.length} 次`}>
     <div className="generation-result-records">{settledHistory.map(run=>renderRun(run,true))}</div>
    </GenerationDisclosure>}
   
  </section>}

  {hasAvailableResults&&<section className="generation-results" aria-label="已保存结果">
   
    
    <div className="generation-result-actions">
     <GenerationActionButton primary={primary.kind==='results'} disabled={!canViewResults} onClick={viewResults}>
      {workflowStatus.code==='results-stale'?'查看并更新结果':'查看并验收结果'}<ArrowRight aria-hidden="true" data-icon="inline-start" />
     </GenerationActionButton>
     {!state.materialCurrent&&!hasDrafts&&<GenerationActionButton onClick={()=>revealSection('generation-material-confirmation')}>先确认当前材料</GenerationActionButton>}
    </div>
   
  </section>}

  {sample.raw&&<Card className="generation-sample-review generation-result-panel" aria-label="云端原始小样" role="region"><CardHeader><CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl">{segment?.name||segment?.id} · {sample.status.label}</h2></CardTitle></CardHeader><CardContent>
   
    
    {!sample.current?<>
     <p role="status" className="text-sm text-muted-foreground mb-0">{sample.reason||'当前生成输入与原始候选尚未匹配。'}</p>
     <div className="generation-result-actions"><GenerationActionButton asChild><a href={assetUrl(sample.raw)} download><Download aria-hidden="true" data-icon="inline-start" />下载历史原始候选</a></GenerationActionButton></div>
    </>:<>
     <SamplePlayback key={sample.raw.id+':'+(sample.audition?.audio_asset?.id||'')} raw={sample.raw} audition={sample.audition} fps={sample.provenance.actual_media?.fps_rational||30}/>
     {sample.provenance.audition_attempt?.status==='FAILED'&&<GenerationResultError>原声试听未完成：{sample.provenance.audition_attempt.error}</GenerationResultError>}
     <div className="generation-result-actions">
      <GenerationActionButton asChild><a href={assetUrl(sample.raw)} download><Download aria-hidden="true" data-icon="inline-start" />下载原始候选</a></GenerationActionButton>
      {!sample.audition&&<GenerationActionButton disabled={pending||hasDrafts||project.source_meta?.has_audio===false} onClick={()=>perform({action:'prepare_sample_audition',segment_id:segment.id})}>准备原声试听</GenerationActionButton>}
      <GenerationActionButton disabled={pending||hasDrafts} onClick={()=>perform({action:'inspect_sample_result',segment_id:segment.id})}>检查实际规格与时间覆盖</GenerationActionButton>
     </div>
     <GenerationDisclosure label="实际规格与时间覆盖">
      <div className="generation-result-records">
       <p className="text-muted-foreground text-sm mb-0">原始候选：{sample.provenance.actual_media?.width||'—'}×{sample.provenance.actual_media?.height||'—'}，{sample.provenance.actual_media?.fps_rational||'—'}fps；{sample.inspection?.actual?.frame_count??'待测'} 帧，文件画面时长 {sample.inspection?.actual?.decoded_duration_seconds??'待测'} 秒。时间轴覆盖：{sampleTimeline?.status==='BLOCKED'?'不通过':sampleTimeline?.ready===true?'通过':'待检查'}。</p>
       {sampleTimeline?.error&&<GenerationResultError>{sampleTimeline.error}</GenerationResultError>}
       {sample.audition&&<p className="text-sm text-muted-foreground mb-0">原声试听首视频帧位于音频 {Number(sample.audition.video_first_in_audio_seconds).toFixed(6)} 秒。</p>}
       <p className="text-sm text-muted-foreground mb-0">口型、手势、切镜和音乐卡点仍需对照原片核验。</p>
      </div>
     </GenerationDisclosure>
    </>}
   
  </CardContent></Card>}

    <Card id="generation-settings" tabIndex="-1" className="generation-material-panel" aria-label="片段材料与提示词" role="region">
     <CardHeader><CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl">片段材料与提示词</h2></CardTitle></CardHeader>
     <CardContent className="generation-settings-block">
    {groups.length>0&&<div className="generation-settings-references">
     {groups.map(group=><section key={group.key} className="generation-reference-group">
      
       <span className="font-medium text-sm">{group.title}</span>
       <div className="generation-settings-reference-images">
        {group.references.map((reference,index)=>{
         const asset=project.assets?.find(item=>item.id===reference.asset_id),href=reference.url||assetUrl(asset);
         return <a key={reference.asset_id+'-'+index} href={href} target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline text-sm">
          <img src={asset?.thumbnail_url||href} alt={reference.reference+' · '+(reference.role_name||reference.name||'参考图')} loading="lazy"/>
          <Badge variant={"secondary"}>{reference.reference}</Badge>
         </a>;
        })}
       </div>
      
     </section>)}
    </div>}
    {!showAction&&renderAdditionalProblems()}
    {configured&&!state.localAdaptationOnly&&hasAvailableResults&&<UiButton type="button" className="generation-settings-prepare" disabled={apiStartBlocked} onClick={()=>prepare('api')} variant="outline" size="default">重新准备生成</UiButton>}
    <MaterialPromptEditor generationUi project={project} segment={segment} prompt={prompt} setPrompt={setPrompt}
     dirty={dirty} save={save} reset={reset} onPatch={onPatch} onAction={onAction}
     planFingerprint={state.fingerprint} busy={pending||state.preparing}/>
    {PreparationLinks&&project.preparation&&<PreparationLinks project={project} generationUi/>}
    </CardContent></Card>
   </div>
   <aside ref={inspectorRef} id="generation-inspector" className="generation-inspector" aria-label="本次生成设置与资料" hidden={!inspectorOpen}>

  <Card className="generation-summary generation-action-card" aria-label="生成设置摘要" role="region"><CardHeader>
      <CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl">生成设置</h2></CardTitle>
      <CardDescription>{taskCount} 个片段 · 共 {Number(inputSeconds).toFixed(2)} 秒</CardDescription>
    </CardHeader><CardContent>
   
    
    <FieldGroup className="generation-scope">
     <GenerationSelect label="本次用途" ariaLabel="本次生成用途" value={purpose} disabled={pending||state.preparing} onValueChange={choosePurpose}
      options={[[ 'strict_final','严格成片候选'],['candidate_sample','候选小样 · 仅供对照']]}/>
     {project.segments.length>1&&<GenerationSelect label="生成范围" ariaLabel="本次生成范围" value={range} disabled={pending||state.preparing} onValueChange={setRange}
      options={[[ 'current',`仅当前片段 ${segment?.name||segment?.id||''}`.trim()],['all','全部片段']]}/>}
     <GenerationSelect label="原声参考" ariaLabel="是否上传原声参考" value={String(includeSourceAudio)} disabled={pending||state.preparing}
      onValueChange={value=>setIncludeSourceAudio(value==='true')} options={[[ 'false','不上传原声参考'],['true','上传同时间原声参考']]}/>
    </FieldGroup>

    <Alert aria-label="智能时长覆盖风险" data-tone={"warning"} role="note">
     <AlertCircle size={17} aria-hidden="true"/>
     <AlertTitle>智能时长覆盖风险</AlertTitle>
     <AlertDescription className="generation-action-stack">
      <p>智能时长无法锁定帧数。回片须逐段核对实际帧数、时长与时间戳。</p>
      {durationWarnings.length>0&&<ul className="generation-duration-warnings">{durationWarnings.map(({segment_id,message})=><li key={segment_id}>{message}</li>)}</ul>}
     </AlertDescription>
    </Alert>

    <Separator decorative />
    <GenerationDisclosure id="generation-model-settings" className="generation-settings-panel"
     label={<span className="flex items-center gap-2"><Settings aria-hidden="true"/><span>模型与 API</span></span>}
     contentClassName="generation-settings-body">
   <section className="generation-settings-block" aria-label="模型与原片参数">
    <p className="text-sm font-medium mb-0">{MODELS[settings.model]||settings.model||'Wan 3.0'} · {settings.resolution||'交付档位待选'}</p>
    <UiButton type="button" disabled={pending||state.preparing} onClick={()=>onStep?.(0,'delivery-spec')} variant="outline">查看本批交付规格</UiButton>
    <p className="text-sm text-muted-foreground mb-0">保留原声 · 无水印。模型声音关闭，导出时接回连续原声。</p>
    {callEvidence?.succeeded?<Badge variant="success">本批次调用已成功 · {callEvidence.model} · {callEvidence.region_label||'原任务地域'}</Badge>
     :configured&&<Badge variant="secondary">API Key 已配置 · 尚未验证本次调用</Badge>}
    <div className="generation-model-settings">
     <GenerationSelect label="视频模型" value={settings.model||'wan3.0-video'} disabled={pending||state.preparing}
      onValueChange={model=>onPatch({settings:{...settings,model,watermark:false,audio:false}})}
      options={[[ 'wan3.0-video','阿里万相 Wan 3.0' ],[ 'wan3.0-video-prime','阿里万相 Wan 3.0 Prime' ]]}/>
     <UiButton type="button" disabled={pending} onClick={onConfig} variant="outline" size="default">API 设置</UiButton>
    </div>
    <dl className="generation-settings-facts">
     <div><dt className="text-sm text-muted-foreground">原片尺寸</dt><dd>{project.source_meta?.width||'—'} × {project.source_meta?.height||'—'}</dd></div>
     <div><dt className="text-sm text-muted-foreground">原片帧率</dt><dd>{project.source_meta?.fps_rational||project.source_meta?.fps||'—'}</dd></div>
     <div><dt className="text-sm text-muted-foreground">画面比例</dt><dd>随原片</dd></div>
    </dl>
    <GenerationDisclosure label="原声参考与上传说明">
     <p className="text-sm text-muted-foreground">关闭模型输出声音（audio=false）与上传原声参考是两项设置。上传 WAV 按归零参考视频的实际源PTS对齐，试听另保留原片音画偏移。这份参考 WAV 不用于最终回贴，也不保证自动修复口型。旧授权不会追加音频。</p>
     {state.sampleOnly&&<p className="text-sm text-muted-foreground">本次仅准备 {selectedIds.join('、')}。候选档位为 {settings.resolution||'待选择'}。原片 {project.source_meta?.width}×{project.source_meta?.height} 保持原尺寸与原时间轴。小样用于对照预览。</p>}
    </GenerationDisclosure>
    <p className="text-sm text-muted-foreground mb-0">固定时长仅支持整数秒，智能时长由模型决定。</p>
    <GenerationDisclosure label="业务空间 ID 与 Key 记录编号怎么区分">
     <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground mb-0">API Key 列表中的“ID”是密钥记录编号，不等于 Workspace ID。请复制同页显示的空间专属 Base URL，或从业务空间详情复制业务空间 ID。</p>
      <p className="text-sm text-muted-foreground mb-0">可在 API 设置的“官方 API 地址”中粘贴完整专属 Base URL。工具会从主机名提取空间与地域，将 /compatible-mode/v1 换为 Wan3 使用的 /api/v1。业务空间 ID 留空时可直接从专属地址识别；如果手填 ID 与地址不同，请核对后修正。</p>
      <p className="text-sm text-muted-foreground mb-0">现有 Key 留空沿用，无需重新输入。不会按数字外观判断空间 ID 无效；地址格式通过也不表示鉴权成功。接口、Key 与模型须为同一地域。</p>
      <a href="https://help.aliyun.com/zh/model-studio/obtain-the-app-id-and-workspace-id" target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline text-sm underline">官方业务空间 ID 获取说明</a>
     </div>
    </GenerationDisclosure>
   </section>
    </GenerationDisclosure>
   </CardContent></Card>
    {packages.length>0&&<GenerationDisclosure className="generation-package-panel" label="已导出生成包">
   <section className="generation-settings-block" aria-label="已导出生成包">
    <ul className="generation-settings-packages">
     {packages.map(asset=><li key={asset.id}>
      <a href={assetUrl(asset)} download className="text-primary underline-offset-4 hover:underline text-sm underline">{asset.name}</a>
      <Badge variant={"secondary"}>{asset.id===state.packageAsset?.id&&!state.preparation.stale?'当前生成包':'历史生成包，请核对材料版本'}</Badge>
     </li>)}
    </ul>
   </section>
    </GenerationDisclosure>}
    <GenerationDisclosure label="更多工具">
    <nav className="generation-inspector-tools" aria-label="生成视频辅助工具">
     <GenerationActionButton className="generation-reference-tool" onClick={event=>onOpenSupport?.('reference',event)}><Video aria-hidden="true" data-icon="inline-start" />原片预览</GenerationActionButton>
     <GenerationActionButton onClick={event=>onOpenSupport?.('history',event)}>处理记录</GenerationActionButton>
     <GenerationActionButton onClick={event=>onOpenSupport?.('help',event)}>使用说明</GenerationActionButton>
    </nav>
    </GenerationDisclosure>
   </aside>
  </div>
  <Separator decorative />
  <footer className="generation-step-navigation" aria-label="当前步骤与下一步">
   <GenerationActionButton onClick={()=>onStep?.(2)}><ArrowLeft aria-hidden="true" data-icon="inline-start" />上一步</GenerationActionButton>
  </footer>
 </div>;
}
