"""Offline consistency gates; synthetic images do not establish model identity quality."""

import io
import json
from hashlib import sha256 as hash_bytes

import pytest
from conftest import offline_visual_qc, plan_for
from PIL import Image

from studio.archive_import import sha256
from studio.reference_policy import read_policy


def policy_markdown(roles=("front", "side", "full_body"), shot_roles=("front", "full_body")):
    policy = {"version": 1, "assets": {"lin": {"required_roles": roles, "shot_roles": shot_roles}}}
    return "# Offline reference policy\n```frameweave-reference-policy\n" + json.dumps(policy) + "\n```\n"


def strict_project(client, project):
    id_ = project["id"]
    result = client.put(f"/api/projects/{id_}/contract", json={
        "markdown": policy_markdown(), "expected_revision": project["contract"]["revision"],
    })
    assert result.status_code == 200, result.text
    p = result.json()
    assert client.post(f"/api/projects/{id_}/contract/approve", json={"hash": p["contract"]["hash"]}).status_code == 200
    plan = plan_for(p)
    first = plan["episodes"][0]["shots"][0]
    first["duration"] = 5
    plan["episodes"][0]["shots"].append({**first, "id": "ep1_s02"})
    assert client.put(f"/api/projects/{id_}/plan", json={"plan": plan, "expected_revision": 0}).status_code == 200
    assert client.post(f"/api/projects/{id_}/plan/approve", json={"revision": 1}).status_code == 200
    return client.get(f"/api/projects/{id_}").json()


def png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), color).save(buffer, "PNG")
    return buffer.getvalue()


def upload(client, p, role, color="red", *, stage="reference", entity="lin", approve=True):
    result = client.post(f"/api/projects/{p['id']}/upload", data={
        "stage": stage, "entity_id": entity, **({"reference_role": role} if stage == "reference" else {}),
    }, files={"file": ("offline.png", png(color), "image/png")})
    assert result.status_code == 201, result.text
    item = result.json()
    if approve:
        response = client.post(f"/api/artifacts/{item['id']}/review", json={"approved": True})
        assert response.status_code == 200, response.text
    return item


def bundle(client, p):
    return [upload(client, p, role, color) for role, color in
            [("front", "red"), ("side", "green"), ("full_body", "blue")]]


@pytest.mark.parametrize("markdown", [
    "```frameweave-reference-policy\n{}\n```",
    policy_markdown(shot_roles=("front", "front")),
    policy_markdown() + policy_markdown(),
    "```frameweave-reference-policy\n{}",
])
def test_offline_malformed_policy_is_rejected(markdown):
    with pytest.raises(ValueError):
        read_policy(markdown)


def test_offline_missing_master_and_pending_master_never_enqueue(client, project):
    p = strict_project(client, project)
    path = f"/api/projects/{p['id']}/jobs"
    request = {"stage": "reference", "entity_id": "lin", "reference_role": "side"}
    assert client.post(path, json=request).status_code == 422
    source = upload(client, p, "front", approve=False)
    assert client.post(path, json=request).status_code == 422
    assert not client.get(f"/api/projects/{p['id']}").json()["jobs"][-1].get("remote_id")
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    assert client.post(path, json={**request, "reference_role": "front"}).status_code == 422
    assert client.post(path, json={**request, "reference_source_id": "wrong-source"}).status_code == 422
    result = client.post(path, json=request)
    assert result.status_code == 202, result.text
    snapshot = result.json()["snapshot"]
    assert snapshot["dependencies"] == [source["id"]]
    assert snapshot["reference_role"] == "side"
    assert snapshot["reference_manifest"][0]["sha256"] == hash_bytes(png("red")).hexdigest()


def test_offline_required_views_and_duplicate_files_block_keyframes(app, client, project):
    p = strict_project(client, project)
    w = app.state.workflow
    upload(client, p, "front")
    with pytest.raises(ValueError, match="多视角"):
        w.enqueue(p["id"], "keyframe", "ep1_s01")
    upload(client, p, "side")
    upload(client, p, "full_body")
    with pytest.raises(ValueError, match="相同图片"):
        w.enqueue(p["id"], "keyframe", "ep1_s01")
    upload(client, p, "side", "green")
    upload(client, p, "full_body", "blue")
    assert w.enqueue(p["id"], "keyframe", "ep1_s01")["status"] == "queued"


def test_offline_master_replacement_invalidates_views_and_old_review(app, client, project):
    p = strict_project(client, project)
    items = bundle(client, p)
    w = app.state.workflow
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    replacement = upload(client, p, "front", "yellow")
    assert replacement["id"] != items[0]["id"]
    current = client.get(f"/api/projects/{p['id']}").json()
    assert sum(v["approved"] for v in current["reference_readiness"]["assets"]["lin"]["views"]) == 1
    assert next(a for a in current["artifacts"] if a["id"] == items[1]["id"])["stale"]
    assert client.post(f"/api/artifacts/{items[1]['id']}/review", json={"approved": True}).status_code == 409
    with pytest.raises(ValueError, match="多视角"):
        w.assert_current(job)


def test_offline_review_version_change_invalidates_even_after_reapproval(app, client, project):
    p = strict_project(client, project)
    items = bundle(client, p)
    for approved in (False, True):
        assert client.post(f"/api/artifacts/{items[0]['id']}/review", json={"approved": approved}).status_code == 200
    with pytest.raises(ValueError, match="多视角"):
        app.state.workflow.enqueue(p["id"], "keyframe", "ep1_s01")


@pytest.mark.asyncio
async def test_offline_changed_reference_bytes_stop_before_provider(app, client, project, monkeypatch):
    p = strict_project(client, project)
    items = bundle(client, p)
    w = app.state.workflow
    job = w.enqueue(p["id"], "reference", "lin", reference_role="side")
    (w.store.media / items[0]["file"]).write_bytes(png("yellow"))

    async def no_calls(*args, **kwargs):
        pytest.fail("Changed bytes must stop before provider submission")

    monkeypatch.setattr(w.providers, "generate", no_calls)
    await w.execute(job)
    saved = w.store.job(job["id"])
    assert saved["status"] == "blocked" and saved["error"]["code"] == "STALE_DEPENDENCY"


def test_offline_previous_frame_is_required_only_within_episode(app, client, project):
    p = strict_project(client, project)
    items = bundle(client, p)
    w = app.state.workflow
    with pytest.raises(ValueError, match="连续性"):
        w.enqueue(p["id"], "keyframe", "ep1_s02")
    previous = upload(client, p, None, stage="keyframe", entity="ep1_s01")
    job = w.enqueue(p["id"], "keyframe", "ep1_s02")
    assert job["snapshot"]["dependencies"] == [items[0]["id"], items[2]["id"], previous["id"]]
    other = w.enqueue(p["id"], "keyframe", "ep2_s01")
    assert other["snapshot"]["dependencies"] == [items[0]["id"], items[2]["id"]]
    with pytest.raises(ValueError, match="不能跨集"):
        w.set_shot_references(p["id"], "ep2_s01", [], "ep1_s02", w.store.project(p["id"])["version"])


@pytest.mark.asyncio
async def test_offline_contact_sheet_and_manifest_reach_image_and_qc(app, client, project, monkeypatch):
    p = strict_project(client, project)
    items = bundle(client, p)
    w = app.state.workflow
    calls = []

    async def generate(kind, job, prompt, refs=(), **kwargs):
        calls.append(kind)
        assert p["contract"]["markdown"] in prompt
        assert "实际上传附件顺序" in prompt
        with Image.open(refs[0]) as image:
            assert image.size == (1920, 1000)
            assert image.getpixel((480, 520)) == (255, 0, 0)
            assert image.getpixel((1440, 520)) == (0, 0, 255)
        if kind == "image":
            assert len(refs) == 1
            filename = kwargs["output"] + ".png"
            (w.store.media / filename).write_bytes(png("orange"))
            return {"file": filename, "mime": "image/png", "metadata": {"aspect_matches": True}, "route": {"provider": "offline"}}
        assert len(refs) == 2
        return {"text": offline_visual_qc()}

    monkeypatch.setattr(w.providers, "generate", generate)
    job = w.enqueue(p["id"], "keyframe", "ep1_s01")
    await w.execute(job)
    saved = w.store.job(job["id"])
    assert saved["status"] == "succeeded", saved.get("error")
    assert calls == ["image", "vision"]
    manifest = saved["runtime"]["reference_uploads"]
    assert manifest[0]["source_artifact_ids"] == [items[0]["id"], items[2]["id"]]
    assert manifest[0]["sha256"] == sha256(w.store.media / manifest[0]["file"])


def test_offline_revalidate_retained_story_requires_exact_hash_preserves_cast(app, client, project):
    p = strict_project(client, project)
    w = app.state.workflow
    original = w.store.project(p["id"])
    original["audio_cast"] = {"voices": {}, "speakers": {}}
    w.store.save_project(original)
    result = client.put(f"/api/projects/{p['id']}/contract", json={
        "markdown": p["contract"]["markdown"] + "\nRevised offline spec", "expected_revision": p["contract"]["revision"],
    }).json()
    client.post(f"/api/projects/{p['id']}/contract/approve", json={"hash": result["contract"]["hash"]})
    path = f"/api/projects/{p['id']}/plan/approve"
    assert client.post(path, json={"revision": 1}).status_code == 422
    assert client.post(path, json={"revision": 1, "contract_hash": p["contract"]["hash"]}).status_code == 422
    response = client.post(path, json={"revision": 1, "contract_hash": result["contract"]["hash"]})
    assert response.status_code == 200, response.text
    assert response.json()["plan"] == original["plan"]
    assert response.json()["audio_cast"] == original["audio_cast"]


def test_offline_policy_requires_exact_plan_coverage_and_real_views(app, client, project):
    p = strict_project(client, project)
    w = app.state.workflow
    changed = plan_for(p)
    changed["assets"][0]["id"] = "someone_else"
    for episode in changed["episodes"]:
        episode["shots"][0]["asset_ids"] = ["someone_else"]
    with pytest.raises(ValueError, match="覆盖"):
        w.check_plan(w.store.project(p["id"]), changed)
    master = upload(client, p, "front")
    result = client.put(f"/api/artifacts/{master['id']}/reference-role", json={"role": "side"})
    assert result.status_code == 422
    assert w.automation_state({**w.store.project(p["id"]), "automation": True}) == {
        "state": "scheduling", "stage": "reference", "entity_ids": ["lin"],
    }
    pending = upload(client, p, "side", "blue", approve=False)
    assert pending["review"] == "pending"
    assert w.automation_state({**w.store.project(p["id"]), "automation": True}) == {
        "state": "review", "stage": "reference",
    }


@pytest.mark.asyncio
async def test_offline_automation_fills_derivative_views_after_master_approval(app, client, project, monkeypatch):
    p = strict_project(client, project)
    master = upload(client, p, "front")
    w = app.state.workflow

    async def generate(kind, job, prompt, refs=(), **kwargs):
        if kind == "vision":
            return {"text": offline_visual_qc()}
        assert job["snapshot"]["dependencies"] == [master["id"]]
        assert len(refs) == 1
        filename = kwargs["output"] + ".png"
        color = {"side": "green", "full_body": "blue"}[job["snapshot"]["reference_role"]]
        (w.store.media / filename).write_bytes(png(color))
        return {"file": filename, "mime": "image/png", "metadata": {"aspect_matches": True}}

    monkeypatch.setattr(w.providers, "generate", generate)
    for role in ("side", "full_body"):
        state = w.automation_state({**w.store.project(p["id"]), "automation": True})
        assert state == {"state": "scheduling", "stage": "reference", "entity_ids": ["lin"]}
        job = w.enqueue(p["id"], "reference", "lin")
        assert job["snapshot"]["reference_role"] == role
        await w.execute(job)
        saved = w.store.job(job["id"])
        assert saved["status"] == "succeeded", saved.get("error")
        assert w.store.artifact(saved["artifact_id"])["review"] == "approved"
    assert w.automation_state({**w.store.project(p["id"]), "automation": True})["stage"] == "keyframe"
