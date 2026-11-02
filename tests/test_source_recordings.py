"""Offline source-recording fixtures; no speech synthesis or external model output."""

import hashlib

import pytest
from conftest import approve_with_plan, plan_for

from studio.providers import ProviderFailure


def test_existing_recording_can_be_selected_while_paused_without_counting_as_shot_audio(
    app, client, project, monkeypatch
):
    p = approve_with_plan(client, project)
    w = app.state.workflow
    paused = client.post(f"/api/projects/{p['id']}/pause").json()
    assert paused["paused"]
    raw = b"offline-existing-recording-not-external-output"
    calls = []

    async def engine(path, payload):
        calls.append((path, payload["kind"]))
        return {"duration_seconds": 1.5, "audio_stream_count": 1, "format_name": "mp3"}

    monkeypatch.setattr(w.providers, "engine_request", engine)
    response = client.post(
        f"/api/projects/{p['id']}/upload",
        data={"stage": "recording", "entity_id": "lin"},
        files={"file": ("offline.mp3", raw, "audio/mpeg")},
    )
    assert response.status_code == 201, response.text
    source = response.json()
    assert source["kind"] == "recording" and source["review"] == "pending"
    assert source["metadata"]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    imported = w.store.job(source["job_id"])
    assert imported["runtime"]["imported"]
    assert imported["snapshot"]["contract"]["markdown"] == p["contract"]["markdown"]
    assert imported["snapshot"]["pipeline_revision"] == w.settings.get().pipeline_revision
    assert "shot_reference_bindings" in imported["snapshot"]
    assert calls == [("/validate", "audio")]
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    updated = client.get(f"/api/projects/{p['id']}").json()
    result = client.put(f"/api/projects/{p['id']}/audio-cast", json={
        "voices": {"lin": {"voice_id": "existing-recording", "source": "recording",
                           "recording_artifact_id": source["id"]}},
        "speakers": {"ep1_s01": "lin"}, "expected_version": updated["version"],
    })
    assert result.status_code == 200, result.text
    view = client.get(f"/api/projects/{p['id']}").json()
    assert next(x for x in view["progress"] if x["id"] == "audio")["done"] == 0
    assert not [a for a in view["artifacts"] if a["kind"] == "audio"]

    # The source survives a storyboard revision, while generated audio still
    # needs an approved video and is invalidated by changed downstream inputs.
    current = w.store.project(p["id"])
    current["plan_revision"] += 1
    w.store.save_project(current, current["version"])
    assert w.recording_source(w.store.project(p["id"]),
                              {"recording_artifact_id": source["id"]}, "lin")["id"] == source["id"]
    (w.store.media / source["file"]).write_bytes(b"changed-offline-file")
    with pytest.raises(ProviderFailure, match="原录音文件"):
        w.recording_source(w.store.project(p["id"]), {"recording_artifact_id": source["id"]}, "lin")


def test_recording_source_cannot_be_bound_to_another_project_or_role(app, client, project, monkeypatch):
    p = approve_with_plan(client, project)
    w = app.state.workflow

    async def engine(path, payload):
        return {"duration_seconds": 1, "audio_stream_count": 1, "format_name": "mp3"}

    monkeypatch.setattr(w.providers, "engine_request", engine)
    response = client.post(f"/api/projects/{p['id']}/upload", data={"stage": "recording", "entity_id": "lin"},
                           files={"file": ("offline.mp3", b"offline-fixture", "audio/mpeg")})
    assert response.status_code == 201
    source = response.json()
    client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True})
    with pytest.raises(ProviderFailure, match="缺少已确认的原音轨"):
        w.recording_source(w.store.project(p["id"]), {"recording_artifact_id": source["id"]}, "another-role")
    other = {**w.store.project(p["id"]), "id": "project_other"}
    with pytest.raises(ProviderFailure, match="缺少已确认的原音轨"):
        w.recording_source(other, {"recording_artifact_id": source["id"]}, "lin")


def test_recording_import_requires_approved_plan_and_character(client, project):
    raw = b"offline-fixture"
    response = client.post(f"/api/projects/{project['id']}/upload",
                           data={"stage": "recording", "entity_id": "lin"},
                           files={"file": ("offline.mp3", raw, "audio/mpeg")})
    assert response.status_code == 422
    p = approve_with_plan(client, project)
    response = client.post(f"/api/projects/{p['id']}/upload",
                           data={"stage": "recording", "entity_id": "missing"},
                           files={"file": ("offline.mp3", raw, "audio/mpeg")})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_recording_input_still_requires_video_before_shot_audio(app, client, monkeypatch):
    from test_autopilot import full_project
    from test_workflow import add_artifact

    p = full_project(client)
    w = app.state.workflow
    plan = plan_for(p)
    plan["assets"][0].update(name="猫", speech_source="recording")
    for episode in plan["episodes"]:
        episode["shots"][0].update(speaker_id="lin", dialogue="喵", narration="")
    w.approve_contract(p["id"], p["contract"]["hash"])
    w.edit_plan(p["id"], plan, 0)
    w.approve_plan(p["id"], 1)
    calls = []

    async def engine(path, payload):
        calls.append(path)
        assert path == "/validate" and payload["kind"] == "audio"
        return {"duration_seconds": 1, "audio_stream_count": 1, "format_name": "mp3"}

    monkeypatch.setattr(w.providers, "engine_request", engine)
    result = client.post(f"/api/projects/{p['id']}/upload", data={"stage": "recording", "entity_id": "lin"},
                         files={"file": ("offline.mp3", b"offline-recording", "audio/mpeg")})
    assert result.status_code == 201, result.text
    source = result.json()
    assert client.post(f"/api/artifacts/{source['id']}/review", json={"approved": True}).status_code == 200
    current = w.store.project(p["id"])
    w.set_audio_cast(p["id"], {"lin": {"voice_id": "existing-recording", "source": "recording",
                                      "recording_artifact_id": source["id"]}},
                     {"ep1_s01": "lin", "ep2_s01": "lin"}, current["version"])
    with pytest.raises(ValueError, match="先完成并批准本镜视频"):
        w.enqueue(p["id"], "audio", "ep1_s01")

    current = w.store.project(p["id"])
    video = add_artifact(w, current, "video", "ep1_s01", review="approved")
    original_latest = w.latest
    monkeypatch.setattr(w, "latest", lambda project, kind, entity, *args, **kwargs:
                        video if kind == "video" and entity == "ep1_s01"
                        else original_latest(project, kind, entity, *args, **kwargs))
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    assert job["snapshot"]["dependencies"] == [source["id"], video["id"]]
    assert job["snapshot"]["audio_source_sha256"] == hashlib.sha256(b"offline-recording").hexdigest()
    await w.execute(job)
    saved = w.store.job(job["id"])
    assert saved["status"] == "succeeded", saved.get("error")
    output = w.store.artifact(saved["artifact_id"])
    assert output["kind"] == "audio" and output["review"] == "approved"
    assert (w.store.media / output["file"]).read_bytes() == b"offline-recording"
    assert calls == ["/validate", "/validate"]
