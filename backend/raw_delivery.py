"""Assemble accepted original candidates without encoding any video track."""
from __future__ import annotations

from fractions import Fraction
import json
import math
import os
from pathlib import Path
import tempfile

from . import candidate_adaptation, generation_audio, media, replacement_media


_FFMPEG = ('ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror', '-n')
_BOUNDARY_TOLERANCE_SECONDS = 0.000002
_STREAM_CONFIGURATION = ('codec_name', 'codec_tag_string', 'profile', 'level', 'width', 'height',
                         'pix_fmt', 'sample_aspect_ratio', 'field_order', 'color_range',
                         'color_space', 'color_transfer', 'color_primaries', 'extradata_hash')


def _probe(path, *options):
    return json.loads(media._run(['ffprobe', '-v', 'error', *options, '-of', 'json', path]))


def has_exact_frame_grid(points, terminal_duration, period):
    return bool(points) and terminal_duration == period and all(point == i * period for i, point in enumerate(points))


def _exact_timeline(path, rate, label):
    timeline = candidate_adaptation._timestamps(path, rate, label)
    if timeline['duration_inferred'] or not has_exact_frame_grid(
            timeline['points'], timeline['terminal_duration'], timeline['period']):
        raise media.MediaError(f'{label}的实际画面时间戳或末帧时长与恒定原片帧序不一致；不能无改画面直拼')
    return timeline


def _video_configuration(path):
    data = _probe(path, '-show_streams', '-show_format', '-show_data_hash', 'sha256')
    tracks = [stream for stream in data.get('streams', []) if stream.get('codec_type') == 'video']
    if len(tracks) != 1:
        raise media.MediaError('无改画面直拼要求每个候选恰有一条视频轨')
    stream = tracks[0]
    if (stream.get('disposition', {}).get('attached_pic') or stream.get('codec_name') != 'h264'
            or stream.get('codec_tag_string') != 'avc1' or stream.get('pix_fmt') != 'yuv420p'
            or not stream.get('extradata_hash')
            or 'mp4' not in data.get('format', {}).get('format_name', '').split(',')):
        raise media.MediaError('无改画面直拼目前只支持单视频轨的 H.264 8 位 4:2:0 MP4/MOV；不会自动重新编码')
    if any(side.get('side_data_type') == 'Display Matrix' for side in stream.get('side_data_list', [])):
        raise media.MediaError('候选含显示旋转或镜像矩阵，不能无改画面直拼')
    return {key: stream.get(key) for key in _STREAM_CONFIGURATION}


def _video_evidence(path):
    packets = _probe(path, '-select_streams', 'v:0', '-show_packets', '-show_data_hash', 'sha256',
                     '-show_entries', 'packet=flags,data_hash').get('packets', [])
    if not packets or 'K' not in packets[0].get('flags', '') or any(not row.get('data_hash') for row in packets):
        raise media.MediaError('候选不从完整关键帧起播，或无法核实视频包内容；不能无改画面直拼')
    text = media._run([*_FFMPEG, '-i', path, '-map', '0:v:0', '-an', '-fps_mode', 'passthrough',
                       '-pix_fmt', 'yuv420p', '-c:v', 'rawvideo', '-f', 'framehash', '-hash', 'sha256', 'pipe:1'])
    frames = [line.rsplit(',', 1)[-1].strip() for line in text.splitlines() if line and not line.startswith('#')]
    if not frames:
        raise media.MediaError('无法核实候选解码画面内容')
    return [row['data_hash'] for row in packets], frames


def _audio_plan(source, video, first, stop, requested_end):
    if not video['meta']['has_audio']:
        return Fraction(0), {'mode': 'none', 'candidate_audio_used': False, 'per_clip_audio_joined': False}
    audio = generation_audio._audio_timing(source)
    rate = audio['sample_rate']
    audio_origin = audio['first_pts']
    video_origin = video['first_pts']
    window = min(video_origin, audio_origin) + video['points'][first]
    video_first = video_origin + video['points'][first]
    video_offset = video_first - window
    video_end = video_first + (stop - first) * video['period']
    raw_first = generation_audio._nearest_integer((window - audio_origin) * rate)
    raw_stop = generation_audio._nearest_integer((video_end - audio_origin) * rate)
    last = audio_origin + Fraction(audio['sample_count'], rate)
    natural_gap = video_end - last
    natural_tail = False
    if raw_stop > audio['sample_count']:
        if (stop != video['frame_count'] or abs(Fraction(str(requested_end)) - video['end']) > Fraction(1, 1_000_000)
                or not 0 < natural_gap < video['period'] or audio_origin > video_first
                or audio['time_base'] > Fraction(1, rate)):
            raise media.MediaError('原声音轨未覆盖当前拼接区间；不会填充末端、循环或拉伸')
        raw_stop = audio['sample_count']
        natural_tail = True
    start, leading = max(0, raw_first), max(0, -raw_first)
    if raw_stop <= start:
        raise media.MediaError('当前拼接区间没有可用原声；不会借用候选音轨')
    return video_offset, {'mode': 'single_continuous_original', 'path': str(source), 'sample_rate': rate,
                          'channels': audio['channels'], 'source_first_sample': start, 'source_stop_sample': raw_stop,
                          'sample_count': raw_stop - start + leading, 'leading_original_gap_samples': leading,
                          'source_window_start_rational': str(window),
                          'original_audio_video_offset_rational': str(audio_origin - video_origin),
                          'video_first_pts_seconds': float(video_offset), 'candidate_audio_used': False,
                          'per_clip_audio_joined': False, 'resampled': False, 'stretched': False,
                          'looped': False, 'terminal_silence_added': False,
                          'natural_tail_policy': generation_audio.FINAL_TAIL_POLICY if natural_tail else None,
                          'natural_tail_gap_rational': str(natural_gap) if natural_tail else None}


def _concat_list(paths, duration, destination):
    lines = ['ffconcat version 1.0']
    for path, seconds in zip(paths, duration):
        lines += [f'file {path.name}', f'duration {float(seconds):.9f}']
    destination.write_text('\n'.join(lines) + '\n', encoding='ascii')


def assemble_original(source, segments, out, target_start=0, target_end=None, *, output_size=None):
    """Copy complete original candidate video tracks and mux one source soundtrack."""
    if not segments:
        raise media.MediaError('请先提供已验收的完整原始候选片段')
    source = media._file(source)
    source_hash = media._hash(source)
    try:
        rate = Fraction(media.probe(source)['fps_rational'])
        if rate <= 0:
            raise ValueError()
    except (KeyError, ValueError, ZeroDivisionError, TypeError):
        raise media.MediaError('原片帧率无效，不能无改画面直拼') from None
    original = _exact_timeline(source, rate, '原片')
    selection = {'points': [float(point) for point in original['points']], 'end': float(original['end'])}
    target_first, target_stop, requested_start, requested_end = replacement_media._selection(selection, target_start, target_end)
    period = original['period']
    dimensions = (original['meta']['width'], original['meta']['height']) if output_size is None else tuple(output_size)
    fingerprints, plan, expected_packets, expected_frames = {}, [], [], []
    cursor, previous_end, configuration = target_first, requested_start, None
    for index, segment in enumerate(segments, 1):
        path = media._file(segment['path'])
        first, stop, start, end = replacement_media._selection(selection, segment['source_start'], segment['source_end'])
        if first != cursor or abs(start - previous_end) > _BOUNDARY_TOLERANCE_SECONDS or stop > target_stop:
            raise media.MediaError('片段须按原片顺序连续且无重叠；不会裁片、补帧或改变速度')
        fingerprint = media._hash(path)
        timing = _exact_timeline(path, rate, f'第 {index} 段原始候选')
        if (timing['meta']['width'], timing['meta']['height']) != dimensions:
            raise media.MediaError(f'第 {index} 段候选的原始尺寸不符合 {dimensions[0]}×{dimensions[1]}；不会缩放')
        if timing['frame_count'] != stop - first:
            raise media.MediaError(f'第 {index} 段候选的完整帧数与原片不一致；不会补帧')
        current = _video_configuration(path)
        if configuration is not None and current != configuration:
            raise media.MediaError('原始候选视频编码配置不兼容，不能直接拼接；不会自动重新编码')
        configuration = current
        packets, frames = _video_evidence(path)
        if len(frames) != timing['frame_count'] or len(packets) != timing['frame_count']:
            raise media.MediaError('候选的画面帧与视频包不能逐项核对；不能无改画面直拼')
        fingerprints[path] = fingerprint
        expected_packets.extend(packets)
        expected_frames.extend(frames)
        plan.append({'path': str(path), 'sha256': fingerprint, 'source_first_frame': first + 1,
                     'source_last_frame': stop, 'output_frames': stop - first, 'source_start': start, 'source_end': end,
                     'input_video_first_pts_rational': str(timing['first_pts']), 'duration': (stop - first) * period})
        cursor, previous_end = stop, end
    if cursor != target_stop or abs(previous_end - requested_end) > _BOUNDARY_TOLERANCE_SECONDS:
        raise media.MediaError('完整片段没有覆盖目标时间轴；不会裁片或补齐缺口')
    out = media._new_output(out)
    if out.suffix.lower() != '.mp4':
        raise media.MediaError('原始视频直拼输出须为 MP4')
    offset, audio = _audio_plan(source, original, target_first, target_stop, requested_end)
    timescale = math.lcm(rate.numerator, offset.denominator)
    movie_timescale = math.lcm(timescale, audio.get('sample_rate', 1))
    if timescale > 1_000_000 or movie_timescale > 2**31 - 1:
        raise media.MediaError('音画时间基准无法用当前 MP4 精确保存；不会重定时')
    expected_count = target_stop - target_first
    with tempfile.TemporaryDirectory(prefix='original-delivery-', dir=out.parent) as tmp:
        temporary = Path(tmp)
        normalized = []
        for index, entry in enumerate(plan):
            dest = temporary / f'part-{index}.mp4'
            start = Fraction(entry['input_video_first_pts_rational'])
            media._run([*_FFMPEG, '-copyts', '-itsoffset', f'{float(-start):.9f}', '-i', entry['path'],
                        '-map', '0:v:0', '-an', '-c:v', 'copy', '-video_track_timescale', str(timescale),
                        '-movie_timescale', str(movie_timescale), '-avoid_negative_ts', 'disabled', dest])
            normalized.append(dest)
        manifest = temporary / 'parts.ffconcat'
        _concat_list(normalized, [entry['duration'] for entry in plan], manifest)
        target = temporary / 'result.mp4'
        command = [*_FFMPEG, '-copyts', '-itsoffset', f'{float(offset):.9f}', '-f', 'concat', '-safe', '1',
                   '-auto_convert', '0', '-i', manifest]
        if audio['mode'] == 'single_continuous_original':
            filters = (f"atrim=start_sample={audio['source_first_sample']}:end_sample={audio['source_stop_sample']},"
                       'asetpts=N/SR/TB')
            if audio['leading_original_gap_samples']:
                filters += f",adelay={audio['leading_original_gap_samples']}S:all=1"
            command += ['-i', source, '-map', '0:v:0', '-map', '1:a:0', '-af', filters, '-c:a', 'aac', '-b:a', '320k']
        else:
            command += ['-map', '0:v:0', '-an']
        media._run(command + ['-c:v', 'copy', '-video_track_timescale', str(timescale), '-movie_timescale', str(movie_timescale),
                             '-avoid_negative_ts', 'disabled', '-movflags', '+faststart', target])
        result = _exact_timeline(target, rate, '导出文件')
        if result['frame_count'] != expected_count or result['first_pts'] != offset:
            raise media.MediaError('导出回读的画面覆盖或原始音画偏移不一致；未保存成片')
        if _video_configuration(target) != configuration or _video_evidence(target) != (expected_packets, expected_frames):
            raise media.MediaError('导出回读发现视频包、画面内容或顺序改变；未保存成片')
        audio_tracks = _probe(target, '-select_streams', 'a', '-show_streams').get('streams', [])
        if audio['mode'] == 'single_continuous_original':
            if len(audio_tracks) != 1:
                raise media.MediaError('导出原声音轨数量不符合单一连续音轨要求；未保存成片')
            stream = audio_tracks[0]
            try:
                verified_audio = (int(stream['sample_rate']) == audio['sample_rate']
                                  and stream['channels'] == audio['channels'] and int(stream['start_pts']) == 0
                                  and Fraction(stream['time_base']) * int(stream['duration_ts']) ==
                                  Fraction(audio['sample_count'], audio['sample_rate']))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                verified_audio = False
            if not verified_audio:
                raise media.MediaError('导出原声的采样率、起点或实际范围不一致；未保存成片')
            audio['codec'] = stream['codec_name']
        elif audio_tracks:
            raise media.MediaError('原片无声但导出包含音轨；未保存成片')
        if media._hash(source) != source_hash or any(media._hash(path) != digest for path, digest in fingerprints.items()):
            raise media.MediaError('拼接期间原片或候选文件已变化；未保存成片')
        sha256 = media._hash(target)
        try:
            os.link(target, out)
        except FileExistsError:
            raise media.MediaError('输出已存在，请使用新文件名以保留旧版') from None
    for entry in plan:
        entry['duration_seconds'] = float(entry.pop('duration'))
    return {'status': 'ORIGINAL_STREAM_COPY_PASS', 'path': str(out), 'media': {**result['meta'], 'path': str(out)},
            'frame_count': expected_count, 'fps_rational': str(rate), 'duration_seconds': float(expected_count * period),
            'sha256': sha256, 'source_sha256': source_hash, 'video_copy': True, 'video_reencoded': False,
            'video_packets_verified': True, 'decoded_frames_verified': True, 'relative_pts_verified': True,
            'frames_interpolated': 0, 'frames_padded': 0, 'frames_retimed': 0, 'frames_removed': 0,
            'plan': plan, 'audio': audio, 'cloud_upload_performed': False}
