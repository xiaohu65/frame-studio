const scopeLabels={head:'只换头部与发型',person:'整个人物外观',preserve:'保持原样'};
const isConfirmed=observation=>observation.confirmed===true;

export function actorPresentation(actor,roles=[],segments=[]){
 const role=roles.find(item=>item.id===actor.target_role_id);
 const preserve=actor.scope==='preserve';
 const observations=actor.observations||[];
 const total=observations.length;
 const confirmed=observations.filter(isConfirmed).length;
 const referenceCount=role?.images?.length||0;
 return {
  role,preserve,
  mappingSelected:preserve||!!role,
  mappingLabel:preserve?'保持原样':role?'已选择角色':'待选择角色',
  scopeLabel:scopeLabels[actor.scope]||scopeLabels.person,
  referenceCount,
  needsReference:!preserve&&!!role&&referenceCount===0,
  confirmed,total,pending:total-confirmed,
  missingSegments:segments.filter(segment=>!observations.some(observation=>observation.segment_id===segment.id)),
  needsDescription:!preserve&&!String(actor.source_description||'').trim(),
 };
}

function observationOrder(a,b){
 const confirmationOrder=Number(isConfirmed(a.observation))-Number(isConfirmed(b.observation));
 if(confirmationOrder)return confirmationOrder;
 const aTime=Number.isFinite(a.observation.time)?a.observation.time:Infinity;
 const bTime=Number.isFinite(b.observation.time)?b.observation.time:Infinity;
 return aTime-bTime||a.index-b.index;
}

export function groupActorObservations(actor,segments=[],analysis,meta={}){
 const rows=(actor.observations||[]).map((observation,index)=>({observation,index}));
 const group=(segment,items)=>{
  const confirmed=items.filter(row=>isConfirmed(row.observation)).length;
  const rate=meta.fps_rational?meta.fps_rational.split('/').map(Number).reduce((a,b)=>a/b):Number(meta.fps);
  const first=Math.round(segment.start*rate),stop=Math.round(segment.end*rate);
  const cuts=Number.isFinite(first)&&Number.isFinite(stop)?[...new Set((analysis?.cuts||[]).map(cut=>Number.isInteger(cut.frame1based??cut.frame)?(cut.frame1based??cut.frame)-1:Math.round(cut.time*rate)).filter(frame=>first<frame&&frame<stop))].sort((a,b)=>a-b):[];
  const bounds=[first,...cuts,stop];
  const shots=Number.isFinite(first)&&Number.isFinite(stop)?bounds.slice(0,-1).map((start,i)=>{
   const end=bounds[i+1],rows=items.filter(row=>start<=Math.round(row.observation.time*rate)&&Math.round(row.observation.time*rate)<end).sort(observationOrder);
   return {id:`${segment.id}-${start}`,firstFrame:start+1,lastFrame:end,start:start/rate,end:end/rate,rows,pending:rows.filter(row=>!isConfirmed(row.observation)).length,confirmed:rows.filter(row=>isConfirmed(row.observation)).length};
  }):[];
  // Invalid/out-of-range rows remain editable rather than disappearing.
  const unassigned=items.filter(row=>!shots.some(shot=>shot.rows.includes(row)));
  if(unassigned.length)shots.push({id:`${segment.id}-unassigned`,rows:unassigned.sort(observationOrder),pending:unassigned.filter(row=>!isConfirmed(row.observation)).length,confirmed:unassigned.filter(row=>isConfirmed(row.observation)).length});
  return {segment,rows:items.sort(observationOrder),pending:items.length-confirmed,confirmed,shots};
 };
 const groups=segments.map(segment=>group(segment,rows.filter(row=>row.observation.segment_id===segment.id)));
 const knownIds=new Set(segments.map(segment=>segment.id));
 const unknown=rows.filter(row=>!knownIds.has(row.observation.segment_id));
 if(unknown.length)groups.push(group({id:'__unknown__',name:'待修正片段'},unknown));
 return groups;
}

export function peopleOverview(project){
 const actors=project.source_actors||[];
 const presentations=actors.map(actor=>actorPresentation(actor,project.roles||[],project.segments||[]));
 return {
  count:actors.length,
  mapped:presentations.filter(item=>!item.preserve&&item.role).length,
  preserved:presentations.filter(item=>item.preserve).length,
  needsReference:presentations.filter(item=>item.needsReference).length,
  pending:presentations.reduce((total,item)=>total+item.pending,0),
  missingPositions:presentations.filter(item=>item.missingSegments.length>0||item.total===0).length,
  needsDescription:presentations.filter(item=>item.needsDescription).length,
 };
}
