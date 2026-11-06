"""Manual polygon keyframes, explicit visibility breaks, and local video masks.

This is a rotoscoping aid.  It does not detect people, recover hidden anatomy,
or certify a precise segmentation.  Locator boxes are never rasterized here.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

from . import media, replacement, replacement_media


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _area(points):
    return sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1])) / 2


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _on_segment(a, b, c):
    return min(a[0], b[0])-1e-10 <= c[0] <= max(a[0], b[0])+1e-10 and min(a[1], b[1])-1e-10 <= c[1] <= max(a[1], b[1])+1e-10


def _intersect(a, b, c, d):
    x, y, z, w = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if x*y < -1e-14 and z*w < -1e-14:
        return True
    return any(abs(v) < 1e-10 and _on_segment(first, second, point) for v, first, second, point in
               ((x, a, b, c), (y, a, b, d), (z, c, d, a), (w, c, d, b)))


def _polygon_error(points):
    if not isinstance(points, list) or not 3 <= len(points) <= 256:
        return '每条轮廓须有3到256个顶点'
    if any(not isinstance(point, list) or len(point) != 2 or
           not all(_number(v) and 0 <= v <= 1 for v in point) for point in points):
        return '轮廓顶点须为0到1之间的[x,y]坐标'
    if len({tuple(point) for point in points}) != len(points):
        return '轮廓顶点不能重复；闭合时不要重复保存第一个点'
    if abs(_area(points)) < 1e-8:
        return '轮廓没有有效面积，请重新描点'
    size = len(points)
    for i in range(size):
        for j in range(i+1, size):
            if j == i+1 or (i == 0 and j == size-1):
                continue
            if _intersect(points[i], points[(i+1) % size], points[j], points[(j+1) % size]):
                return '轮廓边线交叉，请按边缘顺序重新描点'
    return None


def _annotation_fingerprint(p, s, source_sha):
    value = {'source_sha256': source_sha, 'segment': {k: s.get(k) for k in ('id', 'start', 'end', 'mask_keyframes')},
             'actors': [{k: a.get(k) for k in ('id', 'scope')} for a in p.get('source_actors', [])],
             'scene_mode': p.get('scene', {}).get('mode', 'preserve'),
             'shot_cuts': p.get('source_analysis', {}).get('cuts', []) if (p.get('source_analysis') or {}).get('source_sha256') == source_sha else []}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _same_topology(left, right):
    a = {region['id']: region for region in left['regions']}
    b = {region['id']: region for region in right['regions']}
    if a.keys() != b.keys():
        return False
    return all(a[key].get('actor_id') == b[key].get('actor_id') and a[key]['mode'] == b[key]['mode']
               and len(a[key]['points']) == len(b[key]['points'])
               and _area(a[key]['points'])*_area(b[key]['points']) > 0 for key in a)


def _interpolate(left, right, frame_index):
    if frame_index == left['frame_index'] or left['frame_index'] == right['frame_index']:
        return copy.deepcopy(left['regions'])
    if frame_index == right['frame_index']:
        return copy.deepcopy(right['regions'])
    factor = (frame_index-left['frame_index'])/(right['frame_index']-left['frame_index'])
    targets = {region['id']: region for region in right['regions']}
    regions = []
    for start in left['regions']:
        end = targets[start['id']]
        result = {k: v for k, v in start.items() if k != 'points'}
        result['points'] = [[x+(u-x)*factor, y+(v-y)*factor] for (x, y), (u, v) in zip(start['points'], end['points'])]
        regions.append(result)
    return regions


def validate_keyframes(p, s):
    """Return actionable local annotation issues, without altering the draft."""
    result = {'ready': False, 'issues': [], 'keyframes': [], 'automatic_tracking': False,
              'review_required': True, 'segmentation_quality': 'UNVERIFIED_MANUAL_POLYGONS'}
    def issue(code, message, **extra):
        result['issues'].append({'code': code, 'message': message, 'blocking': True, **extra})
    if not p.get('source'):
        issue('NO_SOURCE', '请先导入原片')
        return result
    try:
        timing = replacement_media._timing(p['source']['path'])
        first, stop, _, _ = replacement_media._selection(timing, s['start'], s['end'])
        source_sha = media._hash(p['source']['path'])
        result.update(first_frame=first+1, last_frame=stop, frame_count=stop-first,
                      first_time=timing['points'][first], last_time=timing['points'][stop-1],
                      width=timing['meta']['width'], height=timing['meta']['height'], fps_rational=timing['fps_rational'],
                      source_sha256=source_sha, input_fingerprint=replacement.fingerprint(p),
                      annotation_fingerprint=_annotation_fingerprint(p, s, source_sha))
        if not timing['constant_frame_rate']:
            issue('SOURCE_VFR', '当前人工轮廓流程要求恒定帧率；不会重采样原片')
    except (media.MediaError, OSError, KeyError, TypeError, ValueError) as exc:
        issue('SOURCE_INVALID', str(exc))
        return result
    raw = s.get('mask_keyframes', [])
    if not isinstance(raw, list) or not raw:
        issue('NO_POLYGON_KEYFRAMES', '请在原片首末帧描绘轮廓并保存关键帧，或导入外部逐帧蒙版')
        return result
    if len(raw) > 10000:
        issue('TOO_MANY_KEYFRAMES', '每段轮廓关键帧最多10000个')
        return result
    actors = {actor['id']: actor for actor in p.get('source_actors', [])}
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or not _number(item.get('time')):
            issue('INVALID_KEYFRAME_TIME', '轮廓关键帧须包含原片时间秒数')
            continue
        stamp = item['time']
        if not s['start']-1e-6 <= stamp <= s['end']+1e-6:
            issue('KEYFRAME_OUTSIDE_SEGMENT', '轮廓关键帧时间超出当前片段，不能跨段插值')
            continue
        index = min(range(first, stop), key=lambda n: abs(timing['points'][n]-stamp))
        if index in seen:
            issue('DUPLICATE_KEYFRAME', f'原片第{index+1}帧存在重复轮廓关键帧，请合并为一个')
            continue
        seen.add(index)
        if item.get('confirmed') is not True:
            issue('KEYFRAME_UNCONFIRMED', f'请确认原片第{index+1}帧的轮廓、人物身份和遮挡', frame=index+1)
        regions = item.get('regions')
        if not isinstance(regions, list):
            issue('REGIONS_INVALID', f'原片第{index+1}帧的regions须为列表；完全出画可保存空列表')
            continue
        if len(regions) > 100:
            issue('TOO_MANY_REGIONS', '同一帧最多100条轮廓')
            continue
        normalized = []
        region_ids = set()
        for region in regions:
            if not isinstance(region, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(region.get('id', ''))):
                issue('REGION_ID_INVALID', '轮廓编号仅允许字母、数字、下划线和短横线')
                continue
            rid = region['id']
            if rid in region_ids:
                issue('DUPLICATE_REGION_ID', f'同一帧的轮廓编号{rid}重复')
                continue
            region_ids.add(rid)
            mode = region.get('mode')
            if mode not in ('edit', 'protect'):
                issue('REGION_MODE_INVALID', f'轮廓{rid}须选择编辑或保护')
                continue
            aid = region.get('actor_id') or None
            if aid is not None and aid not in actors:
                issue('ACTOR_REFERENCE_INVALID', f'轮廓{rid}引用的原片人物不存在')
                continue
            if mode == 'edit' and aid is not None and actors[aid].get('scope') == 'preserve':
                issue('PRESERVED_ACTOR_EDITED', f'轮廓{rid}不能编辑设为保留的原片人物')
                continue
            if mode == 'edit' and aid is None and p.get('scene', {}).get('mode', 'preserve') != 'replace':
                issue('SCENE_NOT_SELECTED', f'轮廓{rid}未绑定人物；编辑场景须先选择场景替换')
                continue
            error = _polygon_error(region.get('points'))
            if error:
                issue('POLYGON_INVALID', f'原片第{index+1}帧，轮廓{rid}：{error}', frame=index+1)
                continue
            normalized.append({'id': rid, 'label': str(region.get('label') or rid)[:100], 'actor_id': aid,
                               'mode': mode, 'points': copy.deepcopy(region['points'])})
        result['keyframes'].append({'frame_index': index, 'frame': index+1, 'time': timing['points'][index],
                                    'regions': normalized, 'confirmed': item.get('confirmed') is True,
                                    'break_before': item.get('break_before') is True, 'break_after': item.get('break_after') is True})
    keys = sorted(result['keyframes'], key=lambda key: key['frame_index'])
    result['keyframes'] = keys
    if not keys or keys[0]['frame_index'] != first or keys[-1]['frame_index'] != stop-1:
        issue('ENDPOINTS_REQUIRED', f'请覆盖当前片段首末实际帧：第{first+1}帧（{timing["points"][first]:.6f}秒）和第{stop}帧（{timing["points"][stop-1]:.6f}秒）')
    analysis = p.get('source_analysis') or {}
    cuts = []
    if analysis.get('source_sha256') == result['source_sha256']:
        for cut in analysis.get('cuts', []):
            frame = cut.get('frame1based', cut.get('frame'))
            if isinstance(frame, int) and not isinstance(frame, bool) and first < frame-1 < stop:
                cuts.append(frame-1)
    result['shot_cut_frames'] = [index+1 for index in cuts]
    for left, right in zip(keys, keys[1:]):
        gap = right['frame_index']-left['frame_index']
        if gap <= 1:
            continue
        crossed = [index for index in cuts if left['frame_index'] < index <= right['frame_index']]
        if crossed:
            issue('SHOT_CUT_REQUIRES_BOUNDARY_KEYFRAMES', f'第{crossed[0]+1}帧为切镜候选；请分别保存前一帧和切镜首帧的轮廓，不能跨镜头插值')
            continue
        if left['break_after'] or right['break_before'] or not _same_topology(left, right):
            issue('VISIBILITY_BREAK_REQUIRES_ADJACENT_FRAMES', f'第{left["frame"]}到第{right["frame"]}帧之间轮廓新增、消失、遮挡或顶点顺序变化；请补齐变化前后相邻帧，或导入外部蒙版')
            continue
        for index in range(left['frame_index']+1, right['frame_index']):
            for region in _interpolate(left, right, index):
                error = _polygon_error(region['points'])
                if error:
                    issue('INTERPOLATED_POLYGON_INVALID', f'第{index+1}帧插值轮廓{region["id"]}发生折叠或交叉，请补轮廓关键帧', frame=index+1)
                    break
            else:
                continue
            break
    if not any(region['mode'] == 'edit' for key in keys for region in key['regions']):
        issue('NO_EDIT_REGION', '没有编辑轮廓；保护层不能代替需要替换的区域')
    result['ready'] = not result['issues']
    return result


def _writer(path, width, height, rate, errors, gray):
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'gray' if gray else 'rgb24',
            '-s', f'{width}x{height}', '-r', rate, '-i', 'pipe:0', '-an']
    if gray:
        args += ['-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gray']
    else:
        args += ['-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p' if width % 2 == 0 and height % 2 == 0 else 'yuv444p', '-movflags', '+faststart']
    args += ['-threads', '2', '-enc_time_base', str(1/Fraction(rate)), '-fps_mode', 'passthrough', str(path)]
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)


def validate_patch_operations(operations, context):
    """Normalize explicit brush strokes; never interpolate an imported mask."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 200:
        raise ValueError('每次修补须有1到200笔')
    normalized = []
    max_span = max(1, math.ceil(float(Fraction(context['fps_rational']))*2))
    total_points = 0
    for item in operations:
        if not isinstance(item, dict) or item.get('layer') not in ('edit', 'protect') or item.get('tool') not in ('brush', 'erase'):
            raise ValueError('每笔须明确选择替换层或保护层，以及画笔或橡皮')
        first, last = item.get('first_frame'), item.get('last_frame')
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (first, last)) or not context['first_frame'] <= first <= last <= context['last_frame']:
            raise ValueError('修补须使用当前片段内的明确原片帧号')
        if last-first+1 > max_span:
            raise ValueError('单笔修补区间最多2秒；请缩小到实际坏帧')
        radius, points = item.get('radius'), item.get('points')
        if not _number(radius) or not 1 <= radius <= min(context['width'], context['height'])/2:
            raise ValueError('画笔半径须在1像素到画面短边一半之间')
        if not isinstance(points, list) or not points or any(not isinstance(pt, list) or len(pt) != 2 or not all(_number(v) and 0 <= v <= 1 for v in pt) for pt in points):
            raise ValueError('笔迹须为0到1之间的画面坐标')
        total_points += len(points)
        if total_points > 20000:
            raise ValueError('本次笔迹过多，请分次保存')
        normalized.append({k: copy.deepcopy(item[k]) for k in ('layer', 'tool', 'first_frame', 'last_frame', 'radius', 'points')})
    return normalized


def raster_patch_frame(edit, protect, source_frame, operations):
    """Change only the selected stroke pixels of the selected layer/frame."""
    from PIL import Image, ImageDraw
    import numpy as np
    height, width = edit.shape
    layers = {'edit': edit.copy(), 'protect': protect.copy()}
    for stroke in operations:
        if not stroke['first_frame'] <= source_frame <= stroke['last_frame']:
            continue
        footprint = Image.new('1', (width, height))
        draw = ImageDraw.Draw(footprint)
        radius = stroke['radius']
        points = [(round(x*(width-1)), round(y*(height-1))) for x, y in stroke['points']]
        if len(points) > 1:
            draw.line(points, fill=1, width=max(1, round(radius*2)))
        for x, y in points:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=1)
        layers[stroke['layer']][np.asarray(footprint, dtype=bool)] = 255 if stroke['tool'] == 'brush' else 0
    return layers['edit'], layers['protect']


def apply_mask_patch(p, s, outdir, prefix, operations, expected_fingerprint):
    from .mask_review import patch_masks
    return patch_masks(p, s, outdir, prefix, operations, expected_fingerprint)


def validate_support_operations(operations, context):
    if not isinstance(operations, list) or len(operations) > 200:
        raise ValueError('每次支持域修补最多200笔')
    roles = ('removal', 'foreground', 'support', 'extra_protection')
    checks = []
    for item in operations:
        if not isinstance(item, dict) or item.get('role') not in roles:
            raise ValueError('须明确选择原人物清除、候选前景、允许支持或补充保护层')
        tool = item.get('tool')
        if tool not in ('brush', 'erase', 'seed_from_removal', 'support_from_foreground'):
            raise ValueError('支持域工具无效')
        if tool == 'seed_from_removal' and item['role'] != 'foreground' or tool == 'support_from_foreground' and item['role'] != 'support':
            raise ValueError('起草或支持复制只能用于指定图层')
        checks.append({**item, 'layer': 'edit', 'tool': tool if tool in ('brush', 'erase') else 'brush',
                       'radius': item.get('radius', 1), 'points': item.get('points', [[0, 0]])})
    if checks:
        validate_patch_operations(checks, context)
    return copy.deepcopy(operations)


def patch_support_frame(layers, source_frame, operations):
    import numpy as np
    result = {role: pixels.copy() for role, pixels in layers.items()}
    for item in operations:
        if not item['first_frame'] <= source_frame <= item['last_frame']:
            continue
        if item['tool'] == 'seed_from_removal':
            result['foreground'] = result['removal'].copy()
            result['support'] = result['removal'].copy()
        elif item['tool'] == 'support_from_foreground':
            result['support'] = np.maximum(result['support'], result['foreground'])
        else:
            stroke = {**item, 'layer': 'edit'}
            result[item['role']], _ = raster_patch_frame(result[item['role']], np.zeros_like(result[item['role']]), source_frame, [stroke])
    return result


def render_masks(p, s, outdir, prefix):
    """Render manual polygons at native source cadence, with a review preview."""
    from PIL import Image, ImageDraw
    import numpy as np
    check = validate_keyframes(p, s)
    if not check['ready']:
        raise ValueError('；'.join(issue['message'] for issue in check['issues']))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(prefix)):
        raise ValueError('蒙版输出前缀无效')
    root = Path(outdir)
    paths = {'edit_mask_path': root/f'{prefix}_edit_mask.mkv', 'protection_mask_path': root/f'{prefix}_protection_mask.mkv',
             'preview_path': root/f'{prefix}_mask_preview.mp4'}
    for key in paths:
        paths[key] = media._new_output(paths[key])
    report_path = media._new_output(root/f'{prefix}_mask_report.json')
    width, height, rate = check['width'], check['height'], check['fps_rational']
    keys = check['keyframes']
    processes = []
    editable, protected, interpolated = 0, 0, 0
    try:
        with tempfile.TemporaryFile() as errors:
            writers = [_writer(paths[key], width, height, rate, errors, key != 'preview_path') for key in paths]
            processes.extend(writers)
            reader = replacement_media._reader(p['source']['path'], width, height, first=check['first_frame']-1, stop=check['last_frame'], errors=errors)
            processes.append(reader)
            cursor = 0
            for index in range(check['first_frame']-1, check['last_frame']):
                while cursor+1 < len(keys) and keys[cursor+1]['frame_index'] <= index:
                    cursor += 1
                left, right = keys[cursor], keys[min(cursor+1, len(keys)-1)]
                regions = _interpolate(left, right, index)
                interpolated += int(index not in (left['frame_index'], right['frame_index']))
                edit_image, protect_image = Image.new('L', (width, height)), Image.new('L', (width, height))
                edit_draw, protect_draw = ImageDraw.Draw(edit_image), ImageDraw.Draw(protect_image)
                for region in regions:
                    points = [(round(x*(width-1)), round(y*(height-1))) for x, y in region['points']]
                    (edit_draw if region['mode'] == 'edit' else protect_draw).polygon(points, fill=255)
                edit, protect = np.asarray(edit_image), np.asarray(protect_image)
                effective = (edit == 255) & (protect == 0)
                editable += int(effective.sum())
                protected += int((protect == 255).sum())
                original = np.frombuffer(replacement_media._read_exact(reader.stdout, width*height*3), dtype=np.uint8).reshape(height, width, 3)
                preview = original.copy()
                preview[effective] = np.rint(original[effective]*.58+np.array([255, 116, 72])*.42).astype(np.uint8)
                selected_protect = protect == 255
                preview[selected_protect] = np.rint(original[selected_protect]*.50+np.array([65, 171, 255])*.50).astype(np.uint8)
                writers[0].stdin.write(edit.tobytes())
                writers[1].stdin.write(protect.tobytes())
                writers[2].stdin.write(preview.tobytes())
            reader.stdout.close()
            for writer in writers:
                writer.stdin.close()
            if reader.wait(timeout=30) or any(writer.wait(timeout=180) for writer in writers):
                errors.seek(0)
                raise media.MediaError('本地蒙版编码失败：'+errors.read().decode(errors='replace')[-1200:])
        if editable == 0:
            raise ValueError('编辑轮廓全部为空或被保护层覆盖，请检查轮廓与保护范围')
        if media._hash(p['source']['path']) != check['source_sha256']:
            raise ValueError('制作期间原片文件内容已改变，请重新核对')
        edit_check = replacement_media.validate_mask(p['source']['path'], paths['edit_mask_path'], s['start'], s['end'])
        protect_check = replacement_media.validate_mask(p['source']['path'], paths['protection_mask_path'], s['start'], s['end'], allow_empty=True)
        preview_check = replacement_media.validate_candidate(p['source']['path'], paths['preview_path'], s['start'], s['end'])
        if not all(report['ready'] for report in (edit_check, protect_check, preview_check)):
            raise media.MediaError('制作后蒙版或预览的帧数、帧率、尺寸及二值回读检查未通过')
        report = {k: check[k] for k in ('source_sha256', 'input_fingerprint', 'annotation_fingerprint', 'first_frame', 'last_frame', 'frame_count', 'fps_rational', 'width', 'height')}
        report.update(status='MANUAL_POLYGON_MASKS_NEED_REVIEW', keyframe_count=len(keys), interpolated_frames=interpolated,
                      method='MANUAL_POLYGON_LINEAR_INTERPOLATION', automatic_tracking=False, pixel_segmentation='MANUAL_POLYGON_INTERPOLATION',
                      review_required=True, identity_quality='UNVERIFIED', motion_quality='UNVERIFIED',
                      editable_pixel_samples=editable, protected_pixel_samples=protected, protection_empty=protected == 0,
                      edit_mask_sha256=media._hash(paths['edit_mask_path']), protection_mask_sha256=media._hash(paths['protection_mask_path']),
                      preview_sha256=media._hash(paths['preview_path']), binary_masks_verified=True,
                      frames_retimed=0, frames_interpolated=0, cloud_upload_performed=False, generation_submitted=False,
                      note='轮廓来自人工描点与插值，须逐帧检查边缘、出画、遮挡和手持道具；预览中的橙色为编辑区域、蓝色为保护区域。')
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        return {**{key: str(path) for key, path in paths.items()}, 'report_path': str(report_path), 'report': report}
    except Exception:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        for path in [*paths.values(), report_path]:
            path.unlink(missing_ok=True)
        raise
    finally:
        for proc in processes:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
