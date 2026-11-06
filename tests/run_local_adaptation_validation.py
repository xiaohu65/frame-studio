"""Reproducible offline evidence, using synthetic media and a separate data root.

Run with the project's analysis Python and --output pointing to a new directory.
No real project, private configuration, or provider request is used.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RATE = Fraction(24000, 1001)
WIDTH, HEIGHT = 320, 180
EVENTS = [Fraction(48, 1)/RATE, Fraction(120, 1)/RATE, Fraction(192, 1)/RATE]


def run(args, data=None):
    result = subprocess.run([str(a) for a in args], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def frames(count, rate, origin=Fraction(0), candidate=False):
    result = []
    for i in range(count):
        t = origin+Fraction(i, 1)/rate
        image = Image.new('RGB', (WIDTH, HEIGHT), '#142130')
        draw = ImageDraw.Draw(image)
        draw.text((10, 8), f'{"30fps CANDIDATE" if candidate else "24000/1001 SOURCE"}  F{i+1}', fill='white')
        draw.text((10, 27), f't={float(t):.6f}s  exact={t}', fill='#b6d7ff')
        # A travelling ball touches the post at 5.005s, then returns.
        x = int(35+min(float(t/EVENTS[1]), float((2*EVENTS[1]-t)/EVENTS[1]))*220)
        draw.rectangle((267, 65, 275, 150), fill='#ffc54d')
        draw.ellipse((x, 95, x+12, 107), fill='#fe687c' if candidate else '#6fe0bd')
        draw.line((10, 113, WIDTH-10, 113), fill='#587084', width=2)
        if any(abs(t-event) <= Fraction(1, 2)/rate for event in EVENTS):
            draw.rectangle((10, 145, 310, 174), fill='#e0c046')
            draw.text((16, 153), 'EVENT / AUDIO MARKER', fill='black')
        # Machine-readable ordinal and global time, protected by the source mask.
        for bit in range(16):
            draw.rectangle((bit*10, 124, bit*10+8, 133), fill='white' if i & (1 << bit) else 'black')
        result.append(np.asarray(image))
    return np.stack(result)


def write_video(path, values, rate, audio=None, container_offset=0):
    args = ['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{WIDTH}x{HEIGHT}', '-r', str(rate), '-i', 'pipe:0']
    if audio:
        args += ['-itsoffset', '0.125', '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'pcm_s16le']
    else:
        args += ['-an']
    args += ['-c:v', 'png', '-pix_fmt', 'rgb24', '-threads', '2', '-fps_mode', 'passthrough',
             '-video_track_timescale', str(rate.numerator), '-output_ts_offset', str(container_offset), str(path)]
    run(args, values.tobytes())


def decode_rgb(path):
    return np.frombuffer(run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:v:0',
                              '-pix_fmt', 'rgb24', '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1']),
                         np.uint8).reshape(-1, HEIGHT, WIDTH, 3)


def decode_audio(path):
    return np.frombuffer(run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0',
                              '-ac', '1', '-ar', '48000', '-f', 'f32le', 'pipe:1']), np.float32)


def rejection_cases(out, source):
    from backend import candidate_adaptation as ca, media
    cases = []
    for name, count, rate in [('short', 270, Fraction(30)), ('long', 330, Fraction(30)),
                              ('unsupported25', 251, Fraction(25)), ('wrong-size', 301, Fraction(30)),
                              ('timestamp-gap', 301, Fraction(30))]:
        raw = out/f'rejected-{name}.mov'
        values = frames(count, rate, candidate=True)
        if name == 'wrong-size':
            run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=s=160x90:r=30',
                 '-frames:v', str(count), '-c:v', 'png', '-threads', '2', raw])
        elif name == 'timestamp-gap':
            run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                 '-s', f'{WIDTH}x{HEIGHT}', '-r', '30', '-i', 'pipe:0',
                 '-vf', 'setpts=PTS+if(gte(N\\,150)\\,0.1/TB\\,0)', '-fps_mode', 'passthrough',
                 '-an', '-c:v', 'png', '-threads', '2', '-video_track_timescale', '30000', raw], values.tobytes())
        else:
            write_video(raw, values, rate)
        dest = out/f'rejected-{name}-must-not-exist.mkv'
        try:
            ca.adapt_candidate(source, raw, dest, 0, 10.01)
        except media.MediaError as error:
            actual = media.probe(raw)
            cases.append({'name': name, 'fps_rational': actual['fps_rational'], 'frame_count': count,
                          'duration_seconds': actual['video_duration_seconds'], 'width': actual['width'],
                          'height': actual['height'], 'rejected': True, 'reason': str(error),
                          'derived_output_created': dest.exists()})
            assert not dest.exists()
        else:
            raise AssertionError(f'{name} was incorrectly accepted')
    # A small endpoint difference is insufficient if the nearest missing sample wins.
    brief = out/'endpoint-source51.mov'
    raw = out/'endpoint-candidate63.mov'
    write_video(brief, frames(51, RATE), RATE)
    write_video(raw, frames(63, Fraction(30), candidate=True), Fraction(30))
    try:
        ca.plan_adaptation(brief, raw, 0, float(Fraction(51, 1)/RATE))
    except media.MediaError as error:
        cases.append({'name': 'under33ms-but-missing-nearest', 'fps_rational': '30', 'frame_count': 63,
                      'duration_seconds': 2.1, 'target_frame_count': 51, 'target_duration_seconds': 2.127125,
                      'endpoint_difference_seconds': -0.027125, 'rejected': True, 'reason': str(error),
                      'derived_output_created': False})
    else:
        raise AssertionError('missing terminal sample incorrectly accepted')
    return cases


def prepare_ui_masks(out, pid):
    from backend import replacement_media as rm, workflow
    counts = [81, 79, 80]
    for kind, label in [('edit_mask', 'edit'), ('protection_mask', 'protect')]:
        path = out/f'ui-full-{label}.mkv'
        rm._write_rgb_master([{'path': out/f'segment-{i+1}-{label}.mkv', 'input_first': 0,
                               'input_stop': count} for i, count in enumerate(counts)],
                             path, WIDTH, HEIGHT, str(RATE))
        workflow.attach(pid, path, kind, segment_id='A')


def validate(out):
    if out.exists():
        raise ValueError('请选择新的隔离输出目录，避免覆盖先前证据')
    out.mkdir(parents=True)
    os.environ['FRAME_STUDIO_DATA'] = str(out/'isolated-data')
    # Empty environment and forbidden network methods protect the offline run.
    os.environ.pop('DASHSCOPE_API_KEY', None)
    os.environ.pop('BAILIAN_API_KEY', None)
    from unittest.mock import patch
    from backend import candidate_adaptation as ca, media, replacement, replacement_media as rm
    from backend import provider, store, workflow, replacement_workflow as rw, generation

    source_arrays = frames(240, RATE)
    audio = out/'continuous-markers.wav'
    sound = np.zeros(round((10.01-.125)*48000), np.int16)
    for event in EVENTS:
        at = round((event-Fraction(1, 8))*48000)
        sound[at:at+240] = (np.sin(np.arange(240)*2*np.pi*1000/48000)*16000).astype(np.int16)
    with wave.open(str(audio), 'wb') as handle:
        handle.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        handle.writeframes(sound.tobytes())
    source = out/'source-240frames-10.01s.mov'
    offset_source = out/'source-container-start-3s.mov'
    write_video(source, source_arrays, RATE, audio=audio)
    write_video(offset_source, source_arrays, RATE, audio=audio, container_offset=3)
    cases = []
    report = {'scope': 'LOCAL_SYNTHETIC_ONLY_NO_UPLOAD_NO_PAID_GENERATION',
              'source': str(source), 'offset_source': str(offset_source), 'cases': cases,
              'events_seconds': [float(t) for t in EVENTS], 'original_audio_gap_seconds': .125}

    def adapt_case(name, start, end, src=source, use_floor=False):
        timing = rm._timing(src)
        first, stop, _, _ = rm._selection(timing, start, end)
        count = stop-first
        duration = Fraction(count, 1)/RATE
        length = math.floor(duration*30) if use_floor else math.ceil(duration*30)
        origin = Fraction(first, 1)/RATE
        values = frames(length, Fraction(30), origin, candidate=True)
        raw = out/(name+'-raw30.mov')
        write_video(raw, values, Fraction(30), container_offset=7)
        prepared = rm.prepare_source_clip(src, out/(name+'-reference.mp4'), start, end)
        receipt = ca.adapt_candidate(src, raw, out/(name+'-adapted.mkv'), start, end)
        decoded = decode_rgb(receipt['path'])
        # Check actual decoded pixels against an independent rational oracle.
        target = [Fraction(i-first, 1)/RATE for i in range(first, stop)]
        indices = [min(range(length), key=lambda j: (abs(Fraction(j, 30)-t), j)) for t in target]
        np.testing.assert_array_equal(decoded, values[indices])
        actual = rm._timing(receipt['path'])
        assert actual['frame_count'] == count and Fraction(actual['fps_rational']) == RATE
        assert prepared['timeline']['frame_count'] == count
        error = max(abs(Fraction(j, 30)-t) for j, t in zip(indices, target))
        cases.append({'name': name, 'requested_start': start, 'requested_end': end,
                      'source_first_frame': first+1, 'source_last_frame': stop,
                      'source_first_pts': timing['first_pts'], 'raw_first_pts': rm._timing(raw)['first_pts'],
                      'raw_fps': '30', 'raw_frames': length, 'raw_duration_seconds': length/30,
                      'target_fps': str(RATE), 'target_frames': count,
                      'target_duration_exact': str(duration), 'target_duration_seconds': float(duration),
                      'maximum_sampling_error_seconds': float(error),
                      'first_mapping': [first+1, indices[0]+1], 'last_mapping': [stop, indices[-1]+1],
                      'pixel_mapping_verified': True, 'report_path': str(Path(receipt['path']).with_suffix('.mkv.json'))})
        return receipt, raw, (first, stop)

    with patch.object(provider.WanClient, '_request_json', side_effect=AssertionError('network forbidden')) as requests, \
         patch.object(provider.WanClient, '_upload', side_effect=AssertionError('upload forbidden')) as uploads:
        full, full_raw, _ = adapt_case('standard-240frames-ceil', 0, 10.01)
        adapt_case('noninteger-duration-floor', 0, 10.01, use_floor=True)
        adapt_case('nonzero-origin', 2.017, 6.672, src=offset_source)
        boundaries = [0, 3.337, 6.672, 10.01]
        clips, protection = [], []
        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            receipt, _, (first, stop) = adapt_case(f'segment-{i+1}', start, end, src=offset_source)
            # Existing masks use original cadence and selected frame count.
            mask = np.zeros((stop-first, HEIGHT, WIDTH), np.uint8)
            mask[:, 50:120, 15:280] = 255
            protect = np.zeros_like(mask)
            protect[:, 50:150, 250:285] = 255
            paths = []
            for label, values in [('edit', mask), ('protect', protect)]:
                path = out/f'segment-{i+1}-{label}.mkv'
                run(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', f'{WIDTH}x{HEIGHT}',
                     '-r', str(RATE), '-i', 'pipe:0', '-an', '-c:v', 'ffv1', '-pix_fmt', 'gray', '-threads', '2', path], values.tobytes())
                paths.append(path)
            composite = rm.composite_region(offset_source, receipt['path'], paths[0], out/f'segment-{i+1}-composite.mkv',
                                            start, end, protection_mask=paths[1])
            protection.append(composite['protected_pixel_max_error'])
            decoded = decode_rgb(composite['path'])
            expected = source_arrays[first:stop]
            np.testing.assert_array_equal(decoded[:, 124:134], expected[:, 124:134])
            clips.append({'path': composite['path'], 'source_start': start, 'source_end': end})
        assembly = rm.assemble_strict(offset_source, clips, out/'assembled-lossless.mkv', target_end=10.01,
                                      preview_path=out/'assembled-preview.mp4')
        assembled_frames = decode_rgb(assembly['path'])
        np.testing.assert_array_equal(assembled_frames[:, 124:134], source_arrays[:, 124:134])
        restored = decode_audio(assembly['path'])
        expected = np.concatenate([np.zeros(6000, np.float32), sound.astype(np.float32)/32768])
        assert len(restored) == len(expected), (len(restored), len(expected))
        np.testing.assert_array_equal(restored, expected)
        report['assembly'] = {'fps': assembly['fps_rational'], 'frames': assembly['frame_count'],
                              'duration_seconds': assembly['duration_seconds'], 'protected_pixel_max_errors': protection,
                              'all_source_frame_ordinals_continuous': True, 'audio': assembly['audio'],
                              'audio_samples': len(restored), 'audio_max_sample_error': float(np.max(np.abs(restored-expected))),
                              'marker_samples': [round(t*48000) for t in EVENTS], 'path': assembly['path'],
                              'preview_path': assembly['preview_path']}
        # Keep an isolated project ready for serial browser verification.
        p = store.create_project('隔离验证：24000/1001 本地适配（合成素材）')
        workflow.attach(p['id'], source, 'source')
        prepare_ui_masks(out, p['id'])
        p = store.get_project(p['id'])
        jid = store.uid()
        store.save_json(store.job_path(jid), {'id': jid, 'project_id': p['id'], 'action': 'prepare_replacement',
                                            'status': 'RUNNING', 'created_at': store.now(), 'updated_at': store.now()})
        workflow.action(p['id'], {'action': 'prepare_replacement'}, jid)
        store.update_job(jid, status='SUCCEEDED', message='本地合成测试参考已准备')
        workflow.attach(p['id'], full_raw, 'raw_candidate', segment_id='A')
        adapted = workflow.action(p['id'], {'action': 'adapt_candidate', 'segment_id': 'A'}, store.uid())
        again = workflow.action(p['id'], {'action': 'adapt_candidate', 'segment_id': 'A'}, store.uid())
        p = store.get_project(p['id'])
        report['isolated_project'] = {'id': p['id'], 'data_root': str(store.DATA),
                                       'adaptation_reused': again.get('reused'),
                                       'candidate_review': p['segments'][0].get('candidate_review'),
                                       'raw_sha256': store.digest(full_raw),
                                       'adapted_sha256': store.digest(p['segments'][0]['generated']['path']),
                                       'compatibility_ready': generation.state(p['id'])['compatibility']['ready']}
        assert not report['isolated_project']['compatibility_ready']
        assert report['isolated_project']['candidate_review'] is None
        requests.assert_not_called()
        uploads.assert_not_called()
        report['provider_request_count'] = requests.call_count
        report['cloud_upload_count'] = uploads.call_count
        report['rejections'] = rejection_cases(out, source)
    # Visual evidence of source and adapted contact neighbourhood.
    adapted_frames = decode_rgb(full['path'])
    sheet = Image.new('RGB', (WIDTH*2, HEIGHT*3), '#0c1520')
    for row, index in enumerate((119, 120, 121)):
        sheet.paste(Image.fromarray(source_arrays[index]), (0, row*HEIGHT))
        sheet.paste(Image.fromarray(adapted_frames[index]), (WIDTH, row*HEIGHT))
    sheet.save(out/'contact-event-comparison.png')
    (out/'validation-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'report': str(out/'validation-report.json'), 'project_id': report['isolated_project']['id'],
                      'cases': cases, 'assembly': report['assembly']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    validate(parser.parse_args().output.resolve())
