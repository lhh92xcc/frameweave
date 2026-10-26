import json

import httpx
import pytest
from conftest import approve_with_plan

from studio.models import RoutingSettings
from studio.providers import ProviderFailure, Providers


def configure(w, paid=True):
    w.settings.save(
        RoutingSettings.model_validate(
            {
                "allow_paid": paid,
                "text": [
                    {"provider": "gemini", "model": "gemini-pro"},
                    {
                        "transport": "cloud",
                        "provider": "cloud",
                        "model": "test",
                        "base_url": "https://cloud.example/v1",
                        "api_key": "test-only-key",
                    },
                ],
            }
        )
    )


@pytest.mark.asyncio
async def test_web_quota_falls_back_only_when_paid_enabled(app, client, project):
    w = app.state.workflow
    p = approve_with_plan(client, project)
    configure(w, True)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/chat/jobs":
            return httpx.Response(429, json={"error": {"code": "quota_exhausted"}})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "cloud output"}}]})
        return httpx.Response(200, json={"data": []})

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "plan")
    result = await provider.generate("text", job, "exact approved prompt")
    assert result["text"] == "cloud output"
    assert result["route"]["paid"] is True
    assert len([r for r in calls if r.url.host == "cloud.example"]) == 1
    # Different text steps must have distinct remote submissions and completed steps are memoized.
    result = await provider.generate("text", job, "second prompt")
    assert len([r for r in calls if r.url.host == "cloud.example"]) == 2
    result = await provider.generate("text", job, "exact approved prompt")
    assert len([r for r in calls if r.url.host == "cloud.example"]) == 2
    await provider.close()


@pytest.mark.asyncio
async def test_paid_disabled_never_sends_cloud_request(app, client, project):
    w = app.state.workflow
    p = approve_with_plan(client, project)
    configure(w, False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "quota_exhausted"}})

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "plan")
    with pytest.raises(ProviderFailure):
        await provider.generate("text", job, "prompt")
    assert not any(r.url.host == "cloud.example" for r in calls)
    await provider.close()


@pytest.mark.asyncio
async def test_unknown_cloud_submission_is_not_retried(app, client, project):
    w = app.state.workflow
    p = approve_with_plan(client, project)
    w.settings.save(
        RoutingSettings.model_validate(
            {
                "allow_paid": True,
                "text": [
                    {
                        "transport": "cloud",
                        "provider": "cloud",
                        "model": "test",
                        "base_url": "https://cloud.example/v1",
                        "api_key": "test-key",
                    },
                    {
                        "transport": "cloud",
                        "provider": "fallback",
                        "model": "test",
                        "base_url": "https://fallback.example/v1",
                        "api_key": "test-key",
                    },
                ],
            }
        )
    )
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown submission")

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "plan")
    with pytest.raises(ProviderFailure, match="结果不明"):
        await provider.generate("text", job, "prompt")
    assert len(calls) == 1
    with pytest.raises(ProviderFailure):
        await provider.generate("text", job, "prompt")
    assert len(calls) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_remote_polling_resumes_without_reposting(app, client, project):
    w = app.state.workflow
    p = approve_with_plan(client, project)
    posts = []
    keys = []
    counter = 0

    def handler(request):
        nonlocal counter
        if request.method == "POST":
            posts.append(json.loads(request.content))
            keys.append(request.headers["Idempotency-Key"])
            counter += 1
            return httpx.Response(202, json={"id": f"chat_{counter}"})
        return httpx.Response(
            200, json={"status": "succeeded", "text": "text " + request.url.path.split("_")[-1]}
        )

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "plan")
    assert (await provider.generate("text", job, "first prompt"))["text"] == "text 1"
    assert (await provider.generate("text", job, "second prompt"))["text"] == "text 2"
    assert len(posts) == 2
    assert keys[0] != keys[1]
    assert posts[0]["messages"][0]["content"] == "first prompt"
    await provider.close()


@pytest.mark.asyncio
async def test_completed_cloud_receipt_reprocesses_without_resubmission(app, client, project):
    w = app.state.workflow
    p = approve_with_plan(client, project)
    w.settings.save(
        RoutingSettings.model_validate(
            {
                "allow_paid": True,
                "text": [
                    {
                        "transport": "cloud",
                        "provider": "cloud",
                        "model": "test",
                        "base_url": "https://cloud.example/v1",
                        "api_key": "test-key",
                    }
                ],
            }
        )
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "saved response"}}]})

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "plan")
    assert (await provider.generate("text", job, "prompt"))["text"] == "saved response"
    job["runtime"]["calls"] = {}
    assert (await provider.generate("text", job, "prompt"))["text"] == "saved response"
    assert len(calls) == 1
    await provider.close()


@pytest.mark.asyncio
async def test_retry_api_reuses_cloud_receipt_and_never_sends_key_to_cdn(app, client, project):
    from pathlib import Path

    w = app.state.workflow
    p = approve_with_plan(client, project)
    w.settings.save(
        RoutingSettings.model_validate(
            {
                "allow_paid": True,
                "image": [
                    {
                        "transport": "cloud",
                        "provider": "cloud",
                        "model": "test",
                        "base_url": "https://cloud.example/v1",
                        "api_key": "test-only-secret",
                    }
                ],
            }
        )
    )
    calls, downloads = [], []
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()

    def handler(request):
        if request.method == "POST":
            calls.append(request)
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/result.png"}]})
        downloads.append(request)
        assert "authorization" not in request.headers
        if len(downloads) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})

    provider = Providers(w.store, w.settings, transport=httpx.MockTransport(handler))
    job = w.enqueue(p["id"], "reference", "lin")
    prompt = w.prompt(job["snapshot"], "reference test")
    with pytest.raises(ProviderFailure, match="结果下载失败"):
        await provider.generate("image", job, prompt, output="art_test")
    job["status"] = "blocked"
    w.store.save_job(job)
    retried = client.post(f"/api/jobs/{job['id']}/retry")
    assert retried.status_code == 202, retried.text
    assert retried.json()["id"] == job["id"]
    result = await provider.generate("image", retried.json(), prompt, output="art_test")
    assert len(calls) == 1 and len(downloads) == 2
    assert (w.store.media / result["file"]).read_bytes() == png
    await provider.close()
