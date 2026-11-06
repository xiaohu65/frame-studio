"""Generation recovery contracts. All HTTP/upload responses are local mocks."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from PIL import Image

from backend import provider


PROMPT = "只替换图1人物，保持原片动作与镜头。"
SECRET = "secret-test-key-never-public"
MEDIA = [{"type": "reference_image", "url": "https://example.com/image.png"}]
RESULT_URL = "https://result.oss-cn-beijing.aliyuncs.com/video.mp4?Signature=private-signed-url"


class Response(BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def http_error(status, code, *, message="", xml=False):
    body = (f"<Error><Code>{code}</Code><RequestId>req-123</RequestId><Message>{message}</Message></Error>".encode()
            if xml else json.dumps({"code": code, "request_id": "req-123", "message": message}).encode())
    return HTTPError(RESULT_URL, status, message, {}, BytesIO(body))


class OfflineWan(provider.WanClient):
    """No code path in this client can open a network connection."""

    def __init__(self, state_dir, base="https://offline-workspace.cn-beijing.maas.aliyuncs.com/api/v1"):
        super().__init__({"api_key": SECRET, "base_url": base}, state_dir)
        self.calls = []
        self.uploads = []
        self.handler = None
        self.opener = Mock()
        self.opener.open.side_effect = AssertionError("external network forbidden")

    def _upload(self, path):
        self.uploads.append(path)
        return "oss://temporary/input.png"

    def _request_json(self, method, url, body=None, extra_headers=None):
        self.calls.append((method, url, body, extra_headers))
        if self.handler:
            return self.handler(method, url, body)
        if method == "POST":
            return {"output": {"task_id": "task-001", "task_status": "PENDING"}, "request_id": "submit-001"}
        return {"output": {"task_id": "task-001", "task_status": "SUCCEEDED", "video_url": RESULT_URL},
                "usage": {"duration": 4, "input_video_duration": 2, "output_video_duration": 2,
                          "fps": 30, "SR": "1080P", "ratio": "16:9"}}


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.client = OfflineWan(self.root / "tasks")
        self.image = self.root / "private-input.png"
        Image.new("RGB", (240, 240), "blue").save(self.image)

    def tearDown(self):
        self.folder.cleanup()

    def submit(self, client=None, key="request-A", **kwargs):
        return (client or self.client).submit(PROMPT, MEDIA, {"duration": 2},
                                             approved_budget_yuan=10, request_key=key, **kwargs)

    def make_unknown(self):
        def fail(*args):
            raise provider.ProviderError("连接中断", category="connection", code="CONNECTION_ERROR", ambiguous=True)
        self.client.handler = fail
        result = self.submit()
        self.assertEqual(result["status"], "UNKNOWN")
        return result

    def assert_public_safe(self, value):
        public = json.dumps(value, ensure_ascii=False)
        for secret in (SECRET, PROMPT, RESULT_URL, str(self.image), "private-signed-url"):
            self.assertNotIn(secret, public)

    def test_http_errors_have_safe_specific_categories(self):
        client = provider.WanClient({"api_key": SECRET, "workspace_id": "offline-workspace"}, self.root / "http")
        client.opener = Mock()
        cases = [(401, "InvalidApiKey", "authentication"), (403, "Forbidden", "permission"),
                 (403, "QuotaExhausted", "quota"), (429, "Throttling", "rate_limit"),
                 (500, "InternalError", "provider_http")]
        for status, code, category in cases:
            with self.subTest(status=status, code=code):
                client.opener.open.side_effect = http_error(status, code, message=SECRET+PROMPT+str(self.image)+RESULT_URL)
                with self.assertRaises(provider.ProviderError) as caught:
                    client._request_json("GET", client.base+"/tasks/task-001")
                error = caught.exception
                self.assertEqual(error.category, category)
                self.assertEqual(error.http_status, status)
                self.assertEqual(error.code, code)
                self.assertEqual(error.request_id, "req-123")
                self.assertFalse(error.ambiguous)
                self.assert_public_safe(error.public_error)
                self.assertTrue(client.configured)

    def test_generation_connection_and_bad_response_are_ambiguous(self):
        client = provider.WanClient({"api_key": SECRET, "workspace_id": "offline-workspace"}, self.root / "http")
        client.opener = Mock()
        for result in (URLError(SECRET+RESULT_URL), Response(b"not-json"), Response(b"[]")):
            with self.subTest(result=type(result).__name__):
                if isinstance(result, Exception):
                    client.opener.open.side_effect = result
                else:
                    client.opener.open.side_effect = None
                    client.opener.open.return_value = result
                with self.assertRaises(provider.ProviderError) as caught:
                    client._request_json("POST", client.base+"/video-synthesis", {"input": {"prompt": PROMPT}})
                self.assertTrue(caught.exception.ambiguous)
                self.assertFalse(caught.exception.retryable)
                self.assert_public_safe(caught.exception.public_error)

    def test_upload_policy_auth_and_connection_reasons_survive_submit(self):
        for error, category in ((http_error(401, "InvalidApiKey"), "authentication"),
                                (URLError(SECRET+RESULT_URL), "connection")):
            with self.subTest(category=category):
                client = provider.WanClient({"api_key": SECRET, "workspace_id": "offline-workspace"}, self.root / category)
                client.opener = Mock()
                client.opener.open.side_effect = error
                result = client.submit(PROMPT, [{"type": "reference_image", "path": str(self.image)}], {"duration": 2},
                                       approved_budget_yuan=10, request_key="upload")
                self.assertEqual(result["status"], "UPLOAD_FAILED")
                self.assertEqual(result["error_details"]["category"], category)
                self.assertFalse(result["generation_post_attempted"])
                self.assertTrue(result["configured"])
                self.assert_public_safe(result)
                self.assertEqual(client.opener.open.call_count, 1)

    def test_oss_upload_quota_reason_is_not_swallowed(self):
        client = provider.WanClient({"api_key": SECRET, "workspace_id": "offline-workspace"}, self.root / "oss")
        policy = {"upload_host": "https://upload.oss-cn-beijing.aliyuncs.com", "upload_dir": "temporary",
                  "oss_access_key_id": "upload-key", "signature": "private", "policy": "private"}
        client.opener = Mock()
        client.opener.open.side_effect = http_error(403, "QuotaExceeded", xml=True)
        with patch.object(client, "_request_json", return_value={"data": policy}):
            result = client.submit(PROMPT, [{"type": "reference_image", "path": str(self.image)}], {"duration": 2},
                                   approved_budget_yuan=10, request_key="upload")
        self.assertEqual(result["status"], "UPLOAD_FAILED")
        self.assertEqual(result["error_details"]["category"], "quota")
        self.assertEqual(result["error_details"]["code"], "QuotaExceeded")
        self.assertFalse(result["generation_post_attempted"])

    def test_unknown_is_durable_and_never_posts_twice(self):
        first = self.make_unknown()
        second = self.submit()
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.get_request("request-A"), first)
        record = json.loads(self.client._record_path("request-A").read_text())
        self.assertEqual(record["prompt_sha256"], hashlib.sha256(PROMPT.encode()).hexdigest())
        self.assertNotIn(PROMPT, json.dumps(record, ensure_ascii=False))
        self.assertEqual(record["base_url"], self.client.base)
        self.assertTrue(first["error_details"]["ambiguous"])
        self.assert_public_safe(first)

    def test_parallel_submit_returns_inflight_receipt_with_only_one_post(self):
        entered, release = threading.Event(), threading.Event()
        def pending(*args):
            self.assertEqual(self.client.get_request("request-A")["status"], "SUBMITTING")
            entered.set()
            self.assertTrue(release.wait(2))
            return {"output": {"task_id": "task-001", "task_status": "PENDING"}}
        self.client.handler = pending
        other = OfflineWan(self.client.state_dir)
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.submit)
            self.assertTrue(entered.wait(2))
            try:
                second = pool.submit(self.submit, other).result(timeout=2)
                self.assertEqual(second["status"], "SUBMITTING")
                self.assertTrue(second["generation_post_attempted"])
                self.assertEqual(other.calls, [])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=2)["status"], "PENDING")
        self.assertEqual(len(self.client.calls), 1)

    def test_restart_retains_interrupted_submitting_without_post(self):
        self.make_unknown()
        path = self.client._record_path("request-A")
        record = json.loads(path.read_text())
        record["status"] = "SUBMITTING"
        provider._private_json(path, record)
        restarted = OfflineWan(self.client.state_dir)
        result = self.submit(restarted)
        self.assertEqual(result["status"], "SUBMITTING")
        self.assertEqual(restarted.calls, [])
        restarted.handler = lambda *args: {"output": {"task_id": "recovered-001", "task_status": "PENDING", "orig_prompt": PROMPT}}
        self.assertEqual(restarted.adopt_task("request-A", "recovered-001")["task_id"], "recovered-001")
        self.assertEqual([c[0] for c in restarted.calls], ["GET"])

    def test_rejected_key_remains_configured_and_no_second_post(self):
        client = provider.WanClient({"api_key": SECRET, "workspace_id": "offline-workspace"}, self.root / "rejected")
        client.opener = Mock()
        client.opener.open.side_effect = http_error(401, "InvalidApiKey", message=SECRET)
        first = self.submit(client)
        self.assertEqual(first["status"], "REJECTED")
        self.assertTrue(first["configured"])
        self.assertEqual(first["error_details"]["category"], "authentication")
        self.assertEqual(self.submit(client), first)
        self.assertEqual(client.opener.open.call_count, 1)

    def test_callbacks_check_before_upload_and_before_post(self):
        events = []
        def progress(stage):
            events.append(stage)
            self.assertFalse(self.client.get_request("request-A")["generation_post_attempted"])
        result = self.submit(progress_callback=progress, before_post=lambda: events.append("CHECK"))
        self.assertEqual(events, ["UPLOADING", "SUBMITTING", "CHECK"])
        self.assertEqual(result["status"], "PENDING")
        self.assertTrue(result["generation_post_attempted"])
        self.submit(progress_callback=lambda stage: self.fail("duplicate callback"))
        self.assertEqual(len(self.client.calls), 1)

    def test_any_callback_failure_prevents_generation_post(self):
        for fail_stage in ("UPLOADING", "SUBMITTING", "before_post"):
            with self.subTest(stage=fail_stage):
                client = OfflineWan(self.root / fail_stage)
                def progress(stage):
                    if stage == fail_stage:
                        raise ValueError(SECRET+PROMPT+str(self.image))
                def before():
                    if fail_stage == "before_post":
                        raise ValueError(SECRET+PROMPT)
                result = client.submit(PROMPT, [{"type": "reference_image", "path": str(self.image)}], {"duration": 2},
                                       approved_budget_yuan=10, request_key="cancel", progress_callback=progress, before_post=before)
                self.assertEqual(result["status"], "CANCELED")
                self.assertFalse(result["generation_post_attempted"])
                self.assertEqual(client.calls, [])
                self.assertEqual(len(client.uploads), 0 if fail_stage == "UPLOADING" else 1)
                self.assert_public_safe(result)

    def test_poll_uses_creation_base_and_keeps_status_on_query_failure(self):
        base = "https://workspace.ap-southeast-1.maas.aliyuncs.com/api/v1"
        original = OfflineWan(self.root / "region", base)
        self.submit(original)
        later = OfflineWan(original.state_dir)
        def failed(*args):
            raise provider.ProviderError("权限不足", category="permission", code="Forbidden", http_status=403)
        later.handler = failed
        failed_result = later.poll("task-001")
        self.assertEqual(failed_result["status"], "PENDING")
        self.assertTrue(failed_result["last_poll_failed"])
        self.assertTrue(failed_result["configured"])
        self.assertEqual(later.calls[0][1], base+"/tasks/task-001")
        later.handler = None
        result = later.poll("task-001")
        self.assertFalse(result["last_poll_failed"])
        self.assertIsNone(result["error_details"])
        self.assertEqual(result["usage"]["input_video_duration"], 2)
        self.assertEqual(result["usage"]["output_video_duration"], 2)
        self.assertEqual(result["usage"]["fps"], 30)
        self.assert_public_safe(result)

    def test_adopt_unknown_verifies_exact_original_prompt_without_post(self):
        self.make_unknown()
        self.client.handler = lambda *args: {"output": {"task_id": "recovered-001", "task_status": "RUNNING", "orig_prompt": PROMPT}}
        result = self.client.adopt_task("request-A", "recovered-001")
        self.assertEqual(result["task_id"], "recovered-001")
        self.assertEqual(result["status"], "RUNNING")
        self.assertEqual([c[0] for c in self.client.calls], ["POST", "GET"])
        self.assert_public_safe(result)
        self.assertEqual(self.client.get_request("request-A")["task_id"], "recovered-001")

    def test_adoption_rejects_wrong_prompt_missing_prompt_and_unsubmitted_request(self):
        self.make_unknown()
        for prompt in ("other prompt", None):
            with self.subTest(prompt=prompt):
                self.client.handler = lambda *args: {"output": {"task_id": "other-task", "task_status": "RUNNING", "orig_prompt": prompt}}
                with self.assertRaises(provider.ProviderError):
                    self.client.adopt_task("request-A", "other-task")
                self.assertNotIn("task_id", self.client.get_request("request-A"))
        path = self.client._record_path("request-A")
        record = json.loads(path.read_text())
        record.update(status="SUBMITTING", generation_post_attempted=False)
        provider._private_json(path, record)
        calls = len(self.client.calls)
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.adopt_task("request-A", "other-task")
        self.assertEqual(caught.exception.code, "TASK_ADOPTION_NOT_ALLOWED")
        self.assertEqual(len(self.client.calls), calls)

    def test_adoption_rejects_task_already_associated_with_another_request(self):
        self.submit(key="associated")
        self.make_unknown()
        self.client.handler = lambda *args: {"output": {"task_id": "task-001", "task_status": "RUNNING", "orig_prompt": PROMPT}}
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.adopt_task("request-A", "task-001")
        self.assertEqual(caught.exception.code, "TASK_ALREADY_ASSOCIATED")

    def ready_download(self):
        self.submit()
        self.client.poll("task-001")

    def test_concurrent_downloads_reuse_one_verified_file(self):
        self.ready_download()
        counter = []
        guard = threading.Lock()
        def opened(*args, **kwargs):
            with guard:
                counter.append(1)
            time.sleep(.03)
            return Response(b"local-mocked-video")
        self.client.opener.open.side_effect = opened
        other = OfflineWan(self.client.state_dir)
        other.opener.open.side_effect = opened
        with patch.object(provider, "probe", return_value={"has_video": True, "fps": 30}), ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.client.download, "task-001", self.root / "one.mp4")
            second = pool.submit(other.download, "task-001", self.root / "two.mp4")
            a, b = first.result(), second.result()
        self.assertEqual(a, b)
        self.assertEqual(len(counter), 1)
        self.assertEqual(provider._sha(Path(a["path"])), a["sha256"])
        self.assertEqual(list(self.root.glob("*.part-*")), [])

    def test_changed_download_cache_is_never_silently_reused_or_overwritten(self):
        self.ready_download()
        self.client.opener.open.side_effect = None
        self.client.opener.open.return_value = Response(b"original-result")
        with patch.object(provider, "probe", return_value={"has_video": True}):
            result = self.client.download("task-001", self.root / "result.mp4")
        Path(result["path"]).write_bytes(b"changed")
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.download("task-001", self.root / "other.mp4")
        self.assertEqual(caught.exception.category, "integrity")
        self.assertEqual(caught.exception.code, "DOWNLOAD_CHANGED")
        self.assertEqual(Path(result["path"]).read_bytes(), b"changed")
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_expired_result_is_classified_and_poll_can_refresh_link(self):
        self.ready_download()
        self.client.opener.open.side_effect = http_error(403, "AccessDenied", message=SECRET+RESULT_URL, xml=True)
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.download("task-001", self.root / "result.mp4")
        self.assertEqual(caught.exception.category, "result_expired")
        self.assertFalse(caught.exception.ambiguous)
        self.assert_public_safe(caught.exception.public_error)
        self.assertFalse(self.client.get_request("request-A")["result_ready"])
        self.assertEqual(list(self.root.glob("*.part-*")), [])
        self.assertTrue(self.client.poll("task-001")["result_ready"])
        self.assertEqual(sum(c[0] == "POST" for c in self.client.calls), 1)

    def test_download_connection_failure_is_retryable_without_generation(self):
        self.ready_download()
        self.client.opener.open.side_effect = URLError(SECRET+RESULT_URL)
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.download("task-001", self.root / "result.mp4")
        self.assertEqual(caught.exception.category, "connection")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.client.get_request("request-A")["status"], "SUCCEEDED")
        self.assert_public_safe(caught.exception.public_error)
        self.client.opener.open.side_effect = None
        self.client.opener.open.return_value = Response(b"retried-result")
        with patch.object(provider, "probe", return_value={"has_video": True}):
            result = self.client.download("task-001", self.root / "result.mp4")
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(sum(c[0] == "POST" for c in self.client.calls), 1)

    def test_unknown_request_and_unofficial_result_never_open_network(self):
        with self.assertRaises(provider.ProviderError):
            self.client.get_request("never-submitted")
        self.assertEqual(list(self.client.state_dir.glob("request-*.json")), [])
        self.submit()
        self.client.handler = lambda *args: {"output": {"task_id": "task-001", "task_status": "SUCCEEDED", "video_url": "https://example.com/result.mp4"}}
        self.client.poll("task-001")
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.download("task-001", self.root / "bad.mp4")
        self.assertEqual(caught.exception.code, "RESULT_HOST_INVALID")
        self.client.opener.open.assert_not_called()

    def test_preflight_is_local_and_keeps_existing_budget_semantics(self):
        report = self.client.preflight(PROMPT, MEDIA, {"duration": 2, "resolution": "1080P"}, 10)
        self.assertTrue(report["configured"])
        self.assertEqual(report["estimate"]["reserved_yuan"], 2.4)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(list(self.client.state_dir.glob("request-*.json")), [])
        self.client.submit(PROMPT, MEDIA, {"duration": 2, "resolution": "1080P"},
                           approved_budget_yuan=10, request_key="request-A")
        with self.assertRaises(provider.ProviderError) as caught:
            self.client.submit(PROMPT, MEDIA, {"duration": 2, "resolution": "1080P"}, approved_budget_yuan=3, request_key="request-B")
        self.assertEqual(caught.exception.code, "BUDGET_INSUFFICIENT")
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
