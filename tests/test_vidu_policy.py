"""Offline policy and request-boundary coverage, not provider output verification."""
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from studio.models import Route, ViduOptions
from studio.tokenhub_video import ENDPOINT, generate
from studio.vidu_policy import select_vidu, validate_payload


@pytest.mark.parametrize("options,duration,video,expected", [
    ({}, 5, True, "vidu-video-q2-pro"),
    ({"audio": True}, 5, False, "vidu-video-q3-turbo"),
    ({"bgm": True}, 5, False, "vidu-video-q2-turbo"),
    ({}, 11, False, "vidu-video-q3-turbo"),
    ({}, 16, False, "vidu-video-q3-turbo"),
    ({"family": "q2", "preference": "speed"}, 10, False, "vidu-video-q2-turbo"),
    ({"family": "q2", "preference": "quality"}, 10, False, "vidu-video-q2-pro"),
    ({"family": "q3", "preference": "quality"}, 10, False, "vidu-video-q3-pro"),
    ({"preference": "budget"}, 10, False, "vidu-video-q2"),
    ({"audio": True, "preference": "budget"}, 10, False, "vidu-video-q3-turbo"),
])
def test_selection(options, duration, video, expected):
    assert select_vidu(options, duration, has_video_ref=video)["model"] == expected


@pytest.mark.parametrize("options,duration,video", [
    ({"audio": True, "bgm": True}, 5, False),
    ({"audio": True}, 5, True),
    ({}, 11, True),
    ({"bgm": True}, 11, False),
    ({"family": "q2", "audio": True}, 5, False),
    ({"family": "q3"}, 5, True),
    ({}, 17, False),
    ({}, 0, False),
])
def test_hard_conflicts(options, duration, video):
    with pytest.raises(ValueError):
        select_vidu(options, duration, has_video_ref=video)


@pytest.mark.parametrize("body", [
    {"model": "vidu-video-q2-pro", "audio": False},
    {"model": "vidu-video-q3-turbo", "bgm": False},
    {"model": "vidu-video-q3-turbo", "subjects": [{"videos": []}]},
    {"model": "vidu-video-q3-turbo", "videos": ["offline.mp4"]},
    {"model": "vidu-video-q2-turbo", "subjects": [{"videos": ["offline.mp4"]}]},
    {"model": "vidu-video-q2-pro", "duration": 11},
])
def test_payload_rejects_incompatible_fields(body):
    with pytest.raises(ValueError):
        validate_payload(body)


def test_schema_defaults_and_audio_conflict():
    assert ViduOptions().model_dump() == dict(family="auto", preference="default", audio=False, bgm=False, reference_previous_video=False)
    with pytest.raises(ValueError):
        ViduOptions(audio=True, bgm=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("options,duration,video,expected,path", [
    ({"family": "q2", "preference": "quality"}, 5, False, "vidu-video-q2-pro", "reference-to-video"),
    ({"audio": True}, 11, False, "vidu-video-q3-turbo", "reference-to-video"),
    ({"bgm": True, "preference": "budget"}, 5, False, "vidu-video-q2", "reference-to-video"),
    ({"reference_previous_video": True}, 5, True, "vidu-video-q2-pro", "reference-to-video"),
])
async def test_real_payload_and_provenance(tmp_path, monkeypatch, options, duration, video, expected, path):
    refs = [tmp_path / "keyframe.png", tmp_path / "character.png"]
    for ref in refs:
        Image.new("RGB", (32, 32)).save(ref)
    (tmp_path / "previous.mp4").write_bytes(b"offline video fixture")
    uploads, submits = [], []
    class Storage:
        def __init__(self, root):
            pass

        def upload(self, path):
            uploads.append(path.name)
            return path.name

        def signed_url(self, ref):
            return "https://offline.cos.example/" + ref
    monkeypatch.setattr("studio.tokenhub_video.CosStorage", Storage)
    def handler(req):
        if req.method == "POST":
            assert req.url.path.endswith(path)
            submits.append(json.loads(req.content))
            return httpx.Response(200, json={"task_id": "offline-task"})
        if req.url.host == "cdn.example":
            assert "authorization" not in req.headers
            return httpx.Response(200, content=b"offline output")
        return httpx.Response(200, json={"state": "success", "creations": [{"url": "https://cdn.example/clip.mp4"}]})
    decision = select_vidu(options, duration, has_video_ref=video)
    job = {"runtime": {"active_call": "call"}, "snapshot": {"entity": {"vidu": options}, "dependencies": ["previous"], "vidu_decisions": {"0": decision}}}
    if video:
        job["runtime"]["vidu_video_inputs"] = [{"artifact_id": "previous", "file": "previous.mp4"}]
    async def engine_request(path, body):
        return {"duration_seconds": 5}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SimpleNamespace(client=client, engine_request=engine_request, store=SimpleNamespace(root=tmp_path, media=tmp_path,
                                   artifact=lambda _: {"kind": "video", "review": "approved"}),
                                   checkpoint=lambda j, **kw: j["runtime"].update(kw))
        route = Route(transport="cloud", provider="tokenhub-vidu", model="auto", base_url=ENDPOINT, api_key="offline-key")
        result = await generate(provider, job, route, "FULL CONTRACT", refs, duration)
        await generate(provider, job, route, "FULL CONTRACT", refs, duration)
    assert len(submits) == 1
    payload = submits[0]
    assert payload["model"] == result["actual_route"]["model"] == expected
    assert payload["prompt"] == "FULL CONTRACT"
    assert len(uploads) == (3 if video else 2)
    assert all(subject.get("name") and "id" not in subject for subject in payload["subjects"])
    assert bool(any(s.get("videos") for s in payload["subjects"])) == video
    if expected.startswith("vidu-video-q2"):
        assert "audio" not in payload and payload["bgm"] == options.get("bgm", False)
    else:
        assert "bgm" not in payload and payload["audio"] == options.get("audio", False)


def test_shot_decisions_are_snapshotted_and_edit_invalidates(app, client, project, monkeypatch):
    from conftest import approve_with_plan

    p = approve_with_plan(client, project)
    workflow = app.state.workflow
    monkeypatch.setattr(workflow, "dependencies", lambda *args, **kwargs: [])
    p["plan"]["episodes"][0]["shots"][0]["vidu"] = ViduOptions(family="q2", preference="quality").model_dump()
    p["plan"]["episodes"][1]["shots"][0]["vidu"] = ViduOptions(family="q3", preference="speed").model_dump()
    first = workflow.snapshot(p, "video", "ep1_s01")
    second = workflow.snapshot(p, "video", "ep2_s01")
    assert first["vidu_decisions"]["0"]["model"] == "vidu-video-q2-pro"
    assert second["vidu_decisions"]["0"]["model"] == "vidu-video-q3-turbo"
    assert "api_key" not in json.dumps(first)
    before = p["plan_revision"]
    response = client.put(f"/api/projects/{p['id']}/plan", json={"plan": p["plan"], "expected_revision": before})
    assert response.status_code == 200, response.text
    edited = client.get(f"/api/projects/{p['id']}").json()
    assert edited["plan_revision"] == before + 1 and not edited["plan_approved"]


def test_plan_rejects_impossible_request(app, client, project):
    from conftest import plan_for

    plan = plan_for(project)
    plan["episodes"][0]["shots"][0]["vidu"] = {"family": "q2", "audio": True}
    with pytest.raises(ValueError, match="冲突"):
        app.state.workflow.check_plan(project, plan)
    plan["episodes"][0]["shots"][0]["vidu"] = {"reference_previous_video": True}
    with pytest.raises(ValueError, match="首镜"):
        app.state.workflow.check_plan(project, plan)


def test_video_reference_dependency_is_previous_in_same_episode(app, project, monkeypatch):
    from conftest import plan_for

    workflow = app.state.workflow
    plan = plan_for(project)
    first = plan["episodes"][0]["shots"][0]
    first["duration"] = 5
    second = {**first, "id": "ep1_s02", "vidu": {"reference_previous_video": True}}
    plan["episodes"][0]["shots"].append(second)
    project["plan"] = plan
    monkeypatch.setattr(workflow, "latest", lambda p, kind, entity_id: {
        "id": f"{kind}_{entity_id}", "review": "approved"})
    assert workflow.dependencies(project, "video", "ep1_s02") == ["keyframe_ep1_s02", "video_ep1_s01"]
    plan["episodes"][1]["shots"][0]["vidu"] = {"reference_previous_video": True}
    with pytest.raises(ValueError, match="首镜"):
        workflow.dependencies(project, "video", "ep2_s01")
