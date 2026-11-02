"""Offline mocked Tencent responses; no external generation or paid requests."""
import json
from types import SimpleNamespace

import httpx
import pytest

from studio.models import Route, RoutingSettings
from studio.providers import ProviderFailure
from studio.settings import SettingsStore
from studio.tencent_video import ENDPOINT, generate


def route():
    return Route(transport="cloud", provider="tencent-vidu", model="viduq3-pro", base_url=ENDPOINT, api_key="offline-id:offline-secret")


@pytest.mark.asyncio
async def test_resume_and_credentials_isolated(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"offline fixture image")
    submissions = []
    downloads = []
    job = {"runtime": {"active_call": "call"}}
    def handle(req):
        if req.url.host == "media.example":
            downloads.append(req)
            assert "authorization" not in req.headers
            return httpx.Response(200, content=b"offline fixture video")
        action = req.headers["x-tc-action"]
        assert "TC3-HMAC-SHA256" in req.headers["authorization"]
        if action.startswith("Submit"):
            submissions.append(json.loads(req.content))
            return httpx.Response(200, json={"Response": {"JobId": "offline-job"}})
        return httpx.Response(200, json={"Response": {"Status": "DONE", "ResultVideoUrl": "https://media.example/clip.mp4"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = SimpleNamespace(client=client, checkpoint=lambda j, **kw: j["runtime"].update(kw))
        for _ in range(2):
            result = await generate(provider, job, route(), "complete approved prompt", [image], 5)
            assert result["mime"] == "video/mp4"
    assert len(submissions) == 1 and len(downloads) == 2
    assert submissions[0]["Prompt"] == "complete approved prompt"
    assert submissions[0]["Audio"] is False
    assert submissions[0]["IsRec"] is False


@pytest.mark.asyncio
async def test_unknown_submission_never_resubmitted(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"offline")
    requests = []
    def handle(req):
        requests.append(req)
        raise httpx.ReadTimeout("offline timeout", request=req)
    job = {"runtime": {"active_call": "call"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = SimpleNamespace(client=client, checkpoint=lambda j, **kw: j["runtime"].update(kw))
        with pytest.raises(ProviderFailure, match="腾讯云请求"):
            await generate(provider, job, route(), "prompt", [image], 5)
        with pytest.raises(ProviderFailure, match="上次提交结果不明"):
            await generate(provider, job, route(), "prompt", [image], 5)
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,refs,duration,code", [("x" * 2001, [1], 5, "TENCENT_PROMPT_LIMIT"), ("ok", [1, 2], 5, "TENCENT_REFERENCES"), ("ok", [1], 17, "TENCENT_DURATION")])
async def test_validation_before_submission(prompt, refs, duration, code):
    with pytest.raises(ProviderFailure) as caught:
        await generate(None, {"runtime": {}}, route(), prompt, refs, duration)
    assert caught.value.code == code


def test_credentials_private_and_preserved(tmp_path):
    store = SettingsStore(tmp_path)
    config = RoutingSettings(video=[route()])
    public = store.save(config)
    assert "offline-secret" not in json.dumps(public)
    assert "offline-id" not in json.dumps(public)
    assert public["video"][0]["configured"]
    config.video[0].api_key = None
    store.save(config)
    assert store.get().video[0].api_key == "offline-id:offline-secret"


def test_retry_resumes_saved_tencent_task(app, client, project):
    from conftest import approve_with_plan

    approved = approve_with_plan(client, project)
    workflow = app.state.workflow
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="failed", error={"code": "TENCENT_DOWNLOAD_FAILED", "message": "offline failure"})
    task = {"id": "offline-job", "call": "call"}
    job["runtime"].update(tencent_task=task, active_call="call", cloud_submitting=False)
    workflow.store.save_job(job)
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 202
    assert response.json()["id"] == job["id"]
    assert response.json()["runtime"]["tencent_task"] == task
