import test from 'node:test';
import assert from 'node:assert/strict';
import {comparisonTiming,rawResultComparisonTiming,sourceToPreview,auditionToCandidate,shouldCorrectPlayback,pauseOtherSamplePlayback} from '../src/candidateComparisonTiming.js';

test('raw result comparison uses checked source bounds and native candidate origin',()=>{
 const timing=rawResultComparisonTiming({video_start_time:.041,audio_start_time:0,has_audio:true},
  {start:2.011,end:3.996,generated:{meta:{video_start_time:.02}},candidate_validation:{
   source:{source_start:2,source_end:4,video_start_seconds:.041},candidate:{video_start_time:.08},
  }});
 assert.equal(timing.sourcePlaybackStart,2);
 assert.equal(timing.sourceVideoStart,2.041);
 assert.equal(timing.duration,2);
 assert.equal(sourceToPreview(2,timing).active,false);
 assert.equal(sourceToPreview(2.041,timing).time,.08);
 assert.ok(Math.abs(sourceToPreview(2.051,timing).time-.09)<1e-12);
 assert.equal(sourceToPreview(timing.sourceEnd,timing).ended,true);
});

test('raw comparison prefers selected decoded PTS over the requested cut and container origin',()=>{
 const timing=rawResultComparisonTiming({video_start_time:.041,audio_start_time:0,has_audio:true},
  {start:2.011,end:4,generated:{meta:{video_start_time:.02}},candidate_validation:{
   source:{source_start:2.011,source_end:4,video_start_seconds:.041,first_pts_rational:'2083/1000',decoded_duration_seconds:2},
   candidate:{video_start_time:.08,first_pts_rational:'9/100'},
  }});
 assert.ok(Math.abs(timing.sourcePlaybackStart-2.042)<1e-12);
 assert.equal(timing.sourceVideoStart,2.083);
 assert.equal(timing.duration,2);
 assert.equal(sourceToPreview(2.083,timing).time,.09);
});

test('a short raw return exposes its missing picture while the original timeline continues',()=>{
 const timing=rawResultComparisonTiming({has_audio:true},{start:0,end:301/30},
  {video_start_time:0,decoded_duration_seconds:10});
 assert.equal(sourceToPreview(299/30,timing).active,true);
 assert.deepEqual(sourceToPreview(10,timing),{time:10,position:10,missing:true,active:false,ended:false});
 const tail=sourceToPreview(timing.sourceEnd,timing);
 assert.equal(tail.time,10);
 assert.equal(tail.position,301/30);
 assert.equal(tail.ended,true);
});

test('original audio before a 41ms first video PTS keeps preroll and exact small seeks',()=>{
 const timing=comparisonTiming({video_start_time:.041,audio_start_time:0},{start:0,end:250*1001/24000});
 assert.equal(timing.sourcePlaybackStart,0);
 assert.equal(timing.sourceVideoStart,.041);
 assert.equal(sourceToPreview(0,timing).active,false);
 assert.equal(sourceToPreview(.041,timing).time,0);
 assert.ok(Math.abs(sourceToPreview(.051,timing).time-.01)<1e-12,'a 10ms move is retained, not swallowed by a 70ms threshold');
 assert.equal(sourceToPreview(timing.sourceEnd,timing).ended,true);
});

test('source and derived preview origins and selected frame start are independent',()=>{
 const timing=comparisonTiming({video_start_time:3,audio_start_time:3.041},{start:10,end:12},
  {timeline:{source_first_pts:3,actual_first_frame_seconds:10.01},duration_seconds:2.002,media:{video_start_time:7}});
 assert.equal(timing.sourcePlaybackStart,13.01);
 assert.equal(timing.sourceVideoStart,13.01);
 assert.equal(sourceToPreview(13.01,timing).time,7);
 assert.ok(Math.abs(sourceToPreview(13.02,timing).time-7.01)<1e-12);
 const zero=comparisonTiming({video_start_time:0,audio_start_time:0},{start:2,end:4});
 assert.equal(sourceToPreview(2,zero).time,0);
});

test('audition is anchored to source preroll and the raw candidate PTS without granting complete coverage',()=>{
 const report={video_first_in_audio_seconds:.041,candidate_first_pts_seconds:7,visual_coverage_seconds:10.4};
 assert.equal(auditionToCandidate(.02,report).active,false);
 assert.equal(auditionToCandidate(.041,report).time,7);
 assert.ok(Math.abs(auditionToCandidate(.051,report).time-7.01)<1e-12);
 assert.equal(auditionToCandidate(10.442,report).active,false);
 assert.equal(auditionToCandidate(10.442,report).ended,true);
});

test('B starts at original frame 251 with the same 41ms original AV relationship',()=>{
 const start=250*1001/24000,duration=331*1001/24000;
 const timing=comparisonTiming({video_start_time:.041,audio_start_time:0},{start,end:start+duration},
  {timeline:{source_first_pts:.041,actual_first_frame_seconds:start},duration_seconds:duration,media:{video_start_time:0}});
 assert.equal(timing.sourcePlaybackStart,start);
 assert.equal(timing.sourceVideoStart,start+.041);
 assert.equal(sourceToPreview(start,timing).active,false);
 assert.equal(sourceToPreview(start+.041,timing).time,0);
 assert.ok(Math.abs(sourceToPreview(start+.051,timing).time-.01)<1e-12);
});

test('sub-frame seeks preserve their values for paused and playing comparisons',()=>{
 const timing=comparisonTiming({video_start_time:.041,audio_start_time:0},{start:10.427083333333,end:24.232541666666});
 const first=sourceToPreview(timing.sourceVideoStart+.001,timing),second=sourceToPreview(timing.sourceVideoStart+.003,timing);
 assert.ok(Math.abs(second.time-first.time-.002)<1e-12);
 assert.equal(first.active,true);
});

test('normal playback ignores millisecond noise and corrects only frame-scale drift',()=>{
 for(const fps of ['24000/1001','30/1']){
  for(let update=1;update<=20;update++){
   assert.equal(shouldCorrectPlayback({currentTime:update*.25-.006,targetTime:update*.25,playing:true,fps,now:update*250,lastCorrectionAt:0}),false);
  }
 }
 assert.equal(shouldCorrectPlayback({currentTime:1.97,targetTime:2,playing:true,fps:'30/1',now:2000,lastCorrectionAt:0}),false);
 assert.equal(shouldCorrectPlayback({currentTime:1.95,targetTime:2,playing:true,fps:'24000/1001',now:2000,lastCorrectionAt:0}),true);
});

test('playback corrections have a one-second cooldown and never interrupt an active seek',()=>{
 const drift={currentTime:1.9,targetTime:2,playing:true,fps:'30/1',lastCorrectionAt:1000};
 for(const now of [1001,1250,1500,1999])assert.equal(shouldCorrectPlayback({...drift,now}),false);
 assert.equal(shouldCorrectPlayback({...drift,now:2000}),true);
 assert.equal(shouldCorrectPlayback({...drift,now:3000,seeking:true}),false);
});

test('explicit small seeks and pause alignment bypass playback thresholds and cooldown',()=>{
 for(const playing of [false,true]){
  assert.equal(shouldCorrectPlayback({currentTime:2,targetTime:2.002,playing,force:true,seeking:true,now:1001,lastCorrectionAt:1000}),true);
 }
 assert.equal(shouldCorrectPlayback({currentTime:2,targetTime:2.002,playing:false,now:1001,lastCorrectionAt:1000}),true);
 assert.equal(shouldCorrectPlayback({currentTime:NaN,targetTime:2,force:true}),false);
});

test('the first visible frame after original-audio preroll aligns once without waiting for cooldown',()=>{
 const firstVisible={currentTime:0,targetTime:.209,playing:true,now:250,lastCorrectionAt:0,fps:'30/1'};
 assert.equal(shouldCorrectPlayback(firstVisible),false);
 assert.equal(shouldCorrectPlayback({...firstVisible,starting:true}),true);
 assert.equal(shouldCorrectPlayback({currentTime:.453,targetTime:.459,playing:true,now:500,lastCorrectionAt:250,fps:'30/1'}),false);
});

test('composite and raw observation use mutually exclusive original-audio playback',()=>{
 const player=()=>({paused:false,pauseCount:0,playbackRate:1,pause(){this.paused=true;this.pauseCount++;}});
 const source=player(),composite=player(),raw=player(),audio=player();
 pauseOtherSamplePlayback('composite',{source,composite,raw,audio});
 assert.equal(source.paused,false);assert.equal(composite.paused,false);assert.equal(raw.paused,true);assert.equal(audio.paused,true);
 pauseOtherSamplePlayback('composite',{source,composite,raw,audio});
 assert.equal(audio.pauseCount,1,'already paused original audio is not restarted or repeatedly controlled');
 pauseOtherSamplePlayback('raw',{source,composite,raw,audio});
 assert.equal(source.paused,true);assert.equal(composite.paused,true);
 for(const item of [source,composite,raw,audio])assert.equal(item.playbackRate,1);
});
