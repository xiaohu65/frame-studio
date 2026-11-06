"""Read-only identity checks for reusing an already received paid sample.

This module cannot prepare quotes, authorize uploads, or submit generation.
Masks belong to local derivations; every actual generation input is frozen.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import store, replacement

VERSION = 'COMPLETED_SAMPLE_INPUT_IDENTITY_V1'
_cache = {}
_digests = {}


def file_digest(path, *, cached=False):
    path = str(path)
    info = Path(path).stat()
    signature = (info.st_size, info.st_mtime_ns, info.st_ino)
    if cached and path in _digests and _digests[path][0] == signature:
        return _digests[path][1]
    digest = store.digest(path)
    if len(_digests) > 128:
        _digests.clear()
    _digests[path] = (signature, digest)
    return digest


def local_binding(project, sid, input_fingerprint, candidate, *, recorded=False, cached=False):
    segment = _segment(project, sid) or {}
    def mask(kind):
        asset = segment.get(kind)
        return None if not asset else {'id': asset.get('id'),
            'sha256': asset.get('sha256') if recorded else file_digest(asset['path'], cached=cached)}
    return _hash({'version': 'SAMPLE_LOCAL_BINDING_V1', 'project_id': project['id'], 'segment_id': sid,
        'input_fingerprint': input_fingerprint, 'candidate_asset_id': candidate['id'], 'candidate_sha256': candidate['sha256'],
        'edit_mask': mask('edit_mask'), 'protection_mask': mask('protection_mask'), 'mask_keyframes': segment.get('mask_keyframes', [])})


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _segment(project, sid):
    return next((s for s in project.get('segments', []) if s['id'] == sid), None)


def _asset(asset, live):
    if not asset:
        return None
    return {'id': asset.get('id'), 'sha256': store.digest(asset['path']) if live else asset.get('sha256')}


def _context(project, sid, live):
    segment = _segment(project, sid)
    if segment is None:
        raise ValueError('原生成片段已移除')
    actors = copy.deepcopy(project.get('source_actors', []))
    for actor in actors:
        actor['observations'] = [o for o in actor.get('observations', []) if o.get('segment_id') == sid]
    scene = copy.deepcopy(project.get('scene', {}))
    scene['images'] = [_asset(a, live) for a in scene.get('images', [])]
    scene['regions'] = [r for r in scene.get('regions', []) if r.get('segment_id') == sid]
    return {'project_id': project['id'], 'source': _asset(project.get('source'), live),
        'source_meta': project.get('source_meta'), 'segment': {k: segment.get(k) for k in ('id', 'start', 'end')},
        'actors': actors, 'roles': [{k: r.get(k) for k in ('id', 'name', 'description', 'color')} |
            {'images': [_asset(a, live) for a in r.get('images', [])]} for r in project.get('roles', [])],
        'scene': scene, 'manual_prompt': project.get('prompts', {}).get(sid, ''),
        'settings': project.get('settings'), 'preservation': project.get('preservation', replacement.PRESERVATION),
        'series_link': project.get('series_link')}


def _files(project, run, sid, candidate):
    paths = [candidate.get('path'), (project.get('source') or {}).get('path'), ((_segment(project, sid) or {}).get('reference') or {}).get('path')]
    paths += [a.get('path') for r in project.get('roles', []) for a in r.get('images', [])]
    paths += [a.get('path') for a in project.get('scene', {}).get('images', [])]
    paths += [a.get('path') for entry in run.get('segments', []) for a in entry.get('media', [])]
    return tuple((str(path), info.st_size, info.st_mtime_ns, info.st_ino) for path in paths if path
                 for info in [Path(path).stat()])


def completed_input_identity(project, run, sid, candidate, *, cached=False):
    """Compare current inputs with one COMPLETED segment's immutable receipt."""
    result = {'version': VERSION, 'input_current': False, 'input_fingerprint': None,
        'project_id': project.get('id'), 'segment_id': sid, 'run_id': run.get('id'),
        'candidate_asset_id': candidate.get('id'), 'candidate_sha256': candidate.get('sha256'),
        'reason': None, 'cloud_allowed': False, 'generation_authorized': False}
    try:
        key = _hash({'project': project, 'run': run, 'segment_id': sid, 'candidate': candidate})
        signature = _files(project, run, sid, candidate)
        if cached and key in _cache and _cache[key][0] == signature:
            return copy.deepcopy(_cache[key][1])
        snapshot = run.get('snapshot') or {}
        current = _segment(project, sid)
        frozen = next((s for s in run.get('segments', []) if s['segment_id'] == sid), None)
        provenance = (current or {}).get('sample_provenance') or {}
        if (run.get('project_id') != project['id'] or snapshot.get('id') != project['id']
                or run.get('purpose') != 'candidate_sample' or run.get('status') not in {'COMPLETED', 'PARTIAL'}
                or not frozen or frozen.get('status') != 'COMPLETED' or sid not in run.get('segment_ids', [])
                or not current or provenance.get('run_id') != run['id']
                or provenance.get('asset_id') != candidate.get('id')
                or (current.get('sample_candidate') or {}).get('id') != candidate.get('id')):
            raise ValueError('仅可复用本项目、本片段已完成并已接收的当前候选')
        asset = frozen.get('asset') or {}
        if (not candidate.get('id') or any(candidate.get(k) != asset.get(k) for k in ('id', 'sha256', 'path'))
                or store.digest(candidate['path']) != asset.get('sha256')):
            raise ValueError('当前候选与原批次结果不一致，不能替换、跨段或跨项目复用')
        before, after = _context(snapshot, sid, False), _context(project, sid, True)
        if before != after:
            raise ValueError('源片、片段、角色引用、提示词或模型设置已变化；不能普通复用原候选')
        if run.get('settings') != snapshot.get('settings'):
            raise ValueError('原批次模型参数与冻结项目不一致')
        for item in frozen.get('media', []):
            if not item.get('path') or store.digest(item['path']) != item.get('sha256'):
                raise ValueError('冻结的实际上传媒体已变化或缺失，无法核实原生成输入')
        videos = [m for m in frozen['media'] if m['type'] == 'reference_video']
        if len(videos) != 1 or store.digest((current.get('reference') or {})['path']) != videos[0]['sha256']:
            raise ValueError('当前原片分段参考与冻结上传视频不一致')
        references = replacement.plan(project, sid)['references']
        images = [m for m in frozen['media'] if m['type'] == 'reference_image']
        if [store.digest(r['path']) for r in references] != [m['sha256'] for m in images]:
            raise ValueError('参考图的字节或顺序与实际上传清单不一致')
        policy = run.get('input_policy') or {}
        audios = [m for m in frozen['media'] if m['type'] == 'reference_audio']
        contract = (run.get('input_contract') or {}).get('source_audio', {}).get(sid)
        prompt = snapshot.get('prompts', {}).get(sid, '')
        if policy.get('include_source_audio'):
            from . import generation_audio
            if len(audios) != 1 or not contract or contract.get('sha256') != audios[0]['sha256']:
                raise ValueError('原声参考合同与冻结音频字节不一致')
            generation_audio.validate_reference_audio(project['source']['path'], current['start'], current['end'], contract, audios[0]['path'])
            note = generation_audio.reference_prompt_note(contract)
            if contract.get('submission_prompt_note') != note:
                raise ValueError('当前原声参考提示词合同已变化，不能普通复用')
            prompt += '\n\n' + note
        elif audios:
            raise ValueError('原批次未授权原声参考，冻结清单不能追加音频')
        if prompt != frozen.get('prompt') or frozen.get('parameters') != {k: v for k, v in (run.get('settings') or {}).items() if k != 'model'}:
            # preflight adds defaults; compare the requested settings, preserving
            # provider defaults which were already bound by the paid scope.
            settings = run.get('settings') or {}
            if prompt != frozen.get('prompt') or any(frozen.get('parameters', {}).get(k) != v for k, v in settings.items() if k != 'model'):
                raise ValueError('实际提交提示词或生成参数与冻结版本不一致')
        identity = {'version': VERSION, 'context': after, 'actual_prompt': frozen['prompt'],
            'model': run['settings']['model'], 'parameters': frozen['parameters'], 'input_policy': policy,
            'audio_contract': contract, 'media': [{k: m.get(k) for k in ('type', 'sha256', 'bytes', 'duration_seconds')} for m in frozen['media']]}
        result.update(input_current=True, input_fingerprint=_hash(identity))
        if len(_cache) >= 64:
            _cache.clear()
        _cache[key] = (signature, copy.deepcopy(result))
    except (ValueError, OSError, KeyError, TypeError) as error:
        result['reason'] = str(error)
    return result
