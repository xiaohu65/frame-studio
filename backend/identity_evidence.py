"""Read-only, byte-verified evidence that a source-frame interval keeps the original.

This is mask-format evidence, not a segmentation or identity/quality approval.
No locator records or project files are created by this check.
"""
from . import media, replacement, replacement_media, shot_analysis


def inspect_mask(project, segment):
    source, mask = project['source'], segment['edit_mask']
    before = replacement.fingerprint(project)
    check = replacement_media.validate_mask(source['path'], mask['path'], segment['start'], segment['end'], allow_empty=True)
    evidence = None
    # Registered content hashes and exact cadence/size/count checks are all
    # required. A cached report or an unversioned mask cannot excuse identity.
    if (check.get('ready') and check.get('binary') is True
        and source.get('sha256') == check.get('source_sha256')
        and mask.get('sha256') == check.get('mask_sha256')):
        timing = replacement_media._timing(source['path'])
        first, stop, _, _ = replacement_media._selection(timing, segment['start'], segment['end'])
        grid_first = shot_analysis.frame_boundary(segment['start'], timing['fps_rational'])
        grid_stop = shot_analysis.frame_boundary(segment['end'], timing['fps_rational'])
        if (first, stop) != (grid_first, grid_stop):
            return check, None
        width, height = timing['meta']['width'], timing['meta']['height']
        proc = replacement_media._reader(mask['path'], width, height, gray=True)
        intervals, start = [], None
        try:
            for index in range(first, stop):
                black = not replacement_media._binary_frame(proc, width, height).any()
                if black and start is None:
                    start = index
                if not black and start is not None:
                    intervals.append((start, index)); start = None
            if start is not None:
                intervals.append((start, stop))
            proc.stdout.close()
            if proc.wait(timeout=30):
                raise media.MediaError('遮罩视频解码失败')
            if (before == replacement.fingerprint(project)
                and media._hash(source['path']) == check['source_sha256']
                and media._hash(mask['path']) == check['mask_sha256']):
                evidence = {'fingerprint': before, 'source_sha256': check['source_sha256'],
                            'mask_sha256': check['mask_sha256'], 'segment_id': segment['id'],
                            'first_frame': first+1, 'last_frame': stop,
                            'black_intervals': [{'first_frame': a+1, 'last_frame': b} for a, b in intervals],
                            'meaning': 'KEEP_ORIGINAL_NO_REPLACEMENT', 'identity_approved': False}
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
    return check, evidence


def covers(evidence, first, stop):
    """Ranges use zero-based half-open source indices, evidence uses UI frames."""
    return bool(evidence and first < stop and any(row['first_frame'] <= first+1 and row['last_frame'] >= stop
                                                for row in evidence.get('black_intervals', [])))
