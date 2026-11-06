"""Wan 3.0 HTTP integration. Never retries a generation POST.

Credentials are kept in process memory / environment only. State files are private,
and caller-facing receipts deliberately exclude signed result and upload URLs.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from .media import probe


PRICES = {"wan3.0-video": {"480P": .3, "720P": .6, "1080P": 1.2},
          "wan3.0-video-prime": {"480P": .45, "720P": .9, "1080P": 1.8}}
PRICE_SOURCE = "https://help.aliyun.com/zh/model-studio/model-pricing"
CHECKED_DATE = "2026-11-03"
API_SOURCE = "https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference"
WORKSPACE_SOURCE = "https://help.aliyun.com/zh/model-studio/obtain-the-app-id-and-workspace-id"
WORKSPACE_HELP = ("API Key 列表中的 ID 是密钥记录编号，不等于业务空间 ID。请复制同页显示的空间专属 Base URL，"
                  "或在业务空间详情查找 Workspace ID；现有 Key 可继续沿用，无需重新输入。"
                  "本工具可从官方空间专属 Base URL 提取空间与地域，并使用 Wan3 的 /api/v1 路径。"
                  "空间 ID 不按纯数字或字母前缀判断真伪，实际权限仍须由平台鉴权。")
REGIONS = {"cn-beijing": "北京", "ap-southeast-1": "新加坡", "ap-northeast-1": "日本（东京）",
           "eu-central-1": "德国（法兰克福）", "us-east-1": "美国（弗吉尼亚）", "cn-hongkong": "中国香港"}
MEDIA_TYPES = {"reference_image", "reference_video", "reference_audio", "first_frame", "last_frame", "file", "link"}


class ProviderError(RuntimeError):
    """A deliberately public-safe error, never a raw provider/network message."""

    def __init__(self, message, *, category="validation", code=None, request_id=None,
                 http_status=None, retryable=False, ambiguous=False):
        super().__init__(message)
        self.category = category
        self.code = _token(code)
        self.request_id = _token(request_id)
        self.http_status = http_status
        self.retryable = bool(retryable)
        self.ambiguous = bool(ambiguous)

    @property
    def public_error(self):
        return {"category": self.category, "code": self.code, "request_id": self.request_id,
                "http_status": self.http_status, "retryable": self.retryable,
                "ambiguous": self.ambiguous, "message": str(self)}


def _token(value):
    value = str(value) if value is not None else ""
    return value if re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value) else None


def _http_error(exc, *, method="GET", operation="api", secrets=()):
    """Only code/request id may be copied from an untrusted HTTP response."""
    try:
        body = exc.read(1024 * 1024)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            # OSS returns XML rather than JSON; do not expose its Message/URL.
            import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
            payload = {"code": root.findtext("Code"), "request_id": root.findtext("RequestId")}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    code = _token(payload.get("code")) or "HTTPError"
    request_id = _token(payload.get("request_id"))
    for secret in secrets:
        if secret:
            if secret in code:
                code = "HTTPError"
            if request_id and secret in request_id:
                request_id = None
    status = exc.code
    lowered = code.lower()
    generation = method == "POST" and operation == "api"
    ambiguous = generation and (status >= 500 or status == 408)
    retryable = not generation and (status >= 500 or status in {408, 429})
    if operation == "download" and status in {403, 404, 410}:
        category = "result_expired"
        message = "结果链接已过期或不可用；请查询原任务刷新链接。结果通常仅保留24小时，不会自动重新生成"
        retryable = False
    elif status == 401 or "invalidapikey" in lowered or "invalidaccesskey" in lowered:
        category, message = "authentication", "API Key 未通过验证，请在 API 设置中核对密钥与地域"
    elif any(word in lowered for word in ("quota", "balance", "arrear", "payment", "overdue")):
        category, message = "quota", "云端额度或余额不足，请核对百炼账户额度与计费状态"
    elif status == 403:
        category, message = "permission", "云端拒绝访问，请核对模型权限、工作空间和服务地域"
    elif status == 429 or any(word in lowered for word in ("throttl", "ratelimit", "toomanyrequests")):
        category, message = "rate_limit", "云端请求频率受限，请稍后查询原任务状态"
    elif status == 404:
        category, message = "task_not_found", "未找到原云端任务，请核对任务编号与创建任务时的服务地域"
    else:
        category, message = "provider_http", "云端接口请求未完成"
    if ambiguous:
        message += "；生成提交状态未知，禁止自动重新提交"
    return ProviderError(message, category=category, code=code, request_id=request_id,
                         http_status=status, retryable=retryable, ambiguous=ambiguous)


def _usage(value):
    """Keep documented usage evidence without arbitrary provider fields/URLs."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("duration", "input_video_duration", "output_video_duration", "fps"):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0:
            result[key] = number
    for key, pattern in (("SR", r"(?:480P|720P|1080P|\d{2,5}[xX]\d{2,5})"),
                         ("ratio", r"(?:adaptive|\d{1,3}:\d{1,3})")):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(pattern, item):
            result[key] = item
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _private_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _sha(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def estimate_cost(media: list, output_seconds: float, resolution="480P", model="wan3.0-video", unit_yuan_per_second=None) -> dict:
    """Conservative undiscounted quote; discounts/free quota are never assumed."""
    if model not in PRICES or resolution not in PRICES[model]:
        raise ProviderError("不支持的模型或分辨率")
    inputs = sum(float(m.get("duration_seconds", 0)) for m in media if m["type"] == "reference_video")
    seconds = inputs + float(output_seconds)
    if not math.isfinite(seconds) or output_seconds <= 0 or inputs < 0:
        raise ProviderError("视频时长必须为有限正数")
    # Reserve whole seconds conservatively to cover provider duration quantization.
    reserve_seconds = math.ceil(inputs - 1e-7) + math.ceil(output_seconds - 1e-7)
    rate = PRICES[model][resolution] if unit_yuan_per_second is None else float(unit_yuan_per_second)
    return {"currency": "CNY", "input_video_seconds": round(inputs, 6),
            "output_video_seconds": round(float(output_seconds), 6), "billable_seconds_estimate": round(seconds, 6),
            "unit_yuan_per_second": rate, "estimate_yuan": round(seconds * rate, 4),
            "reserved_yuan": round(reserve_seconds * rate, 4),
            "discount_assumed": False, "free_quota_assumed": False,
            "source": PRICE_SOURCE, "checked_date": CHECKED_DATE}


def parse_workspace_base_url(value):
    """Extract only a complete official workspace host, without network or keys.

    Console OpenAI-compatible URLs carry the same workspace and region as the
    native Wan endpoint. Only the documented base paths may be translated.
    """
    if not isinstance(value, str):
        raise ProviderError("请粘贴完整的官方业务空间 Base URL", category="configuration", code="WORKSPACE_BASE_INVALID")
    value = value.strip()
    parts = urlparse(value)
    match = re.fullmatch(r"([a-zA-Z0-9-]+)\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|us-east-1|cn-hongkong)\.maas\.aliyuncs\.com", parts.hostname or "")
    try:
        valid_port = parts.port in {None, 443}
    except ValueError:
        valid_port = False
    if (parts.scheme != "https" or not match or not valid_port or parts.username or parts.password
            or parts.query or parts.fragment or parts.path.rstrip('/') not in {'', '/api/v1', '/compatible-mode/v1'}):
        raise ProviderError("请粘贴官方空间专属 HTTPS Base URL；只接受专属主机及 /api/v1 或 /compatible-mode/v1 基础路径。", category="configuration", code="WORKSPACE_BASE_INVALID")
    workspace, region = match.groups()
    return {"workspace_id": workspace, "region": region, "region_label": REGIONS[region],
            "base_url": f"https://{workspace}.{region}.maas.aliyuncs.com/api/v1"}


def _api_base(config):
    base = config.get("base_url") or os.environ.get("DASHSCOPE_HTTP_BASE_URL")
    workspace = config.get("workspace_id") or os.environ.get("DASHSCOPE_WORKSPACE_ID")
    if not base:
        region = config.get("region") or "cn-beijing"
        base = f"https://{workspace}.{region}.maas.aliyuncs.com/api/v1" if workspace else "https://dashscope.aliyuncs.com/api/v1"
    base = str(base).strip().rstrip("/")
    parts = urlparse(base)
    if re.fullmatch(r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|us-east-1|cn-hongkong)\.maas\.aliyuncs\.com", parts.hostname or ""):
        return parse_workspace_base_url(base)['base_url']
    allowed = parts.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} or bool(re.fullmatch(r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|us-east-1|cn-hongkong)\.maas\.aliyuncs\.com", parts.hostname or ""))
    if parts.scheme != "https" or not allowed or parts.username or parts.password or parts.query or parts.fragment or parts.path != "/api/v1":
        raise ProviderError("API 地址须为官方百炼 HTTPS /api/v1 地址")
    return base


def inspect_configuration(config):
    """Local shape checks only. Never contacts the provider or verifies a Key."""
    base = _api_base(config)
    host = urlparse(base).hostname or ""
    match = re.fullmatch(r"([a-zA-Z0-9-]+)\.([a-z0-9-]+)\.maas\.aliyuncs\.com", host)
    region = match[2] if match else ("ap-southeast-1" if "dashscope-intl" in host else "cn-beijing")
    workspace = match[1] if match else None
    issues = []
    def issue(code, message):
        issues.append({"code": code, "message": message, "blocking": True, "scope": "configuration"})
    if not workspace:
        issue("WORKSPACE_ENDPOINT_REQUIRED", "Wan3 生成地址缺少业务空间 ID；请粘贴与 Key 同地域的空间专属 Base URL。API Key 列表中的 ID 是密钥记录编号；请使用业务空间 ID 或专属 API Host，沿用现有 Key。旧 dashscope 地址不能提交生成。")
    if workspace and config.get("workspace_id") and str(config["workspace_id"]).lower() != workspace.lower():
        issue("WORKSPACE_ENDPOINT_MISMATCH", "业务空间 ID 与生成地址不一致。请按空间专属 Base URL 中的主机标识核对，勿填 API Key 记录编号；现有 Key 无需重录。修正后请重新准备费用。")
    if config.get("region") and config["region"] != region:
        issue("REGION_ENDPOINT_MISMATCH", "所选地域与生成地址不一致，请核对 Key、模型、业务空间和 Endpoint 的地域。")
    return {"ready": not issues, "issues": issues, "base_url": base, "region": region,
            "region_label": REGIONS[region], "workspace_id": workspace,
            "authentication_verified": False, "source": API_SOURCE,
            "workspace_help": WORKSPACE_HELP, "workspace_help_source": WORKSPACE_SOURCE}


class WanClient:
    def __init__(self, config: dict | None = None, state_dir: Path | str | None = None):
        self.config = dict(config or {})
        self.base = _api_base(self.config)
        self.model = self.config.get("model", "wan3.0-video")
        if self.model not in PRICES:
            raise ProviderError("仅支持 Wan 3.0 标准版或 Prime")
        self._key = self.config.pop("api_key", None) or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY")
        self.state_dir = Path(state_dir or Path(__file__).resolve().parents[1]/"data/wan-tasks").resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.opener = build_opener(_NoRedirect())

    @property
    def configured(self):
        return bool(self._key)

    def configuration_status(self):
        return {**inspect_configuration({**self.config, "base_url": self.base}), "key_present": self.configured}

    def assert_configuration(self):
        report = self.configuration_status()
        if not report["ready"]:
            first = report["issues"][0]
            raise ProviderError(first["message"], category="configuration", code=first["code"])
        return report

    @contextlib.contextmanager
    def _lock(self):
        with (self.state_dir/".lock").open("a") as f:
            os.chmod(self.state_dir/".lock", 0o600)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _record_lock(self, path):
        # Serialise a task's poll/download/adoption without blocking other tasks.
        with path.with_suffix(".lock").open("a") as f:
            os.chmod(path.with_suffix(".lock"), 0o600)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _request_json(self, method, url, body=None, extra_headers=None):
        if method == "POST":
            self.assert_configuration()
        if not self._key:
            raise ProviderError("请先在本地配置百炼 API Key", category="configuration", code="API_KEY_MISSING")
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}
        headers.update(extra_headers or {})
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=float(self.config.get("timeout_seconds", 60))) as resp:
                result = json.loads(resp.read(10 * 1024 * 1024))
                if not isinstance(result, dict):
                    raise ValueError("invalid response")
                return result
        except HTTPError as exc:
            prompt = ((body or {}).get("input") or {}).get("prompt")
            raise _http_error(exc, method=method, secrets=(self._key, prompt)) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise ProviderError("百炼连接异常" + ("；生成提交状态未知，禁止自动重新提交" if method == "POST" else "，可查询原任务重试"),
                                category="connection", code="CONNECTION_ERROR",
                                retryable=method != "POST", ambiguous=method == "POST") from None
        except (ValueError, UnicodeError):
            raise ProviderError("百炼返回了无法核对的响应" + ("；生成提交状态未知，禁止自动重新提交" if method == "POST" else "，可查询原任务重试"),
                                category="provider_response", code="INVALID_RESPONSE",
                                retryable=method != "POST", ambiguous=method == "POST") from None

    def _inspect_media(self, media):
        if len(media) > 20:
            raise ProviderError("参考素材总数不能超过 20")
        inspected = []
        counts = {}
        for item in media:
            item = dict(item)
            kind = item.get("type")
            if kind not in MEDIA_TYPES:
                raise ProviderError("未知素材类型")
            counts[kind] = counts.get(kind, 0) + 1
            if item.get("path"):
                p = Path(item["path"]).expanduser().resolve()
                if not p.is_file():
                    raise ProviderError("素材文件不存在")
                item["path"] = str(p)
                item["sha256"] = _sha(p)
                item["bytes"] = p.stat().st_size
                if kind in {"reference_image", "first_frame", "last_frame"}:
                    from PIL import Image
                    with Image.open(p) as im:
                        w, h = im.size
                        if "A" in im.getbands() or "transparency" in im.info or not 240 <= min(w, h) or max(w, h) > 8000 or max(w,h)/min(w,h) > 8:
                            raise ProviderError("参考图须无透明通道、单边 240–8000 像素、长宽比不超过 8:1")
                    if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"} or item["bytes"] > 20 * 1024**2:
                        raise ProviderError("参考图格式或大小不符合要求")
                elif kind in {"reference_video", "reference_audio"}:
                    meta = probe(p)
                    item["duration_seconds"] = meta["duration_seconds"]
                    if not 1 <= item["duration_seconds"] <= 15.00001:
                        raise ProviderError("参考视频/音频每段须为 1–15 秒")
                    if kind == "reference_video":
                        if p.suffix.lower() not in {".mp4", ".mov"} or item["bytes"] > 100 * 1024**2:
                            raise ProviderError("参考视频须为 MP4/MOV 且不超过 100MB")
                        if not meta["has_video"] or meta["fps"] < 16 or not 240 <= min(meta["width"], meta["height"]) or max(meta["width"],meta["height"]) > 4096 or max(meta["width"],meta["height"])/min(meta["width"],meta["height"]) > 8:
                            raise ProviderError("参考视频须至少 16fps、单边 240–4096 像素")
                    elif p.suffix.lower() not in {".wav", ".mp3"} or item["bytes"] > 15 * 1024**2 or not meta["has_audio"]:
                        raise ProviderError("参考音频须为 WAV/MP3 且不超过 15MB")
                elif kind == "file" and (item["bytes"] > 100 * 1024**2 or p.suffix.lower() not in {".docx",".doc",".xlsx",".xls",".pptx",".ppt",".pdf",".txt",".key",".pages",".numbers",".md"}):
                    raise ProviderError("文档格式不支持或超过 100MB")
            else:
                # Remote duration cannot safely be inferred for charging or validation.
                url = str(item.get("url", ""))
                if kind in {"reference_video", "reference_audio"}:
                    raise ProviderError("请先将视频/音频导入本地，以便核验时长和费用")
                if urlparse(url).scheme != "https":
                    raise ProviderError("远程素材仅支持 HTTPS 链接")
                item['remote_url_sha256']=hashlib.sha256(url.encode()).hexdigest()
            inspected.append(item)
        limits = {"reference_image":10,"reference_video":5,"reference_audio":5,"first_frame":1,"last_frame":1,"file":1,"link":1}
        if any(counts.get(k, 0) > v for k,v in limits.items()):
            raise ProviderError("某类素材数量超过模型上限")
        if counts.get("file") and counts.get("link"):
            raise ProviderError("文件与网页不能同时输入")
        if (counts.get("first_frame") or counts.get("last_frame")) and any(counts.get(k) for k in ["reference_image","reference_video","reference_audio","file","link"]):
            raise ProviderError("首尾帧不能和全模态参考素材同时输入")
        if counts.get("last_frame") and not counts.get("first_frame"):
            raise ProviderError("尾帧模式必须提供首帧")
        for kind in ["reference_video", "reference_audio"]:
            if sum(m.get("duration_seconds",0) for m in inspected if m["type"] == kind) > 15.00001:
                raise ProviderError("参考视频/音频总时长分别不能超过 15 秒")
        return inspected

    def preflight(self, prompt: str, media: list, parameters: dict | None = None, budget_yuan: float | None = None):
        params = {"resolution":"480P","ratio":"16:9","duration":-1,"audio":False,"prompt_extend":False,"watermark":False}
        params.update(parameters or {})
        if set(params)-{"resolution","ratio","duration","audio","prompt_extend","watermark","seed"}:
            raise ProviderError("存在未支持的视频参数")
        if any(not isinstance(params[k],bool) for k in ["audio","prompt_extend","watermark"]):
            raise ProviderError("声音、提示词改写与水印参数须为布尔值")
        if "seed" in params and (not isinstance(params["seed"],int) or isinstance(params["seed"],bool) or params["seed"] not in range(-1,2147483648)):
            raise ProviderError("随机种子超出范围")
        if params.get("watermark") is not False:
            raise ProviderError("本工具固定 watermark=false")
        if not prompt.strip() or len(prompt) > 20000:
            raise ProviderError("提示词须为 1–20000 字符")
        try:
            inspected = self._inspect_media(media)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("无法读取参考素材，请检查文件是否完整且可解码", code="MEDIA_UNREADABLE") from None
        if any(m["type"] in {"file","link"} for m in inspected):
            params["prompt_extend"] = True
        incoming = sum(m.get("duration_seconds",0) for m in inspected if m["type"] == "reference_video")
        duration = params.get("duration")
        if not isinstance(duration, int) or isinstance(duration,bool) or duration != -1 and not 2 <= duration <= 30:
            raise ProviderError("输出时长须为 2–30 的整数或 -1 智能时长")
        max_output = 30 - incoming if duration == -1 else duration
        if incoming + max_output > 30.00001:
            raise ProviderError("输入视频与输出视频合计不能超过 30 秒")
        if params.get("ratio") not in {"adaptive","21:9","16:9","4:3","1:1","3:4","9:16"}:
            raise ProviderError("不支持的画幅")
        endpoint = self.configuration_status()
        rate=None
        if endpoint["region"] == "ap-southeast-1":
            intl={"wan3.0-video":{"480P":.37471,"720P":.74942,"1080P":1.49884},"wan3.0-video-prime":{"480P":.495838,"720P":1.020844,"1080P":2.041687}}
            rate=intl[self.model].get(params["resolution"])
        quote = estimate_cost(inspected,max_output,params["resolution"],self.model,rate)
        quote.update(region=endpoint["region"], region_label=endpoint["region_label"],
                     model=self.model, resolution=params["resolution"])
        public_media = [{k:v for k,v in m.items() if k != "url"} for m in inspected]
        return {"ok":True,"configured":self.configured,"model":self.model,"parameters":params,"media":public_media,
                "estimate":quote,"configuration":endpoint,"budget_yuan":budget_yuan,"budget_sufficient":budget_yuan is not None and budget_yuan >= quote["reserved_yuan"],
                "duration_policy":"Smart duration reserves maximum 30s minus reference-video duration" if duration == -1 else "Explicit output duration",
                "generated_quality":"UNVERIFIED_NOT_SUBMITTED"}

    def _upload(self, path: Path):
        self.assert_configuration()
        # The official temporary-storage API still documents legacy upload hosts.
        upload_base = "https://dashscope-intl.aliyuncs.com/api/v1" if "ap-southeast-1" in self.base or "dashscope-intl" in self.base else "https://dashscope.aliyuncs.com/api/v1"
        policy = self._request_json("GET",upload_base+"/uploads?"+urlencode({"action":"getPolicy","model":self.model})).get("data")
        required = ("upload_host", "upload_dir", "oss_access_key_id", "signature", "policy")
        if not isinstance(policy, dict) or any(not isinstance(policy.get(k), str) or not policy[k] for k in required):
            raise ProviderError("云端未返回完整的临时上传凭证；尚未提交生成", category="provider_response", code="UPLOAD_POLICY_INVALID", retryable=True)
        host = urlparse(policy["upload_host"])
        if host.scheme != "https" or not (host.hostname or "").endswith(".aliyuncs.com") or host.username or host.password:
            raise ProviderError("上传凭证返回了非官方存储地址")
        if path.stat().st_size > float(policy.get("max_file_size_mb",100))*1024**2:
            raise ProviderError("素材超过临时上传上限")
        # ASCII UUID filename avoids filesystem names / private path disclosure.
        key = policy["upload_dir"].rstrip("/")+"/"+uuid.uuid4().hex+path.suffix.lower()
        fields = {"OSSAccessKeyId":policy["oss_access_key_id"],"Signature":policy["signature"],"policy":policy["policy"],
                  "x-oss-object-acl":policy.get("x_oss_object_acl","private"),"x-oss-forbid-overwrite":"true","key":key,"success_action_status":"200"}
        boundary = "----WanStudio"+uuid.uuid4().hex
        parts=[]
        for k,v in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{Path(key).name}"\r\nContent-Type: {mimetypes.guess_type(path.name)[0] or "application/octet-stream"}\r\n\r\n'.encode())
        parts.extend([path.read_bytes(),f"\r\n--{boundary}--\r\n".encode()])
        req=Request(policy["upload_host"],data=b"".join(parts),headers={"Content-Type":"multipart/form-data; boundary="+boundary},method="POST")
        try:
            with self.opener.open(req,timeout=180) as resp:
                if resp.status not in {200,204}:
                    raise ProviderError("云端未确认素材上传成功；尚未提交生成", category="upload", code="UPLOAD_NOT_CONFIRMED", retryable=True)
        except HTTPError as exc:
            raise _http_error(exc, method="POST", operation="upload", secrets=(self._key,)) from None
        except (URLError, OSError, TimeoutError, socket.timeout):
            raise ProviderError("素材上传连接中断；尚未提交生成", category="connection", code="UPLOAD_CONNECTION_ERROR", retryable=True) from None
        return "oss://"+key

    def _public(self, record):
        allowed=["request_key","request_fingerprint","task_id","status","created_at","updated_at","estimate","parameters","model","request_id","error","error_details","usage","download","generation_post_attempted","base_url","last_poll_failed","adopted_at"]
        out={k:record[k] for k in allowed if k in record}
        out["error_details"] = record.get("error_details")
        out["configured"] = self.configured
        out["result_ready"]=bool(record.get("video_url")) and not record.get("result_expired", False)
        return out

    def list_tasks(self):
        return [self._public(json.loads(p.read_text())) for p in sorted(self.state_dir.glob("request-*.json"))]

    def _record_path(self,key):
        if not isinstance(key, str) or not key or len(key) > 200:
            raise ProviderError("缺少稳定的本地请求编号", code="REQUEST_KEY_INVALID")
        return self.state_dir/("request-"+hashlib.sha256(key.encode()).hexdigest()+".json")

    def get_request(self, request_key: str):
        """Read an existing receipt; never creates a task or performs a request."""
        path = self._record_path(request_key)
        if not path.exists():
            raise ProviderError("本地未找到该请求；不会自动创建任务", code="REQUEST_NOT_FOUND")
        return self._public(json.loads(path.read_text()))

    def _record_base(self, record):
        # Older receipts did not retain their endpoint and use the current base.
        return _api_base({"base_url": record.get("base_url") or self.base})

    @staticmethod
    def _save_error(record, error):
        record.update(error=str(error), error_details=error.public_error, updated_at=time.time())

    @staticmethod
    def _clear_error(record):
        record.pop("error", None)
        record.pop("error_details", None)

    def submit(self, prompt: str, media: list, parameters: dict | None = None, *, approved_budget_yuan: float, request_key: str,
               progress_callback=None, before_post=None):
        """One attempted POST per request key. Repeated calls return its saved state.

        Budget is the total cap for all jobs in this state_dir, not a fresh cap per call.
        UNKNOWN and interrupted SUBMITTING states retain their full reservations.
        """
        path=self._record_path(request_key)
        try:
            budget = float(approved_budget_yuan)
        except (ValueError, TypeError, OverflowError):
            budget = 0
        if not math.isfinite(budget) or budget <= 0:
            raise ProviderError("请先设置已授权的总预算", category="budget", code="BUDGET_INVALID")
        pre=self.preflight(prompt,media,parameters,budget)
        try:
            inspected=self._inspect_media(media)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("无法读取参考素材，请检查文件是否完整且可解码", code="MEDIA_UNREADABLE") from None
        fingerprint=hashlib.sha256(json.dumps({"prompt":prompt,"media":[{k:v for k,v in m.items() if k!='url'} for m in inspected],"parameters":pre['parameters'],"model":self.model},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with self._lock():
            if path.exists():
                record=json.loads(path.read_text())
                if record["request_fingerprint"]!=fingerprint:
                    raise ProviderError("同一请求编号对应的素材或提示词已改变；请先核对原任务")
                return self._public(record)
            if not self.configured:
                raise ProviderError("尚未配置百炼 API Key；未上传或提交", category="configuration", code="API_KEY_MISSING")
            self.assert_configuration()
            reserved=sum(float(t.get("estimate",{}).get("reserved_yuan",0)) for t in self.list_tasks() if t.get("status") not in {"FAILED","CANCELED","UPLOAD_FAILED","REJECTED"})
            if reserved+pre["estimate"]["reserved_yuan"] > budget+1e-8:
                raise ProviderError(f"总预算不足：已预留 {reserved:.2f} 元，本次需预留 {pre['estimate']['reserved_yuan']:.2f} 元", category="budget", code="BUDGET_INSUFFICIENT")
            now=time.time()
            record={"request_key":request_key,"request_fingerprint":fingerprint,"status":"UPLOADING","created_at":now,"updated_at":now,"estimate":pre["estimate"],"parameters":pre["parameters"],"model":self.model,"generation_post_attempted":False,
                    "base_url": self.base, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
            _private_json(path,record)
        with self._record_lock(path):
            try:
                if progress_callback:
                    progress_callback("UPLOADING")
            except Exception:
                error = ProviderError("上传前核对未通过；本次未上传或提交，请检查当前材料", category="snapshot", code="PRE_SUBMISSION_CHECK_FAILED")
                record["status"] = "CANCELED"
                self._save_error(record, error)
                _private_json(path, record)
                return self._public(record)
            try:
                resolved=[{"type":m["type"],"url":self._upload(Path(m["path"])) if m.get("path") else m["url"]} for m in inspected]
            except Exception as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError("素材上传未完成；生成尚未提交", category="upload", code="UPLOAD_FAILED", retryable=True)
                record["status"] = "UPLOAD_FAILED"
                self._save_error(record, error)
                _private_json(path, record)
                return self._public(record)
            try:
                if progress_callback:
                    progress_callback("SUBMITTING")
                if before_post:
                    before_post()
            except Exception:
                error = ProviderError("提交前核对未通过；本次未提交生成，请检查当前材料", category="snapshot", code="PRE_SUBMISSION_CHECK_FAILED")
                record["status"] = "CANCELED"
                self._save_error(record, error)
                _private_json(path, record)
                return self._public(record)
            # Persist ambiguity before transmission. No callback can fail after
            # this point and accidentally permit a later automatic second POST.
            record.update(status="SUBMITTING",generation_post_attempted=True,updated_at=time.time())
            _private_json(path,record)
            headers={"X-DashScope-Async":"enable"}
            if any(m["url"].startswith("oss://") for m in resolved):
                headers["X-DashScope-OssResourceResolve"]="enable"
            try:
                data=self._request_json("POST",self.base+"/services/aigc/video-generation/video-synthesis",{"model":self.model,"input":{"prompt":prompt,"media":resolved},"parameters":pre["parameters"]},headers)
                output = data.get("output") or {}
                task = output.get("task_id")
                if not isinstance(task, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", task):
                    raise ProviderError("提交响应缺少有效任务编号，状态未知；禁止自动重新提交", category="provider_response", code="TASK_ID_MISSING", ambiguous=True)
                record.update(task_id=task,status=output.get("task_status","PENDING"),request_id=self._public_token(data.get("request_id")),updated_at=time.time())
                if data.get("usage"):
                    record["usage"] = _usage(data["usage"])
            except Exception as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError("生成提交响应无法核对；状态未知，禁止自动重新提交", category="provider_response", code="SUBMISSION_UNKNOWN", ambiguous=True)
                rejected = error.http_status is not None and 400 <= error.http_status < 500 and not error.ambiguous
                if not rejected:
                    error.ambiguous, error.retryable = True, False
                record["status"] = "REJECTED" if rejected else "UNKNOWN"
                self._save_error(record, error)
            _private_json(path,record)
            return self._public(record)

    def _find_task(self,task_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}",str(task_id)):
            raise ProviderError("任务编号无效")
        for p in self.state_dir.glob("request-*.json"):
            record=json.loads(p.read_text())
            if record.get("task_id")==task_id:
                return p,record
        raise ProviderError("本地未找到该任务；不会自动创建新任务")

    def poll(self, task_id: str):
        path,_=self._find_task(task_id)
        with self._record_lock(path):
            record=json.loads(path.read_text())
            try:
                data=self._request_json("GET",self._record_base(record)+"/tasks/"+task_id)
                self._update_task(record, data)
                record["last_poll_failed"] = False
            except ProviderError as error:
                self._save_error(record, error)
                record["last_poll_failed"] = True
            _private_json(path,record)
            return self._public(record)

    def _update_task(self, record, data):
        output = data.get("output")
        if not isinstance(output, dict) or not _token(output.get("task_status")):
            raise ProviderError("云端任务响应不完整，请查询原任务重试", category="provider_response", code="TASK_RESPONSE_INVALID", retryable=True)
        record.update(status=output["task_status"], updated_at=time.time(), usage=_usage(data.get("usage")))
        self._clear_error(record)
        if output.get("video_url"):
            record["video_url"] = str(output["video_url"])
            record["result_expired"] = False
        if output.get("code"):
            error = ProviderError("云端任务未完成，请根据错误编号核对原任务", category="provider_task", code=self._public_token(output["code"]), request_id=self._public_token(data.get("request_id")))
            self._save_error(record, error)

    def _public_token(self, value):
        token = _token(value)
        return None if token and self._key and self._key in token else token

    def adopt_task(self, request_key: str, task_id: str):
        """Associate a known remote task after an ambiguous POST, never submit."""
        path = self._record_path(request_key)
        if not path.exists():
            raise ProviderError("本地未找到原请求；不能认领云端任务", code="REQUEST_NOT_FOUND")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", str(task_id)):
            raise ProviderError("任务编号无效", code="TASK_ID_INVALID")
        with self._record_lock(path):
            record = json.loads(path.read_text())
            if (record.get("status") not in {"UNKNOWN", "SUBMITTING"} or not record.get("generation_post_attempted") or record.get("task_id")):
                raise ProviderError("仅可核对已尝试提交、状态未知且缺少任务编号的原请求", code="TASK_ADOPTION_NOT_ALLOWED")
            data = self._request_json("GET", self._record_base(record)+"/tasks/"+task_id)
            output = data.get("output") or {}
            original = output.get("orig_prompt")
            if not record.get("prompt_sha256") or not isinstance(original, str):
                raise ProviderError("云端未提供可核对的原始提示词，不能关联此任务", code="TASK_PROMPT_UNVERIFIABLE")
            if output.get("task_id") != task_id or hashlib.sha256(original.encode()).hexdigest() != record["prompt_sha256"]:
                raise ProviderError("任务编号与原提交内容不对应，未关联此任务", code="TASK_PROMPT_MISMATCH")
            with self._lock():
                if any(t.get("task_id") == task_id for t in self.list_tasks()):
                    raise ProviderError("该云端任务已关联其它本地请求，不能重复关联", code="TASK_ALREADY_ASSOCIATED")
                self._update_task(record, data)
                record.update(task_id=task_id, adopted_at=time.time(), last_poll_failed=False)
                _private_json(path, record)
            return self._public(record)

    def download(self, task_id: str, out: str | Path):
        path,_=self._find_task(task_id)
        out=Path(out).resolve()
        with self._record_lock(path):
            record=json.loads(path.read_text())
            cached = record.get("download") or {}
            if cached.get("path") and Path(cached["path"]).exists():
                if not cached.get("sha256") or _sha(Path(cached["path"])) != cached["sha256"]:
                    raise ProviderError("已下载文件内容已变化；未复用或覆盖，请先核对本地结果", category="integrity", code="DOWNLOAD_CHANGED")
                return dict(cached)
            if record.get("status")!="SUCCEEDED" or not record.get("video_url"):
                raise ProviderError("任务尚未成功或结果链接尚未返回", code="RESULT_NOT_READY")
            u=urlparse(record["video_url"])
            if u.scheme!="https" or not (u.hostname or "").endswith(".aliyuncs.com") or u.username or u.password:
                raise ProviderError("结果下载地址不是官方 HTTPS 存储地址", category="provider_response", code="RESULT_HOST_INVALID")
            if out.exists():
                raise ProviderError("输出文件已存在，拒绝覆盖", category="integrity", code="OUTPUT_EXISTS")
            out.parent.mkdir(parents=True,exist_ok=True)
            tmp=out.with_suffix(out.suffix+".part-"+uuid.uuid4().hex)
            try:
                with self.opener.open(Request(record["video_url"]),timeout=180) as response, tmp.open("wb") as f:
                    total=0
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        total+=len(chunk)
                        if total>1024**3:
                            raise ProviderError("结果超过 1GB 下载上限", category="download", code="RESULT_TOO_LARGE")
                        f.write(chunk)
                meta=probe(tmp)
                if not meta["has_video"]:
                    raise ProviderError("下载结果不能解码为视频", category="download", code="RESULT_INVALID", retryable=True)
                os.replace(tmp,out)
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                if isinstance(exc, HTTPError):
                    error = _http_error(exc, operation="download", secrets=(self._key,))
                elif isinstance(exc, ProviderError):
                    error = exc
                elif isinstance(exc, (URLError, TimeoutError, socket.timeout, OSError)):
                    error = ProviderError("结果下载连接中断；可按原任务编号重试，不会重新生成", category="connection", code="DOWNLOAD_CONNECTION_ERROR", retryable=True)
                else:
                    error = ProviderError("结果下载或读取失败；可按原任务编号重试，不会重新生成", category="download", code="DOWNLOAD_FAILED", retryable=True)
                self._save_error(record, error)
                if error.category == "result_expired":
                    record["result_expired"] = True
                _private_json(path, record)
                raise error from None
            record["download"]={"path":str(out),"sha256":_sha(out),"media":meta}
            self._clear_error(record)
            _private_json(path,record)
            return dict(record["download"])
