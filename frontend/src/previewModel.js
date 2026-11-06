const urlOf = asset => typeof asset === 'string' ? asset : asset?.url || '';
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

export function frameRate(value) {
  if (typeof value === 'string' && value.includes('/')) {
    const [n, d] = value.split('/').map(Number);
    return n > 0 && d > 0 ? n / d : 30;
  }
  return finite(value, 30) > 0 ? finite(value, 30) : 30;
}

export function sourceWindow(segment, offset = 0, fps = 30) {
  const start = finite(segment?.start) + finite(offset);
  const end = Math.max(start, finite(segment?.end, start - finite(offset)) + finite(offset));
  return {start, end, last: Math.max(start, end - 1 / frameRate(fps)), duration: end - start};
}

export function clampToWindow(value, window) {
  return Math.max(window.start, Math.min(window.last, finite(value, window.start)));
}

export function durationCheck(expected, actual, fps = 30) {
  const tolerance = 1 / frameRate(fps) + .003;
  const readable = Number.isFinite(actual) && actual > 0;
  return {readable, tolerance, mismatch: readable && Math.abs(actual - expected) > tolerance};
}

export function comparisonTimes(side, time, window) {
  const source = clampToWindow(side === 'source' ? time : window.start + time, window);
  return {source, clip: Math.max(0, source - window.start)};
}

// The final displayed frame is before the selection end. Keep completion as
// explicit state, rather than trying to infer it from the stopped time.
export function selectionPlayback(window) {
  let ended = false;
  const requestedSeeks = new WeakMap();
  const seek = (video, time) => {
    if (!video || !(video.readyState >= 1) || !Number.isFinite(time) || Math.abs(video.currentTime-time) <= .015) return false;
    try {
      requestedSeeks.set(video, time);
      video.currentTime = time;
      return true;
    } catch {
      requestedSeeks.delete(video);
      return false;
    }
  };
  return {
    seek,
    observeSeek(video) {
      const requested = requestedSeeks.get(video);
      const internal = Number.isFinite(requested) && Math.abs(requested-video.currentTime) <= .02;
      requestedSeeks.delete(video);
      if (!internal) ended = false;
      return internal;
    },
    finish(source, clip, completed = true) {
      ended = completed;
      source?.pause(); clip?.pause();
      seek(source, window.last);
      seek(clip, Math.max(0, window.last-window.start));
    },
    restartOnPlay(source, clip) {
      if (!ended) return false;
      ended = false;
      seek(source, window.start); seek(clip, 0);
      return true;
    },
    isEnded: () => ended,
  };
}

export function previewModel(project, segment, mode = 'puppet') {
  const replacement = project.workflow === 'replacement';
  const source = urlOf(project.source);
  const reference = urlOf(segment?.reference);
  const generated = urlOf(segment?.generated);
  const fps = frameRate(project.source_meta?.fps_rational || project.source_meta?.fps);
  const offset = finite(project.source_meta?.video_start_time);
  const window = sourceWindow(segment, offset, fps);
  const full = mode === 'original';
  const compare = mode === 'compare';
  const candidate = mode === 'generated';
  const regionOnly = mode === 'segment-source' || (!full && !candidate && !compare && !reference && !!segment);
  const currentUrl = full || regionOnly || compare ? source : candidate ? generated : reference;
  const prepared = !!reference && (!replacement || segment?.reference?.kind === 'source_reference');
  const stale = replacement && Object.hasOwn(project, 'preparation') && !project.preparation && !!reference;
  const isSubmissionFile = !full && !regionOnly && !candidate && prepared && !stale;
  const asset = full || regionOnly ? project.source : candidate ? segment?.generated : segment?.reference;
  const recordedDuration = Number(asset?.meta?.video_duration_seconds ?? asset?.meta?.duration_seconds);
  const sourceDuration = finite(project.source_meta?.video_duration_seconds ?? project.source_meta?.duration);
  return {
    mode, replacement, source, reference, generated, currentUrl, fps, offset, window,
    full, compare, candidate, regionOnly, prepared, stale, isSubmissionFile,
    bounded: regionOnly || compare,
    initial: full ? offset : regionOnly || compare ? window.start : 0,
    expectedDuration: window.duration,
    recordedDuration: Number.isFinite(recordedDuration) && recordedDuration > 0 ? recordedDuration : null,
    sourceDuration,
    filename: typeof asset === 'object' ? asset?.name || '' : '',
    heading: full ? '原片全片 · 不作为提交文件'
      : compare ? '原片选区与参考文件对照'
      : candidate ? '当前片段候选 · 待核对'
      : regionOnly ? '原片区间预览 · 尚未准备提交文件'
      : stale ? '上次准备的片段 · 准备记录待更新'
      : replacement ? '本次提交片段' : '人偶运动参考',
  };
}
