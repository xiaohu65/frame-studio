import test from 'node:test';
import assert from 'node:assert/strict';
import {actorPresentation,groupActorObservations,peopleOverview} from '../src/peoplePresentation.js';

const segments=[{id:'A',name:'第一段',start:0,end:10},{id:'B',name:'第二段',start:10,end:20}];
const roles=[{id:'new-look',name:'新造型',images:[{id:'image',url:'/image.png'}]},{id:'empty-look',name:'待补图片',images:[]}];
const actor={id:'person',label:'原片人物',scope:'head',target_role_id:'new-look',source_description:'深色短袖，右手持物',observations:[]};

test('selecting a target is separate from confirming identity or recording positions',()=>{
 const summary=actorPresentation({...actor,observations:[{segment_id:'A',time:2,confirmed:false}]},roles,segments);
 assert.equal(summary.role,roles[0]);
 assert.equal(summary.mappingSelected,true);
 assert.equal(summary.mappingLabel,'已选择角色');
 assert.equal(summary.scopeLabel,'只换头部与发型');
 assert.equal(summary.referenceCount,1);
 assert.equal(summary.confirmed,0);
 assert.equal(summary.pending,1);
 assert.deepEqual(summary.missingSegments,[segments[1]]);
 assert.equal('approved' in summary,false);
});

test('only literal true counts as a confirmed position',()=>{
 const observations=[true,false,undefined,1,'true',null].map((confirmed,time)=>({segment_id:'A',time,confirmed}));
 const summary=actorPresentation({...actor,observations},roles,segments);
 assert.equal(summary.total,6);
 assert.equal(summary.confirmed,1);
 assert.equal(summary.pending,5);
 const group=groupActorObservations({...actor,observations},segments)[0];
 assert.equal(group.confirmed,1);
 assert.equal(group.pending,5);
 assert.deepEqual(group.rows.map(row=>row.index),[1,2,3,4,5,0]);
});

test('missing positions expose empty segments and actors without any observations',()=>{
 const summary=actorPresentation(actor,roles,segments);
 assert.equal(summary.total,0);
 assert.equal(summary.pending,0);
 assert.deepEqual(summary.missingSegments,segments);
 const groups=groupActorObservations(actor,segments);
 assert.equal(groups.length,2);
 assert.deepEqual(groups.map(group=>group.rows),[[],[]]);
 assert.equal(groups[0].segment,segments[0]);
 assert.equal(peopleOverview({source_actors:[actor],roles,segments}).missingPositions,1);
 assert.equal(peopleOverview({source_actors:[actor],roles,segments:[]}).missingPositions,1);
});

test('preserved actors retain existing target bindings, references, and pending location records',()=>{
 const preserved={...actor,scope:'preserve',source_description:'',observations:[{segment_id:'A',time:1,confirmed:false}]};
 const summary=actorPresentation(preserved,roles,segments);
 assert.equal(summary.preserve,true);
 assert.equal(summary.role,roles[0]);
 assert.equal(summary.mappingSelected,true);
 assert.equal(summary.mappingLabel,'保持原样');
 assert.equal(summary.scopeLabel,'保持原样');
 assert.equal(summary.referenceCount,1);
 assert.equal(summary.needsReference,false);
 assert.equal(summary.needsDescription,false);
 assert.equal(summary.pending,1);
 assert.deepEqual(summary.missingSegments,[segments[1]]);
 assert.equal(preserved.target_role_id,'new-look');
 assert.equal(preserved.observations.length,1);
 const overview=peopleOverview({source_actors:[preserved],roles,segments});
 assert.equal(overview.mapped,0);
 assert.equal(overview.preserved,1);
 assert.equal(overview.pending,1);
 assert.equal(overview.missingPositions,1);
});

test('reference and description prompts distinguish unselected roles from selected roles without images',()=>{
 const selected=actorPresentation({...actor,scope:'person',target_role_id:'empty-look',source_description:' \n '},roles,segments);
 assert.equal(selected.mappingSelected,true);
 assert.equal(selected.needsReference,true);
 assert.equal(selected.needsDescription,true);
 assert.equal(selected.scopeLabel,'整个人物外观');
 const missing=actorPresentation({...actor,target_role_id:'missing'},roles,segments);
 assert.equal(missing.mappingSelected,false);
 assert.equal(missing.mappingLabel,'待选择角色');
 assert.equal(missing.needsReference,false);
 assert.equal(missing.referenceCount,0);
});

test('groups prioritize pending records, then time and original index without losing unknown records',()=>{
 const observations=[
  {segment_id:'A',time:8,confirmed:true},
  {segment_id:'A',time:5,confirmed:false},
  {segment_id:'missing',time:4,confirmed:false},
  {segment_id:'A',time:2},
  {segment_id:'A',time:5,confirmed:false},
  {segment_id:'A',time:1,confirmed:true},
  {time:3,confirmed:true},
 ];
 const groups=groupActorObservations({...actor,observations},segments);
 assert.deepEqual(groups[0].rows.map(row=>row.index),[3,1,4,5,0]);
 assert.equal(groups[0].pending,3);
 assert.equal(groups[0].confirmed,2);
 assert.equal(groups[1].segment,segments[1]);
 assert.equal(groups[1].rows.length,0);
 assert.deepEqual(groups[2].segment,{id:'__unknown__',name:'待修正片段'});
 assert.deepEqual(groups[2].rows.map(row=>row.index),[2,6]);
 assert.equal(groups[2].pending,1);
 assert.equal(groups[2].confirmed,1);
 const allRows=groups.flatMap(group=>group.rows);
 assert.equal(allRows.length,observations.length);
 for(const row of allRows)assert.equal(row.observation,observations[row.index]);
 assert.deepEqual(groupActorObservations({...actor,observations},[])[0].rows.map(row=>row.index),[3,2,1,4,5,6,0]);
});

test('overview counts saved actors independently and never deduplicates reused target roles',()=>{
 const actors=[
  {...actor,id:'first',observations:[{segment_id:'A',time:1,confirmed:true},{segment_id:'B',time:11,confirmed:true}]},
  {...actor,id:'second',observations:[{segment_id:'A',time:3,confirmed:false}]},
  {...actor,id:'third',target_role_id:'empty-look',source_description:'',observations:[]},
  {...actor,id:'fourth',scope:'preserve',observations:[{segment_id:'A',time:2,confirmed:false}]},
  {...actor,id:'fifth',target_role_id:'missing',source_description:'',observations:[{segment_id:'A',time:4,confirmed:true},{segment_id:'B',time:12,confirmed:true}]},
 ];
 assert.deepEqual(peopleOverview({source_actors:actors,roles,segments}),{count:5,mapped:3,preserved:1,needsReference:1,pending:2,missingPositions:3,needsDescription:2});
 assert.equal(actors[0].target_role_id,actors[1].target_role_id);
 assert.equal(actors.length,5);
 assert.deepEqual(peopleOverview({}),{count:0,mapped:0,preserved:0,needsReference:0,pending:0,missingPositions:0,needsDescription:0});
});

test('presentation leaves frozen source records, ordering, and exact timestamps unchanged',()=>{
 const input={actor:{...actor,observations:[{segment_id:'B',time:12.345678912345,confirmed:true},{segment_id:'A',time:1.234567891234,confirmed:false}]},roles:structuredClone(roles),segments:structuredClone(segments)};
 const before=structuredClone(input);
 function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
 freeze(input);
 actorPresentation(input.actor,input.roles,input.segments);
 groupActorObservations(input.actor,input.segments);
 peopleOverview({source_actors:[input.actor],roles:input.roles,segments:input.segments});
 assert.deepEqual(input,before);
 assert.equal(input.actor.observations[0].time,12.345678912345);
});

test('shot groups use exact source frames, omit rounded boundary cuts and retain real single-frame shots',()=>{
 const rate=24000/1001,segments=[{id:'C',start:581/rate,end:767/rate}];
 const observations=[{segment_id:'C',time:581/rate,confirmed:false},{segment_id:'C',time:582/rate,confirmed:true},{segment_id:'C',time:600/rate,confirmed:false}];
 const analysis={cuts:[{frame:582,time:24.232542},{frame:583,time:24.232542},{frame:599,time:24.941583}]};
 const group=groupActorObservations({...actor,observations},segments,analysis,{fps_rational:'24000/1001'})[0];
 assert.deepEqual(group.shots.map(shot=>[shot.firstFrame,shot.lastFrame]),[[582,582],[583,598],[599,767]]);
 assert.deepEqual(group.shots.map(shot=>shot.rows.map(row=>row.index)),[[0],[1],[2]]);
 assert.deepEqual(group.shots.map(shot=>shot.pending),[1,0,1]);
 assert.equal(observations.length,3);
});
