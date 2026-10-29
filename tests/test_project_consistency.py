"""Offline project consistency and repair gates, without external model calls."""

import json
from pathlib import Path

import pytest
from conftest import approve_with_plan, offline_visual_qc

from studio.models import RoutingSettings


def upload(client, p, stage="reference", entity_id="lin", role="general"):
    response = client.post(f"/api/projects/{p['id']}/upload", data={
        "stage": stage, "entity_id": entity_id, "reference_role": role,
    }, files={"file": ("offline.png", (Path(__file__).parent / "fixtures/reference.png").read_bytes(), "image/png")})
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, artifact):
    result = client.post(f"/api/artifacts/{artifact['id']}/review", json={"approved": True})
    assert result.status_code == 200, result.text


def offline_models(monkeypatch, workflow, inspect=None):
    calls = []

    async def generate(kind, job, prompt, refs=(), **kwargs):
        calls.append((kind, prompt, list(refs)))
        if inspect:
            inspect(kind, job, prompt, refs)
        if kind == "image":
            name = kwargs["output"] + ".png"
            (workflow.store.media / name).write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
            return {"file": name, "mime": "image/png", "metadata": {"aspect_matches": True}}
        return {"text": offline_visual_qc([asset["id"] for asset in job["snapshot"]["qc_assets"]])}

    monkeypatch.setattr(workflow.providers, "generate", generate)
    return calls


def test_offline_default_master_is_locked_and_deliberate_rebinding_invalidates_frames(app, client, project):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    original = upload(client, p)
    approve(client, original)
    frame = upload(client, p, "keyframe", "ep1_s01")
    approve(client, frame)
    other = upload(client, p)
    approve(client, other)
    p = w.store.project(p["id"])
    assert w.dependencies(p, "keyframe", "ep1_s01") == [original["id"]]
    assert w.latest(p, "keyframe", "ep1_s01")["id"] == frame["id"]
    endpoint = f"/api/projects/{p['id']}/assets/lin/master"
    assert client.put(endpoint, json={"artifact_id": other["id"], "expected_version": p["version"] - 1}).status_code == 409
    assert client.put(endpoint, json={"artifact_id": other["id"], "expected_version": p["version"]}).status_code == 200
    p = w.store.project(p["id"])
    assert w.latest(p, "keyframe", "ep1_s01") is None
    assert client.put(endpoint, json={"artifact_id": original["id"], "expected_version": p["version"]}).status_code == 200
    assert w.latest(w.store.project(p["id"]), "keyframe", "ep1_s01") is None


@pytest.mark.asyncio
async def test_offline_bootstrap_waits_for_master_confirmation_then_frames_auto_approve(app, client, project, monkeypatch):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    offline_models(monkeypatch, w)
    job = w.enqueue(p["id"], "reference", "lin")
    await w.execute(job)
    original = w.store.artifact(w.store.job(job["id"])["artifact_id"])
    assert original["review"] == "pending"
    assert client.post(f"/api/projects/{p['id']}/jobs", json={"stage": "keyframe", "entity_id": "ep1_s01"}).status_code == 422
    approve(client, original)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    await w.execute(job)
    saved = w.store.job(job["id"])
    assert saved["status"] == "succeeded", saved.get("error")
    frame = w.store.artifact(saved["artifact_id"])
    assert frame["review"] == "approved" and frame["review_method"] == "automatic_visual_qc"
    assert frame["dependencies"] == [original["id"]]
    assert set(frame["metadata"]["visual_qc"]["asset_checks"]) == {"lin"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["missing_asset", "unknown_asset", "missing_check", "skip", "missing_contract"])
async def test_offline_incomplete_asset_or_contract_review_never_approves(app, client, project, monkeypatch, bad):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    original = upload(client, p)
    approve(client, original)
    offline_models(monkeypatch, w)
    generate = w.providers.generate

    async def invalid(kind, *args, **kwargs):
        result = await generate(kind, *args, **kwargs)
        if kind == "vision":
            qc = json.loads(result["text"])
            if bad == "missing_asset":
                qc["asset_checks"].clear()
            elif bad == "unknown_asset":
                qc["asset_checks"]["someone_else"] = qc["asset_checks"]["lin"]
            elif bad == "missing_check":
                del qc["asset_checks"]["lin"]["appearance"]
            elif bad == "skip":
                qc["asset_checks"]["lin"]["identity"]["result"] = "not_applicable"
            else:
                del qc["checks"]["contract"]
            result["text"] = json.dumps(qc)
        return result

    monkeypatch.setattr(w.providers, "generate", invalid)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    await w.execute(job)
    assert w.store.job(job["id"])["error"]["code"] == "VISION_INVALID"
    assert w.latest(w.store.project(p["id"]), "keyframe", "ep1_s01") is None


@pytest.mark.asyncio
async def test_offline_each_asset_failure_repairs_without_changing_masters(app, client, project, monkeypatch):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    plan = p["plan"]
    for kind, id_ in (("prop", "letter"), ("location", "lighthouse")):
        plan["assets"].append({"id": id_, "kind": kind, "name": id_, "description": "offline fixed design", "prompt": "offline reference"})
        for episode in plan["episodes"]:
            episode["shots"][0]["asset_ids"].append(id_)
    w.edit_plan(p["id"], plan, 1)
    w.approve_plan(p["id"], 2)
    masters = []
    for id_ in ("lin", "letter", "lighthouse"):
        item = upload(client, p, entity_id=id_)
        approve(client, item)
        masters.append(item["id"])
    calls = offline_models(monkeypatch, w)
    generate = w.providers.generate
    reviews = 0

    async def inspect(kind, *args, **kwargs):
        nonlocal reviews
        result = await generate(kind, *args, **kwargs)
        if kind == "vision":
            reviews += 1
            if reviews == 1:
                qc = json.loads(result["text"])
                qc["asset_checks"]["letter"]["appearance"] = {"result": "fail", "finding": "offline wrong seal"}
                qc["correction"] = "恢复母版上的红色火漆印"
                result["text"] = json.dumps(qc)
        return result

    monkeypatch.setattr(w.providers, "generate", inspect)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    await w.execute(job)
    saved = w.store.job(job["id"])
    assert saved["status"] == "succeeded", saved.get("error")
    image_calls = [call for call in calls if call[0] == "image"]
    assert len(image_calls) == 2 and "恢复母版上的红色火漆印" in image_calls[1][1]
    assert image_calls[0][2] == image_calls[1][2] and len(image_calls[1][2]) == 3
    assert saved["snapshot"]["dependencies"] == masters
    assert w.store.artifact(saved["artifact_id"])["metadata"]["visual_qc"]["total_attempts"] == 2


@pytest.mark.asyncio
async def test_offline_human_rework_retains_notes_budget_and_total_attempt_limit(app, client, project, monkeypatch):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    original = upload(client, p)
    approve(client, original)
    calls = offline_models(monkeypatch, w)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    for number in (1, 2, 3):
        await w.execute(job)
        saved = w.store.job(job["id"])
        assert saved["status"] == "succeeded", saved.get("error")
        assert w.store.artifact(saved["artifact_id"])["metadata"]["visual_qc"]["total_attempts"] == number
        saved["runtime"].setdefault("qc_cost_reservations", {})[f"offline-{number}"] = 0.1
        w.store.save_job(saved)
        result = client.post(f"/api/artifacts/{saved['artifact_id']}/review", json={"approved": False, "note": f"修正道具位置 {number}"})
        assert result.status_code == 200, result.text
        job = w.store.job(result.json()["rework_job_id"])
        assert job["snapshot"]["rework"]["attempts_used"] == number
        assert len(job["snapshot"]["rework"]["cost_reservations"]) == number
        assert job["snapshot"]["dependencies"] == [original["id"]]
    await w.execute(job)
    assert w.store.job(job["id"])["error"]["code"] == "IMAGE_QC_EXHAUSTED"
    assert len([call for call in calls if call[0] == "image"]) == 3
    assert "修正道具位置 1" in calls[-2][1] and "修正道具位置 2" in calls[-2][1]


def test_offline_contract_revision_clears_master_binding_without_reviving_old_approval(app, client, project):
    p = approve_with_plan(client, project)
    original = upload(client, p)
    approve(client, original)
    w = app.state.workflow
    assert w.store.project(p["id"])["reference_masters"]
    updated = w.update_contract(p["id"], p["contract"]["markdown"] + "\n新服装要求", p["contract"]["revision"])
    assert not updated.get("reference_masters")
    w.approve_contract(p["id"], updated["contract"]["hash"])
    w.approve_plan(p["id"], 1, updated["contract"]["hash"])
    with pytest.raises(ValueError, match="母版"):
        w.snapshot(w.store.project(p["id"]), "keyframe", "ep1_s01")
    with pytest.raises(ValueError, match="旧版本资产原图"):
        w.enqueue(p["id"], "reference", "lin")


@pytest.mark.asyncio
async def test_offline_rework_budget_stops_before_another_paid_submission(app, client, project, monkeypatch):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    w.settings.save(RoutingSettings.model_validate({
        "allow_paid": True, "image_qc_budget_cny": 0.25,
        "image": [{"transport": "cloud", "provider": "cloud", "model": "offline",
                   "base_url": "https://offline.invalid/v1", "qc_max_cost_cny": 0.2, "api_key": "offline"}],
    }))
    original = upload(client, p)
    approve(client, original)
    provider_generate = w.providers.generate
    offline_models(monkeypatch, w)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    await w.execute(job)
    saved = w.store.job(job["id"])
    saved["runtime"]["qc_cost_reservations"] = {"offline-first-image": 0.2}
    w.store.save_job(saved)
    response = client.post(f"/api/artifacts/{saved['artifact_id']}/review", json={"approved": False, "note": "修正场景灯光"})
    rework = w.store.job(response.json()["rework_job_id"])
    monkeypatch.setattr(w.providers, "generate", provider_generate)
    await w.execute(rework)
    assert w.store.job(rework["id"])["error"]["code"] == "QC_BUDGET_EXHAUSTED"
    assert len(w.store.artifacts(p["id"])) == 2
