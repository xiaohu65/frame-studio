"""Wan input checks and the separate contract for original-stream delivery."""
from __future__ import annotations

from fractions import Fraction
import math

from . import media

DOC = 'https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference'
GUIDE = 'https://help.aliyun.com/zh/model-studio/wan3-video-generation-guide'
RESOLUTIONS = {'480P': 480, '720P': 720, '1080P': 1080}
MODELS = {'wan3.0-video', 'wan3.0-video-prime'}
RATIOS = {'adaptive', '21:9', '16:9', '4:3', '1:1', '3:4', '9:16'}
# Original Wan3 returns for projects 87d66a0a69554212 and 3a9e47dc8e8b4e32.
# These are observed targets. The provider API only promises a tier/ratio request.
OBSERVED_OUTPUT_SIZES = {
    ('wan3.0-video', '16:9', '720P'): (1280, 720),
    ('wan3.0-video', '16:9', '1080P'): (1920, 1080),
}


def _rate(meta):
    try:
        return Fraction(str(meta.get('fps_rational') or meta.get('fps') or '0'))
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


PURPOSES = {'strict_final', 'candidate_sample'}


def _source_ratio(meta):
    width, height = meta.get('width'), meta.get('height')
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (width, height)):
        return None
    divisor = math.gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


def delivery_spec(project, segment_ids=None, *, verified=False, exact_grid=False):
    """Describe a single tier. Quotes freeze coverage from the decoded source."""
    meta, settings = project.get('source_meta', {}), project.get('settings', {})
    model = settings.get('model', 'wan3.0-video')
    resolution = settings.get('resolution', '480P')
    request_ratio = settings.get('ratio', 'adaptive')
    ratio = _source_ratio(meta)
    source_size = (meta.get('width'), meta.get('height'))
    dimensions, basis = (None, None), 'unverified'
    if ratio and request_ratio in ('adaptive', ratio):
        observed = OBSERVED_OUTPUT_SIZES.get((model, ratio, resolution))
        if observed:
            dimensions, basis = observed, 'observed'
        elif model in MODELS and min(source_size) == RESOLUTIONS.get(resolution):
            # Keep the existing native-size path. This is a local acceptance
            # target, not evidence of how Wan maps the model/tier/ratio.
            dimensions, basis = source_size, 'source_bound'

    rate = _rate(meta)
    source = project.get('source') or {}
    timing = None
    if verified and source.get('path'):
        from . import replacement_media
        timing = replacement_media._timing(source['path'], decode_pts=exact_grid)
        rate = Fraction(timing['fps_rational'])
        if exact_grid:
            from . import raw_delivery
            raw = timing['raw_pts']
            if not raw_delivery.has_exact_frame_grid(raw['points'], raw['terminal_duration'], 1 / rate):
                raise media.MediaError('原片的实际时间戳或末帧时长无法通过无改画面直封要求，暂缓正式批次报价')
            timing.update(points=[float(point) for point in raw['points']],
                          end=float(raw['points'][-1] + raw['terminal_duration']))
    source_seconds = timing['end'] if timing else (
        meta.get('video_duration_seconds') or project.get('timeline_duration') or meta.get('duration'))
    source_count = timing['frame_count'] if timing else meta.get('video_frame_count')
    if source_count is None and source_seconds and rate:
        source_count = round(Fraction(str(source_seconds)) * rate)
    segments, warnings = [], []
    selected = project.get('segments', [])
    if segment_ids is not None:
        by_id = {segment['id']: segment for segment in selected}
        selected = [by_id[sid] for sid in segment_ids if sid in by_id]
    for segment in selected:
        sid = segment['id']
        start, end = segment['start'], segment['end']
        first = stop = None
        if timing:
            first, stop, _, _ = replacement_media._selection(timing, start, end)
        elif rate:
            tolerance = Fraction(2, 1_000_000)
            first = max(0, math.ceil((Fraction(str(start)) - tolerance) * rate))
            stop = max(first, math.ceil((Fraction(str(end)) - tolerance) * rate))
        count = stop - first if first is not None else None
        duration = count / rate if count is not None else None
        segments.append({'segment_id': sid, 'source_start': start, 'source_end': end,
                         'first_source_frame': first + 1 if count else None, 'last_source_frame': stop if count else None,
                         'frame_count': count, 'duration_seconds': float(duration) if duration is not None else None})
        if settings.get('duration', -1) == -1 and duration is not None:
            message = f'{sid} 段要求完整覆盖 {count} 帧、{float(duration):.6f} 秒。'
            if duration.denominator != 1:
                message += '固定时长只能填整数秒，无法指定这一帧数。'
            message += '智能时长由模型决定，可能少帧或多帧。收到原始回片后按解码帧和时间戳验收。'
            warnings.append({'segment_id': sid, 'message': message})
    frame_count = sum(row['frame_count'] for row in segments) if segments and rate else None
    return {'revision': project.get('delivery_revision', 0),
            'model': model, 'resolution': resolution, 'ratio': ratio, 'request_ratio': request_ratio,
            'width': dimensions[0], 'height': dimensions[1], 'fps': str(rate) if rate else None, 'model_output_fps': '30',
            'dimension_basis': basis, 'frame_count': frame_count,
            'duration_seconds': float(frame_count / rate) if frame_count is not None else None,
            'source_frame_count': source_count, 'source_duration_seconds': source_seconds,
            'coverage_verified': timing is not None, 'segments': segments, 'duration_warnings': warnings}


def delivery_dimensions(project):
    spec = delivery_spec(project)
    return (spec['width'], spec['height']) if spec['width'] is not None else None


def inspect(project, purpose='strict_final', segment_ids=None):
    if purpose not in PURPOSES:
        raise ValueError('请选择严格成片或候选小样用途')
    meta, settings = project.get('source_meta', {}), project.get('settings', {})
    width, height = meta.get('width', 0), meta.get('height', 0)
    spec = delivery_spec(project, segment_ids)
    rate = _rate(meta)
    resolution = settings.get('resolution', '480P')
    model = settings.get('model', 'wan3.0-video')
    ratio = settings.get('ratio', 'adaptive')
    issues, final_issues = [], []

    def issue(code, message, details, **extra):
        item = {'code': code, 'message': message, 'details': details,
                'blocking': True, 'scope': 'tool_strict_compatibility', **extra}
        if purpose == 'candidate_sample' and code in {'MODEL_FPS_INCOMPATIBLE', 'OUTPUT_SIZE_TIER_MISMATCH'}:
            final_issues.append(item)
        else:
            issues.append(item)

    if model not in MODELS:
        issue('MODEL_UNSUPPORTED', '当前模型尚未适配，请选择已支持的 Wan3 模型。', '模型名称必须为 wan3.0-video 或 wan3.0-video-prime。')
    if project.get('source'):
        if rate < 16:
            issue('MODEL_INPUT_FPS_UNSUPPORTED', 'API 参考视频须至少 16fps。', '候选小样也不能绕过实际输入帧率要求。', scope='api_input')
        if rate != 30:
            shown = f'{float(rate):.3f}'.rstrip('0').rstrip('.') if rate else '未知'
            if rate == Fraction(24000, 1001):
                issue('MODEL_FPS_INCOMPATIBLE', f'原片约 {shown}fps，仅开放本地候选适配验证，上传与付费生成尚未开放。',
                      'API参考视频允许≥16fps，输出固定30fps；精确24000/1001原片可在第5步本地验证候选取帧，'
                      '原片、遮罩和最终时间轴保持24000/1001。适配通过不代表实际结果验收或付费授权。')
            else:
                issue('MODEL_FPS_INCOMPATIBLE', f'原片约 {shown}fps，本工具严格合成需要与 Wan3 的 30fps 输出一致。',
                      f'API参考视频允许≥16fps，输出固定30fps；本工具第5步要求与原片帧率完全一致。当前精确帧率：{meta.get("fps_rational") or meta.get("fps") or "未知"}。不会自动补帧或调速。')
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in (width, height)):
            issue('OUTPUT_SIZE_UNKNOWN', '原片尺寸尚未核实，请检查原片文件。', '收费前必须能核实原片的宽、高与帧率。')
        else:
            short = min(width, height)
            matching = next((name for name, edge in RESOLUTIONS.items() if short == edge), None)
            if spec['ratio'] and ratio in ('adaptive', spec['ratio']) and resolution in RESOLUTIONS and spec['width'] is None:
                issue('OUTPUT_SIZE_TIER_MISMATCH', f'{model} · {spec["ratio"]} · {resolution} 的原始回片宽高待核实。',
                      '当前组合缺少可核实的目标宽高，暂缓正式批次报价。这不是API输入限制。原始回片须按本批规格直封。',
                      suggested_resolution=matching)
            if max(width, height) > 4096 or short < 240 or max(width, height) / short > 8:
                issue('MODEL_INPUT_SIZE_UNSUPPORTED', '原片尺寸超出当前 API 的参考视频要求。', 'API参考视频单边240–4096像素，长宽比不超过8:1。', scope='api_input')
            if ratio not in RATIOS or ratio not in ('adaptive', spec['ratio']):
                issue('OUTPUT_RATIO_MISMATCH', '所选画面比例与原片不同或不受支持，请使用随原片比例。',
                      f'请求ratio={ratio}，原片{width}×{height}；严格流程不裁剪或缩放。adaptive也只表示随输入推荐比例，不能保证精确像素尺寸。')
        if resolution not in RESOLUTIONS:
            issue('OUTPUT_SIZE_SETTING_INVALID', '请先在第 1 步选择本批交付档位。', '每批选一档，支持480P、720P、1080P。')
        duration = settings.get('duration', -1)
        for segment in project.get('segments', []):
            if segment_ids is not None and segment['id'] not in segment_ids:
                continue
            seconds = segment.get('end', 0) - segment.get('start', 0)
            if not 1 <= seconds <= 15.00001:
                issue('MODEL_INPUT_DURATION_UNSUPPORTED', f'{segment.get("name", segment["id"])}的时长须为 1–15 秒才能作为 API 参考视频。',
                      '这是API参考视频输入要求；本工具的分段保存规则不因此改变。', segment_id=segment['id'], scope='api_input')
            if duration != -1 and (not isinstance(duration, int) or isinstance(duration, bool) or not 2 <= duration <= 30 or abs(duration - seconds) > .002 or duration + seconds > 30.00001):
                issue('OUTPUT_DURATION_MISMATCH', '输出时长设置无法满足当前片段的严格时长要求。',
                      '现有替换流程使用duration=-1智能时长。若显式指定时长，必须与片段一致且满足API输入加输出不超过30秒；即使智能时长也仍须检验实际生成帧数和时长。', segment_id=segment['id'])
    return {'ready': not issues, 'issues': issues, 'strict_final_issues': final_issues,
            'purpose': purpose, 'sample_only': purpose == 'candidate_sample',
            'strict_final_ready': not issues and not final_issues, 'model': model, 'output_fps': 30,
            'resolution': resolution, 'ratio': ratio, 'source_width': width, 'source_height': height,
            'delivery_width': spec['width'], 'delivery_height': spec['height'], 'delivery_spec': spec,
            'source_fps': str(rate), 'exact_output_pixels_guaranteed': False,
            'result_requires_validation': True, 'verified_submission_conditions_only': True,
            'sources': [DOC, GUIDE]}


def assert_preflight(project, segment, report, purpose='strict_final', segment_ids=None):
    """Recheck the actual prepared/frozen input before upload and before POST."""
    if segment_ids is not None and segment['id'] not in segment_ids:
        raise ValueError('实际片段超出本次授权范围')
    compatibility = inspect(project, purpose, segment_ids)
    if not compatibility['ready']:
        raise ValueError(compatibility['issues'][0]['message'])
    parameters = report['parameters']
    if report['model'] != project['settings']['model'] or any(parameters.get(key) != project['settings'].get(key, default) for key, default in
            [('resolution', '480P'), ('ratio', 'adaptive'), ('duration', -1), ('audio', False), ('watermark', False)]):
        raise ValueError('实际请求参数与当前生成设置不一致，请重新准备')
    references = [item for item in report['media'] if item['type'] == 'reference_video']
    if len(references) != 1:
        raise ValueError('当前替换任务须使用一份独立原片分段参考')
    actual = media.probe(references[0]['path'])
    source = project['source_meta']
    if (actual['width'], actual['height']) != (source['width'], source['height']) or _rate(actual) != _rate(source):
        raise ValueError('实际分段文件的尺寸或帧率与原片不同，请重新准备')
    expected = segment['end'] - segment['start']
    if abs(actual.get('video_duration_seconds', actual['duration_seconds']) - expected) > 1 / 30 + .002:
        raise ValueError('实际分段文件时长与当前选区不一致，请重新准备')
    return compatibility
