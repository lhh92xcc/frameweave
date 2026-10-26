from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.catalog import TEMPLATES
from studio.models import (
    ContractEdit,
    PlanEdit,
    ProjectCreate,
    Review,
    RoutingSettings,
    RunRequest,
    StageRequest,
)
from studio.providers import ProviderFailure, Providers
from studio.settings import SettingsStore
from studio.store import Conflict, Store, uid
from studio.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir=None, start_worker=True, transport=None):
    store = Store(Path(data_dir or os.environ.get("FRAMEWEAVE_DATA", ROOT / ".local")))
    settings = SettingsStore(store.root)
    providers = Providers(store, settings, transport=transport)
    workflow = Workflow(store, settings, providers)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            await workflow.start()
        yield
        await workflow.close()
        await providers.close()

    app = FastAPI(title="FrameWeave · 镜织工坊", version="0.1.0", lifespan=lifespan)
    app.state.workflow, app.state.store, app.state.providers = workflow, store, providers
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_guard(request, call_next):
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
            url = urlsplit(origin)
            if (
                url.scheme != "http"
                or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.port not in {8787, 5173}
            ):
                return JSONResponse(
                    {"error": {"code": "ORIGIN_REJECTED", "message": "只允许本地工作台修改配置和任务"}},
                    status_code=403,
                )
        length = request.headers.get("content-length", "")
        if (
            length.isdigit()
            and int(length) > (130 if request.url.path.endswith("/upload") else 16) * 1024 * 1024
        ):
            return JSONResponse(
                {"error": {"code": "BODY_TOO_LARGE", "message": "请求大小超过限制"}}, status_code=413
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": str(exc).strip("'")}}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"error": {"code": "CONFLICT", "message": str(exc)}}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": str(exc)}}, status_code=422)

    @app.exception_handler(ProviderFailure)
    async def provider_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=503)

    @app.get("/api/health")
    async def health():
        async def check(url):
            try:
                response = await providers.client.get(url + "/health", timeout=3)
                return "ready" if response.is_success else "unavailable"
            except httpx.HTTPError:
                return "offline"

        gateway, engine = await asyncio.gather(check(providers.gateway), check(providers.engine))
        return {
            "status": "ok",
            "gateway": gateway,
            "engine": engine,
            "worker": bool(workflow.runner and not workflow.runner.done()),
        }

    @app.get("/api/templates")
    async def templates():
        return TEMPLATES

    @app.get("/api/projects")
    async def projects():
        return [workflow.project_view(p, include_source=False) for p in store.projects()]

    @app.post("/api/projects", status_code=201)
    async def create(body: ProjectCreate):
        return workflow.project_view(await workflow.create(body))

    @app.get("/api/projects/{project_id}")
    async def project(project_id: str):
        return workflow.project_view(store.project(project_id))

    @app.put("/api/projects/{project_id}/contract")
    async def contract(project_id: str, body: ContractEdit):
        return workflow.update_contract(project_id, body.markdown, body.expected_revision)

    @app.post("/api/projects/{project_id}/contract/approve")
    async def approve_contract(project_id: str, body: dict):
        return workflow.approve_contract(project_id, body.get("hash"))

    @app.get("/api/projects/{project_id}/contract.md")
    async def download_contract(project_id: str):
        return Response(
            store.project(project_id)["contract"]["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="production-requirements.md"'},
        )

    @app.put("/api/projects/{project_id}/plan")
    async def edit_plan(project_id: str, body: PlanEdit):
        return workflow.edit_plan(project_id, body.plan.model_dump(), body.expected_revision)

    @app.post("/api/projects/{project_id}/plan/approve")
    async def approve_plan(project_id: str, body: dict):
        return workflow.approve_plan(project_id, body.get("revision"))

    @app.post("/api/projects/{project_id}/jobs", status_code=202)
    async def generate(project_id: str, body: StageRequest):
        return workflow.enqueue(project_id, body.stage, body.entity_id, body.force)

    @app.post("/api/projects/{project_id}/automation")
    async def automation(project_id: str, body: RunRequest):
        p = store.project(project_id)
        p.update(automation=body.enabled, paused=False, cancelled=False)
        store.save_project(p, p["version"])
        workflow.wake.set()
        return p

    @app.post("/api/projects/{project_id}/pause")
    @app.post("/api/projects/{project_id}/resume")
    @app.post("/api/projects/{project_id}/cancel")
    async def project_control(project_id: str, request: Request):
        action = request.url.path.rsplit("/", 1)[-1]
        if action not in {"pause", "resume", "cancel"}:
            raise HTTPException(404)
        p = store.project(project_id)
        if action == "cancel":
            p.update(cancelled=True, automation=False)
            for job in store.jobs(project_id):
                if job["status"] in {"queued", "running"}:
                    await cancel_job(job["id"])
        else:
            p.update(paused=action == "pause", cancelled=False)
        store.save_project(p, p["version"])
        workflow.wake.set()
        return p

    @app.get("/api/jobs")
    async def jobs():
        result = []
        for job in store.jobs():
            result.append(
                {k: v for k, v in job.items() if k not in {"snapshot", "effective_prompt", "runtime"}}
            )
        return result

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return {**store.job(job_id), "events": store.events(job_id)}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        j = store.job(job_id)
        if j["status"] in {"queued", "running"}:
            j["status"] = "cancelled"
            store.save_job(j)
            remote = j["runtime"].get("remote_id")
            if remote:
                try:
                    await providers.client.post(providers.gateway + f"/api/jobs/{remote}/cancel", timeout=5)
                except httpx.HTTPError:
                    store.event(job_id, "网关取消请求未确认，请在原生网关检查")
        return j

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: str):
        j = store.job(job_id)
        if j["status"] not in {"failed", "blocked", "cancelled"}:
            raise ValueError("只能重试已停止的任务")
        receipt = j["runtime"].get("receipt")
        if j["runtime"].get("cloud_submitting") and not (receipt and (store.root / receipt).is_file()):
            raise ValueError("提交结果不明：请先在供应商核对，再通过“已核对并重新生成”创建显式新请求")
        if j["runtime"].get("remote_id") or (receipt and (store.root / receipt).is_file()):
            p = store.project(j["project_id"])
            workflow.assert_current(j, p, allow_cancelled_job=True)
            j.update(status="queued", error=None)
            store.save_job(j)
            workflow.wake.set()
            return j
        return workflow.enqueue(j["project_id"], j["stage"], j["entity_id"], force=True)

    @app.post("/api/projects/{project_id}/upload", status_code=201)
    async def upload(
        project_id: str, stage: str = Form(...), entity_id: str = Form(...), file: UploadFile = File(...)
    ):
        if stage not in {"reference", "keyframe", "video", "audio"}:
            raise ValueError("不支持该素材类型")
        p = store.project(project_id)
        snapshot = workflow.snapshot(p, stage, entity_id)
        chunks, size = [], 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise HTTPException(413, "素材大小超过 128MB")
            chunks.append(chunk)
        raw = b"".join(chunks)
        artifact_id = uid("art")
        if stage in {"reference", "keyframe"}:
            from PIL import Image, UnidentifiedImageError

            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    mime = Image.MIME[image.format]
                    metadata = {"width": image.width, "height": image.height, "format": image.format}
                    w, h = map(int, p["aspect_ratio"].split(":"))
                    metadata["aspect_matches"] = abs(image.width / image.height - w / h) < 0.015
            except (OSError, ValueError, KeyError, UnidentifiedImageError) as exc:
                raise ValueError("请选择可解码的 PNG、JPEG 或 WebP 图片") from exc
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("只支持 PNG、JPEG、WebP 图片")
        else:
            mime = "video/mp4" if stage == "video" else "audio/mpeg"
            metadata = {}
        filename = artifact_id + (mimetypes.guess_extension(mime) or ".bin")
        path = store.media / filename
        path.write_bytes(raw)
        try:
            if stage in {"video", "audio"}:
                metadata = await providers.engine_request("/validate", {"file": filename, "kind": stage})
            current = store.project(project_id)
            if (
                current["contract"]["hash"] != snapshot["contract_hash"]
                or current["plan_revision"] != snapshot["plan_revision"]
            ):
                raise Conflict("项目已更新，请重新导入到当前版本")
            job = store.enqueue(
                project_id, stage, entity_id, {**snapshot, "import_nonce": artifact_id}, force=True
            )
            job.update(
                status="succeeded",
                effective_prompt=workflow.prompt(snapshot, "用户导入已有素材，待人工确认符合规范"),
                runtime={"imported": True},
            )
            store.save_job(job)
            result = workflow.save_output(
                job,
                artifact_id,
                {
                    "file": filename,
                    "mime": mime,
                    "metadata": metadata,
                    "route": {"provider": "user-upload", "model": "original", "paid": False},
                },
            )
            workflow.wake.set()
            return result
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.get("/api/artifacts")
    async def artifacts():
        result = []
        for p in store.projects():
            result += [
                {**a, "project_title": p["title"]}
                for a in workflow.project_view(p, include_source=False)["artifacts"]
            ]
        return result

    @app.post("/api/artifacts/{artifact_id}/review")
    async def review(artifact_id: str, body: Review):
        return workflow.review(artifact_id, body.approved, body.note)

    @app.get("/api/artifacts/{artifact_id}/content")
    async def media(artifact_id: str):
        a = store.artifact(artifact_id)
        path = (store.media / a["file"]).resolve()
        if not path.is_relative_to(store.media) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type=a["mime"])

    @app.get("/api/artifacts/{artifact_id}/thumbnail")
    async def thumbnail(artifact_id: str):
        a = store.artifact(artifact_id)
        path = (store.root / a.get("thumbnail", "missing")).resolve()
        if not path.is_relative_to(store.root / "thumbnails") or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/export.zip")
    async def export(project_id: str):
        exports = store.root / "exports"
        exports.mkdir(exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(suffix=".zip", dir=exports)
        os.close(fd)
        path = Path(filename)
        try:
            await asyncio.to_thread(workflow.export, project_id, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(
            path,
            media_type="application/zip",
            filename="frameweave-project.zip",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/api/settings")
    async def get_settings():
        return settings.public(settings.get())

    @app.put("/api/settings")
    async def save_settings(body: RoutingSettings):
        return settings.save(body)

    @app.get("/api/gateway/catalog")
    async def catalog():
        return await providers.gateway_catalog()

    @app.api_route("/api/gateway/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def gateway(path: str, request: Request):
        # Keep the evolving gateway UI available while native forms cover common setup.
        allowed = {"accounts", "web-image-providers", "video-providers", "models", "seedream", "connect"}
        parts = path.split("/")
        if parts[0] not in allowed or ".." in parts or len(parts) > 3:
            raise HTTPException(404)
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise HTTPException(413)
        try:
            response = await providers.client.request(
                request.method,
                providers.gateway + "/api/" + path,
                content=raw,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise ProviderFailure("GATEWAY_OFFLINE", "网页模型网关未连接，请检查启动日志") from exc
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    @app.get("/api/upstreams")
    async def upstreams():
        manifest = ROOT / "upstream-lock.json"
        return json.loads(manifest.read_text()) if manifest.exists() else {}

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        async def index(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
