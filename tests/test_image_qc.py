import json
from pathlib import Path

import httpx
import pytest
from conftest import approve_with_plan

from studio.models import RoutingSettings
from studio.providers import ProviderFailure
from studio.store import uid
from studio.workflow import QC_CHECKS


def qc(result="pass", correction=""):
    return json.dumps({
        "checks": {key: {"result": result, "finding": "offline fixture"} for key in QC_CHECKS},
        "correction": correction,
    })


@pytest.mark.asyncio
async def test_offline_qc_repairs_then_only_saves_passing_candidate(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    prompts, vision_inputs = [], []

    async def generate(kind, job, prompt, refs=(), **kwargs):
        if kind == "image":
            prompts.append(prompt)
            name = kwargs["output"] + ".png"
            (workflow.store.media / name).write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
            return {"file": name, "mime": "image/png", "metadata": {"aspect_matches": True}, "route": {"provider": "offline"}}
        vision_inputs.append([path.name for path in refs])
        return {"text": qc("fail", "修正左手持物") if len(vision_inputs) == 1 else qc()}

    monkeypatch.setattr(workflow.providers, "generate", generate)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["status"] == "succeeded"
    assert len(prompts) == 2 and "修正左手持物" in prompts[1]
    assert '"identity"' in prompts[1] and "offline fixture" in prompts[1]
    assert approved["contract"]["markdown"] in prompts[0] and approved["contract"]["markdown"] in prompts[1]
    assert len(vision_inputs) == 2 and vision_inputs[0][-1] != vision_inputs[1][-1]
    assert len(workflow.store.artifacts(approved["id"])) == 1
    artifact = workflow.store.artifact(saved["artifact_id"])
    assert artifact["review"] == "pending" and artifact["metadata"]["visual_qc"]["attempt"] == 2
    assert client.get(f"/api/jobs/{job['id']}/image-attempts/1").status_code == 200
    await workflow.execute(saved)
    assert len(prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fail", "uncertain", "malformed", "unavailable"])
async def test_offline_qc_never_exposes_failed_candidate(app, client, project, monkeypatch, outcome):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    prompts = []

    async def generate(kind, job, prompt, refs=(), **kwargs):
        if kind == "image":
            prompts.append(prompt)
            name = kwargs["output"] + ".png"
            (workflow.store.media / name).write_bytes(b"offline candidate")
            return {"file": name, "mime": "image/png", "metadata": {"aspect_matches": True}}
        if outcome == "unavailable":
            raise ProviderFailure("NO_ROUTE", "offline vision unavailable")
        return {"text": "{}" if outcome == "malformed" else qc(outcome, "修正身份")}

    monkeypatch.setattr(workflow.providers, "generate", generate)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    saved = workflow.store.job(job["id"])
    assert saved["status"] == "blocked"
    assert saved["error"]["code"] == {"fail": "IMAGE_QC_EXHAUSTED", "uncertain": "IMAGE_QC_EXHAUSTED", "malformed": "VISION_INVALID", "unavailable": "NO_ROUTE"}[outcome]
    assert len(prompts) == (3 if outcome in {"fail", "uncertain"} else 1)
    assert len(set(prompts)) == len(prompts)
    assert not workflow.store.artifacts(approved["id"])


def test_offline_multi_reference_binding_invalidates_downstream(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    uploads = []
    for _ in range(2):
        item = client.post(f"/api/projects/{approved['id']}/upload", data={"stage": "reference", "entity_id": "lin"}, files={"file": ("reference.png", png, "image/png")}).json()
        assert client.post(f"/api/artifacts/{item['id']}/review", json={"approved": True}).status_code == 200
        uploads.append(item)
    current = client.get(f"/api/projects/{approved['id']}").json()
    response = client.put(f"/api/projects/{approved['id']}/shots/ep1_s01/references", json={"artifact_ids": [uploads[0]["id"]], "expected_version": current["version"]})
    assert response.status_code == 200, response.text
    snapshot = workflow.snapshot(workflow.store.project(approved["id"]), "keyframe", "ep1_s01")
    assert snapshot["dependencies"] == [uploads[1]["id"], uploads[0]["id"]]
    bad = client.put(f"/api/projects/{approved['id']}/shots/ep1_s01/references", json={"artifact_ids": [uid("art")], "expected_version": response.json()["version"]})
    assert bad.status_code == 404


def test_offline_previous_keyframe_dependency_goes_stale(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(f"/api/projects/{approved['id']}/upload", data={"stage": "reference", "entity_id": "lin"}, files={"file": ("reference.png", png, "image/png")}).json()
    assert client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True}).status_code == 200
    previous = client.post(f"/api/projects/{approved['id']}/upload", data={"stage": "keyframe", "entity_id": "ep1_s01"}, files={"file": ("frame.png", png, "image/png")}).json()
    assert client.post(f"/api/artifacts/{previous['id']}/review", json={"approved": True}).status_code == 200
    current = client.get(f"/api/projects/{approved['id']}").json()
    response = client.put(f"/api/projects/{approved['id']}/shots/ep2_s01/references", json={"artifact_ids": [], "previous_shot_id": "ep1_s01", "expected_version": current["version"]})
    assert response.status_code == 200, response.text
    assert workflow.dependencies(workflow.store.project(approved["id"]), "keyframe", "ep2_s01") == [upload["id"], previous["id"]]
    assert client.post(f"/api/artifacts/{previous['id']}/review", json={"approved": False}).status_code == 200
    with pytest.raises(ValueError, match="连续性参考"):
        workflow.dependencies(workflow.store.project(approved["id"]), "keyframe", "ep2_s01")


@pytest.mark.asyncio
async def test_offline_cloud_vision_receives_candidate_and_references(app, client, project, monkeypatch):
    workflow = app.state.workflow
    workflow.settings.save(RoutingSettings.model_validate({"allow_paid": True, "image_qc_budget_cny": 1, "vision": [{"transport": "cloud", "provider": "cloud", "model": "vision", "base_url": "https://vision.example/v1", "api_key": "secret", "qc_max_cost_cny": 0.1}]}))
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    image = workflow.store.media / "fixture.png"
    image.write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": qc()}}]})

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    result = await workflow.providers.generate("vision", job, "offline visual check", [image])
    assert json.loads(result["text"])["checks"]["identity"]["result"] == "pass"
    assert len(requests) == 1 and requests[0].url.host == "vision.example"


@pytest.mark.asyncio
async def test_offline_gateway_vision_uploads_references_in_order(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    images = [workflow.store.media / "reference.png", workflow.store.media / "candidate.png"]
    for image in images:
        image.write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
    reference_ids = ["ref_" + "a" * 32, "ref_" + "b" * 32]

    def handler(request):
        if request.url.path == "/api/references":
            assert request.content.index(b"reference.png") < request.content.index(b"candidate.png")
            return httpx.Response(200, json={"data": [{"id": value} for value in reference_ids]})
        if request.url.path == "/api/chat/jobs":
            assert json.loads(request.content)["reference_images"] == reference_ids
            return httpx.Response(202, json={"id": "offline_vision"})
        if request.url.path == "/api/jobs/offline_vision":
            return httpx.Response(200, json={"status": "succeeded", "text": qc()})
        return httpx.Response(404)

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    result = await workflow.providers.generate("vision", job, "offline visual check", images)
    assert json.loads(result["text"])["checks"]["story"]["result"] == "pass"


@pytest.mark.asyncio
async def test_offline_qc_recovery_keeps_generated_candidate(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    original = workflow.providers.generate
    calls = {"image": 0, "vision": 0}

    async def generate(kind, *args, **kwargs):
        calls[kind] += 1
        if kind == "vision":
            return {"text": "{}" if calls[kind] == 1 else qc()}
        if kind == "image":
            job_arg = args[0]
            cached = job_arg["runtime"].get("test_image")
            if cached:
                return cached
            result = {"file": "offline.png", "mime": "image/png", "metadata": {"aspect_matches": True}}
            (workflow.store.media / "offline.png").write_bytes(b"offline")
            job_arg["runtime"]["test_image"] = result
            return result
        return await original(kind, *args, **kwargs)

    monkeypatch.setattr(workflow.providers, "generate", generate)
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "VISION_INVALID"
    assert client.get(f"/api/jobs/{job['id']}/image-attempts/1").status_code == 200
    resumed = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert resumed["id"] == job["id"]
    await workflow.execute(resumed)
    assert workflow.store.job(job["id"])["status"] == "succeeded"
    assert calls == {"image": 2, "vision": 2}


@pytest.mark.asyncio
async def test_offline_budget_blocks_paid_requests_before_submission(app, client, project, monkeypatch):
    workflow = app.state.workflow
    workflow.settings.save(RoutingSettings.model_validate({
        "allow_paid": True,
        "image_qc_budget_cny": 0.15,
        "vision": [{"transport": "cloud", "provider": "cloud", "model": "vision", "base_url": "https://vision.example/v1", "api_key": "offline", "qc_max_cost_cny": 0.1}],
    }))
    approved = approve_with_plan(client, project)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    image = workflow.store.media / "fixture.png"
    image.write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": qc()}}]})

    monkeypatch.setattr(workflow.providers.client, "_transport", httpx.MockTransport(handler))
    await workflow.providers.generate("vision", job, "offline check 1", [image])
    await workflow.providers.generate("vision", job, "offline check 1", [image])
    with pytest.raises(ProviderFailure, match="预算不足"):
        await workflow.providers.generate("vision", job, "offline check 2", [image])
    assert len(requests) == 1
    assert list(job["runtime"]["qc_cost_reservations"].values()) == [0.1]


def test_offline_reference_role_validation_and_approval_revocation(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    path = f"/api/projects/{approved['id']}/upload"
    invalid = client.post(path, data={"stage": "reference", "entity_id": "lin", "reference_role": "wide"}, files={"file": ("reference.png", png, "image/png")})
    assert invalid.status_code == 422
    source = client.post(path, data={"stage": "reference", "entity_id": "lin", "reference_role": "front"}, files={"file": ("reference.png", png, "image/png")}).json()
    assert source["metadata"]["reference_role"] == "front"
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    keyframe = client.post(path, data={"stage": "keyframe", "entity_id": "ep1_s01"}, files={"file": ("frame.png", png, "image/png")}).json()
    assert client.post(f"/api/artifacts/{keyframe['id']}/review", json={"approved": True}).status_code == 200
    p = workflow.store.project(approved["id"])
    assert workflow.latest(p, "keyframe", "ep1_s01") is not None
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": False}).status_code == 200
    assert workflow.latest(p, "keyframe", "ep1_s01") is None
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    assert workflow.latest(p, "keyframe", "ep1_s01") is None


def test_offline_reclassify_invalidates_even_after_reapproval(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    png = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    path = f"/api/projects/{approved['id']}/upload"
    source = client.post(path, data={"stage": "reference", "entity_id": "lin"}, files={"file": ("reference.png", png, "image/png")}).json()
    client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True})
    frame = client.post(path, data={"stage": "keyframe", "entity_id": "ep1_s01"}, files={"file": ("frame.png", png, "image/png")}).json()
    client.post(f"/api/artifacts/{frame['id']}/review", json={"approved": True})
    changed = client.put(f"/api/artifacts/{source['id']}/reference-role", json={"role": "side"})
    assert changed.status_code == 200 and changed.json()["review"] == "pending"
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    assert workflow.latest(workflow.store.project(approved["id"]), "keyframe", "ep1_s01") is None


@pytest.mark.asyncio
async def test_offline_required_identity_cannot_be_marked_not_applicable(app, client, project, monkeypatch):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)

    async def generate(kind, job, prompt, refs=(), **kwargs):
        if kind == "image":
            name = kwargs["output"] + ".png"
            (workflow.store.media / name).write_bytes(b"offline")
            return {"file": name, "mime": "image/png", "metadata": {"aspect_matches": True}}
        result = json.loads(qc())
        result["checks"]["identity"] = {"result": "not_applicable", "finding": "offline"}
        return {"text": json.dumps(result)}

    monkeypatch.setattr(workflow.providers, "generate", generate)
    job = workflow.enqueue(approved["id"], "reference", "lin")
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "VISION_INVALID"
    assert not workflow.store.artifacts(approved["id"])
