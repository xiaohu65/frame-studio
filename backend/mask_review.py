"""Local review and immutable pixel patches of complete, imported mask videos.

No tracking, segmentation model, supplier API, or source/audio retiming lives
here. Actual file bytes, not catalogue hashes, bind every read and commit.
"""
from __future__ import annotations

import base64
import copy
from fractions import Fraction
from functools import lru_cache
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile

from . import media, replacement_media, store

ACTIONS = {'render_mask_preview', 'patch_masks', 'restore_mask_version', 'save_mask_support', 'render_mask_support'}


@lru_cache(maxsize=24)
def _timing(path, actual_sha):
    # The content digest is deliberately part of this cache key.
    return replacement_media._timing(path)


def context(p, s):
    assets = {}
    for key in ('source', 'edit_mask', 'protection_mask'):
        asset = p.get(key) if key == 'source' else s.get(key)
        if asset:
            assets[key] = {'id': asset.get('id'), 'sha256': media._hash(asset['path'])}
        else:
            assets[key] = None
    if not assets['source'] or not assets['edit_mask']:
        raise ValueError('请先导入原片与当前片段的替换遮罩')
    binding = {'assets': assets, 'segment': {k: s[k] for k in ('id', 'start', 'end')}}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, allow_nan=False).encode()).hexdigest()
    timing = _timing(p['source']['path'], assets['source']['sha256'])
    if not timing['constant_frame_rate']:
        raise ValueError('当前逐帧修补要求恒定帧率原片；未重采样原片')
    first, stop, _, _ = replacement_media._selection(timing, s['start'], s['end'])
    return {'fingerprint': fingerprint, 'binding': binding, 'first_frame': first+1, 'last_frame': stop,
            'frame_count': stop-first, 'width': timing['meta']['width'], 'height': timing['meta']['height'],
            'fps_rational': timing['fps_rational'], 'first_time': timing['points'][first],
            'last_time': timing['points'][stop-1]}


def _assert_current(p, s, expected):
    current = context(p, s)
    if not expected or current['fingerprint'] != expected:
        raise ValueError('原片、遮罩或片段范围已改变；请重新载入当前预览，本次未覆盖文件')
    return current


def _technical(p, s, ctx):
    for key in ('edit_mask', 'protection_mask'):
        asset = s.get(key)
        if not asset:
            continue
        check = replacement_media.validate_mask(p['source']['path'], asset['path'], s['start'], s['end'], allow_empty=key == 'protection_mask')
        if not check['ready']:
            raise ValueError(('替换层：' if key == 'edit_mask' else '保护层：')+'；'.join(i['message'] for i in check['issues']))
    _assert_current(p, s, ctx['fingerprint'])


def _frame_reader(asset, ctx, first, stop, errors, gray=False):
    return replacement_media._reader(asset['path'], ctx['width'], ctx['height'], first=first, stop=stop, gray=gray, errors=errors)


def _finish(processes):
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for pipe in (proc.stdin, proc.stdout):
            if pipe and not pipe.closed:
                pipe.close()


def _binary(proc, ctx):
    return replacement_media._binary_frame(proc, ctx['width'], ctx['height']).astype('uint8')*255


def _check_mask_timing(s, ctx):
    for key in ('edit_mask', 'protection_mask'):
        if not s.get(key):
            continue
        timing = _timing(s[key]['path'], ctx['binding']['assets'][key]['sha256'])
        if (timing['meta']['width'], timing['meta']['height'], timing['fps_rational'], timing['frame_count'], timing['constant_frame_rate']) != (ctx['width'], ctx['height'], ctx['fps_rational'], ctx['frame_count'], True):
            raise ValueError('遮罩尺寸、帧率或帧数与当前片段不一致，请先更换正确文件')


def _candidate_assets(s):
    seen = set()
    for key, label in (('generated', '本段候选'), ('raw_candidate', '原始候选'), ('sample_candidate', '生成小样')):
        asset = s.get(key)
        if asset and asset.get('id') not in seen:
            seen.add(asset.get('id'))
            yield key, label, asset


@lru_cache(maxsize=12)
def _diagnostic_plan(source, source_sha, candidate, candidate_sha, start, end):
    """Certify decoded PTS, then expose only individually supported stills.

    A valid individual sample is never permission to fill an absent endpoint,
    adapt the whole video, composite it, or approve its visual content.
    """
    from . import candidate_adaptation as ca
    source_rate = Fraction(media.probe(source)['fps_rational'])
    candidate_rate = Fraction(media.probe(candidate)['fps_rational'])
    original = ca._timestamps(source, source_rate, '原片')
    proposed = ca._timestamps(candidate, candidate_rate, '诊断候选')
    sw, sh = original['meta']['width'], original['meta']['height']
    cw, ch = proposed['meta']['width'], proposed['meta']['height']
    if sw*ch != sh*cw:
        raise ValueError('候选宽高比与原片不同，不能安全叠加遮罩边界；未裁切或拉伸')
    first, stop, _, _ = replacement_media._selection({'points': [float(t) for t in original['points']], 'end': float(original['end'])}, start, end)
    mapping = {}
    for index in range(first, stop):
        target = original['points'][index]-original['points'][first]
        selected = ca._nearest(proposed['points'], target)
        sample = proposed['points'][selected]
        error = abs(sample-target)
        missing_next_is_closer = target > proposed['points'][-1] and proposed['frame_count']/candidate_rate-target < error
        if target < 0 or target >= proposed['end'] or missing_next_is_closer or error > proposed['period']/2+proposed['tick']:
            continue
        mapping[index+1] = {'candidate_frame': selected+1, 'target_time_rational': str(target),
                            'candidate_time_rational': str(sample), 'sampling_error_rational': str(error),
                            'target_seconds': float(target), 'candidate_seconds': float(sample), 'sampling_error_seconds': float(error)}
    ranges = []
    for frame in mapping:
        if ranges and ranges[-1]['last_frame']+1 == frame:
            ranges[-1]['last_frame'] = frame
        else:
            ranges.append({'first_frame': frame, 'last_frame': frame})
    for item in ranges:
        item.update(first_source_seconds=float(original['points'][item['first_frame']-1]),
                    last_source_seconds=float(original['points'][item['last_frame']-1]))
    # Reuse, never relax, the existing complete-video checks. Their failure is
    # evidence displayed alongside the partial still range, not an exception
    # converted into a successful adaptation record.
    native_check = replacement_media.validate_candidate(source, candidate, start, end)
    full_ready, full_reason = native_check['ready'], ''
    if not full_ready:
        try:
            ca.plan_adaptation(source, candidate, start, end, sample_only=True)
            full_ready = True
        except (ValueError, media.MediaError) as exc:
            full_reason = str(exc)
    return {'mapping': mapping, 'available_source_ranges': ranges,
            'full_timeline_check': {'ready': full_ready, 'reason': full_reason},
            'candidate_native_size': [cw, ch], 'source_size': [sw, sh], 'candidate_frame_count': proposed['frame_count'],
            'candidate_fps_rational': str(candidate_rate), 'spatial_mapping': 'IDENTITY' if (cw, ch) == (sw, sh) else 'STILL_ONLY_LANCZOS_EQUAL_ASPECT',
            'candidate_end_seconds': float(proposed['end']), 'candidate_last_pts_seconds': float(proposed['points'][-1]),
            'source_first_pts_rational': str(original['first_pts']), 'candidate_first_pts_rational': str(proposed['first_pts']),
            'reference_zero': 'ACTUAL_SELECTED_SOURCE_FIRST_FRAME', 'selected_source_first_frame': first+1,
            'selected_source_first_seconds': float(original['points'][first]),
            'method': 'DIAGNOSTIC_NEAREST_DECODED_PTS_TIES_EARLIER', 'diagnostic_only': True,
            'strict_final_approved': False, 'composite_ready': False, 'candidate_audio_used': False,
            'frames_padded': 0, 'frames_retimed': 0, 'cloud_mask_constraint': False,
            'note': '仅静帧诊断：按片段首个实际源帧归零，选最近有效候选PTS；不补齐缺帧、不改变整段适配与合成门槛。'}


def diagnostic_candidate(p, s, candidate_id, expected=None):
    selected = next(((key, label, asset) for key, label, asset in _candidate_assets(s) if asset.get('id') == candidate_id), None)
    if not selected:
        raise ValueError('此候选已更换或不属于当前片段；请重新选择')
    key, label, asset = selected
    source_sha, candidate_sha = media._hash(p['source']['path']), media._hash(asset['path'])
    binding = {'source_sha256': source_sha, 'candidate_id': asset['id'], 'candidate_sha256': candidate_sha,
               'segment': {k: s[k] for k in ('id', 'start', 'end')}}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if expected is not None and expected != fingerprint:
        raise ValueError('诊断候选或原片范围已改变，请重新载入；本次未覆盖遮罩')
    plan = _diagnostic_plan(p['source']['path'], source_sha, asset['path'], candidate_sha, s['start'], s['end'])
    if media._hash(asset['path']) != candidate_sha or media._hash(p['source']['path']) != source_sha:
        raise ValueError('读取期间原片或诊断候选已改变，请重新载入')
    return {**{k: v for k, v in plan.items() if k != 'mapping'}, 'id': asset['id'], 'name': asset.get('name', label),
            'slot': key, 'label': label, 'fingerprint': fingerprint, 'binding': binding,
            'historical_material': bool(s.get('candidate_stale')), '_mapping': plan['mapping'], '_asset': asset}


def frame_data(p, s, frame, expected, candidate_id=None, candidate_expected=None):
    """Exact source frame plus both native binary layers, for browser canvas."""
    import numpy as np
    from PIL import Image
    ctx = _assert_current(p, s, expected)
    diagnostic = diagnostic_candidate(p, s, candidate_id, candidate_expected) if candidate_id else None
    if candidate_id and not candidate_expected:
        raise ValueError('请先载入当前诊断候选的文件指纹')
    if not isinstance(frame, int) or isinstance(frame, bool) or not ctx['first_frame'] <= frame <= ctx['last_frame']:
        raise ValueError('原片帧号超出当前片段')
    _check_mask_timing(s, ctx)
    procs = []
    arrays = {}
    try:
        with tempfile.TemporaryFile() as errors:
            for key in ('source', 'edit_mask', 'protection_mask'):
                if key == 'protection_mask' and not s.get(key):
                    arrays[key] = np.zeros((ctx['height'], ctx['width']), dtype=np.uint8)
                    continue
                index = frame-1 if key == 'source' else frame-ctx['first_frame']
                proc = _frame_reader(p['source'] if key == 'source' else s[key], ctx, index, index+1, errors, gray=key != 'source')
                procs.append(proc)
                if key == 'source':
                    arrays[key] = np.frombuffer(replacement_media._read_exact(proc.stdout, ctx['width']*ctx['height']*3), dtype=np.uint8).reshape(ctx['height'], ctx['width'], 3)
                else:
                    arrays[key] = _binary(proc, ctx)
                proc.stdout.close()
                if proc.wait(timeout=30):
                    raise media.MediaError('当前帧解码失败，请检查遮罩文件')
        _assert_current(p, s, expected)
        candidate_report = None
        if diagnostic:
            row = diagnostic['_mapping'].get(frame)
            candidate_report = {k: v for k, v in diagnostic.items() if not k.startswith('_')}
            candidate_report.update(available=bool(row), source_frame=frame)
            if row:
                image = replacement_media._frame(diagnostic['_asset']['path'], row['candidate_frame']-1)
                arrays['candidate'] = np.asarray(image.resize((ctx['width'], ctx['height']), Image.Resampling.LANCZOS))
                candidate_report.update(row)
            else:
                candidate_report['unavailable_reason'] = '此原片帧没有合法的候选时间样本；不冻结末帧或补齐，只显示原片与遮罩。'
            diagnostic_candidate(p, s, candidate_id, candidate_expected)
            _assert_current(p, s, expected)
        images = {}
        for key, array in arrays.items():
            buffer = BytesIO()
            Image.fromarray(array).save(buffer, format='PNG')
            images[key] = 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
        source_timing = _timing(p['source']['path'], ctx['binding']['assets']['source']['sha256'])
        return {'fingerprint': expected, 'source_frame': frame, 'segment_frame': frame-ctx['first_frame']+1,
                'source_time': source_timing['points'][frame-1], 'images': images, 'candidate': candidate_report}
    finally:
        _finish(procs)


def _overlay(original, edit, protect):
    import numpy as np
    preview = original.copy()
    effective, protected = (edit == 255) & (protect == 0), protect == 255
    preview[effective] = np.rint(original[effective]*.58+np.array([255, 116, 72])*.42).astype(np.uint8)
    preview[protected] = np.rint(original[protected]*.50+np.array([65, 171, 255])*.50).astype(np.uint8)
    return preview


def _paths(outdir, prefix, patch=False):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(prefix)):
        raise ValueError('遮罩输出编号无效')
    root = Path(outdir)
    root.mkdir(parents=True, exist_ok=True)
    result = {'preview_path': root/f'{prefix}_mask_overlay.mp4', 'report_path': root/f'{prefix}_mask_review.json'}
    if patch:
        result.update(edit_mask_path=root/f'{prefix}_patched_edit.mkv', protection_mask_path=root/f'{prefix}_patched_protect.mkv')
    return {key: media._new_output(path) for key, path in result.items()}


def _render(p, s, outdir, prefix, operations=None, expected=None):
    import numpy as np
    from . import mask_editor
    ctx = _assert_current(p, s, expected) if expected else context(p, s)
    strokes = mask_editor.validate_patch_operations(operations, ctx) if operations is not None else None
    _technical(p, s, ctx)
    paths = _paths(outdir, prefix, strokes is not None)
    procs = []
    effective_count = protected_count = overlap_count = changed_edit = changed_protect = 0
    changed_frames = []
    try:
        with tempfile.TemporaryFile() as errors:
            original = _frame_reader(p['source'], ctx, ctx['first_frame']-1, ctx['last_frame'], errors)
            edit_reader = _frame_reader(s['edit_mask'], ctx, 0, ctx['frame_count'], errors, gray=True)
            protect_reader = _frame_reader(s['protection_mask'], ctx, 0, ctx['frame_count'], errors, gray=True) if s.get('protection_mask') else None
            procs += [original, edit_reader]+([protect_reader] if protect_reader else [])
            preview = mask_editor._writer(paths['preview_path'], ctx['width'], ctx['height'], ctx['fps_rational'], errors, False)
            writers = [preview]
            procs.append(preview)
            if strokes is not None:
                writers += [mask_editor._writer(paths[key], ctx['width'], ctx['height'], ctx['fps_rational'], errors, True) for key in ('edit_mask_path', 'protection_mask_path')]
                procs += writers[1:]
            for frame in range(ctx['first_frame'], ctx['last_frame']+1):
                edit = _binary(edit_reader, ctx)
                protect = _binary(protect_reader, ctx) if protect_reader else np.zeros_like(edit)
                if strokes is not None:
                    before_edit, before_protect = edit, protect
                    edit, protect = mask_editor.raster_patch_frame(edit, protect, frame, strokes)
                    ce, cp = int((edit != before_edit).sum()), int((protect != before_protect).sum())
                    changed_edit += ce
                    changed_protect += cp
                    if ce or cp:
                        changed_frames.append(frame)
                    writers[1].stdin.write(edit.tobytes())
                    writers[2].stdin.write(protect.tobytes())
                rgb = np.frombuffer(replacement_media._read_exact(original.stdout, ctx['width']*ctx['height']*3), dtype=np.uint8).reshape(ctx['height'], ctx['width'], 3)
                preview.stdin.write(_overlay(rgb, edit, protect).tobytes())
                effective_count += int(((edit == 255) & (protect == 0)).sum())
                protected_count += int((protect == 255).sum())
                overlap_count += int(((edit == 255) & (protect == 255)).sum())
            for reader in [original, edit_reader]+([protect_reader] if protect_reader else []):
                reader.stdout.close()
                if reader.wait(timeout=30):
                    raise media.MediaError('遮罩或原片解码失败；原文件已保留')
            for writer in writers:
                writer.stdin.close()
            for writer in writers:
                if writer.wait(timeout=180):
                    errors.seek(0)
                    raise media.MediaError('本地遮罩输出失败：'+errors.read().decode(errors='replace')[-800:])
        if not effective_count:
            raise ValueError('有效替换区域全空或全部被保护层覆盖；本次未保存')
        if strokes is not None and not changed_frames:
            raise ValueError('这些笔迹没有改变任何遮罩像素；原版本继续有效')
        if strokes is not None:
            for key in ('edit_mask', 'protection_mask'):
                check = replacement_media.validate_mask(p['source']['path'], paths[key+'_path'], s['start'], s['end'], allow_empty=key == 'protection_mask')
                if not check['ready']:
                    raise media.MediaError('修补遮罩二值、尺寸、帧率或帧数回读检查未通过')
        preview_check = replacement_media.validate_candidate(p['source']['path'], paths['preview_path'], s['start'], s['end'])
        if not preview_check['ready']:
            raise media.MediaError('预览帧率、尺寸或帧数回读检查未通过')
        _assert_current(p, s, ctx['fingerprint'])
        report = {**ctx, 'method': 'LOCAL_PIXEL_PATCH' if strokes is not None else 'IMPORTED_MASK_OVERLAY',
                  'binary_masks_verified': True, 'review_required': True, 'protection_priority': True,
                  'effective_edit_pixel_samples': effective_count, 'protected_pixel_samples': protected_count,
                  'overlap_pixel_samples': overlap_count, 'changed_source_frames': changed_frames,
                  'changed_edit_pixels': changed_edit, 'changed_protect_pixels': changed_protect,
                  'automatic_tracking': False, 'cloud_upload_performed': False, 'generation_submitted': False,
                  'frames_retimed': 0, 'frames_interpolated': 0, 'motion_quality': 'UNVERIFIED',
                  'edit_mask_sha256': media._hash(paths['edit_mask_path']) if strokes is not None else ctx['binding']['assets']['edit_mask']['sha256'],
                  'protection_mask_sha256': media._hash(paths['protection_mask_path']) if strokes is not None else (ctx['binding']['assets']['protection_mask'] or {}).get('sha256'),
                  'preview_sha256': media._hash(paths['preview_path']),
                  'note': '橙色为有效替换范围，蓝色为保护范围；重叠处保护优先。规格通过不等于人物轮廓合格，须检查手部、运动模糊、头饰支持范围及镜中同一人。'}
        if strokes is not None:
            report['operations'] = strokes
        paths['report_path'].write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        return {**{key: str(value) for key, value in paths.items()}, 'report': report}
    except Exception:
        _finish(procs)
        for path in paths.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        _finish(procs)


def render_preview(p, s, outdir, prefix, expected=None):
    return _render(p, s, outdir, prefix, expected=expected)


def patch_masks(p, s, outdir, prefix, operations, expected_fingerprint):
    return _render(p, s, outdir, prefix, operations=operations, expected=expected_fingerprint)


def invalidate(s):
    for key in ('mask_preview', 'mask_render_report', 'candidate_review', 'candidate_validation',
                'composite', 'composited', 'composite_preview', 'composite_report'):
        s[key] = None


def _segment(p, sid):
    s = next((item for item in p['segments'] if item['id'] == sid), None)
    if s is None:
        raise ValueError('请选择片段')
    return s


def _version(asset):
    if asset.get('kind') != 'mask_patch_version' or store.digest(asset['path']) != asset.get('sha256'):
        raise ValueError('修补版本记录已改变，不能恢复')
    return json.loads(Path(asset['path']).read_text())


def status(p, s):
    ctx = context(p, s)
    report, preview = s.get('mask_render_report') or {}, s.get('mask_preview')
    # Legacy polygon previews have no complete material binding and are hidden.
    try:
        ready = bool(preview and report.get('fingerprint') == ctx['fingerprint'] and
                     report.get('preview_sha256') == media._hash(preview['path']))
    except OSError:
        ready = False
    versions = []
    for asset in p.get('assets', []):
        if asset.get('kind') != 'mask_patch_version':
            continue
        try:
            item = _version(asset)
            if item['segment'] == ctx['binding']['segment'] and item['source_sha256'] == ctx['binding']['assets']['source']['sha256']:
                versions.append({'id': asset['id'], 'created_at': item['created_at'], 'changed_source_frames': item['changed_source_frames'],
                                 'is_current': item['derived_fingerprint'] == ctx['fingerprint']})
        except (OSError, ValueError, KeyError):
            continue
    job = next((j for j in store.list_jobs(p['id'], limit=None) if j.get('action') == 'render_mask_preview'
                and j.get('segment_id') == s['id'] and j.get('input_material_fingerprint') == ctx['fingerprint']), None)
    candidates = []
    for key, label, asset in _candidate_assets(s):
        try:
            diagnostic = diagnostic_candidate(p, s, asset['id'])
            candidates.append({k: v for k, v in diagnostic.items() if not k.startswith('_')})
        except (OSError, ValueError, KeyError, media.MediaError) as exc:
            candidates.append({'id': asset['id'], 'name': asset.get('name', label), 'slot': key, 'label': label,
                               'error': str(exc), 'diagnostic_only': True, 'strict_final_approved': False})
    return {**ctx, 'preview': preview if ready else None, 'report': report if ready else None,
            'preview_job': {k: job.get(k) for k in ('id', 'status', 'message')} if job else None,
            'technical_ready': ready, 'versions': versions, 'diagnostic_candidates': candidates,
            'note': '技术规格通过不等于人物轮廓合格；请对照原片逐帧检查。'}


def action(pid, body, jid):
    if body['action'] in ('save_mask_support', 'render_mask_support'):
        from . import mask_support
        return mask_support.action(pid, body, jid)
    from . import mask_editor
    p = store.get_project(pid)
    sid = body.get('segment_id')
    s = _segment(p, sid)
    ctx = _assert_current(p, s, body.get('mask_fingerprint')) if body.get('mask_fingerprint') else context(p, s)
    a = body['action']
    diagnostic_reference = None
    if a == 'patch_masks' and body.get('diagnostic_candidate_id'):
        if not body.get('diagnostic_candidate_fingerprint'):
            raise ValueError('缺少修补所依据的诊断候选指纹；请重新载入')
        diagnostic = diagnostic_candidate(p, s, body['diagnostic_candidate_id'], body['diagnostic_candidate_fingerprint'])
        diagnostic_reference = {k: diagnostic[k] for k in ('id', 'name', 'fingerprint', 'binding', 'diagnostic_only', 'strict_final_approved', 'full_timeline_check')}
    if a in ('patch_masks', 'restore_mask_version') and not body.get('mask_fingerprint'):
        raise ValueError('请先载入当前文件的范围预览')
    outdir = store.DATA/'projects'/pid/'exports'
    if a == 'restore_mask_version':
        record = next((asset for asset in p.get('assets', []) if asset['id'] == body.get('version_id')), None)
        if not record:
            raise ValueError('修补版本不存在')
        version = _version(record)
        if version['segment'] != ctx['binding']['segment'] or version['source_sha256'] != ctx['binding']['assets']['source']['sha256']:
            raise ValueError('此版本属于另一原片或片段范围')
        target = body.get('target')
        if target not in ('base', 'derived'):
            raise ValueError('请选择修补前或修补后版本')
        pair = copy.deepcopy(version[target])
        registered = {asset['id']: asset for asset in p.get('assets', [])}
        for key, asset in pair.items():
            if asset and (asset.get('id') not in registered or store.digest(asset['path']) != asset['sha256']):
                raise ValueError('旧遮罩文件已改变或缺失，当前有效文件已保留')
        probe = copy.deepcopy(s)
        probe.update(pair)
        restored_ctx = context(p, probe)
        _technical(p, probe, restored_ctx)
        with store.LOCK:
            latest = store.get_project(pid)
            current = _segment(latest, sid)
            _assert_current(latest, current, ctx['fingerprint'])
            invalidate(current)
            current.update(pair)
            store.put_project(latest)
        return {'message': '已切换到保留的遮罩版本；请重新核对范围与结果验收'}
    if a == 'patch_masks':
        result = mask_editor.apply_mask_patch(p, s, outdir, jid, body.get('operations'), ctx['fingerprint'])
    elif a == 'render_mask_preview':
        result = render_preview(p, s, outdir, jid, ctx['fingerprint'])
    else:
        raise ValueError('未知遮罩操作')
    # Commit and asset registration share one lock. Recheck actual input bytes
    # before registering anything; an older async result cannot replace a mask.
    with store.LOCK:
        latest = store.get_project(pid)
        current = _segment(latest, sid)
        _assert_current(latest, current, ctx['fingerprint'])
        if diagnostic_reference:
            diagnostic_candidate(latest, current, diagnostic_reference['id'], diagnostic_reference['fingerprint'])
            result['report']['diagnostic_candidate'] = diagnostic_reference
        if a == 'patch_masks':
            base = {key: copy.deepcopy(current.get(key)) for key in ('edit_mask', 'protection_mask')}
            derived = {key: store.add_asset(pid, result[key+'_path'], key, meta=media.probe(result[key+'_path'])) for key in ('edit_mask', 'protection_mask')}
            latest = store.get_project(pid)
            current = _segment(latest, sid)
            invalidate(current)
            current.update(derived)
            derived_ctx = context(latest, current)
            version = {'segment': ctx['binding']['segment'], 'source_sha256': ctx['binding']['assets']['source']['sha256'],
                       'base': base, 'derived': derived, 'created_at': store.now(), 'derived_fingerprint': derived_ctx['fingerprint'],
                       'changed_source_frames': result['report']['changed_source_frames']}
            manifest = media._new_output(outdir/f'{jid}_mask_version.json')
            store.save_json(manifest, version)
            record = store.add_asset(pid, manifest, 'mask_patch_version')
            latest['assets'].append(record)
            result['report'].update(fingerprint=derived_ctx['fingerprint'], binding=derived_ctx['binding'], base_fingerprint=ctx['fingerprint'])
        preview = store.add_asset(pid, result['preview_path'], 'mask_preview', meta=media.probe(result['preview_path']))
        # add_asset writes its own catalogue revision. Retain that catalogue
        # while preserving this action's material change in memory.
        latest['assets'] = store.get_project(pid)['assets']
        current.update(mask_preview=preview, mask_render_report=result['report'])
        Path(result['report_path']).write_text(json.dumps(result['report'], ensure_ascii=False, indent=2)+'\n')
        store.put_project(latest)
    return {'asset': preview, 'report': result['report'],
            'message': '局部修补已另存为无损版本，旧遮罩已保留；请核对轮廓与结果验收' if a == 'patch_masks' else '当前原片与遮罩的叠加预览已生成；橙色替换、蓝色保护，仍需人工核对'}
