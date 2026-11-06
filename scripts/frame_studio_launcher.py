"""Local, foreground launcher. No backend imports during preflight/diagnosis.

Keep runtime metadata/logs outside data/. Never stop an existing listener, query
a supplier, recover persisted jobs, or open a browser. The child binds before
importing the application, so an occupied port cannot trigger backend recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request, HTTPRedirectHandler


ROOT = Path(__file__).resolve().parents[1]
APP = "frame-studio-local"
MANIFEST = "frame-studio-build.json"
DEFAULT_PORT = 8765
LIVE = {"QUEUED", "RUNNING", "UPLOADING", "SUBMITTING", "PENDING",
        "SUCCEEDED", "DOWNLOADING", "UNKNOWN", "DOWNLOAD_FAILED"}


class StartupError(RuntimeError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root, paths):
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0" + digest(path).encode() + b"\n")
    return h.hexdigest()


def frontend_version(root):
    frontend = root / "frontend"
    paths = [p for p in frontend.rglob("*") if p.is_file()
             and not any(part in {"node_modules", "dist", ".git", "tests"}
                         or part.startswith(".frame-build-")
                         for part in p.relative_to(frontend).parts)
             and p.suffix.lower() not in {".md", ".log", ".pyc"}]
    return tree_digest(frontend, paths)


def backend_version(root):
    paths = list((root / "backend").glob("*.py"))
    paths.append(root / "scripts/frame_studio_launcher.py")
    return tree_digest(root, paths)


def runtime_dir(root):
    override = os.environ.get("FRAME_STUDIO_RUNTIME")
    if override:
        return Path(override).expanduser().resolve()
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return Path.home() / "Library/Logs/FrameStudio" / key


def data_dir(root):
    return Path(os.environ.get("FRAME_STUDIO_DATA", root / "data")).expanduser().resolve()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".frame-", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def check_root(root):
    required = ["backend/server.py", "backend/store.py", "frontend/package.json",
                "frontend/package-lock.json", "frontend/index.html"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise StartupError("项目目录不完整，缺少：" + "、".join(missing) +
                           "。请从完整项目中的「启动帧工坊.command」启动。")


def pending_records(data):
    """Read only task records; never import store or read private-config.json."""
    result = []
    patterns = [("jobs/*.json", "job"),
                ("projects/*/generation/preparation.json", "preparation"),
                ("projects/*/generation/runs/*/run.json", "generation")]
    for pattern, kind in patterns:
        for path in sorted(data.glob(pattern)):
            try:
                record = read_json(path)
                if kind == "job":
                    active = record["status"] in {"RUNNING", "QUEUED"}
                elif kind == "preparation":
                    active = record["status"] == "PREPARING"
                else:
                    active = record.get("status") == "RUNNING" or any(
                        item["status"] in LIVE for item in record["segments"])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                result.append({"path": str(path.relative_to(data)), "status": "UNREADABLE"})
                continue
            if active:
                result.append({"path": str(path.relative_to(data)), "status": record["status"]})
    return result


def assert_no_pending(data):
    pending = pending_records(data)
    if pending:
        names = "、".join(item["path"] for item in pending[:3])
        raise StartupError(f"发现 {len(pending)} 条活动、待核查或无法读取的任务记录（{names}）。"
                           "本次不启动、不恢复、不覆盖任务。请先核对原运行窗口及已有任务结果；"
                           "不要删除任务记录或重复付费提交。需要人工处理的恢复请交回制作会话。")


def build_state(root):
    dist = root / "frontend/dist"
    source = frontend_version(root)
    info = {"source": source, "built_source": None, "current": False, "reason": "页面尚未构建"}
    if not (dist / "index.html").is_file():
        return info
    try:
        manifest = read_json(dist / MANIFEST)
        if manifest["app"] != APP or manifest["schema"] != 1:
            raise ValueError("manifest")
        info["built_source"] = manifest["source"]
        info["built_at"] = manifest["built_at"]
        if manifest["source"] != source:
            info["reason"] = "前端源码已更新，现有页面构建较旧"
        elif manifest["output"] != tree_digest(dist, [p for p in dist.rglob("*")
                                                     if p.is_file() and p.name != MANIFEST]):
            info["reason"] = "构建文件已变化或缺失，需重新构建"
        else:
            info.update(current=True, reason="页面构建与源码一致")
    except (OSError, ValueError, KeyError, TypeError):
        info["reason"] = "现有页面没有可信构建记录，版本无法确认"
    return info


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def page_probe(port):
    # Explicitly bypass proxies; do not follow a local service's external redirect.
    request = Request(f"http://127.0.0.1:{port}/", headers={"Sec-Fetch-Site": "none"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=1) as response:
            content = response.read(2 * 1024 * 1024 + 1)
            return {"http_status": response.status,
                    "index_sha256": hashlib.sha256(content).hexdigest()}
    except HTTPError as error:
        return {"http_status": error.code}
    except (OSError, URLError):
        return {"http_status": None}


def process_stamp(pid, runner=subprocess.run):
    result = runner(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True,
                    text=True, timeout=3, env={**os.environ, "LC_ALL": "C"})
    return result.stdout.strip() if result.returncode == 0 else None


def listener_details(root, port, runner=subprocess.run):
    """Inspect ownership, never terminate anything or print process arguments."""
    if not shutil.which("lsof"):
        return {"kind": "unknown", "pids": []}
    listing = runner(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
                     capture_output=True, text=True, timeout=3)
    pids = sorted({int(line[1:]) for line in listing.stdout.splitlines()
                   if re.fullmatch(r"p\d+", line)})
    if not pids:
        return {"kind": "unknown", "pids": []}
    kinds, starts = [], {}
    for pid in pids:
        starts[str(pid)] = process_stamp(pid, runner)
        cwd_result = runner(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                            capture_output=True, text=True, timeout=3)
        cwd = next((line[1:] for line in cwd_result.stdout.splitlines() if line.startswith("n")), "")
        args = runner(["ps", "-p", str(pid), "-o", "command="],
                      capture_output=True, text=True, timeout=3).stdout
        if cwd and Path(cwd).resolve() == root.resolve() and (
            re.search(r"(?:^|\s)-m\s+backend\.server(?:\s|$)", args)
            or ("frame_studio_launcher.py" in args and "_serve" in args)
        ):
            kinds.append("studio")
        elif cwd and Path(cwd).resolve() in {root.resolve(), (root / "frontend").resolve()} and "vite" in args:
            kinds.append("vite")
        else:
            kinds.append("other")
    return {"kind": kinds[0] if len(set(kinds)) == 1 else "other", "pids": pids, "starts": starts}


def inspect_service(root, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(.5)
        connected = sock.connect_ex(("127.0.0.1", port)) == 0
    if not connected:
        # A reserved, not-yet-listening socket also counts as occupied. Use a
        # fresh socket: connect_ex may already have bound its ephemeral port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                reservation.bind(("127.0.0.1", port))
            except OSError:
                return {"kind": "unknown", "pids": [], "http_status": None}
            return {"kind": "absent", "pids": []}
    try:
        owner = listener_details(root, port)
    except (OSError, subprocess.TimeoutExpired):
        owner = {"kind": "unknown", "pids": []}
    owner.update(page_probe(port))
    if owner["kind"] == "studio":
        try:
            record = read_json(runtime_dir(root) / f"service-{port}.json")
            same_process = (record["pid"] in owner["pids"] and record.get("process_start")
                            and record["process_start"] == owner.get("starts", {}).get(str(record["pid"])))
            if (same_process and record["root"] == str(root.resolve()) and
                    record["data"] == str(data_dir(root))):
                owner["runtime"] = record
            elif same_process and record.get("data") != str(data_dir(root)):
                owner["different_data"] = True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return owner


def snapshot(root, port):
    service = inspect_service(root, port)
    return {"entry": f"http://127.0.0.1:{port}/", "root": str(root),
            "data": str(data_dir(root)), "logs": str(runtime_dir(root)),
            "service": service, "build": build_state(root),
            "pending_offline": pending_records(data_dir(root)) if service["kind"] == "absent" else [],
            "backend_source": backend_version(root)}


def report(info):
    service, build = info["service"], info["build"]
    print(f"项目目录：{info['root']}\n本机入口：{info['entry']}\n日志目录：{info['logs']}")
    kind = service["kind"]
    if kind == "absent":
        if info.get("pending_offline"):
            print(f"状态：此端口没有服务，但有 {len(info['pending_offline'])} 条任务待核查；不会直接启动或自动恢复。请交回制作会话核对原任务与结果。")
        else:
            print("状态：此端口没有服务。双击「启动帧工坊.command」启动，并保留运行窗口。")
    elif kind == "studio":
        print("状态：此项目的帧工坊已运行，本次复用，不重启。")
        if service.get("different_data"):
            print("注意：已有服务使用另一数据目录；不要在此端口启动第二份服务。")
        runtime = service.get("runtime")
        if runtime:
            print(f"运行后端：{runtime['backend'][:12]}（启动于 {runtime['started_at']}，UTC）")
            print(f"启动时页面：{runtime['frontend'][:12]}")
            if runtime["backend"] != info["backend_source"]:
                print("版本不同：后端源码已更新，运行进程仍是启动时版本。先保存草稿、等待任务完成，再手动停止并启动。")
            if runtime["frontend"] != build.get("built_source"):
                print("版本不同：页面构建在服务启动后发生了变化。先保存草稿，安排前后端一起更新。")
        else:
            print("运行后端版本：旧启动方式没有版本记录，无法确认；不能把磁盘源码当作运行版本。")
    else:
        label = "Vite 开发服务" if kind == "vite" else "其他服务或归属尚不能确认的进程"
        print(f"状态：端口被{label}占用。不会停止任何进程；请核对原运行窗口和端口。")
        if kind == "vite":
            print("这是开发入口，页面直接来自源码，API 代理到 127.0.0.1:8765；下方 dist 版本用于后端静态入口。")
    print(f"页面源码：{build['source'][:12]}；已构建：{(build.get('built_source') or '未知')[:12]}。{build['reason']}。")
    http = service.get("http_status")
    if kind != "absent" and http is None:
        print("服务尚未响应网页请求。请检查原运行窗口和日志，不要反复启动。")
    elif http == 403:
        print("入口返回 403：在已有浏览器地址栏手动输入上述本机入口。若仍失败，记录地址和错误交回排查；不要关闭本机访问保护。")
    elif http and http != 200:
        print(f"入口返回 HTTP {http}，请核对入口并查看运行日志。")
    if kind == "studio" and http == 200:
        index = Path(info["root"]) / "frontend/dist/index.html"
        if index.is_file() and digest(index) != service.get("index_sha256"):
            print("入口页面与此项目构建不一致。请核对地址、代理和原运行窗口。")
    if kind == "studio" and not build["current"]:
        print("本次保留运行服务及页面；更新前先保存草稿，等活动任务结束后再手动停止并启动。")


@contextmanager
def launch_lock(runtime):
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (runtime / "launcher.lock").open("a") as handle:
        os.chmod(handle.name, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StartupError("此项目已有启动或运行窗口。请先查看原窗口，或运行 --status；不会重复构建或重启。") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def check_dependencies(runner=subprocess.run):
    if sys.version_info < (3, 10):
        raise StartupError("需要 Python 3.10 或更新版本，请设置 FRAME_STUDIO_PYTHON。")
    checked = runner([sys.executable, "-c", "import PIL, numpy"], capture_output=True, text=True)
    if checked.returncode:
        raise StartupError(f"Python 环境缺少可用的 Pillow / NumPy。当前环境：{sys.executable}。"
                           "请按 README 安装依赖，启动器不会自动修改 Python 环境。")
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise StartupError("缺少 " + "、".join(missing) + "，请安装并确认启动窗口能找到它们。")


def dependencies_match(frontend):
    try:
        expected = read_json(frontend / "package-lock.json")
        installed = read_json(frontend / "node_modules/.package-lock.json")
        packages = installed["packages"]
        for name, item in packages.items():
            locked = expected["packages"][name]
            if any(item.get(key) != locked.get(key) for key in ("version", "integrity", "resolved")):
                return False
        direct = {**expected["packages"][""].get("dependencies", {}),
                  **expected["packages"][""].get("devDependencies", {})}
        return all("node_modules/" + name in packages and
                   read_json(frontend / "node_modules" / name / "package.json")["version"] ==
                   expected["packages"]["node_modules/" + name]["version"] for name in direct)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def run_logged(args, cwd, log):
    completed = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise StartupError(f"准备页面失败（退出码 {completed.returncode}）。请查看 {log.name}；旧构建已保留，本次不会启动旧页面。")


def build_frontend(root, log, runner=run_logged, before_publish=None):
    frontend = root / "frontend"
    if not shutil.which("npm") or not shutil.which("node"):
        raise StartupError("页面需要更新，但未找到 Node.js / npm。请安装后重试；已有依赖不会每次重装。")
    if not dependencies_match(frontend):
        print("前端依赖缺失或与锁定版本不同，正在安装；详情写入日志。", flush=True)
        runner(["npm", "ci", "--no-audit", "--no-fund"], frontend, log)
    else:
        # Verify transitive dependencies before trusting the install receipt.
        result = subprocess.run(["npm", "ls", "--all", "--parseable"], cwd=frontend,
                                stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            runner(["npm", "ci", "--no-audit", "--no-fund"], frontend, log)
    before = frontend_version(root)
    print("正在构建与当前源码一致的页面；详情写入日志。", flush=True)
    with tempfile.TemporaryDirectory(prefix="frame-studio-build-") as temporary:
        stage = Path(temporary) / "dist"
        runner(["npm", "run", "build", "--", "--outDir", str(stage), "--emptyOutDir"], frontend, log)
        if before != frontend_version(root):
            raise StartupError("构建期间源码发生变化，未发布这次页面。请完成修改后重新启动。")
        if not (stage / "index.html").is_file():
            raise StartupError("构建未生成 index.html，旧页面已保留。请检查日志。")
        write_json(stage / MANIFEST, {"schema": 1, "app": APP, "source": before,
                                    "output": tree_digest(stage, [p for p in stage.rglob("*") if p.is_file()]),
                                    "built_at": timestamp()})
        # Stage a copy on the same filesystem so publishing is a directory rename.
        with tempfile.TemporaryDirectory(prefix=".frame-build-", dir=frontend) as local:
            candidate = Path(local) / "dist"
            shutil.copytree(stage, candidate)
            dist, backup = frontend / "dist", Path(local) / "previous"
            if before != frontend_version(root):
                raise StartupError("发布前源码发生变化，旧页面已保留。请完成修改后重试。")
            if before_publish:
                before_publish()
            if dist.is_symlink():
                raise StartupError("dist 是符号链接，未覆盖；请先人工核对构建目录。")
            if dist.exists():
                dist.rename(backup)
            try:
                candidate.rename(dist)
            except BaseException:
                if backup.exists():
                    backup.rename(dist)
                raise


def serve(root, port):
    """Bind before application imports. Never invoke automatic task recovery."""
    with ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler) as http:
        assert_no_pending(data_dir(root))
        if not build_state(root)["current"]:
            raise StartupError("页面构建已过期，未启动服务。请完成修改后重新启动。")
        version = backend_version(root)
        sys.path.insert(0, str(root))
        from backend.server import Handler
        if version != backend_version(root):
            raise StartupError("后端源码在启动期间发生变化，未启动服务。请完成修改后重试。")
        http.RequestHandlerClass = Handler
        runtime = runtime_dir(root)
        write_json(runtime / f"service-{port}.json", {
            "app": APP, "pid": os.getpid(), "root": str(root.resolve()),
            "process_start": process_stamp(os.getpid()),
            "data": str(data_dir(root)), "backend": version,
            "frontend": build_state(root)["built_source"], "started_at": timestamp(), "port": port})
        print(f"帧工坊已启动：http://127.0.0.1:{port}/（仅本机）", flush=True)
        try:
            http.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass


def supervise(root, port, log):
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_serve", "--port", str(port)],
                             cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    lines = queue.Queue()

    def copy_output():
        for line in iter(child.stdout.readline, b""):
            lines.put(line)
        child.stdout.close()

    reader = threading.Thread(target=copy_output, daemon=True)
    reader.start()
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        while child.poll() is None or reader.is_alive() or not lines.empty():
            if stop_requested:
                stop_requested = False
                pending = pending_records(data_dir(root))
                if pending:
                    print("仍有活动或待核查任务，保留服务。请等任务结束并保存草稿后再按 Control-C。", flush=True)
                elif child.poll() is None:
                    child.send_signal(signal.SIGINT)
            try:
                line = lines.get(timeout=.2)
            except queue.Empty:
                continue
            log.write(line.decode("utf-8", errors="replace"))
            log.flush()
            print(line.decode("utf-8", errors="replace"), end="", flush=True)
        return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="帧工坊本机启动与只读诊断；不会打开浏览器或停止已有服务。")
    parser.add_argument("action", nargs="?", default="start", choices=["start", "_serve"])
    parser.add_argument("--status", action="store_true", help="只读检查入口、版本和构建，不启动或更新")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出只读诊断（不启动）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口须为 1–65535")
    os.chdir(ROOT)
    log_path = None
    try:
        check_root(ROOT)
        if args.action == "_serve":
            serve(ROOT, args.port)
            return 0
        info = snapshot(ROOT, args.port)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        report(info)
        if args.status:
            return 0
        if info["service"]["kind"] != "absent":
            return 0 if (info["service"]["kind"] == "studio" and
                         info["service"].get("http_status") == 200 and
                         not info["service"].get("different_data")) else 1
        runtime = runtime_dir(ROOT)
        with launch_lock(runtime):
            # Recheck under the shared project lock before any build or app import.
            if inspect_service(ROOT, args.port)["kind"] != "absent":
                raise StartupError("端口状态刚刚变化，请运行 --status 核对；本次未构建或启动。")
            assert_no_pending(data_dir(ROOT))
            check_dependencies()
            log_path = runtime / ("startup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.log")
            with log_path.open("x", encoding="utf-8", buffering=1) as log:
                os.chmod(log_path, 0o600)
                print(f"本次日志：{log_path}", flush=True)
                if not build_state(ROOT)["current"]:
                    def before_publish():
                        if inspect_service(ROOT, args.port)["kind"] != "absent":
                            raise StartupError("构建期间端口已有服务，未发布页面。请运行 --status 核对。")
                        assert_no_pending(data_dir(ROOT))
                    build_frontend(ROOT, log, before_publish=before_publish)
                assert_no_pending(data_dir(ROOT))
                print(f"运行后端：{backend_version(ROOT)[:12]}；页面：{build_state(ROOT)['built_source'][:12]}。", flush=True)
                print("请保留此窗口。停止前先保存草稿、等待任务完成，再按 Control-C。", flush=True)
                result = supervise(ROOT, args.port, log)
                if result:
                    raise StartupError(f"服务退出（代码 {result}）。请查看 {log_path}，不要反复重启。")
                return 0
    except (StartupError, OSError, subprocess.SubprocessError) as error:
        print(f"启动未完成：{error}", file=sys.stderr)
        if log_path:
            print(f"失败日志：{log_path}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("启动准备已取消，未开始自动恢复任务。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
