import json
from pathlib import Path

import httpx
import pytest
from conftest import approve_with_plan

from studio.models import RoutingSettings
from studio.providers import ProviderFailure, Providers, gateway_result_uncertain
from studio.workflow import QC_CHECKS


def mock_offline_visual_pass(monkeypatch, workflow):
    original = workflow.providers.generate

    async def generate(kind, *args, **kwargs):
        if kind == "vision":
            return {"text": json.dumps({
                "checks": {key: {"result": "pass", "finding": "offline mock"} for key in QC_CHECKS},
                "correction": "",
            })}
        return await original(kind, *args, **kwargs)

    monkeypatch.setattr(workflow.providers, "generate", generate)


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
@pytest.mark.parametrize("fallback", [True, False])
async def test_offline_image_records_actual_seedream_fallback(app, client, project, monkeypatch, fallback):
    workflow = app.state.workflow
    mock_offline_visual_pass(monkeypatch, workflow)
    configure(workflow, paid=True)
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()

    def handler(request):
        if request.method == "POST" and request.url.path == "/api/jobs":
            assert request.headers["X-FrameWeave-Paid-Auth-Fallback"] == "allow"
            return httpx.Response(202, json={"id": "offline_image"})
        if request.url.path == "/api/jobs/offline_image":
            return httpx.Response(200, json={
                "status": "succeeded", "images": ["offline.png"],
                **({"fallback_provider": "seedream", "billing": {"model": "doubao-seedream-5-0-flash-260915"}}
                   if fallback else {}),
            })
        if request.url.path == "/images/offline.png":
            return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["status"] == "succeeded"
    route = workflow.store.artifact(saved["artifact_id"])["route"]
    assert route["provider"] == ("seedream" if fallback else "gemini")
    assert route["paid"] is fallback
    if fallback:
        assert route["model"] == "doubao-seedream-5-0-flash-260915"


@pytest.mark.asyncio
async def test_offline_paid_disabled_blocks_hidden_seedream_fallback(app, client, project, monkeypatch):
    workflow = app.state.workflow
    configure(workflow, paid=False)
    approved = approve_with_plan(client, project)
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"configured": True})

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "HIDDEN_PAID_FALLBACK"
    assert "/api/jobs" not in requests


@pytest.mark.asyncio
async def test_offline_reference_regeneration_uploads_original_and_records_binding(
    app, client, project, monkeypatch
):
    workflow = app.state.workflow
    mock_offline_visual_pass(monkeypatch, workflow)
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(
        f"/api/projects/{approved['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", png, "image/png")},
    ).json()
    assert client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True}).status_code == 200
    reference_id = "ref_" + "a" * 32
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/seedream":
            return httpx.Response(200, json={"configured": False})
        if request.url.path == "/api/references":
            assert png in request.content
            assert upload["file"].encode() in request.content
            return httpx.Response(200, json={"data": [{"id": reference_id}]})
        if request.method == "POST" and request.url.path == "/api/jobs":
            assert "X-FrameWeave-Paid-Auth-Fallback" not in request.headers
            payload = json.loads(request.content)
            assert payload["reference_images"] == [reference_id]
            assert upload["id"] in payload["prompt"]
            assert approved["contract"]["markdown"] in payload["prompt"]
            assert "必须以绑定的实际参考原图为主要视觉依据" in payload["prompt"]
            return httpx.Response(202, json={"id": "offline_image"})
        if request.url.path == "/api/jobs/offline_image":
            return httpx.Response(200, json={"status": "succeeded", "images": ["offline.png"]})
        if request.url.path == "/images/offline.png":
            return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved_job = workflow.store.job(job["id"])
    assert saved_job["status"] == "succeeded"
    artifact = workflow.store.artifact(saved_job["artifact_id"])
    assert artifact["dependencies"] == [upload["id"]]
    assert artifact["review"] == "pending"
    assert workflow.store.artifact(upload["id"])["review"] == "approved"
    assert (workflow.store.media / upload["file"]).read_bytes() == png
    assert [request.url.path for request in requests].count("/api/references") == 1
    assert [request.url.path for request in requests].count("/api/jobs") == 1
    next_job = workflow.enqueue(approved["id"], "reference", "lin", force=True)
    assert next_job["snapshot"]["dependencies"] == [upload["id"]]


def test_offline_failed_seedream_requires_verification():
    assert gateway_result_uncertain({
        "status": "failed", "fallback_provider": "seedream", "error": {"code": "seedream_network_error"}
    })
    assert gateway_result_uncertain({"status": "cancelled", "fallback_provider": "seedream"})
    assert not gateway_result_uncertain({"status": "failed", "error": {"code": "accounts_unavailable"}})


@pytest.mark.asyncio
async def test_offline_seedream_failure_is_not_resubmitted(app, client, project, monkeypatch):
    workflow = app.state.workflow
    configure(workflow, paid=True)
    approved = approve_with_plan(client, project)
    submissions = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/api/jobs":
            submissions.append(request)
            return httpx.Response(202, json={"id": "offline_seedream_failure"})
        if request.url.path == "/api/jobs/offline_seedream_failure":
            return httpx.Response(200, json={
                "status": "failed", "fallback_provider": "seedream",
                "error": {"code": "seedream_network_error"}, "attempts": [],
            })
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["error"]["code"] == "REMOTE_RESULT_UNCERTAIN"
    assert saved["runtime"]["remote_id"] == "offline_seedream_failure"
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code != 202
    assert len(submissions) == 1


@pytest.mark.asyncio
async def test_offline_reference_upload_failure_does_not_submit_model_job(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(
        f"/api/projects/{approved['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", png, "image/png")},
    ).json()
    client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True})
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/seedream":
            return httpx.Response(200, json={"configured": False})
        return httpx.Response(503)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["error"]["code"] == "REFERENCE_UPLOAD_FAILED"
    assert "/api/references" in requests and "/api/jobs" not in requests
    assert workflow.store.artifacts(approved["id"])[0]["id"] == upload["id"]


@pytest.mark.parametrize(
    "response_data", [[], [{"id": "invalid"}], [{"id": "ref_" + "a" * 32}, {"id": "ref_" + "a" * 32}]]
)
@pytest.mark.asyncio
async def test_offline_invalid_reference_upload_response_never_submits_text_only(
    app, client, project, monkeypatch, response_data
):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(
        f"/api/projects/{approved['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", png, "image/png")},
    ).json()
    client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True})
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/seedream":
            return httpx.Response(200, json={"configured": False})
        if request.url.path == "/api/references":
            return httpx.Response(200, json={"data": response_data})
        return httpx.Response(500)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "REFERENCE_UPLOAD_FAILED"
    assert "/api/jobs" not in requests


@pytest.mark.asyncio
async def test_offline_auth_failure_keeps_gateway_id_and_explains_login_recovery(
    app, client, project, monkeypatch
):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/seedream":
            return httpx.Response(200, json={"configured": False})
        if request.method == "POST" and request.url.path == "/api/jobs":
            return httpx.Response(202, json={"id": "offline_auth_failure"})
        if request.url.path == "/api/jobs/offline_auth_failure":
            return httpx.Response(
                200,
                json={"status": "failed", "error": {"code": "authentication_failed"}},
            )
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["error"]["code"] == "authentication_failed"
    assert "更新登录信息" in saved["error"]["message"]
    assert saved["runtime"]["remote_id"] == "offline_auth_failure"
    retry = client.post(f"/api/jobs/{job['id']}/retry")
    assert retry.status_code == 202, retry.text
    assert retry.json()["retry_of"] == job["id"]
    assert len([request for request in requests if request.method == "POST"]) == 1


def test_offline_retry_without_remote_id_keeps_reference_binding_and_successor(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(
        f"/api/projects/{approved['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", png, "image/png")},
    ).json()
    client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True})
    original = workflow.enqueue(approved["id"], "reference", "lin")
    original.update(status="blocked", error={"code": "REFERENCE_UPLOAD_FAILED", "message": "离线测试"})
    workflow.store.save_job(original)
    response = client.post(f"/api/jobs/{original['id']}/retry")
    assert response.status_code == 202, response.text
    retry = response.json()
    assert retry["retry_of"] == original["id"]
    assert retry["snapshot"]["dependencies"] == [upload["id"]]
    assert client.post(f"/api/jobs/{original['id']}/retry").json()["id"] == retry["id"]
    retry["status"] = "succeeded"
    workflow.store.save_job(retry)
    candidate = dict(upload, id="offline_candidate", route={"provider": "gemini"}, review="pending")
    workflow.store.save_artifact(candidate)
    assert client.post(f"/api/jobs/{original['id']}/retry").json()["id"] == retry["id"]
    assert len([job for job in workflow.store.jobs(approved["id"]) if job.get("retry_of")]) == 1


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


@pytest.mark.asyncio
async def test_offline_fixture_failed_gateway_job_retries_with_new_submission(
    app, client, project, monkeypatch
):
    from pathlib import Path

    workflow = app.state.workflow
    mock_offline_visual_pass(monkeypatch, workflow)
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    submissions = []

    def handler(request):
        if request.url.path == "/api/seedream":
            return httpx.Response(200, json={"configured": False})
        if request.method == "POST" and request.url.path == "/api/jobs":
            submissions.append(request.headers["Idempotency-Key"])
            return httpx.Response(202, json={"id": f"offline_image_{len(submissions)}"})
        if request.url.path == "/api/jobs/offline_image_1":
            return httpx.Response(
                200,
                json={"status": "failed", "error": {"code": "accounts_unavailable"}, "attempts": []},
            )
        if request.url.path == "/api/jobs/offline_image_2":
            return httpx.Response(200, json={"status": "succeeded", "images": ["offline-reference.png"]})
        if request.url.path == "/images/offline-reference.png":
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    original = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(original)
    original = workflow.store.job(original["id"])
    assert original["status"] == "blocked"
    assert original["runtime"]["remote_id"] == "offline_image_1"
    assert original["error"]["code"] == "accounts_unavailable"

    response = client.post(f"/api/jobs/{original['id']}/retry")
    assert response.status_code == 202, response.text
    retry = response.json()
    assert retry["id"] != original["id"]
    assert retry["retry_of"] == original["id"]
    assert retry["snapshot"] == original["snapshot"]
    assert retry["status"] == "queued"
    assert len(submissions) == 1
    assert client.post(f"/api/jobs/{original['id']}/retry").json()["id"] == retry["id"]
    await workflow.execute(retry)
    assert workflow.store.job(retry["id"])["status"] == "succeeded"
    assert len(submissions) == 2 and submissions[0] != submissions[1]
    assert client.post(f"/api/jobs/{original['id']}/retry").json()["id"] == retry["id"]
    assert len(submissions) == 2
    assert workflow.store.job(original["id"])["status"] == "blocked"
    artifact = workflow.store.artifacts(approved["id"])[0]
    assert artifact["job_id"] == retry["id"] and artifact["review"] == "pending"


@pytest.mark.parametrize("remote_status", ["queued", "waiting", "running", "succeeded"])
def test_offline_fixture_retry_resumes_existing_gateway_job(app, client, project, monkeypatch, remote_status):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="blocked", error={"code": "POLL_TIMEOUT", "message": "离线测试"})
    job["runtime"].update(remote_id="offline_remote", active_call="offline_call", route_index=0)
    workflow.store.save_job(job)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": remote_status})

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 202, response.text
    assert response.json()["id"] == job["id"]
    assert response.json()["runtime"]["remote_id"] == "offline_remote"
    assert response.json()["status"] == "queued"
    assert requests == [("GET", "/api/jobs/offline_remote")]
    assert len(workflow.store.jobs(approved["id"])) == 1


@pytest.mark.parametrize(
    "remote",
    [
        {"status": "failed", "error": {"code": "upstream_timeout"}},
        {"status": "failed", "error": {"code": "service_stopped"}},
        {"status": "failed", "error": {"code": "download_failed"}},
        {
            "status": "failed",
            "error": {"code": "accounts_unavailable"},
            "attempts": [{"result_uncertain": True}],
        },
        {
            "status": "failed",
            "error": {"code": "accounts_unavailable"},
            "attempts": [{"error": {"code": "upstream_timeout"}}],
        },
        {"status": "failed", "error": {"code": "image_generation_failed"}, "images": ["offline-partial.png"]},
    ],
)
def test_offline_fixture_retry_refuses_uncertain_gateway_result(app, client, project, monkeypatch, remote):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="blocked", error={"code": "accounts_unavailable", "message": "离线测试"})
    job["runtime"]["remote_id"] = "offline_remote"
    workflow.store.save_job(job)
    monkeypatch.setattr(
        workflow.providers.client,
        "_transport",
        httpx.MockTransport(lambda request: httpx.Response(200, json=remote)),
    )
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REMOTE_RESULT_UNCERTAIN"
    saved = workflow.store.job(job["id"])
    assert saved["status"] == "blocked" and saved["runtime"]["remote_id"] == "offline_remote"
    assert len(workflow.store.jobs(approved["id"])) == 1
    assert client.get("/api/jobs").json()[0]["verification_required"] is True
    assert client.get(f"/api/projects/{approved['id']}").json()["jobs"][0]["verification_required"] is True
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 422


@pytest.mark.parametrize(
    "gateway_response", [httpx.Response(404), httpx.Response(503), httpx.Response(200, json={})]
)
def test_offline_fixture_retry_refuses_unknown_gateway_state(
    app, client, project, monkeypatch, gateway_response
):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="blocked", error={"code": "POLL_TIMEOUT", "message": "离线测试"})
    job["runtime"]["remote_id"] = "offline_remote"
    workflow.store.save_job(job)
    monkeypatch.setattr(
        workflow.providers.client, "_transport", httpx.MockTransport(lambda request: gateway_response)
    )
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "GATEWAY_STATUS_UNKNOWN"
    assert workflow.store.job(job["id"])["status"] == "blocked"
    assert len(workflow.store.jobs(approved["id"])) == 1


def test_offline_fixture_retry_preserves_completed_text_calls(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "plan")
    job.update(status="blocked", error={"code": "accounts_unavailable", "message": "离线测试"})
    job["runtime"].update(remote_id="offline_remote", calls={"offline_call": {"text": "离线测试文本"}})
    workflow.store.save_job(job)
    monkeypatch.setattr(
        workflow.providers.client,
        "_transport",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "failed", "attempts": []})),
    )
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 202, response.text
    assert response.json()["id"] != job["id"]
    assert response.json()["runtime"]["calls"] == {"offline_call": {"text": "离线测试文本"}}


def test_offline_fixture_retry_rechecks_project_after_gateway_lookup(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    job.update(status="blocked", error={"code": "accounts_unavailable", "message": "离线测试"})
    job["runtime"]["remote_id"] = "offline_remote"
    workflow.store.save_job(job)

    def handler(request):
        current = workflow.store.project(approved["id"])
        current["plan_revision"] += 1
        workflow.store.save_project(current, current["version"])
        return httpx.Response(200, json={"status": "failed"})

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    response = client.post(f"/api/jobs/{job['id']}/retry")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STALE_INPUT"
    assert workflow.store.job(job["id"])["status"] == "blocked"
    assert len(workflow.store.jobs(approved["id"])) == 1
