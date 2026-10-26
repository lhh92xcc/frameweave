import io
import json
import zipfile

import pytest
from conftest import SOURCE, approve_with_plan, plan_for

from studio.store import digest, now, uid


def test_source_chapters_use_upstream(client, project):
    assert len(project["chapters"]) == 2
    assert project["chapters"][0]["chapter_number"] == 1
    assert "机械鸟" in project["chapters"][1]["content"]


def test_all_six_modes_have_specific_contracts(client):
    templates = client.get("/api/templates").json()
    assert len(templates) == 6
    for t in templates:
        result = client.post(
            "/api/projects", json={"title": t["name"], "source": SOURCE, "mode": t["id"]}
        ).json()
        assert t["rules"] in result["contract"]["markdown"]
        assert not result["contract"]["approved"]


def test_contract_approval_and_stale_revision(client, project):
    id_ = project["id"]
    assert client.post(f"/api/projects/{id_}/jobs", json={"stage": "plan"}).status_code == 422
    assert client.post(f"/api/projects/{id_}/contract/approve", json={"hash": "wrong"}).status_code == 409
    old = project["contract"]
    new = client.put(
        f"/api/projects/{id_}/contract",
        json={"markdown": old["markdown"] + "\n禁止戴帽子。", "expected_revision": 0},
    )
    assert new.status_code == 200
    assert new.json()["contract"]["revision"] == 1
    assert not new.json()["contract"]["approved"]
    assert (
        client.put(
            f"/api/projects/{id_}/contract", json={"markdown": old["markdown"], "expected_revision": 0}
        ).status_code
        == 409
    )
    assert client.post(f"/api/projects/{id_}/contract/approve", json={"hash": old["hash"]}).status_code == 409


def test_plan_validation_covers_duration_chapters_and_bindings(client, project):
    id_ = project["id"]
    client.post(f"/api/projects/{id_}/contract/approve", json={"hash": project["contract"]["hash"]})
    plan = plan_for(project)
    plan["episodes"][0]["shots"][0]["duration"] = 5
    assert (
        client.put(f"/api/projects/{id_}/plan", json={"plan": plan, "expected_revision": 0}).status_code
        == 422
    )
    plan = plan_for(project)
    plan["episodes"][1]["source_chapters"] = [1]
    assert (
        client.put(f"/api/projects/{id_}/plan", json={"plan": plan, "expected_revision": 0}).status_code
        == 422
    )
    plan = plan_for(project)
    plan["episodes"][0]["shots"][0]["asset_ids"] = ["missing"]
    assert (
        client.put(f"/api/projects/{id_}/plan", json={"plan": plan, "expected_revision": 0}).status_code
        == 422
    )


def add_artifact(workflow, p, kind, entity_id, deps=(), review="pending"):
    value = {
        "id": uid("art"),
        "project_id": p["id"],
        "job_id": "fixture",
        "entity_id": entity_id,
        "kind": kind,
        "file": "test.png",
        "mime": "image/png",
        "contract_hash": p["contract"]["hash"],
        "contract_revision": p["contract"]["revision"],
        "plan_revision": p["plan_revision"],
        "dependencies": list(deps),
        "review": review,
        "created_at": now(),
    }
    workflow.store.save_artifact(value)
    return value


def test_review_dependencies_and_new_reference_invalidates_downstream(app, client, project):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    assert (
        client.post(
            f"/api/projects/{p['id']}/jobs", json={"stage": "keyframe", "entity_id": "ep1_s01"}
        ).status_code
        == 422
    )
    ref = add_artifact(w, p, "reference", "lin")
    assert (
        client.post(
            f"/api/projects/{p['id']}/jobs", json={"stage": "keyframe", "entity_id": "ep1_s01"}
        ).status_code
        == 422
    )
    assert client.post(f"/api/artifacts/{ref['id']}/review", json={"approved": True}).status_code == 200
    keyframe = add_artifact(w, p, "keyframe", "ep1_s01", [ref["id"]], "approved")
    video = add_artifact(w, p, "video", "ep1_s01", [keyframe["id"]], "approved")
    assert w.latest(p, "video", "ep1_s01")["id"] == video["id"]
    add_artifact(w, p, "reference", "lin", review="pending")
    assert w.latest(p, "keyframe", "ep1_s01") is None
    assert w.latest(p, "video", "ep1_s01") is None
    assert client.post(f"/api/artifacts/{video['id']}/review", json={"approved": True}).status_code == 409


def test_job_idempotency_and_paused_projects(app, client, project):
    id_ = project["id"]
    a = client.post(f"/api/projects/{id_}/jobs", json={"stage": "requirements"}).json()
    b = client.post(f"/api/projects/{id_}/jobs", json={"stage": "requirements"}).json()
    assert a["id"] == b["id"]
    client.post(f"/api/projects/{id_}/pause")
    assert client.post(f"/api/projects/{id_}/jobs", json={"stage": "requirements"}).status_code == 422
    client.post(f"/api/projects/{id_}/resume")
    assert client.post(f"/api/jobs/{a['id']}/cancel").json()["status"] == "cancelled"


def test_saved_settings_are_write_only_and_origin_scoped(client, app):
    data = client.get("/api/settings").json()
    data["text"] = [
        {
            "transport": "cloud",
            "provider": "cloud",
            "model": "test",
            "base_url": "https://model.example/v1",
            "api_key": "test-secret-never-return",
        }
    ]
    for kind in ["image", "video"]:
        for r in data[kind]:
            r.pop("configured", None)
    data["audio"].pop("configured", None)
    response = client.put("/api/settings", json=data)
    assert response.status_code == 200, response.text
    assert "test-secret-never-return" not in response.text
    assert "test-secret-never-return" not in client.get("/api/settings").text
    assert (app.state.store.root / "settings.json").stat().st_mode & 0o777 == 0o600
    keep = response.json()
    for kind in ["text", "image", "video", "audio"]:
        for route in [keep[kind]] if kind == "audio" else keep[kind]:
            route.pop("configured", None)
    assert client.put("/api/settings", json=keep).status_code == 200
    assert app.state.workflow.settings.get().text[0].api_key == "test-secret-never-return"
    assert (
        client.put("/api/settings", json=keep, headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )


def test_prompt_exact_contract_and_export_no_settings(app, client, project):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    job = w.enqueue(p["id"], "reference", "lin")
    prompt = w.prompt(job["snapshot"], "test task")
    assert p["contract"]["markdown"] in prompt
    assert digest(p["contract"]["markdown"]) in prompt
    assert "api_key" not in json.dumps(job["snapshot"])
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/projects/{p['id']}/export.zip").content))
    assert archive.read("source.txt").decode() == SOURCE
    assert archive.read("production-requirements.md").decode() == p["contract"]["markdown"]
    assert "settings.json" not in archive.namelist()


@pytest.mark.asyncio
async def test_restart_recovery_preserves_remote_job_and_blocks_unknown_cloud(app, client, project):
    w = app.state.workflow
    a = w.enqueue(project["id"], "requirements")
    a.update(status="running", runtime={"remote_id": "remote-123", "route_index": 0})
    w.store.save_job(a)
    b = w.store.enqueue(project["id"], "requirements", "other", {"x": 1})
    b.update(status="running", runtime={"cloud_submitting": True})
    w.store.save_job(b)
    # Paused project keeps recovered jobs from executing while states are inspected.
    p = w.store.project(project["id"])
    p["paused"] = True
    w.store.save_project(p)
    await w.start()
    assert w.store.job(a["id"])["status"] == "queued"
    assert w.store.job(a["id"])["runtime"]["remote_id"] == "remote-123"
    assert w.store.job(b["id"])["status"] == "blocked"
    await w.close()


@pytest.mark.asyncio
async def test_model_protocol_executes_requirements_and_repairs_storyboard(app, client, project, monkeypatch):
    """Run real orchestration with deterministic model responses, without model spend."""
    w = app.state.workflow
    prompts = []
    plan = plan_for(project)
    outline = {
        "assets": plan["assets"],
        "episodes": [{k: v for k, v in e.items() if k != "shots"} for e in plan["episodes"]],
    }
    invalid = json.loads(json.dumps({"shots": plan["episodes"][0]["shots"]}))
    invalid["shots"][0]["duration"] = 9
    responses = iter(
        [
            "## 人物与场景\n"
            + "林夏始终穿深蓝工装，机械鸟沿信件线索飞向灯塔；保留两章因果与空间连续性。" * 5,
            json.dumps(outline, ensure_ascii=False),
            json.dumps(invalid, ensure_ascii=False),
            json.dumps({"shots": plan["episodes"][0]["shots"]}, ensure_ascii=False),
            json.dumps({"shots": plan["episodes"][1]["shots"]}, ensure_ascii=False),
        ]
    )

    async def generate(kind, job, prompt, **kwargs):
        assert kind == "text"
        assert job["snapshot"]["contract"]["markdown"] in prompt
        prompts.append(prompt)
        return {"text": next(responses)}

    monkeypatch.setattr(w.providers, "generate", generate)
    requirements = w.enqueue(project["id"], "requirements")
    await w.execute(requirements)
    assert w.store.job(requirements["id"])["status"] == "succeeded"
    p = w.store.project(project["id"])
    assert "创作 Bible" in p["contract"]["markdown"]
    assert not p["contract"]["approved"]
    w.approve_contract(p["id"], p["contract"]["hash"])
    job = w.enqueue(p["id"], "plan")
    await w.execute(job)
    assert w.store.job(job["id"])["status"] == "succeeded"
    p = w.store.project(p["id"])
    assert p["plan"] == plan
    assert not p["plan_approved"]
    assert "上次输出未通过结构校验" in prompts[3]
    assert all("SHA-256" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_long_source_reads_every_segment_then_reduces_without_truncation(app, client, monkeypatch):
    w = app.state.workflow
    source = "第一章 长篇\n" + "前半内容" * 3000 + "末段必须保留的线索"
    p = client.post("/api/projects", json={"title": "长篇归并", "source": source}).json()
    job = w.enqueue(p["id"], "requirements")
    leaf_sources, reductions = [], []

    async def text(job, prompt):
        assert p["contract"]["markdown"] in prompt
        if "<source>" in prompt:
            leaf_sources.append(prompt.split("<source>\n", 1)[1].split("\n</source>", 1)[0])
            # Force a reduction round to exercise hierarchy even if a model ignores its limit.
            return "摘要内容" * 2000
        reductions.append(prompt)
        return "章节 1：完整因果和末段线索。"

    monkeypatch.setattr(w, "text", text)
    result = await w.source_digest(job)
    assert "".join(leaf_sources) == p["chapters"][0]["content"]
    assert "末段必须保留的线索" in leaf_sources[-1]
    assert reductions and len(result) < 12000


def test_uploaded_reference_passes_real_api_route_and_review(client, project):
    from pathlib import Path

    p = approve_with_plan(client, project)
    response = client.post(
        f"/api/projects/{p['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={
            "file": (
                "reference.png",
                (Path(__file__).parent / "fixtures/reference.png").read_bytes(),
                "image/png",
            )
        },
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["review"] == "pending"
    assert artifact["metadata"]["width"] == 160
    assert client.post(f"/api/artifacts/{artifact['id']}/review", json={"approved": True}).status_code == 200
    assert client.get(f"/api/artifacts/{artifact['id']}/content").content.startswith(b"\x89PNG")
