import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceRangePreviewModel} from '../src/sourceRangePreviewModel.js';

const project={id:'source-project',workflow:'replacement',preparation:{fingerprint:'saved'},source:{url:'/original.mp4',name:'原片.mp4'},source_meta:{duration:28.486792,video_duration_seconds:99,fps_rational:'24000/1001',video_start_time:.041}};
const saved={id:'B',name:'已保存分段',start:336*1001/24000,end:683*1001/24000,reference:{kind:'source_reference',url:'/old-B.mp4',name:'旧分段.mp4'}};

test('draft range previews always use the original, even when a prepared reference exists',()=>{
 const draft={...saved,start:15,end:25};
 const model=sourceRangePreviewModel(project,draft,saved);
 assert.equal(model.sourceUrl,'/original.mp4');
 assert.notEqual(model.sourceUrl,saved.reference.url);
 assert.equal(model.sourceDuration,project.source_meta.duration,'range validation uses the same duration as the source editor');
 assert.equal(model.window.start,15.041);
 assert.equal(model.window.end,25.041);
 assert.equal(model.initial,model.window.start);
 assert.equal(model.full,false);
 assert.equal(model.rangeValid,true);
 assert.equal(model.playable,true);
 assert.equal(model.rangeChanged,true);
});

test('source offset and rational frame rate place the final displayed frame before the range end',()=>{
 const model=sourceRangePreviewModel(project,saved,saved);
 assert.equal(model.fps,24000/1001);
 assert.equal(model.offset,.041);
 assert.equal(model.window.start,saved.start+.041);
 assert.equal(model.window.end,saved.end+.041);
 assert.ok(Math.abs(model.window.last-(saved.end+.041-1001/24000))<1e-10);
 assert.ok(model.window.last<model.window.end);
 assert.equal(model.rangeChanged,false);
 assert.equal(model.referenceOutdated,false);
});

test('range validity matches the editor rules without accepting coerced or clamped input',()=>{
 for(const [start,end] of [[-1,10],[0,0],[8,7],[0,15.00002],[20,28.5],[NaN,10],[0,Infinity],['0',10],[0,'10'],[undefined,10],[0,null]]){
  const model=sourceRangePreviewModel(project,{...saved,start,end},saved);
  assert.equal(model.rangeValid,false,`invalid range ${String(start)}–${String(end)}`);
  assert.equal(model.playable,false);
 }
 assert.equal(sourceRangePreviewModel(project,null,saved).rangeValid,false);
 assert.equal(sourceRangePreviewModel(project,{...saved,start:0,end:15.00001},saved).rangeValid,true);
 assert.equal(sourceRangePreviewModel(project,{...saved,start:20,end:project.source_meta.duration+.001},saved).rangeValid,true);
 assert.equal(sourceRangePreviewModel(project,{...saved,start:20,end:project.source_meta.duration+.00101},saved).rangeValid,false);
});

test('full-source mode starts at the source offset and does not depend on draft range validity',()=>{
 const model=sourceRangePreviewModel(project,{...saved,start:20,end:10},saved,'original');
 assert.equal(model.full,true);
 assert.equal(model.initial,.041);
 assert.equal(model.rangeValid,false);
 assert.equal(model.playable,true);
 assert.equal(sourceRangePreviewModel({...project,source_meta:{...project.source_meta,duration:0}},saved,saved,'original').playable,false);
 assert.equal(sourceRangePreviewModel({...project,source:null},saved,saved,'original').playable,false);
 assert.equal(sourceRangePreviewModel({...project,source:null},saved,saved).playable,false);
 assert.equal(sourceRangePreviewModel({...project,source:'/string-source.mp4'},saved,saved).sourceUrl,'/string-source.mp4');
});

test('unsaved range edits make historical references outdated independently of saved preparation state',()=>{
 assert.ok(project.preparation);
 assert.equal(sourceRangePreviewModel(project,{...saved,end:saved.end-.1},saved).referenceOutdated,true);
 assert.equal(sourceRangePreviewModel({...project,preparation:null},saved,saved).referenceOutdated,true);
 const {preparation,...withoutPreparation}=project;
 assert.equal(sourceRangePreviewModel(withoutPreparation,saved,saved).referenceOutdated,false);
 assert.equal(sourceRangePreviewModel({...project,workflow:'legacy',preparation:null},saved,saved).referenceOutdated,false);
 assert.equal(sourceRangePreviewModel(project,saved,{...saved,reference:null}).referenceOutdated,false);
 assert.equal(sourceRangePreviewModel(project,saved,undefined).rangeChanged,true);
});

test('playback identity changes for media, range, mode, and timing, while names do not affect it',()=>{
 const base=sourceRangePreviewModel(project,saved,saved).identity;
 assert.equal(sourceRangePreviewModel(project,{...saved,name:'重命名'},saved).identity,base);
 for(const [nextProject,nextDraft,nextMode] of [
  [{...project,id:'another-project'},saved,'range'],
  [{...project,source:{url:'/another-source.mp4'}},saved,'range'],
  [project,{...saved,id:'C'},'range'],
  [project,{...saved,start:saved.start+.1},'range'],
  [project,{...saved,end:saved.end-.1},'range'],
  [project,saved,'original'],
  [{...project,source_meta:{...project.source_meta,video_start_time:.082}},saved,'range'],
  [{...project,source_meta:{...project.source_meta,fps_rational:'25/1'}},saved,'range'],
  [{...project,source_meta:{...project.source_meta,duration:29}},saved,'range'],
 ])assert.notEqual(sourceRangePreviewModel(nextProject,nextDraft,saved,nextMode).identity,base);
});

test('building previews preserves the original project, saved segment, and unsaved draft precision',()=>{
 const draft={...saved,start:14.014000000001,end:28.486791666667};
 const before=structuredClone({project,saved,draft});
 Object.freeze(draft);
 Object.freeze(saved);
 Object.freeze(project);
 sourceRangePreviewModel(project,draft,saved);
 sourceRangePreviewModel(project,draft,saved,'original');
 assert.deepEqual({project,saved,draft},before);
 assert.equal(draft.start,14.014000000001);
 assert.equal(draft.end,28.486791666667);
});
