import {frameRate,sourceWindow} from './previewModel.js';

const urlOf=asset=>typeof asset==='string'?asset:asset?.url||'';
const finiteOrZero=value=>Number.isFinite(Number(value))?Number(value):0;

export function sourceRangePreviewModel(project,draftSegment,savedSegment,mode='range'){
 const sourceUrl=urlOf(project.source);
 const full=mode==='original';
 const fps=frameRate(project.source_meta?.fps_rational||project.source_meta?.fps);
 const offset=finiteOrZero(project.source_meta?.video_start_time);
 // Use the same duration and limits as the source-stage save validation.
 const sourceDuration=project.source_meta?.duration||0;
 const window=sourceWindow(draftSegment,offset,fps);
 const rangeValid=!!draftSegment&&Number.isFinite(draftSegment.start)&&Number.isFinite(draftSegment.end)&&draftSegment.start>=0&&draftSegment.end>draftSegment.start&&draftSegment.end-draftSegment.start<=15.00001&&draftSegment.end<=sourceDuration+.001;
 const playable=!!sourceUrl&&(full?sourceDuration>0:rangeValid);
 const rangeChanged=!savedSegment||draftSegment?.start!==savedSegment.start||draftSegment?.end!==savedSegment.end;
 const preparationOutdated=project.workflow==='replacement'&&Object.hasOwn(project,'preparation')&&!project.preparation;
 const referenceOutdated=!!savedSegment?.reference&&(preparationOutdated||rangeChanged);
 const identity=JSON.stringify([project.id,sourceUrl,draftSegment?.id,mode,draftSegment?.start,draftSegment?.end,offset,fps,sourceDuration]);
 return {sourceUrl,full,fps,offset,sourceDuration,window,initial:full?offset:window.start,rangeValid,playable,rangeChanged,referenceOutdated,identity};
}
