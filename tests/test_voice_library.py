"""Offline voice preservation fixtures; no real provider requests or generated audio."""

import io
import json
import zipfile

import httpx
import pytest
from conftest import approve_with_plan
from test_minimax import cast, configure, success, transport_for

from studio.api import create_app
from studio.store import Conflict


def saved_voice(app):
    preview = app.state.store.root / "offline-preview.mp3"
    preview.write_bytes(b"offline-preview-fixture-not-generated-audio")
    values = {
        "name": "离线青年音色", "profile": {"voice_id": "offline-activated-voice", "speed": 1.06},
        "preview": preview, "receipt": {"usage_voice_count": 1, "trace_id": "offline-activation"},
        "provenance": {"contract": "offline full approved Markdown", "reference_bindings": ["offline-ref"]},
        "source_project_id": "offline-source", "source_role_id": "offline-human",
    }
    return app.state.workflow.voice_library.import_activated(**values), values


def test_preservation_is_immutable_idempotent_private_and_survives_restart(client, app):
    entry, values = saved_voice(app)
    library = app.state.workflow.voice_library
    assert library.import_activated(**values) == entry
    with pytest.raises(Conflict, match="不能被覆盖"):
        library.import_activated(**{**values, "profile": {**values["profile"], "speed": 0.9}})
    with pytest.raises(ValueError, match="计费回执"):
        library.import_activated(**{**values, "receipt": {}})
    for name in ("preview.mp3", "record.json"):
        assert (library.root / entry["id"] / name).stat().st_mode & 0o777 == 0o600
    response = client.get("/api/audio/voice-library")
    voice = response.json()["voices"][0]
    assert voice["locked"] and voice["activated"] and voice["review"] == "pending"
    assert "provenance" not in voice and "activation_receipt" not in voice
    assert client.get(voice["preview_url"]).content == values["preview"].read_bytes()
    from fastapi.testclient import TestClient

    with TestClient(create_app(app.state.store.root, start_worker=False)) as restarted:
        assert restarted.get("/api/audio/voice-library").json() == response.json()
    # No mutation/removal interface is exposed for locked entries.
    assert client.put("/api/audio/voice-library/" + entry["id"], json={"voice_id": "other"}).status_code in {404, 405}
    assert client.delete("/api/audio/voice-library/" + entry["id"]).status_code in {404, 405}


def test_backup_preserves_preview_provenance_and_detects_tampering(client, app):
    entry, values = saved_voice(app)
    response = client.get("/api/audio/voice-library/export.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("voice-library.json"))
        assert manifest["voices"][0]["provenance"] == values["provenance"]
        assert archive.read(entry["id"] + "/preview.mp3") == values["preview"].read_bytes()
    assert not list((app.state.store.root / "exports").iterdir())
    app.state.workflow.voice_library.preview(entry["id"]).write_bytes(b"tampered offline file")
    assert client.get(f"/api/audio/voice-library/{entry['id']}/preview").status_code == 409


@pytest.mark.asyncio
async def test_locked_cast_reuses_original_id_without_design(client, app, project, monkeypatch):
    configure(client)
    entry, _ = saved_voice(app)
    p = cast(client, approve_with_plan(client, project))
    profile = {**entry["profile"], "library_id": entry["id"]}
    body = {"expected_version": p["version"], "voices": {"lin": profile}, "speakers": {"ep1_s01": "lin"}}
    calls = transport_for(app, monkeypatch, lambda request: httpx.Response(200, json=success()))
    response = client.put(f"/api/projects/{p['id']}/audio-cast", json=body)
    assert response.status_code == 200, response.text
    p = response.json()
    assert not calls  # Saving a reusable binding never submits synthesis.
    assert p["contract"]["approved"] and p["plan_approved"]
    body["expected_version"] = p["version"]
    body["voices"]["lin"] = {**profile, "voice_id": "different-new-voice"}
    assert client.put(f"/api/projects/{p['id']}/audio-cast", json=body).status_code == 409
    w = app.state.workflow
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    assert job["snapshot"]["audio_binding"]["voice_setting"]["library_id"] == entry["id"]
    assert job["snapshot"]["contract"]["markdown"] == p["contract"]["markdown"]
    await w.execute(job)
    assert w.store.job(job["id"])["status"] == "succeeded"
    assert len(calls) == 1 and calls[0].url.path == "/v1/t2a_v2"
    assert json.loads(calls[0].content)["voice_setting"] == entry["profile"]


def test_missing_library_binding_blocks_before_submission(client, app, project):
    configure(client)
    p = approve_with_plan(client, project)
    response = client.put(f"/api/projects/{p['id']}/audio-cast", json={
        "expected_version": p["version"],
        "voices": {"lin": {"voice_id": "missing-voice", "library_id": "voice_" + "a" * 20}},
        "speakers": {"ep1_s01": "lin"},
    })
    assert response.status_code == 404
    assert "不会自动重新设计" in response.json()["error"]["message"]
    assert not app.state.store.jobs()


@pytest.mark.asyncio
@pytest.mark.parametrize("recording", [False, True])
async def test_unverified_custom_voices_and_recording_roles_never_submit_tts(client, app, project, monkeypatch, recording):
    configure(client)
    p = approve_with_plan(client, project)
    profile = {"voice_id": "unverified-custom-voice"}
    if recording:
        profile["source"] = "recording"
    response = client.put(f"/api/projects/{p['id']}/audio-cast", json={
        "expected_version": p["version"], "voices": {"lin": profile}, "speakers": {"ep1_s01": "lin"},
    })
    assert response.status_code == 200
    calls = transport_for(app, monkeypatch, lambda request: pytest.fail("must not submit paid request"))
    w = app.state.workflow
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    await w.execute(job)
    failed = w.store.job(job["id"])
    assert failed["error"]["code"] == ("RECORDED_AUDIO_ONLY" if recording else "VOICE_POLICY_BLOCKED")
    assert not calls


def test_only_system_catalogue_entries_can_extend_no_activation_fee_allowlist(app):
    library = app.state.workflow.voice_library
    library.cache_system_voices([
        {"id": "offline-new-system", "kind": "system_voice"},
        {"id": "offline-unactivated", "kind": "voice_generation"},
        {"id": "offline-clone", "kind": "voice_cloning"},
    ])
    assert "offline-new-system" in library.allowed_voice_ids()
    assert not ({"offline-unactivated", "offline-clone"} & library.allowed_voice_ids())


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://api.minimaxi.com/v1/voice_design", "https://api.minimaxi.com/v1/voice_clone"])
async def test_voice_customization_endpoints_are_disabled_before_network(url):
    from studio.minimax import MiniMaxRejected, request

    def reject_network(request):
        pytest.fail("customization must never contact the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_network)) as client:
        with pytest.raises(MiniMaxRejected, match="已禁止定制"):
            await request(client, url, "offline-secret", {"prompt": "offline fixture"})
