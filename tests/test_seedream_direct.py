"""Offline Seedream protocol fixtures; no external generation or billing evidence."""

import base64
import json

import httpx
import pytest
from PIL import Image

from studio.models import Route, RoutingSettings
from studio.providers import ProviderFailure


def route():
    return Route(transport="cloud", provider="volcengine-seedream", model="offline-model",
                 base_url="https://ark.cn-beijing.volces.com/api/v3", api_key="offline-key")


def test_seedream_origin_is_fixed():
    bad = route().model_copy(update={"base_url": "https://wrong.example/v1"})
    with pytest.raises(ValueError, match="官方北京"):
        RoutingSettings(image=[bad])


@pytest.mark.asyncio
async def test_seedream_keeps_all_approved_reference_bytes_and_resumes_receipt(app, tmp_path, monkeypatch):
    provider = app.state.workflow.providers
    monkeypatch.setattr(provider, "checkpoint", lambda job, **values: job["runtime"].update(values))
    refs = [tmp_path / "offline-a.png", tmp_path / "offline-b.png"]
    for i, path in enumerate(refs):
        Image.new("RGB", (8, 8), (i * 50, 0, 0)).save(path)
    expected = [p.read_bytes() for p in refs]
    calls = []

    def handle(req):
        calls.append(req)
        assert str(req.url) == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert req.headers["authorization"] == "Bearer offline-key"
        body = json.loads(req.content)
        assert body["prompt"] == "offline approved contract and bindings"
        assert body["size"] == "1920x1080" and body["response_format"] == "b64_json"
        assert [base64.b64decode(image.split(",", 1)[1]) for image in body["image"]] == expected
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(expected[0]).decode()}],
                                        "usage": {"generated_images": 1}})

    job = {"id": "offline-seedream", "runtime": {"active_call": "reference"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(provider, "client", client)
        for _ in range(2):
            output = await provider.cloud_generate("image", job, route(), "offline approved contract and bindings",
                                                   refs, "16:9")
            assert output["content"] == expected[0]
            assert output["metadata"]["provider_usage"]["generated_images"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_seedream_rejects_unsupported_or_excess_references_before_submission(app, tmp_path, monkeypatch):
    provider = app.state.workflow.providers
    monkeypatch.setattr(provider, "checkpoint", lambda job, **values: job["runtime"].update(values))
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        monkeypatch.setattr(provider, "client", client)
        job = {"id": "offline-seedream-invalid", "runtime": {"active_call": "reference"}}
        with pytest.raises(ProviderFailure, match="最多接收 10 张"):
            await provider.cloud_generate("image", job, route(), "offline", [tmp_path / "missing"] * 11, "16:9")
        bad = tmp_path / "invalid.png"
        bad.write_bytes(b"offline-not-an-image")
        with pytest.raises(ProviderFailure, match="参考图不可读"):
            await provider.cloud_generate("image", job, route(), "offline", [bad], "16:9")
        with pytest.raises(ProviderFailure, match="官方地址"):
            await provider.cloud_generate("image", job, route().model_copy(update={"base_url": "https://wrong.example"}),
                                          "offline", [], "16:9")
    assert not calls and not job["runtime"].get("cloud_submitting")


@pytest.mark.asyncio
async def test_seedream_unknown_submission_is_not_repeated(app, monkeypatch):
    provider = app.state.workflow.providers
    monkeypatch.setattr(provider, "checkpoint", lambda job, **values: job["runtime"].update(values))
    calls = []

    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout("offline fixture")

    job = {"id": "offline-seedream-unknown", "runtime": {"active_call": "reference"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(provider, "client", client)
        for _ in range(2):
            with pytest.raises(ProviderFailure) as exc:
                await provider.cloud_generate("image", job, route(), "offline", [], "1:1")
            assert exc.value.code == "SUBMISSION_UNKNOWN"
    assert len(calls) == 1
