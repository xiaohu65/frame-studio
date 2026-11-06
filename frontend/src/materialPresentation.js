const nonempty=value=>typeof value==='string'&&value.length>0;
const fileMatches=(input,segment)=>!!input&&!!segment.reference&&nonempty(segment.id)&&input.segment_id===segment.id&&nonempty(input.asset_id)&&nonempty(segment.reference.id)&&input.asset_id===segment.reference.id&&nonempty(input.sha256)&&nonempty(segment.reference.sha256)&&input.sha256===segment.reference.sha256;

export function groupMaterialIssues(plan){
 const groups=[{id:'identity',title:'人物与全剧身份',step:1,issues:[]},{id:'shots',title:'镜头与遮挡区间',step:1,issues:[]},{id:'range',title:'场景与像素保护范围',step:2,issues:[]},{id:'source',title:'原片、素材与模型参数',step:0,issues:[]}];
 const groupFor=issue=>{const code=issue.code||'';return /^SHOT_|OCCLUSION|ACTOR_OVERLAP/.test(code)?groups[1]:/MASK|POLYGON|SCENE_|REGION_|LOCATORS_ONLY/.test(code)?groups[2]:/ACTOR|IDENTITY|TARGET_|SERIES_|PERSON_REFERENCE/.test(code)?groups[0]:groups[3];};
 for(const issue of plan?.issues||[])groupFor(issue).issues.push(issue);
 for(const issue of plan?.wan_compatibility?.issues||[])groups[3].issues.push({...issue,code:issue.code||'WAN_COMPATIBILITY'});
 return groups.filter(group=>group.issues.length).map(group=>{
  const targetId=group.step===1?'identity-mapping':group.step===2?'mask-panel':'source-import';
  const issues=group.issues.map(issue=>({...issue,target:{step:group.step,targetId,segmentId:issue.segment_id??null,actorId:issue.actor_id??null}}));
  const blockerCount=issues.filter(issue=>issue.blocking).length;
  return {...group,targetId,issues,blockerCount,warningCount:issues.length-blockerCount};
 });
}

function estimateRecord(job,segments,latestAttempt){
 const result=job.result;
 const reports=result.reports||[],inputs=result.submission_inputs||[];
 const allSegmentsCovered=segments.length>0&&segments.every(segment=>reports.some(report=>report.segment_id===segment.id&&fileMatches(report.input_reference,segment))&&inputs.some(input=>fileMatches(input,segment)));
 const globalScope=job.segment_id===undefined||job.segment_id===null||job.segment_id==='';
 return {job,result,reports,submissionInputs:inputs,blockers:result.blockers||[],reservedYuan:Number.isFinite(result.total_reserved_yuan)?result.total_reserved_yuan:null,allSegmentsCovered,globalScope,complete:globalScope&&allSegmentsCovered&&result.estimate_complete===true,isLatestAttempt:job===latestAttempt};
}

export function materialState(project,jobs=[]){
 const segments=project.segments||[];
 const preparation=project.preparation||null;
 const plan=project.replacement_plan||preparation?.plan||null;
 const fingerprint=nonempty(plan?.fingerprint)?plan.fingerprint:null;
 const preparationExists=!!preparation;
 const prepared=preparationExists&&!!fingerprint&&preparation.fingerprint===fingerprint;
 const localReady=plan?.ready===true;
 const wanReady=plan?.wan_compatibility?.ready!==false;
 const approved=!!fingerprint&&project.generic_review?.approved===true&&project.generic_review.fingerprint===fingerprint;
 const preflightJobs=jobs.filter(job=>job.action==='preflight'&&(!job.project_id||job.project_id===project.id)).map((job,index)=>({job,index})).sort((a,b)=>(b.job.created_at||'').localeCompare(a.job.created_at||'')||a.index-b.index).map(item=>item.job);
 const latestPreflightAttempt=preflightJobs[0]||null;
 const currentPreflightJob=fingerprint?preflightJobs.find(job=>job.status==='SUCCEEDED'&&job.result?.plan?.fingerprint===fingerprint)||null:null;
 const currentPreflight=currentPreflightJob?estimateRecord(currentPreflightJob,segments,latestPreflightAttempt):null;
 const currentEstimate=currentPreflight?.complete?currentPreflight:null;
 const partialEstimate=currentPreflight&&!currentPreflight.complete?currentPreflight:null;
 const oldPreflights=preflightJobs.filter(job=>job.status==='SUCCEEDED'&&(!fingerprint||job.result?.plan?.fingerprint!==fingerprint)).map(job=>({job,result:job.result||null,label:'旧费用记录，仅供参考'}));
 const referencesBySegment=Object.fromEntries(segments.map(segment=>[segment.id,plan?.segment_plans?.[segment.id]?.references||plan?.references||[]]));
 const files=segments.map(segment=>{
  const asset=segment.reference||null;
  const input=currentPreflight?.submissionInputs.find(item=>item.segment_id===segment.id&&item.asset_id===asset?.id)||null;
  const idMatched=!!asset&&nonempty(asset.id)&&!!input;
  const verified=prepared&&fileMatches(input,segment);
  const exists=!!asset;
  const filePrepared=prepared&&exists;
  const state=!exists?'missing':!prepared?'outdated':verified?'verified':'prepared';
  const rawDuration=verified?input.duration_seconds:asset?.meta?.video_duration_seconds??asset?.meta?.duration_seconds;
  return {segment,asset,exists,prepared:filePrepared,needsUpdate:exists&&!prepared,idMatched,verified,state,input,durationSeconds:Number.isFinite(rawDuration)?rawDuration:null,references:referencesBySegment[segment.id],label:state==='missing'?'尚未制作':state==='outdated'?'旧参考 · 准备记录需要更新':state==='verified'?'当前文件已完成本次预检核对':'文件已制作 · 尚未完成当前预检'};
 });
 const groups=groupMaterialIssues(plan);
 const localBlockers=(plan?.issues||[]).filter(issue=>issue.blocking);
 const blockers=groups.flatMap(group=>group.issues).filter(issue=>issue.blocking);
 const warnings=groups.flatMap(group=>group.issues).filter(issue=>!issue.blocking);
 let status,title,primaryAction;
 if(!project.source){status='needs-source';title='先导入原片';primaryAction={kind:'navigate',step:0,targetId:'source-import',label:'导入原片'};}
 else if(!plan){status='unchecked';title='尚未检查当前材料';primaryAction={kind:'action',action:'replacement_check',segmentId:null,label:'检查已保存材料'};}
 else if(!localReady){status='issues';title=localBlockers.length?`本地材料有 ${localBlockers.length} 项待处理`:'材料检查尚未满足';primaryAction={kind:'action',action:'replacement_check',segmentId:null,label:'重新检查已保存材料'};}
 else if(!prepared){status='needs-preparation';title='分段文件需要制作或更新';primaryAction={kind:'action',action:'prepare_replacement',segmentId:null,label:'制作全部分段与定位预览'};}
 else if(!approved){status='needs-review';title='本地材料已检查，待人工核对';primaryAction={kind:'section',targetId:'material-review',label:'进行人工核对'};}
 else{status='reviewed';title='当前版本本地材料审核已记录';primaryAction={kind:'action',action:'package',segmentId:null,label:'导出本地素材包'};}
 return {plan,fingerprint,preparation,preparationExists,prepared,localReady,planReady:localReady,wanReady,wanChecked:!!plan?.wan_compatibility,approved,groups,localBlockers,blockers,warnings,referencesBySegment,files,latestPreflightAttempt,currentPreflight,currentEstimate,partialEstimate,oldPreflights,status,title,primaryAction,note:wanReady?'本地记录不授权上传或付费生成。':'Wan 兼容性仍有限制；本地核对与云端提交条件分别记录。'};
}
