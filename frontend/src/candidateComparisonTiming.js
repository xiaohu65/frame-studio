// Presentation coordinates only; browser playback never certifies lip sync.
function number(value,fallback=0){return Number.isFinite(Number(value))?Number(value):fallback;}

function ptsSeconds(value){
 if(value===null||value===undefined||value==='')return undefined;
 const [numerator,denominator=1]=String(value).split('/').map(Number);
 const seconds=numerator/denominator;
 return Number.isFinite(seconds)?seconds:undefined;
}

export function comparisonTiming(meta={},segment={},report={}){
 meta=meta||{};segment=segment||{};report=report||{};
 const timeline=report?.timeline||{};
 const videoOrigin=number(timeline.source_first_pts,number(meta.video_start_time,number(meta.video_start_seconds)));
 const relativeStart=number(timeline.actual_first_frame_seconds,number(segment.start));
 const audioOrigin=meta.has_audio===false?videoOrigin:number(meta.audio_start_time,number(meta.audio_start_seconds,videoOrigin));
 const previewOrigin=number(report?.media?.video_start_time,number(report?.media?.video_start_seconds));
 const duration=number(report.duration_seconds,Math.max(0,number(segment.end)-number(segment.start)));
 return {sourceVideoStart:videoOrigin+relativeStart,sourcePlaybackStart:Math.min(videoOrigin,audioOrigin)+relativeStart,
  previewOrigin,duration,sourceEnd:videoOrigin+relativeStart+duration};
}

export function sourceToPreview(sourceTime,timing){
 const elapsed=number(sourceTime)-timing.sourceVideoStart;
 const position=Math.max(0,Math.min(timing.duration,elapsed));
 const coverage=Math.min(timing.duration,timing.candidateDuration??timing.duration);
 const ended=elapsed>=timing.duration;
 const missing=timing.duration-coverage>1e-6&&elapsed>=coverage-1e-9;
 return {time:timing.previewOrigin+Math.min(coverage,position),position,missing,
  active:elapsed>=0&&!ended&&!missing,ended};
}

export function rawResultComparisonTiming(sourceMeta={},segment={},inspectedCandidate){
 const report=segment.candidate_validation||{},source=report.source||{},candidate={...inspectedCandidate,...report.candidate},candidateMeta=segment.generated?.meta||{};
 const sourceOrigin=number(source.video_start_time??source.video_start_seconds??sourceMeta?.video_start_time??sourceMeta?.video_start_seconds);
 const selectedPts=ptsSeconds(source.first_pts_rational);
 const start=selectedPts===undefined?number(source.source_start??report.source_start,number(segment.start)):selectedPts-sourceOrigin;
 const end=number(source.source_end??report.source_end,number(segment.end));
 const candidateOrigin=ptsSeconds(candidate.first_pts_rational??inspectedCandidate?.first_pts_rational)??candidate.video_start_time??candidate.video_start_seconds??candidateMeta.video_start_time??candidateMeta.video_start_seconds;
 const timing=comparisonTiming(sourceMeta,{start,end},{
  timeline:{source_first_pts:sourceOrigin,actual_first_frame_seconds:start},
  media:{video_start_time:candidateOrigin},
  duration_seconds:source.decoded_duration_seconds??undefined,
 });
 const duration=candidate.decoded_duration_seconds??candidate.video_duration_seconds??candidateMeta.video_duration_seconds;
 return Number.isFinite(duration)&&duration>0?{...timing,candidateDuration:duration}:timing;
}

export function auditionToCandidate(audioTime,report={}){
 const offset=number(report.video_first_in_audio_seconds),elapsed=number(audioTime)-offset;
 const coverage=number(report.visual_coverage_seconds);
 return {time:number(report.candidate_first_pts_seconds)+Math.max(0,Math.min(coverage,elapsed)),
  active:elapsed>=0&&elapsed<coverage,ended:elapsed>=coverage};
}

export function shouldCorrectPlayback({currentTime,targetTime,playing=false,force=false,starting=false,seeking=false,fps=30,now=0,lastCorrectionAt=-Infinity}){
 if(!Number.isFinite(currentTime)||!Number.isFinite(targetTime))return false;
 // Explicit play, pause and seek use exact coordinates, including small moves.
 if(force||playing&&starting)return true;
 if(seeking)return false;
 const difference=Math.abs(currentTime-targetTime);
 if(!playing)return difference>1e-6;
 const [numerator,denominator=1]=String(fps).split('/').map(Number);
 const rate=numerator/denominator;
 const frameSeconds=Number.isFinite(rate)&&rate>=1&&rate<=120?1/rate:1/30;
 // Do not chase normal startup/clock noise or repeatedly interrupt decoding.
 return difference>frameSeconds&&now-lastCorrectionAt>=1000;
}

export function pauseOtherSamplePlayback(mode,{source,composite,raw,audio}={}){
 const inactive=mode==='composite'?[raw,audio]:[source,composite];
 for(const player of inactive)if(player&&!player.paused)player.pause();
}
