"""Explicit, version-bound Wan generation for replacement projects.

Reading state never uploads or submits. Only confirm() starts new paid requests.
Recovery and refresh only inspect existing requests and download their results.
"""
from __future__ import annotations
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from fractions import Fraction
from contextlib import contextmanager

from . import store, replacement, replacement_workflow as rw
from .provider import WanClient, ProviderError, inspect_configuration, _private_json

POLL_SECONDS = 15
QUOTE_SECONDS = 1800
LIVE = {'QUEUED', 'UPLOADING', 'SUBMITTING', 'PENDING', 'RUNNING', 'SUCCEEDED', 'DOWNLOADING'}
AMBIGUOUS = {'UNKNOWN', 'SUBMITTING'}
RETRYABLE = {'FAILED', 'REJECTED', 'UPLOAD_FAILED', 'NOT_SUBMITTED', 'CANCELED'}
_workers = set()
_worker_lock = threading.Lock()
_adoption_lock = threading.Lock()
_state_cache = {}
_public_audio_success = {}
_public_audio_lock = threading.RLock()
_public_audio_flights = {}


def _root(pid):
    store.project_path(pid)  # Validate the identifier before constructing paths.
    root = store.DATA / 'projects' / pid / 'generation'
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


@contextmanager
def _lock(pid):
    with store.LOCK:
        with (_root(pid) / '.lock').open('a') as handle:
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


def _run_path(pid, run_id):
    if not isinstance(run_id, str) or not run_id.isalnum():
        raise ValueError('生成批次编号无效')
    return _root(pid) / 'runs' / run_id / 'run.json'


def _runs(pid):
    return sorted([_read(path) for path in (_root(pid) / 'runs').glob('*/run.json')], key=lambda run: run['created_at'], reverse=True)


def _fp(project):
    return replacement.fingerprint(project)


def _scope_fingerprint(record):
    payload = {key: record.get(key) for key in ('fingerprint', 'purpose', 'segment_ids', 'base_url', 'settings', 'pricing', 'reserved_yuan')}
    payload.update({key: record[key] for key in ('delivery_spec', 'input_policy', 'input_contract', 'material_manifest', 'materials_fingerprint') if key in record})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _input_policy(record):
    policy = record.get('input_policy') or {}
    result = {'include_source_audio': bool(policy.get('include_source_audio', False))}
    if result['include_source_audio']:
        result['source_audio_method'] = policy.get('source_audio_method', 'legacy-unversioned')
    return result


def _material_manifest(entries):
    return [{'segment_id': entry['segment_id'], 'prompt_sha256': hashlib.sha256(entry['prompt'].encode()).hexdigest(),
             'parameters': entry['parameters'], 'media': [{key: asset.get(key) for key in ('type', 'sha256', 'bytes', 'duration_seconds')}
                for asset in entry['media']]} for entry in entries]


def _assert_input_files(record):
    for entry in record.get('media', record.get('segments', [])):
        for asset in entry['media']:
            if asset.get('path') and store.digest(asset['path']) != asset.get('sha256'):
                raise ValueError('已确认的输入文件发生变化，请重新准备材料和报价；未上传或提交')


def _audio_validation_key(record):
    """Cheap real-byte binding; never use mtime alone for a public success."""
    from . import generation_audio
    snapshot = record.get('snapshot') or {}
    source = snapshot.get('source') or {}
    entries = record.get('media') if 'media' in record else record.get('segments', [])
    assets = [source] + [asset for entry in entries for asset in entry.get('media', [])]
    actual = {}
    for asset in assets:
        if not asset.get('path') or not asset.get('sha256'):
            raise ValueError('冻结输入缺少真实文件指纹，不能复用公开校验')
        path = str(Path(asset['path']).resolve())
        digest = actual.setdefault(path, None)
        if digest is None:
            digest = actual[path] = store.digest(path)
        if digest != asset['sha256']:
            raise ValueError('已确认的输入文件发生变化；未使用公开状态缓存')
    payload = {key: record.get(key) for key in ('snapshot', 'settings', 'purpose', 'segment_ids',
        'input_policy', 'input_contract', 'material_manifest', 'materials_fingerprint')}
    payload.update(entries=entries, actual_file_sha256=actual,
                   reference_method=generation_audio.REFERENCE_METHOD,
                   final_tail_policy=generation_audio.FINAL_TAIL_POLICY)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _public_audio_validation(record, *, force=False):
    if not _input_policy(record)['include_source_audio']:
        return
    while True:
        key = _audio_validation_key(record)
        # Only map operations hold the guard: an unrelated cold quote cannot
        # make an already verified quote wait for its expensive media scans.
        with _public_audio_lock:
            hit = not force and key in _public_audio_success
            flight = _public_audio_flights.get(key)
            leader = not hit and flight is None
            if leader:
                flight = {'done': threading.Event(), 'error': None}
                _public_audio_flights[key] = flight
        if hit:
            if _audio_validation_key(record) == key:
                return
            continue
        if not leader:
            flight['done'].wait()
            # Real bytes are checked again after waiting, never consume the
            # key computed before another thread's long validation.
            _audio_validation_key(record)
            if flight['error'] is not None:
                raise ValueError(str(flight['error'])) from None
            continue
        try:
            _validate_current_audio_contract(record)
            if _audio_validation_key(record) != key:
                raise ValueError('核验期间输入文件已变化；未发布公开校验缓存')
            with _public_audio_lock:
                if len(_public_audio_success) >= 32:
                    _public_audio_success.clear()
                _public_audio_success[key] = True
            return
        except Exception as error:
            with _public_audio_lock:
                _public_audio_success.pop(key, None)
                flight['error'] = error
            raise
        finally:
            with _public_audio_lock:
                _public_audio_flights.pop(key, None)
                flight['done'].set()


def _assert_current_audio_contract(record, *, cached=False):
    if cached:
        return _public_audio_validation(record)
    return _validate_current_audio_contract(record)


def _validate_current_audio_contract(record):
    if not _input_policy(record)['include_source_audio']:
        return
    from . import generation_audio
    REFERENCE_METHOD = generation_audio.REFERENCE_METHOD
    contracts = (record.get('input_contract') or {}).get('source_audio') or {}
    if (_input_policy(record).get('source_audio_method') != REFERENCE_METHOD
            or set(contracts) != set(record['segment_ids'])
            or any(c.get('method') != REFERENCE_METHOD or c.get('timeline_contract') != 'REFERENCE_VIDEO_ZERO'
                   or Fraction(c.get('video_first_in_audio_rational', '-1')) != 0 for c in contracts.values())):
        raise ValueError('原声参考对齐合同版本已变化，请重新准备报价与材料确认；未上传或提交')
    snapshot = record.get('snapshot') or {}
    source = (snapshot.get('source') or {}).get('path')
    entries = record.get('media') if 'media' in record else record.get('segments', [])
    for sid in record['segment_ids']:
        segment = next((s for s in snapshot.get('segments', []) if s['id'] == sid), None)
        entry = next((s for s in entries if s['segment_id'] == sid), None)
        audios = [m for m in (entry or {}).get('media', []) if m['type'] == 'reference_audio']
        contract = contracts[sid]
        if not source or not segment or len(audios) != 1 or audios[0].get('sha256') != contract.get('sha256'):
            raise ValueError('冻结原声参考与实际媒体清单不一致，未上传或提交')
        note = generation_audio.reference_prompt_note(contract)
        if (contract.get('submission_prompt_note') != note or
                entry.get('prompt') != snapshot.get('prompts', {}).get(sid, '') + '\n\n' + note):
            raise ValueError('实际提交提示词与原声参考合同不一致，未上传或提交')
        generation_audio.validate_reference_audio(source, segment['start'], segment['end'], contract, audios[0]['path'])


def _source_audio_reference(pid, project, segment):
    from . import generation_audio
    expected = generation_audio.plan_reference_audio(project['source']['path'], segment['start'], segment['end'])
    payload = {'source_sha256': store.digest(project['source']['path']), 'start': segment['start'], 'end': segment['end'], 'method': generation_audio.REFERENCE_METHOD}
    if expected.get('reference_coverage_policy'):
        payload.update(reference_coverage_policy=expected['reference_coverage_policy'],
                       coverage_duration_rational=expected['coverage_duration_rational'])
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    directory = _root(pid) / 'audio'
    directory.mkdir(exist_ok=True)
    dest = directory / (key + '.wav')
    receipt = _read(dest.with_suffix('.wav.json'), {})
    valid = False
    if dest.is_file():
        try:
            generation_audio.validate_reference_audio(project['source']['path'], segment['start'], segment['end'], receipt, dest)
            valid = True
        except (ValueError, OSError, KeyError, TypeError):
            pass
    if not valid:
        if dest.exists():
            dest = directory / (key + '_' + uuid.uuid4().hex + '.wav')
        receipt = generation_audio.prepare_reference_audio(project['source']['path'], dest, segment['start'], segment['end'])
    return receipt


def _assert_scope(record):
    """A quote/run cannot grow beyond its frozen, reviewed selection."""
    ids = record.get('segment_ids')
    entries = record.get('media') if 'media' in record else record.get('segments')
    if (not ids or len(ids) != len(set(ids)) or [item['segment_id'] for item in entries or []] != ids
            or record.get('purpose') not in {'strict_final', 'candidate_sample'}
            or record.get('scope_fingerprint') != _scope_fingerprint(record)):
        raise ValueError('本次用途、片段范围或费用绑定已变化，请重新准备；未授权其他片段')
    if 'material_manifest' in record:
        if _material_manifest(entries) != record['material_manifest']:
            raise ValueError('输入媒体清单与本次授权不一致，不能追加原声或其他素材')
        audio_ids = [entry['segment_id'] for entry in entries if any(m['type'] == 'reference_audio' for m in entry['media'])]
        if audio_ids != (ids if _input_policy(record)['include_source_audio'] else []):
            raise ValueError('原声参考上传范围与本次授权不一致')
    elif any(m['type'] == 'reference_audio' for entry in entries for m in entry['media']):
        raise ValueError('旧授权不含原声参考，不能追加上传；请重新准备报价并授权')


def _assert_delivery_quote(quote, project):
    from . import generation_compatibility
    frozen = quote.get('delivery_spec') or {}
    current = generation_compatibility.delivery_spec(project, quote['segment_ids'])
    if (not frozen.get('coverage_verified') or
            any(frozen.get(key) != current[key] for key in
                ('revision', 'model', 'resolution', 'ratio', 'request_ratio', 'width', 'height', 'fps'))):
        raise ValueError('本批交付规格未冻结或已变化，请重新准备报价并确认')


def _inspect_plan(project, segment_ids, purpose):
    from . import generation_compatibility
    if segment_ids == [s['id'] for s in project['segments']]:
        plan = rw.inspect_plan(project)
    else:
        plans = {sid: rw.inspect_plan(project, sid) for sid in segment_ids}
        plan = copy.deepcopy(next(iter(plans.values())))
        seen, issues = set(), []
        for child in plans.values():
            for issue in child['issues']:
                key = json.dumps(issue, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    issues.append(issue)
        plan.update(segment_plans=plans, issues=issues, ready=not any(i.get('blocking') for i in issues))
    plan.update(fingerprint=_fp(project), segment_ids=list(segment_ids), purpose=purpose,
                wan_compatibility=generation_compatibility.inspect(project, purpose, segment_ids))
    return plan


def _assert_preparation(project, segment_ids):
    if segment_ids == [s['id'] for s in project['segments']]:
        return rw._assert_preparation(project)
    preparation = project.get('preparation') or {}
    if preparation.get('fingerprint') != _fp(project):
        raise ValueError('请先按当前材料制作本次片段原片参考')
    for sid in segment_ids:
        asset = rw._segment(project, sid).get('reference') or {}
        recorded = preparation.get('references', {}).get(sid) or {}
        if (asset.get('kind') != 'source_reference' or asset.get('id') != recorded.get('id')
                or not asset.get('path') or store.digest(asset['path']) != recorded.get('sha256')):
            raise ValueError(sid + '段原片参考已变化，请重新准备')


def _prepare_selected_references(pid, project, segment_ids, jid):
    """Create only requested local clips; never alter source, masks or timing."""
    from . import replacement_media
    fingerprint = _fp(project)
    preparation = copy.deepcopy(project.get('preparation') or {})
    if preparation.get('fingerprint') != fingerprint:
        preparation = {'references': {}, 'timelines': {}}
    clips, timelines = {}, {}
    outdir = store.DATA / 'projects' / pid / 'exports'
    outdir.mkdir(parents=True, exist_ok=True)
    for sid in segment_ids:
        segment = rw._segment(project, sid)
        if _fp(store.get_project(pid)) != fingerprint:
            raise ValueError('准备期间材料已变化，请重新准备本次片段')
        store.update_job(jid, message='正在准备' + sid + '段原片原速参考')
        receipt = replacement_media.prepare_source_clip(project['source']['path'], outdir / f'{sid}_原片原速参考_{jid}.mp4', segment['start'], segment['end'])
        clips[sid] = store.add_asset(pid, receipt['path'], 'source_reference', meta=receipt.get('media', {}))
        timelines[sid] = receipt
    def commit(latest):
        for sid, asset in clips.items():
            current = rw._segment(latest, sid)
            current.update(reference=asset, reference_timeline=timelines[sid]['timeline'])
            preparation.setdefault('references', {})[sid] = {'id': asset['id'], 'sha256': asset['sha256']}
            preparation.setdefault('timelines', {})[sid] = timelines[sid]
        preparation.update(fingerprint=fingerprint, created_at=store.now(), cloud_uploaded=False, generation_submitted=False)
        latest['preparation'] = preparation
    rw._commit(pid, fingerprint, commit)


def _input_reference(project, segment, segment_ids, target):
    from . import replacement_media
    _assert_preparation(project, segment_ids)
    asset = segment['reference']
    timing = replacement_media._timing(asset['path'])
    info = timing['meta']
    actual = info.get('video_duration_seconds') or info['duration_seconds'] - info.get('video_start_time', 0)
    expected = segment['end'] - segment['start']
    if (actual > 15.00001 or abs(actual - expected) > 1 / 30 + .002
            or timing['frame_count'] != target['frame_count'] or not timing['constant_frame_rate']):
        raise ValueError(segment['id'] + '段提交文件的完整帧数或时间轴与原片选区不符，请重新准备')
    if Path(asset['path']).resolve() == Path(project['source']['path']).resolve():
        raise ValueError('当前提交引用了原片全片，请重新制作独立的分段参考')
    return {'segment_id': segment['id'], 'asset_id': asset['id'], 'name': asset['name'],
            'sha256': store.digest(asset['path']), 'duration_seconds': actual, 'expected_duration_seconds': expected,
            'source_start': segment['start'], 'source_end': segment['end'], 'fps_rational': info['fps_rational'],
            'frame_count': timing['frame_count'], 'width': info['width'], 'height': info['height'],
            'uses_full_source_file': False}


def _state_fp(project):
    assets = [project.get('source')]
    assets += [asset for role in project.get('roles', []) for asset in role.get('images', [])]
    assets += project.get('scene', {}).get('images', [])
    assets += [segment.get(kind) for segment in project.get('segments', []) for kind in ('edit_mask', 'protection_mask')]
    signature = []
    for asset in assets:
        if asset and asset.get('path'):
            info = Path(asset['path']).stat()
            signature.append((asset['path'], info.st_size, info.st_mtime_ns))
    key = (project.get('revision'), tuple(signature))
    cached = _state_cache.get(project['id'])
    if cached and cached[0] == key:
        return cached[1]
    fingerprint = _fp(project)
    _state_cache[project['id']] = (key, fingerprint)
    return fingerprint


def _config():
    from . import workflow
    return workflow.config(private=True)


def _error(error, category='local'):
    if isinstance(error, ProviderError):
        return error.public_error
    return {'category': category, 'code': 'LOCAL_VALIDATION', 'message': str(error)[:600], 'retryable': True, 'ambiguous': False}


def _sample_audition(project, candidate, provenance, *, cached=False):
    from . import generation_identity as gi
    audition = provenance.get('audition') or {}
    audio = audition.get('audio_asset') or {}
    if (audition.get('candidate_asset_id') != candidate['id']
            or audition.get('candidate_sha256') != candidate['sha256']
            or audition.get('source_sha256') != project['source']['sha256']
            or not audio.get('path')):
        return None
    try:
        if gi.file_digest(audio['path'], cached=cached) == audio.get('sha256'):
            return audition
    except (OSError, ValueError, TypeError):
        pass
    return None


def _sample_state(project, sid, run, *, cached=False):
    from . import generation_identity as gi
    segment = rw._segment(project, sid)
    candidate = segment.get('sample_candidate') or {}
    try:
        _assert_scope(run)
        identity = gi.completed_input_identity(project, run, sid, candidate, cached=cached)
    except (ValueError, OSError, KeyError, TypeError) as error:
        identity = {'input_current': False, 'reason': str(error), 'input_fingerprint': None}
    result = {**identity, 'run_id': run.get('id'), 'candidate_asset_id': candidate.get('id'),
        'candidate_sha256': candidate.get('sha256'), 'audition_current': False,
        'actual_review': 'UNREVIEWED', 'final_approved': False}
    if identity['input_current']:
        audition = _sample_audition(project, candidate, segment.get('sample_provenance') or {}, cached=cached)
        if audition:
            audio = audition['audio_asset']
            result.update(audition_current=True, audition_asset_id=audio.get('id'), audition_sha256=audio.get('sha256'))
    return result


def _sample_states(project, runs):
    result = {}
    for segment in project.get('segments', []):
        if not segment.get('sample_candidate'):
            continue
        rid = (segment.get('sample_provenance') or {}).get('run_id')
        run = next((r for r in runs if r['id'] == rid), None)
        if run:
            result[segment['id']] = _sample_state(project, segment['id'], run, cached=True)
        else:
            result[segment['id']] = {'input_current': False, 'reason': '原生成批次缺失，无法核实原始小样',
                'candidate_asset_id': segment['sample_candidate'].get('id'), 'audition_current': False, 'final_approved': False}
    return result


def _run_public(run, fingerprint, project, sample_states=None):
    result = {key: run.get(key) for key in ['id', 'fingerprint', 'purpose', 'segment_ids', 'scope_fingerprint', 'delivery_spec', 'input_policy', 'materials_fingerprint', 'status', 'created_at', 'updated_at', 'settings', 'estimate_yuan', 'reserved_yuan', 'quote_id', 'error']}
    result['stale'] = run['fingerprint'] != fingerprint
    result['segments'] = []
    result['call_evidence'] = {'succeeded': any(s.get('status') == 'COMPLETED' and s.get('task_id') and s.get('asset') for s in run['segments']),
        'model': run.get('settings', {}).get('model'), 'run_id': run['id'], 'scope': 'THIS_RUN_ONLY', 'key_verified_for_other_calls': False}
    try:
        result['call_evidence']['region_label'] = inspect_configuration({'base_url': run.get('base_url')})['region_label']
    except (ValueError, ProviderError):
        result['call_evidence']['region_label'] = None
    for segment in run['segments']:
        item = {key: segment.get(key) for key in ['segment_id', 'name', 'status', 'task_id', 'error', 'error_details', 'asset', 'attached', 'usage', 'generation_post_attempted', 'return_inspection']}
        item['input_media_types'] = [m['type'] for m in segment.get('media', [])]
        item['purpose'] = run.get('purpose', 'strict_final')
        item['source_audio_planned'] = 'reference_audio' in item['input_media_types']
        item['source_audio_provided'] = (bool(segment.get('task_id')) if item['source_audio_planned'] else False)
        if item['source_audio_planned'] and segment.get('generation_post_attempted') and not segment.get('task_id'):
            item['source_audio_provided'] = None
        item['cloud_completed'] = segment.get('status') in {'SUCCEEDED', 'DOWNLOADING', 'DOWNLOAD_FAILED', 'COMPLETED'}
        item['raw_received'] = bool(segment.get('asset'))
        current = next((entry for entry in project['segments'] if entry['id'] == segment['segment_id']), {})
        sample = run.get('purpose') == 'candidate_sample'
        local = (sample_states or {}).get(segment['segment_id']) or {}
        if sample and local.get('run_id') == run['id'] and local.get('candidate_asset_id') == (item.get('asset') or {}).get('id'):
            item['sample_state'] = local
        if sample:
            item['attached'] = item.get('sample_state', {}).get('input_current') is True
        else:
            item['attached'] = (bool(item['attached']) and not result['stale'] and not current.get('candidate_stale')
                                and (current.get('generated') or {}).get('id') == (item.get('asset') or {}).get('id')
                                and (current.get('generation_provenance') or {}).get('run_id') == run['id'])
        result['segments'].append(item)
    result['local_current'] = any(s.get('sample_state', {}).get('input_current') for s in result['segments'])
    return result


def state(pid):
    from . import generation_compatibility
    project = store.get_project(pid)
    compatibility = generation_compatibility.inspect(project)
    sample_compatibility = generation_compatibility.inspect(project, 'candidate_sample')
    try:
        configuration = inspect_configuration(_config())
    except ProviderError as error:
        configuration = {'ready': False, 'authentication_verified': False, 'issues': [error.public_error]}
    prep = _read(_root(pid) / 'preparation.json', {'status': 'IDLE'})
    runs = _runs(pid)
    try:
        fingerprint = _state_fp(project)
    except Exception:
        fingerprint = None
    if (fingerprint and prep.get('status') in {'IDLE', 'NEEDS_ATTENTION'}
            and replacement.edit_mask_optional(project)):
        cached_plan = (prep.get('plan') or project.get('replacement_plan')
                       or (project.get('preparation') or {}).get('plan') or {})
        if any(issue.get('code') == 'MASK_REQUIRED' for issue in cached_plan.get('issues', [])):
            # Recheck saved drafts that still carry the old missing-mask gate.
            prep = {'status': 'IDLE', 'plan': rw.inspect_plan(project), 'fingerprint': fingerprint}
    confirmation = (replacement.material_confirmation_state(project, fingerprint) if fingerprint is not None else
                    {'current': False, 'fingerprint': None, 'confirmed_at': None, 'source': None, 'legacy': False})
    result_states = rw.result_states(project)
    if prep.get('status') == 'IDLE' and not runs:
        return {'preparation': prep, 'quote': None, 'runs': [], 'current_run': None,
                'project_result_count': 0, 'material_confirmation': confirmation, 'compatibility': compatibility,
                'result_states': result_states, 'sample_states': _sample_states(project, runs),
                'sample_compatibility': sample_compatibility, 'configuration': configuration}
    quote = prep.get('quote')
    stale = bool(prep.get('fingerprint')) and prep['fingerprint'] != fingerprint
    if quote and not quote.get('confirmed_run'):
        try:
            _assert_delivery_quote(quote, project)
            if (project.get('preparation') or {}).get('fingerprint') != fingerprint:
                stale = True
        except ValueError:
            stale = True
    public_prep = {key: prep.get(key) for key in ['status', 'stage', 'message', 'error', 'plan', 'package_asset', 'fingerprint', 'job_id', 'purpose', 'segment_ids', 'input_policy']}
    public_prep['stale'] = stale
    if stale and prep.get('status') not in {'PREPARING', 'FAILED'}:
        public_prep.update(status='IDLE', message='材料已修改，请按当前版本重新准备。', package_asset=None, plan=None)
    public_quote = None
    if quote and not stale and quote.get('expires_at', 0) > time.time():
        public_quote = {key: value for key, value in quote.items() if key not in {'snapshot', 'media', 'base_url'}}
        public_quote['submission_ready'] = configuration['ready'] and quote.get('base_url') == configuration.get('base_url')
        try:
            _assert_current_audio_contract(quote, cached=True)
            _assert_input_files(quote)
            public_quote['input_materials_current'] = True
        except (OSError, ValueError):
            public_quote.update(input_materials_current=False, submission_ready=False)
    sample_states = _sample_states(project, runs)
    public_runs = [_run_public(run, fingerprint, project, sample_states) for run in runs]
    current = next((run for run in public_runs if not run['stale']), None) or next((run for run in public_runs if run.get('local_current')), None)
    count = sum(1 for segment in project['segments'] if segment.get('generated') and not segment.get('candidate_stale') and (segment.get('generation_provenance') or {}).get('fingerprint') == fingerprint and (segment.get('generation_provenance') or {}).get('asset_id') == segment['generated']['id'])
    return {'preparation': public_prep, 'quote': public_quote, 'runs': public_runs, 'current_run': current,
            'project_result_count': count, 'material_confirmation': confirmation, 'compatibility': compatibility,
            'result_states': result_states, 'sample_states': sample_states, 'sample_compatibility': sample_compatibility, 'configuration': configuration}


def _confirm_materials(project, source):
    """Call only after validating this operation's current material snapshot."""
    if not replacement.material_confirmation_state(project)['current']:
        project['material_confirmation'] = {'confirmed': True, 'fingerprint': _fp(project),
                                            'confirmed_at': store.now(), 'source': source}


def _export_message(project, plan):
    if not plan['ready']:
        return '生成包已导出；材料仍有待处理项，尚未完成当前材料确认。'
    if not replacement.material_confirmation_state(project)['current']:
        return '生成包已导出；核对材料后，可随同一导出操作补齐材料确认。'
    return '生成包已导出，当前材料确认已保留；平台结果导入后仍须逐段验收。'


def _set_preparation(pid, **changes):
    with _lock(pid):
        path = _root(pid) / 'preparation.json'
        record = _read(path, {})
        record.update(changes, updated_at=time.time())
        _private_json(path, record)


def request_prepare(pid, body):
    project = store.get_project(pid)
    if project.get('workflow') != 'replacement':
        raise ValueError('此生成流程仅用于人物与场景替换项目')
    if 'expected_revision' in body and body['expected_revision'] != project['revision']:
        raise store.RevisionConflict(project['revision'])
    mode = body.get('mode', 'api')
    if mode not in {'api', 'export'}:
        raise ValueError('请选择 API 生成或平台生成包')
    purpose = body.get('purpose', 'strict_final')
    include_audio = body.get('include_source_audio', False)
    if not isinstance(include_audio, bool) or mode == 'export' and include_audio:
        raise ValueError('请明确选择是否向模型上传原声参考；当前平台导出不包含此选项')
    policy = {'include_source_audio': include_audio}
    if include_audio:
        from .generation_audio import REFERENCE_METHOD
        policy['source_audio_method'] = REFERENCE_METHOD
    if purpose not in {'strict_final', 'candidate_sample'} or mode == 'export' and purpose != 'strict_final':
        raise ValueError('请选择有效的生成用途；候选小样须使用独立 API 准备路径')
    try:
        local_adaptation_rate = Fraction(str(project.get('source_meta', {}).get('fps_rational', '0'))) == Fraction(24000, 1001)
    except (ValueError, ZeroDivisionError):
        local_adaptation_rate = False
    if mode == 'export' and WanClient(_config()).configured and not local_adaptation_rate:
        raise ValueError('当前已配置 API Key，请使用系统内生成流程；不会自动切换到平台导出')
    fingerprint = _fp(project)
    retry_of = body.get('retry_of')
    segment_ids = body.get('segment_ids')
    if segment_ids is not None and (not isinstance(segment_ids, list) or not all(isinstance(sid, str) for sid in segment_ids) or len(segment_ids) != len(set(segment_ids))):
        raise ValueError('生成片段必须是没有重复项的片段列表')
    if retry_of:
        prior = _read(_run_path(pid, retry_of))
        if prior['fingerprint'] != fingerprint:
            raise ValueError('材料版本已变化，请按当前版本重新开始生成，不沿旧批次重试')
        if prior.get('purpose', 'strict_final') != purpose:
            raise ValueError('重试用途必须与原批次一致，请重新核对本次范围')
        if _input_policy(prior) != policy:
            raise ValueError('旧授权不能追加原声参考，请作为新材料重新准备报价和授权')
        allowed = {item['segment_id'] for item in prior['segments'] if item['status'] in RETRYABLE}
        if not segment_ids or not set(segment_ids).issubset(allowed):
            raise ValueError('仅可明确重试已失败或尚未提交的片段；状态未知时先核查原任务')
    elif segment_ids is None:
        segment_ids = [segment['id'] for segment in project['segments']]
    if not segment_ids:
        raise ValueError('请先保存至少一个片段')
    if not set(segment_ids).issubset({segment['id'] for segment in project['segments']}):
        raise ValueError('生成片段已经移除，请按当前项目重新准备')
    if mode == 'export' and segment_ids != [s['id'] for s in project['segments']]:
        raise ValueError('当前平台包只支持全部片段；选段试做请使用候选小样 API 路径')
    with _lock(pid):
        latest = store.get_project(pid)
        if _fp(latest) != fingerprint:
            raise ValueError('材料已修改，请按当前版本重新准备；未记录材料确认')
        project = latest
        path = _root(pid) / 'preparation.json'
        prior = _read(path, {})
        if prior.get('status') == 'PREPARING':
            return state(pid)
        if (mode == 'api' and purpose == 'strict_final' and segment_ids == [s['id'] for s in project['segments']]
                and body.get('material_confirmed') is True and
                all(project.get('prompts', {}).get(segment['id'], '').strip() for segment in project['segments'])):
            local_plan = _inspect_plan(project, segment_ids, purpose)
            if local_plan['ready']:
                _confirm_materials(project, 'api')
                store.put_project(project)
        package = prior.get('package_asset') or {}
        if (mode == 'export' and prior.get('status') == 'EXPORTED' and prior.get('mode') == mode and
                prior.get('fingerprint') == fingerprint and prior.get('segment_ids') == segment_ids and package.get('path')):
            try:
                rw._assert_preparation(project)
                package_valid = Path(package['path']).is_file() and store.digest(package['path']) == package.get('sha256')
            except (ValueError, OSError, KeyError):
                package_valid = False
            if package_valid:
                plan = rw.inspect_plan(project)
                if body.get('material_confirmed') is True and plan['ready']:
                    _confirm_materials(project, 'export')
                    store.put_project(project)
                prior.update(plan=plan, message=_export_message(project, plan), updated_at=time.time())
                _private_json(path, prior)
                return state(pid)
        if mode == 'api':
            for run in _runs(pid):
                if run['fingerprint'] == fingerprint and _input_policy(run) == policy and any(item['segment_id'] in segment_ids and item['status'] == 'COMPLETED' for item in run['segments']):
                    return state(pid)
        quote = prior.get('quote') or {}
        if (prior.get('fingerprint') == fingerprint and prior.get('mode') == mode and
                prior.get('purpose', 'strict_final') == purpose and _input_policy(prior) == policy and prior.get('segment_ids') == segment_ids
                and quote.get('base_url') == inspect_configuration(_config())['base_url']
                and quote.get('expires_at', 0) > time.time() and not quote.get('confirmed_run')):
            try:
                _assert_delivery_quote(quote, project)
                _assert_preparation(project, segment_ids)
                _assert_input_files(quote)
                _assert_current_audio_contract(quote)
            except (ValueError, OSError, KeyError):
                # Returning to an earlier material fingerprint does not restore
                # its invalidated/missing local references. Rebuild before quote.
                pass
            else:
                return state(pid)
        if mode == 'api' and not retry_of:
            existing = next((run for run in _runs(pid) if run['fingerprint'] == fingerprint and _input_policy(run) == policy and
                             any(item['segment_id'] in segment_ids for item in run['segments'])), None)
            if existing and all(item['status'] == 'COMPLETED' for item in existing['segments']):
                return state(pid)
            if existing and any(item['status'] in LIVE | AMBIGUOUS | {'DOWNLOAD_FAILED'} for item in existing['segments']):
                return state(pid)
            if existing:
                raise ValueError('已有同版本生成记录，请从失败片段的重试入口继续，避免重复生成成功片段')
        record = {'status': 'PREPARING', 'stage': 'CHECKING', 'mode': mode, 'fingerprint': fingerprint,
                  'segment_ids': segment_ids, 'purpose': purpose, 'input_policy': policy, 'retry_of': retry_of, 'message': '正在核对已保存材料', 'created_at': time.time()}
        _private_json(path, record)
        job = store.start_job(pid, 'prepare_generation', lambda jid: prepare(pid, mode, segment_ids, jid,
                              material_confirmed=body.get('material_confirmed') is True, requested_fingerprint=fingerprint, purpose=purpose, include_source_audio=include_audio))
        record['job_id'] = job['job_id']
        _private_json(path, record)
    return state(pid)


def prepare(pid, mode, segment_ids, jid, material_confirmed=False, requested_fingerprint=None, purpose='strict_final', include_source_audio=False):
    """Local-only pipeline, including the no-Key platform fallback."""
    try:
        project = store.get_project(pid)
        initial_fp = _fp(project)
        if requested_fingerprint is not None and initial_fp != requested_fingerprint:
            raise ValueError('开始准备前材料已修改，请按当前版本重新操作；未记录材料确认')
        if not project.get('source') or not project.get('segments'):
            raise ValueError('请先导入原片并保存分段')
        missing = [segment for segment in project['segments'] if segment['id'] in segment_ids and not project.get('prompts', {}).get(segment['id'], '').strip()]
        if missing:
            _set_preparation(pid, stage='TEMPLATES', message='仅为缺失的提示词准备模板，保留已有手工内容')
            with store.LOCK:
                latest = store.get_project(pid)
                if _fp(latest) != initial_fp:
                    raise ValueError('准备期间材料已修改，请重新开始准备')
                prompts = dict(latest.get('prompts', {}))
                for segment in missing:
                    prompts[segment['id']] = replacement.generate_prompt(latest, segment)
                store.update_project(pid, {'expected_revision': latest['revision'], 'prompts': prompts})
        project = store.get_project(pid)
        target_fp = _fp(project)
        plan = _inspect_plan(project, segment_ids, purpose)
        full_material_scope = purpose == 'strict_final' and segment_ids == [s['id'] for s in project['segments']]
        if full_material_scope:
            rw._commit(pid, target_fp, lambda latest: latest.update(replacement_plan=plan))
        _set_preparation(pid, fingerprint=target_fp, plan=plan)
        if mode == 'api' and full_material_scope and material_confirmed and plan['ready']:
            rw._commit(pid, target_fp, lambda latest: _confirm_materials(latest, 'api'))
        if mode == 'api' and (not plan['ready'] or not plan['wan_compatibility']['ready']):
            _set_preparation(pid, status='NEEDS_ATTENTION', stage='CHECKING', message='请处理生成前待办，修正后继续。')
            return {'plan': plan, 'message': '准备结束，材料或 API 兼容性有待处理项；未上传或提交'}
        try:
            _assert_preparation(project, segment_ids)
            _set_preparation(pid, stage='PREPARING_FILES', message='复用当前有效的分段文件')
        except (ValueError, OSError, KeyError):
            _set_preparation(pid, stage='PREPARING_FILES', message='正在制作本次选定的原片分段文件')
            if segment_ids == [s['id'] for s in project['segments']]:
                rw.action(pid, {'action': 'prepare_replacement'}, jid)
            else:
                _prepare_selected_references(pid, project, segment_ids, jid)
        project = store.get_project(pid)
        if _fp(project) != target_fp:
            raise ValueError('准备期间材料已修改，已有本地文件保留，请重新准备')
        plan = _inspect_plan(project, segment_ids, purpose)
        if full_material_scope:
            rw._commit(pid, target_fp, lambda latest: latest.update(replacement_plan=plan))
        _set_preparation(pid, plan=plan)
        if mode == 'export':
            _set_preparation(pid, stage='PACKAGING', message='正在导出平台生成包，不上传或调用生成 API')
            result = rw.export_package(pid, jid)
            if material_confirmed and plan['ready']:
                def acknowledge(latest):
                    rw._assert_preparation(latest)
                    _confirm_materials(latest, 'export')
                rw._commit(pid, target_fp, acknowledge)
            project = store.get_project(pid)
            _set_preparation(pid, status='EXPORTED', stage='COMPLETE', package_asset=result['asset'], message=_export_message(project, plan))
            return result
        if not plan['ready'] or not plan['wan_compatibility']['ready']:
            _set_preparation(pid, status='NEEDS_ATTENTION', stage='CHECKING', message='分段制作后仍有材料待办，请处理后继续。')
            return {'plan': plan, 'message': '材料仍有待办，未上传或提交'}
        _set_preparation(pid, stage='ESTIMATING', message='正在核对实际任务与本次费用，不访问生成接口')
        from . import generation_compatibility
        delivery_spec = generation_compatibility.delivery_spec(
            project, segment_ids, verified=True, exact_grid=purpose == 'strict_final')
        client = WanClient({**_config(), 'model': project['settings']['model']})
        reports, items, audio_contracts = [], [], {}
        for target in delivery_spec['segments']:
            sid = target['segment_id']
            segment = rw._segment(project, sid)
            reference = _input_reference(project, segment, segment_ids, target)
            media = [{'type': 'reference_video', 'path': segment['reference']['path']}] + [
                {'type': 'reference_image', 'path': asset['path']} for asset in replacement.plan(project, sid)['references']]
            if include_source_audio:
                audio_contracts[sid] = _source_audio_reference(pid, project, segment)
                media.append({'type': 'reference_audio', 'path': audio_contracts[sid]['path']})
            prompt = project['prompts'][sid]
            if include_source_audio:
                contract = audio_contracts[sid]
                from . import generation_audio
                note = generation_audio.reference_prompt_note(contract)
                audio_contracts[sid]['submission_prompt_note'] = note
                prompt += '\n\n' + note
            parameters = {key: value for key, value in project['settings'].items() if key != 'model'}
            report = client.preflight(prompt, media, parameters)
            generation_compatibility.assert_preflight(project, segment, report, purpose, segment_ids)
            reports.append({'segment_id': sid, **report, 'input_reference': reference})
            items.append({'segment_id': sid, 'name': segment.get('name', sid), 'prompt': prompt, 'media': report['media'], 'parameters': report['parameters'], 'reference': reference})
        if _fp(store.get_project(pid)) != target_fp:
            raise ValueError('估算期间材料已修改，请重新准备与确认费用')
        input_seconds = sum(report['input_reference']['duration_seconds'] for report in reports)
        expected = sum((report['input_reference']['duration_seconds'] * 2 * report['estimate']['unit_yuan_per_second']) for report in reports)
        reserved = sum(report['estimate']['reserved_yuan'] for report in reports)
        quote = {'id': uuid.uuid4().hex, 'fingerprint': target_fp, 'mode': mode, 'purpose': purpose, 'segment_ids': segment_ids,
                 'count': len(segment_ids), 'input_seconds': input_seconds, 'expected_output_seconds': input_seconds,
                 'settings': copy.deepcopy(project['settings']), 'estimate_yuan': round(expected, 4), 'reserved_yuan': round(reserved, 4),
                 'pricing': [report['estimate'] for report in reports], 'reports': reports, 'media': items,
                 'base_url': client.base, 'created_at': time.time(), 'expires_at': time.time() + QUOTE_SECONDS,
                 'snapshot': project, 'delivery_spec': delivery_spec,
                 'duration_policy': '编辑预计与原片同长；智能时长不保证逐帧一致，按最大允许输出预留上限，结果仍需严格复核。'}
        quote['configuration'] = client.configuration_status()
        quote['input_policy'] = {'include_source_audio': include_source_audio}
        if include_source_audio:
            from .generation_audio import REFERENCE_METHOD
            quote['input_policy']['source_audio_method'] = REFERENCE_METHOD
        quote['input_contract'] = {'version': 1, 'source_audio': audio_contracts,
            'output_audio': False, 'note': 'audio=false仅关闭模型输出声音；reference_audio是独立上传材料，不保证口型自动通过。'}
        quote['material_manifest'] = _material_manifest(items)
        quote['materials_fingerprint'] = hashlib.sha256(json.dumps({'fingerprint': target_fp, 'policy': quote['input_policy'],
            'contract': quote['input_contract'], 'manifest': quote['material_manifest']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        quote['scope_fingerprint'] = _scope_fingerprint(quote)
        # This asynchronous free preparation must finish the complete live
        # check before READY is visible, so its first public read is cheap.
        _public_audio_validation(quote, force=True)
        _set_preparation(pid, status='READY', stage='AWAITING_CONFIRMATION', quote=quote, message='准备完成。请集中核对材料、设置和费用；确认前不会上传或付费生成。')
        return {'message': '生成准备完成，等待逐项核对及本次费用授权', 'quote_id': quote['id']}
    except Exception as error:
        _set_preparation(pid, status='FAILED', error=_error(error), message=str(error)[:500])
        raise


def _mutate_run(pid, run_id, fn):
    with _lock(pid):
        path = _run_path(pid, run_id)
        run = _read(path)
        if not run:
            raise ValueError('生成批次不存在')
        fn(run)
        run['updated_at'] = time.time()
        _private_json(path, run)
        return run


def _segment_update(pid, run_id, sid, **changes):
    return _mutate_run(pid, run_id, lambda run: next(item for item in run['segments'] if item['segment_id'] == sid).update(changes))


def confirm(pid, body):
    if body.get('confirm_upload') is not True or body.get('confirm_generation') is not True:
        raise ValueError('请明确确认本次素材上传和付费生成')
    with _lock(pid):
        prep_path = _root(pid) / 'preparation.json'
        prep = _read(prep_path, {})
        quote = prep.get('quote') or {}
        if not quote or quote['id'] != body.get('quote_id'):
            raise ValueError('本次任务或费用信息已变化，请重新准备并确认')
        _assert_scope(quote)
        if body.get('include_source_audio', False) != _input_policy(quote)['include_source_audio']:
            raise ValueError('原声参考选择与报价不一致；旧授权不能追加音频上传')
        if quote.get('delivery_spec') or quote['purpose'] == 'candidate_sample' or len(quote['segment_ids']) != len(quote['snapshot']['segments']):
            if (body.get('purpose') != quote['purpose'] or body.get('segment_ids') != quote['segment_ids']
                    or body.get('scope_fingerprint') != quote['scope_fingerprint']):
                raise ValueError('请明确确认本次用途和选定片段；A 段授权不能扩为全部片段')
        if quote.get('confirmed_run'):
            return state(pid)
        _assert_current_audio_contract(quote)
        _assert_input_files(quote)
        project = store.get_project(pid)
        if quote['fingerprint'] != _fp(project) or quote['expires_at'] < time.time():
            raise ValueError('材料或费用确认已过期，请按当前版本重新准备')
        _assert_delivery_quote(quote, project)
        if (_input_policy(quote)['include_source_audio'] or not replacement.material_confirmation_state(project)['current']) and body.get('material_confirmed') is not True:
            raise ValueError('请确认当前人物对应、替换范围与参考素材；材料确认不代表候选结果验收')
        configured = _config()
        client = WanClient({**configured, 'model': project['settings']['model']})
        if not client.configured:
            raise ValueError('尚未配置 API Key；未上传或提交')
        client.assert_configuration()
        if client.base != quote['base_url']:
            raise ValueError('API 地域或地址已变化，请重新准备与确认费用')
        plan = _inspect_plan(project, quote['segment_ids'], quote['purpose'])
        if not plan['ready'] or not plan['wan_compatibility']['ready']:
            raise ValueError('生成前材料或 API 兼容性仍有待办，请修正后继续')
        _assert_preparation(project, quote['segment_ids'])
        from . import generation_compatibility
        fresh_reports = []
        for item in quote['media']:
            report = client.preflight(item['prompt'], item['media'], item['parameters'])
            generation_compatibility.assert_preflight(project, rw._segment(project, item['segment_id']), report, quote['purpose'], quote['segment_ids'])
            fresh_reports.append(report)
        fresh_reservation = sum(report['estimate']['reserved_yuan'] for report in fresh_reports)
        if abs(fresh_reservation - quote['reserved_yuan']) > .0001:
            raise ValueError('本次费用预留已变化，请重新准备与确认；未记录材料确认或提交生成')
        budget = body.get('approved_budget_yuan')
        if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not math.isfinite(budget) or budget < quote['reserved_yuan'] or budget > quote['reserved_yuan'] + .0001:
            raise ValueError('请确认页面所示本次预留上限；不会自动提高授权金额')
        for prior in _runs(pid):
            if prior['fingerprint'] == quote['fingerprint'] and _input_policy(prior) == _input_policy(quote) and any(item['segment_id'] in quote['segment_ids'] and item['status'] == 'COMPLETED' for item in prior['segments']):
                raise ValueError('本次片段已有同版本生成结果，不会再次提交收费')
            if any(item['status'] in LIVE | AMBIGUOUS | {'DOWNLOAD_FAILED'} for item in prior['segments']):
                raise ValueError('已有未完成或状态未知的生成任务，请先核查原批次，避免重复收费')
        run_id = uuid.uuid4().hex
        directory = _run_path(pid, run_id).parent
        inputs = directory / 'inputs'
        inputs.mkdir(parents=True)
        os.chmod(directory, 0o700)
        os.chmod(inputs, 0o700)
        segments = []
        copied = {}
        for item in quote['media']:
            media = []
            for source in item['media']:
                source_path = Path(source['path'])
                digest = store.digest(source_path)
                if digest != source.get('sha256'):
                    raise ValueError('素材与刚才确认的预检文件不一致，请重新准备')
                if digest not in copied:
                    dest = inputs / (digest + source_path.suffix.lower())
                    shutil.copy2(source_path, dest)
                    os.chmod(dest, 0o600)
                    if store.digest(dest) != digest:
                        raise ValueError('素材复制期间发生变化，请重新准备')
                    copied[digest] = str(dest)
                media.append({**source, 'path': copied[digest]})
            segments.append({**copy.deepcopy(item), 'media': media, 'status': 'QUEUED', 'request_key': run_id + ':' + item['segment_id'], 'attached': False, 'asset': None})
        if _fp(store.get_project(pid)) != quote['fingerprint']:
            raise ValueError('确认期间材料发生变化，请重新准备；未上传或提交')
        # One material acknowledgement is distinct from this batch's paid authority
        # and from the candidate's six actual acceptance checks in step 5.
        if quote['purpose'] == 'strict_final' and quote['segment_ids'] == [s['id'] for s in project['segments']]:
            _confirm_materials(project, 'api')
            store.put_project(project)
        run = {'id': run_id, 'project_id': pid, 'quote_id': quote['id'], 'fingerprint': quote['fingerprint'], 'status': 'RUNNING',
               'created_at': time.time(), 'updated_at': time.time(), 'base_url': quote['base_url'], 'settings': quote['settings'],
               'estimate_yuan': quote['estimate_yuan'], 'reserved_yuan': quote['reserved_yuan'], 'segments': segments,
               'purpose': quote['purpose'], 'segment_ids': quote['segment_ids'], 'scope_fingerprint': quote['scope_fingerprint'], 'pricing': quote['pricing'],
               **{key: copy.deepcopy(quote[key]) for key in ('delivery_spec', 'input_policy', 'input_contract', 'material_manifest', 'materials_fingerprint') if key in quote},
               'authorization': {'confirmed_upload': True, 'confirmed_generation': True, 'budget_yuan': budget,
                                 'purpose': quote['purpose'], 'segment_ids': quote['segment_ids'], 'scope_fingerprint': quote['scope_fingerprint'],
                                 'input_policy': _input_policy(quote), 'materials_fingerprint': quote.get('materials_fingerprint'),
                                 'material_fingerprint': quote['fingerprint'], 'material_confirmation': replacement.material_confirmation_state(project), 'confirmed_at': store.now()},
               'snapshot': quote['snapshot']}
        _private_json(directory / 'run.json', run)
        quote['confirmed_run'] = run_id
        prep.update(quote=quote, status='READY')
        _private_json(prep_path, prep)
    start_worker(pid, run_id, allow_submit=True)
    return state(pid)


def _client(pid, run):
    endpoint = inspect_configuration({'base_url': run['base_url']})
    return WanClient({**_config(), 'model': run['settings']['model'], 'base_url': run['base_url'],
                      'workspace_id': endpoint['workspace_id'], 'region': endpoint['region']}, _run_path(pid, run['id']).parent / 'provider')


def _publish(pid, run, segment, download):
    path = Path(download['path'])
    if store.digest(path) != download['sha256']:
        raise ValueError('下载结果文件已变化，未关联到片段')
    from . import candidate_adaptation, generation_compatibility, media, replacement_media
    snapshot = run['snapshot']
    baseline_segment = rw._segment(snapshot, segment['segment_id'])
    sample = run.get('purpose') == 'candidate_sample'
    frozen = run.get('delivery_spec') or {}
    target = None
    if sample:
        inspection = candidate_adaptation.inspect_candidate_return(snapshot['source']['path'], path,
            baseline_segment['start'], baseline_segment['end'], resolution=run['settings'].get('resolution', '480P'))
    else:
        output_size = (frozen['width'], frozen['height']) if frozen.get('width') else generation_compatibility.delivery_dimensions(snapshot)
        if frozen:
            target = next(row for row in frozen['segments'] if row['segment_id'] == segment['segment_id'])
            target = {**target, **{key: frozen[key] for key in ('model', 'resolution', 'ratio', 'width', 'height', 'fps')}}
        inspection = {'status': 'RAW_CANDIDATE_RECEIVED', 'target': target, 'actual': download['media'],
                      'raw_strict_compatible': False, 'actual_review': 'UNREVIEWED', 'usage_used_for_validation': False}
        try:
            report = replacement_media.validate_candidate(snapshot['source']['path'], path,
                baseline_segment['start'], baseline_segment['end'], output_size=output_size, verify_raw_pts=True)
            inspection.update(report, actual=report['candidate'], raw_strict_compatible=report['ready'])
        except media.MediaError as error:
            inspection['issues'] = [{'code': 'RAW_MEDIA_UNVERIFIED', 'message': str(error), 'blocking': True}]
    if store.digest(path) != download['sha256']:
        raise ValueError('核验期间原始回片文件已变化，未关联到片段')
    _segment_update(pid, run['id'], segment['segment_id'], return_inspection=inspection)
    with store.LOCK:
        project = store.get_project(pid)
        # Reuse a prior registered download after a worker/process interruption.
        asset = next((item for item in project.get('assets', []) if item.get('path') == str(path) and item.get('sha256') == download['sha256']), None)
        if not asset:
            asset = store.add_asset(pid, path, 'generated', meta=download['media'])
        project = store.get_project(pid)
        current = next((item for item in project['segments'] if item['id'] == segment['segment_id']), None)
        baseline = next((item for item in run['snapshot']['segments'] if item['id'] == segment['segment_id']), {})
        candidate_key = 'sample_candidate' if sample else 'generated'
        expected_result = (baseline.get(candidate_key) or {}).get('id')
        current_result = (current.get(candidate_key) or {}).get('id') if current else None
        candidate_unchanged = current_result in {expected_result, asset['id']}
        delivery_current = not frozen or frozen['revision'] == project.get('delivery_revision', 0)
        attached = current is not None and delivery_current and _fp(project) == run['fingerprint'] and candidate_unchanged
        if attached and sample:
            same_asset = (current.get('sample_candidate') or {}).get('id') == asset['id']
            prior = current.get('sample_provenance') or {} if same_asset else {}
            current.update(sample_candidate=asset,
                           sample_provenance={**prior, 'run_id': run['id'], 'task_id': segment.get('task_id'),
                               'fingerprint': run['fingerprint'], 'scope_fingerprint': run['scope_fingerprint'],
                               'purpose': 'candidate_sample', 'segment_ids': run['segment_ids'], 'asset_id': asset['id'],
                               'candidate_resolution': run['settings'].get('resolution', '480P'),
                               'actual_media': inspection['actual'], 'return_inspection': inspection,
                               'source_audio_provided': any(m['type'] == 'reference_audio' for m in segment['media']),
                               'strict_final_approved': False})
            store.put_project(project)
        elif attached:
            if current.get('candidate_adaptation'):
                project.update(final=None, final_master=None, assembly_report=None)
            current.pop('candidate_adaptation', None)
            provenance = {'run_id': run['id'], 'task_id': segment.get('task_id'), 'fingerprint': run['fingerprint'],
                          'asset_id': asset['id'], 'submitted_at': run['created_at']}
            if target:
                provenance['delivery_target'] = target
            current.update(generated=asset, candidate_stale=False, candidate_validation=None, candidate_review=None,
                           composited=None, composite=None, composite_preview=None, composite_report=None,
                           generation_provenance=provenance)
            store.put_project(project)
    return asset, attached


def _aggregate(run):
    statuses = [item['status'] for item in run['segments']]
    if any(status in LIVE for status in statuses):
        run['status'] = 'RUNNING'
    elif all(status == 'COMPLETED' for status in statuses):
        run['status'] = 'COMPLETED'
    elif any(status in {'UNKNOWN', 'DOWNLOAD_FAILED'} for status in statuses):
        run['status'] = 'ATTENTION'
    elif any(status == 'COMPLETED' for status in statuses):
        run['status'] = 'PARTIAL'
    else:
        run['status'] = 'FAILED'


def _receipt(client, request_key):
    try:
        return client.get_request(request_key)
    except ProviderError as error:
        if error.code == 'REQUEST_NOT_FOUND':
            return None
        raise


def drive_once(pid, run_id, allow_submit=False):
    run = _read(_run_path(pid, run_id))
    client = _client(pid, run)
    needs_manual = False
    stop_submit = False
    for original in run['segments']:
        sid = original['segment_id']
        segment = next(item for item in _read(_run_path(pid, run_id))['segments'] if item['segment_id'] == sid)
        if segment['status'] in {'COMPLETED', 'NOT_SUBMITTED', 'FAILED', 'REJECTED', 'UPLOAD_FAILED', 'CANCELED'}:
            continue
        try:
            receipt = _receipt(client, segment['request_key'])
            if segment['status'] == 'QUEUED' and not receipt:
                if not allow_submit or stop_submit:
                    _segment_update(pid, run_id, sid, status='NOT_SUBMITTED', error='本段未提交生成。请从失败片段入口重新准备并确认。')
                    continue
                def ensure_snapshot():
                    _assert_scope(run)
                    _assert_current_audio_contract(run)
                    authorization = run.get('authorization') or {}
                    if any(authorization.get(key) != run.get(key) for key in ('purpose', 'segment_ids', 'scope_fingerprint')):
                        raise ProviderError('本次执行范围与付费授权不一致，未提交', category='snapshot')
                    if (authorization.get('confirmed_upload') is not True or authorization.get('confirmed_generation') is not True
                            or authorization.get('budget_yuan') != run['reserved_yuan']
                            or authorization.get('material_fingerprint') != run['fingerprint']):
                        raise ProviderError('本次材料或预算与付费授权不一致，未提交', category='snapshot')
                    if ('input_policy' in run and authorization.get('input_policy') != run['input_policy']
                            or authorization.get('materials_fingerprint') != run.get('materials_fingerprint')):
                        raise ProviderError('原声参考或输入材料与授权不一致，未上传或提交', category='snapshot')
                    if inspect_configuration(_config())['base_url'] != run['base_url']:
                        raise ProviderError('生成地址已变化，请重新报价与授权；本段未提交', category='snapshot')
                    if any(store.digest(media['path']) != media.get('sha256') for media in segment['media']):
                        raise ProviderError('已确认素材快照发生变化，本段未提交', category='snapshot')
                    current = store.get_project(pid)
                    if _fp(current) != run['fingerprint']:
                        raise ProviderError('项目已修改，本段没有提交；旧任务结果将保留来源。', category='snapshot')
                    frozen = run.get('delivery_spec')
                    if frozen and frozen['revision'] != current.get('delivery_revision', 0):
                        raise ProviderError('本批交付档位已改动，请重新报价与授权；本段未提交', category='snapshot')
                    from . import generation_compatibility
                    report = client.preflight(segment['prompt'], segment['media'], segment['parameters'])
                    generation_compatibility.assert_preflight(run['snapshot'], rw._segment(run['snapshot'], sid), report, run['purpose'], run['segment_ids'])
                ensure_snapshot()
                receipt = client.submit(segment['prompt'], segment['media'], segment['parameters'], approved_budget_yuan=run['reserved_yuan'], request_key=segment['request_key'],
                                        progress_callback=lambda stage, sid=sid: _segment_update(pid, run_id, sid, status=stage), before_post=ensure_snapshot)
            if not receipt:
                _segment_update(pid, run_id, sid, status='UNKNOWN', error='本地请求记录缺失，禁止自动重新提交。')
                needs_manual = True
                continue
            status = receipt.get('status', 'UNKNOWN')
            updates = {key: receipt[key] for key in ['task_id', 'error', 'error_details', 'generation_post_attempted', 'usage'] if key in receipt}
            _segment_update(pid, run_id, sid, status=status, **updates)
            if status == 'UPLOADING':
                # An interrupted upload never reached the generation POST.
                _segment_update(pid, run_id, sid, status='UPLOAD_FAILED', error='上传中断，生成尚未提交；可修复后明确重试。')
                continue
            if status == 'SUBMITTING' and not receipt.get('task_id'):
                _segment_update(pid, run_id, sid, status='UNKNOWN', error='提交响应中断，是否收费尚不能确认。先核查原任务，禁止自动重提。')
                needs_manual = True
                continue
            if status in {'REJECTED', 'UPLOAD_FAILED'}:
                if (receipt.get('error_details') or {}).get('category') in {'authentication', 'permission', 'quota'}:
                    stop_submit = True
                continue
            task_id = receipt.get('task_id')
            if task_id and status in {'PENDING', 'RUNNING', 'UNKNOWN', 'SUCCEEDED', 'DOWNLOAD_FAILED'}:
                receipt = client.poll(task_id)
                status = receipt.get('status', 'UNKNOWN')
                updates = {key: receipt[key] for key in ['task_id', 'error', 'error_details', 'generation_post_attempted', 'usage'] if key in receipt}
                _segment_update(pid, run_id, sid, status=status, **updates)
                if receipt.get('last_poll_failed'):
                    needs_manual = True
                    continue
            if status == 'SUCCEEDED' and receipt.get('result_ready'):
                _segment_update(pid, run_id, sid, status='DOWNLOADING')
                try:
                    download = client.download(task_id, _run_path(pid, run_id).parent / (sid + '_generated.mp4'))
                    current_run = _read(_run_path(pid, run_id))
                    current_segment = next(item for item in current_run['segments'] if item['segment_id'] == sid)
                    asset, attached = _publish(pid, current_run, current_segment, download)
                    _segment_update(pid, run_id, sid, status='COMPLETED', asset=asset, attached=attached, error=None if attached else '结果已保存为历史候选；材料或当前候选已变化，未覆盖现有成果。', error_details=None)
                except Exception as error:
                    _segment_update(pid, run_id, sid, status='DOWNLOAD_FAILED', error=str(error)[:500], error_details=_error(error))
                    needs_manual = True
            elif status == 'UNKNOWN':
                needs_manual = True
        except Exception as error:
            details = _error(error)
            if details.get('category') == 'snapshot':
                _segment_update(pid, run_id, sid, status='NOT_SUBMITTED', error=details['message'], error_details=details)
                stop_submit = True
            else:
                # No automatic paid retry, including errors before a task id arrives.
                try:
                    prior = _receipt(client, segment['request_key'])
                    attempted = bool(prior and prior.get('generation_post_attempted'))
                except Exception:
                    attempted = True
                _segment_update(pid, run_id, sid, status='UNKNOWN' if attempted else 'NOT_SUBMITTED', error=details['message'], error_details=details)
                needs_manual = True
    final = _mutate_run(pid, run_id, _aggregate)
    return final, needs_manual


def start_worker(pid, run_id, allow_submit=False, once=False):
    key = (pid, run_id)
    with _worker_lock:
        if key in _workers:
            return
        _workers.add(key)
    def worker():
        try:
            first = True
            while True:
                run, manual = drive_once(pid, run_id, allow_submit=allow_submit and first)
                first = False
                if once or manual or run['status'] != 'RUNNING':
                    break
                time.sleep(POLL_SECONDS)
        except Exception as error:
            _mutate_run(pid, run_id, lambda run: run.update(status='ATTENTION', error=_error(error)))
        finally:
            with _worker_lock:
                _workers.discard(key)
    threading.Thread(target=worker, daemon=True).start()


def refresh(pid, body):
    run_id = body.get('run_id')
    run = _read(_run_path(pid, run_id))
    if not run:
        raise ValueError('生成批次不存在，不会创建新任务')
    start_worker(pid, run_id, allow_submit=False, once=True)
    return state(pid)


def adopt(pid, body):
    # A returned prompt is evidence only when it uniquely identifies a local
    # attempted submission. Never guess between identical manual prompts.
    with _adoption_lock:
        with _lock(pid):
            run = _read(_run_path(pid, body.get('run_id')))
            if not run:
                raise ValueError('生成批次不存在')
            segment = next((item for item in run['segments'] if item['segment_id'] == body.get('segment_id')), None)
            if not segment or segment['status'] not in AMBIGUOUS or segment.get('task_id'):
                raise ValueError('仅可为提交状态未知且无编号的原请求补充平台任务编号')
            for path in (store.DATA / 'projects').glob('*/generation/runs/*/run.json'):
                other_run = _read(path)
                if other_run.get('base_url') != run.get('base_url'):
                    continue
                for other in other_run['segments']:
                    if other.get('request_key') == segment['request_key']:
                        continue
                    if other.get('task_id') == body.get('task_id'):
                        raise ValueError('这个平台任务已经关联其他片段，不能重复关联')
                    if other.get('prompt') == segment.get('prompt') and (other.get('generation_post_attempted') or other.get('status') in AMBIGUOUS):
                        raise ValueError('存在使用相同提示词的其他提交，无法唯一核实片段归属。请先到平台核对原任务及素材；不会猜测关联或重新付费提交。')
        receipt = _client(pid, run).adopt_task(segment['request_key'], body.get('task_id'))
        _segment_update(pid, run['id'], segment['segment_id'], task_id=receipt['task_id'], status=receipt['status'], error=None, error_details=None)
    return refresh(pid, {'run_id': run['id']})


def recover():
    """Restart only queries/downloads; never replay a generation POST."""
    for path in (store.DATA / 'projects').glob('*/generation/preparation.json'):
        prep = _read(path, {})
        if prep.get('status') == 'PREPARING':
            _set_preparation(path.parents[1].name, status='FAILED', error={'category':'local','message':'本地准备中断，请重新准备；没有因此提交生成。'})
        elif prep.get('status') == 'READY':
            # Read-only startup warming precedes HTTP listening. Never revive
            # stale/expired/consumed quotes, mutate a receipt or query a task.
            quote = prep.get('quote') or {}
            if not quote.get('confirmed_run') and quote.get('expires_at', 0) > time.time():
                try:
                    project = store.get_project(path.parents[1].name)
                    if prep.get('fingerprint') == quote.get('fingerprint') == _fp(project):
                        _public_audio_validation(quote, force=True)
                except (ValueError, OSError, KeyError, TypeError):
                    pass
    for path in (store.DATA / 'projects').glob('*/generation/runs/*/run.json'):
        run = _read(path)
        if any(item['status'] in LIVE | {'UNKNOWN', 'DOWNLOAD_FAILED'} for item in run['segments']):
            start_worker(run['project_id'], run['id'], allow_submit=False)


def _sample_context(pid, sid):
    project = store.get_project(pid)
    segment = rw._segment(project, sid)
    candidate = segment.get('sample_candidate') or {}
    provenance = segment.get('sample_provenance') or {}
    fingerprint = _fp(project)
    if (not candidate or provenance.get('asset_id') != candidate.get('id')
            or store.digest(candidate['path']) != candidate.get('sha256')):
        raise ValueError('候选小样与当前材料不一致，请保留旧结果并核对原批次')
    run = _read(_run_path(pid, provenance.get('run_id')))
    if not run:
        raise ValueError('原生成批次缺失，不能核实当前候选')
    sample = _sample_state(project, sid, run)
    if not sample['input_current']:
        raise ValueError(sample.get('reason') or '真实生成输入已变化，无法核实当前原始小样')
    return project, segment, candidate, provenance, fingerprint


def _inspect_sample(pid, sid):
    from . import candidate_adaptation
    project, segment, candidate, provenance, fingerprint = _sample_context(pid, sid)
    report = candidate_adaptation.inspect_candidate_return(project['source']['path'], candidate['path'],
        segment['start'], segment['end'], resolution=provenance['candidate_resolution'])
    def save(latest):
        current = rw._segment(latest, sid)
        if (current.get('sample_candidate') or {}).get('id') != candidate['id']:
            raise ValueError('核验期间小样已变化，未关联检查记录')
        current['sample_provenance']['return_inspection'] = report
    rw._commit(pid, fingerprint, save)
    return report


def _audition_job(pid, sid, fingerprint, candidate_id, fn):
    def record(status, error=None):
        def save(latest):
            current = rw._segment(latest, sid)
            if (current.get('sample_candidate') or {}).get('id') != candidate_id:
                raise ValueError('小样已变化，未写入旧试听状态')
            current['sample_provenance']['audition_attempt'] = {'status': status, 'error': error,
                'candidate_asset_id': candidate_id, 'material_fingerprint': fingerprint, 'updated_at': store.now()}
        rw._commit(pid, fingerprint, save)
    def run(jid):
        record('PREPARING')
        try:
            result = fn(jid)
            record('READY')
            return result
        except Exception as error:
            try:
                record('FAILED', str(error))
            except Exception:
                pass
            raise
    return store.start_job(pid, 'prepare_sample_audition', run, segment_id=sid)


def inspect_sample_result(pid, body):
    sid = body.get('segment_id')
    _sample_context(pid, sid)
    return store.start_job(pid, 'inspect_sample_result', lambda jid: {'report': _inspect_sample(pid, sid),
        'cloud_uploaded': False, 'generation_submitted': False}, segment_id=sid)


def prepare_sample_audition(pid, body):
    """An original-mix WAV plus raw candidate, restricted to common coverage."""
    sid = body.get('segment_id')
    project, segment, candidate, provenance, fingerprint = _sample_context(pid, sid)
    prior = _sample_audition(project, candidate, provenance)
    if prior:
        return {'asset': prior['audio_asset'], 'report': prior, 'reused': True, 'generation_submitted': False, 'cloud_uploaded': False}
    def prepare_audition(jid):
        from . import generation_audio
        inspection = _inspect_sample(pid, sid)
        if not inspection.get('pts_checked') or not inspection.get('audition_visual_coverage_seconds'):
            raise ValueError('候选实际PTS无法核实，不能猜测原声试听时间轴')
        directory = store.DATA / 'projects' / pid / 'exports'
        directory.mkdir(exist_ok=True)
        receipt = generation_audio.prepare_original_audio(project['source']['path'], directory / f'{sid}_原声试听_覆盖部分_{jid}.wav',
            segment['start'], segment['end'], coverage_seconds=inspection['audition_visual_coverage_seconds'])
        if store.digest(candidate['path']) != candidate['sha256']:
            raise ValueError('制作试听期间候选已变化，未关联原声')
        asset = store.add_asset(pid, receipt['path'], 'sample_audition_audio', meta=receipt['media'])
        receipt.update(audio_asset=asset, candidate_asset_id=candidate['id'], candidate_sha256=candidate['sha256'],
            material_fingerprint=fingerprint, status='ORIGINAL_AUDIO_AUDITION_ONLY',
            candidate_first_pts_seconds=float(Fraction(inspection['actual']['first_pts_rational'])),
            visual_coverage_seconds=inspection['audition_visual_coverage_seconds'],
            timeline_adaptation_status=inspection['timeline_adaptation']['status'],
            final_approved=False, strict_composite_ready=False, actual_review='UNREVIEWED')
        def save(latest):
            current = rw._segment(latest, sid)
            if (current.get('sample_candidate') or {}).get('id') != candidate['id']:
                raise ValueError('当前小样已变化，未关联试听')
            current['sample_provenance']['audition'] = receipt
        rw._commit(pid, fingerprint, save)
        return {'asset': asset, 'report': receipt, 'cloud_uploaded': False, 'generation_submitted': False,
                'message': '原声试听仅覆盖共同区间，仍需核对原始回片的规格和口型。'}
    return _audition_job(pid, sid, fingerprint, candidate['id'], prepare_audition)


def dispatch(pid, body):
    action = body.get('action')
    if action == 'prepare':
        return request_prepare(pid, body)
    if action == 'confirm':
        return confirm(pid, body)
    if action == 'refresh':
        return refresh(pid, body)
    if action == 'adopt':
        return adopt(pid, body)
    if action in ('prepare_sample_preview', 'reuse_completed_sample', 'confirm_sample_materials'):
        raise ValueError('第 4 步已移除小样遮罩合成。请查看或下载原始小样，也可使用原声试听。')
    if action == 'inspect_sample_result':
        return inspect_sample_result(pid, body)
    if action == 'prepare_sample_audition':
        return prepare_sample_audition(pid, body)
    raise ValueError('未知生成操作')
