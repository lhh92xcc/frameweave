from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "upstream/ai-video-generation"))

from app.services.novel_parser import split_chapters  # noqa: E402

for prefix in [Path("/opt/homebrew/opt/ffmpeg-full/bin"), Path("/usr/local/opt/ffmpeg-full/bin")]:
    if prefix.exists():
        os.environ["PATH"] = str(prefix) + os.pathsep + os.environ.get("PATH", "")

os.environ["FRAMEWEAVE_DATA"] = tempfile.mkdtemp(prefix="frameweave-test-default-")

from studio.api import create_app  # noqa: E402

SOURCE = "第一章 雾中的灯塔\n林夏在灯塔找到一封信。她穿深蓝色工装，带着蓝色齿轮。\n\n第二章 机械鸟\n她在钟楼修好机械鸟，机械鸟飞向灯塔。"


def engine_transport(request):
    if request.url.path == "/chapters":
        import dataclasses

        return httpx.Response(
            200,
            json={
                "chapters": [
                    dataclasses.asdict(c) for c in split_chapters(json.loads(request.content)["text"])
                ]
            },
        )
    if request.url.path == "/api/seedream":
        return httpx.Response(200, json={"configured": False})
    if request.url.path.startswith("/api/"):
        return httpx.Response(200, json={"data": []})
    return httpx.Response(200, json={"status": "ok"})


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, start_worker=False, transport=httpx.MockTransport(engine_transport))


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def project(client):
    response = client.post(
        "/api/projects", json={"title": "灯塔来信", "source": SOURCE, "episodes": 2, "duration": 10}
    )
    assert response.status_code == 201, response.text
    return response.json()


def plan_for(p):
    return {
        "assets": [
            {
                "id": "lin",
                "name": "林夏",
                "kind": "character",
                "description": "年轻的灯塔修理师，深蓝色工装",
                "prompt": "年轻修理师穿深蓝色工装，全身标准人设图",
            }
        ],
        "episodes": [
            {
                "id": f"ep{i}",
                "title": f"第{i}集",
                "synopsis": "林夏循着信中的线索修好机械鸟。",
                "source_chapters": [i],
                "shots": [
                    {
                        "id": f"ep{i}_s01",
                        "title": "灯塔里的信",
                        "description": "穿深蓝工装的林夏打开信，抬头看向窗外。",
                        "image_prompt": "林夏在灯塔室内低头看信，中景自然光",
                        "video_prompt": "林夏缓慢打开信，抬头，镜头轻推，保持服装一致",
                        "duration": 10,
                        "dialogue": "",
                        "narration": "一封信改变了她的旅途。",
                        "asset_ids": ["lin"],
                    }
                ],
            }
            for i in (1, 2)
        ],
    }


def approve_with_plan(client, p):
    id_ = p["id"]
    assert (
        client.post(f"/api/projects/{id_}/contract/approve", json={"hash": p["contract"]["hash"]}).status_code
        == 200
    )
    result = client.put(f"/api/projects/{id_}/plan", json={"plan": plan_for(p), "expected_revision": 0})
    assert result.status_code == 200, result.text
    result = client.post(f"/api/projects/{id_}/plan/approve", json={"revision": 1})
    assert result.status_code == 200, result.text
    return client.get(f"/api/projects/{id_}").json()
