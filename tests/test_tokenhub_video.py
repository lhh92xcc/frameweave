"""Offline TokenHub fixtures: no external generation or billing validation."""
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from studio.models import Route
from studio.providers import ProviderFailure
from studio.tokenhub_video import ENDPOINT, MODELS, generate


def route(model="vidu-video-q3-turbo"):
    return Route(transport="cloud", provider="tokenhub-vidu", base_url=ENDPOINT, model=model, api_key="offline-tokenhub-key")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", sorted(MODELS - {"vidu-video-q2"}))
async def test_models_payload_resume_and_key_isolation(tmp_path, monkeypatch, model):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (32, 32)).save(frame)
    uploads = []
    class Storage:
        def __init__(self, root):
            pass

        def upload(self, path):
            uploads.append(path)
            return {"key": "verified-offline-image"}

        def signed_url(self, ref):
            return "https://offline.cos.example/frame.png?temporary=capability"
    monkeypatch.setattr("studio.tokenhub_video.CosStorage", Storage)
    requests = []
    def handle(req):
        requests.append(req)
        if req.url.host == "media.example":
            assert "authorization" not in req.headers
            return httpx.Response(200, content=b"offline fixture, not generated video")
        assert req.headers["authorization"] == "Bearer offline-tokenhub-key"
        if req.method == "POST":
            return httpx.Response(200, json={"data": {"task_id": "offline/task"}})
        assert req.url.raw_path.endswith(b"offline%2Ftask")
        return httpx.Response(200, json={"state": "success", "creations": [{"url": "https://media.example/video.mp4"}]})
    job = {"runtime": {"active_call": "call"}}
    prompt = "完整批准规范与参考绑定" * 250
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = SimpleNamespace(client=client, store=SimpleNamespace(root=tmp_path), checkpoint=lambda j, **kw: j["runtime"].update(kw))
        for _ in range(2):
            await generate(provider, job, route(model), prompt, [frame], 10)
    submissions = [req for req in requests if req.method == "POST"]
    assert len(submissions) == 1 and len(uploads) == 1
    payload = json.loads(submissions[0].content)
    assert payload["model"] == model and payload["duration"] == 10
    assert payload["prompt"] == prompt
    assert payload["resolution"] == "720p"
    flag, excluded = ("bgm", "audio") if "q2" in model else ("audio", "bgm")
    assert payload[flag] is False and excluded not in payload
    assert "offline-tokenhub-key" not in json.dumps(job)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,duration", [("vidu-video-q2", 17), ("vidu-video-q3-pro", 17), ("vidu-video-q2", 1.5)])
async def test_duration_stops_before_network(model, duration):
    with pytest.raises(ProviderFailure) as caught:
        await generate(None, {}, route(model), "prompt", [], duration)
    assert caught.value.code == "TOKENHUB_DURATION"


@pytest.mark.asyncio
async def test_unknown_submission_is_not_retried(tmp_path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (32, 32)).save(frame)
    with pytest.raises(ProviderFailure) as caught:
        await generate(None, {"runtime": {"active_call": "call", "cloud_submitting": True}}, route(), "prompt", [frame], 5)
    assert caught.value.code == "SUBMISSION_UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("model,ref_count,prompt,rules,code", [
    ("vidu-video-q3-turbo", 1, "文" * 5001, {}, "TOKENHUB_PROMPT_LIMIT"),
    ("vidu-video-q3-turbo", 1, "完整规范", {"poll_protocol": "post_task_get"}, "TOKENHUB_POLL_PROTOCOL"),
    ("vidu-video-q3-turbo", 8, "完整规范", {}, "TOKENHUB_REFERENCE_LIMIT"),
    ("vidu-video-q2-pro", 5, "完整规范", {}, "TOKENHUB_REFERENCE_LIMIT"),
    ("vidu-video-q3-pro", 2, "完整规范", {}, "TOKENHUB_REFERENCE_MODEL"),
    ("vidu-video-q2", 1, "完整规范", {}, "TOKENHUB_IMAGE_MODEL"),
])
async def test_provider_contract_blocks_before_upload(model, ref_count, prompt, rules, code, tmp_path):
    frame = tmp_path / "offline.png"
    Image.new("RGB", (32, 32)).save(frame)
    job = {"runtime": {"active_call": "call"}, "snapshot": {"pipeline_rules": {"vidu": rules}}}
    with pytest.raises(ProviderFailure) as caught:
        await generate(None, job, route(model), prompt, [frame] * ref_count, 5)
    assert caught.value.code == code
    assert not job["runtime"].get("cloud_submitting")


@pytest.mark.asyncio
async def test_known_task_with_oversize_prompt_still_resumes(tmp_path):
    frame = tmp_path / "offline.png"
    Image.new("RGB", (32, 32)).save(frame)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=b"offline video fixture")
        return httpx.Response(200, json={"state": "success", "creations": [{"url": "https://cdn.example/video.mp4"}]})

    job = {"runtime": {"active_call": "call", "tokenhub_task": {"id": "existing", "call": "call"}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = SimpleNamespace(client=client, checkpoint=lambda j, **values: j["runtime"].update(values))
        await generate(provider, job, route(), "文" * 5001, [frame], 5)
    assert [request.method for request in requests] == ["GET", "GET"]


def test_retry_preserves_tokenhub_task(app, client, project):
    from conftest import approve_with_plan

    approved = approve_with_plan(client, project)
    workflow = app.state.workflow
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="failed", error={"code": "TOKENHUB_DOWNLOAD_FAILED", "message": "offline failure"})
    task = {"id": "offline-job", "call": "call"}
    job["runtime"].update(tokenhub_task=task, active_call="call", cloud_submitting=False)
    workflow.store.save_job(job)
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 202
    assert response.json()["id"] == job["id"]
    assert response.json()["runtime"]["tokenhub_task"] == task
