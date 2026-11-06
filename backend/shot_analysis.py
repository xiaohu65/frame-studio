"""Local shot-boundary candidates on the original decoded frame timeline.

Scene-score peaks can also be whip pans, flashes or large foreground motion.
They are review prompts, never an automatic shot/identity assignment.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

from . import media


_FRAME = re.compile(r'^frame:(\d+)\s+pts:\S+\s+pts_time:([-+0-9.eE]+)')
_SCORE = re.compile(r'^lavfi\.scene_score=([-+0-9.eE]+)')


def frame_boundary(seconds, rate):
    """Normalize a display timestamp onto the exact native frame grid."""
    return round(Fraction(str(seconds)) * Fraction(rate))


def shot_intervals(project, segment):
    """Half-open zero-based source-frame ranges; cut frame wins over rounded PTS."""
    rate = Fraction(project.get('source_meta', {}).get('fps_rational') or str(project['source_meta']['fps']))
    first, stop = (frame_boundary(segment[key], rate) for key in ('start', 'end'))
    analysis = project.get('source_analysis') or {}
    cuts = set()
    if analysis.get('source_sha256') == (project.get('source') or {}).get('sha256'):
        for cut in analysis.get('cuts', []):
            frame = cut.get('frame1based', cut.get('frame'))
            index = frame-1 if isinstance(frame, int) and not isinstance(frame, bool) else frame_boundary(cut['time'], rate)
            if first < index < stop:
                cuts.add(index)
    boundaries = [first, *sorted(cuts), stop]
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if a < b]


def analyze(source, threshold=.30):
    """Return hard-cut candidates with one-based original frame numbers.

    Every source frame is evaluated. The 320px-wide analysis copy only makes
    scoring cheaper; neither the original file nor its temporal order changes.
    Times use each decoded frame's PTS, normalized to the first video frame.
    """
    source = media._file(source)
    if isinstance(threshold, bool):
        raise ValueError('镜头切换阈值须为0到1之间的数字')
    threshold = float(threshold)
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError('镜头切换阈值须大于0且不超过1')
    meta = media.probe(source)
    if not meta['has_video']:
        raise media.MediaError('镜头分析需要原片视频轨')
    digest = media._hash(source)
    # gte(scene,0) evaluates scene score and retains every frame, so metadata's
    # frame index is still the original source's decode/display frame index.
    output = media._run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-copyts', '-i', source,
        '-map', '0:v:0', '-vf', 'scale=320:-2,select=gte(scene\\,0),metadata=mode=print:key=lavfi.scene_score:file=-',
        '-an', '-fps_mode', 'passthrough', '-f', 'null', '/dev/null'], timeout=1800)
    records = []
    current = None
    for line in output.splitlines():
        match = _FRAME.match(line)
        if match:
            current = {'frame': int(match[1])+1, 'pts': float(match[2])}
            continue
        match = _SCORE.match(line)
        if match and current is not None:
            score = float(match[1])
            if not math.isfinite(score) or not math.isfinite(current['pts']):
                raise media.MediaError('镜头分析出现无效时间戳或分数')
            records.append({**current, 'score': score})
            current = None
    if not records or any(record['frame'] != i+1 for i, record in enumerate(records)):
        raise media.MediaError('无法完整读取原片逐帧镜头分数；未推断镜头边界')
    if media._hash(source) != digest:
        raise media.MediaError('镜头分析期间原片文件发生变化，请重新导入后检查')
    first_pts = records[0]['pts']
    cuts = [{'frame': record['frame'], 'time': round(record['pts']-first_pts, 9), 'score': record['score']}
            for record in records[1:] if record['score'] >= threshold]
    return {'source_sha256': digest, 'cuts': cuts, 'method': 'FFMPEG_SCENE_SCORE_CANDIDATES',
            'threshold': threshold, 'requires_review': True, 'frame_count': len(records),
            'source_first_pts': first_pts, 'frame_numbering': 'SOURCE_ONE_BASED',
            'time_origin': 'FIRST_VIDEO_FRAME', 'analysis_width': 320,
            'automatic_identity_assignment': False, 'source_modified': False,
            'limitations': ['甩镜、闪光和高速前景运动可能产生误报，必须人工核对。',
                           '缓慢转场或相近画面的切镜可能漏检；没有候选不等于没有切镜。',
                           '候选边界两侧的人物身份需分别确认，不能自动跨边界插值或交换角色。']}
