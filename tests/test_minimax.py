"""Offline MiniMax protocol fixtures. No external model output or paid calls."""

import json

import httpx
import pytest
from conftest import approve_with_plan

from studio.providers import ProviderFailure
from studio.store import uid

SECRET = "offline-minimax-secret-never-return"
AUDIO = b"offline-audio-protocol-fixture-not-model-output"


def configure(client, *, key=SECRET, paid=True):
    settings = client.get("/api/settings").json()
    for kind in ("text", "image", "vision", "video", "audio"):
        for route in [settings[kind]] if kind == "audio" else settings[kind]:
            route.pop("configured", None)
    settings.update(allow_paid=paid, audio_mode="cloud", voice="cute_boy")
    settings["audio"] = {
        "transport": "cloud", "provider": "minimax", "model": "speech-2.8-turbo",
        "base_url": "https://api.minimaxi.com/v1", "api_key": key,
    }
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200, response.text
    return response.json()


def cast(client, p, voice="cute_boy"):
    response = client.put(f"/api/projects/{p['id']}/audio-cast", json={
        "expected_version": p["version"],
        "voices": {"lin": {"voice_id": voice, "speed": 1, "pitch": 2, "vol": 1, "emotion": "calm"},
                   "narrator": {"voice_id": "Chinese (Mandarin)_Humorous_Elder", "speed": 0.95}},
        "speakers": {"ep1_s01": "lin", "ep2_s01": "narrator"},
    })
    assert response.status_code == 200, response.text
    return response.json()


def success():
    return {"base_resp": {"status_code": 0, "status_msg": "success"},
            "data": {"audio": AUDIO.hex(), "status": 2},
            "trace_id": "offline-trace-123", "extra_info": {"usage_characters": 24, "audio_length": 1200}}


def transport_for(app, monkeypatch, synthesis):
    calls = []

    def handle(request):
        if request.url.host == "api.minimaxi.com":
            calls.append(request)
            assert request.headers["Authorization"] == "Bearer " + SECRET
            return synthesis(request)
        if request.url.path == "/validate":
            file = json.loads(request.content)["file"]
            assert (app.state.store.media / file).read_bytes() == AUDIO
            return httpx.Response(200, json={"duration_seconds": 1.2})
        return httpx.Response(200, json={"configured": False})

    monkeypatch.setattr(app.state.providers, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    return calls


def test_minimax_key_write_only_origin_fixed_and_retained(client, app):
    settings = configure(client)
    assert settings["audio"]["configured"]
    assert SECRET not in json.dumps(settings)
    settings["audio"].pop("configured")
    for kind in ("text", "image", "vision", "video"):
        for route in settings[kind]:
            route.pop("configured", None)
    assert client.put("/api/settings", json=settings).status_code == 200
    assert app.state.workflow.settings.get().audio.api_key == SECRET
    settings["audio"].update(base_url="https://attacker.example/v1", api_key=SECRET)
    rejected = client.put("/api/settings", json=settings)
    assert rejected.status_code == 422
    assert SECRET not in rejected.text
    assert app.state.workflow.settings.get().audio.base_url == "https://api.minimaxi.com/v1"
    assert (app.state.store.root / "settings.json").stat().st_mode & 0o777 == 0o600


def test_voice_catalogue_uses_saved_key_without_synthesis(client, app, monkeypatch):
    configure(client)
    calls = transport_for(app, monkeypatch, lambda request: httpx.Response(200, json={
        "base_resp": {"status_code": 0},
        "system_voice": [{"voice_id": "cute_boy", "voice_name": "可爱男童"}],
        "voice_generation": [{"voice_id": "offline-designed", "description": []}],
    }))
    response = client.get("/api/audio/minimax/voices")
    assert response.status_code == 200
    assert [v["id"] for v in response.json()["voices"]] == ["cute_boy"]
    assert SECRET not in response.text
    assert len(calls) == 1 and calls[0].url.path == "/v1/get_voice"
    assert json.loads(calls[0].content) == {"voice_type": "all"}


@pytest.mark.asyncio
async def test_sync_protocol_role_binding_snapshot_and_receipt_recovery(client, app, project, monkeypatch):
    configure(client)
    p = cast(client, approve_with_plan(client, project))
    w = app.state.workflow
    calls = transport_for(app, monkeypatch, lambda request: httpx.Response(200, json=success()))
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    assert SECRET not in json.dumps(job)
    assert job["snapshot"]["audio_cast"]["speakers"]["ep1_s01"] == "lin"
    assert "shot_reference_bindings" in job["snapshot"]
    await w.execute(job)
    finished = w.store.job(job["id"])
    assert finished["status"] == "succeeded", finished.get("error")
    assert len(calls) == 1 and str(calls[0].url) == "https://api.minimaxi.com/v1/t2a_v2"
    payload = json.loads(calls[0].content)
    assert payload["model"] == "speech-2.8-turbo"
    assert payload["text"] == p["plan"]["episodes"][0]["shots"][0]["narration"]
    assert payload["stream"] is False and payload["output_format"] == "hex"
    assert payload["voice_setting"] == {"voice_id": "cute_boy", "speed": 1, "pitch": 2, "vol": 1, "emotion": "calm"}
    assert payload["audio_setting"]["format"] == "mp3"
    assert "instructions" not in payload
    assert p["contract"]["markdown"] in finished["effective_prompt"]
    artifact = w.store.artifact(finished["artifact_id"])
    assert artifact["route"]["provider"] == "minimax" and artifact["route"]["paid"]
    assert artifact["review"] == "pending" and artifact["metadata"]["speaker_id"] == "lin"
    assert artifact["metadata"]["minimax"]["usage_characters"] == 24
    receipt = w.store.root / finished["runtime"]["receipt"]
    assert receipt.read_bytes() == AUDIO and receipt.stat().st_mode & 0o777 == 0o600
    # A second character resolves to its own profile, rather than the global cat voice.
    other = w.snapshot(p, "audio", "ep2_s01")
    assert other["audio_binding"]["voice_setting"]["voice_id"].endswith("Humorous_Elder")
    # Re-running an existing receipt validates it without submitting paid speech again.
    await w.audio(finished)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "paid", "code"), [(None, True, "AUDIO_NOT_CONFIGURED"), (SECRET, False, "PAID_DISABLED")])
async def test_missing_key_and_paid_permission_stop_before_submission(client, app, project, monkeypatch, key, paid, code):
    configure(client, key=key, paid=paid)
    p = approve_with_plan(client, project)
    calls = transport_for(app, monkeypatch, lambda request: pytest.fail("must not submit"))
    job = app.state.workflow.enqueue(p["id"], "audio", "ep1_s01")
    await app.state.workflow.execute(job)
    assert app.state.store.job(job["id"])["error"]["code"] == code
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "timeout", "invalid_hex", "unfinished", "redirect", "provider_error", "http401", "http502",
    "http503_secret_trace", "http503_invalid_trace", "invalid_json", "balance",
])
async def test_safe_failures_and_uncertain_submission_never_auto_retry(client, app, project, monkeypatch, failure):
    configure(client)
    p = approve_with_plan(client, project)

    def response(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream error " + SECRET, request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://attacker.example/collect"})
        if failure == "http401":
            return httpx.Response(401, text=SECRET)
        if failure == "http502":
            return httpx.Response(502, headers={"trace_id": "offline-support-trace"}, text=SECRET)
        if failure == "http503_secret_trace":
            return httpx.Response(503, headers={"trace_id": SECRET}, text=SECRET)
        if failure == "http503_invalid_trace":
            return httpx.Response(503, headers={"trace_id": "untrusted/body?value"}, text=SECRET)
        if failure == "invalid_json":
            return httpx.Response(200, headers={"x-request-id": "offline-request-id"}, text=SECRET)
        data = success()
        if failure == "invalid_hex":
            data["data"]["audio"] = "invalid-hex"
        elif failure == "unfinished":
            data["data"]["status"] = 1
        elif failure == "balance":
            data["base_resp"] = {"status_code": 1008, "status_msg": SECRET}
        else:
            data["base_resp"] = {"status_code": 1004, "status_msg": SECRET}
        return httpx.Response(200, json=data)

    calls = transport_for(app, monkeypatch, response)
    w = app.state.workflow
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    await w.execute(job)
    failed = w.store.job(job["id"])
    assert failed["status"] == "blocked"
    assert SECRET not in client.get("/api/jobs/" + job["id"]).text
    assert len(calls) == 1
    message = failed["error"]["message"]
    if failure == "http502":
        assert "HTTP 502" in message and "offline-support-trace" in message
    if failure in {"http503_secret_trace", "http503_invalid_trace"}:
        assert "HTTP 503" in message and "trace_id" not in message
    if failure == "invalid_json":
        assert "HTTP 200" in message and "offline-request-id" in message
    if failure == "balance":
        assert "余额不足" in message and "HTTP 200" in message
    if failure in {"http401", "provider_error", "balance"}:
        assert not failed["runtime"]["cloud_submitting"]
    else:
        assert failed["runtime"]["cloud_submitting"]
        assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 422
        await w.execute(failed)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_validation_failure_recovers_paid_receipt_without_resubmission(client, app, project, monkeypatch):
    configure(client)
    p = approve_with_plan(client, project)
    w = app.state.workflow
    calls = transport_for(app, monkeypatch, lambda request: httpx.Response(200, json=success()))
    original = w.providers.engine_request

    async def unavailable(*args):
        raise ProviderFailure("ENGINE_UNAVAILABLE", "offline validation outage")

    monkeypatch.setattr(w.providers, "engine_request", unavailable)
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    await w.execute(job)
    assert w.store.job(job["id"])["status"] == "blocked"
    monkeypatch.setattr(w.providers, "engine_request", original)
    retry = client.post(f"/api/jobs/{job['id']}/retry")
    assert retry.status_code == 202 and retry.json()["id"] == job["id"]
    await w.execute(w.store.job(job["id"]))
    assert w.store.job(job["id"])["status"] == "succeeded"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_voice_edits_invalidate_audio_without_unapproving_storyboard(client, app, project, monkeypatch):
    configure(client)
    p = cast(client, approve_with_plan(client, project))
    w = app.state.workflow
    calls = transport_for(app, monkeypatch, lambda request: httpx.Response(200, json=success()))
    job = w.enqueue(p["id"], "audio", "ep1_s01")
    await w.execute(job)
    audio = w.store.artifact(w.store.job(job["id"])["artifact_id"])
    assert client.post(f"/api/artifacts/{audio['id']}/review", json={"approved": True}).status_code == 200
    updated = cast(client, w.store.project(p["id"]), voice="clever_boy")
    assert updated["contract"]["approved"] and updated["plan_approved"]
    assert updated["plan_revision"] == p["plan_revision"]
    assert w.latest(updated, "audio", "ep1_s01") is None
    assert client.post(f"/api/artifacts/{audio['id']}/review", json={"approved": True}).status_code == 409
    await w.execute(w.store.job(job["id"]))
    assert w.store.job(job["id"])["error"]["code"] == "STALE_INPUT" and len(calls) == 1


def test_cast_change_invalidates_render_but_preserves_video_and_key_rotation(client, app, project):
    configure(client)
    p = cast(client, approve_with_plan(client, project))
    w = app.state.workflow
    fixtures = {}
    (w.store.media / "offline-fixture").write_bytes(b"offline dependency graph fixture")
    # Offline dependency-graph fixtures; no generated media or external output is claimed.
    for stage, entity in [("reference", "lin"), ("keyframe", "ep1_s01"), ("video", "ep1_s01"),
                          ("audio", "ep1_s01"), ("render", "ep1")]:
        snapshot = w.snapshot(p, stage, entity)
        job = w.store.record_import(p["id"], stage, entity, snapshot, "offline dependency fixture")
        artifact = w.save_output(job, uid("art"), {"file": "offline-fixture", "mime": "application/octet-stream"})
        w.review(artifact["id"], True)
        fixtures[stage] = artifact
    configure(client, key="offline-rotated-key")
    assert w.latest(p, "audio", "ep1_s01")["id"] == fixtures["audio"]["id"]
    assert w.latest(p, "render", "ep1")["id"] == fixtures["render"]["id"]
    p = cast(client, w.store.project(p["id"]), voice="clever_boy")
    assert w.latest(p, "audio", "ep1_s01") is None
    assert w.latest(p, "render", "ep1") is None
    assert w.latest(p, "video", "ep1_s01")["id"] == fixtures["video"]["id"]
    p = cast(client, w.store.project(p["id"]), voice="cute_boy")
    assert w.latest(p, "audio", "ep1_s01") is None
    assert w.latest(p, "render", "ep1") is None


def test_audio_cast_validates_characters_shots_and_optimistic_version(client, project):
    p = approve_with_plan(client, project)
    url = f"/api/projects/{p['id']}/audio-cast"
    assert client.put(url, json={"expected_version": p["version"] - 1}).status_code == 409
    assert client.put(url, json={"expected_version": p["version"], "voices": {"missing": {"voice_id": "cute_boy"}}}).status_code == 422
    assert client.put(url, json={"expected_version": p["version"], "speakers": {"missing": "lin"}}).status_code == 422
    assert client.put(url, json={"expected_version": p["version"], "voices": {"lin": {"voice_id": "cute_boy", "pitch": 13}}}).status_code == 422
