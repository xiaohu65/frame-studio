"""Manual, immutable local support domains. No segmentation or generation.

R removes old subject pixels; F identifies candidate foreground; S permits
sampling. Only F&S and explicitly reviewed (R&S)-F may sample the candidate.
Pbase|Padd always restores original pixels. Versions are catalogue assets, not
mutable segment fields or final six-check approvals.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from . import mask_editor, mask_review as mr, media, replacement_media as rm, store

ROLES = ('removal', 'foreground', 'support', 'extra_protection')
ACTIONS = {'save_mask_support', 'render_mask_support'}
SCHEMA = 'LOCAL_MASK_SUPPORT_V1'


def _hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _context(p, s, candidate_id, candidate_fingerprint, mask_fingerprint):
    ctx = mr._assert_current(p, s, mask_fingerprint)
    if not candidate_fingerprint:
        raise ValueError('请先选择并核对当前候选文件指纹')
    candidate = mr.diagnostic_candidate(p, s, candidate_id, candidate_fingerprint)
    if not candidate['full_timeline_check']['ready'] or len(candidate['_mapping']) != ctx['frame_count']:
        raise ValueError('完整候选时间覆盖未通过；仅可静帧诊断，不能制作完整支持域版本：'+candidate['full_timeline_check'].get('reason', '缺少合法取样'))
    binding = {'mask_fingerprint': ctx['fingerprint'], 'candidate_fingerprint': candidate['fingerprint'],
               'source_sha256': ctx['binding']['assets']['source']['sha256'],
               'candidate_sha256': candidate['binding']['candidate_sha256'],
               'segment_id': s['id'], 'first_frame': ctx['first_frame'], 'last_frame': ctx['last_frame'],
               'width': ctx['width'], 'height': ctx['height'], 'fps_rational': ctx['fps_rational'],
               'frame_count': ctx['frame_count']}
    return ctx, candidate, binding


def _version(p, version_id, binding, *, snapshot=None):
    if not version_id:
        if snapshot is not None:
            snapshot.update(version_id=None)
        return None
    registered = {a['id']: a for a in p.get('assets', [])}
    asset = copy.deepcopy(registered.get(version_id))
    if not asset or asset.get('kind') != 'mask_support_version':
        raise ValueError('支持域版本未登记或清单已改变；当前文件未覆盖')
    raw = Path(asset['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != asset.get('sha256'):
        raise ValueError('支持域版本未登记或清单已改变；当前文件未覆盖')
    # Hash and parse the same bytes. A second path read could otherwise parse
    # a different manifest than the one whose catalogue hash just passed.
    value = json.loads(raw)
    if value.get('schema') != SCHEMA or value.get('binding') != binding:
        raise ValueError('支持域版本属于另一候选、原片遮罩或时间轴；请重新核对')
    observed = {'version_id': version_id, 'asset': asset, 'manifest_sha256': asset['sha256'], 'layers': {}}
    for role in ROLES:
        layer = value['layers'][role]
        current = copy.deepcopy(registered.get(layer['id']))
        if not current or current.get('kind') != 'mask_support_layer' or current['path'] != layer['path'] or current.get('sha256') != layer['sha256'] or media._hash(current['path']) != layer['sha256']:
            raise ValueError('支持域图层已改变或缺失；旧版本和候选保留')
        observed['layers'][role] = {'asset': current, 'manifest_reference': copy.deepcopy(layer), 'actual_sha256': layer['sha256']}
    if snapshot is not None:
        snapshot.update(observed)
    return value


def _material_snapshot(p, s, ctx, candidate, binding):
    registered = {a['id']: a for a in p.get('assets', [])}
    assets = {'source': p['source'], 'edit_mask': s['edit_mask'], 'protection_mask': s.get('protection_mask'), 'candidate': candidate['_asset']}
    hashes = {key: (ctx['binding']['assets'][key] or {}).get('sha256') for key in ('source', 'edit_mask', 'protection_mask')}
    hashes['candidate'] = candidate['binding']['candidate_sha256']
    return {'binding': copy.deepcopy(binding), 'source_meta': copy.deepcopy(p.get('source_meta')),
            'timeline_duration': p.get('timeline_duration'),
            'assets': {key: {'reference': copy.deepcopy(asset), 'catalogue': copy.deepcopy(registered.get(asset['id'])), 'actual_sha256': hashes[key]} if asset else None for key, asset in assets.items()}}


def _work_snapshot(p, s, ctx, candidate, binding, version_snapshot, imports=()):
    return {'materials': _material_snapshot(p, s, ctx, candidate, binding),
            'version': copy.deepcopy(version_snapshot), 'imports': copy.deepcopy(list(imports))}


def _assert_work_snapshot(p, s, expected):
    binding = expected['materials']['binding']
    candidate_id = expected['materials']['assets']['candidate']['reference']['id']
    ctx, candidate, current = _context(p, s, candidate_id, binding['candidate_fingerprint'], binding['mask_fingerprint'])
    if _material_snapshot(p, s, ctx, candidate, current) != expected['materials']:
        raise ValueError('核验后输入素材、登记信息或时间轴已改变，旧成果保留')
    observed = {}
    _version(p, expected['version']['version_id'], current, snapshot=observed)
    if observed != expected['version']:
        raise ValueError('核验后父版本或图层登记信息已改变，旧成果保留')
    _assert_imports(expected['imports'])


def _file_snapshots(paths):
    return {key: {'path': str(path), 'sha256': media._hash(path), 'bytes': Path(path).stat().st_size} for key, path in paths.items()}


def _assert_files(expected):
    for item in expected.values():
        try:
            valid = Path(item['path']).stat().st_size == item['bytes'] and media._hash(item['path']) == item['sha256']
        except OSError:
            valid = False
        if not valid:
            raise ValueError('核验后的支持域输出已改变或缺失，拒绝登记；旧成果保留')


def _asset_record(pid, kind, verified, meta=None):
    """Use the verified SHA, never a fresh digest under an older approval."""
    aid = store.uid()
    return {'id': aid, 'name': Path(verified['path']).name, 'path': str(Path(verified['path']).resolve()),
            'kind': kind, 'sha256': verified['sha256'], 'bytes': verified['bytes'], 'meta': meta or {},
            'url': f'/api/media/{pid}/{aid}'}


def _write_verified_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()
    Path(path).write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _commit_verified(pid, sid, inputs, files, assets):
    # Caller holds LOCK. Re-read at the final commit boundary, after metadata
    # probing and report creation, then append the complete batch atomically.
    latest = store.get_project(pid)
    _assert_work_snapshot(latest, mr._segment(latest, sid), inputs)
    _assert_files(files)
    latest.setdefault('assets', []).extend(assets)
    store.put_project(latest)


def _review_records(records, ctx):
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError('局部检查记录须为列表，每次最多1000项')
    result = {}
    for item in records:
        first, last = item.get('first_frame'), item.get('last_frame')
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (first, last)) or not ctx['first_frame'] <= first <= last <= ctx['last_frame']:
            raise ValueError('检查记录须绑定当前片段的明确源帧')
        if last-first+1 > max(1, int(float(Fraction(ctx['fps_rational']))*2+0.999999)):
            raise ValueError('每次局部检查范围最多2秒，不自动传播到其它镜头')
        values = {key: item.get(key, 'unreviewed') for key in ('content', 'protection', 'cleanup')}
        if values['content'] not in ('unreviewed', 'ok', 'missing_content', 'misaligned') or values['protection'] not in ('unreviewed', 'all_foreground_checked', 'needs_fix') or values['cleanup'] not in ('unreviewed', 'allow_candidate_background'):
            raise ValueError('局部内容、全部前景保护或清除来源记录无效')
        for frame in range(first, last+1):
            result[str(frame)] = copy.deepcopy(values)
    return result


def analyze_frame(layers, base_removal, base_protect, review, active):
    import numpy as np
    r, f, support, additional = (layers[k] == 255 for k in ROLES)
    protect = (base_protect == 255) | additional
    foreground = f & support & ~protect
    cleanup = r & support & ~f & ~protect
    findings = []
    if active:
        if not np.any(base_removal) and np.any(r | f | support):
            findings.append('SOURCE_PASSTHROUGH_FRAME_EDITED')
        if np.any(f & ~support & ~protect):
            findings.append('FOREGROUND_OUTSIDE_SUPPORT')
        if np.any(r & ~support & ~protect):
            findings.append('OLD_REMOVAL_NOT_COVERED')
        if review.get('content') != 'ok':
            findings.append({'missing_content': 'GENERATED_CONTENT_MISSING', 'misaligned': 'CANDIDATE_MISALIGNED'}.get(review.get('content'), 'CONTENT_REVIEW_REQUIRED'))
        if review.get('protection') != 'all_foreground_checked':
            findings.append('ALL_FOREGROUND_REVIEW_REQUIRED')
        if np.any(cleanup) and review.get('cleanup') != 'allow_candidate_background':
            findings.append('CLEANUP_SOURCE_NOT_ALLOWED')
    edge = bool(np.any(f[0]) or np.any(f[-1]) or np.any(f[:, 0]) or np.any(f[:, -1]))
    return {'blocking': findings, 'foreground_pixels': int(foreground.sum()), 'cleanup_pixels': int(cleanup.sum()),
            'protected_pixels': int(protect.sum()), 'unused_support_pixels': int((support & ~r & ~f).sum()),
            'foreground_touches_frame_edge': edge, 'active': bool(active)}


def composite_frame(source, candidate, layers, base_protect, *, active=True, cleanup_allowed=False):
    import numpy as np
    result = source.copy()
    if not active:
        return result
    r, f, support, additional = (layers[k] == 255 for k in ROLES)
    protected = (base_protect == 255) | additional
    selected = f & support & ~protected
    if cleanup_allowed:
        selected |= r & support & ~f & ~protected
    result[selected] = candidate[selected]
    return result


def _validate_import(p, s, candidate, binding, manifest_path):
    path = Path(manifest_path).expanduser().resolve()
    raw = path.read_bytes()
    value = json.loads(raw)
    if value.get('schema') != 'BOUND_SUPPORT_MASK_IMPORT_V1' or value.get('binding') != binding:
        raise ValueError('导入清单的候选、原片遮罩或源帧时间轴绑定不一致')
    role, timeline = value.get('role'), value.get('timeline')
    if role not in ROLES or timeline not in ('source', 'candidate') or timeline == 'candidate' and role != 'foreground':
        raise ValueError('只有候选前景matte可用候选原生时间轴；其它图层须使用源网格')
    raw_path = Path(value.get('mask_path', '')).expanduser()
    mask_path = (path.parent/raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
    if not value.get('mask_sha256') or media._hash(mask_path) != value['mask_sha256']:
        raise ValueError('导入遮罩真实SHA256与绑定清单不一致')
    check = rm.validate_mask(candidate['_asset']['path'] if timeline == 'candidate' else p['source']['path'], mask_path,
                             0 if timeline == 'candidate' else s['start'], None if timeline == 'candidate' else s['end'], allow_empty=True)
    if not check['ready']:
        raise ValueError('导入图层不是对应时间轴的原尺寸、原帧率、原帧数纯二值视频：'+'；'.join(i['message'] for i in check['issues']))
    return {**value, 'mask_path': str(mask_path), 'manifest_path': str(path), 'manifest_sha256': hashlib.sha256(raw).hexdigest()}


class _Masks:
    def __init__(self, path, width, height, count, errors):
        self.width, self.height, self.count = width, height, count
        self.proc = rm._reader(path, width, height, gray=True, errors=errors)
        self.index, self.last = -1, None

    def read(self, index):
        if index < self.index:
            raise ValueError('图层时间映射不是单调序列，不能跨镜回填')
        while self.index < index:
            self.last = rm._binary_frame(self.proc, self.width, self.height).astype('uint8')*255
            self.index += 1
        return self.last.copy()

    def finish(self):
        self.read(self.count-1)
        self.proc.stdout.close()
        if self.proc.wait(timeout=30):
            raise media.MediaError('支持域图层解码失败')


def _streams(p, s, version, ctx, errors):
    readers = {role: _Masks(version['layers'][role]['path'], ctx['width'], ctx['height'], ctx['frame_count'], errors) for role in ROLES} if version else {}
    readers['base_removal'] = _Masks(s['edit_mask']['path'], ctx['width'], ctx['height'], ctx['frame_count'], errors)
    if s.get('protection_mask'):
        readers['base_protect'] = _Masks(s['protection_mask']['path'], ctx['width'], ctx['height'], ctx['frame_count'], errors)
    return readers


def _layers_at(readers, index, version, shape):
    import numpy as np
    base = readers['base_removal'].read(index)
    protect = readers['base_protect'].read(index) if 'base_protect' in readers else np.zeros(shape, np.uint8)
    layers = {role: readers[role].read(index) if version else base.copy() if role == 'removal' else np.zeros(shape, np.uint8) for role in ROLES}
    return layers, base, protect


def _assert_imports(imports):
    for item in imports:
        if media._hash(item['mask_path']) != item['mask_sha256'] or media._hash(item['manifest_path']) != item['manifest_sha256']:
            raise ValueError('导入期间图层或绑定清单已改变，旧版本未覆盖')


def save_version(p, s, body, outdir, prefix):
    from PIL import Image
    import numpy as np
    ctx, candidate, binding = _context(p, s, body.get('candidate_id'), body.get('candidate_fingerprint'), body.get('mask_fingerprint'))
    parent_snapshot = {}
    parent = _version(p, body.get('version_id'), binding, snapshot=parent_snapshot)
    operations = mask_editor.validate_support_operations(body.get('operations', []), ctx)
    records = _review_records(body.get('reviews', []), ctx)
    raw_imports = body.get('imports', [])
    if not isinstance(raw_imports, list) or len(raw_imports) > 4:
        raise ValueError('每次最多导入四个独立图层清单')
    imports = [_validate_import(p, s, candidate, binding, path) for path in raw_imports]
    inputs = _work_snapshot(p, s, ctx, candidate, binding, parent_snapshot, imports)
    if len({item['role'] for item in imports}) != len(imports):
        raise ValueError('同一图层不能在一次保存中导入两个文件')
    if not operations and not records and not imports:
        raise ValueError('请先手工标注、导入图层或记录局部检查')
    mr._technical(p, s, ctx)
    root = Path(outdir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {role: media._new_output(root/f'{prefix}_support_{role}.mkv') for role in ROLES}
    processes, readers = [], {}
    active = set(parent.get('active_frames', [])) if parent else set()
    reviews = copy.deepcopy(parent.get('frame_reviews', {})) if parent else {}
    for operation in operations:
        active.update(range(operation['first_frame'], operation['last_frame']+1))
    active.update(int(frame) for frame in records)
    reports = {}
    origins = {role: ['PARENT_VERSION' if parent else 'SOURCE_EDIT_MASK_BASE' if role == 'removal' else 'EMPTY_MANUAL_BASE'] for role in ROLES}
    for item in imports:
        origins[item['role']] = ['BOUND_'+item['timeline'].upper()+'_GRID_IMPORT']
    for item in operations:
        for role in ('foreground', 'support') if item['tool'] == 'seed_from_removal' else (item['role'],):
            label = 'MANUAL_SEED_NOT_SEGMENTATION' if item['tool'] == 'seed_from_removal' else 'LOCAL_MANUAL_'+item['tool'].upper()
            if label not in origins[role]:
                origins[role].append(label)
    try:
        with tempfile.TemporaryFile() as errors:
            readers = _streams(p, s, parent, ctx, errors)
            processes.extend(reader.proc for reader in readers.values())
            imported = {}
            for item in imports:
                native = item['timeline'] == 'candidate'
                width, height = candidate['candidate_native_size'] if native else (ctx['width'], ctx['height'])
                reader = _Masks(item['mask_path'], width, height, candidate['candidate_frame_count'] if native else ctx['frame_count'], errors)
                imported[item['role']] = (item, reader)
                processes.append(reader.proc)
            writers = {role: mask_editor._writer(path, ctx['width'], ctx['height'], ctx['fps_rational'], errors, True) for role, path in paths.items()}
            processes.extend(writers.values())
            for index, frame in enumerate(range(ctx['first_frame'], ctx['last_frame']+1)):
                layers, base, protect = _layers_at(readers, index, parent, (ctx['height'], ctx['width']))
                before = {role: pixels.copy() for role, pixels in layers.items()}
                for role, (item, reader) in imported.items():
                    sample = candidate['_mapping'][frame]['candidate_frame']-1 if item['timeline'] == 'candidate' else index
                    pixels = reader.read(sample)
                    if pixels.shape != base.shape:
                        pixels = np.array(Image.fromarray(pixels).resize((ctx['width'], ctx['height']), Image.Resampling.NEAREST))
                    layers[role] = pixels
                    if np.any(pixels):
                        active.add(frame)
                layers = mask_editor.patch_support_frame(layers, frame, operations)
                if any(np.any(layers[role] != before[role]) for role in ROLES):
                    reviews[str(frame)] = copy.deepcopy(records.get(str(frame), {}))
                elif str(frame) in records:
                    reviews[str(frame)] = copy.deepcopy(records[str(frame)])
                reports[str(frame)] = analyze_frame(layers, base, protect, reviews.get(str(frame), {}), frame in active)
                for role, writer in writers.items():
                    writer.stdin.write(layers[role].tobytes())
            for reader in [*readers.values(), *(reader for _, reader in imported.values())]:
                reader.finish()
            for writer in writers.values():
                writer.stdin.close()
            for writer in writers.values():
                if writer.wait(timeout=180):
                    raise media.MediaError('支持域派生图层编码失败，原件未覆盖')
        verified_outputs = _file_snapshots(paths)
        for path in paths.values():
            if not rm.validate_mask(p['source']['path'], path, s['start'], s['end'], allow_empty=True)['ready']:
                raise media.MediaError('支持域派生图层原尺寸、帧率、帧数或纯二值回读失败')
        _assert_work_snapshot(p, s, inputs)
        _assert_files(verified_outputs)
        return {'paths': paths, 'binding': binding, 'parent_version_id': body.get('version_id'), 'active_frames': sorted(active),
                'frame_reviews': reviews, 'frame_reports': reports, 'imports': [{k: v for k, v in item.items() if k != 'mask_path'} for item in imports],
                'operations': operations, 'role_origins': origins, 'automatic_segmentation': False,
                'blocking_frames': [int(frame) for frame, report in reports.items() if report['blocking']],
                'strict_final_approved': False, 'six_checks_approved': False, 'review_required': True,
                'verified_parent_version_sha256': parent_snapshot.get('manifest_sha256'),
                '_verified_inputs': inputs, '_verified_outputs': verified_outputs}
    except Exception:
        mr._finish(processes)
        for path in paths.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        mr._finish(processes)


def status(p, s, candidate_id, candidate_fingerprint, mask_fingerprint):
    ctx, candidate, binding = _context(p, s, candidate_id, candidate_fingerprint, mask_fingerprint)
    versions = []
    for asset in p.get('assets', []):
        if asset.get('kind') != 'mask_support_version':
            continue
        try:
            value = json.loads(Path(asset['path']).read_text())
            if value.get('binding', {}).get('segment_id') != s['id']:
                continue
            current = _version(p, asset['id'], binding)
            versions.append({'id': asset['id'], 'created_at': current['created_at'], 'current': True,
                             'active_frame_count': len(current['active_frames']), 'blocking_frames': current['blocking_frames']})
        except (OSError, ValueError, KeyError):
            versions.append({'id': asset['id'], 'current': False, 'note': '旧候选/旧时间轴或文件已改变；保留历史，不能直接合成'})
    outputs = []
    for asset in p.get('assets', []):
        if asset.get('kind') == 'mask_support_report':
            try:
                value = json.loads(Path(asset['path']).read_text())
                if value.get('binding') == binding and media._hash(asset['path']) == asset['sha256']:
                    current_version = {}
                    _version(p, value['version_id'], binding, snapshot=current_version)
                    current_inputs = _work_snapshot(p, s, ctx, candidate, binding, current_version)
                    if value.get('verified_inputs_fingerprint') != _hash_json(current_inputs):
                        continue
                    for key in ('master', 'preview'):
                        output = value[key]
                        if value.get(f'verified_{key}_sha256') != output['sha256'] or media._hash(output['path']) != output['sha256'] or not any(item['id'] == output['id'] for item in p.get('assets', [])):
                            raise ValueError('派生输出已改变，不能作为当前输出展示')
                    outputs.append({'version_id': value['version_id'], 'master': value['master'], 'preview': value['preview'], 'report': asset})
            except (OSError, ValueError, KeyError):
                continue
    return {'binding': binding, 'versions': versions, 'outputs': outputs, 'strict_final_approved': False,
            'import_template': {'schema': 'BOUND_SUPPORT_MASK_IMPORT_V1', 'binding': binding, 'role': 'foreground',
                                'timeline': 'source', 'mask_path': '', 'mask_sha256': ''},
            'note': '独立本地派生版本；R清除原人物，F保留候选前景，S允许取样，原保护与补充保护总是取原片。'}


def frame_data(p, s, frame, body):
    import numpy as np
    from PIL import Image
    from io import BytesIO
    import base64
    ctx, candidate, binding = _context(p, s, body.get('candidate_id'), body.get('candidate_fingerprint'), body.get('mask_fingerprint'))
    version = _version(p, body.get('version_id'), binding)
    result = mr.frame_data(p, s, frame, ctx['fingerprint'], candidate['id'], candidate['fingerprint'])
    arrays = {key: np.array(Image.open(BytesIO(base64.b64decode(value.split(',')[1])))) for key, value in result['images'].items()}
    layers = {role: arrays['edit_mask'].copy() if role == 'removal' else np.zeros_like(arrays['edit_mask']) for role in ROLES}
    if version:
        processes = []
        try:
            for role in ROLES:
                proc = rm._reader(version['layers'][role]['path'], ctx['width'], ctx['height'], first=frame-ctx['first_frame'], stop=frame-ctx['first_frame']+1, gray=True)
                processes.append(proc)
                layers[role] = mr._binary(proc, ctx)
                proc.stdout.close()
                if proc.wait(timeout=30):
                    raise media.MediaError('支持域当前帧解码失败')
        finally:
            mr._finish(processes)
    review = (version or {}).get('frame_reviews', {}).get(str(frame), {})
    active = frame in (version or {}).get('active_frames', [])
    for role, pixels in layers.items():
        buffer = BytesIO()
        Image.fromarray(pixels).save(buffer, format='PNG')
        result['images'][role] = 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    result.update(support_binding=binding, version_id=body.get('version_id'), frame_review=review, active=active,
                  support_report=analyze_frame(layers, arrays['edit_mask'], arrays['protection_mask'], review, active),
                  strict_final_approved=False)
    _context(p, s, candidate['id'], candidate['fingerprint'], ctx['fingerprint'])
    _version(p, body.get('version_id'), binding)
    return result


def _master_writer(path, ctx, errors):
    return subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-s', f"{ctx['width']}x{ctx['height']}", '-r', ctx['fps_rational'], '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-level', '3',
        '-pix_fmt', 'bgr0', '-threads', '2', '-enc_time_base', str(1/Fraction(ctx['fps_rational'])), '-fps_mode', 'passthrough', str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)


def render(p, s, body, outdir, prefix):
    from PIL import Image
    import numpy as np
    ctx, candidate, binding = _context(p, s, body.get('candidate_id'), body.get('candidate_fingerprint'), body.get('mask_fingerprint'))
    version_snapshot = {}
    version = _version(p, body.get('version_id'), binding, snapshot=version_snapshot)
    inputs = _work_snapshot(p, s, ctx, candidate, binding, version_snapshot)
    if not version or not version['active_frames']:
        raise ValueError('请先保存明确范围的本地支持域版本')
    if version['blocking_frames']:
        raise ValueError('支持域仍有待修/未核对帧，不能合成：'+','.join(map(str, version['blocking_frames'][:16])))
    paths = {'master': media._new_output(Path(outdir)/f'{prefix}_support_master.mkv'), 'preview': media._new_output(Path(outdir)/f'{prefix}_support_preview.mp4')}
    processes = []
    active = set(version['active_frames'])
    try:
        with tempfile.TemporaryFile() as errors:
            readers = _streams(p, s, version, ctx, errors)
            processes.extend(reader.proc for reader in readers.values())
            source = rm._reader(p['source']['path'], ctx['width'], ctx['height'], first=ctx['first_frame']-1, stop=ctx['last_frame'], errors=errors)
            cw, ch = candidate['candidate_native_size']
            proposed = rm._reader(candidate['_asset']['path'], cw, ch, errors=errors)
            processes += [source, proposed]
            master, preview = _master_writer(paths['master'], ctx, errors), mask_editor._writer(paths['preview'], ctx['width'], ctx['height'], ctx['fps_rational'], errors, False)
            processes += [master, preview]
            cursor, candidate_pixels = -1, None
            for index, frame in enumerate(range(ctx['first_frame'], ctx['last_frame']+1)):
                target = candidate['_mapping'][frame]['candidate_frame']-1
                while cursor < target:
                    candidate_pixels = np.frombuffer(rm._read_exact(proposed.stdout, cw*ch*3), np.uint8).reshape(ch, cw, 3)
                    cursor += 1
                pixels = np.array(Image.fromarray(candidate_pixels).resize((ctx['width'], ctx['height']), Image.Resampling.LANCZOS))
                original = np.frombuffer(rm._read_exact(source.stdout, ctx['width']*ctx['height']*3), np.uint8).reshape(ctx['height'], ctx['width'], 3)
                layers, base, protect = _layers_at(readers, index, version, original.shape[:2])
                review = version['frame_reviews'].get(str(frame), {})
                check = analyze_frame(layers, base, protect, review, frame in active)
                if check['blocking']:
                    raise ValueError(f'源帧{frame}支持域检查已改变，停止合成')
                output = composite_frame(original, pixels, layers, protect, active=frame in active, cleanup_allowed=review.get('cleanup') == 'allow_candidate_background')
                master.stdin.write(output.tobytes())
                preview.stdin.write(output.tobytes())
            while cursor < candidate['candidate_frame_count']-1:
                rm._read_exact(proposed.stdout, cw*ch*3)
                cursor += 1
            for reader in readers.values():
                reader.finish()
            source.stdout.close()
            proposed.stdout.close()
            if source.wait(timeout=30) or proposed.wait(timeout=30):
                raise media.MediaError('支持域合成输入解码失败')
            for writer in (master, preview):
                writer.stdin.close()
            for writer in (master, preview):
                if writer.wait(timeout=180):
                    raise media.MediaError('支持域合成编码失败')
        verified_outputs = _file_snapshots(paths)
        if not all(rm.validate_candidate(p['source']['path'], path, s['start'], s['end'])['ready'] for path in paths.values()):
            raise media.MediaError('支持域输出尺寸、帧率或帧数回读失败')
        # Re-read the lossless master and enforce source identity at protected
        # pixels and everywhere outside the explicitly permitted sampling set.
        with tempfile.TemporaryFile() as errors:
            readers = _streams(p, s, version, ctx, errors)
            processes.extend(reader.proc for reader in readers.values())
            source = rm._reader(p['source']['path'], ctx['width'], ctx['height'], first=ctx['first_frame']-1, stop=ctx['last_frame'], errors=errors)
            output = rm._reader(paths['master'], ctx['width'], ctx['height'], errors=errors)
            processes += [source, output]
            preserved_samples, passthrough_frames = 0, 0
            for index, frame in enumerate(range(ctx['first_frame'], ctx['last_frame']+1)):
                original = np.frombuffer(rm._read_exact(source.stdout, ctx['width']*ctx['height']*3), np.uint8).reshape(ctx['height'], ctx['width'], 3)
                actual = np.frombuffer(rm._read_exact(output.stdout, ctx['width']*ctx['height']*3), np.uint8).reshape(ctx['height'], ctx['width'], 3)
                layers, _, protect = _layers_at(readers, index, version, original.shape[:2])
                r, f, support, additional = (layers[k] == 255 for k in ROLES)
                allowed = ((f | r) & support & ~((protect == 255) | additional)) if frame in active else np.zeros(r.shape, bool)
                if not np.array_equal(actual[~allowed], original[~allowed]):
                    raise media.MediaError('保护区或未授权区域像素发生变化，本次派生合成未登记')
                preserved_samples += int((~allowed).sum())
                passthrough_frames += int(not np.any(allowed))
            for reader in readers.values():
                reader.finish()
            source.stdout.close()
            output.stdout.close()
            if source.wait(timeout=30) or output.wait(timeout=30):
                raise media.MediaError('无损支持域回读解码失败')
        _assert_work_snapshot(p, s, inputs)
        _assert_files(verified_outputs)
        return {**paths, 'binding': binding, 'version_id': body['version_id'], 'active_frames': sorted(active),
                'source_passthrough_frames': passthrough_frames, 'preserved_pixel_samples': preserved_samples,
                'protected_and_outside_pixels_exact': True, 'strict_final_approved': False, 'six_checks_approved': False,
                'candidate_audio_used': False, 'audio_exported': False, 'frames_retimed': 0, 'frames_padded': 0,
                'verified_master_sha256': verified_outputs['master']['sha256'],
                'verified_preview_sha256': verified_outputs['preview']['sha256'],
                'verified_inputs_fingerprint': _hash_json(inputs),
                'verified_version_sha256': version_snapshot['manifest_sha256'],
                'verified_layer_sha256': {role: item['actual_sha256'] for role, item in version_snapshot['layers'].items()},
                '_verified_inputs': inputs, '_verified_outputs': verified_outputs,
                'note': '仅独立本地派生，非最终验收；无损母版已回读保护和范围外像素，MP4仅预览，原声音轨仍待原流程接回。'}
    except Exception:
        mr._finish(processes)
        for path in paths.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        mr._finish(processes)


def action(pid, body, jid):
    p = store.get_project(pid)
    s = mr._segment(p, body.get('segment_id'))
    outdir = store.DATA/'projects'/pid/'exports'
    outdir.mkdir(parents=True, exist_ok=True)
    if body['action'] == 'save_mask_support':
        result = save_version(p, s, body, outdir, jid)
        inputs, verified = result['_verified_inputs'], result['_verified_outputs']
        with store.LOCK:
            latest = store.get_project(pid)
            _assert_work_snapshot(latest, mr._segment(latest, s['id']), inputs)
            _assert_files(verified)
            layers = {role: _asset_record(pid, 'mask_support_layer', verified[role], media.probe(path)) for role, path in result['paths'].items()}
            manifest = {**{key: value for key, value in result.items() if key != 'paths' and not key.startswith('_')},
                        'schema': SCHEMA, 'layers': layers, 'created_at': store.now()}
            path = media._new_output(outdir/f'{jid}_support_version.json')
            manifest_file = _write_verified_json(path, manifest)
            asset = _asset_record(pid, 'mask_support_version', manifest_file)
            _commit_verified(pid, s['id'], inputs, {**verified, 'manifest': manifest_file}, [*layers.values(), asset])
        return {'asset': asset, 'version_id': asset['id'], 'blocking_frames': manifest['blocking_frames'],
                'message': '已另存独立支持域版本；待修帧仍阻断合成，原mask、候选和保护原件保留'}
    if body['action'] == 'render_mask_support':
        result = render(p, s, body, outdir, jid)
        inputs, verified = result['_verified_inputs'], result['_verified_outputs']
        with store.LOCK:
            latest = store.get_project(pid)
            _assert_work_snapshot(latest, mr._segment(latest, s['id']), inputs)
            _assert_files(verified)
            master = _asset_record(pid, 'mask_support_master', verified['master'])
            preview = _asset_record(pid, 'mask_support_preview', verified['preview'])
            report_value = {**{key: value for key, value in result.items() if key not in ('master', 'preview') and not key.startswith('_')},
                            'master': master, 'preview': preview}
            path = media._new_output(outdir/f'{jid}_support_composite_report.json')
            report_file = _write_verified_json(path, report_value)
            report = _asset_record(pid, 'mask_support_report', report_file)
            _commit_verified(pid, s['id'], inputs, {**verified, 'report': report_file}, [master, preview, report])
        return {'asset': preview, 'master': master, 'report': report, 'version_id': body['version_id'],
                'message': '独立本地支持域合成已保存；保护与范围外像素回读通过，实际六项仍待验收'}
    raise ValueError('未知本地支持域操作')
