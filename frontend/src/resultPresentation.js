// Result acceptance remains separate from input-material confirmation.
export const ORIGINAL_DELIVERY_POLICY='ORIGINAL_CANDIDATE_STREAM_COPY_V1';
const reviewDrafts=new Map();
const draftRevisions=new Map();
const comparisonNotes=new Map();
export function resultDraftRevision(key){return draftRevisions.get(key)||0;}
function bumpDraftRevision(key){const revision=resultDraftRevision(key)+1;draftRevisions.set(key,revision);return revision;}
export function resultDraftKey(projectId,segment,state={}){
 return [projectId,segment?.id,segment?.generated?.id,state.deliveryPolicy||'legacy',state.candidate_fingerprint||segment?.candidate_validation?.fingerprint||'unchecked'].join(':');
}
export function readResultDraft(key){return reviewDrafts.get(key)||{checks:{},editing:false};}
export function writeResultDraft(key,value){bumpDraftRevision(key);reviewDrafts.set(key,{checks:{...value.checks},editing:!!value.editing});}
export function clearResultDraft(key){bumpDraftRevision(key);reviewDrafts.delete(key);}
export function resultSegmentState(segment={},states){
 const loaded=!!states?.segments&&Object.hasOwn(states.segments,segment.id);
 const record=states?.segments?.[segment.id]||{};
 const deliveryPolicy=states?.delivery_policy;
 const hasResult=!!segment.generated;
 const candidateCurrent=deliveryPolicy===ORIGINAL_DELIVERY_POLICY&&!!segment.generated?.id&&record.candidate_current===true&&
  record.candidate_asset_id===segment.generated.id&&!record.candidate_stale&&!segment.candidate_stale;
 const validationCurrent=candidateCurrent&&record.validation_current===true&&!!record.candidate_fingerprint;
 const reviewed=validationCurrent&&record.review_current===true;
 const label=!loaded?'正在读取状态':!hasResult?'待导入':!candidateCurrent?'结果待更新':reviewed?'已验收':validationCurrent?'待验收':'待检查';
 return {...record,deliveryPolicy,loaded,hasResult,candidateCurrent,validationCurrent,reviewed,label};
}
export function resultProjectState(project,states,materialConfirmation){
 const segments=(project.segments||[]).map(segment=>({segment,...resultSegmentState(segment,states)}));
 const pending=segments.filter(item=>!item.reviewed);
 const allReviewed=segments.length>0&&!pending.length;
 return {segments,pending,completed:segments.length-pending.length,
  exportReady:allReviewed&&materialConfirmation?.current===true,
  finalCurrent:!!project.final&&allReviewed&&states?.final_current===true};
}
export function resultSource(segment){
 if(!segment?.generated)return '';
 if(segment.candidate_adaptation?.asset_id===segment.generated.id)return '历史适配结果';
 if(segment.generation_provenance?.asset_id===segment.generated.id)return 'API 自动接入';
 return '手动导入';
}

export function readComparisonNotes(key){return comparisonNotes.get(key)||'';}
export function writeComparisonNotes(key,notes){if(notes)comparisonNotes.set(key,notes);else comparisonNotes.delete(key);}

function rationalNumber(value){
 const [numerator,denominator=1]=String(value??'').split('/').map(Number);
 return denominator?numerator/denominator:NaN;
}

export function resultEvidence(segment={},state={},runs=[]){
 const assetId=segment.generated?.id,provenance=segment.generation_provenance;
 const receipt=assetId&&provenance?.asset_id===assetId&&runs.find(run=>run.id===provenance.run_id)?.segments?.find(item=>item.segment_id===segment.id&&item.asset?.id===assetId);
 const validation=segment.candidate_validation;
 const report=state.candidateCurrent&&validation?.candidate_asset_id===assetId&&validation?.fingerprint===state.candidate_fingerprint?validation:null;
 const inspection=receipt?.return_inspection;
 const actual=inspection?.actual||report?.candidate?{...inspection?.actual,...report?.candidate}:null;
 const expected=report?.source?.frame_count??(state.candidateCurrent?inspection?.target?.frame_count:undefined);
 const delta=Number.isInteger(expected)&&Number.isInteger(actual?.frame_count)?actual.frame_count-expected:null;
 let coverage=report?.coverage??(state.candidateCurrent?inspection?.coverage:undefined);
 // Older checked runs recorded the selected first PTS and certified the raw CFR grid.
 // Derive a tail location only if the source bounds and both frame counts agree.
 const source=report?.source,rate=rationalNumber(source?.fps_rational),first=source?.first_frame;
 const origin=source?.video_start_time??source?.video_start_seconds;
 const selected=inspection?.target?.selected_video_first_pts_seconds;
 if(!coverage&&delta<0&&source?.constant_frame_rate===true&&actual?.cfr_pts_certified===true&&rate>0&&
    rationalNumber(actual.fps_rational)===rate&&Number.isInteger(first)&&source.last_frame-first+1===expected&&
    inspection.target?.frame_count===expected&&Number.isFinite(selected)&&Number.isFinite(origin)){
  const start=selected-origin,firstMissing=actual.frame_count/rate,lastMissing=(expected-1)/rate;
  coverage={estimated:true,missing_frame_count:-delta,missing_ranges:[{source_first_frame:first+actual.frame_count,
   source_last_frame:source.last_frame,source_first_seconds:start+firstMissing,source_last_seconds:start+lastMissing,
   segment_first_seconds:firstMissing,segment_last_seconds:lastMissing}]};
 }
 const review=segment.candidate_review;
 const notes=[...(review?.fingerprint===state.candidate_fingerprint?[review?.notes]:[]),...(Array.isArray(report?.observations)?report.observations:[])]
  .filter(note=>typeof note==='string'&&note.trim());
 const precheckIssues=state.candidateCurrent&&!report?inspection?.issues:undefined;
 return {report,actual,expected,delta,coverage,notes,precheckIssues,usage:receipt?.usage};
}

// A review may finish after the editor has unmounted or another segment is open.
const reviewSubmissions=new Map();
export function trackResultSubmission(key,jobId){reviewSubmissions.set(jobId,key);}
export function reconcileResultSubmissions(jobs=[]){
 const saved=[];
 for(const job of jobs){
  const key=reviewSubmissions.get(job.id);if(!key)continue;
  if(job.status==='SUCCEEDED'){clearResultDraft(key);saved.push(key);reviewSubmissions.delete(job.id);}
  else if(['FAILED','UNKNOWN'].includes(job.status))reviewSubmissions.delete(job.id);
 }
 return saved;
}

// Capture at request time, before obtaining a job id. Edits after this token
// belong to another draft revision even if the candidate fingerprint is equal.
const rechecks=new Map();
export function captureResultRecheck(project,segment,state,materialFingerprint){
 const draftKey=resultDraftKey(project.id,segment,state);
 return {projectId:project.id,segmentId:segment?.id,candidateId:segment?.generated?.id,
  candidateFingerprint:state?.candidate_fingerprint,materialFingerprint,draftKey,revision:resultDraftRevision(draftKey)};
}
export function trackResultRecheck(token,jobId){
 if(jobId&&token?.projectId&&token.segmentId&&token.candidateId&&token.candidateFingerprint&&token.materialFingerprint)rechecks.set(jobId,{...token});
}
export function reconcileResultRechecks(jobs=[],{project,resultStates,materialConfirmation}={}){
 const cleared=[];
 for(const job of jobs){
  const token=rechecks.get(job.id);
  if(!token||project?.id!==token.projectId)continue;
  if(['FAILED','CANCELED','CANCELLED'].includes(job.status)){rechecks.delete(job.id);continue;}
  // UNKNOWN remains tracked: later confirmation of this exact task may pass.
  if(job.status!=='SUCCEEDED')continue;
  const report=job.result?.report;
  if(report?.ready===false){rechecks.delete(job.id);continue;}
  if(report?.ready!==true)continue;
  const segment=project.segments?.find(item=>item.id===token.segmentId);
  const current=resultStates?.segments?.[token.segmentId];
  if(!current||!materialConfirmation?.fingerprint)continue;
  const state=resultSegmentState(segment,resultStates);
  rechecks.delete(job.id); // Consume once, including stale successful events.
  if(job.action!=='validate_candidate'||job.project_id!==token.projectId||job.segment_id!==token.segmentId||
     job.input_candidate_id!==token.candidateId||job.input_material_fingerprint!==token.materialFingerprint||
     report.segment_id!==token.segmentId||report.fingerprint!==token.candidateFingerprint||
     segment?.generated?.id!==token.candidateId||segment?.candidate_stale||
     materialConfirmation.fingerprint!==token.materialFingerprint||
     state.candidate_fingerprint!==token.candidateFingerprint||!state.validationCurrent||
     resultDraftKey(project.id,segment,state)!==token.draftKey||resultDraftRevision(token.draftKey)!==token.revision)continue;
  writeResultDraft(token.draftKey,{checks:{},editing:true});
  cleared.push({key:token.draftKey,revision:resultDraftRevision(token.draftKey)});
 }
 return cleared;
}
