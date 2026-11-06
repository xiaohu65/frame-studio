import test from 'node:test';
import assert from 'node:assert/strict';
import {previewModel, sourceWindow, clampToWindow, comparisonTimes, durationCheck,selectionPlayback} from '../src/previewModel.js';

const project = {id:'example', workflow:'replacement', preparation:{fingerprint:'prepared'},
  source:{url:'/original.mp4',name:'原片.mp4'}, source_meta:{duration:28.486792, fps_rational:'24000/1001',video_start_time:.041}};
const A = {id:'A', start:0, end:352*1001/24000, reference:{kind:'source_reference',url:'/actual-A.mp4',name:'A.mp4',meta:{video_duration_seconds:14.681333}}};
const B = {id:'B', start:336*1001/24000, end:683*1001/24000, reference:{kind:'source_reference',url:'/actual-B.mp4',name:'B.mp4',meta:{video_duration_seconds:14.472792}}};

test('A/B submit previews use the real short files, never the full source URL',()=>{
  for (const s of [A,B]) {
    const m=previewModel(project,s,'puppet');
    assert.equal(m.currentUrl,s.reference.url);
    assert.notEqual(m.currentUrl,project.source.url);
    assert.equal(m.initial,0); assert.equal(m.isSubmissionFile,true);
    assert.equal(m.heading,'本次提交片段');
    assert.ok(Math.abs(m.recordedDuration-(s.end-s.start))<1e-6);
  }
});
test('full source is explicitly unsubmitted and starts at its own first frame',()=>{
  const m=previewModel(project,B,'original');
  assert.equal(m.currentUrl,'/original.mp4'); assert.equal(m.initial,.041);
  assert.equal(m.bounded,false); assert.equal(m.isSubmissionFile,false);
  assert.match(m.heading,/全片.*不作为提交文件/);
});
test('without a prepared file, selection playback is labelled source interval only',()=>{
  const m=previewModel({...project,preparation:null},{...B,reference:null},'puppet');
  assert.equal(m.currentUrl,'/original.mp4'); assert.equal(m.bounded,true);
  assert.equal(m.initial,B.start+.041); assert.equal(m.isSubmissionFile,false);
  assert.match(m.heading,/尚未准备提交文件/);
});
test('input edits make an old reference visibly stale',()=>{
  const m=previewModel({...project,preparation:null},A,'puppet');
  assert.equal(m.stale,true);assert.equal(m.isSubmissionFile,false);
  assert.match(m.heading,/待更新/);
});
test('seek and stop boundaries use the final original frame, never the next segment',()=>{
  const w=sourceWindow(B,.041,24000/1001);
  assert.equal(clampToWindow(0,w),B.start+.041);
  assert.ok(Math.abs(clampToWindow(100,w)-(B.end+.041-1001/24000))<1e-10);
  assert.ok(clampToWindow(100,w)<w.end);
});
test('comparison seeking maps either player onto the same source frame',()=>{
  const w=sourceWindow(B,.041,24000/1001);
  assert.deepEqual(comparisonTimes('clip',2,w),comparisonTimes('source',w.start+2,w));
  assert.equal(comparisonTimes('clip',100,w).source,w.last);
});
test('browser metadata is checked independently of the saved file duration',()=>{
  assert.equal(durationCheck(A.end-A.start,28.486792,24000/1001).mismatch,true);
  assert.equal(durationCheck(A.end-A.start,14.681333,24000/1001).mismatch,false);
  assert.equal(durationCheck(A.end-A.start,NaN,24000/1001).readable,false);
});

const mediaAt = time => ({currentTime:time,readyState:1,paused:false,pause(){this.paused=true;}});
test('stopping on the last frame replays the selection from its beginning on the next play',()=>{
  const w=sourceWindow(B,.041,24000/1001),control=selectionPlayback(w);
  const source=mediaAt(w.end),clip=mediaAt(w.duration);
  control.finish(source,clip);
  assert.equal(source.paused,true);assert.equal(clip.paused,true);
  assert.equal(source.currentTime,w.last);
  assert.equal(clip.currentTime,w.last-w.start);
  assert.equal(control.observeSeek(source),true);
  assert.equal(control.observeSeek(clip),true);
  assert.equal(control.isEnded(),true,'internal final-frame seeks keep the completion state');
  assert.equal(control.restartOnPlay(source,clip),true);
  assert.equal(source.currentTime,w.start);assert.equal(clip.currentTime,0);
  assert.equal(control.isEnded(),false);
  assert.equal(control.restartOnPlay(source,clip),false,'restart is performed only once');
});
test('a deliberate seek after completion cancels automatic restart without moving the playhead',()=>{
  const w=sourceWindow(B,.041,24000/1001),control=selectionPlayback(w);
  const source=mediaAt(w.end),clip=mediaAt(w.duration);
  control.finish(source,clip);control.observeSeek(source);control.observeSeek(clip);
  source.currentTime=w.start+3;
  assert.equal(control.observeSeek(source),false);
  assert.equal(control.restartOnPlay(source,clip),false);
  assert.equal(source.currentTime,w.start+3);
});
test('seeking to a late frame is not mistaken for natural completion',()=>{
  const w=sourceWindow(B,.041,24000/1001),control=selectionPlayback(w);
  const source=mediaAt(w.last),clip=mediaAt(w.last-w.start);
  control.observeSeek(source);
  assert.equal(control.restartOnPlay(source,clip),false);
  assert.equal(source.currentTime,w.last);
  control.finish(source,clip,false);
  assert.equal(control.isEnded(),false,'clamping a manual out-of-range seek does not mark playback complete');
});
test('changing the selected media creates a fresh completion state',()=>{
  const w=sourceWindow(A,.041,24000/1001),old=selectionPlayback(w);
  old.finish(mediaAt(w.end),null);
  const next=selectionPlayback(sourceWindow(B,.041,24000/1001));
  assert.equal(old.isEnded(),true);assert.equal(next.isEnded(),false);
});
