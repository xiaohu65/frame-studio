"""Independent local support domains, exact protection and failure isolation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
from PIL import Image

from backend import mask_editor, mask_review, mask_support as support, media, replacement_media as rm, server, store, workflow
from tests.test_mask_candidate_diagnostics import encode_rgb, png_array
from tests.test_mask_review import encode_mask, decode_mask


class LocalMaskSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        root = Path(cls.fixtures.name)
        cls.source, cls.candidate = root/'source.mp4', root/'candidate.mkv'
        cls.r = np.zeros((8, 64, 96), np.uint8)
        cls.r[:, 18:57, 25:72] = 255
        cls.r[2] = 0  # A genuine non-subject insert is always original pixels.
        cls.native_f = np.zeros((8, 32, 48), np.uint8)
        cls.native_f[:, 1:9, 21:27] = 255  # Tall crown.
        cls.native_f[:, 7:29, 18:30] = 255
        cls.native_f[:, 11:18, 8:42] = 255  # Wide shoulders/sleeves.
        cls.native_f[:, 24:32, 13:36] = 255  # Skirt.
        cls.native_f[2] = 0
        cls.f = np.stack([np.array(Image.fromarray(frame).resize((96, 64), Image.Resampling.NEAREST)) for frame in cls.native_f])
        cls.s = np.maximum(cls.r, cls.f)
        cls.s[:, 1:5, 1:7] = 255  # Allowed support alone must not copy background.
        cls.s[2] = 0
        cls.p = np.zeros_like(cls.r)
        cls.p[:, 42:53, 54:65] = 255  # Existing small foreground dog.
        cls.extra = np.zeros_like(cls.r)
        cls.extra[:, 50:59, 30:42] = 255  # A separately checked animal edge.
        original = np.empty((8, 64, 96, 3), np.uint8)
        original[:] = [25, 35, 45]
        original[cls.r == 255] = [145, 145, 145]
        original[cls.p == 255] = [60, 140, 220]
        original[cls.extra == 255] = [50, 180, 90]
        original[2] = [15, 65, 110]
        candidate = np.empty((8, 32, 48, 3), np.uint8)
        candidate[:] = [180, 70, 120]
        candidate[cls.native_f == 255] = [240, 200, 50]
        encode_rgb(cls.source, original, '30', first_pts_ticks=984)
        encode_rgb(cls.candidate, candidate, '30')
        cls.paths = {}
        for role, values in [('removal', cls.r), ('foreground', cls.f), ('support', cls.s), ('extra_protection', cls.extra), ('base_protect', cls.p), ('native_foreground', cls.native_f)]:
            cls.paths[role] = root/f'{role}.mkv'
            encode_mask(cls.paths[role], values, '30')

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(store, 'DATA', self.root), patch.object(workflow, 'DATA', self.root),
                        patch.object(store, 'start_job', return_value={'job_id': 'not-started'})]
        for item in self.patches:
            item.start()
        self.pid = store.create_project('仅本地支持域隔离测试')['id']
        workflow.attach(self.pid, self.source, 'source')
        self.sid = self.project()[1]['id']
        workflow.attach(self.pid, self.paths['removal'], 'edit_mask', segment_id=self.sid)
        workflow.attach(self.pid, self.paths['base_protect'], 'protection_mask', segment_id=self.sid)
        workflow.attach(self.pid, self.candidate, 'raw_candidate', segment_id=self.sid)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def project(self):
        p = store.get_project(self.pid)
        return p, p['segments'][0]

    def body(self, action='save_mask_support', **extra):
        p, s = self.project()
        ctx = mask_review.context(p, s)
        candidate = mask_review.diagnostic_candidate(p, s, s['raw_candidate']['id'])
        return {'action': action, 'segment_id': self.sid, 'mask_fingerprint': ctx['fingerprint'],
                'candidate_id': candidate['id'], 'candidate_fingerprint': candidate['fingerprint'], **extra}

    def imports(self, native=False):
        p, s = self.project()
        body = self.body()
        binding = support.status(p, s, body['candidate_id'], body['candidate_fingerprint'], body['mask_fingerprint'])['binding']
        result = []
        for role in ('foreground', 'support', 'extra_protection'):
            path = self.paths['native_foreground'] if native and role == 'foreground' else self.paths[role]
            value = {'schema': 'BOUND_SUPPORT_MASK_IMPORT_V1', 'binding': binding, 'role': role,
                     'timeline': 'candidate' if native and role == 'foreground' else 'source', 'mask_path': str(path), 'mask_sha256': media._hash(path)}
            target = self.root/f'{role}_import.json'
            target.write_text(json.dumps(value))
            result.append(str(target))
        return result

    def reviews(self, **extra):
        return [{'first_frame': 1, 'last_frame': 8, 'content': 'ok', 'protection': 'all_foreground_checked', 'cleanup': 'allow_candidate_background', **extra}]

    def save(self, **extra):
        return workflow.action(self.pid, self.body(imports=self.imports(), reviews=self.reviews(), **extra), store.uid())

    def render(self, version_id):
        # Exercise legacy renderer internals without exposing them as a project action.
        return support.action(self.pid, self.body('render_mask_support', version_id=version_id), store.uid())

    def pixels(self, path):
        proc = rm._reader(path, 96, 64)
        result = [np.frombuffer(rm._read_exact(proc.stdout, 96*64*3), np.uint8).reshape(64, 96, 3) for _ in range(8)]
        proc.stdout.close()
        self.assertEqual(proc.wait(timeout=30), 0)
        return np.stack(result)

    def test_new_crown_shoulders_and_skirt_survive_without_background_intrusion_and_protection_is_exact(self):
        hashes = {str(path): media._hash(path) for path in [self.source, self.candidate, *self.paths.values()]}
        before_p, before_s = self.project()
        version = self.save()['version_id']
        result = self.render(version)
        actual, original = self.pixels(result['master']['path']), self.pixels(self.source)
        candidate = png_array(mask_review.frame_data(*self.project(), 1, self.body()['mask_fingerprint'], self.body()['candidate_id'], self.body()['candidate_fingerprint'])['images']['candidate'])
        self.assertTrue(np.array_equal(actual[0, 5, 46], candidate[5, 46]))
        self.assertTrue(np.array_equal(actual[0, 28, 18], candidate[28, 18]))
        self.assertTrue(np.array_equal(actual[0, 61, 50], candidate[61, 50]))
        # Old subject is wider than new foreground; candidate background is used
        # only in the separately allowed removal region.
        self.assertTrue(np.array_equal(actual[0, 20, 28], candidate[20, 28]))
        self.assertTrue(np.array_equal(actual[:, 2, 2], original[:, 2, 2]))
        protected = (self.p == 255) | (self.extra == 255)
        self.assertTrue(np.array_equal(actual[protected], original[protected]))
        self.assertTrue(np.array_equal(actual[2], original[2]))
        allowed = ((self.r == 255) | (self.f == 255)) & (self.s == 255) & ~protected
        self.assertTrue(np.array_equal(actual[~allowed], original[~allowed]))
        report = json.loads(Path(result['report']['path']).read_text())
        self.assertTrue(report['protected_and_outside_pixels_exact'])
        self.assertEqual(report['source_passthrough_frames'], 1)
        self.assertFalse(report['six_checks_approved'])
        self.assertFalse(report['strict_final_approved'])
        after_p, after_s = self.project()
        self.assertEqual(after_s['edit_mask'], before_s['edit_mask'])
        self.assertEqual(after_s['protection_mask'], before_s['protection_mask'])
        self.assertEqual(after_s['raw_candidate'], before_s['raw_candidate'])
        self.assertIsNone(after_s.get('candidate_review'))
        self.assertEqual(hashes, {path: media._hash(path) for path in hashes})

    def test_candidate_native_foreground_import_uses_bound_nearest_binary_mapping(self):
        result = workflow.action(self.pid, self.body(imports=self.imports(native=True), reviews=self.reviews()), store.uid())
        p, s = self.project()
        state = support.status(p, s, self.body()['candidate_id'], self.body()['candidate_fingerprint'], self.body()['mask_fingerprint'])
        version = support._version(p, result['version_id'], state['binding'])
        pixels = decode_mask(version['layers']['foreground']['path'])
        self.assertTrue(np.array_equal(pixels, self.f))
        self.assertEqual(set(np.unique(pixels)), {0, 255})
        self.assertFalse(version['strict_final_approved'])

    def test_missing_top_misalignment_cleanup_and_all_animals_review_block_export(self):
        for content, protection, cleanup, code in [('missing_content', 'all_foreground_checked', 'allow_candidate_background', 'GENERATED_CONTENT_MISSING'),
                                                   ('misaligned', 'all_foreground_checked', 'allow_candidate_background', 'CANDIDATE_MISALIGNED'),
                                                   ('ok', 'unreviewed', 'allow_candidate_background', 'ALL_FOREGROUND_REVIEW_REQUIRED'),
                                                   ('ok', 'all_foreground_checked', 'unreviewed', 'CLEANUP_SOURCE_NOT_ALLOWED')]:
            with self.subTest(code=code):
                result = workflow.action(self.pid, self.body(imports=self.imports(), reviews=self.reviews(content=content, protection=protection, cleanup=cleanup)), store.uid())
                p, s = self.project()
                value = json.loads(Path(result['asset']['path']).read_text())
                self.assertIn(code, value['frame_reports']['1']['blocking'])
                before = len(p['assets'])
                with self.assertRaisesRegex(ValueError, '待修/未核对'):
                    self.render(result['version_id'])
                self.assertEqual(len(self.project()[0]['assets']), before)

    def test_uncovered_old_subject_and_outside_support_are_saved_pending_not_silently_clipped(self):
        body = self.body(imports=self.imports(), reviews=self.reviews())
        body['operations'] = [{'role': 'support', 'tool': 'erase', 'radius': 4, 'points': [[.49, .1]], 'first_frame': 1, 'last_frame': 1},
                              {'role': 'support', 'tool': 'erase', 'radius': 4, 'points': [[.29, .38]], 'first_frame': 1, 'last_frame': 1}]
        result = workflow.action(self.pid, body, store.uid())
        value = json.loads(Path(result['asset']['path']).read_text())
        self.assertIn('FOREGROUND_OUTSIDE_SUPPORT', value['frame_reports']['1']['blocking'])
        self.assertIn('OLD_REMOVAL_NOT_COVERED', value['frame_reports']['1']['blocking'])
        with self.assertRaisesRegex(ValueError, '待修/未核对'):
            self.render(result['version_id'])

    def test_source_passthrough_insert_cannot_be_enabled_by_imported_foreground(self):
        body = self.body(imports=self.imports(), reviews=self.reviews(), operations=[{'role': 'foreground', 'tool': 'brush', 'radius': 3, 'points': [[.4, .4]], 'first_frame': 3, 'last_frame': 3}, {'role': 'support', 'tool': 'support_from_foreground', 'first_frame': 3, 'last_frame': 3}])
        result = workflow.action(self.pid, body, store.uid())
        value = json.loads(Path(result['asset']['path']).read_text())
        self.assertIn('SOURCE_PASSTHROUGH_FRAME_EDITED', value['frame_reports']['3']['blocking'])
        with self.assertRaises(ValueError):
            self.render(result['version_id'])

    def test_local_patch_preserves_other_frames_and_original_protection_cannot_be_erased(self):
        original = self.save()['version_id']
        p, s = self.project()
        original_value = json.loads(Path(store.asset_by_id(self.pid, original)['path']).read_text())
        body = self.body(version_id=original, operations=[{'role': 'foreground', 'tool': 'erase', 'radius': 2, 'points': [[.49, .12]], 'first_frame': 1, 'last_frame': 1},
                                                        {'role': 'extra_protection', 'tool': 'erase', 'radius': 10, 'points': [[.62, .73]], 'first_frame': 1, 'last_frame': 1}], reviews=self.reviews(first_frame=1, last_frame=1))
        new = workflow.action(self.pid, body, store.uid())['version_id']
        value = json.loads(Path(store.asset_by_id(self.pid, new)['path']).read_text())
        first, second = decode_mask(original_value['layers']['foreground']['path']), decode_mask(value['layers']['foreground']['path'])
        self.assertTrue(np.array_equal(first[1:], second[1:]))
        self.assertTrue((first[0] != second[0]).any())
        output = self.render(new)
        actual, source = self.pixels(output['master']['path']), self.pixels(self.source)
        self.assertTrue(np.array_equal(actual[self.p == 255], source[self.p == 255]))
        self.assertEqual(value['parent_version_id'], original)
        self.assertTrue(Path(original_value['layers']['foreground']['path']).exists())

    def test_wrong_candidate_timeline_or_import_digest_fails_without_registering_or_overwriting(self):
        good = self.save()['version_id']
        for key, value in [('candidate_sha256', 'wrong'), ('first_frame', 2)]:
            imports = self.imports()
            path = Path(imports[0])
            data = json.loads(path.read_text());data['binding'][key] = value;path.write_text(json.dumps(data))
            before = copy.deepcopy(self.project())
            with self.assertRaisesRegex(ValueError, '绑定不一致'):
                workflow.action(self.pid, self.body(imports=imports), store.uid())
            self.assertEqual(self.project(), before)
        imports = self.imports();path = Path(imports[0]);data = json.loads(path.read_text());data['mask_sha256'] = 'wrong';path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            workflow.action(self.pid, self.body(imports=imports), store.uid())
        old = self.body('render_mask_support', version_id=good)
        workflow.attach(self.pid, self.candidate, 'raw_candidate', segment_id=self.sid)
        with self.assertRaisesRegex(ValueError, '已更换'):
            support.action(self.pid, old, store.uid())
        with self.assertRaises(ValueError):
            self.render(good)

    def test_manual_seed_is_only_selected_frames_and_does_not_claim_segmentation_or_approval(self):
        body = self.body(operations=[{'role': 'foreground', 'tool': 'seed_from_removal', 'first_frame': 1, 'last_frame': 1}])
        result = workflow.action(self.pid, body, store.uid())
        value = json.loads(Path(result['asset']['path']).read_text())
        f = decode_mask(value['layers']['foreground']['path'])
        self.assertTrue(np.array_equal(f[0], self.r[0]))
        self.assertFalse(f[1:].any())
        self.assertEqual(value['active_frames'], [1])
        self.assertFalse(value['six_checks_approved'])
        self.assertIn(1, value['blocking_frames'])
        with self.assertRaisesRegex(ValueError, '待修/未核对'):
            self.render(result['version_id'])

    def test_read_only_frame_and_reverting_to_parent_version_do_not_mutate_project(self):
        good = self.save()['version_id']
        before = store.project_path(self.pid).read_bytes()
        result = support.frame_data(*self.project(), 1, self.body(version_id=good))
        self.assertEqual(result['version_id'], good)
        self.assertTrue(np.array_equal(png_array(result['images']['foreground']), self.f[0]))
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        failed_child = workflow.action(self.pid, self.body(version_id=good, reviews=self.reviews(content='missing_content')), store.uid())['version_id']
        self.assertNotEqual(good, failed_child)
        old_result = self.render(good)
        self.assertEqual(old_result['version_id'], good)
        state = support.status(*self.project(), self.body()['candidate_id'], self.body()['candidate_fingerprint'], self.body()['mask_fingerprint'])
        self.assertFalse(any(item['version_id'] == failed_child for item in state['outputs']))

    def test_nonbinary_and_wrong_rate_imports_do_not_replace_saved_version(self):
        good = self.save()['version_id']
        for bad_rate, grey in [('30', True), ('8', False)]:
            path = self.root/f'bad_{bad_rate}.mkv'
            pixels = self.f.copy()
            if grey:
                pixels[0, 0, 0] = 123
            encode_mask(path, pixels, bad_rate)
            imports = self.imports()
            manifest = Path(imports[0]);value = json.loads(manifest.read_text());value.update(mask_path=str(path), mask_sha256=media._hash(path));manifest.write_text(json.dumps(value))
            before = copy.deepcopy(self.project())
            with self.assertRaisesRegex(ValueError, '二值'):
                workflow.action(self.pid, self.body(imports=imports), store.uid())
            self.assertEqual(self.project(), before)
        self.assertTrue(Path(store.asset_by_id(self.pid, good)['path']).exists())

    def test_async_candidate_change_cannot_register_a_new_support_version(self):
        body = self.body(imports=self.imports(), reviews=self.reviews())
        saver = support.save_version
        def replace_candidate(*args, **kwargs):
            result = saver(*args, **kwargs)
            workflow.attach(self.pid, self.candidate, 'raw_candidate', segment_id=self.sid)
            return result
        with patch.object(support, 'save_version', side_effect=replace_candidate):
            with self.assertRaisesRegex(ValueError, '已更换'):
                workflow.action(self.pid, body, store.uid())
        self.assertFalse(any(asset['kind'] == 'mask_support_version' for asset in self.project()[0]['assets']))

    def test_native_matte_import_follows_ntsc_source_sampling_without_retiming(self):
        source, candidate = self.root/'ntsc_source.mp4', self.root/'native30.mkv'
        encode_rgb(source, np.full((8, 64, 96, 3), 80, np.uint8), '24000/1001', first_pts_ticks=984)
        encode_rgb(candidate, np.full((11, 32, 48, 3), 160, np.uint8), '30')
        native = np.zeros((11, 32, 48), np.uint8)
        for frame in range(11):
            native[frame, 8:24, 8+frame:20+frame] = 255
        matte = self.root/'native30_matte.mkv';encode_mask(matte, native, '30')
        edit = self.root/'ntsc_edit.mkv';encode_mask(edit, np.full((8, 64, 96), 255, np.uint8))
        pid = store.create_project('仅NTSC导入映射')['id'];workflow.attach(pid, source, 'source')
        p = store.get_project(pid);sid = p['segments'][0]['id']
        workflow.attach(pid, edit, 'edit_mask', segment_id=sid);workflow.attach(pid, candidate, 'raw_candidate', segment_id=sid)
        p = store.get_project(pid);s = p['segments'][0];ctx = mask_review.context(p, s);diagnostic = mask_review.diagnostic_candidate(p, s, s['raw_candidate']['id'])
        body = {'action': 'save_mask_support', 'segment_id': sid, 'candidate_id': diagnostic['id'], 'candidate_fingerprint': diagnostic['fingerprint'], 'mask_fingerprint': ctx['fingerprint']}
        state = support.status(p, s, diagnostic['id'], diagnostic['fingerprint'], ctx['fingerprint'])
        manifest = self.root/'native_ntsc_import.json';manifest.write_text(json.dumps({'schema': 'BOUND_SUPPORT_MASK_IMPORT_V1', 'binding': state['binding'], 'role': 'foreground', 'timeline': 'candidate', 'mask_path': str(matte), 'mask_sha256': media._hash(matte)}))
        result = workflow.action(pid, {**body, 'imports': [str(manifest)]}, store.uid())
        value = json.loads(Path(result['asset']['path']).read_text());actual = decode_mask(value['layers']['foreground']['path'])
        expected = np.stack([np.array(Image.fromarray(native[diagnostic['_mapping'][frame]['candidate_frame']-1]).resize((96, 64), Image.Resampling.NEAREST)) for frame in range(ctx['first_frame'], ctx['last_frame']+1)])
        self.assertTrue(np.array_equal(actual, expected))
        self.assertEqual(media.probe(value['layers']['foreground']['path'])['fps_rational'], '24000/1001')

    def test_changed_layer_or_output_is_not_presented_as_current(self):
        version = self.save()['version_id'];result = self.render(version)
        Path(result['master']['path']).write_bytes(b'corrupted temporary master')
        p, s = self.project();body = self.body()
        state = support.status(p, s, body['candidate_id'], body['candidate_fingerprint'], body['mask_fingerprint'])
        self.assertEqual(state['outputs'], [])
        value = json.loads(Path(store.asset_by_id(self.pid, version)['path']).read_text())
        Path(value['layers']['foreground']['path']).write_bytes(b'corrupted temporary layer')
        state = support.status(*self.project(), body['candidate_id'], body['candidate_fingerprint'], body['mask_fingerprint'])
        self.assertFalse(state['versions'][0]['current'])

    def test_bound_support_api_is_read_only_and_local_actions_keep_segment_scope(self):
        version = self.save()['version_id']
        body = self.body(version_id=version)
        before = store.project_path(self.pid).read_bytes()
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True);thread.start()
        try:
            root = f'http://127.0.0.1:{httpd.server_port}/api/projects/{self.pid}'
            query = urlencode({key: value for key, value in body.items() if key in ('candidate_id', 'candidate_fingerprint', 'mask_fingerprint', 'version_id')})
            with urlopen(root+f'/mask-review/{self.sid}/support-status?'+query) as response:
                state = json.load(response)
            self.assertTrue(state['versions'][0]['current'])
            with urlopen(root+f'/mask-review/{self.sid}/support-frame?frame=1&'+query) as response:
                frame = json.load(response)
            self.assertEqual(frame['version_id'], version)
            self.assertTrue(np.array_equal(png_array(frame['images']['foreground']), self.f[0]))
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(root+f'/mask-review/{self.sid}/support-status?'+query, headers={'Origin': 'https://outside.example', 'Sec-Fetch-Site': 'cross-site'}))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            self.assertEqual(store.project_path(self.pid).read_bytes(), before)
            request = self.body('render_mask_support', version_id=version)
            with self.assertRaises(HTTPError) as retired:
                urlopen(Request(root+'/actions', data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'}, method='POST'))
            self.assertEqual(retired.exception.code, 400)
            with retired.exception as rejected:
                self.assertIn('已停用生成画面的二次处理', json.load(rejected)['error'])
            self.assertEqual(store.project_path(self.pid).read_bytes(), before)
            request = self.body('save_mask_support', version_id=version)
            with urlopen(Request(root+'/actions', data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'}, method='POST')) as response:
                self.assertEqual(response.status, 202)
            args, scope = store.start_job.call_args
            self.assertEqual(args[1], 'save_mask_support')
            self.assertEqual(scope['segment_id'], self.sid)
            self.assertEqual(scope['input_material_fingerprint'], body['mask_fingerprint'])
        finally:
            httpd.shutdown();httpd.server_close();thread.join(timeout=2)

    def test_modified_frames_lose_inherited_local_reviews_but_other_frames_keep_them(self):
        parent = self.save()['version_id']
        result = workflow.action(self.pid, self.body(version_id=parent, operations=[{'role': 'foreground', 'tool': 'erase', 'radius': 2, 'points': [[.49, .12]], 'first_frame': 1, 'last_frame': 1}]), store.uid())
        value = json.loads(Path(result['asset']['path']).read_text())
        self.assertEqual(value['frame_reviews']['1'], {})
        self.assertEqual(value['frame_reviews']['2']['content'], 'ok')
        self.assertIn('CONTENT_REVIEW_REQUIRED', value['frame_reports']['1']['blocking'])
        self.assertIn('ALL_FOREGROUND_REVIEW_REQUIRED', value['frame_reports']['1']['blocking'])
        self.assertEqual(value['frame_reports']['2']['blocking'], [])
        with self.assertRaisesRegex(ValueError, '待修/未核对'):
            self.render(result['version_id'])

    def rewrite_catalogued_version(self, version_id):
        asset = store.asset_by_id(self.pid, version_id)
        value = json.loads(Path(asset['path']).read_text())
        value['frame_reviews']['1']['content'] = 'missing_content'
        value['frame_reports']['1']['blocking'] = ['GENERATED_CONTENT_MISSING']
        value['blocking_frames'] = sorted(set(value['blocking_frames']) | {1})
        Path(asset['path']).write_text(json.dumps(value))
        p, _ = self.project()
        registered = next(item for item in p['assets'] if item['id'] == version_id)
        registered.update(sha256=media._hash(asset['path']), bytes=Path(asset['path']).stat().st_size)
        store.put_project(p)

    def test_parent_layers_changed_after_initial_validation_before_read_are_rejected(self):
        parent = self.save()['version_id']
        manifest = json.loads(Path(store.asset_by_id(self.pid, parent)['path']).read_text())
        paths = [Path(manifest['layers'][role]['path']) for role in ('foreground', 'support')]
        originals = [path.read_bytes() for path in paths]
        replacement = []
        for index, path in enumerate(paths):
            pixels = decode_mask(path).copy();pixels[0, 1, 1] = 255
            other = self.root/f'changed_parent_{index}.mkv';encode_mask(other, pixels, '30');replacement.append(other.read_bytes())
        streams = support._streams
        changed = False
        def mutate_before_read(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                for path, raw in zip(paths, replacement):
                    path.write_bytes(raw)
            return streams(*args, **kwargs)
        before = store.project_path(self.pid).read_bytes()
        try:
            with patch.object(support, '_streams', side_effect=mutate_before_read):
                with self.assertRaisesRegex(ValueError, '图层已改变'):
                    workflow.action(self.pid, self.body(version_id=parent, reviews=self.reviews()), store.uid())
            self.assertTrue(changed)
            self.assertEqual(store.project_path(self.pid).read_bytes(), before)
        finally:
            for path, raw in zip(paths, originals):
                path.write_bytes(raw)
        p, s = self.project();body = self.body()
        state = support.status(p, s, body['candidate_id'], body['candidate_fingerprint'], body['mask_fingerprint'])
        self.assertTrue(state['versions'][0]['current'])

    def test_save_registration_rejects_updated_catalogue_under_same_parent_version_id(self):
        parent = self.save()['version_id']
        saver = support.save_version
        after_mutation = []
        def mutate_after_computation(*args, **kwargs):
            result = saver(*args, **kwargs)
            self.rewrite_catalogued_version(parent)
            after_mutation.append(store.project_path(self.pid).read_bytes())
            return result
        with patch.object(support, 'save_version', side_effect=mutate_after_computation):
            with self.assertRaisesRegex(ValueError, '父版本或图层登记信息已改变'):
                workflow.action(self.pid, self.body(version_id=parent, reviews=self.reviews()), store.uid())
        self.assertEqual(store.project_path(self.pid).read_bytes(), after_mutation[0])
        self.assertEqual(sum(a['kind'] == 'mask_support_version' for a in self.project()[0]['assets']), 1)

    def test_save_registration_rechecks_import_manifest_and_mask_bytes(self):
        parent = self.save()['version_id']
        saver = support.save_version
        for target in ('manifest', 'mask'):
            with self.subTest(target=target):
                imports = self.imports()
                manifest = Path(imports[0])
                value = json.loads(manifest.read_text())
                own_mask = self.root/f'own_{target}.mkv'
                own_mask.write_bytes(Path(value['mask_path']).read_bytes())
                value['mask_path'] = str(own_mask);value['mask_sha256'] = media._hash(own_mask);manifest.write_text(json.dumps(value))
                before = store.project_path(self.pid).read_bytes()
                def mutate_import_after_computation(*args, **kwargs):
                    result = saver(*args, **kwargs)
                    if target == 'manifest':
                        manifest.write_text(manifest.read_text()+'\n')
                    else:
                        pixels = self.f.copy();pixels[0, 1, 1] = 255
                        other = self.root/'changed_import.mkv';other.unlink(missing_ok=True);encode_mask(other, pixels, '30');own_mask.write_bytes(other.read_bytes())
                    return result
                with patch.object(support, 'save_version', side_effect=mutate_import_after_computation):
                    with self.assertRaisesRegex(ValueError, '导入期间图层或绑定清单已改变'):
                        workflow.action(self.pid, self.body(version_id=parent, imports=imports, reviews=self.reviews()), store.uid())
                self.assertEqual(store.project_path(self.pid).read_bytes(), before)

    def test_render_registration_rejects_changed_input_metadata_and_same_id_version(self):
        parent = self.save()['version_id']
        renderer = support.render
        for target in ('candidate_metadata', 'version'):
            with self.subTest(target=target):
                # Start every render from a valid reviewed parent. The preceding
                # subtest's input metadata is a new, stable initial snapshot.
                if target == 'version':
                    parent = self.save()['version_id']
                after_mutation = []
                def mutate_after_readback(*args, **kwargs):
                    result = renderer(*args, **kwargs)
                    if target == 'version':
                        self.rewrite_catalogued_version(parent)
                    else:
                        p, s = self.project();aid = s['raw_candidate']['id']
                        s['raw_candidate']['meta']['race_label'] = 'changed after verification'
                        next(a for a in p['assets'] if a['id'] == aid)['meta']['race_label'] = 'changed after verification'
                        store.put_project(p)
                    after_mutation.append(store.project_path(self.pid).read_bytes())
                    return result
                with patch.object(support, 'render', side_effect=mutate_after_readback):
                    with self.assertRaisesRegex(ValueError, '核验后'):
                        self.render(parent)
                self.assertEqual(store.project_path(self.pid).read_bytes(), after_mutation[0])
                self.assertFalse(any(a['kind'] in ('mask_support_master', 'mask_support_preview', 'mask_support_report') for a in self.project()[0]['assets']))

    def test_changed_valid_master_or_preview_after_readback_is_never_registered(self):
        parent = self.save()['version_id']
        good = self.render(parent)
        old_hashes = {key: media._hash(good[key]['path']) for key in ('master', 'asset')}
        renderer = support.render
        for target in ('master', 'preview'):
            with self.subTest(target=target):
                before = store.project_path(self.pid).read_bytes()
                def mutate_verified_output(*args, **kwargs):
                    result = renderer(*args, **kwargs)
                    pixels = self.pixels(result['master']).copy()
                    pixels[0, 43, 55, 0] ^= 63  # A base-protected source pixel.
                    alternate = self.root/('changed_output.mkv' if target == 'master' else 'changed_output.mp4')
                    alternate.unlink(missing_ok=True);encode_rgb(alternate, pixels, '30')
                    Path(result[target]).write_bytes(alternate.read_bytes())
                    return result
                with patch.object(support, 'render', side_effect=mutate_verified_output):
                    with self.assertRaisesRegex(ValueError, '输出已改变'):
                        self.render(parent)
                self.assertEqual(store.project_path(self.pid).read_bytes(), before)
                self.assertEqual(old_hashes, {key: media._hash(good[key]['path']) for key in old_hashes})
        report = json.loads(Path(good['report']['path']).read_text())
        self.assertEqual(report['verified_master_sha256'], good['master']['sha256'])
        self.assertEqual(report['verified_preview_sha256'], good['asset']['sha256'])

    def test_atomic_registration_rechecks_after_report_creation_without_partial_assets(self):
        parent = self.save()['version_id']
        writer = support._write_verified_json
        before = store.project_path(self.pid).read_bytes()
        def mutate_master_during_report(path, value):
            verified = writer(path, value)
            if str(path).endswith('_support_composite_report.json'):
                pixels = self.pixels(value['master']['path']).copy();pixels[0, 43, 55, 1] ^= 31
                alternate = self.root/'late_changed_master.mkv';encode_rgb(alternate, pixels, '30')
                Path(value['master']['path']).write_bytes(alternate.read_bytes())
            return verified
        with patch.object(support, '_write_verified_json', side_effect=mutate_master_during_report):
            with self.assertRaisesRegex(ValueError, '输出已改变'):
                self.render(parent)
        self.assertEqual(store.project_path(self.pid).read_bytes(), before)

    def test_save_final_commit_rechecks_parent_after_version_manifest_creation(self):
        parent = self.save()['version_id']
        writer = support._write_verified_json
        after_mutation = []
        def mutate_parent_during_manifest(path, value):
            verified = writer(path, value)
            if str(path).endswith('_support_version.json'):
                self.rewrite_catalogued_version(parent)
                after_mutation.append(store.project_path(self.pid).read_bytes())
            return verified
        with patch.object(support, '_write_verified_json', side_effect=mutate_parent_during_manifest):
            with self.assertRaisesRegex(ValueError, '父版本或图层登记信息已改变'):
                workflow.action(self.pid, self.body(version_id=parent, reviews=self.reviews()), store.uid())
        self.assertEqual(store.project_path(self.pid).read_bytes(), after_mutation[0])
        self.assertEqual(sum(a['kind'] == 'mask_support_version' for a in self.project()[0]['assets']), 1)

    def test_report_does_not_become_current_for_a_rewritten_same_id_version(self):
        parent = self.save()['version_id']
        good = self.render(parent)
        report = json.loads(Path(good['report']['path']).read_text())
        old_version_sha = store.asset_by_id(self.pid, parent)['sha256']
        self.assertEqual(report['verified_version_sha256'], old_version_sha)
        self.rewrite_catalogued_version(parent)
        body = self.body()
        state = support.status(*self.project(), body['candidate_id'], body['candidate_fingerprint'], body['mask_fingerprint'])
        self.assertTrue(state['versions'][0]['current'])
        self.assertEqual(state['outputs'], [])
        self.assertEqual(report['verified_master_sha256'], media._hash(good['master']['path']))


if __name__ == '__main__':
    unittest.main()
