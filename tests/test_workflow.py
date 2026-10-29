import io
import json
import zipfile
from pathlib import Path

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
    # Actual local bytes are required for immutable reference manifests.
    (workflow.store.media / "test.png").write_bytes((Path(__file__).parent / "fixtures/reference.png").read_bytes())
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
        "dependency_versions": workflow.dependency_versions(deps),
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
    replacement = add_artifact(w, p, "reference", "lin", review="approved")
    current = w.store.project(p["id"])
    # Candidates do not redefine identity; selecting a new approved original does.
    assert w.latest(current, "keyframe", "ep1_s01")["id"] == keyframe["id"]
    w.bind_master(p["id"], "lin", replacement["id"], current["version"])
    p = w.store.project(p["id"])
    assert w.latest(p, "keyframe", "ep1_s01") is None
    assert w.latest(p, "video", "ep1_s01") is None
    assert client.post(f"/api/artifacts/{video['id']}/review", json={"approved": True}).status_code == 409


def test_review_expected_pending_prevents_overwriting_concurrent_decisions(app, client, project):
    approved = approve_with_plan(client, project)
    workflow = app.state.workflow
    reference = add_artifact(workflow, approved, "reference", "lin")
    url = f"/api/artifacts/{reference['id']}/review"
    first = client.post(url, json={"approved": False, "note": "修改眼睛", "expected_review": "pending"})
    assert first.status_code == 200 and first.json()["review"] == "rejected"
    concurrent = client.post(url, json={"approved": True, "expected_review": "pending"})
    assert concurrent.status_code == 409
    assert workflow.store.artifact(reference["id"])["review_note"] == "修改眼睛"
    assert client.post(url, json={"approved": True}).status_code == 200


def test_review_expected_pending_does_not_bypass_version_or_approval_guards(app, client, project):
    workflow = app.state.workflow
    approved = approve_with_plan(client, project)
    old = add_artifact(workflow, approved, "reference", "lin")
    current = add_artifact(workflow, approved, "reference", "lin")
    body = {"approved": True, "expected_review": "pending"}
    assert client.post(f"/api/artifacts/{old['id']}/review", json=body).status_code == 409
    approved["plan_approved"] = False
    workflow.store.save_project(approved)
    assert client.post(f"/api/artifacts/{current['id']}/review", json=body).status_code == 422
    assert workflow.store.artifact(current["id"])["review"] == "pending"


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
    for kind in ["image", "vision", "video"]:
        for r in data[kind]:
            r.pop("configured", None)
    data["audio"].pop("configured", None)
    response = client.put("/api/settings", json=data)
    assert response.status_code == 200, response.text
    assert "test-secret-never-return" not in response.text
    assert "test-secret-never-return" not in client.get("/api/settings").text
    assert (app.state.store.root / "settings.json").stat().st_mode & 0o777 == 0o600
    keep = response.json()
    for kind in ["text", "image", "vision", "video", "audio"]:
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


def test_offline_reference_source_binding_validation_and_version_guards(app, client, project):
    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    raw = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    upload = client.post(
        f"/api/projects/{p['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", raw, "image/png")},
    ).json()
    request = {"stage": "reference", "entity_id": "lin", "force": True}
    assert client.post(f"/api/projects/{p['id']}/jobs", json=request).status_code == 422
    assert client.post(f"/api/artifacts/{upload['id']}/review", json={"approved": True}).status_code == 200
    response = client.post(f"/api/projects/{p['id']}/jobs", json=request)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["snapshot"]["dependencies"] == [upload["id"]]
    assert job["snapshot"]["reference_base_id"] == upload["id"]
    assert client.post(f"/api/projects/{p['id']}/jobs", json=request).json()["id"] == job["id"]

    wrong_entity = add_artifact(workflow, p, "reference", "other", review="approved")
    wrong_project = add_artifact(
        workflow, {**p, "id": "another-project"}, "reference", "lin", review="approved"
    )
    for invalid in (wrong_entity, wrong_project, {**upload, "id": "missing"}):
        response = client.post(
            f"/api/projects/{p['id']}/jobs", json={**request, "reference_source_id": invalid["id"]}
        )
        assert response.status_code in {404, 422}
    assert (
        client.post(
            f"/api/projects/{p['id']}/jobs",
            json={"stage": "keyframe", "entity_id": "ep1_s01", "reference_source_id": upload["id"]},
        ).status_code
        == 422
    )

    candidate = add_artifact(workflow, p, "reference", "lin")
    with pytest.raises(Exception, match="当前素材已变化"):
        workflow.assert_current(job)
    new_job = workflow.enqueue(p["id"], "reference", "lin", force=True)
    assert new_job["snapshot"]["dependencies"] == [upload["id"]]
    assert new_job["snapshot"]["reference_base_id"] == candidate["id"]
    changed_source = workflow.store.artifact(upload["id"])
    changed_source["review"] = "rejected"
    workflow.store.save_artifact(changed_source)
    with pytest.raises(Exception, match="参考原图或审核状态已变化"):
        workflow.assert_current(new_job)
    assert client.post(f"/api/projects/{p['id']}/jobs", json=request).status_code == 422

    changed_source["review"] = "approved"
    workflow.store.save_artifact(changed_source)
    updated = workflow.store.project(p["id"])
    updated["plan_revision"] += 1
    workflow.store.save_project(updated)
    with pytest.raises(Exception, match="规范或分镜已变化"):
        workflow.assert_current(new_job)
    assert client.post(f"/api/projects/{p['id']}/jobs", json=request).status_code == 422
    assert (
        client.post(
            f"/api/projects/{p['id']}/jobs", json={**request, "reference_source_id": upload["id"]}
        ).status_code
        == 422
    )


@pytest.mark.asyncio
async def test_offline_reference_job_checks_stale_source_before_model_submission(
    app, client, project, monkeypatch
):
    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    source = add_artifact(workflow, p, "reference", "lin", review="approved")
    job = workflow.enqueue(p["id"], "reference", "lin", reference_source_id=source["id"])
    source["review"] = "rejected"
    workflow.store.save_artifact(source)
    calls = []

    async def generate(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(workflow.providers, "generate", generate)
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "STALE_DEPENDENCY"
    assert calls == []


@pytest.mark.asyncio
async def test_offline_legacy_reference_job_does_not_ignore_newly_uploaded_source(app, client, project):
    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    job = workflow.enqueue(p["id"], "reference", "lin")
    del job["snapshot"]["reference_base_id"]
    workflow.store.save_job(job)
    raw = (Path(__file__).parent / "fixtures/reference.png").read_bytes()
    client.post(
        f"/api/projects/{p['id']}/upload",
        data={"stage": "reference", "entity_id": "lin"},
        files={"file": ("reference.png", raw, "image/png")},
    )
    await workflow.execute(job)
    assert workflow.store.job(job["id"])["error"]["code"] == "STALE_DEPENDENCY"


def test_user_reference_can_be_uploaded_before_approval_but_not_reviewed(client, project):
    plan = plan_for(project)
    result = client.put(
        f"/api/projects/{project['id']}/plan",
        json={"plan": plan, "expected_revision": project["plan_revision"]},
    )
    assert result.status_code == 200, result.text
    response = client.post(
        f"/api/projects/{project['id']}/upload",
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
    assert artifact["route"]["provider"] == "user-upload"
    assert artifact["contract_hash"] == project["contract"]["hash"]
    assert artifact["plan_revision"] == 1
    assert client.get(f"/api/artifacts/{artifact['id']}/content").content.startswith(b"\x89PNG")
    assert (
        client.post(f"/api/artifacts/{artifact['id']}/review", json={"approved": True}).status_code == 422
    )
    for stage, entity in [("reference", "lin"), ("keyframe", "ep1_s01")]:
        assert (
            client.post(
                f"/api/projects/{project['id']}/jobs", json={"stage": stage, "entity_id": entity}
            ).status_code
            == 422
        )
    assert (
        client.post(
            f"/api/projects/{project['id']}/upload",
            data={"stage": "keyframe", "entity_id": "ep1_s01"},
            files={"file": ("reference.png", (Path(__file__).parent / "fixtures/reference.png").read_bytes())},
        ).status_code
        == 422
    )
    current = client.get(f"/api/projects/{project['id']}").json()
    assert current["automation"] is False
    assert current["contract"]["approved"] is False and current["plan_approved"] is False
    assert len(current["jobs"]) == 1 and current["jobs"][0]["status"] == "succeeded"
    imported_job = client.get(f"/api/jobs/{current['jobs'][0]['id']}").json()
    assert imported_job["runtime"]["imported"] is True
    assert imported_job["snapshot"]["contract"]["approved"] is False
    assert client.post(
        f"/api/projects/{project['id']}/contract/approve", json={"hash": project["contract"]["hash"]}
    ).status_code == 200
    assert client.post(f"/api/projects/{project['id']}/plan/approve", json={"revision": 1}).status_code == 200
    assert client.post(f"/api/artifacts/{artifact['id']}/review", json={"approved": True}).status_code == 200
    generated = client.post(
        f"/api/projects/{project['id']}/jobs", json={"stage": "keyframe", "entity_id": "ep1_s01"}
    )
    assert generated.status_code == 202
    assert generated.json()["snapshot"]["contract"]["approved"] is True
    assert generated.json()["snapshot"]["dependencies"] == [artifact["id"]]
    revised = client.put(
        f"/api/projects/{project['id']}/contract",
        json={"markdown": project["contract"]["markdown"] + "\n新版本", "expected_revision": 0},
    )
    assert revised.status_code == 200
    current = client.get(f"/api/projects/{project['id']}").json()
    assert next(item for item in current["artifacts"] if item["id"] == artifact["id"])["stale"]
    assert (
        client.post(
            f"/api/projects/{project['id']}/upload",
            data={"stage": "reference", "entity_id": "lin"},
            files={"file": ("reference.png", (Path(__file__).parent / "fixtures/reference.png").read_bytes())},
        ).status_code
        == 422
    )


async def test_automation_reports_batch_failures_without_automatic_resubmission(app, client, project):
    p = approve_with_plan(client, project)
    plan = plan_for(p)
    plan["assets"].append(
        {
            "id": "bird",
            "name": "机械鸟",
            "kind": "prop",
            "description": "一只精致的黄铜机械鸟",
            "prompt": "黄铜机械鸟的完整参考图",
        }
    )
    edited = client.put(
        f"/api/projects/{p['id']}/plan", json={"plan": plan, "expected_revision": p["plan_revision"]}
    )
    assert edited.status_code == 200, edited.text
    assert client.post(f"/api/projects/{p['id']}/plan/approve", json={"revision": 2}).status_code == 200
    client.post(f"/api/projects/{p['id']}/automation", json={"enabled": True})
    workflow = app.state.workflow
    p = workflow.store.project(p["id"])
    assert workflow.automation_state(p)["state"] == "scheduling"
    await workflow.advance(p)
    jobs = {job["entity_id"]: job for job in workflow.store.jobs(p["id"])}
    assert set(jobs) == {"lin", "bird"}
    assert client.get(f"/api/projects/{p['id']}").json()["automation_status"]["state"] == "queued"

    running = jobs["lin"]
    running["status"] = "running"
    workflow.store.save_job(running)
    workflow.store.event(running["id"], "开始生成")
    workflow.store.event(running["id"], "正在等待网页网关")
    view = client.get(f"/api/projects/{p['id']}").json()
    assert view["automation_status"]["state"] == "running"
    assert view["jobs"][0]["latest_event"]["message"] == "正在等待网页网关"
    assert "snapshot" not in view["jobs"][0]

    running.update(status="blocked", error={"code": "NO_ROUTE", "message": "没有可用模型渠道"})
    workflow.store.save_job(running)
    successful = jobs["bird"]
    successful["status"] = "succeeded"
    workflow.store.save_job(successful)
    add_artifact(workflow, p, "reference", "bird")
    view = client.get(f"/api/projects/{p['id']}").json()
    assert view["jobs"][0]["status"] == "succeeded"
    assert view["automation_status"] == {
        "state": "attention", "stage": "reference", "job_ids": [running["id"]]
    }
    await workflow.advance(p)
    assert len(workflow.store.jobs(p["id"])) == 2

    retried = client.post(f"/api/jobs/{running['id']}/retry")
    assert retried.status_code == 202, retried.text
    assert retried.json()["id"] != running["id"]
    assert client.get(f"/api/projects/{p['id']}").json()["automation_status"]["state"] == "queued"
    retried_job = workflow.store.job(retried.json()["id"])
    retried_job["status"] = "succeeded"
    workflow.store.save_job(retried_job)
    reference = add_artifact(workflow, p, "reference", "lin")
    assert client.get(f"/api/projects/{p['id']}").json()["automation_status"] == {
        "state": "review", "stage": "reference"
    }
    reference["review"] = "rejected"
    workflow.store.save_artifact(reference)
    assert client.get(f"/api/projects/{p['id']}").json()["automation_status"] == {
        "state": "rework", "stage": "reference"
    }


async def test_automation_scheduling_failure_is_visible_safe_and_retryable(app, client, project, monkeypatch):
    workflow = app.state.workflow
    client.post(f"/api/projects/{project['id']}/automation", json={"enabled": True})
    p = workflow.store.project(project["id"])
    original = workflow.enqueue

    def fail_enqueue(*args, **kwargs):
        raise ValueError("请先批准当前制作规范")

    monkeypatch.setattr(workflow, "enqueue", fail_enqueue)
    await workflow.advance_safely(p)
    view = client.get(f"/api/projects/{p['id']}").json()
    assert view["automation_status"] == {
        "state": "error",
        "error": {"code": "SCHEDULING_FAILED", "message": "请先批准当前制作规范"},
    }
    monkeypatch.setattr(workflow, "enqueue", original)
    await workflow.advance_safely(workflow.store.project(p["id"]))
    assert workflow.store.jobs(p["id"]) == []
    client.post(f"/api/projects/{p['id']}/automation", json={"enabled": True})
    await workflow.advance_safely(workflow.store.project(p["id"]))
    assert client.get(f"/api/projects/{p['id']}").json()["automation_status"]["state"] == "queued"


async def test_automation_does_not_expose_unexpected_scheduling_errors(app, client, project, monkeypatch):
    workflow = app.state.workflow
    client.post(f"/api/projects/{project['id']}/automation", json={"enabled": True})

    def fail_enqueue(*args, **kwargs):
        raise ValueError("private-credential-in-validation-error")

    monkeypatch.setattr(workflow, "enqueue", fail_enqueue)
    await workflow.advance_safely(workflow.store.project(project["id"]))
    response = client.get(f"/api/projects/{project['id']}")
    assert response.json()["automation_status"]["state"] == "error"
    assert "private-credential-in-validation-error" not in response.text


async def test_automation_reports_successful_job_with_missing_result(app, client, project):
    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    job = workflow.enqueue(p["id"], "reference", "lin")
    job["status"] = "succeeded"
    workflow.store.save_job(job)
    client.post(f"/api/projects/{p['id']}/automation", json={"enabled": True})
    await workflow.advance_safely(workflow.store.project(p["id"]))
    state = client.get(f"/api/projects/{p['id']}").json()["automation_status"]
    assert state["state"] == "error"
    assert "当前结果不可用" in state["error"]["message"]
    assert len(workflow.store.jobs(p["id"])) == 1


async def test_automation_uses_latest_attempt_not_last_database_update(app, client, project):
    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    older = workflow.enqueue(p["id"], "reference", "lin")
    snapshot = {**older["snapshot"], "settings_fingerprint": "changed-settings"}
    newer = workflow.store.enqueue(p["id"], "reference", "lin", snapshot)
    newer.update(status="blocked", error={"code": "NO_ROUTE", "message": "没有可用模型渠道"})
    workflow.store.save_job(newer)
    older["status"] = "succeeded"
    workflow.store.save_job(older)
    client.post(f"/api/projects/{p['id']}/automation", json={"enabled": True})
    state = client.get(f"/api/projects/{p['id']}").json()["automation_status"]
    assert state == {"state": "attention", "stage": "reference", "job_ids": [newer["id"]]}
    await workflow.advance(workflow.store.project(p["id"]))
    assert len(workflow.store.jobs(p["id"])) == 2


async def test_older_failed_requirement_does_not_block_approved_plan(app, client, project):
    workflow = app.state.workflow
    old_job = workflow.enqueue(project["id"], "requirements")
    old_job.update(status="blocked", error={"code": "GATEWAY_REJECTED", "message": "网关未接受请求"})
    workflow.store.save_job(old_job)
    p = approve_with_plan(client, project)
    client.post(f"/api/projects/{p['id']}/automation", json={"enabled": True})
    state = client.get(f"/api/projects/{p['id']}").json()["automation_status"]
    assert state == {"state": "scheduling", "stage": "reference", "entity_ids": ["lin"]}
    await workflow.advance(workflow.store.project(p["id"]))
    jobs = workflow.store.jobs(p["id"])
    assert any(job["stage"] == "reference" and job["status"] == "queued" for job in jobs)
    assert workflow.store.job(old_job["id"])["status"] == "blocked"


def test_queue_marks_uncertain_requests_without_exposing_runtime(app, client, project):
    workflow = app.state.workflow
    job = workflow.enqueue(project["id"], "requirements")
    job.update(status="blocked", runtime={"cloud_submitting": True, "secret": "private-runtime-value"})
    workflow.store.save_job(job)
    response = client.get("/api/jobs")
    assert response.json()[0]["verification_required"] is True
    assert "runtime" not in response.json()[0]
    assert "private-runtime-value" not in response.text
    job.update(
        error={"code": "SUBMISSION_UNKNOWN", "message": "请核对"}, runtime={"remote_id": "remote-123"}
    )
    workflow.store.save_job(job)
    assert client.get("/api/jobs").json()[0]["verification_required"] is False
