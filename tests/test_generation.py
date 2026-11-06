"""Offline contracts for explicitly confirmed, version-bound generation batches.

The local source, masks and references are real media. Threads are queued rather
than started, and the real provider's HTTP/upload boundaries are replaced here.
"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlparse
import zipfile

from PIL import Image

from backend import generation, media, provider, replacement, replacement_media, replacement_workflow as rw, store, workflow


class LocalResponse(BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class MockCloud:
    """Keep the real WanClient receipts, locking and idempotency implementation."""

    def __init__(self, root):
        self.root = root
        self.clients = []
        self.posts = []
        self.polls = []
        self.uploads = []
        self.downloads = []
        self.preflights = []
        self.remote = {}
        self.uploaded = {}
        self.post_errors = {}
        self.download_errors = set()
        self.download_hook = None
        self.default_status = "SUCCEEDED"
        self.return_url = True
        self.usage = {"input_video_duration": 2, "output_video_duration": 2, "fps": 30, "SR": "480P", "ratio": "4:3"}
        cloud = self

        class OfflineWan(provider.WanClient):
            def __init__(self, config=None, state_dir=None):
                super().__init__(config, state_dir or cloud.root / "preflight-provider")
                cloud.clients.append(self)
                self.opener = type("LocalOpener", (), {"open": lambda _, request, **kwargs: cloud.open(request)})()

            def preflight(self, *args, **kwargs):
                cloud.preflights.append(args[0])
                return super().preflight(*args, **kwargs)

            def _upload(self, path):
                url = "oss://offline/" + str(len(cloud.uploads))
                cloud.uploads.append(str(path))
                cloud.uploaded[url] = str(path)
                return url

            def _request_json(self, method, url, body=None, extra_headers=None):
                if method == "POST":
                    prompt = body["input"]["prompt"]
                    task_id = "task-" + str(len(cloud.posts)+1)
                    cloud.posts.append({"task_id": task_id, "prompt": prompt, "base": self.base, "body": copy.deepcopy(body)})
                    reference_url = next(item["url"] for item in body["input"]["media"] if item["type"] == "reference_video")
                    cloud.remote[task_id] = {"prompt": prompt, "base": self.base, "source": cloud.uploaded[reference_url]}
                    failure = cloud.post_errors.get(prompt)
                    if failure == "unknown":
                        raise provider.ProviderError("模拟提交响应丢失", category="connection", code="CONNECTION_ERROR", ambiguous=True)
                    if failure == "auth":
                        raise provider.ProviderError("模拟密钥被拒绝", category="authentication", code="InvalidApiKey", http_status=401)
                    if failure == "rejected":
                        raise provider.ProviderError("模拟当前段请求被拒绝", category="provider_http", code="InvalidInput", http_status=400)
                    return {"output": {"task_id": task_id, "task_status": "PENDING"}}
                if method != "GET" or "/tasks/" not in url:
                    raise AssertionError("external request forbidden")
                task_id = url.rsplit("/", 1)[-1]
                cloud.polls.append((task_id, url))
                task = cloud.remote[task_id]
                if not url.startswith(task["base"]+"/tasks/"):
                    raise AssertionError("queried the wrong provider region")
                output = {"task_id": task_id, "task_status": cloud.default_status, "orig_prompt": task["prompt"]}
                if cloud.return_url and cloud.default_status == "SUCCEEDED":
                    output["video_url"] = "https://offline.oss-cn-beijing.aliyuncs.com/"+task_id+".mp4?Signature=private"
                return {"output": output, "usage": copy.deepcopy(cloud.usage)}

        self.client_type = OfflineWan

    def open(self, request):
        task_id = Path(urlparse(request.full_url).path).stem
        if task_id not in self.remote:
            raise AssertionError("external result download forbidden")
        self.downloads.append(task_id)
        if task_id in self.download_errors:
            self.download_errors.remove(task_id)
            raise URLError("offline download interrupted")
        if self.download_hook:
            hook, self.download_hook = self.download_hook, None
            hook(task_id)
        return LocalResponse(Path(self.remote[task_id]["source"]).read_bytes())


class GenerationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.materials = tempfile.TemporaryDirectory()
        cls.material_root = Path(cls.materials.name)
        cls.source = cls.material_root / "source30.mp4"
        cls.mask = cls.material_root / "mask2.mkv"
        cls.target = cls.material_root / "person.png"
        media._run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "color=c=blue:s=640x480:r=30:d=4",
                    "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", cls.source])
        media._run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "color=c=black:s=640x480:r=30:d=2",
                    "-vf", "drawbox=x=0:y=0:w=320:h=480:color=white:t=fill,format=gray", "-c:v", "ffv1", "-threads", "2", cls.mask])
        Image.new("RGB", (240, 320), "orange").save(cls.target)
        cls.reference = cls.material_root / "reference2.mp4"
        replacement_media.prepare_source_clip(cls.source, cls.reference, 0, 2)
        cls.sample_source = cls.material_root / 'sample1080_23976.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=c=blue:s=1920x1080:r=24000/1001',
                    '-frames:v', '767', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', '-pix_fmt', 'yuv420p', cls.sample_source])
        cls.audio_source = cls.material_root / 'audio_30.mov'
        cls.audio_sample_source = cls.material_root / 'audio_23976.mov'
        for path, rate, duration in [(cls.audio_source, '30', '4'), (cls.audio_sample_source, '24000/1001', '10.427083333333')]:
            media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', f'color=blue:s=640x480:r={rate}:d={duration}',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12', '-map', '0:v:0', '-map', '1:a:0',
                '-vf', 'settb=1/24000,setpts=PTS+984', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2',
                '-enc_time_base', '1/24000', '-fps_mode', 'passthrough', '-video_track_timescale', '24000', '-c:a', 'pcm_s32le', path])
        cls.short_candidate = cls.material_root / 'candidate312.mp4'
        media._run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=red:s=640x480:r=30',
                    '-frames:v', '312', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', cls.short_candidate])

    @classmethod
    def tearDownClass(cls):
        cls.materials.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.cloud = MockCloud(self.data)
        self.api_config = {"api_key": "offline-test-key", "base_url": "https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1"}
        self.jobs = []
        self.worker_calls = []
        def configuration(private=False):
            return dict(self.api_config) if private else {"configured": bool(self.api_config.get("api_key")), "base_url": self.api_config["base_url"]}
        def queue_job(pid, action, function, **context):
            jid = store.uid()
            store.save_json(store.job_path(jid), {"id": jid, "project_id": pid, "action": action, "status": "QUEUED", "created_at": store.now()})
            if action != "render_mask_preview":
                self.jobs.append((jid, function))
            return {"job_id": jid, "status": "QUEUED"}
        self.patches = [
            patch.object(store, "DATA", self.data), patch.object(workflow, "DATA", self.data),
            patch.object(workflow, "CONFIG", self.data / "private-config.json"), patch.object(workflow, "config", configuration),
            patch.dict(os.environ, {"DASHSCOPE_API_KEY": "", "BAILIAN_API_KEY": ""}),
            patch.object(generation, "WanClient", self.cloud.client_type), patch.object(store, "start_job", queue_job),
            patch.object(generation, "start_worker", side_effect=lambda pid, run_id, **kwargs: self.worker_calls.append((pid, run_id, kwargs))),
            # Deep binary-mask validation has separate media tests. Keep its gate
            # active here and inject failures explicitly in the gate tests below.
            patch.object(replacement_media, "validate_mask", return_value={"ready": True, "issues": []}),
            patch.object(provider.WanClient, "_request_json", side_effect=AssertionError("real cloud request forbidden")),
            patch.object(provider.WanClient, "_upload", side_effect=AssertionError("real upload forbidden")),
        ]
        self.started = [item.start() for item in self.patches]
        self.mask_check = self.started[-3]
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)
        project = store.create_project("离线生成批次")
        self.pid = project["id"]
        workflow.attach(self.pid, self.source, "source")
        project = store.get_project(self.pid)
        store.update_project(self.pid, {"expected_revision": project["revision"],
            "segments": [{"id": "A", "name": "甲段", "start": 0, "end": 2}, {"id": "B", "name": "乙段", "start": 2, "end": 4}],
            "roles": [{"id": "target", "name": "目标角色", "description": "保留角色外观", "images": []}],
            "source_actors": [{"id": "actor", "label": "人物一", "source_description": "画面左侧人物", "target_role_id": "target", "scope": "head",
                "observations": [{"segment_id": sid, "time": stamp, "box": [.1, .1, .4, .7], "visibility": "visible", "confirmed": True} for sid, stamp in [("A", 0), ("B", 2)]]}],
            "prompts": {"A": "手工甲段提示词：只替换人物头部。", "B": "手工乙段提示词：保持机位与原片节奏。"}})
        p = store.get_project(self.pid)
        store.update_project(self.pid, {"settings": {**p["settings"], "resolution": "480P"}, "expected_revision": p["revision"]})
        workflow.attach(self.pid, self.target, "reference", role="target")
        for sid in ("A", "B"):
            workflow.attach(self.pid, self.mask, "edit_mask", segment_id=sid)
        self.seed_preparation()

    def seed_preparation(self):
        references = {}
        for sid in ("A", "B"):
            dest = self.data / (sid+"_reference.mp4")
            shutil.copy2(self.reference, dest)
            references[sid] = store.add_asset(self.pid, dest, "source_reference", meta=media.probe(dest))
        project = store.get_project(self.pid)
        for segment in project["segments"]:
            segment["reference"] = references[segment["id"]]
        project["preparation"] = {"fingerprint": replacement.fingerprint(project),
                                  "references": {sid: {"id": asset["id"], "sha256": asset["sha256"]} for sid, asset in references.items()}}
        store.put_project(project)

    def run_jobs(self):
        while self.jobs:
            jid, function = self.jobs.pop(0)
            store.update_job(jid, status="RUNNING")
            try:
                result = function(jid)
                store.update_job(jid, status="SUCCEEDED", result=result)
            except Exception as error:
                store.update_job(jid, status="FAILED", message=str(error))
                raise

    def prepare(self, mode="api", **body):
        generation.request_prepare(self.pid, {"mode": mode, **body})
        self.run_jobs()
        return generation.state(self.pid)

    def confirmation(self, state=None):
        quote = (state or generation.state(self.pid))["quote"]
        return {"quote_id": quote["id"], "confirm_upload": True, "confirm_generation": True,
                "approved_budget_yuan": quote["reserved_yuan"], "material_confirmed": True,
                "purpose": quote["purpose"], "segment_ids": quote["segment_ids"], "scope_fingerprint": quote["scope_fingerprint"],
                "include_source_audio": bool(quote.get('input_policy', {}).get('include_source_audio'))}

    def confirmed_run(self):
        state = self.prepare()
        result = generation.confirm(self.pid, self.confirmation(state))
        self.assertIsInstance(result["current_run"], dict)
        return result["current_run"]["id"]

    def drive(self, run_id, allow_submit=True):
        return generation.drive_once(self.pid, run_id, allow_submit=allow_submit)

    def prompts(self):
        return store.get_project(self.pid)["prompts"]

    def sample_project(self):
        asset = store.add_asset(self.pid, self.sample_source, 'source', meta=media.probe(self.sample_source))
        project = store.get_project(self.pid)
        project.update(source=asset, source_meta={**asset['meta'], 'duration': 767 * 1001 / 24000}, timeline_duration=767 * 1001 / 24000)
        project['settings']['resolution'] = '720P'
        project['segments'] = [{'id': sid, 'name': sid + '段', 'start': first * 1001 / 24000, 'end': stop * 1001 / 24000,
                                **({'edit_mask': project['segments'][0]['edit_mask']} if sid == 'A' else {})}
                               for sid, first, stop in [('A', 0, 250), ('B', 250, 581), ('C', 581, 767)]]
        project['source_actors'][0]['observations'] = [project['source_actors'][0]['observations'][0]]
        project['prompts'] = {'A': '仅替换主唱与同一人的镜中倒影。' + '保留原片时间轴。' * 100}
        store.put_project(project)
        return project

    def audio_project(self, fractional=False):
        source = self.audio_sample_source if fractional else self.audio_source
        asset = store.add_asset(self.pid, source, 'source', meta=media.probe(source))
        project = store.get_project(self.pid)
        project.update(source=asset, source_meta={**asset['meta'], 'duration': asset['meta']['video_duration_seconds']})
        if fractional:
            project['segments'] = [{**project['segments'][0], 'start': 0, 'end': 250 * 1001 / 24000}]
            project['source_actors'][0]['observations'] = project['source_actors'][0]['observations'][:1]
        store.put_project(project)
        return project

    def test_audio_opt_in_has_a_new_manifest_scope_and_requires_new_material_confirmation(self):
        self.audio_project()
        prior = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        old_body = self.confirmation(prior)
        current = self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=True)
        self.assertEqual(prior['quote']['fingerprint'], current['quote']['fingerprint'])
        self.assertNotEqual(prior['quote']['scope_fingerprint'], current['quote']['scope_fingerprint'])
        self.assertNotEqual(prior['quote']['materials_fingerprint'], current['quote']['materials_fingerprint'])
        self.assertEqual([m['type'] for m in current['quote']['material_manifest'][0]['media']], ['reference_video', 'reference_image', 'reference_audio'])
        audio = current['quote']['input_contract']['source_audio']['A']
        self.assertEqual(audio['video_first_in_audio_rational'], '0')
        self.assertAlmostEqual(audio['duration_seconds'], 2.0)
        self.assertEqual(audio['source_first_sample'], 1968)
        self.assertFalse(current['quote']['settings']['audio'])
        with self.assertRaises(ValueError):
            generation.confirm(self.pid, old_body)
        body = self.confirmation(current)
        with self.assertRaisesRegex(ValueError, '材料'):
            generation.confirm(self.pid, {**body, 'material_confirmed': False})
        with self.assertRaisesRegex(ValueError, '原声参考'):
            generation.confirm(self.pid, {**body, 'include_source_audio': False})
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_reference_audio_file_change_invalidates_quote_before_upload(self):
        self.audio_project()
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=True)
        Path(state['quote']['input_contract']['source_audio']['A']['path']).write_bytes(b'changed offline file')
        self.assertFalse(generation.state(self.pid)['quote']['input_materials_current'])
        with self.assertRaisesRegex(ValueError, '输入文件发生变化'):
            generation.confirm(self.pid, self.confirmation(state))
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_reference_audio_alignment_version_change_expires_authority_before_upload(self):
        from backend import generation_audio
        self.audio_project()
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=True)
        body = self.confirmation(state)
        with patch.object(generation_audio, 'REFERENCE_METHOD', 'TEST_NEXT_ALIGNMENT_VERSION'):
            self.assertFalse(generation.state(self.pid)['quote']['input_materials_current'])
            with self.assertRaisesRegex(ValueError, '合同版本已变化'):
                generation.confirm(self.pid, body)
        run_id = generation.confirm(self.pid, body)['current_run']['id']
        with patch.object(generation_audio, 'REFERENCE_METHOD', 'TEST_NEXT_ALIGNMENT_VERSION'):
            run, _ = self.drive(run_id)
        self.assertEqual(run['segments'][0]['status'], 'NOT_SUBMITTED')
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_audio_opt_in_on_a_silent_source_fails_locally_without_cloud_or_fake_audio(self):
        with self.assertRaisesRegex(media.MediaError, '没有音轨'):
            self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=True)
        self.assertEqual(generation.state(self.pid)['preparation']['status'], 'FAILED')
        self.assertFalse(generation.state(self.pid)['quote'])
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_old_paid_run_cannot_gain_audio_and_completed_video_only_run_needs_new_quote(self):
        self.audio_project()
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        self.drive(run_id)
        new = self.prepare(purpose='candidate_sample', segment_ids=['A'], include_source_audio=True)
        self.assertNotEqual(new['quote']['id'], state['quote']['id'])
        self.assertFalse(new['quote'].get('confirmed_run'))
        old = generation._read(generation._run_path(self.pid, run_id))
        self.assertFalse(any(m['type'] == 'reference_audio' for m in old['segments'][0]['media']))
        self.assertFalse(generation.state(self.pid)['runs'][0]['segments'][0]['source_audio_provided'])
        self.assertEqual(len(self.cloud.posts), 1)
        new_id = generation.confirm(self.pid, self.confirmation(new))['current_run']['id']
        self.drive(new_id)
        self.assertEqual(len(self.cloud.posts), 2)
        self.assertIn('reference_audio', [m['type'] for m in self.cloud.posts[-1]['body']['input']['media']])

    def test_worker_rejects_audio_appended_to_a_video_only_authorization(self):
        self.audio_project()
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        project = store.get_project(self.pid)
        audio = generation._source_audio_reference(self.pid, project, project['segments'][0])
        generation._mutate_run(self.pid, run_id, lambda run: run['segments'][0]['media'].append({'type': 'reference_audio', 'path': audio['path'], 'sha256': audio['sha256']}))
        run, _ = self.drive(run_id)
        self.assertEqual(run['segments'][0]['status'], 'NOT_SUBMITTED')
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_312_frame_result_is_received_but_blocked_while_original_audio_audition_is_separate(self):
        self.audio_project(fractional=True)
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        self.cloud.default_status = 'PENDING'
        self.drive(run_id)
        self.cloud.remote[self.cloud.posts[0]['task_id']]['source'] = str(self.short_candidate)
        self.cloud.default_status = 'SUCCEEDED'
        self.cloud.usage['output_video_duration'] = 10.43
        run, _ = self.drive(run_id, allow_submit=False)
        self.assertEqual(run['status'], 'COMPLETED')
        self.assertEqual(run['segments'][0]['usage']['output_video_duration'], 10.43)
        inspection = run['segments'][0]['return_inspection']
        self.assertEqual(inspection['actual']['frame_count'], 312)
        self.assertAlmostEqual(inspection['actual']['decoded_duration_seconds'], 10.4)
        self.assertFalse(inspection['timeline_adaptation']['ready'])
        self.assertIn('末端覆盖不足', inspection['timeline_adaptation']['error'])
        candidate = store.get_project(self.pid)['segments'][0]['sample_candidate']
        generation.prepare_sample_audition(self.pid, {'segment_id': 'A'})
        self.run_jobs()
        segment = store.get_project(self.pid)['segments'][0]
        audition = segment['sample_provenance']['audition']
        self.assertEqual(audition['visual_coverage_seconds'], 10.4)
        self.assertEqual(audition['video_first_in_audio_seconds'], .041)
        self.assertTrue(audition['retained_source_samples_exact'])
        self.assertFalse(audition['strict_composite_ready'])
        self.assertFalse(segment.get('generated'))
        self.assertFalse(segment.get('candidate_review'))
        self.assertFalse(segment.get('sample_preview'))
        self.assertEqual(generation.prepare_sample_audition(self.pid, {'segment_id': 'A'})['reused'], True)
        generation._publish(self.pid, run, run['segments'][0], {'path': candidate['path'], 'sha256': candidate['sha256'], 'media': candidate['meta']})
        self.assertEqual(store.get_project(self.pid)['segments'][0]['sample_provenance']['audition']['audio_asset']['id'], audition['audio_asset']['id'])
        self.assertEqual(len(self.cloud.posts), 1)

    def test_first_sample_prepares_only_A_and_quotes_exact_720p_budget_without_cloud(self):
        self.sample_project()
        before = store.get_project(self.pid)
        source_sha = store.digest(before['source']['path'])
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        quote = state['quote']
        self.assertEqual(state['preparation']['status'], 'READY')
        self.assertEqual(quote['segment_ids'], ['A'])
        self.assertEqual(quote['purpose'], 'candidate_sample')
        self.assertEqual(quote['count'], 1)
        self.assertAlmostEqual(quote['input_seconds'], 250 * 1001 / 24000, places=6)
        self.assertEqual(quote['estimate_yuan'], 12.5125)
        self.assertEqual(quote['reserved_yuan'], 18.6)
        self.assertEqual(quote['pricing'][0]['region'], 'cn-beijing')
        self.assertTrue(quote['submission_ready'])
        self.assertEqual(quote['reports'][0]['input_reference']['fps_rational'], '24000/1001')
        current = store.get_project(self.pid)
        self.assertEqual(current['preparation']['timelines']['A']['timeline']['frame_count'], 250)
        self.assertEqual(set(current['preparation']['references']), {'A'})
        self.assertEqual(current['prompts'], before['prompts'])
        self.assertEqual(current['settings']['resolution'], '720P')
        self.assertFalse(any(s.get('reference') for s in current['segments'][1:]))
        self.assertEqual(store.digest(current['source']['path']), source_sha)
        self.assertFalse(state['material_confirmation']['current'], 'A material review cannot approve the whole project')
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])
        self.assertEqual(self.worker_calls, [])

    def test_selected_sample_confirmation_and_worker_cannot_expand_scope(self):
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        body = self.confirmation(state)
        for changes in ({'segment_ids': ['A', 'B']}, {'purpose': 'strict_final'}, {'scope_fingerprint': 'wrong'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '用途|范围|片段'):
                generation.confirm(self.pid, {**body, **changes})
        run_id = generation.confirm(self.pid, body)['current_run']['id']
        run, _ = self.drive(run_id)
        self.assertEqual([s['segment_id'] for s in run['segments']], ['A'])
        self.assertEqual(len(self.cloud.posts), 1)
        self.assertEqual(run['authorization']['segment_ids'], ['A'])
        current = store.get_project(self.pid)
        self.assertTrue(current['segments'][0]['sample_candidate'])
        self.assertFalse(current['segments'][0].get('generated'))
        self.assertFalse(current['segments'][0].get('candidate_validation'))
        self.assertFalse(current['segments'][1].get('sample_candidate'))
        self.assertFalse(current.get('final'))
        generation.confirm(self.pid, body)
        self.drive(run_id, allow_submit=True)
        self.assertEqual(len(self.cloud.posts), 1)

    def test_worker_rechecks_scope_before_any_upload(self):
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        generation._mutate_run(self.pid, run_id, lambda run: run['segment_ids'].append('B'))
        run, _ = self.drive(run_id)
        self.assertEqual(run['segments'][0]['status'], 'NOT_SUBMITTED')
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_sample_unknown_never_resubmits_or_opens_a_new_paid_quote(self):
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        self.cloud.post_errors[self.prompts()['A']] = 'unknown'
        self.assertEqual(self.drive(run_id)[0]['segments'][0]['status'], 'UNKNOWN')
        self.drive(run_id, allow_submit=True)
        self.assertEqual(len(self.cloud.posts), 1)
        same = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        self.assertEqual(len(same['runs']), 1)
        self.assertTrue(same['quote']['confirmed_run'])
        with self.assertRaises(ValueError):
            self.prepare(purpose='candidate_sample', segment_ids=['A'], retry_of=run_id)

    def test_legacy_endpoint_still_allows_local_quote_but_blocks_upload_and_payment(self):
        self.api_config['base_url'] = 'https://dashscope.aliyuncs.com/api/v1'
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        self.assertFalse(state['quote']['submission_ready'])
        self.assertFalse(state['configuration']['ready'])
        self.assertFalse(state['configuration']['authentication_verified'])
        with self.assertRaisesRegex(provider.ProviderError, '业务空间'):
            generation.confirm(self.pid, self.confirmation(state))
        self.assertEqual(self.cloud.posts + self.cloud.uploads + self.worker_calls, [])

    def test_raw_sample_inspection_keeps_source_masks_and_final_acceptance_untouched(self):
        state = self.prepare(purpose='candidate_sample', segment_ids=['A'])
        run_id = generation.confirm(self.pid, self.confirmation(state))['current_run']['id']
        self.drive(run_id)
        before = store.get_project(self.pid)
        source_sha = store.digest(before['source']['path'])
        mask_sha = store.digest(before['segments'][0]['edit_mask']['path'])
        generation.inspect_sample_result(self.pid, {'segment_id': 'A'})
        self.run_jobs()
        current = store.get_project(self.pid)
        segment = current['segments'][0]
        self.assertFalse(segment.get('sample_preview'))
        self.assertFalse(segment.get('sample_preview_report'))
        inspection = segment['sample_provenance']['return_inspection']
        self.assertTrue(inspection['timeline_adaptation']['ready'])
        self.assertEqual(inspection['actual']['fps_rational'], '30/1')
        self.assertEqual(inspection['actual']['frame_count'], 60)
        self.assertFalse(generation.state(self.pid)['sample_states']['A']['final_approved'])
        self.assertFalse(segment.get('generated'))
        self.assertFalse(segment.get('candidate_review'))
        self.assertFalse(segment.get('candidate_validation'))
        self.assertFalse(segment.get('composited'))
        self.assertFalse(current.get('final'))
        self.assertEqual(store.digest(current['source']['path']), source_sha)
        self.assertEqual(store.digest(segment['edit_mask']['path']), mask_sha)
        self.assertEqual(len(self.cloud.posts), 1, 'local inspection never makes a paid request')

    def test_prepare_is_local_reuses_valid_files_and_preserves_manual_prompts(self):
        before = store.get_project(self.pid)
        with patch.object(rw, "action", wraps=rw.action) as local_action:
            state = self.prepare()
        self.assertEqual(state["preparation"]["status"], "READY")
        self.assertEqual(store.get_project(self.pid)["prompts"], before["prompts"])
        self.assertEqual([s["reference"]["id"] for s in store.get_project(self.pid)["segments"]], [s["reference"]["id"] for s in before["segments"]])
        local_action.assert_not_called()
        self.assertGreaterEqual(self.mask_check.call_count, 2)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.worker_calls, [])

    def test_idle_state_exposes_current_material_version_and_compatibility(self):
        state = generation.state(self.pid)
        self.assertEqual(state["preparation"]["status"], "IDLE")
        self.assertFalse(state["material_confirmation"]["current"])
        self.assertEqual(state["material_confirmation"]["fingerprint"], replacement.fingerprint(store.get_project(self.pid)))
        self.assertTrue(state["compatibility"]["ready"])
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])

    def test_missing_prompt_gets_template_without_overwriting_manual_text(self):
        project = store.get_project(self.pid)
        original = project["prompts"]["A"]
        store.update_project(self.pid, {"prompts": {"A": original, "B": "  "}, "expected_revision": project["revision"]})
        state = self.prepare(material_confirmed=True)
        self.assertEqual(state["preparation"]["status"], "READY")
        self.assertEqual(self.prompts()["A"], original)
        self.assertIn("编辑视频1", self.prompts()["B"])
        self.assertNotEqual(self.prompts()["B"].strip(), "")
        self.assertTrue(state["material_confirmation"]["current"])
        self.assertEqual(state["material_confirmation"]["fingerprint"], replacement.fingerprint(store.get_project(self.pid)))
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_no_key_export_contains_exact_segment_files_and_never_submits(self):
        self.api_config["api_key"] = None
        state = self.prepare(mode="export")
        self.assertEqual(state["preparation"]["status"], "EXPORTED")
        self.assertIsNone(state["quote"])
        with zipfile.ZipFile(state["preparation"]["package_asset"]["path"]) as archive:
            self.assertIn("片段_A/原片参考.mp4", archive.namelist())
            self.assertIn("片段_B/原片参考.mp4", archive.namelist())
            self.assertEqual(archive.read("片段_A/完整提示词.txt").decode(), self.prompts()["A"])
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.worker_calls, [])

    def test_no_key_export_can_acknowledge_materials_later_without_rebuilding_package(self):
        self.api_config["api_key"] = None
        with patch.object(rw, "export_package", wraps=rw.export_package) as exporter:
            pending = self.prepare(mode="export")
            self.assertFalse(pending["material_confirmation"]["current"])
            package_id = pending["preparation"]["package_asset"]["id"]
            refs = [segment["reference"]["id"] for segment in store.get_project(self.pid)["segments"]]
            confirmed = self.prepare(mode="export", material_confirmed=True)
            self.assertTrue(confirmed["material_confirmation"]["current"])
            self.assertEqual(confirmed["material_confirmation"]["source"], "export")
            self.assertEqual(confirmed["preparation"]["package_asset"]["id"], package_id)
            self.assertEqual([segment["reference"]["id"] for segment in store.get_project(self.pid)["segments"]], refs)
            self.assertEqual(exporter.call_count, 1)
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_no_key_pending_package_exports_without_claiming_material_confirmation(self):
        self.api_config["api_key"] = None
        project = store.get_project(self.pid)
        project["source_actors"][0]["scope"] = "person"
        project["segments"][0]["edit_mask"] = None
        store.put_project(project)
        state = self.prepare(mode="export", material_confirmed=True)
        self.assertEqual(state["preparation"]["status"], "EXPORTED")
        self.assertTrue(Path(state["preparation"]["package_asset"]["path"]).is_file())
        self.assertFalse(state["preparation"]["plan"]["ready"])
        self.assertFalse(state["material_confirmation"]["current"])
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        self.assertEqual(self.cloud.posts, [])

    def test_confirmed_no_key_materials_reuse_in_api_but_fee_authority_is_still_required(self):
        self.api_config["api_key"] = None
        exported = self.prepare(mode="export", material_confirmed=True)
        self.assertTrue(exported["material_confirmation"]["current"])
        record = copy.deepcopy(store.get_project(self.pid)["material_confirmation"])
        self.api_config["api_key"] = "offline-test-key"
        ready = self.prepare()
        body = self.confirmation(ready)
        body.pop("material_confirmed")
        with self.assertRaises(ValueError):
            generation.confirm(self.pid, {**body, "confirm_generation": False})
        accepted = generation.confirm(self.pid, body)
        self.assertTrue(accepted["material_confirmation"]["current"])
        self.assertEqual(store.get_project(self.pid)["material_confirmation"], record)
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        self.assertEqual(self.cloud.posts, [])

    def test_material_confirmation_never_fills_candidate_or_legacy_six_checks(self):
        run_id = self.confirmed_run()
        project = store.get_project(self.pid)
        self.assertTrue(project["material_confirmation"]["confirmed"])
        self.assertNotIn("checks", project["material_confirmation"])
        self.assertIsNone(project["generic_review"])
        run = generation._read(generation._run_path(self.pid, run_id))
        self.assertNotIn("checks", run["authorization"])
        self.assertTrue(run["authorization"]["material_confirmation"]["current"])
        self.assertTrue(all(not segment.get("candidate_review") for segment in project["segments"]))

    def test_existing_candidate_can_acknowledge_materials_locally_without_paying_for_incompatible_api(self):
        workflow.attach(self.pid, self.reference, "generated", segment_id="A")
        project = store.get_project(self.pid)
        store.update_project(self.pid, {"settings": {**project["settings"], "resolution": "720P"}, "expected_revision": project["revision"]})
        state = self.prepare(material_confirmed=True)
        self.assertEqual(state["preparation"]["status"], "NEEDS_ATTENTION")
        self.assertTrue(state["material_confirmation"]["current"])
        self.assertFalse(state["compatibility"]["ready"])
        self.assertIsNone(state["quote"])
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        rw._assert_plan_review(store.get_project(self.pid))
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_api_prepare_can_acknowledge_materials_without_a_second_quote_or_paid_run(self):
        first = self.prepare()
        self.assertFalse(first["material_confirmation"]["current"])
        second = self.prepare(material_confirmed=True)
        self.assertTrue(second["material_confirmation"]["current"])
        self.assertEqual(second["quote"]["id"], first["quote"]["id"])
        self.assertIsNone(second["current_run"])
        self.assertEqual(self.worker_calls, [])
        self.assertEqual(self.cloud.posts, [])

    def test_current_legacy_material_review_is_reused_without_rewriting_its_checks(self):
        ready = self.prepare()
        project = store.get_project(self.pid)
        legacy = {"approved": True, "fingerprint": replacement.fingerprint(project),
                  "checks": {key: True for key in rw.CHECKS}, "reviewed_at": "2026-11-03T00:00:00+0800"}
        project["generic_review"] = legacy
        store.put_project(project)
        body = self.confirmation(ready)
        body.pop("material_confirmed")
        state = generation.confirm(self.pid, body)
        self.assertTrue(state["material_confirmation"]["current"])
        self.assertTrue(state["material_confirmation"]["legacy"])
        self.assertEqual(store.get_project(self.pid)["generic_review"], legacy)
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])

    def test_material_version_change_requires_new_acknowledgement(self):
        self.api_config["api_key"] = None
        confirmed = self.prepare(mode="export", material_confirmed=True)
        previous_fp = confirmed["material_confirmation"]["fingerprint"]
        project = store.get_project(self.pid)
        store.update_project(self.pid, {"prompts": {**project["prompts"], "A": "新的材料版本"}, "expected_revision": project["revision"]})
        state = generation.state(self.pid)
        self.assertFalse(state["material_confirmation"]["current"])
        self.assertNotEqual(state["material_confirmation"]["fingerprint"], previous_fp)
        self.assertEqual(state["material_confirmation"]["fingerprint"], replacement.fingerprint(store.get_project(self.pid)))

    def test_queued_export_cannot_acknowledge_edits_made_after_the_request(self):
        self.api_config["api_key"] = None
        generation.request_prepare(self.pid, {"mode": "export", "material_confirmed": True})
        project = store.get_project(self.pid)
        store.update_project(self.pid, {"prompts": {**project["prompts"], "A": "排队后的新修改"}, "expected_revision": project["revision"]})
        with self.assertRaisesRegex(ValueError, "开始准备前"):
            self.run_jobs()
        self.assertFalse(generation.state(self.pid)["material_confirmation"]["current"])
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])
        self.assertEqual(self.cloud.posts, [])

    def test_head_only_with_preserved_scene_gets_local_quote_without_masks(self):
        project = store.get_project(self.pid)
        for segment in project["segments"]:
            segment.pop("edit_mask")
        store.put_project(project)

        state = self.prepare()

        self.assertEqual(state["preparation"]["status"], "READY")
        self.assertFalse(state["preparation"]["plan"]["pixel_masks_ready"])
        self.assertEqual(state["quote"]["segment_ids"], ["A", "B"])
        self.assertGreater(state["quote"]["reserved_yuan"], 0)
        for entry in state["quote"]["material_manifest"]:
            self.assertEqual([item["type"] for item in entry["media"]], ["reference_video", "reference_image"])
        self.assertFalse(state["material_confirmation"]["current"])
        self.assertEqual(state["runs"], [])
        self.assertEqual(self.worker_calls, [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.cloud.posts, [])

    def test_existing_head_only_plan_rechecks_old_mask_block_without_preparing(self):
        project = store.get_project(self.pid)
        project["segments"][0].pop("edit_mask")
        old_plan = {"ready": False, "issues": [
            {"code": "MASK_REQUIRED", "message": "需要编辑蒙版", "blocking": True, "segment_id": "A"}]}
        project["replacement_plan"] = old_plan
        store.put_project(project)
        generation._set_preparation(self.pid, status="NEEDS_ATTENTION", plan=old_plan,
                                    fingerprint=replacement.fingerprint(project))

        state = generation.state(self.pid)

        self.assertEqual(state["preparation"]["status"], "IDLE")
        plan = state["preparation"]["plan"]
        self.assertTrue(plan["ready"], plan["issues"])
        self.assertFalse(plan["pixel_masks_ready"])
        self.assertIsNone(state["quote"])
        self.assertFalse(state["material_confirmation"]["current"])
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.cloud.preflights, [])

        prepared = self.prepare()
        self.assertEqual(prepared["preparation"]["status"], "READY")
        self.assertIsNotNone(prepared["quote"])
        self.assertEqual(prepared["runs"], [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.cloud.posts, [])

    def test_person_replacement_without_mask_does_not_receive_quote(self):
        project = store.get_project(self.pid)
        project["source_actors"][0]["scope"] = "person"
        project["segments"][0]["edit_mask"] = None
        store.put_project(project)
        state = self.prepare(material_confirmed=True)
        self.assertEqual(state["preparation"]["status"], "NEEDS_ATTENTION")
        self.assertIsNone(state["quote"])
        self.assertIn("MASK_REQUIRED", [issue["code"] for issue in state["preparation"]["plan"]["issues"]])
        self.assertEqual(self.cloud.preflights, [])
        self.assertEqual(self.cloud.posts, [])
        self.assertFalse(state["material_confirmation"]["current"])

    def test_invalid_supplied_mask_blocks_head_only_quote(self):
        self.mask_check.return_value = {"ready": False, "issues": [{"code": "TIMING", "message": "模拟蒙版帧数不符", "blocking": True}]}

        state = self.prepare()

        self.assertEqual(state["preparation"]["status"], "NEEDS_ATTENTION")
        self.assertIsNone(state["quote"])
        issues = state["preparation"]["plan"]["issues"]
        self.assertIn("EDIT_MASK_TIMING", {issue["code"] for issue in issues if issue["blocking"]})
        self.assertEqual(self.cloud.preflights, [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.cloud.posts, [])

    def test_mask_is_rechecked_at_confirmation(self):
        state = self.prepare()
        self.mask_check.return_value = {"ready": False, "issues": [{"code": "TIMING", "message": "模拟蒙版帧数不符", "blocking": True}]}
        with self.assertRaisesRegex(ValueError, "待办"):
            generation.confirm(self.pid, self.confirmation(state))
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.cloud.posts, [])

    def test_non30_source_does_not_receive_a_quote_or_get_converted(self):
        project = store.get_project(self.pid)
        original_sha = store.digest(project["source"]["path"])
        project["source_meta"].update(fps=24000/1001, fps_rational="24000/1001")
        store.put_project(project)
        state = self.prepare()
        self.assertEqual(state["preparation"]["status"], "NEEDS_ATTENTION")
        self.assertFalse(state["preparation"]["plan"]["wan_compatibility"]["ready"])
        self.assertIsNone(state["quote"])
        self.assertEqual(self.cloud.preflights, [])
        self.assertEqual(store.digest(store.get_project(self.pid)["source"]["path"]), original_sha)

    def test_confirm_requires_one_material_confirmation_upload_generation_and_exact_fee(self):
        state = self.prepare()
        valid = self.confirmation(state)
        variants = [{"confirm_upload": False}, {"confirm_generation": False}, {"material_confirmed": False}, {"material_confirmed": None},
                    {"material_confirmed": 1}, {"material_confirmed": "true"},
                    {"approved_budget_yuan": None}, {"approved_budget_yuan": True},
                    {"approved_budget_yuan": valid["approved_budget_yuan"]-.01},
                    {"approved_budget_yuan": valid["approved_budget_yuan"]+1}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                generation.confirm(self.pid, {**valid, **changes})
        self.assertEqual(generation.state(self.pid)["runs"], [])
        self.assertEqual(self.worker_calls, [])
        self.assertEqual(self.cloud.posts, [])
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])

    def test_confirm_rechecks_current_preflight_fee_before_recording_authority(self):
        ready = self.prepare()
        original = self.cloud.client_type.preflight
        def changed_price(client, *args, **kwargs):
            report = original(client, *args, **kwargs)
            report["estimate"]["reserved_yuan"] += 1
            return report
        with patch.object(self.cloud.client_type, "preflight", changed_price), self.assertRaisesRegex(ValueError, "费用预留已变化"):
            generation.confirm(self.pid, self.confirmation(ready))
        self.assertIsNone(store.get_project(self.pid)["material_confirmation"])
        self.assertIsNone(store.get_project(self.pid)["generic_review"])
        self.assertEqual(generation.state(self.pid)["runs"], [])
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_concurrent_confirmation_of_same_quote_creates_one_run(self):
        state = self.prepare()
        body = self.confirmation(state)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: generation.confirm(self.pid, body), range(2)))
        self.assertEqual(results[0]["current_run"]["id"], results[1]["current_run"]["id"])
        self.assertEqual(len(generation.state(self.pid)["runs"]), 1)
        self.assertEqual(len(self.worker_calls), 1)
        self.assertEqual(self.cloud.posts, [])

    def test_repeat_prepare_does_not_make_new_quotes_or_pay_for_completed_segments(self):
        state = self.prepare()
        quote_id = state["quote"]["id"]
        self.assertEqual(self.prepare()["quote"]["id"], quote_id)
        run_id = generation.confirm(self.pid, self.confirmation(state))["current_run"]["id"]
        self.assertEqual(self.prepare()["current_run"]["id"], run_id)
        run, manual = self.drive(run_id)
        self.assertFalse(manual)
        self.assertEqual(run["status"], "COMPLETED")
        repeated = self.prepare()
        self.assertEqual(repeated["current_run"]["id"], run_id)
        self.assertEqual(len(repeated["runs"]), 1)
        self.assertEqual(len(self.cloud.posts), 2)
        self.drive(run_id)
        self.assertEqual(len(self.cloud.posts), 2)

    def test_returning_to_previous_settings_rebuilds_preparation_instead_of_reusing_quote(self):
        first = self.prepare()
        original_fp = first['quote']['fingerprint']
        old_refs = [s['reference']['id'] for s in store.get_project(self.pid)['segments']]
        for resolution in ('720P', '480P'):
            project = store.get_project(self.pid)
            store.update_project(self.pid, {'settings': {**project['settings'], 'resolution': resolution},
                                           'expected_revision': project['revision']})
        project = store.get_project(self.pid)
        self.assertEqual(replacement.fingerprint(project), original_fp)
        self.assertIsNone(project['preparation'])
        self.assertIsNone(generation.state(self.pid)['quote'])
        with self.assertRaisesRegex(ValueError, '交付规格'):
            generation.confirm(self.pid, self.confirmation(first))
        with patch.object(rw, 'action', wraps=rw.action) as local_prepare:
            recovered = self.prepare()
        self.assertTrue(any(call.args[1]['action'] == 'prepare_replacement' for call in local_prepare.call_args_list))
        self.assertNotEqual(recovered['quote']['id'], first['quote']['id'])
        self.assertEqual(recovered['preparation']['status'], 'READY')
        current = store.get_project(self.pid)
        rw._assert_preparation(current)
        self.assertNotEqual([s['reference']['id'] for s in current['segments']], old_refs)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])
        self.assertEqual(self.worker_calls, [])

    def test_paid_scope_freezes_decoded_delivery_target(self):
        state = self.prepare()
        spec = state['quote']['delivery_spec']
        self.assertTrue(spec['coverage_verified'])
        self.assertEqual((spec['resolution'], spec['ratio'], spec['width'], spec['height']), ('480P', '4:3', 640, 480))
        self.assertEqual((spec['frame_count'], spec['duration_seconds']), (120, 4.0))
        self.assertEqual([row['first_source_frame'] for row in spec['segments']], [1, 61])
        body = self.confirmation(state)
        run_id = generation.confirm(self.pid, body)['current_run']['id']
        run, _ = self.drive(run_id)
        self.assertEqual(run['delivery_spec'], spec)
        returned = run['segments'][0]['return_inspection']
        self.assertEqual(returned['target']['frame_count'], 60)
        self.assertEqual(returned['actual']['frame_count'], 60)
        self.assertFalse(returned['usage_used_for_validation'])
        provenance = store.get_project(self.pid)['segments'][0]['generation_provenance']
        self.assertEqual(provenance['delivery_target'], returned['target'])

    def test_worker_stops_if_confirmed_delivery_tier_was_changed_and_restored(self):
        run_id = self.confirmed_run()
        for resolution in ('720P', '480P'):
            project = store.get_project(self.pid)
            store.update_project(self.pid, {'settings': {**project['settings'], 'resolution': resolution}})
        run, _ = self.drive(run_id)
        self.assertTrue(all(row['status'] == 'NOT_SUBMITTED' for row in run['segments']))
        self.assertEqual(self.cloud.posts + self.cloud.uploads, [])

    def test_missing_or_modified_reference_rebuilds_before_quote_reuse(self):
        for issue in ('missing', 'changed'):
            with self.subTest(reference=issue):
                first = self.prepare()
                project = store.get_project(self.pid)
                old = project['segments'][0]['reference']
                path = Path(old['path'])
                if issue == 'missing':
                    path.unlink()
                else:
                    path.write_bytes(path.read_bytes()+b'changed-local-reference')
                self.assertEqual(replacement.fingerprint(store.get_project(self.pid)), first['quote']['fingerprint'])
                recovered = self.prepare()
                self.assertNotEqual(recovered['quote']['id'], first['quote']['id'])
                current = store.get_project(self.pid)
                rw._assert_preparation(current)
                self.assertNotEqual(current['segments'][0]['reference']['id'], old['id'])
                self.assertTrue(Path(current['segments'][0]['reference']['path']).is_file())
                self.assertEqual(self.cloud.posts, [])
                self.assertEqual(self.cloud.uploads, [])
                self.assertEqual(self.worker_calls, [])

    def test_invalid_preparation_does_not_bypass_completed_run_deduplication(self):
        run_id = self.confirmed_run()
        self.assertEqual(self.drive(run_id)[0]['status'], 'COMPLETED')
        counts = (len(self.cloud.posts), len(self.cloud.uploads), len(self.worker_calls))
        project = store.get_project(self.pid)
        project['preparation'] = None
        store.put_project(project)
        with patch.object(rw, 'action', wraps=rw.action) as local_prepare:
            state = self.prepare()
        self.assertEqual(state['current_run']['id'], run_id)
        self.assertEqual(state['current_run']['status'], 'COMPLETED')
        self.assertEqual(self.jobs, [])
        local_prepare.assert_not_called()
        self.assertEqual((len(self.cloud.posts), len(self.cloud.uploads), len(self.worker_calls)), counts)

    def test_invalid_preparation_does_not_create_new_requests_for_unknown_run(self):
        for prompt in self.prompts().values():
            self.cloud.post_errors[prompt] = 'unknown'
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertEqual({s['status'] for s in run['segments']}, {'UNKNOWN'})
        counts = (len(self.cloud.posts), len(self.cloud.uploads), len(self.worker_calls))
        project = store.get_project(self.pid)
        project['preparation'] = None
        store.put_project(project)
        with patch.object(rw, 'action', wraps=rw.action) as local_prepare:
            state = self.prepare()
        self.assertEqual(state['current_run']['id'], run_id)
        self.assertEqual(self.jobs, [])
        local_prepare.assert_not_called()
        self.assertEqual((len(self.cloud.posts), len(self.cloud.uploads), len(self.worker_calls)), counts)

    def test_timeout_unknown_never_reposts_and_other_success_is_preserved(self):
        self.cloud.post_errors[self.prompts()["A"]] = "unknown"
        run_id = self.confirmed_run()
        run, manual = self.drive(run_id)
        self.assertTrue(manual)
        statuses = {item["segment_id"]: item["status"] for item in run["segments"]}
        self.assertEqual(statuses, {"A": "UNKNOWN", "B": "COMPLETED"})
        posts = len(self.cloud.posts)
        self.drive(run_id, allow_submit=False)
        self.drive(run_id, allow_submit=True)
        self.assertEqual(len(self.cloud.posts), posts)
        self.assertEqual(generation.state(self.pid)["current_run"]["segments"][0]["status"], "UNKNOWN")

    def test_restart_only_queries_existing_requests_and_does_not_submit_queued_ones(self):
        run_id = self.confirmed_run()
        self.worker_calls.clear()
        generation.recover()
        self.assertEqual(self.worker_calls, [(self.pid, run_id, {"allow_submit": False})])
        run, _ = self.drive(run_id, allow_submit=False)
        self.assertEqual({item["status"] for item in run["segments"]}, {"NOT_SUBMITTED"})
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_restart_pending_tasks_finishes_by_query_and_download_only(self):
        self.cloud.default_status = "RUNNING"
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertEqual(run["status"], "RUNNING")
        self.assertEqual(len(self.cloud.posts), 2)
        self.cloud.default_status = "SUCCEEDED"
        self.worker_calls.clear()
        generation.recover()
        self.assertEqual(self.worker_calls[0][2], {"allow_submit": False})
        final, _ = self.drive(run_id, allow_submit=False)
        self.assertEqual(final["status"], "COMPLETED")
        self.assertEqual(len(self.cloud.posts), 2)

    def test_authentication_failure_keeps_configuration_and_stops_remaining_submits(self):
        self.cloud.post_errors[self.prompts()["A"]] = "auth"
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertEqual([item["status"] for item in run["segments"]], ["REJECTED", "NOT_SUBMITTED"])
        self.assertEqual(run["segments"][0]["error_details"]["category"], "authentication")
        self.assertTrue(workflow.config()["configured"])
        self.assertTrue(self.cloud.clients[-1].configured)
        self.assertEqual(len(self.cloud.posts), 1)

    def test_failed_segment_retry_never_regenerates_successful_segment(self):
        self.cloud.post_errors[self.prompts()["B"]] = "rejected"
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertEqual([item["status"] for item in run["segments"]], ["COMPLETED", "REJECTED"])
        first_asset = store.get_project(self.pid)["segments"][0]["generated"]["id"]
        with self.assertRaises(ValueError):
            self.prepare(retry_of=run_id, segment_ids=["A"])
        self.cloud.post_errors.clear()
        state = self.prepare(retry_of=run_id, segment_ids=["B"])
        self.assertEqual(state["quote"]["segment_ids"], ["B"])
        retry_id = generation.confirm(self.pid, self.confirmation(state))["current_run"]["id"]
        self.assertNotEqual(retry_id, run_id)
        self.assertEqual(len(self.drive(retry_id)[0]["segments"]), 1)
        self.assertEqual(store.get_project(self.pid)["segments"][0]["generated"]["id"], first_asset)
        self.assertEqual([p["prompt"] for p in self.cloud.posts].count(self.prompts()["A"]), 1)
        self.assertEqual([p["prompt"] for p in self.cloud.posts].count(self.prompts()["B"]), 2)

    def test_retry_rejects_duplicate_segment_ids_before_preparing_or_charging(self):
        self.cloud.post_errors[self.prompts()["B"]] = "rejected"
        run_id = self.confirmed_run()
        self.drive(run_id)
        before = generation.state(self.pid)
        posts, uploads = len(self.cloud.posts), len(self.cloud.uploads)
        for ids in (["B", "B"], "B", [1]):
            with self.subTest(segment_ids=ids), self.assertRaises(ValueError):
                generation.request_prepare(self.pid, {"mode": "api", "retry_of": run_id, "segment_ids": ids})
        self.assertEqual(self.jobs, [])
        self.assertEqual(generation.state(self.pid)["quote"]["id"], before["quote"]["id"])
        self.assertEqual(len(generation.state(self.pid)["runs"]), len(before["runs"]))
        self.assertEqual((len(self.cloud.posts), len(self.cloud.uploads)), (posts, uploads))

    def test_old_failed_run_cannot_charge_again_after_a_later_successful_retry(self):
        self.cloud.post_errors[self.prompts()["B"]] = "rejected"
        old_id = self.confirmed_run()
        self.drive(old_id)
        self.cloud.post_errors.clear()
        retry_state = self.prepare(retry_of=old_id, segment_ids=["B"])
        retry_confirmation = self.confirmation(retry_state)
        retry_id = generation.confirm(self.pid, retry_confirmation)["current_run"]["id"]
        self.assertEqual(self.drive(retry_id)[0]["status"], "COMPLETED")
        result_id = store.get_project(self.pid)["segments"][1]["generated"]["id"]
        posts, uploads = len(self.cloud.posts), len(self.cloud.uploads)
        state = self.prepare(retry_of=old_id, segment_ids=["B"])
        self.assertEqual(state["current_run"]["id"], retry_id)
        self.assertEqual(len(state["runs"]), 2)
        # Replaying an earlier confirmation is idempotent as well.
        generation.confirm(self.pid, retry_confirmation)
        self.assertEqual(len(generation.state(self.pid)["runs"]), 2)
        self.assertEqual(store.get_project(self.pid)["segments"][1]["generated"]["id"], result_id)
        self.assertEqual((len(self.cloud.posts), len(self.cloud.uploads)), (posts, uploads))

    def test_confirm_rechecks_completed_results_even_for_an_unconfirmed_old_quote(self):
        self.cloud.post_errors[self.prompts()["B"]] = "rejected"
        old_id = self.confirmed_run()
        self.drive(old_id)
        self.cloud.post_errors.clear()
        retry_state = self.prepare(retry_of=old_id, segment_ids=["B"])
        body = self.confirmation(retry_state)
        prep_path = generation._root(self.pid) / "preparation.json"
        quoted_before_completion = copy.deepcopy(generation._read(prep_path))
        retry_id = generation.confirm(self.pid, body)["current_run"]["id"]
        self.drive(retry_id)
        # Restore an unconfirmed quote issued before that result completed,
        # representing another pending confirmation window, not a new approval.
        provider._private_json(prep_path, quoted_before_completion)
        with self.assertRaisesRegex(ValueError, "已有同版本生成结果"):
            generation.confirm(self.pid, body)
        self.assertEqual(len(generation.state(self.pid)["runs"]), 2)
        self.assertEqual(len(self.cloud.posts), 3)

    def test_changed_version_and_changed_region_reject_old_confirmation(self):
        state = self.prepare()
        body = self.confirmation(state)
        self.api_config["base_url"] = "https://workspace.ap-southeast-1.maas.aliyuncs.com/api/v1"
        with self.assertRaisesRegex(ValueError, "地域"):
            generation.confirm(self.pid, body)
        self.api_config["base_url"] = "https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1"
        project = store.get_project(self.pid)
        store.update_project(self.pid, {"prompts": {**project["prompts"], "A": "已修改的手工提示词"}, "expected_revision": project["revision"]})
        with self.assertRaisesRegex(ValueError, "过期"):
            generation.confirm(self.pid, body)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_edit_during_download_archives_result_without_attaching_or_overwriting(self):
        run_id = self.confirmed_run()
        def edited(_):
            project = store.get_project(self.pid)
            store.update_project(self.pid, {"prompts": {**project["prompts"], "A": "处理期间的新版本"}, "expected_revision": project["revision"]})
        self.cloud.download_hook = edited
        run, _ = self.drive(run_id)
        self.assertEqual(run["segments"][0]["status"], "COMPLETED")
        self.assertFalse(run["segments"][0]["attached"])
        self.assertTrue(Path(run["segments"][0]["asset"]["path"]).exists())
        project = store.get_project(self.pid)
        self.assertIsNone(project["segments"][0].get("generated"))
        self.assertIsNone(project["segments"][1].get("generated"))
        self.assertEqual(len(self.cloud.posts), 1)
        self.assertIsNone(generation.state(self.pid)["current_run"])

    def test_task_for_A_cannot_be_adopted_as_B(self):
        for prompt in self.prompts().values():
            self.cloud.post_errors[prompt] = "unknown"
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertEqual([item["status"] for item in run["segments"]], ["UNKNOWN", "UNKNOWN"])
        task_a = next(item["task_id"] for item in self.cloud.posts if item["prompt"] == self.prompts()["A"])
        with self.assertRaises(provider.ProviderError) as error:
            generation.adopt(self.pid, {"run_id": run_id, "segment_id": "B", "task_id": task_a})
        self.assertEqual(error.exception.code, "TASK_PROMPT_MISMATCH")
        self.assertIsNone(generation.state(self.pid)["current_run"]["segments"][1]["task_id"])
        self.assertIsNone(store.get_project(self.pid)["segments"][1].get("generated"))
        self.assertEqual(len(self.cloud.posts), 2)

    def test_adoption_refuses_same_prompt_ambiguity_before_querying_any_task(self):
        run_id = "a" * 32
        run = {"id": run_id, "project_id": self.pid, "base_url": self.api_config["base_url"],
               "segments": [{"segment_id": sid, "request_key": run_id+":"+sid, "prompt": "相同手工提示词",
                             "status": "UNKNOWN", "generation_post_attempted": True} for sid in ("A", "B")]}
        path = generation._run_path(self.pid, run_id)
        provider._private_json(path, run)
        with self.assertRaisesRegex(ValueError, "相同提示词"):
            generation.adopt(self.pid, {"run_id": run_id, "segment_id": "B", "task_id": "task-for-A"})
        self.assertEqual(generation._read(path), run)
        self.assertEqual(self.cloud.polls, [])
        self.assertEqual(self.cloud.posts, [])

    def test_adoption_refuses_task_already_associated_in_another_project(self):
        run_id, other_id = "b" * 32, "c" * 32
        other_pid = store.create_project("另一个独立测试项目")["id"]
        run = {"id": run_id, "project_id": self.pid, "base_url": self.api_config["base_url"],
               "segments": [{"segment_id": "A", "request_key": run_id+":A", "prompt": "当前段唯一提示词",
                             "status": "UNKNOWN", "generation_post_attempted": True}]}
        other = {"id": other_id, "project_id": other_pid, "base_url": self.api_config["base_url"],
                 "segments": [{"segment_id": "B", "request_key": other_id+":B", "prompt": "另一个项目的提示词",
                               "status": "COMPLETED", "task_id": "already-associated-task", "generation_post_attempted": True}]}
        path = generation._run_path(self.pid, run_id)
        provider._private_json(path, run)
        provider._private_json(generation._run_path(other_pid, other_id), other)
        with self.assertRaisesRegex(ValueError, "已经关联其他片段"):
            generation.adopt(self.pid, {"run_id": run_id, "segment_id": "A", "task_id": "already-associated-task"})
        self.assertEqual(generation._read(path), run)
        self.assertEqual(self.cloud.polls, [])
        self.assertEqual(self.cloud.posts, [])

    def test_unambiguous_original_task_can_still_be_adopted_without_reposting(self):
        self.cloud.post_errors[self.prompts()["A"]] = "unknown"
        run_id = self.confirmed_run()
        self.drive(run_id)
        original_id = self.cloud.posts[0]["task_id"]
        def timeout(*args):
            raise TimeoutError("task adoption did not return; check nested file locks")
        prior_handler = signal.signal(signal.SIGALRM, timeout)
        signal.setitimer(signal.ITIMER_REAL, 5)
        try:
            result = generation.adopt(self.pid, {"run_id": run_id, "segment_id": "A", "task_id": original_id})
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prior_handler)
        self.assertEqual(result["current_run"]["segments"][0]["task_id"], original_id)
        self.assertEqual(self.drive(run_id, allow_submit=False)[0]["status"], "COMPLETED")
        self.assertEqual(len(self.cloud.posts), 2)

    def test_download_failure_retries_original_task_without_another_post(self):
        run_id = self.confirmed_run()
        self.cloud.download_errors.add("task-1")
        run, manual = self.drive(run_id)
        self.assertTrue(manual)
        self.assertEqual([item["status"] for item in run["segments"]], ["DOWNLOAD_FAILED", "COMPLETED"])
        posts = len(self.cloud.posts)
        final, _ = self.drive(run_id, allow_submit=False)
        self.assertEqual(final["status"], "COMPLETED")
        self.assertEqual(self.cloud.downloads.count("task-1"), 2)
        self.assertEqual(len(self.cloud.posts), posts)

    def test_publish_clears_old_composite_validation_and_review(self):
        project = store.get_project(self.pid)
        for segment in project["segments"]:
            segment.update(candidate_validation={"ready": True}, candidate_review={"approved": True},
                           composited={"id": "old"}, composite={"id": "old"}, composite_preview={"id": "old"},
                           composite_report={"old": True}, candidate_stale=True)
        store.put_project(project)
        run_id = self.confirmed_run()
        self.assertEqual(self.drive(run_id)[0]["status"], "COMPLETED")
        for segment in store.get_project(self.pid)["segments"]:
            self.assertIsNotNone(segment["generated"])
            self.assertFalse(segment["candidate_stale"])
            for key in ("candidate_validation", "candidate_review", "composited", "composite", "composite_preview", "composite_report"):
                self.assertIsNone(segment[key], key)
            self.assertEqual(segment["generation_provenance"]["run_id"], run_id)

    def test_public_results_follow_current_candidate_and_existing_stale_gate(self):
        run_id = self.confirmed_run()
        self.drive(run_id)
        before = generation.state(self.pid)
        self.assertEqual(before["project_result_count"], 2)
        self.assertTrue(all(item["attached"] for item in before["current_run"]["segments"]))
        project = store.get_project(self.pid)
        workflow.attach(self.pid, project["segments"][0]["generated"]["path"], "generated", segment_id="A")
        replaced = generation.state(self.pid)
        self.assertFalse(replaced["current_run"]["segments"][0]["attached"])
        self.assertEqual(replaced["project_result_count"], 1)
        project = store.get_project(self.pid)
        project["segments"][1]["candidate_stale"] = True
        store.put_project(project)
        stale = generation.state(self.pid)
        self.assertFalse(stale["current_run"]["segments"][1]["attached"])
        self.assertEqual(stale["project_result_count"], 0)
        self.assertEqual(len(self.cloud.posts), 2)

    def test_region_change_before_submission_requires_new_authorization(self):
        base = "https://workspace.ap-southeast-1.maas.aliyuncs.com/api/v1"
        self.api_config["base_url"] = base
        run_id = self.confirmed_run()
        self.api_config["base_url"] = "https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1"
        run, _ = self.drive(run_id)
        self.assertEqual([item["status"] for item in run["segments"]], ["NOT_SUBMITTED", "NOT_SUBMITTED"])
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(self.cloud.uploads, [])

    def test_api_success_without_result_url_is_not_local_completion_or_failure(self):
        self.cloud.return_url = False
        run_id = self.confirmed_run()
        run, _ = self.drive(run_id)
        self.assertIn(run["status"], {"RUNNING", "ATTENTION"})
        self.assertEqual([item["status"] for item in run["segments"]], ["SUCCEEDED", "SUCCEEDED"])
        self.assertFalse(any(item["attached"] for item in run["segments"]))
        self.assertFalse(any(item["asset"] for item in run["segments"]))
        self.cloud.return_url = True
        self.assertEqual(self.drive(run_id, allow_submit=False)[0]["status"], "COMPLETED")
        self.assertEqual(len(self.cloud.posts), 2)


if __name__ == "__main__":
    unittest.main()
