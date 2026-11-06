"""Local evidence and strict regional compositing for source-video editing.

Keyframe boxes are identification evidence, never segmentation masks or a claim
of automatic tracking.  The composite path accepts only a separately supplied
binary mask video and verifies protected RGB pixels after lossless encoding.
"""
from __future__ import annotations

from fractions import Fraction
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

from . import media


def _timing(path, *, decode_pts=False):
    path = media._file(path)
    meta = media.probe(path)
    if not meta['has_video']:
        raise media.MediaError('输入素材没有视频轨')
    fields = 'best_effort_timestamp_time,pkt_duration_time,duration_time'
    if decode_pts:
        fields += ',pts,pkt_duration,duration'
    data = json.loads(media._run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames', '-show_streams',
        '-show_entries', f'frame={fields}:stream=time_base',
        '-of', 'json', path], timeout=180))
    frames = data.get('frames', [])
    points = []
    for frame in frames:
        value = frame.get('best_effort_timestamp_time')
        if value is None:
            raise media.MediaError('视频有缺失时间戳，无法严格核对原片帧序列')
        points.append(float(value))
    if not points:
        raise media.MediaError('视频没有可解码的画面帧')
    origin = points[0]
    points = [v-origin for v in points]
    try:
        rate = Fraction(meta['fps_rational'])
        period = 1/float(rate)
    except (ValueError, ZeroDivisionError):
        raise media.MediaError('视频帧率无效，无法核对帧序列') from None
    # Matroska may quantize 24000/1001 timestamps to milliseconds. Quantization
    # is not VFR; allow one stream clock tick, without masking larger drift.
    clock = float(Fraction(data.get('streams', [{}])[0].get('time_base', '1/1000000')))
    tolerance = max(.000005, period*.0005, clock*1.01)
    deltas = [b-a for a, b in zip(points, points[1:])]
    cfr = all(abs(delta-period) <= tolerance for delta in deltas)
    last_duration = frames[-1].get('duration_time') or frames[-1].get('pkt_duration_time')
    end = points[-1]+(float(last_duration) if last_duration and float(last_duration)>0 else period)
    result = {'meta': meta, 'points': points, 'frame_count': len(points),
              'first_pts': origin, 'fps_rational': str(rate), 'period': period,
              'constant_frame_rate': cfr, 'end': end}
    if decode_pts:
        tick = Fraction(data.get('streams', [{}])[0].get('time_base', '0'))
        pts, terminal_duration = [], None
        if tick > 0:
            try:
                pts = [int(frame['pts']) * tick for frame in frames]
            except (KeyError, ValueError, TypeError):
                pass  # A decoder's best-effort timestamps cannot certify the raw frame grid.
            try:
                duration_ticks = int(frames[-1].get('duration') or frames[-1].get('pkt_duration'))
                if duration_ticks > 0:
                    terminal_duration = duration_ticks * tick
            except (ValueError, TypeError):
                pass
        result['raw_pts'] = {'first': pts[0] if pts else None,
                             'points': [point - pts[0] for point in pts], 'terminal_duration': terminal_duration}
    return result


def _selection(timing, start, end):
    start = media._finite(start, '原片起点')
    end = timing['end'] if end is None else media._finite(end, '原片终点')
    if start < 0 or end <= start or end > timing['end']+.002:
        raise media.MediaError('截取范围超出原片画面时间轴')
    indices = [i for i, point in enumerate(timing['points']) if point >= start-.000002 and point < end-.000002]
    if not indices:
        raise media.MediaError('所选区间不足一个原片画面帧')
    return indices[0], indices[-1]+1, start, end


def prepare_source_clip(source, out, start, end):
    """Cut existing frames at native size and cadence; never interpolate or pad.

    Source VFR is rejected for this strict first version.  Audio is intentionally
    restored once from the original source during assembly, not from AI clips.
    """
    timing = _timing(source)
    if not timing['constant_frame_rate']:
        raise media.MediaError('原片为可变帧率，当前严格流程暂不能认证逐帧还原；未重采样或改变原片')
    first, stop, requested_start, requested_end = _selection(timing, start, end)
    out = media._new_output(out)
    rate = timing['fps_rational']
    time_base = str(1/Fraction(rate))
    width, height = timing['meta']['width'], timing['meta']['height']
    pix_fmt = 'yuv420p' if width % 2 == 0 and height % 2 == 0 else 'yuv444p'
    media._run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', source,
                '-map', '0:v:0', '-vf', f'trim=start_frame={first}:end_frame={stop},setpts=PTS-STARTPTS',
                '-an', '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-threads', '2',
                '-pix_fmt', pix_fmt, '-enc_time_base', time_base, '-fps_mode', 'passthrough', '-movflags', '+faststart', out])
    result = _timing(out)
    if (result['frame_count'] != stop-first or not result['constant_frame_rate'] or
            Fraction(result['fps_rational']) != Fraction(rate) or
            (result['meta']['width'], result['meta']['height']) != (width, height)):
        raise media.MediaError('原片截取后的帧数、尺寸或帧率不一致；不得作为严格参考')
    timeline = {'source_start': requested_start, 'source_end': requested_end,
                'first_frame': first+1, 'last_frame': stop, 'frame_count': stop-first,
                'actual_first_frame_seconds': timing['points'][first],
                'actual_end_seconds': timing['points'][stop-1]+timing['period'],
                'fps_rational': rate, 'source_first_pts': timing['first_pts'],
                'duration_error_seconds': abs((stop-first)*timing['period']-(requested_end-requested_start)),
                'frames_interpolated': 0, 'frames_padded': 0, 'frames_retimed': 0}
    return {'status': 'SOURCE_FRAMES_AND_CADENCE_PASS', 'path': str(out), 'media': result['meta'],
            'timeline': timeline, 'original_audio_source': str(Path(source).resolve()),
            'audio_policy': 'RESTORE_ORIGINAL_ONCE_AT_ASSEMBLY', 'pixel_lossless': False,
            'motion_quality': 'ORIGINAL_FRAMES_NO_GENERATION', 'cloud_upload_performed': False}


def _missing_coverage(source_pts, candidate_pts, first, stop, period):
    """Identify uncovered source times on a shared grid without guessing action alignment."""
    coverage = {'missing_frame_count': None, 'missing_ranges': []}
    if not source_pts or not candidate_pts or any(point % period for point in candidate_pts):
        return coverage
    present = set(candidate_pts)
    anchor = source_pts[first]
    missing = [index for index in range(first, stop) if source_pts[index] - anchor not in present]
    ranges = coverage['missing_ranges']
    for index in missing:
        frame = index + 1
        source_seconds = float(source_pts[index])
        segment_seconds = float(source_pts[index] - anchor)
        if not ranges or ranges[-1]['source_last_frame'] != frame - 1:
            ranges.append({'source_first_frame': frame, 'source_first_seconds': source_seconds,
                           'segment_first_seconds': segment_seconds})
        ranges[-1].update(source_last_frame=frame, source_last_seconds=source_seconds,
                          segment_last_seconds=segment_seconds)
    coverage['missing_frame_count'] = len(missing)
    return coverage


def _pts_evidence(raw, certified, first=0, stop=None):
    evidence = {'cfr_pts_certified': certified, 'first_pts_rational': None, 'last_pts_rational': None,
                'decoded_duration_seconds': None}
    points = raw['points']
    if not points:
        return evidence
    stop = len(points) if stop is None else stop
    origin, terminal = raw['first'], raw['terminal_duration']
    evidence.update(first_pts_rational=str(origin + points[first]), last_pts_rational=str(origin + points[stop - 1]))
    end = points[stop] if stop < len(points) else None
    if end is None and terminal is not None:
        end = points[-1] + terminal
    if end is not None:
        evidence['decoded_duration_seconds'] = float(end - points[first])
    return evidence


def validate_candidate(source, candidate, source_start=0, source_end=None, *, output_size=None, verify_raw_pts=False):
    original = _timing(source, decode_pts=verify_raw_pts)
    proposed = _timing(candidate, decode_pts=verify_raw_pts)
    raw_source = original.get('raw_pts', {})
    source_pts = raw_source.get('points')
    selection = original
    if source_pts:
        selection = {**original, 'points': [float(point) for point in source_pts]}
        if raw_source['terminal_duration'] is not None:
            selection['end'] = float(source_pts[-1] + raw_source['terminal_duration'])
    first, stop, start, end = _selection(selection, source_start, source_end)
    issues = []
    def issue(code, message):
        issues.append({'code': code, 'message': message, 'blocking': True})
    if not original['constant_frame_rate']:
        issue('SOURCE_VFR', '原片可变帧率尚不能通过严格逐帧合成')
    if not proposed['constant_frame_rate']:
        issue('CANDIDATE_VFR', '候选结果可变帧率，不能静默重采样后合成')
    if Fraction(original['fps_rational']) != Fraction(proposed['fps_rational']):
        issue('FPS_MISMATCH', f"候选帧率 {proposed['fps_rational']} 与原片 {original['fps_rational']} 不同")
    source_size = (original['meta']['width'], original['meta']['height'])
    expected_size = source_size if output_size is None else tuple(output_size)
    if (proposed['meta']['width'], proposed['meta']['height']) != expected_size:
        if output_size is None:
            issue('SIZE_MISMATCH', '候选尺寸与原片不一致；严格流程不会自动缩放')
        else:
            issue('SIZE_MISMATCH', f'候选尺寸与约定的 {expected_size[0]}×{expected_size[1]} 不一致；严格流程不会自动缩放')
    if proposed['frame_count'] != stop-first:
        issue('FRAME_COUNT_MISMATCH', f"候选 {proposed['frame_count']} 帧，原片区间 {stop-first} 帧")
    wanted_duration = (stop-first)*original['period']
    if abs(proposed['end']-wanted_duration) > max(.002, original['period']*.05):
        issue('DURATION_MISMATCH', '候选画面时长与原片帧序列不一致')
    report = {'issues': issues,
              'source': {**original['meta'], 'frame_count': stop-first, 'source_start': start, 'source_end': end,
                         'first_frame': first+1, 'last_frame': stop, 'constant_frame_rate': original['constant_frame_rate']},
              'candidate': {**proposed['meta'], 'frame_count': proposed['frame_count'], 'constant_frame_rate': proposed['constant_frame_rate']},
              'audio_policy': 'IGNORE_CANDIDATE_AUDIO_RESTORE_ORIGINAL', 'motion_quality': 'UNVERIFIED',
              'note': '尺寸、帧率、帧数一致仅代表技术条件通过；人物、动作、接触和遮挡仍须逐段审核'}
    if verify_raw_pts:
        from . import raw_delivery
        period = 1 / Fraction(original['fps_rational'])
        raw_candidate = proposed['raw_pts']
        source_exact = raw_delivery.has_exact_frame_grid(source_pts, raw_source['terminal_duration'], period)
        candidate_exact = raw_delivery.has_exact_frame_grid(raw_candidate['points'], raw_candidate['terminal_duration'], period)
        if not source_exact:
            issue('SOURCE_PTS_MISMATCH', '原片逐帧时间戳或末帧时长无法精确核实')
        if not candidate_exact:
            issue('CANDIDATE_PTS_MISMATCH', '回片逐帧时间戳或末帧时长与原片帧间隔不一致')
        report['source'].update(_pts_evidence(raw_source, source_exact, first, stop))
        report['candidate'].update(_pts_evidence(raw_candidate, candidate_exact))
        coverage = _missing_coverage(source_pts if source_exact else None, raw_candidate['points'], first, stop, period)
        coverage['frame_count_delta'] = proposed['frame_count'] - (stop - first)
        report.update(coverage=coverage, usage_used_for_validation=False)
        report['note'] = '尺寸、帧率、帧数、时间戳及画面时长通过技术检查后，仍须逐段核对人物、动作、接触和遮挡'
        if coverage['missing_frame_count']:
            first_gap = coverage['missing_ranges'][0]
            issue('MISSING_FRAME_COVERAGE', f"回片缺少 {coverage['missing_frame_count']} 个源帧时刻，"
                  f"第一个缺口为原片 F{first_gap['source_first_frame']}（{first_gap['source_first_seconds']:.6f} 秒）")
        try:
            raw_delivery._video_configuration(candidate)
        except media.MediaError as error:
            issue('CANDIDATE_STREAM_INCOMPATIBLE', str(error))
    report['ready'] = not issues
    return report


def _font(size):
    from PIL import ImageFont
    for path in ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
                 '/System/Library/Fonts/Supplemental/Arial.ttf']:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _frame(path, index):
    from PIL import Image
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(path),
        '-vf', f'select=eq(n\\,{index})', '-frames:v', '1', '-an', '-c:v', 'png', '-threads', '1',
        '-f', 'image2pipe', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if result.returncode or not result.stdout:
        raise media.MediaError('无法读取原片定位画面')
    return Image.open(BytesIO(result.stdout)).convert('RGB')


def build_contact_sheet(p, outpath, max_samples=96):
    """Draw observed identity boxes only at their exact sampled frame."""
    from PIL import Image, ImageDraw, ImageOps
    if not p.get('source'):
        raise media.MediaError('请先导入原片')
    source = p['source']['path']
    timing = _timing(source)
    samples = []
    actors = p.get('source_actors', [])
    for segment in p.get('segments', []):
        first, stop, _, _ = _selection(timing, segment['start'], segment['end'])
        wanted = {first, (first+stop-1)//2, stop-1}
        analysis = p.get('source_analysis') or {}
        if analysis.get('source_sha256') == p['source'].get('sha256'):
            for cut in analysis.get('cuts', []):
                index = cut['frame'] - 1
                if first < index < stop:
                    wanted.update((index-1, index))
        for actor in actors:
            for observation in actor.get('observations', []):
                if observation.get('segment_id') == segment['id'] and segment['start']-.0001 <= observation['time'] <= segment['end']+.0001:
                    wanted.add(min(range(first, stop), key=lambda i: abs(timing['points'][i]-observation['time'])))
        for index in sorted(wanted):
            seen = []
            for actor in actors:
                for observation in actor.get('observations', []):
                    if observation.get('segment_id') != segment['id']:
                        continue
                    nearest = min(range(first, stop), key=lambda i: abs(timing['points'][i]-observation['time']))
                    if nearest == index:
                        seen.append({'id': actor['id'], 'label': actor.get('label', actor['id']),
                                     'target_role_id': actor.get('target_role_id'), 'scope': actor.get('scope'),
                                     'box': observation.get('box'), 'visibility': observation.get('visibility', 'visible'),
                                     'confirmed': bool(observation.get('confirmed')), 'color': actor.get('color', '#56adff')})
            samples.append({'segment_id': segment['id'], 'time': timing['points'][index],
                            'frame_index': index+1, 'actors': seen})
    total_samples = len(samples)
    samples = samples[:max_samples]
    roles = p.get('roles', [])
    tile_w, tile_h, columns = 480, 318, 3
    header_h = 84
    role_h = math.ceil(len(roles)/columns)*130 if roles else 0
    mapping_h = math.ceil(len(actors)/columns)*66 if actors else 0
    rows = max(1, math.ceil(len(samples)/columns))
    sheet = Image.new('RGB', (tile_w*columns, header_h+rows*tile_h+mapping_h+role_h+45), '#111923')
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 12), '人物对应核对 · 原片关键帧', font=_font(27), fill='#f4f7fb')
    draw.text((18, 48), '矩形仅表示人工定位；未标注的画面不推断人物轨迹。此图不代表逐帧遮罩或动作验收。', font=_font(16), fill='#b7c7da')
    for i, sample in enumerate(samples):
        image = _frame(source, sample['frame_index']-1)
        overlay = ImageDraw.Draw(image)
        scale = max(1, round(image.width/500))
        for actor in sample['actors']:
            box = actor.get('box')
            if actor['visibility'] != 'visible' or not box:
                continue
            coords = (box[0]*image.width, box[1]*image.height, box[2]*image.width, box[3]*image.height)
            color = actor['color']
            overlay.rectangle(coords, outline=color, width=3*scale)
            number = next(j+1 for j, original in enumerate(actors) if original['id'] == actor['id'])
            label = f"{number}. {actor['label'][:12]}"
            overlay.text((coords[0]+4*scale, max(0, coords[1]-22*scale)), label, font=_font(17*scale), fill=color, stroke_width=scale, stroke_fill='#111923')
        image = ImageOps.contain(image, (tile_w-16, 260))
        x = (i % columns)*tile_w+8
        y = header_h+(i//columns)*tile_h
        sheet.paste(image, (x+(tile_w-16-image.width)//2, y+(260-image.height)//2))
        draw.text((x, y+265), f"{sample['segment_id']} · F{sample['frame_index']} · {sample['time']:.3f}s", font=_font(17), fill='#ecf3ff')
        label = ' / '.join(a['id']+(' 已确认' if a['confirmed'] else ' 待确认') for a in sample['actors']) or '此采样帧没有人工身份定位'
        draw.text((x, y+289), label[:55], font=_font(14), fill='#b7c7da')
    for i, actor in enumerate(actors):
        x = (i % columns)*tile_w+12
        y = header_h+rows*tile_h+(i//columns)*66
        target = next((r.get('name', r['id']) for r in roles if r['id'] == actor.get('target_role_id')), '待绑定')
        if actor.get('scope') == 'preserve':target = '保持原样'
        draw.text((x, y+3), f"{i+1}. {actor.get('label', actor['id'])[:18]} → {target[:18]}", font=_font(16), fill=actor.get('color', '#ecf3ff'))
        draw.text((x, y+29), actor['id'][:47], font=_font(13), fill='#b7c7da')
    for i, role in enumerate(roles):
        x = (i % columns)*tile_w+10
        y = header_h+rows*tile_h+mapping_h+(i//columns)*130
        image = None
        for asset in role.get('images', []):
            try:
                with Image.open(asset['path']) as original:
                    image = ImageOps.contain(original.convert('RGB'), (100, 116))
                break
            except (OSError, KeyError):
                continue
        if image is not None:
            sheet.paste(image, (x, y))
        draw.text((x+110, y+10), f"{role['id']} · {role.get('name', '')}", font=_font(17), fill='#ecf3ff')
        draw.text((x+110, y+40), f"{len(role.get('images', []))} 张外观参考", font=_font(15), fill='#b7c7da')
    draw.text((18, sheet.height-31), f'已显示 {len(samples)}/{total_samples} 个采样点；身份、遮挡与高速动作仍需播放原片检查。', font=_font(15), fill='#c5d3e3')
    out = media._new_output(outpath)
    sheet.save(out)
    return {'path': str(out), 'samples': samples, 'sample_count': len(samples), 'total_sample_count': total_samples,
            'truncated': len(samples) < total_samples, 'sampling': 'KEYFRAMES_NOT_FULL_TRACKING',
            'automatic_tracking': False, 'pixel_mask': False, 'motion_quality': 'UNVERIFIED',
            'source_sha256': media._hash(source), 'cloud_upload_performed': False}


def _reader(path, width, height, *, first=None, stop=None, gray=False, errors=None):
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(path), '-map', '0:v:0']
    if first is not None:
        args += ['-vf', f'trim=start_frame={first}:end_frame={stop},setpts=PTS-STARTPTS']
    args += ['-an', '-pix_fmt', 'gray' if gray else 'rgb24', '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1']
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors or subprocess.DEVNULL)


def _read_exact(pipe, length):
    parts = []
    remaining = length
    while remaining:
        part = pipe.read(remaining)
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    data = b''.join(parts)
    if len(data) != length:
        raise media.MediaError('解码帧数不足，已停止区域合成')
    return data


def _binary_frame(proc, width, height):
    import numpy as np
    frame = np.frombuffer(_read_exact(proc.stdout, width*height), dtype=np.uint8).reshape(height, width)
    if not np.all((frame == 0) | (frame == 255)):
        raise media.MediaError('遮罩包含灰色或压缩残留；须使用逐帧纯黑白二值无损遮罩')
    return frame == 255


def validate_mask(source, mask, source_start=0, source_end=None, allow_empty=False):
    """Check a newly supplied mask's actual decoded pixels, without uploading."""
    result = validate_candidate(source, mask, source_start, source_end)
    result['binary'] = False
    result['media'] = result['candidate']
    result['frame_count'] = result['candidate']['frame_count']
    result['source_sha256'] = media._hash(source)
    result['mask_sha256'] = media._hash(mask)
    if Path(mask).suffix.lower() not in ['.mkv', '.avi', '.mov', '.mp4']:
        result['issues'].append({'code': 'MASK_REQUIRES_VIDEO', 'message': '遮罩须为逐帧视频；静态矩形不能代表人物跟踪', 'blocking': True})
    if result['issues']:
        result['ready'] = False
        return result
    width, height = result['source']['width'], result['source']['height']
    proc = _reader(mask, width, height, gray=True)
    white = 0
    try:
        for _ in range(result['frame_count']):
            white += int(_binary_frame(proc, width, height).sum())
        proc.stdout.close()
        if proc.wait(timeout=30):
            raise media.MediaError('遮罩视频解码失败')
        result['binary'] = True
        result['white_pixel_samples'] = white
        if white == 0 and not allow_empty:
            result['issues'].append({'code': 'EMPTY_EDIT_MASK', 'message': '遮罩全黑，没有任何可编辑区域', 'blocking': True})
    except media.MediaError as exc:
        result['issues'].append({'code': 'MASK_NOT_BINARY', 'message': str(exc), 'blocking': True})
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        if proc.stdout and not proc.stdout.closed:
            proc.stdout.close()
    result['ready'] = not result['issues']
    return result


def _write_rgb_master(segments, out, width, height, rate):
    """Decode explicit frame ranges and concatenate them without resampling."""
    processes = []
    frames = 0
    with tempfile.TemporaryFile() as errors:
        writer = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', rate, '-i', 'pipe:0',
            '-an', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgr0', '-threads', '2', str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        processes.append(writer)
        try:
            for item in segments:
                reader = _reader(item['path'], width, height, first=item['input_first'], stop=item['input_stop'], errors=errors)
                processes.append(reader)
                for _ in range(item['input_stop']-item['input_first']):
                    writer.stdin.write(_read_exact(reader.stdout, width*height*3))
                    frames += 1
                reader.stdout.close()
                if reader.wait(timeout=30):
                    raise media.MediaError('严格拼接输入解码失败')
            writer.stdin.close()
            if writer.wait(timeout=180):
                raise media.MediaError('严格拼接无损编码失败')
        except Exception:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
            raise
        finally:
            for proc in processes:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
    return frames


def assemble_strict(source, segments, out, target_start=0, target_end=None, preview_path=None):
    """Keep source frames, native cadence and one continuous original soundtrack.

    A candidate with the wrong cadence/count is rejected, rather than changing
    a punch, contact or camera move to make its duration fit.
    """
    if not segments:
        raise media.MediaError('请先准备待拼接片段')
    original = _timing(source)
    if not original['constant_frame_rate']:
        raise media.MediaError('可变帧率原片尚不能通过严格逐帧拼接；未修改原片')
    target_first, target_stop, _, _ = _selection(original, target_start, target_end)
    out = media._new_output(out)
    if out.suffix.lower() != '.mkv':
        raise media.MediaError('严格拼接母版须为 .mkv；可另提供 MP4 预览路径')
    plan = []
    cursor = target_first
    last_start = -1
    for segment in segments:
        if segment.get('anchors'):
            raise media.MediaError('严格还原模式不允许时间锚点调速；请修复候选动作而不是修改原片节奏')
        first, stop, _, _ = _selection(original, segment['source_start'], segment['source_end'])
        if first < last_start:
            raise media.MediaError('片段须按原片时间顺序排列')
        last_start = first
        check = validate_candidate(source, segment['path'], segment['source_start'], segment['source_end'])
        if not check['ready']:
            raise media.MediaError('待拼接片段与原片不一致：'+'；'.join(i['message'] for i in check['issues']))
        if first > cursor:
            raise media.MediaError('原片帧序列存在空档；不能静默补帧、冻结或改变节奏')
        visible_first = max(first, cursor, target_first)
        visible_stop = min(stop, target_stop)
        if visible_first >= visible_stop:
            raise media.MediaError('片段完全重叠或超出目标范围，请移除多余片段')
        plan.append({'path': str(Path(segment['path']).resolve()), 'source_first_frame': visible_first+1,
                     'source_last_frame': visible_stop, 'input_first': visible_first-first,
                     'input_stop': visible_stop-first, 'overlap_removed_frames': visible_first-first,
                     'output_frames': visible_stop-visible_first, 'speed_scale': 1})
        cursor = visible_stop
    if cursor != target_stop:
        raise media.MediaError('片段未覆盖完整目标时间轴，不能静默截短或补帧')
    width, height = original['meta']['width'], original['meta']['height']
    rate = original['fps_rational']
    expected = target_stop-target_first
    duration = expected*original['period']
    with tempfile.TemporaryDirectory(prefix='strict-assembly-', dir=out.parent) as temp:
        raw = Path(temp)/'frames.mkv'
        count = _write_rgb_master(plan, raw, width, height, rate)
        if count != expected:
            raise media.MediaError('严格拼接输出帧数与原片目标区间不一致')
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', raw]
        audio = {'mode': 'none', 'candidate_audio_used': False, 'per_clip_audio_joined': False}
        if original['meta']['has_audio']:
            audio_start = (original['first_pts']+original['points'][target_first]-
                           original['meta'].get('audio_start_time', 0))
            delay = max(0, -audio_start)
            af = f'asetpts=PTS-STARTPTS,atrim=start={max(0,audio_start):.9f}:duration={max(0,duration-delay):.9f},asetpts=PTS-STARTPTS'
            if delay:
                af += f',adelay={delay*1000:.6f}:all=1'
            args += ['-i', source, '-filter_complex', f'[1:a:0]{af}[a]', '-map', '0:v:0', '-map', '[a]',
                     '-c:a', 'pcm_s24le']
            audio = {'mode': 'single_continuous_original', 'path': str(Path(source).resolve()),
                     'source_start_seconds': audio_start, 'original_gap_preserved_seconds': delay,
                     'candidate_audio_used': False, 'per_clip_audio_joined': False, 'looped': False}
        else:
            args += ['-map', '0:v:0', '-an']
        media._run(args+['-c:v', 'copy', '-t', f'{duration:.9f}', out])
    verified = _timing(out)
    if verified['frame_count'] != expected or Fraction(verified['fps_rational']) != Fraction(rate):
        raise media.MediaError('母版回读帧数或帧率不一致，严格拼接验收失败')
    result = {'status': 'STRICT_FRAME_TIMELINE_PASS', 'path': str(out), 'media': verified['meta'],
              'frame_count': expected, 'fps_rational': rate, 'first_source_frame': target_first+1,
              'last_source_frame': target_stop, 'duration_seconds': duration, 'plan': plan,
              'frames_interpolated': 0, 'frames_padded': 0, 'frames_retimed': 0,
              'audio': audio, 'lossless_master': True, 'pixel_preservation': 'DECODED_INPUT_CLIPS',
              'original_audio_source': str(Path(source).resolve()), 'motion_quality': 'UNVERIFIED',
              'cloud_upload_performed': False}
    if preview_path:
        preview = media._new_output(preview_path)
        pix_fmt = 'yuv420p' if width % 2 == 0 and height % 2 == 0 else 'yuv444p'
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-i', out, '-map', '0:v:0',
                '-map', '0:a:0?', '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-threads', '2',
                '-pix_fmt', pix_fmt, '-enc_time_base', str(1/Fraction(rate)), '-fps_mode', 'passthrough', '-c:a', 'aac', '-b:a', '320k',
                '-movflags', '+faststart', preview]
        media._run(args)
        preview_timing = _timing(preview)
        if preview_timing['frame_count'] != expected or Fraction(preview_timing['fps_rational']) != Fraction(rate):
            raise media.MediaError('MP4 预览的帧数或帧率未通过原片核对')
        result['preview_path'] = str(preview)
        result['preview_media'] = preview_timing['meta']
        result['preview_pixel_lossless'] = False
    out.with_suffix(out.suffix+'.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


def composite_region(source, candidate, mask, out, source_start=0, source_end=None, protection_mask=None):
    """Composite matching frames into a lossless FFV1 RGB master.

    White edit-mask pixels accept the candidate; white protection-mask pixels
    always retain the source. Masks are videos, not static boxes. Candidate
    sound is never used; the original continuous soundtrack is restored later.
    """
    import numpy as np
    report = validate_candidate(source, candidate, source_start, source_end)
    if not report['ready']:
        raise media.MediaError('候选技术条件未通过：'+'；'.join(i['message'] for i in report['issues']))
    for name, path in [('编辑遮罩', mask), ('前景保护遮罩', protection_mask)]:
        if path is None:
            continue
        if Path(path).suffix.lower() not in ['.mkv', '.avi', '.mov', '.mp4']:
            raise media.MediaError(name+'须为与原片区间逐帧对应的视频；静态图片不能代表人物跟踪')
        check = validate_candidate(source, path, source_start, source_end)
        if not check['ready']:
            raise media.MediaError(name+'的尺寸、帧率、帧数或时长与原片区间不一致')
    out = media._new_output(out)
    if out.suffix.lower() != '.mkv':
        raise media.MediaError('区域合成母版须保存为 .mkv（FFV1 无损），避免把压缩 MP4 误认为像素不变')
    timing = _timing(source)
    first, stop, _, _ = _selection(timing, source_start, source_end)
    width, height = timing['meta']['width'], timing['meta']['height']
    count = stop-first
    rate = timing['fps_rational']
    processes = []
    verified_error = 0
    with tempfile.TemporaryFile() as errors:
        writer = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', rate, '-i', 'pipe:0',
            '-an', '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'bgr0', '-threads', '2', '-fps_mode', 'passthrough', str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        processes.append(writer)
        def readers(include_output=False):
            paths = [(source, False, first, stop), (out if include_output else candidate, False, None, None),
                     (mask, True, None, None)]
            if protection_mask:
                paths.append((protection_mask, True, None, None))
            procs = [_reader(path, width, height, first=begin, stop=end, gray=gray, errors=errors)
                     for path, gray, begin, end in paths]
            processes.extend(procs)
            return procs
        try:
            procs = readers()
            changed_pixels = 0
            for _ in range(count):
                original = np.frombuffer(_read_exact(procs[0].stdout, width*height*3), dtype=np.uint8).reshape(height, width, 3)
                proposed = np.frombuffer(_read_exact(procs[1].stdout, width*height*3), dtype=np.uint8).reshape(height, width, 3)
                edit = _binary_frame(procs[2], width, height)
                if protection_mask:
                    edit = edit & ~_binary_frame(procs[3], width, height)
                changed_pixels += int(edit.sum())
                result = np.where(edit[..., None], proposed, original).astype(np.uint8)
                writer.stdin.write(result.tobytes())
            writer.stdin.close()
            if writer.wait(timeout=180):
                raise media.MediaError('无损区域合成编码失败')
            for proc in procs:
                proc.stdout.close()
                if proc.wait(timeout=30):
                    raise media.MediaError('区域合成输入解码失败')
            # Verify decoded master pixels, not merely pre-encode arrays.
            procs = readers(include_output=True)
            for _ in range(count):
                original = np.frombuffer(_read_exact(procs[0].stdout, width*height*3), dtype=np.uint8).reshape(height, width, 3)
                result = np.frombuffer(_read_exact(procs[1].stdout, width*height*3), dtype=np.uint8).reshape(height, width, 3)
                edit = _binary_frame(procs[2], width, height)
                if protection_mask:
                    edit = edit & ~_binary_frame(procs[3], width, height)
                if (~edit).any():
                    error = int(np.abs(result[~edit].astype(np.int16)-original[~edit].astype(np.int16)).max())
                    verified_error = max(verified_error, error)
            for proc in procs:
                proc.stdout.close()
                if proc.wait(timeout=30):
                    raise media.MediaError('合成母版回读核验失败')
            if verified_error:
                raise media.MediaError('合成母版的受保护区域发生变化，验收未通过')
        except Exception:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if out.exists():
                out.unlink()
            raise
        finally:
            for proc in processes:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
    result = {'status': 'REGION_COMPOSITE_TECHNICAL_PASS', 'path': str(out), 'media': media.probe(out),
              'frame_count': count, 'fps_rational': rate, 'lossless_master': True,
              'protected_pixel_max_error': verified_error, 'protected_pixels_verified_after_decode': True,
              'editable_pixel_samples': changed_pixels, 'motion_quality': 'UNVERIFIED', 'identity_quality': 'UNVERIFIED',
              'mask_source': str(Path(mask).resolve()), 'protection_mask_source': str(Path(protection_mask).resolve()) if protection_mask else None,
              'original_audio_source': str(Path(source).resolve()), 'original_audio_available': timing['meta']['has_audio'],
              'audio_policy': 'RESTORE_ORIGINAL_ONCE_AT_ASSEMBLY', 'candidate_audio_used': False,
              'cloud_upload_performed': False}
    out.with_suffix(out.suffix+'.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result
