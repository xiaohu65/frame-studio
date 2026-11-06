"""Isolated launcher checks. No production server, browser, provider or data."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MODULE = Path(__file__).resolve().parents[1] / "scripts/frame_studio_launcher.py"
spec = importlib.util.spec_from_file_location("frame_studio_launcher", MODULE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class StartupDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        cwd = Path.cwd()
        self.addCleanup(os.chdir, cwd)
        self.tmp = tempfile.TemporaryDirectory(prefix="frame-launcher-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.frontend = self.root / "frontend"
        for name in ("frontend/src", "backend", "scripts", "runtime", "data"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ("backend/server.py", "backend/store.py", "scripts/frame_studio_launcher.py"):
            (self.root / name).write_text("# test fixture\n")
        (self.frontend / "index.html").write_text("<title>fixture</title>")
        (self.frontend / "src/main.jsx").write_text("// v1")
        (self.frontend / "package.json").write_text('{"name":"fixture"}')
        (self.frontend / "package-lock.json").write_text('{"packages":{"":{}}}')
        self.env = patch.dict(os.environ, {"FRAME_STUDIO_DATA": str(self.root / "data"),
                                         "FRAME_STUDIO_RUNTIME": str(self.root / "runtime")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def stamp(self):
        dist = self.frontend / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "index.html").write_text("<title>built</title>")
        (dist / "main.js").write_text("// built")
        self.write("frontend/dist/" + launcher.MANIFEST, {
            "schema": 1, "app": launcher.APP, "source": launcher.frontend_version(self.root),
            "built_at": "2026-11-03T00:00:00+00:00",
            "output": launcher.tree_digest(dist, [p for p in dist.rglob("*") if p.is_file() and p.name != launcher.MANIFEST])})

    def test_versions_detect_source_lock_and_missing_output_without_mtime(self):
        self.stamp()
        self.assertTrue(launcher.build_state(self.root)["current"])
        original = launcher.frontend_version(self.root)
        source = self.frontend / "src/main.jsx"
        before = source.stat()
        source.write_text("// v2")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertNotEqual(original, launcher.frontend_version(self.root))
        self.assertFalse(launcher.build_state(self.root)["current"])
        source.write_text("// v1")
        self.assertTrue(launcher.build_state(self.root)["current"])
        (self.frontend / "dist/main.js").unlink()
        self.assertIn("缺失", launcher.build_state(self.root)["reason"])
        self.stamp()
        (self.frontend / "package-lock.json").write_text('{"packages":{},"changed":true}')
        self.assertFalse(launcher.build_state(self.root)["current"])

    def test_legacy_or_tampered_dist_is_never_reported_current(self):
        (self.frontend / "dist").mkdir()
        (self.frontend / "dist/index.html").write_text("legacy")
        self.assertIn("无法确认", launcher.build_state(self.root)["reason"])
        self.stamp()
        (self.frontend / "dist/index.html").write_text("tampered")
        self.assertFalse(launcher.build_state(self.root)["current"])

    def test_pending_scan_is_readonly_and_does_not_read_private_config(self):
        paths = [self.write("data/jobs/a.json", {"status": "RUNNING"}),
                 self.write("data/jobs/done.json", {"status": "SUCCEEDED"}),
                 self.write("data/projects/p/generation/preparation.json", {"status": "PREPARING"}),
                 self.write("data/projects/p/generation/runs/r/run.json", {
                     "status": "ATTENTION", "segments": [{"status": "DOWNLOAD_FAILED"}]}),
                 self.write("data/jobs/broken.json", [])]
        private = self.root / "data/private-config.json"
        private.write_text("invalid private fixture, must not be read")
        prior = {str(p): p.read_bytes() for p in paths + [private]}
        with self.assertRaises(launcher.StartupError):
            launcher.assert_no_pending(self.root / "data")
        self.assertEqual(len(launcher.pending_records(self.root / "data")), 4)
        self.assertEqual(prior, {str(p): p.read_bytes() for p in paths + [private]})

    def test_port_absent_reserved_and_other_http_service_are_distinct(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            self.assertEqual(launcher.inspect_service(self.root, port)["kind"], "unknown")
        self.assertEqual(launcher.inspect_service(self.root, port)["kind"], "absent")
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"another app")
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(launcher, "listener_details", return_value={"kind": "other", "pids": [123]}):
                    state = launcher.inspect_service(self.root, server.server_port)
                self.assertEqual(state["kind"], "other")
                self.assertEqual(state["http_status"], 200)
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_process_recognition_requires_project_cwd_not_only_process_name(self):
        def runner(args, **kwargs):
            if "-Fp" in args:
                return subprocess.CompletedProcess(args, 0, "p42\n", "")
            if "cwd" in args:
                return subprocess.CompletedProcess(args, 0, "p42\nn" + str(self.root) + "\n", "")
            return subprocess.CompletedProcess(args, 0, "python -m backend.server --port 10000\n", "")
        with patch.object(launcher.shutil, "which", return_value="lsof"):
            self.assertEqual(launcher.listener_details(self.root, 10000, runner)["kind"], "studio")
            self.assertEqual(launcher.listener_details(self.root / "another", 10000, runner)["kind"], "other")

    def test_diagnostic_preserves_legacy_uncertainty_and_403_security_hint(self):
        info = {"root": str(self.root), "entry": "http://127.0.0.1:12345/", "logs": "fixture-logs",
                "backend_source": "b" * 64,
                "build": {"source": "a" * 64, "current": False, "reason": "需构建"},
                "service": {"kind": "studio", "http_status": 403}}
        output = io.StringIO()
        with redirect_stdout(output):
            launcher.report(info)
        self.assertIn("无法确认", output.getvalue())
        self.assertIn("地址栏手动输入", output.getvalue())
        self.assertIn("不要关闭", output.getvalue())
        info["service"]["runtime"] = {"backend": "c" * 64, "frontend": "d" * 64, "started_at": "fixture"}
        with redirect_stdout(output):
            launcher.report(info)
        self.assertIn("后端源码已更新", output.getvalue())

    def test_existing_service_and_readonly_status_never_build_import_or_touch_runtime(self):
        service = {"kind": "studio", "pids": [123], "http_status": 200}
        before = sorted(str(p) for p in self.root.rglob("*"))
        with patch.object(launcher, "ROOT", self.root), \
             patch.object(launcher, "inspect_service", return_value=service), \
             patch.object(launcher, "build_frontend", side_effect=AssertionError("must not build")), \
             patch.object(launcher, "check_dependencies", side_effect=AssertionError("must not inspect dependencies")), \
             patch.object(launcher, "supervise", side_effect=AssertionError("must not start")), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main([]), 0)
            self.assertEqual(launcher.main(["--status"]), 0)
            self.assertEqual(launcher.main(["--json"]), 0)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))

    def test_parallel_launcher_is_blocked_without_terminating_owner(self):
        runtime = self.root / "runtime"
        with launcher.launch_lock(runtime):
            with self.assertRaises(launcher.StartupError):
                with launcher.launch_lock(runtime):
                    self.fail("second launcher acquired lock")

    def test_missing_dependencies_are_actionable_and_do_not_install(self):
        failure = subprocess.CompletedProcess([], 1, "", "private traceback must not be shown")
        with self.assertRaises(launcher.StartupError) as error:
            launcher.check_dependencies(lambda *a, **k: failure)
        self.assertIn("Pillow / NumPy", str(error.exception))
        self.assertNotIn("traceback", str(error.exception))
        with patch.object(launcher.shutil, "which", return_value=None):
            with self.assertRaises(launcher.StartupError) as error:
                with tempfile.TemporaryFile(mode="w+") as log:
                    launcher.build_frontend(self.root, log)
        self.assertIn("Node.js / npm", str(error.exception))

    def test_failed_build_preserves_old_page_and_success_gets_content_receipt(self):
        self.stamp()
        original = (self.frontend / "dist/index.html").read_bytes()
        def runner(args, cwd, log):
            if "build" in args:
                stage = Path(args[args.index("--outDir") + 1])
                stage.mkdir()
                (stage / "index.html").write_text("new page")
        with patch.object(launcher.shutil, "which", return_value="fixture"), \
             patch.object(launcher, "dependencies_match", return_value=False), \
             redirect_stdout(io.StringIO()), tempfile.TemporaryFile(mode="w+") as log:
            with self.assertRaises(launcher.StartupError):
                launcher.build_frontend(self.root, log, lambda *args: (_ for _ in ()).throw(launcher.StartupError("fake build failure")))
            self.assertEqual(original, (self.frontend / "dist/index.html").read_bytes())
            launcher.build_frontend(self.root, log, runner)
        self.assertEqual((self.frontend / "dist/index.html").read_text(), "new page")
        self.assertTrue(launcher.build_state(self.root)["current"])

    def test_build_race_or_running_service_before_publish_preserves_old_page(self):
        self.stamp()
        original = (self.frontend / "dist/index.html").read_bytes()
        def runner(args, cwd, log):
            if "build" in args:
                stage = Path(args[args.index("--outDir") + 1])
                stage.mkdir()
                (stage / "index.html").write_text("new page")
        def stop():
            raise launcher.StartupError("service became active")
        with patch.object(launcher.shutil, "which", return_value="fixture"), \
             patch.object(launcher, "dependencies_match", return_value=False), \
             redirect_stdout(io.StringIO()), tempfile.TemporaryFile(mode="w+") as log:
            with self.assertRaises(launcher.StartupError):
                launcher.build_frontend(self.root, log, runner, before_publish=stop)
            self.assertEqual(original, (self.frontend / "dist/index.html").read_bytes())
            def changing(args, cwd, log):
                runner(args, cwd, log)
                if "build" in args:
                    (self.frontend / "src/main.jsx").write_text("// changed during build")
            with self.assertRaises(launcher.StartupError):
                launcher.build_frontend(self.root, log, changing)
        self.assertEqual(original, (self.frontend / "dist/index.html").read_bytes())

    def test_dependencies_match_lock_versions_and_skip_platform_optional_absences(self):
        locked = {"": {"devDependencies": {"vite": "1"}},
                  "node_modules/vite": {"version": "1", "integrity": "fixture"},
                  "node_modules/other-platform": {"version": "2", "optional": True}}
        self.write("frontend/package-lock.json", {"packages": locked})
        self.write("frontend/node_modules/.package-lock.json", {"packages": {"node_modules/vite": locked["node_modules/vite"]}})
        self.write("frontend/node_modules/vite/package.json", {"version": "1"})
        self.assertTrue(launcher.dependencies_match(self.frontend))
        self.write("frontend/node_modules/vite/package.json", {"version": "2"})
        self.assertFalse(launcher.dependencies_match(self.frontend))

    def test_occupied_port_binds_before_any_application_import_or_recovery(self):
        # A fake backend import would write a sentinel. It must never execute.
        (self.root / "backend/__init__.py").write_text("")
        sentinel = self.root / "imported"
        (self.root / "backend/server.py").write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            with self.assertRaises(OSError):
                launcher.serve(self.root, sock.getsockname()[1])
        self.assertFalse(sentinel.exists())

    def test_foreground_lifecycle_reuse_logs_and_active_task_stop_guard(self):
        # Run a complete copy against a tiny fake backend, never the real app.
        if not shutil.which("lsof"):
            self.skipTest("process ownership requires lsof")
        shutil.copyfile(MODULE, self.root / "scripts/frame_studio_launcher.py")
        (self.root / "backend/__init__.py").write_text("")
        (self.root / "backend/server.py").write_text('''from http.server import BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
 def log_message(self, *args): pass
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b"fake backend")
def main():
 raise AssertionError("legacy main/recovery must never run")
''')
        self.stamp()
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        env = dict(os.environ)
        env.pop("DASHSCOPE_API_KEY", None)
        env.pop("BAILIAN_API_KEY", None)
        proc = subprocess.Popen([launcher.sys.executable, str(self.root / "scripts/frame_studio_launcher.py"),
                                 "--port", str(port)], cwd=self.root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        record_path = self.root / "runtime" / f"service-{port}.json"
        job_path = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self.fail(proc.communicate()[0].decode())
                if record_path.is_file() and launcher.page_probe(port).get("http_status") == 200:
                    break
                time.sleep(.05)
            else:
                self.fail("isolated launcher did not become ready")
            state = launcher.inspect_service(self.root, port)
            self.assertEqual(state["kind"], "studio")
            self.assertEqual(state["runtime"]["backend"], launcher.backend_version(self.root))
            initial_record = record_path.read_bytes()
            second = subprocess.run([launcher.sys.executable, str(self.root / "scripts/frame_studio_launcher.py"),
                                     "--port", str(port)], cwd=self.root, env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("本次复用，不重启", second.stdout)
            self.assertEqual(record_path.read_bytes(), initial_record)
            job_path = self.write("data/jobs/live.json", {"status": "RUNNING"})
            prior = job_path.read_bytes()
            proc.send_signal(signal.SIGINT)
            time.sleep(.35)
            self.assertIsNone(proc.poll())
            self.assertEqual(launcher.page_probe(port)["http_status"], 200)
            self.assertEqual(job_path.read_bytes(), prior)
            self.write("data/jobs/live.json", {"status": "SUCCEEDED"})
            proc.send_signal(signal.SIGINT)
            output = proc.communicate(timeout=5)[0].decode()
            self.assertEqual(proc.returncode, 0, output)
            self.assertIn("仍有活动或待核查任务，保留服务", output)
            logs = list((self.root / "runtime").glob("startup-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("帧工坊已启动", logs[0].read_text())
            self.assertEqual(launcher.inspect_service(self.root, port)["kind"], "absent")
        finally:
            if job_path:
                self.write("data/jobs/live.json", {"status": "SUCCEEDED"})
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    # Only PIDs created by this isolated fixture can be stopped.
                    if record_path.is_file():
                        os.kill(json.loads(record_path.read_text())["pid"], signal.SIGTERM)
                    proc.kill()
                    proc.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
