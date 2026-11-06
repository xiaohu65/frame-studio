import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';

import { Button as UiButton } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card';
import { Badge } from './ui/badge';
import { Checkbox } from './ui/checkbox';
import { Spinner } from './ui/spinner';
import { Separator } from './ui/separator';

import {ArrowLeft,ArrowRight,PanelRightClose,PanelRightOpen,Video} from 'lucide-react';
import {time} from '../api';
import {resultDraftKey,readResultDraft,writeResultDraft,clearResultDraft,resultEvidence,trackResultSubmission,reconcileResultSubmissions,captureResultRecheck,trackResultRecheck,reconcileResultRechecks,resultDraftRevision} from '../resultPresentation';
import {ResultNotice,ResultSelect,ResultDisclosure} from './ResultControls';
import {ResultReceipt,ResultTechnicalCheck} from './ResultReceive';
import {revealGenerationDisclosures} from './GenerationControls';
import {resultWorkflowStatus} from '../workflowStatus';
import './ResultStage.css';
import ResultExport from './ResultExport';
import CandidateComparison from './ResultComparison';

const RUNNING=['QUEUED','RUNNING','PROCESSING'];
function focusReview(){const node=document.getElementById('candidate-review');node?.scrollIntoView({block:'start',behavior:'smooth'});node?.focus({preventScroll:true});}
function focusResultSection(id){
 const node=document.getElementById(id);
 if(!node)return;
 const focus=()=>{if(!node.isConnected)return;node.scrollIntoView({block:'start',behavior:'instant'});node.focus({preventScroll:true});};
 if(revealGenerationDisclosures(node))requestAnimationFrame(focus);
 else focus();
}
export default function ResultStage({project,segment,resultStates,runs=[],materialConfirmation,onSelectSegment,onStep,onAction,onUpload,onImport,jobs=[],busy=false,error='',dirtyDraftTargets=[],checksDefinition,onOpenSupport,navigationRequest}){
 const [inspectorOpen,setInspectorOpen]=useState(true);
 const inspectorRef=useRef(null),inspectorToggle=useRef(null),appliedNavigation=useRef(null);
 const workflowStatus=resultWorkflowStatus(project,{result_states:resultStates,material_confirmation:materialConfirmation},{selectedSegmentId:segment?.id});
 const state=workflowStatus.segmentState,whole=workflowStatus.projectState;
 const evidence=resultEvidence(segment,state,runs);
 const key=resultDraftKey(project.id,segment,state);
 const [,redrawDraft]=useState(0);
 const draft=readResultDraft(key);
 const checks=draft.checks,editing=draft.editing;
 const count=checksDefinition.filter(([name])=>checks[name]).length;
 const [request,setRequest]=useState(''),requestLock=useRef(false),[trackedJob,setTrackedJob]=useState(null);
 const currentJobs=jobs.filter(job=>job.segment_id===segment?.id&&['validate_candidate','review_candidate'].includes(job.action));
 const running=currentJobs.find(job=>RUNNING.includes(job.status));
 const projectRunning=jobs.find(job=>['assemble','extract_audio'].includes(job.action)&&RUNNING.includes(job.status));
 const locked=!!busy||!!request||!!running||!!projectRunning;
 const lastCurrentJob=currentJobs.find(job=>(!job.input_material_fingerprint||job.input_material_fingerprint===materialConfirmation?.fingerprint)&&(!job.input_candidate_id||job.input_candidate_id===segment?.generated?.id));
 const lastFailure=lastCurrentJob?.status==='FAILED'?lastCurrentJob:null;
 const lastProjectJob=jobs.find(job=>['assemble','extract_audio'].includes(job.action));
 const projectFailure=lastProjectJob?.status==='FAILED'?lastProjectJob:null;
 const next=whole.pending.find(item=>item.segment.id!==segment?.id);
 const reviewOpen=state.validationCurrent&&(!state.reviewed||editing);
 useEffect(()=>{if(inspectorRef.current)inspectorRef.current.scrollTop=0;},[segment?.id]);
 useEffect(()=>{
  if(workflowStatus.loading||!navigationRequest||navigationRequest.step!==4||appliedNavigation.current===navigationRequest)return;
  if(navigationRequest.segmentId&&navigationRequest.segmentId!==segment?.id)return;
  const targetId=navigationRequest.targetId;
  const node=targetId&&document.getElementById(targetId);
  if(!node)return;
  if(inspectorRef.current?.contains(node))setInspectorOpen(true);
  const frame=requestAnimationFrame(()=>{
   if(!document.getElementById(targetId))return;
   appliedNavigation.current=navigationRequest;
   focusResultSection(targetId);
  });
  return()=>cancelAnimationFrame(frame);
 },[navigationRequest,project.id,segment?.id,workflowStatus.loading,state.candidateCurrent,state.validationCurrent]);
 useLayoutEffect(()=>{
  const saved=reconcileResultSubmissions(jobs);
  const cleared=reconcileResultRechecks(jobs,{project,resultStates,materialConfirmation}).find(item=>item.key===key);
  if(saved.includes(key)||cleared&&resultDraftRevision(key)===cleared.revision)redrawDraft(value=>value+1);
 },[jobs,key,project,resultStates,materialConfirmation,request]);
 useEffect(()=>{
  if(!trackedJob)return;
  const job=jobs.find(item=>item.id===trackedJob.id);
  if(job?.status==='SUCCEEDED'){
   if(trackedJob.action==='validate_candidate'&&job.result?.report?.ready===true&&trackedJob.key===key){
    if(!state.validationCurrent)return;
    requestAnimationFrame(focusReview);
   }
   setTrackedJob(null);
  }else if(job&&['FAILED','UNKNOWN'].includes(job.status))setTrackedJob(null);
 },[jobs,trackedJob,key,state.validationCurrent]);
 function updateDraft(value){const nextValue={...draft,...value};writeResultDraft(key,nextValue);redrawDraft(value=>value+1);}
 async function act(action,extra={}){
  if(locked||requestLock.current)return;
  requestLock.current=true;setRequest(action);
  const selected=segment?.id,submittedKey=key;
  const recheck=action==='validate_candidate'?captureResultRecheck(project,segment,state,materialConfirmation?.fingerprint):null;
  try{const response=await onAction(action,extra);if(response?.job_id){if(recheck)trackResultRecheck(recheck,response.job_id);if(action==='review_candidate')trackResultSubmission(submittedKey,response.job_id);else setTrackedJob({id:response.job_id,action,segmentId:selected,key:submittedKey});}return response;}
  finally{requestLock.current=false;setRequest('');}
 }
 function revealSection(id){
  if(inspectorRef.current?.contains(document.getElementById(id))){setInspectorOpen(true);requestAnimationFrame(()=>focusResultSection(id));}
  else focusResultSection(id);
 }
 function closeInspector(){
  if(inspectorRef.current?.contains(document.activeElement))inspectorToggle.current?.focus();
  setInspectorOpen(false);
 }
 function switchSegment(id){onSelectSegment?.(id);setInspectorOpen(true);requestAnimationFrame(()=>focusResultSection('candidate-import'));}
 if(workflowStatus.loading)return <p role="status" className="text-muted-foreground result-stage">{workflowStatus.label}</p>;
 return <div className="result-stage" data-testid="replacement-export-stage">
  <div className="result-stage-toolbar">
   <div className="result-review-context">
    {whole.segments.length>1?<ResultSelect label="验收片段" ariaLabel="选择验收片段" className="result-material-picker" value={segment?.id||''} onValueChange={switchSegment}
     disabled={!!request||!whole.segments.length} placeholder="尚无分段"
     options={whole.segments.map(({segment: item,label})=>[item.id,`${item.name||item.id} · ${label}`])}/>:<>
     <span className="text-sm font-medium">{segment?.name||segment?.id||'尚无分段'}</span>
     {segment&&<span className="text-sm text-muted-foreground tabular-nums">{time(segment.start)} — {time(segment.end)}</span>}
    </>}
    <span className="text-sm text-muted-foreground" role="status">已验收 {whole.completed} / {whole.segments.length} 段</span>
   </div>
   <UiButton ref={inspectorToggle} type="button" aria-expanded={inspectorOpen} aria-controls="result-inspector" onClick={()=>inspectorOpen?closeInspector():setInspectorOpen(true)} variant={"outline"} size="default">
    {inspectorOpen?<PanelRightClose aria-hidden="true" data-icon="inline-start" />:<PanelRightOpen aria-hidden="true" data-icon="inline-start" />}
    {inspectorOpen?'收起结果资料':'查看结果资料'}
   </UiButton>
  </div>
  <div className="result-work-grid" data-inspector-open={inspectorOpen}>
   <div className="result-main">
  {dirtyDraftTargets.length>0&&<ResultNotice tone="warning" className="result-draft-note">
   <span role="status" className="text-sm">有未保存修改，本页处理已保存版本。</span>
   <div className="flex flex-wrap gap-3">
    {dirtyDraftTargets.map((target,index)=><UiButton type="button" key={index} onClick={()=>onStep?.(target.step,target.targetId,target)} variant={"outline"} size="default">{target.label}</UiButton>)}
   </div>
  </ResultNotice>}
  {error&&<ResultNotice tone="destructive" role="alert">{error}</ResultNotice>}
  {!state.hasResult&&lastFailure&&<ResultNotice tone="destructive" role="alert">{lastFailure.message||'当前段处理未完成，请查看处理记录。'}</ResultNotice>}
  {materialConfirmation?.current!==true&&<ResultNotice tone="warning">
   <span className="text-sm">当前版本材料尚未确认。</span>
   <UiButton type="button" onClick={()=>onStep?.(3,'generation-material-confirmation')} variant={"outline"} size="default">去确认人物、参考图与范围</UiButton>
  </ResultNotice>}
  {!state.candidateCurrent&&<Card className="result-panel result-comparison-empty" aria-label="等待本段原始回片" role="region"><CardHeader><CardTitle><h2 className="font-semibold leading-snug tracking-tight text-xl mb-0">对照验收</h2></CardTitle></CardHeader><CardContent>
   
    
    <p className="text-sm text-muted-foreground mb-0">接收本段原始回片后，核对画面并完成六项验收。</p>
    <div className="flex"><UiButton type="button" onClick={()=>revealSection('candidate-import')} variant={"outline"} size="default">查看本段结果文件<ArrowRight aria-hidden="true" data-icon="inline-start" /></UiButton></div>
   
  </CardContent></Card>}
  {state.candidateCurrent&&<Card className="result-panel result-review" id={state.validationCurrent?'candidate-review':'candidate-preview'} tabIndex="-1" aria-labelledby="result-review-title" role="region"><CardHeader><div>
     <div className="flex items-center justify-between gap-3">
      <CardTitle><h2 id="result-review-title" className="font-semibold leading-snug tracking-tight text-xl mb-0">{state.validationCurrent?'对照验收':'原始回片对照预览'}</h2></CardTitle>
      {reviewOpen&&<Badge role="status" variant={"warning"}>勾选中 {count} / 6 · 尚未保存</Badge>}
     </div>
     {!state.validationCurrent&&<CardDescription className="mt-2 mb-0">技术检查通过后可逐项验收。</CardDescription>}
    </div></CardHeader><CardContent>
   
    
    <CandidateComparison key={`${segment.id}:${segment.generated.id}`} project={project} segment={segment} evidence={evidence}/>
    {state.validationCurrent&&<Separator decorative />}
    {state.validationCurrent&&(state.reviewed&&!editing?<div className="result-review-saved">
     <Badge role="status" variant={"success"}>本段验收已保存</Badge>
     <div className="flex items-center gap-3">
      <UiButton type="button" disabled={locked} onClick={()=>updateDraft({checks:{},editing:true})} variant={"outline"} size="default">重新验收</UiButton>
      {next&&<UiButton type="button" onClick={()=>switchSegment(next.segment.id)} size="default">
       处理下一段：{next.segment.name||next.segment.id}<ArrowRight aria-hidden="true" data-icon="inline-start" />
      </UiButton>}
     </div>
    </div>:<div className="result-review-form">
     {state.reviewed&&editing&&<ResultNotice>正在重新验收，保存前保留上次验收记录。</ResultNotice>}
     <fieldset className="result-review-checks" disabled={locked} aria-label="六项效果验收">
      {checksDefinition.map(([name,label])=><label data-checked={!!checks[name]} key={name} className="text-sm result-review-option">
       <Checkbox aria-label={`候选复核${label}`} checked={!!checks[name]} disabled={locked} onCheckedChange={checked=>updateDraft({checks:{...checks,[name]:checked===true},editing:true})} />
       <span>{label}</span>
      </label>)}
     </fieldset>
     <div className="flex items-center justify-end gap-3">
      {state.reviewed&&editing&&<UiButton type="button" disabled={locked} onClick={()=>{clearResultDraft(key);redrawDraft(value=>value+1);}} variant={"outline"} size="default">取消重新验收</UiButton>}
      <UiButton type="button" onClick={()=>act('review_candidate',{approved:true,checks,fingerprint:state.candidate_fingerprint})} size="default" disabled={Boolean(request==='review_candidate'||running?.action==='review_candidate') || (locked||count!==6||!state.candidate_fingerprint||materialConfirmation?.current!==true)} aria-busy={(request==='review_candidate'||running?.action==='review_candidate') || undefined}>{(request==='review_candidate'||running?.action==='review_candidate') && <Spinner data-icon="inline-start" aria-hidden="true" />}保存本段验收</UiButton>
     </div>
    </div>)}
   
  </CardContent></Card>}
  <ResultExport project={project} whole={whole} reviewOpen={reviewOpen} reviewingExisting={state.reviewed&&editing}
   locked={locked} assembling={request==='assemble'||projectRunning?.action==='assemble'}
   projectFailure={projectFailure} projectRunning={projectRunning} onSelectSegment={switchSegment}
   onExport={()=>act('assemble',{segment_id:null})} onExtractAudio={()=>act('extract_audio')}/>
   </div>
   <aside ref={inspectorRef} id="result-inspector" className="result-inspector" aria-label="本段结果文件与技术检查" hidden={!inspectorOpen}>
    <ResultReceipt segment={segment} state={state} evidence={evidence} locked={locked}
     onStep={onStep} onUpload={onUpload} onImport={onImport}/>
    {state.candidateCurrent&&<ResultTechnicalCheck key={key} segment={segment} state={state} evidence={evidence}
     request={request} running={running} lastFailure={lastFailure} locked={locked} onCheck={()=>act('validate_candidate')} onReview={focusReview}/>}
    <ResultDisclosure label="更多工具">
    <nav className="result-inspector-tools" aria-label="结果与导出辅助工具">
     <UiButton type="button" className="result-reference-tool" onClick={event=>onOpenSupport?.('reference',event)} variant={"outline"} size="default"><Video aria-hidden="true" data-icon="inline-start" />原片预览</UiButton>
     <UiButton type="button" onClick={event=>onOpenSupport?.('history',event)} variant={"outline"} size="default">处理记录</UiButton>
     <UiButton type="button" onClick={event=>onOpenSupport?.('help',event)} variant={"outline"} size="default">使用说明</UiButton>
    </nav>
    </ResultDisclosure>
   </aside>
  </div>
  <Separator decorative />
  <footer className="result-step-navigation" aria-label="当前步骤与下一步">
   <UiButton type="button" onClick={()=>onStep?.(3)} variant="outline"><ArrowLeft aria-hidden="true" data-icon="inline-start" />返回生成视频</UiButton>
  </footer>
 </div>;
}
