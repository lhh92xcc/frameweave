"""These tests read and execute actual pinned engines, not replacement implementations."""

import dataclasses
import os
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_engine_parser_and_renderer_contract():
    import inspect

    from app.media.video_validation import FFprobeVideoValidator
    from app.rendering.ffmpeg_renderer import AudioTrackInput, FFmpegVideoRenderer
    from app.rendering.subtitles import SubtitleCue
    from app.services.novel_parser import split_chapters

    assert split_chapters("第一章 开始\n一次真实解析\n第二章 结束\n另一个场景")[1].chapter_number == 2
    assert {"clips", "subtitles", "audio_tracks"} <= set(
        inspect.signature(FFmpegVideoRenderer.render).parameters
    )
    assert dataclasses.is_dataclass(AudioTrackInput)
    assert dataclasses.is_dataclass(SubtitleCue)
    assert callable(FFprobeVideoValidator.validate_bytes)


def test_gateway_schema_and_routes_are_compatible(tmp_path):
    # Separate interpreter: gateway and production engine have conflicting module names.
    python = ROOT / ".local/gateway-venv/bin/python"
    if not python.exists():
        pytest.fail("Run scripts/bootstrap.sh to install isolated gateway dependencies")
    script = """
from server import app
from schemas import ImageRequest, VideoRequest, ChatRequest
paths={r.path for r in app.routes}
assert {'/api/jobs','/api/chat/jobs','/api/video/jobs','/api/references','/api/video-providers','/api/web-image-providers','/api/accounts','/api/jobs/{job_id}'} <= paths
assert 'reference_images' in ImageRequest.model_fields
assert 'reference_images' in VideoRequest.model_fields
assert 'messages' in ChatRequest.model_fields
print('Gateway contracts OK')
"""
    result = subprocess.run(
        [str(python), "-c", script],
        cwd=ROOT / "upstream/web2api-web",
        env={
            **os.environ,
            "WEB2API_STATE_DIR": str(tmp_path / "gateway"),
            "WEB2API_IMAGES_DIR": str(tmp_path / "generated"),
        },
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_actual_ffmpeg_render_and_short_clip_gate(tmp_path, monkeypatch):
    from services import engine_bridge

    monkeypatch.setattr(engine_bridge, "MEDIA", tmp_path)
    source = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x288:rate=24",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=20,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine_bridge.app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/render",
            json={
                "clips": [
                    {"file": source.name, "duration": 1, "subtitle": ""},
                    {"file": source.name, "duration": 1, "subtitle": ""},
                ],
                "output": "art_123456.mp4",
                "subtitles": False,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["metadata"]["input_clip_count"] == 2
        assert response.json()["metadata"]["ffprobe"]["duration_seconds"] == pytest.approx(2, abs=0.15)
        assert (tmp_path / "art_123456.mp4").stat().st_size > 1000
        response = await client.post(
            "/render", json={"clips": [{"file": source.name, "duration": 3}], "output": "art_123456.mp4"}
        )
        assert response.status_code == 422
        response = await client.post("/validate", json={"file": "../outside.mp4"})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "aspect_ratio,width,height", [("9:16", 720, 1280), ("16:9", 1280, 720), ("1:1", 720, 720)]
)
async def test_real_chinese_subtitle_render(tmp_path, monkeypatch, aspect_ratio, width, height):
    from services import engine_bridge

    monkeypatch.setattr(engine_bridge, "MEDIA", tmp_path)
    source = tmp_path / "subtitle-source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=160x288:rate=24",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=20,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine_bridge.app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/render",
            json={
                "clips": [{"file": source.name, "duration": 1, "subtitle": "让灵感，成为下一帧。"}],
                "output": "art_234567.mp4",
                "subtitles": True,
                "aspect_ratio": aspect_ratio,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["metadata"]["subtitle_count"] == 1
        probe = response.json()["metadata"]["ffprobe"]
        assert (probe["width"], probe["height"]) == (width, height)
        assert "让灵感" in (tmp_path / "art_234567.srt").read_text()


@pytest.mark.asyncio
async def test_real_audio_mix_and_invalid_media_error(tmp_path, monkeypatch):
    from services import engine_bridge

    monkeypatch.setattr(engine_bridge, "MEDIA", tmp_path)
    source, voice = tmp_path / "source.mp4", tmp_path / "voice.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=160x288:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        timeout=20,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.5",
            str(voice),
        ],
        check=True,
        timeout=20,
    )
    (tmp_path / "broken.mp4").write_bytes(b"invalid-media")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine_bridge.app), base_url="http://testserver"
    ) as client:
        result = await client.post(
            "/render",
            json={
                "clips": [
                    {"file": source.name, "duration": 1},
                    {"file": source.name, "duration": 1, "audio": voice.name},
                ],
                "output": "art_abcdef.mp4",
                "subtitles": False,
            },
        )
        assert result.status_code == 200, result.text
        metadata = result.json()["metadata"]
        assert metadata["has_audio"] and metadata["audio_track_count"] == 2
        assert metadata["ffprobe"]["duration_seconds"] == pytest.approx(2, abs=0.15)
        invalid = await client.post("/validate", json={"file": "broken.mp4"})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VIDEO_ARTIFACT_NOT_PLAYABLE"
