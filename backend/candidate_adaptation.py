"""Offline, lossless sampling of a 30fps candidate on the original frame timeline.

This module never changes the source or masks and never authorizes generation.
All sampling comparisons use exact rational timestamps; floats are report fields
and the existing source-selection boundary convention only.
"""
from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import tempfile

from . import media, replacement_media as rm

SOURCE_RATE = Fraction(24000, 1001)
CANDIDATE_RATE = Fraction(30)
METHOD = 'NEAREST_PRESENTATION_TIMESTAMP_TIES_EARLIER_V1'


def _floor(value):
    return value.numerator // value.denominator


def _ceil(value):
    return -((-value.numerator) // value.denominator)


def _on_quantized_grid(value, expected, tick):
    """Only the two representable ticks bracketing the ideal CFR time qualify."""
    scaled = expected / tick
    return _floor(scaled) * tick <= value <= _ceil(scaled) * tick


def _timestamps(path, required_rate, label):
    path = media._file(path)
    meta = media.probe(path)
    try:
        rate = Fraction(meta['fps_rational'])
    except (ValueError, ZeroDivisionError, KeyError):
        raise media.MediaError(label + '帧率无效') from None
    if not meta.get('has_video') or rate != required_rate:
        raise media.MediaError(label + f'只支持精确恒定 {required_rate}fps')
    data = json.loads(media._run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_frames', '-show_streams', '-show_entries',
        'frame=best_effort_timestamp,pts,pkt_duration,duration:stream=time_base,start_time',
        '-of', 'json', path], timeout=180))
    try:
        tick = Fraction(data['streams'][0]['time_base'])
        if tick <= 0:
            raise ValueError()
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        raise media.MediaError(label + '缺少有效时间基准') from None
    frames = data.get('frames', [])
    if not frames:
        raise media.MediaError(label + '没有可解码的画面帧')
    points = []
    for frame in frames:
        # pts is preferred so decoder best-effort repair cannot hide a missing PTS.
        try:
            value = frame['pts']
            if value == 'N/A':
                raise ValueError()
            points.append(int(value) * tick)
        except (KeyError, TypeError, ValueError):
            raise media.MediaError(label + '有缺失时间戳，不能适配') from None
    first_pts = points[0]
    points = [point - first_pts for point in points]
    period = 1 / rate
    if any(b <= a for a, b in zip(points, points[1:])):
        raise media.MediaError(label + '时间戳不严格单调，不能适配')
    if any(not _on_quantized_grid(point, i * period, tick) for i, point in enumerate(points)):
        raise media.MediaError(label + '不是支持的恒定帧率或有累计时间戳漂移')
    # Coarse clocks are reliable only when they represent every frame period
    # exactly. Otherwise a short 24fps sequence can masquerade as 24000/1001
    # inside the floor/ceil envelope. Millisecond Matroska quantization remains
    # supported; this is a clock-certification limit, not endpoint leniency.
    if tick > period or ((period / tick).denominator != 1 and tick > Fraction(1, 1000)):
        raise media.MediaError(label + '时间基准过粗，无法认证恒定帧率')
    duration_ticks = frames[-1].get('duration') or frames[-1].get('pkt_duration')
    if duration_ticks is None:
        # Some codecs omit durations entirely; a certified complete CFR sequence
        # has one terminal frame period. This is inference, recorded explicitly.
        terminal_duration = period
        duration_inferred = True
    else:
        try:
            terminal_duration = int(duration_ticks) * tick
        except (TypeError, ValueError):
            raise media.MediaError(label + '末帧时长时间戳无效') from None
        duration_inferred = False
        if terminal_duration <= 0 or not _on_quantized_grid(terminal_duration, period, tick):
            raise media.MediaError(label + '末帧时长异常，不能用末帧延长内容')
    return {'path': str(path), 'meta': meta, 'points': points, 'first_pts': first_pts,
            'tick': tick, 'rate': rate, 'period': period, 'frame_count': len(points),
            'end': points[-1] + terminal_duration, 'duration_inferred': duration_inferred,
            'terminal_duration': terminal_duration}


def _nearest(points, target):
    right = bisect_left(points, target)
    if right == 0:
        return 0
    if right == len(points):
        return right - 1
    left = right - 1
    return left if target - points[left] <= points[right] - target else right


def inspect_candidate_return(source, candidate, source_start=0, source_end=None, *, resolution=None, output_size=None):
    """File evidence only. Provider usage and COMPLETED never approve a result."""
    actual = media.probe(candidate)
    report = {'status': 'RAW_CANDIDATE_RECEIVED', 'candidate_sha256': media._hash(candidate),
        'source_sha256': media._hash(source), 'actual': actual, 'usage_used_for_validation': False,
        'pts_checked': False, 'timeline_adaptation': {'status': 'NOT_CHECKED', 'ready': False},
        'raw_strict_compatible': False, 'strict_composite_ready': False, 'actual_review': 'UNREVIEWED',
        'final_approved': False, 'issues': []}
    try:
        proposed = _timestamps(candidate, CANDIDATE_RATE, '候选')
        report.update(pts_checked=True, actual={**actual, 'frame_count': proposed['frame_count'],
            'decoded_duration_seconds': float(proposed['end']), 'first_pts_rational': str(proposed['first_pts']),
            'last_pts_rational': str(proposed['points'][-1] + proposed['first_pts']),
            'cfr_pts_certified': True})
        original = rm._timing(source)
        first, stop, _, _ = rm._selection(original, source_start, source_end)
        target = Fraction(stop - first) / Fraction(original['fps_rational'])
        report['target'] = {'frame_count': stop - first, 'fps_rational': original['fps_rational'],
            'duration_seconds': float(target), 'duration_rational': str(target),
            'selected_video_first_pts_seconds': original['first_pts'] + original['points'][first]}
        report['audition_visual_coverage_seconds'] = float(min(target, proposed['end']))
        if resolution and min(actual['width'], actual['height']) != {'480P': 480, '720P': 720, '1080P': 1080}[resolution]:
            raise media.MediaError('实际候选尺寸与授权档位不符')
        plan = plan_adaptation(source, candidate, source_start, source_end, sample_only=True)
        report['timeline_adaptation'] = {'status': 'PLAN_PASS', 'ready': True, 'method': plan['method'],
            'endpoints': plan['endpoints'], 'spatial_mapping': plan['spatial_mapping'],
            'max_sampling_error_seconds': plan['max_sampling_error_seconds']}
        report['raw_strict_compatible'] = rm.validate_candidate(
            source, candidate, source_start, source_end, output_size=output_size)['ready']
    except Exception as error:
        report['issues'].append({'code': 'LOCAL_CANDIDATE_SPEC_OR_COVERAGE', 'message': str(error), 'blocking': True})
        report['timeline_adaptation'] = {'status': 'BLOCKED', 'ready': False, 'error': str(error)}
    if report['candidate_sha256'] != media._hash(candidate) or report['source_sha256'] != media._hash(source):
        report.update(pts_checked=False, timeline_adaptation={'status': 'BLOCKED', 'ready': False, 'error': '核验期间文件已变化'})
    return report


def plan_adaptation(source, candidate, source_start=0, source_end=None, *, sample_only=False):
    """Certify inputs and return the explicit, reproducible frame sampling plan.

    Endpoint policy has three independent conditions: the candidate frame count
    must be floor/ceil(target_duration*30); its decoded terminal PTS must be on
    the declared CFR grid; and no missing adjacent grid sample may be nearer to
    a target than the chosen decoded sample. A duration delta alone never passes.
    """
    source_sha256 = media._hash(media._file(source))
    candidate_sha256 = media._hash(media._file(candidate))
    source_rate = Fraction(media.probe(source)['fps_rational']) if sample_only else SOURCE_RATE
    if source_rate not in {SOURCE_RATE, CANDIDATE_RATE}:
        raise media.MediaError('候选小样时间轴适配目前只支持精确24000/1001或30fps原片')
    original = _timestamps(source, source_rate, '原片')
    proposed = _timestamps(candidate, CANDIDATE_RATE, '候选')
    sw, sh = original['meta']['width'], original['meta']['height']
    cw, ch = proposed['meta']['width'], proposed['meta']['height']
    if sample_only and sw * ch != sh * cw:
        raise media.MediaError('候选小样宽高比与原片不一致；不裁切或拉伸')
    if not sample_only and (sw, sh) != (cw, ch):
        raise media.MediaError('候选尺寸与原片不一致；本地适配不缩放或裁剪')
    selection_timing = {'points': [float(p) for p in original['points']], 'end': float(original['end'])}
    first, stop, requested_start, requested_end = rm._selection(selection_timing, source_start, source_end)
    count = stop - first
    duration = count / source_rate
    expected = duration * CANDIDATE_RATE
    permitted_counts = sorted({_floor(expected), _ceil(expected)})
    if proposed['frame_count'] not in permitted_counts:
        raise media.MediaError('候选存在实质覆盖不足或明显错长：帧数不属于目标时长的相邻30fps网格端点')
    period = proposed['period']
    nominal_end = proposed['frame_count'] * period
    endpoint_delta = nominal_end - duration
    # Actual container endpoint may be quantized by up to one stream tick;
    # neither it nor the nominal endpoint may exceed the one-frame hard bound.
    actual_delta = proposed['end'] - duration
    if abs(endpoint_delta) >= period or abs(actual_delta) > period:
        raise media.MediaError('候选首尾覆盖超出一个30fps帧的离散网格容差')
    mappings = []
    points = proposed['points']
    for index in range(first, stop):
        target = original['points'][index] - original['points'][first]
        selected = _nearest(points, target)
        sample = points[selected]
        error = abs(sample - target)
        # Check that the absent next ideal grid point would not win. Repeating
        # a final available sample when a closer next sample is missing is padding,
        # even if the total duration difference is less than 33.33ms.
        if target > points[-1] and nominal_end - target < error:
            raise media.MediaError('候选末端覆盖不足：目标需要尚不存在的更近30fps样本；不冻结末帧补齐')
        if error > period:
            raise media.MediaError('候选无法为目标时刻提供合法样本')
        mappings.append({'target_frame': index - first + 1, 'source_frame': index + 1,
            'candidate_frame': selected + 1, 'target_seconds': float(target),
            'candidate_seconds': float(sample), 'sampling_error_seconds': float(error),
            'target_time_rational': str(target), 'candidate_time_rational': str(sample),
            'sampling_error_rational': str(error)})
    maximum = max(Fraction(row['sampling_error_rational']) for row in mappings)
    if media._hash(source) != source_sha256 or media._hash(candidate) != candidate_sha256:
        raise media.MediaError('核验期间原片或候选发生变化，请重新适配')
    timeline = {'source_start': requested_start, 'source_end': requested_end,
        'first_frame': first + 1, 'last_frame': stop, 'frame_count': count,
        'actual_first_frame_seconds': float(original['points'][first]),
        'actual_end_seconds': float(original['points'][stop - 1] + original['period']),
        'source_first_pts': float(original['first_pts']),
        'actual_first_frame_time_rational': str(original['points'][first]),
        'actual_end_time_rational': str(original['points'][stop - 1] + original['period']),
        'fps_rational': str(source_rate)}
    return {'ready': True, 'status': 'LOCAL_ADAPTATION_PLAN_PASS', 'method': METHOD,
        'frame_count': count, 'fps_rational': str(source_rate), 'duration_seconds': float(duration),
        'sample_only': sample_only, 'strict_final_approved': False,
        'spatial_mapping': {'performed': (sw, sh) != (cw, ch), 'source_size': [sw, sh], 'candidate_native_size': [cw, ch],
            'derived_size': [sw, sh], 'scale_rational': str(Fraction(sw, cw)), 'cropped': False,
            'native_source_quality': (sw, sh) == (cw, ch), 'method': 'LANCZOS_EQUAL_ASPECT' if (sw, sh) != (cw, ch) else 'IDENTITY'},
        'target_duration_rational': str(duration), 'timeline': timeline, 'mapping': mappings,
        'max_sampling_error_seconds': float(maximum), 'max_sampling_error_rational': str(maximum),
        'first_frame_mapping': mappings[0], 'last_frame_mapping': mappings[-1],
        'source_sha256': source_sha256, 'candidate_sha256': candidate_sha256,
        'original_source': {'path': original['path'], 'fps_rational': str(source_rate),
            'frame_count': original['frame_count'],
            'duration_seconds': float(original['frame_count'] / source_rate),
            'duration_rational': str(original['frame_count'] / source_rate),
            'selected_frame_count': count, 'selected_duration_rational': str(duration)},
        'original_candidate': {'path': proposed['path'], 'fps_rational': str(CANDIDATE_RATE),
            'frame_count': proposed['frame_count'], 'duration_seconds': float(nominal_end),
            'duration_rational': str(nominal_end), 'actual_pts_end_seconds': float(proposed['end'])},
        'endpoints': {'tolerance_seconds': float(period), 'tolerance_rational': str(period),
            'permitted_candidate_frame_counts': permitted_counts,
            'nominal_end_difference_seconds': float(endpoint_delta),
            'actual_pts_end_difference_seconds': float(actual_delta),
            'all_targets_have_nearest_available_sample': True,
            'rule': 'BRACKETING_DURATION_GRID_AND_NEAREST_SAMPLE_COVERAGE'},
        'file_time_baseline': {'source_first_pts_rational': str(original['first_pts']),
            'candidate_first_pts_rational': str(proposed['first_pts']),
            'source_stream_start_seconds': original['meta']['video_start_seconds'],
            'candidate_stream_start_seconds': proposed['meta']['video_start_seconds'],
            'reference_zero': 'ACTUAL_SELECTED_SOURCE_FIRST_FRAME',
            'candidate_zero': 'ACTUAL_CANDIDATE_FIRST_FRAME',
            'source_terminal_duration_inferred': original['duration_inferred'],
            'candidate_terminal_duration_inferred': proposed['duration_inferred']},
        'frames_padded': 0, 'frames_interpolated': 0, 'frames_blended': 0, 'frames_retimed': 0,
        'candidate_audio_used': False, 'cloud_upload_performed': False, 'generation_submitted': False,
        'motion_quality': 'UNVERIFIED',
        'note': '本地适配只核验时间采样；舍弃部分候选帧可能影响快速动作顺滑度，取样误差不代表动作误差。'}


def adapt_candidate(source, candidate, out, source_start=0, source_end=None, *, sample_only=False):
    """Write a separate FFV1 RGB master; publish only after strict validation."""
    report = plan_adaptation(source, candidate, source_start, source_end, sample_only=sample_only)
    out = media._new_output(out)
    if out.suffix.lower() != '.mkv':
        raise media.MediaError('适配结果须保存为 .mkv（FFV1 无损）')
    sidecar = out.with_suffix(out.suffix + '.json')
    if sidecar.exists():
        raise media.MediaError('适配报告已存在，请保留旧版本并使用新文件名')
    width = media.probe(source)['width']
    height = media.probe(source)['height']
    processes = []
    with tempfile.TemporaryDirectory(prefix='.adapt-', dir=out.parent) as temporary:
        pending = Path(temporary) / 'candidate.mkv'
        with tempfile.TemporaryFile() as errors:
            writer = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}',
                '-r', report['fps_rational'], '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
                '-pix_fmt', 'bgr0', '-threads', '2', '-fps_mode', 'passthrough', str(pending)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            processes.append(writer)
            if report['spatial_mapping']['performed']:
                reader = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(candidate),
                    '-map', '0:v:0', '-vf', f'scale={width}:{height}:flags=lanczos', '-an', '-f', 'rawvideo',
                    '-pix_fmt', 'rgb24', '-threads', '2', '-fps_mode', 'passthrough', 'pipe:1'],
                    stdout=subprocess.PIPE, stderr=errors)
            else:
                reader = rm._reader(candidate, width, height, errors=errors)
            processes.append(reader)
            try:
                current_index, current_frame = -1, None
                for row in report['mapping']:
                    wanted_index = row['candidate_frame'] - 1
                    while current_index < wanted_index:
                        current_frame = rm._read_exact(reader.stdout, width * height * 3)
                        current_index += 1
                    writer.stdin.write(current_frame)
                # Decode the original candidate to its true end, rather than
                # permitting truncation or corruption after the last used frame.
                while current_index < report['original_candidate']['frame_count'] - 1:
                    rm._read_exact(reader.stdout, width * height * 3)
                    current_index += 1
                if reader.stdout.read(1):
                    raise media.MediaError('候选解码帧数超出已核验时间戳序列')
                reader.stdout.close()
                if reader.wait(timeout=30):
                    raise media.MediaError('候选解码失败，未接入适配结果')
                writer.stdin.close()
                if writer.wait(timeout=180):
                    raise media.MediaError('候选无损适配编码失败')
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    if process.stdout and not process.stdout.closed:
                        process.stdout.close()
        strict = rm.validate_candidate(source, pending, source_start, source_end)
        if not strict['ready']:
            raise media.MediaError('适配结果未通过现有严格技术检查：' + '；'.join(i['message'] for i in strict['issues']))
        if media._hash(source) != report['source_sha256'] or media._hash(candidate) != report['candidate_sha256']:
            raise media.MediaError('适配期间原片或候选发生变化，未接入结果')
        strict['candidate']['path'] = str(out)
        report.update(status='LOCAL_ADAPTATION_TECHNICAL_PASS', path=str(out),
            media={**media.probe(pending), 'path': str(out)}, strict_validation=strict,
            output_sha256=media._hash(pending), lossless_master=True,
            message='本地适配通过，实际效果待验收。')
        if sample_only:
            report['timeline_size_validation'] = report.pop('strict_validation')
            report.update(status='SAMPLE_ADAPTATION_TECHNICAL_PASS', lossless_master=False,
                          encoding_lossless=True, motion_quality='UNVERIFIED',
                          message='候选小样已作等比尺寸与时间采样适配；仅供对照，非原生原片分辨率质量，不代表口型、动作或正式成片验收。')
        pending_report = Path(temporary) / 'report.json'
        pending_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        # link is atomic and refuses replacement if another process won this path.
        published = False
        try:
            os.link(pending, out)
            published = True
            os.link(pending_report, sidecar)
        except Exception:
            if published:
                out.unlink(missing_ok=True)
            raise
    return report
