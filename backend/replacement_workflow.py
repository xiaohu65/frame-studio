"""Source-video replacement preparation. Every action here is local-only.

The original is the timing and motion authority. Locator boxes are identity
review aids. Delivery uses the original candidate streams and continuous source audio.
"""
from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path
import zipfile

from . import generation_compatibility, store, replacement, replacement_media

ACTIONS = {'prepare_replacement', 'replacement_check', 'review_replacement',
           'validate_candidate', 'adapt_candidate', 'composite_replacement', 'review_candidate',
           'assemble_preview', 'assemble', 'save_prompt', 'package', 'preflight', 'render_masks'}
CHECKS = ('identity', 'action', 'camera', 'timing', 'audio', 'occlusion')
DELIVERY_POLICY = 'ORIGINAL_CANDIDATE_STREAM_COPY_V1'
_DERIVED_VIDEO_KINDS = {'adapted_candidate', 'candidate_adapted', 'composited', 'composite_preview',
                        'sample_preview', 'mask_support_master', 'mask_support_preview'}


def _segment(p, sid):
    segment = next((s for s in p['segments'] if s['id'] == sid), None)
    if not segment:
        raise ValueError('请明确选择一个片段')
    return segment


def _commit(pid, fingerprint, fn):
    """Do not replace a person's edits with a long-running job's old project."""
    with store.LOCK:
        latest = store.get_project(pid)
        if replacement.fingerprint(latest) != fingerprint:
            raise ValueError('处理期间项目已修改；保留了本地输出，请按最新人物与素材重新检查')
        fn(latest)
        return store.put_project(latest)


def _issue(plan, code, message, **extra):
    plan['issues'].append({'code': code, 'message': message, 'blocking': True, **extra})


def inspect_plan(p, sid=None):
    from . import identity_evidence, shot_analysis
    mask_issues, preserved = [], {}
    for s in p.get('segments', []):
        if sid is not None and s['id'] != sid:
            continue
        for kind in ('edit_mask', 'protection_mask'):
            asset = s.get(kind)
            if not asset or not p.get('source'):
                continue
            asset_context = {'asset_id': asset['id']} if asset.get('id') is not None else {}
            try:
                if kind == 'edit_mask':
                    check, evidence = identity_evidence.inspect_mask(p, s)
                    if evidence:
                        preserved[s['id']] = evidence
                else:
                    check = replacement_media.validate_mask(p['source']['path'], asset['path'], s['start'], s['end'], allow_empty=True)
                for issue in check.get('issues', []):
                    mask_issues.append({'code': kind.upper() + '_' + issue.get('code', 'INVALID'), 'message': issue['message'], 'blocking': True, 'segment_id': s['id'], **asset_context})
            except Exception as error:
                mask_issues.append({'code': kind.upper() + '_INVALID', 'message': str(error), 'blocking': True, 'segment_id': s['id'], **asset_context})
    current_fingerprint = replacement.fingerprint(p)
    preserved = {key: value for key, value in preserved.items() if value['fingerprint'] == current_fingerprint}
    plan = replacement.plan(p, sid, preserved_evidence=preserved)
    plan['issues'].extend(mask_issues)
    plan['preserved_intervals'] = preserved
    if sid is None:
        plan['segment_plans'] = {s['id']: replacement.plan(p, s['id'], preserved_evidence=preserved) for s in p.get('segments', [])}
    for s in p.get('segments', []):
        if sid is not None and s['id'] != sid:
            continue
        analysis = p.get('source_analysis') or {}
        if analysis.get('source_sha256') and analysis.get('source_sha256') == (p.get('source') or {}).get('sha256'):
            intervals = shot_analysis.shot_intervals(p, s)
            if len(intervals) > 1:
                for actor in p.get('source_actors', []):
                    observations = [o for o in actor.get('observations', []) if o.get('segment_id') == s['id'] and o.get('confirmed')]
                    for first, stop in intervals:
                        if identity_evidence.covers(preserved.get(s['id']), first, stop):
                            continue
                        if not any(first <= shot_analysis.frame_boundary(o['time'], p['source_meta']['fps_rational']) < stop for o in observations):
                            _issue(plan, 'SHOT_IDENTITY_UNCONFIRMED', f'{actor.get("label", actor["id"])}在{s["id"]}段原片第{first+1}–{stop}帧的镜头区间尚无人物位置确认；甩镜可能误报，仍需对照两侧确认。', actor_id=actor['id'], segment_id=s['id'], first_frame=first+1, last_frame=stop)
    for segment_id, segment_plan in plan.get('segment_plans', {}).items():
        # The per-segment report must enforce the same actual mask/shot checks
        # as the full report, rather than presenting a misleading green state.
        seen = {json.dumps(issue, sort_keys=True) for issue in segment_plan['issues']}
        for issue in plan['issues']:
            encoded = json.dumps(issue, sort_keys=True)
            if issue.get('segment_id') == segment_id and encoded not in seen:
                segment_plan['issues'].append(copy.deepcopy(issue)); seen.add(encoded)
        segment_plan['ready'] = not any(issue.get('blocking') for issue in segment_plan['issues'])
        segment_plan['preserved_intervals'] = {segment_id: preserved[segment_id]} if segment_id in preserved else {}
    plan['source_analysis'] = p.get('source_analysis')
    plan['ready'] = not any(i.get('blocking') for i in plan['issues'])
    plan['cloud_gate'] = {'upload_allowed': False, 'generation_allowed': False}
    plan['technical_pass_is_motion_approval'] = False
    plan['wan_compatibility'] = generation_compatibility.inspect(p)
    return plan


def _all_checks(body):
    checks = body.get('checks', {})
    if any(checks.get(key) is not True for key in CHECKS):
        raise ValueError('请逐项核对人物对应、动作、镜头、节奏、原声及遮挡后再确认')
    return {key: True for key in CHECKS}


def _digest_asset(asset):
    try:
        return store.digest(asset['path']) if asset else None
    except (OSError, KeyError, TypeError):
        return None


def refresh_adaptation_state(p):
    """Keep legacy adaptation links unchanged when loading or saving a project."""
    return p


def _candidate_inputs(p, s, material_fingerprint=None):
    asset = s.get('generated') or {}
    if not asset.get('id'):
        raise ValueError('尚未接收原始回片，请导入当前段原始视频')
    if (asset.get('kind') != 'generated' or s.get('candidate_adaptation')
            or any(a.get('kind') in _DERIVED_VIDEO_KINDS and a.get('sha256') == asset.get('sha256') for a in p.get('assets', []))):
        raise ValueError('当前文件属于历史适配、合成或导出，请重新导入原始回片')
    if s.get('candidate_stale'):
        raise ValueError('当前回片对应旧版材料，请接收当前段原始回片')
    candidate_sha = _digest_asset(asset)
    if not candidate_sha or candidate_sha != asset.get('sha256'):
        raise ValueError('原始回片文件缺失或内容已变化，请重新导入')
    source = p.get('source') or {}
    source_sha = _digest_asset(source)
    if not source_sha or source_sha != source.get('sha256'):
        raise ValueError('原片文件缺失或内容已变化，请重新核对原片')
    inputs = {'delivery_policy': DELIVERY_POLICY, 'material_fingerprint': material_fingerprint or replacement.fingerprint(p),
              'source_sha256': source_sha, 'segment_id': s['id'], 'source_start': s['start'], 'source_end': s['end'],
              'candidate_asset_id': asset['id'], 'candidate_sha256': candidate_sha}
    target = (s.get('generation_provenance') or {}).get('delivery_target')
    if target:
        inputs['delivery_target'] = target
    return inputs


def _fingerprint_inputs(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def candidate_fingerprint(p, s):
    return _fingerprint_inputs(_candidate_inputs(p, s))


def _assert_preparation(p):
    preparation = p.get('preparation') or {}
    if preparation.get('fingerprint') != replacement.fingerprint(p):
        raise ValueError('请先按当前项目制作原片参考并重新检查')
    records = preparation.get('references', {})
    for s in p['segments']:
        a = s.get('reference') or {}
        recorded = records.get(s['id']) or {}
        if a.get('kind') != 'source_reference' or a.get('id') != recorded.get('id') or not a.get('path') or store.digest(a['path']) != recorded.get('sha256'):
            raise ValueError(s['id'] + '段原片参考已变化，请重新制作；人偶或其它视频不能替代原片参考')


def _delivery_size_options(p, segment_inputs):
    default_size = generation_compatibility.delivery_dimensions(p)
    sizes = set()
    for row in segment_inputs:
        target = row.get('delivery_target')
        sizes.add((target['width'], target['height']) if target else default_size)
    if len(sizes) != 1:
        raise ValueError('各段须使用同一交付规格，才能直封为一份无字幕母版')
    size = sizes.pop()
    return {'output_size': size} if size else {}


def _candidate_check(p, s):
    inputs = _candidate_inputs(p, s)
    size_options = _delivery_size_options(p, [inputs])
    report = replacement_media.validate_candidate(
        p['source']['path'], s['generated']['path'], s['start'], s['end'],
        verify_raw_pts=True, **size_options)
    resolution = p['settings'].get('resolution')
    if resolution is None or (not size_options and p.get('delivery_revision')):
        report['ready'] = False
        report['issues'].append({'code': 'DELIVERY_SIZE_UNVERIFIED', 'blocking': True,
                                 'message': ('请先在第 1 步选择本批交付档位' if resolution is None else
                                             '所选交付档位的目标宽高待核实，原始回片可预览，母版技术验收待处理')})
    if inputs != _candidate_inputs(p, s):
        raise ValueError('检查期间原片、原始回片或材料已变化，请重新检查')
    report.update(delivery_policy=DELIVERY_POLICY, fingerprint=_fingerprint_inputs(inputs),
                  segment_id=s['id'], candidate_asset_id=inputs['candidate_asset_id'],
                  candidate_sha256=inputs['candidate_sha256'])
    return report


def _validation_current(s, fingerprint):
    validation = s.get('candidate_validation') or {}
    return (validation.get('delivery_policy') == DELIVERY_POLICY and validation.get('ready') is True
            and validation.get('fingerprint') == fingerprint)


def _review_current(s, fingerprint):
    review = s.get('candidate_review') or {}
    return (review.get('delivery_policy') == DELIVERY_POLICY and review.get('approved') is True
            and review.get('fingerprint') == fingerprint
            and all((review.get('checks') or {}).get(key) is True for key in CHECKS))


def _assert_plan_review(p):
    plan = inspect_plan(p)
    if not plan['ready'] or not replacement.material_confirmation_state(p)['current']:
        raise ValueError('当前版本的材料尚未确认；请在第4步核对人物对应、替换范围与参考素材后继续')


def _assembly_inputs(p):
    material_fingerprint = replacement.fingerprint(p)
    segments = []
    for s in p['segments']:
        inputs = _candidate_inputs(p, s, material_fingerprint)
        fingerprint = _fingerprint_inputs(inputs)
        if not _validation_current(s, fingerprint) or not _review_current(s, fingerprint):
            raise ValueError(s['id'] + '段仍缺少当前原始回片的技术检查或六项验收')
        segments.append({**inputs, 'candidate_fingerprint': fingerprint, 'review': copy.deepcopy(s['candidate_review'])})
    return {'delivery_policy': DELIVERY_POLICY, 'material_fingerprint': material_fingerprint,
            'source_sha256': _digest_asset(p.get('source')), 'timeline_duration': p.get('timeline_duration'),
            'segments': segments}


def result_states(p):
    """Read current raw-file, technical, and six-check evidence without saving."""
    rows = {}
    try:
        material_fingerprint = replacement.fingerprint(p)
        material_current = replacement.material_confirmation_state(p, material_fingerprint)['current']
    except (OSError, KeyError, TypeError, ValueError):
        material_fingerprint = None
        material_current = False

    for s in p.get('segments', []):
        preparation = p.get('preparation') or {}
        recorded_reference = preparation.get('references', {}).get(s['id']) or {}
        reference = s.get('reference') or {}
        reference_current = bool(material_fingerprint and preparation.get('fingerprint') == material_fingerprint and
                                 reference.get('kind') == 'source_reference' and reference.get('id') and
                                 recorded_reference.get('id') == reference['id'] and recorded_reference.get('sha256') and
                                 _digest_asset(reference) == recorded_reference['sha256'])
        reasons = {'candidate': None, 'validation': None, 'review': None}
        inputs = None
        try:
            inputs = _candidate_inputs(p, s, material_fingerprint)
        except (OSError, KeyError, TypeError, ValueError) as error:
            reasons['candidate'] = str(error)
        fingerprint = _fingerprint_inputs(inputs) if inputs else None
        validation_current = bool(fingerprint and _validation_current(s, fingerprint))
        if not validation_current:
            validation = s.get('candidate_validation') or {}
            failed = fingerprint and validation.get('fingerprint') == fingerprint and validation.get('ready') is False
            reasons['validation'] = '技术检查未通过' if failed else '当前原始回片尚未完成技术检查'
        review_current = bool(validation_current and material_current and _review_current(s, fingerprint))
        if not review_current:
            reasons['review'] = '当前材料尚未确认' if not material_current else '当前原始回片尚未完成六项验收'
        rows[s['id']] = {'candidate_current': bool(inputs), 'candidate_stale': bool(s.get('candidate_stale')),
                         'candidate_asset_id': inputs['candidate_asset_id'] if inputs else None,
                         'reference_current': reference_current, 'validation_current': validation_current,
                         'review_current': review_current, 'candidate_fingerprint': fingerprint, 'reasons': reasons}

    final_current = False
    final = p.get('final') or {}
    report = p.get('assembly_report') or {}
    if not final and not p.get('final_master'):
        final_reason = '尚未导出成片'
    elif report.get('delivery_policy') != DELIVERY_POLICY:
        final_reason = '历史成片 · 按原始回片重新验收后可导出'
    else:
        final_reason = '材料、回片、验收或成片文件已变化，历史成片仍可查看'
        provenance = report.get('provenance') or {}
        try:
            final_current = bool(rows and all(row['review_current'] for row in rows.values())
                                 and report.get('video_copy') is True
                                 and final.get('id') and final.get('sha256')
                                 and provenance.get('inputs') == _assembly_inputs(p)
                                 and provenance.get('asset_id') == final['id']
                                 and provenance.get('sha256') == final['sha256'] == _digest_asset(final))
        except (OSError, KeyError, TypeError, ValueError):
            pass
        if final_current:
            final_reason = None
    return {'delivery_policy': DELIVERY_POLICY, 'segments': rows, 'final_current': final_current, 'final_reason': final_reason}


def provider_media(p, s):
    _assert_preparation(p)
    refs = replacement.plan(p, s['id'])['references']
    return [{'type': 'reference_video', 'path': s['reference']['path']}] + [
        {'type': 'reference_image', 'path': a['path']} for a in refs]


def input_reference(p, s):
    """Expose the exact local file that would be sent, distinct from preview."""
    from . import media
    _assert_preparation(p)
    asset = s['reference']
    info = media.probe(asset['path'])
    actual = info.get('video_duration_seconds') or info['duration_seconds'] - info.get('video_start_time', 0)
    expected = s['end'] - s['start']
    tolerance = 1 / max(1, float(p.get('source_meta', {}).get('fps') or 30)) + .002
    if actual > 15.00001 or abs(actual - expected) > tolerance:
        raise ValueError(s['id'] + '段实际提交文件时长与选区不符，请重新准备片段；原片预览不会替代提交文件')
    if Path(asset['path']).resolve() == Path(p['source']['path']).resolve():
        raise ValueError('当前提交引用了原片全片，请重新制作独立的分段参考')
    return {'segment_id': s['id'], 'asset_id': asset['id'], 'name': asset['name'],
            'sha256': store.digest(asset['path']), 'duration_seconds': actual, 'expected_duration_seconds': expected,
            'source_start': s['start'], 'source_end': s['end'], 'fps_rational': info['fps_rational'],
            'width': info['width'], 'height': info['height'], 'uses_full_source_file': False}


def export_package(pid, jid):
    p = store.get_project(pid)
    fp = replacement.fingerprint(p)
    _assert_preparation(p)
    out = store.DATA / 'projects' / pid / 'exports' / f'通用替换_本地素材包_{jid}.zip'
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'workflow': 'replacement', 'fingerprint': fp, 'cloud_uploaded': False,
                'generation_submitted': False, 'segments': [], 'preservation': replacement.PRESERVATION,
                'delivery_spec': generation_compatibility.delivery_spec(p, verified=True)}
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for s in p['segments']:
            plan = replacement.plan(p, s['id'])
            folder = '片段_' + s['id'] + '/'
            for ref in plan['references']:
                # Registered names can contain user slashes; the archive stays flat.
                archive.write(ref['path'], folder + '参考图/' + ref['reference'] + '_' + Path(ref['name']).name)
            archive.write(s['reference']['path'], folder + '原片参考.mp4')
            for kind in ('edit_mask', 'protection_mask'):
                if s.get(kind):
                    archive.write(s[kind]['path'], folder + kind + Path(s[kind]['path']).suffix)
            prompt = p['prompts'].get(s['id']) or replacement.generate_prompt(p, s)
            archive.writestr(folder + '完整提示词.txt', prompt)
            manifest['segments'].append({'id': s['id'], 'start': s['start'], 'end': s['end'],
                                         'references': plan['references'], 'mappings': plan['mappings'],
                                         'reference_sha256': store.digest(s['reference']['path']), 'issues': plan['issues']})
        archive.writestr('人物引用与原片时间轴.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('使用说明.txt', '本包为本地准备材料，未上传、未生成。人物编号属于本地身份记录，不是Wan的硬性人物控制字段。\n'
                          '定位框不能代替逐帧黑白遮罩。动作、镜头、节奏、遮挡必须在候选结果中逐段复核。\n')
    asset = store.add_asset(pid, out, 'package')
    return {'asset': asset, 'fingerprint': fp, 'message': '本地素材包已导出，包含逐段准确图号、原片参考、提示词和人物对应清单'}


def action(pid, body, jid):
    from . import media, workflow
    a = body['action']
    if a in ('adapt_candidate', 'composite_replacement'):
        raise ValueError('已停用生成画面的二次处理。请核对原始回片，通过后直接拼接并接入音频。')
    p = store.get_project(pid)
    fp = replacement.fingerprint(p)
    outdir = store.DATA / 'projects' / pid / 'exports'
    outdir.mkdir(parents=True, exist_ok=True)
    sid = body.get('segment_id')
    if a == 'package':
        return export_package(pid, jid)
    if a == 'render_masks':
        from . import mask_editor, shot_analysis
        s = _segment(p, sid)
        analysis = p.get('source_analysis') or {}
        if analysis.get('source_sha256') != store.digest(p['source']['path']):
            analysis = shot_analysis.analyze(p['source']['path'])
        p['source_analysis'] = analysis
        result = mask_editor.render_masks(p, s, outdir, jid)
        edit = store.add_asset(pid, result['edit_mask_path'], 'edit_mask', meta=media.probe(result['edit_mask_path']))
        protect = store.add_asset(pid, result['protection_mask_path'], 'protection_mask', meta=media.probe(result['protection_mask_path']))
        preview = store.add_asset(pid, result['preview_path'], 'mask_preview', meta=media.probe(result['preview_path']))
        def save(latest):
            current = _segment(latest, sid)
            current.update(edit_mask=edit, protection_mask=protect, mask_preview=preview, mask_render_report=result['report'],
                           candidate_review=None, composited=None, composite_preview=None, candidate_validation=None)
            from .mask_review import context
            binding = context(latest, current)
            current['mask_render_report'].update(fingerprint=binding['fingerprint'], binding=binding['binding'])
            latest['preparation'] = None
            latest['generic_review'] = None
            latest['source_analysis'] = analysis
        _commit(pid, fp, save)
        return {'asset': preview, 'report': result['report'], 'message': '人工轮廓遮罩与叠加预览已在本机制作；请逐帧检查轮廓与遮挡，定位框未用于替换'}
    if a == 'save_prompt':
        s = _segment(p, sid)
        prompt = str(body.get('prompt') or replacement.generate_prompt(p, s))
        def save(latest):
            latest['prompts'][sid] = prompt
            latest['preparation'] = None
            latest['generic_review'] = None
            for segment in latest['segments']:
                segment['candidate_review'] = None
        _commit(pid, fp, save)
        return {'prompt': prompt, 'message': '提示词已保存；请按新版本重新制作与检查引用'}
    if a == 'replacement_check':
        plan = inspect_plan(p)
        _commit(pid, fp, lambda latest: latest.update(replacement_plan=plan))
        return {'plan': plan, 'message': '本地核对完成；仍有阻断项' if not plan['ready'] else '本地材料检查通过，仍等待人工审核和云端确认'}
    if a == 'prepare_replacement':
        if not p.get('source') or not p['segments']:
            raise ValueError('请先导入原片并保存分段')
        from . import shot_analysis
        analysis = p.get('source_analysis') or {}
        if analysis.get('source_sha256') != store.digest(p['source']['path']):
            analysis = shot_analysis.analyze(p['source']['path'])
        p['source_analysis'] = analysis
        clips, timelines = {}, {}
        for s in p['segments']:
            if replacement.fingerprint(store.get_project(pid)) != fp:
                raise ValueError('项目已修改，请重新制作本地参考')
            store.update_job(jid, message=f'正在按原帧率准备{s["id"]}段原片参考')
            dest = outdir / f'{s["id"]}_原片原速参考_{jid}.mp4'
            receipt = replacement_media.prepare_source_clip(p['source']['path'], dest, s['start'], s['end'])
            clips[s['id']] = store.add_asset(pid, dest, 'source_reference', meta=receipt.get('media', {}))
            timelines[s['id']] = receipt
        sheet = replacement_media.build_contact_sheet(p, outdir / f'人物与镜头核对_{jid}.png')
        contact = store.add_asset(pid, sheet['path'], 'identity_sheet')
        plan = inspect_plan(p)
        manifest = {'fingerprint': fp, 'plan': plan, 'timelines': timelines, 'contact_sheet': sheet,
                    'segment_plans': {s['id']: replacement.plan(p, s['id']) for s in p['segments']},
                    'cloud_uploaded': False, 'generation_submitted': False}
        path = outdir / f'人物场景替换清单_{jid}.json'
        store.save_json(path, manifest)
        manifest_asset = store.add_asset(pid, path, 'replacement_manifest')
        preparation = {'fingerprint': fp, 'plan': plan, 'contact_sheet': contact, 'manifest_asset': manifest_asset,
                       'timelines': timelines, 'references': {key: {'id': value['id'], 'sha256': value['sha256']} for key, value in clips.items()},
                       'created_at': store.now(), 'cloud_uploaded': False, 'generation_submitted': False}
        def save(latest):
            for s in latest['segments']:
                s['reference'] = clips[s['id']]
                s['reference_timeline'] = timelines[s['id']]['timeline']
                s['reviewed'] = False
            latest['preparation'] = preparation
            latest['replacement_plan'] = plan
            latest['generic_review'] = None
            latest['source_analysis'] = analysis
        _commit(pid, fp, save)
        return {'plan': plan, 'preparation': preparation, 'asset': contact, 'message': '原片参考、人物定位核对图和素材清单已在本机制作；未提交云端'}
    if a == 'review_replacement':
        if body.get('approved') is not True:
            _commit(pid, fp, lambda latest: latest.update(generic_review=None, material_confirmation=None))
            return {'message': '已撤销本地准备审核'}
        checks = _all_checks(body)
        plan = inspect_plan(p)
        if body.get('fingerprint') != plan['fingerprint']:
            raise ValueError('审核清单已变化，请重新检查最新人物与素材')
        if not plan['ready']:
            raise ValueError('本地清单仍有阻断项，请先处理人物绑定、素材与遮罩')
        _assert_preparation(p)
        review = {'approved': True, 'fingerprint': fp, 'checks': checks, 'reviewed_at': store.now(), 'generation_authorized': False}
        _commit(pid, fp, lambda latest: latest.update(generic_review=review))
        return {'plan': plan, 'review': review, 'message': '本地准备审核已记录；上传与生成仍等待用户确认'}
    if a == 'preflight':
        from .provider import WanClient
        config = {**workflow.config(private=True), 'model': p['settings'].get('model', 'wan3.0-video')}
        client = WanClient(config, store.DATA / 'projects' / pid / 'wan-tasks')
        plan = inspect_plan(p)
        reports, blockers = [], [i['message'] for i in plan['issues'] if i.get('blocking')]
        blockers.extend(i['message'] for i in plan['wan_compatibility']['issues'] if i.get('blocking'))
        targets = [_segment(p, sid)] if sid else p['segments']
        params = {k: v for k, v in p['settings'].items() if k != 'model'}
        for s in targets:
            try:
                reference = input_reference(p, s)
                report = client.preflight(p['prompts'].get(s['id']) or replacement.generate_prompt(p, s), provider_media(p, s), params)
                reports.append({**report, 'segment_id': s['id'], 'input_reference': reference})
            except (ValueError, RuntimeError) as error:
                blockers.append(s['id'] + '段：' + str(error))
        if not client.configured:
            blockers.append('尚未配置百炼 API Key；本地操作仍可继续')
        blockers.append('上传与付费生成尚未授权')
        total = round(sum(r['estimate']['reserved_yuan'] for r in reports), 4)
        return {'plan': plan, 'reports': reports, 'blockers': list(dict.fromkeys(blockers)),
                'total_reserved_yuan': total, 'estimate_complete': len(reports) == len(targets),
                'submission_inputs': [r['input_reference'] for r in reports],
                'cloud_uploaded': False, 'generation_submitted': False,
                'message': f'本地预检完成，已核对部分的保守预留为 ¥{total:.2f}；未访问云端'}
    if a == 'validate_candidate':
        s = _segment(p, sid)
        report = _candidate_check(p, s)
        def save(latest):
            current = _segment(latest, sid)
            if candidate_fingerprint(latest, current) != report['fingerprint']:
                raise ValueError('原始回片或材料已改变，请重新检查')
            current['candidate_validation'] = report
            review = current.get('candidate_review')
            if report['ready'] and review == s.get('candidate_review') and (review or {}).get('delivery_policy') == DELIVERY_POLICY:
                current['candidate_review'] = None
        _commit(pid, fp, save)
        return {'report': report, 'message': '原始回片技术检查完成；实际效果待逐项验收'}
    if a == 'review_candidate':
        s = _segment(p, sid)
        if body.get('approved') is not True:
            def revoke(latest):
                current = _segment(latest, sid)
                if (current.get('candidate_review') or {}).get('delivery_policy') == DELIVERY_POLICY:
                    current['candidate_review'] = None
            _commit(pid, fp, revoke)
            return {'message': '已撤销原始回片验收'}
        checks = _all_checks(body)
        _assert_plan_review(p)
        fingerprint = candidate_fingerprint(p, s)
        if not _validation_current(s, fingerprint):
            raise ValueError('请先完成当前原始回片的技术检查')
        if body.get('fingerprint') != fingerprint:
            raise ValueError('原始回片或材料已经变化，请重新检查并验收')
        review = {'delivery_policy': DELIVERY_POLICY, 'review_id': store.uid(), 'approved': True, 'fingerprint': fingerprint,
                  'checks': checks, 'reviewed_at': store.now()}
        def save(latest):
            current = _segment(latest, sid)
            _assert_plan_review(latest)
            if candidate_fingerprint(latest, current) != fingerprint or not _validation_current(current, fingerprint):
                raise ValueError('验收期间原始回片或检查记录已变化，请重新验收')
            current['candidate_review'] = review
        _commit(pid, fp, save)
        return {'review': review, 'message': '已保存原始回片六项验收'}
    if a in ('assemble_preview', 'assemble'):
        if body.get('anchors'):
            raise ValueError('通用替换保持原片动作与节奏，不支持通过调速锚点掩盖动作误差')
        if not p.get('source') or not p['segments']:
            raise ValueError('请先导入原片并保存完整分段')
        if a == 'assemble_preview':
            _assert_preparation(p)
            segments = [{'path': s['reference']['path'], 'source_start': s['start'], 'source_end': s['end']} for s in p['segments']]
            dest = outdir / f'原片流程预演_{jid}.mkv'
            video = dest.with_suffix('.mp4')
            store.update_job(jid, message='正在制作原片拼接预演')
            receipt = replacement_media.assemble_strict(p['source']['path'], segments, dest, target_end=p.get('timeline_duration'), preview_path=video)
            master = store.add_asset(pid, receipt['path'], 'local_preview_master', meta=receipt['media'])
            asset = store.add_asset(pid, video, 'local_preview', meta=media.probe(video))
            def save_preview(latest):
                _assert_preparation(latest)
                latest.update(local_preview=asset, local_preview_master=master)
            _commit(pid, fp, save_preview)
            return {'asset': asset, 'master': master, 'report': receipt, 'generation_submitted': False,
                    'message': '已制作原片拼接预演'}
        from . import raw_delivery
        _assert_plan_review(p)
        inputs = _assembly_inputs(p)
        segments = [{'path': s['generated']['path'], 'source_start': s['start'], 'source_end': s['end']} for s in p['segments']]
        store.update_job(jid, message='正在直拼原始回片并接回连续原声')
        receipt = raw_delivery.assemble_original(p['source']['path'], segments, outdir / f'原始回片成片_{jid}.mp4',
                                                 target_end=p.get('timeline_duration'), **_delivery_size_options(p, inputs['segments']))
        if receipt.get('video_copy') is not True or not receipt.get('sha256') or store.digest(receipt['path']) != receipt['sha256']:
            raise ValueError('导出文件未通过原始视频流或文件完整性核对')
        if (receipt.get('source_sha256') != inputs['source_sha256']
                or [row['sha256'] for row in receipt['plan']] != [row['candidate_sha256'] for row in inputs['segments']]):
            raise ValueError('导出实际使用的原片或回片与验收版本不一致，导出文件未关联')
        asset = store.add_asset(pid, receipt['path'], 'final', meta=receipt['media'])
        if asset['sha256'] != receipt['sha256']:
            raise ValueError('导出文件登记期间已变化，请重新导出')
        receipt.update(delivery_policy=DELIVERY_POLICY,
                       provenance={'inputs': inputs, 'asset_id': asset['id'], 'sha256': asset['sha256']})
        store.save_json(Path(asset['path']).with_suffix('.mp4.json'), receipt)
        def save(latest):
            _assert_plan_review(latest)
            try:
                current_inputs = _assembly_inputs(latest)
            except ValueError as error:
                raise ValueError('拼接期间回片或验收发生变化，导出文件未关联') from error
            if current_inputs != inputs or _digest_asset(asset) != asset['sha256']:
                raise ValueError('拼接期间回片、验收或导出文件发生变化，导出文件未关联')
            latest.update(final=asset, final_master=None, assembly_report=receipt)
        _commit(pid, fp, save)
        return {'asset': asset, 'report': receipt, 'generation_submitted': False,
                'message': '已直拼原始回片，并按原片时间轴接回连续原声'}
    raise ValueError('未知操作')
