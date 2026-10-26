"""Provider routing. Only pre-submission failures may trigger paid fallback."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image

from studio.models import Route
from studio.store import digest

GATEWAY_URL = os.environ.get("FRAMEWEAVE_GATEWAY", "http://127.0.0.1:8766").rstrip("/")
ENGINE_URL = os.environ.get("FRAMEWEAVE_ENGINE", "http://127.0.0.1:8767").rstrip("/")
FREE_PROVIDERS = {"gemini", "bing", "imagefx", "recraft", "leonardo", "seaart", "whisk", "pollinations"}


class ProviderFailure(Exception):
    def __init__(self, code, message, safe_fallback=False):
        self.code, self.message, self.safe_fallback = code, message, safe_fallback
        super().__init__(message)


class Providers:
    def __init__(self, store, settings, gateway=GATEWAY_URL, engine=ENGINE_URL, transport=None):
        self.store, self.settings = store, settings
        self.gateway, self.engine = gateway, engine
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15), transport=transport)

    async def close(self):
        await self.client.aclose()

    async def engine_request(self, path, payload):
        try:
            response = await self.client.post(self.engine + path, json=payload)
            if response.is_error:
                detail = response.json().get("detail", "媒体引擎返回错误")
                raise ProviderFailure("ENGINE_REJECTED", str(detail)[:400])
            return response.json()
        except (httpx.TransportError, ValueError) as exc:
            raise ProviderFailure("ENGINE_UNAVAILABLE", "媒体引擎未就绪，请检查服务状态和运行日志") from exc

    async def gateway_catalog(self):
        result = {}
        for key, path in [
            ("accounts", "/api/accounts"),
            ("video", "/api/video-providers"),
            ("image", "/api/web-image-providers"),
        ]:
            try:
                response = await self.client.get(self.gateway + path, timeout=10)
                response.raise_for_status()
                result[key] = response.json().get("data", [])
            except (httpx.HTTPError, ValueError):
                result[key] = []
        return result

    async def is_paid(self, route):
        if route.transport == "cloud":
            return True
        if route.provider in FREE_PROVIDERS or route.provider.endswith("-web"):
            return False
        # New gateway connectors are discovered at runtime after upstream updates.
        catalog = await self.gateway_catalog()
        info = next((r for r in catalog["video"] if r["id"] == route.provider), None)
        return not info or info.get("auth_type") not in {"web_cookie", "web_session", "web_token", "public"}

    def checkpoint(self, job, **values):
        if self.store.job(job["id"])["status"] == "cancelled":
            raise asyncio.CancelledError
        job["runtime"].update(values)
        self.store.save_job(job)

    async def generate(self, kind, job, prompt, refs=(), duration=5, aspect_ratio="9:16", output=None):
        call_key = digest(
            {
                "kind": kind,
                "prompt": prompt,
                "refs": [p.name for p in refs],
                "duration": duration,
                "ratio": aspect_ratio,
            }
        )
        calls = job["runtime"].setdefault("calls", {})
        if kind == "text" and call_key in calls:
            return calls[call_key]
        if job["runtime"].get("active_call") != call_key:
            if job["runtime"].get("cloud_submitting"):
                raise ProviderFailure("SUBMISSION_UNKNOWN", "上一次云模型调用结果不明，请先核对供应商记录")
            self.checkpoint(job, active_call=call_key, route_index=0, remote_id=None, remote_kind=None)
        settings = self.settings.get()
        routes = [Route.model_validate(r) for r in job["snapshot"]["routes"][kind]]
        allow_paid = job["snapshot"]["allow_paid"]
        last = None
        for index, saved in enumerate(routes):
            if index < job["runtime"].get("route_index", 0):
                continue
            matching = next(
                (
                    r
                    for r in getattr(settings, kind)
                    if (r.transport, r.provider, r.model, r.base_url)
                    == (saved.transport, saved.provider, saved.model, saved.base_url)
                ),
                None,
            )
            route = saved.model_copy(update={"api_key": matching.api_key if matching else None})
            if await self.is_paid(route) and not allow_paid:
                last = ProviderFailure(
                    "PAID_DISABLED", "已关闭付费渠道；请配置网页额度，或在设置中启用付费兜底", True
                )
                continue
            self.store.event(job["id"], f"渠道 {index + 1}：{route.provider} / {route.model}")
            try:
                if route.transport == "gateway":
                    result = await self.gateway_generate(
                        kind, job, route, index, prompt, refs, duration, aspect_ratio
                    )
                else:
                    result = await self.cloud_generate(kind, job, route, prompt, refs, aspect_ratio)
                result["route"] = {
                    "transport": route.transport,
                    "provider": route.provider,
                    "model": route.model,
                    "paid": await self.is_paid(route),
                }
                if kind != "text":
                    raw, mime = result.pop("content"), result.pop("mime")
                    if not output:
                        raise RuntimeError("Missing artifact output")
                    if kind == "image":
                        try:
                            import io

                            with Image.open(io.BytesIO(raw)) as image:
                                image.verify()
                            with Image.open(io.BytesIO(raw)) as image:
                                result["metadata"] = {
                                    "width": image.width,
                                    "height": image.height,
                                    "format": image.format,
                                }
                                mime = Image.MIME[image.format]
                            ratio = image.width / image.height
                            w, h = map(int, aspect_ratio.split(":"))
                            result["metadata"]["aspect_matches"] = abs(ratio - w / h) < 0.015
                        except (ValueError, OSError, KeyError) as exc:
                            raise ProviderFailure("INVALID_IMAGE", "模型未返回可解码的图片") from exc
                    suffix = mimetypes.guess_extension(mime.split(";")[0]) or (
                        ".mp4" if kind == "video" else ".png"
                    )
                    filename = output + suffix
                    path = self.store.media / filename
                    path.write_bytes(raw)
                    if kind == "video":
                        try:
                            result["metadata"] = await self.engine_request("/validate", {"file": filename})
                        except Exception:
                            path.unlink(missing_ok=True)
                            raise
                    result.update(file=filename, mime=mime)
                if kind == "text":
                    calls[call_key] = result
                self.checkpoint(
                    job,
                    active_call=None,
                    route_index=0,
                    remote_id=None,
                    remote_kind=None,
                    cloud_submitting=False,
                )
                return result
            except ProviderFailure as exc:
                last = exc
                self.store.event(job["id"], f"{exc.code}：{exc.message}")
                if not exc.safe_fallback:
                    raise
                self.checkpoint(job, route_index=index + 1, remote_id=None, remote_kind=None)
        raise last or ProviderFailure("NO_ROUTE", "没有可用模型渠道，请先填写模型配置")

    async def gateway_generate(self, kind, job, route, index, prompt, refs, duration, ratio):
        runtime = job["runtime"]
        limit = {"text": 64000, "image": 16000, "video": 20000}[kind]
        if len(prompt) > limit:
            raise ProviderFailure(
                "PROMPT_TOO_LONG",
                f"当前规范与提示词共 {len(prompt)} 字，超过网页渠道 {limit} 字限制。请精简规范或选择更长上下文的渠道；不会截断规范。",
                True,
            )
        if len(refs) > 10:
            raise ProviderFailure(
                "TOO_MANY_REFERENCES",
                "当前网页渠道最多接受 10 张参考图；请合并素材或选择支持更多引用的渠道",
                True,
            )
        if kind == "image" and route.provider == "gemini" and not job["snapshot"]["allow_paid"]:
            try:
                response = await self.client.get(self.gateway + "/api/seedream", timeout=10)
                response.raise_for_status()
                if response.json().get("configured"):
                    raise ProviderFailure(
                        "HIDDEN_PAID_FALLBACK",
                        "Web2API 原生 Seedream 兜底已配置；关闭它或启用工作台付费策略后再生成",
                        True,
                    )
            except httpx.HTTPError as exc:
                raise ProviderFailure("GATEWAY_OFFLINE", "网页模型网关未启动", True) from exc
        remote_id = runtime.get("remote_id") if runtime.get("route_index") == index else None
        if not remote_id:
            reference_ids = []
            if refs:
                files = [
                    ("files", (p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"))
                    for p in refs
                ]
                try:
                    response = await self.client.post(self.gateway + "/api/references", files=files)
                    response.raise_for_status()
                    reference_ids = [x["id"] for x in response.json()["data"]]
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    raise ProviderFailure(
                        "REFERENCE_UPLOAD_FAILED", "参考图上传失败；未提交生成请求", True
                    ) from exc
            if kind == "text":
                payload = {
                    "provider": route.provider,
                    "model": route.model,
                    "messages": [
                        {"role": "user", "content": prompt[i : i + 30000]}
                        for i in range(0, len(prompt), 30000)
                    ],
                }
                path = "/api/chat/jobs"
            elif kind == "image":
                payload = {
                    "provider": route.provider,
                    "model": route.model,
                    "prompt": prompt,
                    "aspect_ratio": ratio,
                    "reference_images": reference_ids,
                    "size": "2K",
                    "response_format": "url",
                }
                path = "/api/jobs"
            else:
                payload = {
                    "provider": route.provider,
                    "model": route.model,
                    "prompt": prompt,
                    "aspect_ratio": ratio,
                    "reference_images": reference_ids,
                    "duration": duration,
                    "resolution": "720p",
                    "audio": job["snapshot"].get("audio_mode") == "native",
                }
                path = "/api/video/jobs"
            try:
                response = await self.client.post(
                    self.gateway + path,
                    json=payload,
                    headers={
                        "Idempotency-Key": f"frameweave-{job['id']}-{index}-{runtime['active_call'][:16]}"
                    },
                )
            except httpx.TransportError as exc:
                raise ProviderFailure(
                    "SUBMISSION_UNKNOWN", "网关提交结果不明；重试会复用同一幂等键，请先检查网关任务"
                ) from exc
            if response.is_error:
                # Gateway enqueue errors are before acceptance and safe to route elsewhere.
                raise ProviderFailure(
                    "GATEWAY_REJECTED",
                    f"网页渠道未接受请求（HTTP {response.status_code}），请检查账号、模型和额度",
                    True,
                )
            try:
                remote_id = response.json()["id"]
            except (ValueError, KeyError) as exc:
                raise ProviderFailure("SUBMISSION_UNKNOWN", "网关未返回任务 ID，请先核对网关任务") from exc
            self.checkpoint(job, route_index=index, remote_id=remote_id, remote_kind=kind)
        self.store.event(job["id"], f"网页网关任务：{remote_id}")
        for _ in range(1800):
            current = self.store.job(job["id"])
            if current["status"] == "cancelled":
                await self.client.post(self.gateway + f"/api/jobs/{remote_id}/cancel")
                raise asyncio.CancelledError
            try:
                response = await self.client.get(self.gateway + f"/api/jobs/{remote_id}", timeout=20)
                response.raise_for_status()
                remote = response.json()
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(2)
                continue
            if remote["status"] == "succeeded":
                if kind == "text":
                    if not remote.get("text"):
                        raise ProviderFailure("EMPTY_TEXT", "模型返回了空文本")
                    return {"text": remote["text"], "remote_id": remote_id}
                paths = remote.get("images" if kind == "image" else "videos", [])
                if not paths:
                    raise ProviderFailure("EMPTY_MEDIA", "网关任务成功但没有可下载素材")
                prefix = "/images/" if kind == "image" else "/videos/"
                raw, mime = await self.download(self.gateway + prefix + paths[0])
                return {
                    "content": raw,
                    "mime": mime,
                    "remote_id": remote_id,
                    "attempts": remote.get("attempts", []),
                }
            if remote["status"] in {"failed", "cancelled"}:
                error = remote.get("error") or {}
                code = error.get("code", "GATEWAY_JOB_FAILED")
                safe = any(
                    word in code.lower()
                    for word in (
                        "quota",
                        "credit",
                        "configured",
                        "credential",
                        "login",
                        "auth",
                        "not_ready",
                        "rate_limit",
                    )
                )
                raise ProviderFailure(
                    code, "网页任务失败，请在连接设置或原生网关中检查账号、额度与模型权限", safe
                )
            await asyncio.sleep(2)
        raise ProviderFailure("POLL_TIMEOUT", "任务仍未结束，已保存上游任务 ID；稍后重试继续查询，不重复提交")

    async def cloud_generate(self, kind, job, route, prompt, refs, ratio):
        if not route.api_key or not route.base_url:
            raise ProviderFailure("CLOUD_NOT_CONFIGURED", "云模型缺少 API Key 或服务地址", True)
        receipt_dir = self.store.root / "receipts"
        receipt_dir.mkdir(exist_ok=True, mode=0o700)
        receipt = receipt_dir / (job["id"] + "-" + job["runtime"]["active_call"] + ".json")
        if job["runtime"].get("cloud_submitting") and not receipt.exists():
            raise ProviderFailure(
                "SUBMISSION_UNKNOWN", "云模型上次提交结果不明；需先在供应商侧核对，防止重复扣费"
            )
        if kind == "video":
            raise ProviderFailure(
                "VIDEO_PROTOCOL", "官方视频模型请通过 Web2API 视频连接器配置其 API Key", True
            )
        headers = {"Authorization": "Bearer " + route.api_key}
        if kind == "text":
            path = "/chat/completions"
            kwargs = {
                "json": {
                    "model": route.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                }
            }
        elif refs:
            path = "/images/edits"
            kwargs = {
                "data": {
                    "model": route.model,
                    "prompt": prompt,
                    "n": "1",
                    "size": {"9:16": "1024x1536", "16:9": "1536x1024", "1:1": "1024x1024"}[ratio],
                },
                "files": [
                    ("image[]", (p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"))
                    for p in refs
                ],
            }
        else:
            path = "/images/generations"
            kwargs = {
                "json": {
                    "model": route.model,
                    "prompt": prompt,
                    "n": 1,
                    "size": {"9:16": "1024x1536", "16:9": "1536x1024", "1:1": "1024x1024"}[ratio],
                }
            }
        if receipt.exists():
            body = json.loads(receipt.read_text())
        else:
            self.checkpoint(job, cloud_submitting=True, receipt=str(receipt.relative_to(self.store.root)))
            try:
                response = await self.client.post(route.base_url + path, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                raise ProviderFailure(
                    "SUBMISSION_UNKNOWN", "云模型提交结果不明，请核对供应商请求记录后处理"
                ) from exc
            if response.status_code in {400, 401, 403, 404, 422, 429}:
                self.checkpoint(job, cloud_submitting=False)
                raise ProviderFailure(
                    "CLOUD_REJECTED",
                    f"云模型未接受请求（HTTP {response.status_code}），请检查模型、密钥或限流",
                    True,
                )
            if response.is_error:
                raise ProviderFailure(
                    "SUBMISSION_UNKNOWN", f"云模型响应 HTTP {response.status_code}；请核对是否已扣费"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderFailure("INVALID_RESPONSE", "模型未返回 JSON；请核对供应商记录") from exc
            fd = os.open(receipt, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(body, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
        # Persisted receipt makes response processing/download safely resumable.
        self.checkpoint(job, cloud_submitting=False)
        try:
            if kind == "text":
                value = body["choices"][0]["message"]["content"]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Empty model text")
                self.checkpoint(job, cloud_submitting=False)
                return {"text": value}
            item = body["data"][0]
            if item.get("b64_json"):
                raw, mime = base64.b64decode(item["b64_json"], validate=True), "image/png"
            else:
                # Never forward the model's Bearer token to an output CDN.
                raw, mime = await self.download(urljoin(route.base_url + "/", item["url"]))
            self.checkpoint(job, cloud_submitting=False)
            return {"content": raw, "mime": mime}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderFailure("INVALID_RESPONSE", "模型响应不符合接口协议，已保留提交记录") from exc

    async def download(self, url, max_bytes=512 * 1024 * 1024):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ProviderFailure("INVALID_MEDIA_URL", "模型返回的素材地址无效")
        try:
            async with self.client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProviderFailure("MEDIA_TOO_LARGE", "输出素材超过 512MB 限制")
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get(
                    "content-type", "application/octet-stream"
                ).split(";")[0]
        except httpx.HTTPError as exc:
            raise ProviderFailure("MEDIA_DOWNLOAD_FAILED", "结果下载失败；请保留上游任务记录后重试") from exc
