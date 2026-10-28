from __future__ import annotations

import asyncio
import io
import json
import mimetypes
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.catalog import TEMPLATES
from studio.cloud_storage import CloudStorageError, CosSettings
from studio.models import (
    REFERENCE_ROLES,
    AudioCast,
    ContractEdit,
    PlanEdit,
    ProjectCreate,
    ReferenceRoleEdit,
    Review,
    RoutingSettings,
    RunRequest,
    ShotReferences,
    StageRequest,
)
from studio.providers import ProviderFailure, Providers, gateway_result_uncertain, requires_verification
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
    app.state.cloud = workflow.cloud
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

    @app.exception_handler(RequestValidationError)
    async def schema_invalid(request, exc):
        # Model-level validation can otherwise echo the entire credential-bearing request.
        return JSONResponse({"detail": [
            {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
        ]}, status_code=422)

    @app.exception_handler(ProviderFailure)
    async def provider_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=503)

    @app.exception_handler(CloudStorageError)
    async def storage_error(request, exc):
        return JSONResponse({"error": {"code": "COS_STORAGE_ERROR", "message": str(exc)}}, status_code=503)

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
            "gateway_console_url": "http://127.0.0.1:"
            + os.environ.get(
                "FRAMEWEAVE_PUBLIC_GATEWAY_PORT", os.environ.get("FRAMEWEAVE_GATEWAY_PORT", "8766")
            ),
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

    @app.put("/api/projects/{project_id}/audio-cast")
    async def audio_cast(project_id: str, body: AudioCast):
        return workflow.set_audio_cast(
            project_id, {key: value.model_dump(exclude_none=True) for key, value in body.voices.items()},
            body.speakers, body.expected_version,
        )

    @app.post("/api/projects/{project_id}/jobs", status_code=202)
    async def generate(project_id: str, body: StageRequest):
        return workflow.enqueue(
            project_id, body.stage, body.entity_id, body.force, reference_source_id=body.reference_source_id
        )

    @app.put("/api/projects/{project_id}/shots/{shot_id}/references")
    async def shot_references(project_id: str, shot_id: str, body: ShotReferences):
        return workflow.project_view(workflow.set_shot_references(
            project_id, shot_id, body.artifact_ids, body.previous_shot_id, body.expected_version
        ))

    @app.post("/api/projects/{project_id}/automation")
    async def automation(project_id: str, body: RunRequest):
        p = store.project(project_id)
        p.update(automation=body.enabled, paused=False, cancelled=False)
        p.pop("automation_error", None)
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
            item = {k: v for k, v in job.items() if k not in {"snapshot", "effective_prompt", "runtime"}}
            item["verification_required"] = requires_verification(job)
            result.append(item)
        return result

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return {**store.job(job_id), "events": store.events(job_id)}

    @app.get("/api/jobs/{job_id}/image-attempts/{number}")
    async def image_attempt(job_id: str, number: int):
        runtime = store.job(job_id).get("runtime", {})
        attempts = runtime.get("image_qc_attempts", [])
        pending = runtime.get("pending_qc_image")
        if number < 1 or (number > len(attempts) and not (pending and pending["attempt"] == number)):
            raise HTTPException(404)
        result = attempts[number - 1]["result"] if number <= len(attempts) else pending["result"]
        path = (store.media / result["file"]).resolve()
        if not path.is_relative_to(store.media) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type=result["mime"])

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
        successor = next(
            (
                candidate
                for candidate in store.jobs(j["project_id"])
                if candidate.get("retry_of") == j["id"]
                and candidate["fingerprint"] == j["fingerprint"]
                and candidate["status"] in {"queued", "running", "succeeded"}
            ),
            None,
        )
        if successor:
            return successor
        receipt = j["runtime"].get("receipt")
        if j["runtime"].get("cloud_submitting") and not (receipt and (store.root / receipt).is_file()):
            raise ValueError("提交结果不明：请先在供应商核对，再通过“已核对并重新生成”创建显式新请求")
        if requires_verification(j) and not (receipt and (store.root / receipt).is_file()):
            raise ValueError("提交结果不明：请先在供应商核对，再通过“已核对并重新生成”创建显式新请求")
        if (
            j["runtime"].get("pending_qc_image")
            and not j["runtime"].get("remote_id")
            and not j["runtime"].get("cloud_submitting")
            and (j.get("error") or {}).get("code") != "IMAGE_QC_EXHAUSTED"
        ):
            workflow.assert_current(j, allow_cancelled_job=True)
            j["runtime"].pop("vision_calls", None)
            if (j.get("error") or {}).get("code") == "VISION_INVALID":
                j["runtime"]["qc_retry_nonce"] = j["runtime"].get("qc_retry_nonce", 0) + 1
            j.update(status="queued", error=None)
            store.save_job(j)
            workflow.wake.set()
            return j
        remote_id = j["runtime"].get("remote_id")
        if remote_id or (receipt and (store.root / receipt).is_file()):
            p = store.project(j["project_id"])
            workflow.assert_current(j, p, allow_cancelled_job=True)
            if remote_id:
                remote = await providers.gateway_job_status(remote_id)
                current = store.job(job_id)
                if current["status"] not in {"failed", "blocked", "cancelled"}:
                    return current
                if current["runtime"].get("remote_id") != remote_id:
                    raise Conflict("任务状态已变化，请刷新后重试")
                j = current
                workflow.assert_current(j, allow_cancelled_job=True)
                if remote["status"] in {"failed", "cancelled"}:
                    if gateway_result_uncertain(remote):
                        message = "网页任务可能已在供应商侧完成，请先核对任务和账单，再单独确认是否重新生成"
                        j["error"] = {"code": "REMOTE_RESULT_UNCERTAIN", "message": message}
                        store.save_job(j)
                        store.event(j["id"], message)
                        raise ProviderFailure("REMOTE_RESULT_UNCERTAIN", message)
                    return workflow.enqueue(
                        j["project_id"],
                        j["stage"],
                        j["entity_id"],
                        force=True,
                        retry_of=j,
                        reference_source_id=(
                            j["snapshot"]["dependencies"][0]
                            if j["stage"] == "reference" and j["snapshot"]["dependencies"]
                            else None
                        ),
                    )
            j.update(status="queued", error=None)
            store.save_job(j)
            workflow.wake.set()
            return j
        if j["stage"] == "reference" and "reference_base_id" in j["snapshot"]:
            workflow.assert_current(j, allow_cancelled_job=True)
        return workflow.enqueue(
            j["project_id"],
            j["stage"],
            j["entity_id"],
            force=True,
            retry_of=j,
            reference_source_id=(
                j["snapshot"]["dependencies"][0]
                if j["stage"] == "reference" and j["snapshot"]["dependencies"]
                else None
            ),
        )

    @app.post("/api/projects/{project_id}/upload", status_code=201)
    async def upload(
        project_id: str, stage: str = Form(...), entity_id: str = Form(...), file: UploadFile = File(...),
        reference_role: str = Form(default="general"),
    ):
        if stage not in {"reference", "keyframe", "video", "audio"}:
            raise ValueError("不支持该素材类型")
        p = store.project(project_id)
        if stage == "reference":
            asset = workflow.entity(p, "reference", entity_id)
            if reference_role not in REFERENCE_ROLES[asset["kind"]]:
                raise ValueError("参考图分类与素材类型不匹配")
        elif reference_role != "general":
            raise ValueError("只有素材参考图可以设置参考分类")
        snapshot = workflow.snapshot(p, stage, entity_id, allow_unapproved_reference=stage == "reference")
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
                    if stage == "reference":
                        metadata["reference_role"] = reference_role
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
            job = store.record_import(
                project_id,
                stage,
                entity_id,
                {**snapshot, "import_nonce": artifact_id},
                workflow.prompt(snapshot, "用户导入已有素材，待人工确认符合规范"),
            )
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
            result = await workflow.backup_output(job, result)
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
        return workflow.review(artifact_id, body.approved, body.note, body.expected_review)

    @app.put("/api/artifacts/{artifact_id}/reference-role")
    async def reference_role(artifact_id: str, body: ReferenceRoleEdit):
        artifact = store.artifact(artifact_id)
        project = store.project(artifact["project_id"])
        if artifact["kind"] != "reference" or artifact.get("archived"):
            raise ValueError("仅当前项目的参考图可以分类")
        if artifact["contract_hash"] != project["contract"]["hash"] or artifact["plan_revision"] != project["plan_revision"]:
            raise ValueError("旧版本参考图不能重新分类")
        asset = workflow.entity(project, "reference", artifact["entity_id"])
        if body.role not in REFERENCE_ROLES[asset["kind"]]:
            raise ValueError("参考图分类与素材类型不匹配")
        old_role = artifact.get("metadata", {}).get("reference_role", "general")
        if old_role == body.role:
            return artifact
        artifact.setdefault("metadata", {})["reference_role"] = body.role
        artifact["review"] = "pending"
        artifact["review_version"] = artifact.get("review_version", 0) + 1
        artifact.pop("reviewed_at", None)
        artifact.pop("review_note", None)
        store.save_artifact(artifact)
        workflow.wake.set()
        return artifact

    @app.get("/api/artifacts/{artifact_id}/content")
    async def media(artifact_id: str, download: bool = False):
        a = store.artifact(artifact_id)
        if a.get("storage") and workflow.cloud.get().enabled:
            if a["mime"] == "image/svg+xml":

                def read_svg():
                    with workflow.cloud.materialize(store, a) as source:
                        return source.read_bytes()

                return Response(
                    await asyncio.to_thread(read_svg),
                    media_type=a["mime"],
                    headers={
                        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"
                    },
                )
            return RedirectResponse(
                await asyncio.to_thread(
                    workflow.cloud.signed_url,
                    a["storage"],
                    (a.get("title") or a["file"]) if download else None,
                ),
                status_code=307,
            )
        path = (store.media / a["file"]).resolve()
        if not path.is_relative_to(store.media) or not path.is_file():
            raise HTTPException(404)
        headers = (
            {"Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"}
            if a["mime"] == "image/svg+xml"
            else None
        )
        return FileResponse(
            path,
            media_type=a["mime"],
            headers=headers,
            filename=(a.get("title") or path.name) if download else None,
        )

    @app.get("/api/artifacts/{artifact_id}/thumbnail")
    async def thumbnail(artifact_id: str):
        a = store.artifact(artifact_id)
        if a.get("thumbnail_storage") and workflow.cloud.get().enabled:
            return RedirectResponse(
                await asyncio.to_thread(workflow.cloud.signed_url, a["thumbnail_storage"]), status_code=307
            )
        path = (store.root / a.get("thumbnail", "missing")).resolve()
        if not path.is_relative_to(store.root / "thumbnails") or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/export.zip")
    async def export(project_id: str, include_history: bool = False):
        exports = store.root / "exports"
        exports.mkdir(exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(suffix=".zip", dir=exports)
        os.close(fd)
        path = Path(filename)
        try:
            await asyncio.to_thread(workflow.export, project_id, path, include_history=include_history)
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

    @app.get("/api/audio/minimax/voices")
    async def minimax_voices():
        from studio.minimax import voices

        current = settings.get().audio
        if current.provider != "minimax":
            raise ValueError("请先在声音设置中选择 MiniMax 并保存 API Key")
        result = await voices(providers.client, current.api_key)
        library = workflow.voice_library
        library.cache_system_voices(result["voices"])
        allowed = library.allowed_voice_ids()
        return {"voices": [v for v in result["voices"] if v["id"] in allowed]}

    @app.get("/api/audio/voice-library")
    async def voice_library():
        library = workflow.voice_library
        return {"voices": [library.public(entry) for entry in library.entries()],
                "policy": {"customization_allowed": False, "new_casting": "system_voices_only",
                           "activated_voice_reuse": True, "tts_character_charges": True}}

    @app.get("/api/audio/voice-library/export.zip")
    async def export_voice_library():
        exports = store.root / "exports"
        exports.mkdir(exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(suffix=".zip", dir=exports)
        os.close(fd)
        path = Path(filename)
        try:
            await asyncio.to_thread(workflow.voice_library.export, path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(path, media_type="application/zip", filename="frameweave-voice-library.zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.get("/api/audio/voice-library/{voice_id}/preview")
    async def voice_preview(voice_id: str):
        return FileResponse(workflow.voice_library.preview(voice_id), media_type="audio/mpeg")

    @app.put("/api/settings")
    async def save_settings(body: RoutingSettings):
        return settings.save(body)

    @app.get("/api/storage")
    async def storage_settings():
        rows = [a for a in store.artifacts() if a.get("storage")]
        return {
            **workflow.cloud.public(workflow.cloud.get()),
            "usage": {
                "originals": len(rows),
                "bytes": sum(a["storage"]["size"] for a in rows),
                "thumbnails": sum(bool(a.get("thumbnail_storage")) for a in rows),
                "local_copies": sum((store.media / a["file"]).is_file() for a in rows),
            },
        }

    @app.put("/api/storage")
    async def save_storage_settings(body: CosSettings):
        workflow.cloud.save(body)
        return await storage_settings()

    @app.post("/api/storage/test")
    async def test_storage():
        result = await asyncio.to_thread(workflow.cloud.probe)
        return {**result, "lifecycle": await asyncio.to_thread(workflow.cloud.lifecycle)}

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
        if parts[0] == "seedream" and (path != "seedream" or request.method not in {"GET", "PUT", "DELETE"}):
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
        if parts[0] == "seedream":
            if response.is_error:
                return JSONResponse(
                    {"error": {"code": "SEEDREAM_SETTINGS_ERROR", "message": "Seedream 配置操作失败，请检查网关和输入后重试"}},
                    status_code=response.status_code,
                )
            try:
                data = response.json()
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("configured"), bool)
                    or not isinstance(data.get("model"), str)
                    or not re.fullmatch(r"doubao-seedream-[0-9-]+(?:flash-|lite-)?[0-9]+", data["model"])
                    or data.get("base_url") != "https://ark.cn-beijing.volces.com/api/v3"
                    or type(data.get("output_price_cny")) not in (int, float)
                    or not 0 <= data["output_price_cny"] <= 10000
                ):
                    raise ValueError("invalid seedream settings")
                return {key: data[key] for key in ("configured", "model", "base_url", "output_price_cny")}
            except (ValueError, TypeError) as exc:
                raise ProviderFailure("GATEWAY_INVALID_RESPONSE", "无法读取 Seedream 配置状态") from exc
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
