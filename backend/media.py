"""Local, deterministic media operations driven by an explicit source timeline.

Generated video is never treated as motion-approved because it decodes. Optional
anchor-based retiming is a separate, recorded edit. Audio is laid once over the
complete sequence, so per-clip AAC padding cannot accumulate or duplicate beats.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


class MediaError(RuntimeError):
    pass


def _run(args, timeout=1800):
    try:
        p=subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=timeout)
    except FileNotFoundError:
        raise MediaError("缺少 ffmpeg / ffprobe，请安装后再处理") from None
    except subprocess.TimeoutExpired:
        raise MediaError("本地媒体处理超时；未提交远程生成") from None
    if p.returncode:
        # Local ffmpeg stderr contains paths, but never keys or remote input URLs.
        raise MediaError("本地媒体处理失败："+p.stderr[-1600:])
    return p.stdout


def _finite(v, name):
    v=float(v)
    if not math.isfinite(v):raise MediaError(name+"必须是有限数字")
    return v


def _file(path):
    p=Path(path).expanduser().resolve()
    if not p.is_file():raise MediaError("输入文件不存在")
    return p


def _new_output(path):
    p=Path(path).expanduser().resolve()
    if p.exists():raise MediaError("输出已存在，请使用新文件名以保留旧版")
    p.parent.mkdir(parents=True,exist_ok=True)
    return p


def _hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def probe(path) -> dict:
    p=_file(path)
    data=json.loads(_run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",p],timeout=60))
    streams=data.get('streams',[])
    video=next((s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    audio=next((s for s in streams if s.get('codec_type')=='audio'),None)
    fmt=data.get('format',{})
    duration=float(fmt.get('duration') or (video or audio or {}).get('duration') or 0)
    rate=(video or {}).get('avg_frame_rate','0/1')
    try:
        n,d=map(float,rate.split('/'));fps=n/d if d else 0
    except Exception:fps=0
    def stream_duration(stream):
        try:return float((stream or {}).get('duration'))
        except (ValueError,TypeError):
            if stream is video and video and fps:
                try:return int(video.get('nb_frames'))/fps
                except (ValueError,TypeError):pass
            return None
    return {'path':str(p),'duration_seconds':duration,'bytes':p.stat().st_size,'has_video':bool(video),'has_audio':bool(audio),
            'width':int((video or {}).get('width',0)),'height':int((video or {}).get('height',0)),'fps':fps,'fps_rational':rate,
            'video_codec':(video or {}).get('codec_name'),'audio_codec':(audio or {}).get('codec_name'),
            'audio_sample_rate':int((audio or {}).get('sample_rate',0)),'audio_channels':int((audio or {}).get('channels',0)),
            'video_duration_seconds':stream_duration(video),'audio_duration_seconds':stream_duration(audio),
            'video_start_seconds':float((video or {}).get('start_time') or 0),'audio_start_seconds':float((audio or {}).get('start_time') or 0),
            'video_start_time':float((video or {}).get('start_time') or 0),'audio_start_time':float((audio or {}).get('start_time') or 0)}


def extract_audio(source, out, start=0, duration=None) -> dict:
    """Extract unseparated original mix as lossless 48kHz WAV with zero start."""
    source=_file(source);out=_new_output(out);meta=probe(source)
    if not meta['has_audio']:raise MediaError("原片没有音轨，无法提取原声")
    start=_finite(start,'起点')
    if start<0 or start>=meta['duration_seconds']:raise MediaError("音频起点超出原片")
    duration=meta['duration_seconds']-start if duration is None else _finite(duration,'时长')
    if duration<=0 or start+duration>meta['duration_seconds']+.001:raise MediaError("音频范围超出原片")
    _run(['ffmpeg','-hide_banner','-loglevel','error','-n','-i',source,'-map','0:a:0','-af',f'atrim=start={start:.9f}:duration={duration:.9f},asetpts=PTS-STARTPTS',
          '-ac','2','-ar','48000','-c:a','pcm_s24le',out])
    info=probe(out)
    receipt={'status':'EXTRACTED_ORIGINAL_MIX','path':str(out),'sha256':_hash(out),'source':str(source),'source_start_seconds':start,'source_duration_seconds':duration,
             'contains_original_dialogue_and_effects':True,'music_isolated':False,'media':info}
    out.with_suffix(out.suffix+'.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt


def separation_capability() -> dict:
    configured=os.environ.get('FRAME_STUDIO_DEMUCS_PYTHON')
    dedicated=Path.home()/'.local/share/frame-studio/audio-env/bin/python'
    python=configured or (str(dedicated) if dedicated.is_file() else sys.executable)
    try:
        result=subprocess.run([python,'-c','import demucs.api; print(1)'],capture_output=True,text=True,timeout=15)
        available=result.returncode==0 and result.stdout.strip()=='1'
    except (OSError,subprocess.TimeoutExpired):available=False
    return {'available':available,'python':python,'engine':'Demucs htdemucs','runs_locally':True,'provider_charge':False,
            'note':'首次运行可能下载公开模型权重；伴奏仍可能含击球和环境声，不保证纯净音乐' if available else '尚未安装 Demucs；原声提取可用，音乐分离尚不可用'}


def separate_music(source, out_dir) -> dict:
    """Run installed local Demucs; never pretend original audio is a music stem."""
    source=_file(source);out_dir=Path(out_dir).expanduser().resolve()
    capability=separation_capability()
    if not capability['available']:
        raise MediaError('尚未安装 Demucs，无法分离伴奏；可以先使用“原声提取”保留原片全部声音')
    dest=out_dir/'htdemucs'/source.stem
    if dest.exists():raise MediaError('该分离结果已存在，请使用新目录保留旧版')
    out_dir.mkdir(parents=True,exist_ok=True)
    _run([capability['python'],'-m','demucs','--two-stems','vocals','-n','htdemucs','--device','cpu','--out',out_dir,source],timeout=7200)
    music=dest/'no_vocals.wav';vocals=dest/'vocals.wav'
    if not music.exists() or not vocals.exists():raise MediaError('分离程序未产生伴奏与人声音轨')
    receipt={'status':'SEPARATED_LOCAL_MODEL','source':str(source),'music_path':str(music),'vocals_path':str(vocals),'music_sha256':_hash(music),
             'music_isolated':True,'quality':'UNVERIFIED_SEPARATION_ARTIFACTS_POSSIBLE','provider_charge':False,'media':probe(music)}
    (out_dir/'separation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt


def trim_video(source,out,start,duration,width=1920,height=1080,fps=None):
    source=_file(source);out=_new_output(out)
    start=_finite(start,'起点');duration=_finite(duration,'时长')
    source_meta=probe(source)
    video_duration=source_meta.get('video_duration_seconds') or source_meta['duration_seconds']-source_meta.get('video_start_time',0)
    if start<0 or duration<=0 or start+duration>video_duration+.05:raise MediaError('截取范围超出输入视频')
    try:
        rate=Fraction(str(source_meta['fps_rational'] if fps is None else fps)).limit_denominator(1000000)
    except (ValueError,ZeroDivisionError,TypeError):raise MediaError('帧率须为正数或分数字符串，例如 24000/1001') from None
    if not 1<=float(rate)<=120:raise MediaError('帧率须在 1–120 之间')
    fps_text=f'{rate.numerator}/{rate.denominator}'
    _run(['ffmpeg','-hide_banner','-loglevel','error','-n','-i',source,'-vf',f'setpts=PTS-STARTPTS,trim=start={start:.9f}:duration={duration:.9f},setpts=PTS-STARTPTS,scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_text}',
          '-an','-c:v','libx264','-crf','16','-preset','medium','-pix_fmt','yuv420p','-movflags','+faststart',out])
    result=probe(out)
    error=abs(result['duration_seconds']-duration)
    if abs(result['fps']-float(rate))>1e-6 or error>1/float(rate)+.002:
        raise MediaError('截取后帧率或时长超过一帧容差，请检查输入时间轴')
    result['trim_validation']={'requested_start_seconds':start,'requested_duration_seconds':duration,'duration_error_seconds':error,
                               'requested_fps_rational':fps_text,'source_fps_rational':source_meta['fps_rational'],
                               'output_fps_rational':result['fps_rational'],'original_fps_preserved':abs(source_meta['fps']-result['fps'])<1e-6,
                               'status':'FPS_AND_DURATION_PASS'}
    return result


def _anchor_list(segment,meta):
    s0,s1=map(float,[segment['source_start'],segment['source_end']])
    trim_start=float(segment.get('trim_start',0))
    video_duration=meta.get('video_duration_seconds') or meta['duration_seconds']-meta.get('video_start_time',0)
    trim_end=float(segment.get('trim_end',video_duration))
    if s1<=s0 or trim_start<0 or trim_end<=trim_start or trim_end>video_duration+.05:
        raise MediaError('片段起止时间无效')
    anchors=segment.get('anchors')
    if anchors:
        points=[(_finite(a.get('generated_seconds',a.get('generated')),'生成时间'),_finite(a.get('source_seconds',a.get('source')),'原片时间')) for a in anchors]
        points.sort()
        # Endpoints prevent accidental omitted lead-in / tail. Users may add any
        # observed contact events inside the segment to correct local timing.
        if abs(points[0][0]-trim_start)>.05 or abs(points[0][1]-s0)>.05 or abs(points[-1][0]-trim_end)>.05 or abs(points[-1][1]-s1)>.05:
            raise MediaError('卡点锚点必须包含片段首尾，并使用原片绝对时间')
        if any(b[0]<=a[0] or b[1]<=a[1] for a,b in zip(points,points[1:])):
            raise MediaError('卡点锚点必须在生成时间和原片时间上同时严格递增')
    else:points=[(trim_start,s0),(trim_end,s1)]
    return points


def _map_source(points,s):
    for (ga,sa),(gb,sb) in zip(points,points[1:]):
        if sa-1e-6<=s<=sb+1e-6:return ga+(s-sa)/(sb-sa)*(gb-ga)
    raise MediaError('时间点不在锚点范围内')


def assemble(segments: list, out, music_path=None, audio_source_start=0, target_duration=None,
             width=1920,height=1080,fps=30,music_gain_db=0,source_audio_offset=0) -> dict:
    """Assemble source-ordered generated clips, removing later-clip overlaps.

    Segment schema: {path, source_start, source_end, trim_start?, trim_end?,
    anchors?: [{generated_seconds, source_seconds}]}. Source values are absolute
    seconds on one common source timeline; anchors must include both endpoints.
    Music offset is specified on the supplied audio file, never inferred from clips.
    """
    if not segments:raise MediaError('请至少添加一个待拼接视频')
    if width%2 or height%2 or not 1<=fps<=60:raise MediaError('输出尺寸须为偶数，帧率须为 1–60')
    out=_new_output(out)
    cursor=None;origin=None;plan=[];last_start=-math.inf
    for seg in segments:
        p=_file(seg['path']);meta=probe(p)
        if not meta['has_video']:raise MediaError('待拼接文件没有视频轨')
        s0,s1=map(float,[seg['source_start'],seg['source_end']])
        if not all(math.isfinite(v) for v in [s0,s1]):raise MediaError('原片时间无效')
        if s0<last_start:raise MediaError('片段必须按原片时间顺序排列')
        points=_anchor_list(seg,meta)
        if cursor is None:origin=s0;cursor=s0
        if s0>cursor+.001:raise MediaError('原片时间轴存在空档；请补足片段，不能静默冻结画面')
        visible=max(s0,cursor)
        if visible>=s1-1e-6:raise MediaError('片段完全被前面的片段覆盖')
        cuts=[visible]+[s for g,s in points if visible+1e-6<s<s1-1e-6]+[s1]
        for sa,sb in zip(cuts,cuts[1:]):
            ga,gb=_map_source(points,sa),_map_source(points,sb)
            plan.append({'path':str(p),'source_start':sa,'source_end':sb,'generated_start':ga,'generated_end':gb,'duration':sb-sa,
                         'speed_scale':(sb-sa)/(gb-ga),'overlap_trimmed_seconds':max(0,visible-s0),'anchors_supplied':bool(seg.get('anchors'))})
        cursor=s1;last_start=s0
    total=cursor-origin
    if target_duration is not None and abs(float(target_duration)-total)>1/fps:
        raise MediaError('指定总时长与片段原片时间轴不一致；请调整片段边界')
    with tempfile.TemporaryDirectory(prefix='wan-studio-assemble-',dir=out.parent) as temp:
        temp=Path(temp);parts=[]
        for i,item in enumerate(plan):
            part=temp/f'part-{i:04d}.mp4';parts.append(part)
            count=round((item['source_end']-origin)*fps)-round((item['source_start']-origin)*fps)
            if count<1:raise MediaError('相邻卡点间隔不足一帧，请合并过近的锚点')
            item['output_frames']=count
            # Allocate frames from the shared absolute timeline, preventing the
            # per-anchor rounding errors from accumulating across the sequence.
            vf=f"setpts=PTS-STARTPTS,trim=start={item['generated_start']:.9f}:end={item['generated_end']:.9f},setpts=(PTS-STARTPTS)*{item['speed_scale']:.12f},scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={int(fps)},tpad=stop_mode=clone:stop_duration={1/fps:.9f}"
            _run(['ffmpeg','-hide_banner','-loglevel','error','-n','-i',item['path'],'-vf',vf,'-an','-c:v','libx264','-crf','16','-preset','medium','-threads','4','-pix_fmt','yuv420p','-frames:v',count,part])
        # All paths are temporary ASCII filenames; concat cannot interpret user paths.
        listing=temp/'concat.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
        raw=temp/'joined.mp4'
        _run(['ffmpeg','-hide_banner','-loglevel','error','-n','-f','concat','-safe','1','-i',listing,'-map','0:v:0','-c:v','copy','-an',raw])
        args=['ffmpeg','-hide_banner','-loglevel','error','-n','-i',raw]
        if music_path:
            music=_file(music_path);ameta=probe(music)
            if not ameta['has_audio']:raise MediaError('音乐文件没有音频轨')
            audio_start=_finite(audio_source_start,'原声起点')+_finite(source_audio_offset,'原声音轨偏移')
            if audio_start<0 or audio_start+total>ameta['duration_seconds']+.02:
                raise MediaError('原声音轨长度不足以覆盖完整视频，不能静默循环或补静音')
            gain=_finite(music_gain_db,'音量')
            if not -60<=gain<=12:raise MediaError('音量范围须为 -60 至 12dB')
            args+=['-i',music,'-filter_complex',f'[1:a:0]atrim=start={audio_start:.9f}:duration={total:.9f},asetpts=PTS-STARTPTS,aresample=48000,volume={gain:.4f}dB[a]',
                   '-map','0:v:0','-map','[a]','-c:a','aac','-b:a','320k','-ar','48000']
        else:args+=['-map','0:v:0','-an']
        args+=['-c:v','copy','-t',f'{total:.9f}','-movflags','+faststart',out]
        _run(args)
    meta=probe(out)
    _run(['ffmpeg','-v','error','-xerror','-i',out,'-f','null','-'])
    duration_error=abs(meta['duration_seconds']-total)
    if duration_error>max(1/fps,.025)+.01:raise MediaError('拼接时长超出一帧容差；请检查片段和卡点锚点')
    receipt={'status':'ENCODE_AND_TIMELINE_PASS','path':str(out),'sha256':_hash(out),'media':meta,'timeline_origin_seconds':origin,'source_end_seconds':cursor,
             'target_duration_seconds':total,'duration_error_seconds':duration_error,'output_frame_tolerance_seconds':1/fps,'plan':plan,
             'audio':{'mode':'single_continuous_source_track' if music_path else 'none','path':str(Path(music_path).resolve()) if music_path else None,
                      'source_start_seconds':float(audio_source_start)+float(source_audio_offset),'gain_db':music_gain_db,'looped':False,'per_clip_audio_joined':False},
             'motion_quality':'UNVERIFIED_REVIEW_CONTACTS_AND_CAMERA','beat_sync':'ANCHORS_APPLIED_REVIEW_REQUIRED' if any(p['anchors_supplied'] for p in plan) else 'SOURCE_TIMELINE_AUDIO_ALIGNED_GENERATED_ACTIONS_REQUIRE_REVIEW'}
    out.with_suffix(out.suffix+'.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    return receipt
