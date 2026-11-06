"""Local original-mix contracts. Never uploads, retimes, or authorizes media."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import wave

from . import media, replacement_media as rm

METHOD = 'ORIGINAL_DECODED_PCM_NATIVE_SAMPLE_GRID_V1'
REFERENCE_METHOD = 'SOURCE_VIDEO_ZERO_ALIGNED_ORIGINAL_PCM_V1'
FINAL_TAIL_POLICY = 'FINAL_SOURCE_FRAME_NATURAL_AUDIO_END_V1'
MAX_BYTES = 15 * 1024 ** 2
_SOURCE_PCM_DIGESTS = {}


def _nearest_integer(value):
    lower = value.numerator // value.denominator
    return lower if value - lower <= Fraction(1, 2) else lower + 1


def _audio_timing(source):
    data = json.loads(media._run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_frames', '-show_streams',
        '-show_entries', 'frame=pts,nb_samples:stream=time_base,sample_rate,channels', '-of', 'json', source], timeout=180))
    try:
        stream, frames = data['streams'][0], data['frames']
        rate, channels, tick = int(stream['sample_rate']), int(stream['channels']), Fraction(stream['time_base'])
        first = int(frames[0]['pts']) * tick
        count = 0
        for frame in frames:
            point, samples = int(frame['pts']) * tick, int(frame['nb_samples'])
            if samples <= 0 or abs(point - first - Fraction(count, rate)) > tick:
                raise ValueError()
            count += samples
        if rate <= 0 or channels <= 0 or tick <= 0:
            raise ValueError()
    except (ValueError, KeyError, IndexError, ZeroDivisionError, TypeError):
        raise media.MediaError('原声缺少有效连续采样时间戳；不能猜测对齐或静默补齐') from None
    return {'first_pts': first, 'sample_rate': rate, 'channels': channels, 'sample_count': count, 'time_base': tick}


def plan_original_audio(source, source_start=0, source_end=None, *, coverage_seconds=None, reference=False):
    source = media._file(source)
    if not media.probe(source)['has_audio']:
        raise media.MediaError('原片没有音轨；不能提供或伪造原声参考／试听')
    video = rm._timing(source)
    if not video['constant_frame_rate']:
        raise media.MediaError('原片时间轴不恒定，不能猜测原声参考对应区间')
    first, stop, _, _ = rm._selection(video, source_start, source_end)
    audio = _audio_timing(source)
    rate, channels = audio['sample_rate'], audio['channels']
    duration = Fraction(stop - first) / Fraction(video['fps_rational'])
    if coverage_seconds is not None:
        duration = min(duration, Fraction(str(coverage_seconds)))
    if duration <= 0:
        raise media.MediaError('没有可试听的共同覆盖范围')
    video_origin = Fraction(str(video['first_pts']))
    origin = min(video_origin, audio['first_pts'])
    video_first = video_origin + Fraction(first) / Fraction(video['fps_rational'])
    window_start = video_first if reference else origin + Fraction(first) / Fraction(video['fps_rational'])
    video_offset = video_first - window_start
    relative = (window_start - audio['first_pts']) * rate
    end_relative = relative + (duration + video_offset) * rate
    start_sample, end_sample = _nearest_integer(relative), _nearest_integer(end_relative)
    count = end_sample - start_sample
    leading = max(0, -start_sample)
    source_first, source_stop = max(0, start_sample), start_sample + count
    if source_stop > audio['sample_count'] or source_stop <= source_first or leading >= count:
        raise media.MediaError('原声音轨未覆盖当前选段；不会补末端静音、循环或拉伸原声')
    return {'method': REFERENCE_METHOD if reference else METHOD, 'source_sha256': media._hash(source), 'source_path': str(source),
        'timeline_contract': 'REFERENCE_VIDEO_ZERO' if reference else 'ORIGINAL_AV_AUDITION',
        'final_audio_source': False, 'reference_video_first_pts_seconds': 0 if reference else None,
        'source_first_frame': first + 1, 'source_last_frame': stop,
        'selected_video_duration_rational': str(Fraction(stop - first) / Fraction(video['fps_rational'])),
        'coverage_duration_rational': str(duration), 'duration_seconds': count / rate,
        'source_window_start_rational': str(window_start), 'video_first_in_audio_rational': str(video_offset),
        'video_first_in_audio_seconds': float(video_offset),
        'sample_rate': rate, 'channels': channels, 'sample_width': 4, 'pcm_format': 's32le', 'sample_count': count,
        'source_first_sample': source_first, 'source_stop_sample': source_stop,
        'leading_original_gap_samples': leading, 'leading_original_gap_seconds': leading / rate,
        'source_audio_first_pts_rational': str(audio['first_pts']), 'selected_video_first_pts_rational': str(video_first),
        'original_audio_video_offset_rational': str(audio['first_pts'] - video_origin),
        'start_quantization_error_seconds': float(abs(Fraction(start_sample) - relative) / rate),
        'end_quantization_error_seconds': float(abs(Fraction(end_sample) - end_relative) / rate),
        'resampled': False, 'stretched': False, 'looped': False, 'terminal_silence_added': False,
        'candidate_audio_used': False, 'contains_original_voice_music_effects': True,
        'cloud_uploaded': False, 'generation_submitted': False}


def _reference_limits(plan):
    if not 1 <= plan['duration_seconds'] <= 15.00001 or plan['sample_count'] * plan['channels'] * 4 + 128 > MAX_BYTES:
        raise media.MediaError('原声参考须为1–15秒且WAV不超过15MB；不会为通过限制静默压缩或改采样率')


def plan_reference_audio(source, source_start=0, source_end=None):
    """One canonical upload contract; only a real final-frame EOF may be short.

    Full references retain their existing V1 dictionaries byte-for-byte. The
    exception never takes a caller-supplied coverage, segment name or project
    order as evidence. Source PTS and decoded audio EOF decide the only range.
    """
    source = media._file(source)
    source_sha = media._hash(source)
    try:
        plan = plan_original_audio(source, source_start, source_end, reference=True)
    except media.MediaError as original_error:
        from . import candidate_adaptation as ca
        meta = media.probe(source)
        if not meta.get('has_audio'):
            raise original_error
        video = ca._timestamps(source, Fraction(meta['fps_rational']), '自然尾差原片')
        audio = _audio_timing(source)
        period = video['period']
        # This exception requires an exact, continuous source clock. Existing
        # full V1 references keep their prior quantized-clock support.
        if (audio['time_base'] > Fraction(1, audio['sample_rate']) or
                any(point != index * period for index, point in enumerate(video['points'])) or
                video['terminal_duration'] != period):
            raise media.MediaError('原声自然尾差需要可认证的连续原生时间戳，不能猜测对齐')
        first, stop, _, requested_end = rm._selection(
            {'points': [float(p) for p in video['points']], 'end': float(video['end'])}, source_start, source_end)
        if stop != video['frame_count'] or abs(Fraction(str(requested_end)) - video['end']) > Fraction(1, 1000000):
            raise media.MediaError('仅真实原片末段可使用自然结束原声；中间段或未到视频实际终点仍阻断')
        video_first = video['first_pts'] + video['points'][first]
        video_last = video['first_pts'] + video['points'][-1]
        video_end = video['first_pts'] + video['end']
        audio_end = audio['first_pts'] + Fraction(audio['sample_count'], audio['sample_rate'])
        gap = video_end - audio_end
        if not (video_last < audio_end < video_end and 0 < gap < period and audio['first_pts'] <= video_first):
            raise media.MediaError('原声缺失超出实际最后一帧，或未覆盖参考起点；不能作为自然尾差放行')
        coverage = audio_end - video_first
        plan = plan_original_audio(source, source_start, source_end, reference=True, coverage_seconds=coverage)
        if plan['source_stop_sample'] != audio['sample_count'] or plan['leading_original_gap_samples']:
            raise media.MediaError('自然结束原声未精确取到实际EOF；未补声或额外截短')
        plan.update(reference_coverage_policy=FINAL_TAIL_POLICY,
            source_video_frame_count=video['frame_count'], source_video_end_rational=str(video_end),
            source_audio_total_samples=audio['sample_count'], source_audio_end_rational=str(audio_end),
            natural_tail_gap_rational=str(gap), maximum_tail_gap_rational=str(period))
    _reference_limits(plan)
    if plan['source_sha256'] != source_sha or media._hash(source) != source_sha:
        raise media.MediaError('规划期间原片已变化，未接受原声参考合同')
    return plan


def reference_prompt_note(contract):
    note = (f'本次原声参考时间合同：音频1是同一原片连续人声、音乐与音效，已按归零视频1的实际源PTS对齐。'
            f'视频1首视频帧对应音频1的0秒，音频窗口总长{contract["duration_seconds"]:.9f}秒。')
    if contract.get('reference_coverage_policy') == FINAL_TAIL_POLICY:
        note += (f'原音轨在最后一帧内自然结束，完整视频仍保留{float(Fraction(contract["selected_video_duration_rational"])):.9f}秒；'
                 '不得随参考音频缩短画面，不补声音或拉伸。')
    return note + ('请依原片时间轴保留口型开合、闭唇、停顿、手势、切镜和音乐卡点，不拉伸或重演原声；'
                   '输出声音关闭，最终仍使用原片原声。原声参考不等于口型验收通过。')


def _source_pcm_digest(source, plan):
    """A digest derived from the source, never from a caller's WAV receipt."""
    key = (plan['source_sha256'], plan['source_first_sample'], plan['source_stop_sample'],
           plan['leading_original_gap_samples'], plan['sample_rate'], plan['channels'])
    if key in _SOURCE_PCM_DIGESTS:
        return _SOURCE_PCM_DIGESTS[key]
    digest = hashlib.sha256(bytes(plan['leading_original_gap_samples'] * plan['channels'] * 4))
    length = 0
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(source), '-map', '0:a:0', '-vn',
            '-af', f'atrim=start_sample={plan["source_first_sample"]}:end_sample={plan["source_stop_sample"]}',
            '-c:a', 'pcm_s32le', '-f', 's32le', 'pipe:1'], stdout=subprocess.PIPE, stderr=errors)
        try:
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b''):
                length += len(chunk); digest.update(chunk)
            if proc.wait(timeout=180) or length != (plan['source_stop_sample'] - plan['source_first_sample']) * plan['channels'] * 4:
                raise media.MediaError('实际原片采样窗口无法完整解码，未接受参考音频')
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=5)
            proc.stdout.close()
    if media._hash(source) != plan['source_sha256']:
        raise media.MediaError('核对原声PCM期间原片已变化')
    if len(_SOURCE_PCM_DIGESTS) >= 32:
        _SOURCE_PCM_DIGESTS.clear()
    _SOURCE_PCM_DIGESTS[key] = digest.hexdigest()
    return digest.hexdigest()


def _validate_reference_audio(source, source_start, source_end, contract, path):
    """Recompute the sole allowed contract, then verify the actual frozen WAV."""
    expected = plan_reference_audio(source, source_start, source_end)
    if any(contract.get(key) != value for key, value in expected.items()):
        raise media.MediaError('原声参考合同与真实原片范围或自然结束边界不一致')
    # A full V1 contract cannot smuggle an alternative partial-coverage policy.
    if contract.get('reference_coverage_policy') != expected.get('reference_coverage_policy'):
        raise media.MediaError('原声参考覆盖策略与实际原片不一致')
    path = media._file(path)
    if media._hash(path) != contract.get('sha256'):
        raise media.MediaError('已确认的输入文件发生变化；实际原声参考WAV指纹不一致')
    try:
        with wave.open(str(path), 'rb') as reader:
            actual = (reader.getframerate(), reader.getnchannels(), reader.getsampwidth(), reader.getnframes())
            if actual != (expected['sample_rate'], expected['channels'], expected['sample_width'], expected['sample_count']):
                raise media.MediaError('实际原声参考WAV采样格式或长度与合同不一致')
            pcm = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError):
        raise media.MediaError('实际原声参考WAV不可核对') from None
    pcm_digest = hashlib.sha256(pcm).hexdigest()
    if (pcm_digest != contract.get('pcm_sha256') or pcm_digest != _source_pcm_digest(source, expected) or
            contract.get('reference_audio') is not True or contract.get('retained_source_samples_exact') is not True):
        raise media.MediaError('实际原声参考PCM与已核对收据不一致')
    if media._hash(path) != contract.get('sha256') or media._hash(source) != expected['source_sha256']:
        raise media.MediaError('核验期间原片或原声参考已变化')
    return expected


def validate_reference_audio(source, source_start, source_end, contract, path):
    try:
        return _validate_reference_audio(source, source_start, source_end, contract, path)
    except media.MediaError as error:
        # Generation state and reuse treat invalid receipts as local blockers,
        # rather than leaking a media RuntimeError into the page endpoint.
        raise ValueError(str(error)) from None


def _prepare_plan(source, out, plan, reference):
    if reference and (not 1 <= plan['duration_seconds'] <= 15.00001 or plan['sample_count'] * plan['channels'] * 4 + 128 > MAX_BYTES):
        raise media.MediaError('原声参考须为1–15秒且WAV不超过15MB；不会为通过限制静默压缩或改采样率')
    out = media._new_output(out)
    if out.suffix.lower() != '.wav':
        raise media.MediaError('原声参考与无损试听须保存为WAV')
    with tempfile.TemporaryDirectory(prefix='.original-audio-', dir=out.parent) as temporary:
        decoded = Path(temporary) / 'decoded.wav'
        media._run(['ffmpeg', '-v', 'error', '-n', '-i', source, '-map', '0:a:0', '-vn', '-c:a', 'pcm_s32le', decoded])
        with wave.open(str(decoded), 'rb') as reader:
            if reader.getframerate() != plan['sample_rate'] or reader.getnchannels() != plan['channels'] or reader.getsampwidth() != 4:
                raise media.MediaError('原声解码格式发生变化；未接入参考')
            if plan.get('reference_coverage_policy') == FINAL_TAIL_POLICY and reader.getnframes() != plan['source_audio_total_samples']:
                raise media.MediaError('实际解码原声EOF与连续时间戳不一致；未接受自然尾差')
            reader.setpos(plan['source_first_sample'])
            content = reader.readframes(plan['source_stop_sample'] - plan['source_first_sample'])
        frame_bytes = plan['channels'] * 4
        if len(content) != (plan['source_stop_sample'] - plan['source_first_sample']) * frame_bytes:
            raise media.MediaError('原声实际解码覆盖不足；未补齐或拉伸')
        pcm = bytes(plan['leading_original_gap_samples'] * frame_bytes) + content
        with wave.open(str(out), 'wb') as writer:
            writer.setnchannels(plan['channels']); writer.setsampwidth(4); writer.setframerate(plan['sample_rate'])
            writer.writeframes(pcm)
    if media._hash(source) != plan['source_sha256']:
        raise media.MediaError('提取期间原片已变化；派生文件保留，未关联')
    with wave.open(str(out), 'rb') as reader:
        decoded_output = reader.readframes(reader.getnframes())
    if decoded_output != pcm:
        raise media.MediaError('原声派生文件回读未逐样本一致')
    plan.update(path=str(out), sha256=media._hash(out), media=media.probe(out), bytes=out.stat().st_size,
                pcm_sha256=hashlib.sha256(pcm).hexdigest(), retained_source_pcm_sha256=hashlib.sha256(content).hexdigest(),
                retained_source_samples_exact=True, reference_audio=reference, strict_final_approved=False)
    out.with_suffix('.wav.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    return plan


def prepare_original_audio(source, out, source_start=0, source_end=None, *, reference=False, coverage_seconds=None):
    plan = plan_original_audio(source, source_start, source_end, coverage_seconds=coverage_seconds, reference=reference)
    return _prepare_plan(source, out, plan, reference)


def prepare_reference_audio(source, out, source_start=0, source_end=None):
    plan = plan_reference_audio(source, source_start, source_end)
    result = _prepare_plan(source, out, plan, True)
    validate_reference_audio(source, source_start, source_end, result, result['path'])
    return result
