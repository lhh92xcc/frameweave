"""Stable HTTP boundary around the pinned ai-video-generation engine.

Run with the engine directory on PYTHONPATH. No upstream source is patched.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import os
import tempfile
from pathlib import Path
from typing import Literal

from app.domain.models import TTSGenerationRequest
from app.media.audio_validation import FFprobeAudioValidator
from app.media.video_validation import FFprobeVideoValidator
from app.providers.edge_tts import EdgeTTSProvider
from app.providers.errors_audio import AudioArtifactValidationError
from app.providers.errors_video import VideoArtifactValidationError
from app.rendering.ffmpeg_renderer import AudioTrackInput, FFmpegRenderError
from app.rendering.subtitles import SubtitleCue, serialize_srt
from app.services.novel_parser import split_chapters
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.renderer import StudioRenderer

MEDIA = Path(os.environ.get("FRAMEWEAVE_DATA", ".local")).resolve() / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="FrameWeave engine adapter", version="1.0.0")


@app.exception_handler(FFmpegRenderError)
@app.exception_handler(VideoArtifactValidationError)
@app.exception_handler(AudioArtifactValidationError)
async def render_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=422, content={"detail": exc.message, "code": exc.code})


def local_file(name: str) -> Path:
    path = (MEDIA / name).resolve()
    if not path.is_relative_to(MEDIA) or not path.is_file():
        raise HTTPException(422, "素材路径无效")
    return path


class Chapters(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


@app.get("/health")
async def health():
    return {"status": "ok", "engine": "ai-video-generation", "adapter": "1"}


@app.post("/chapters")
async def chapters(body: Chapters):
    return {"chapters": [dataclasses.asdict(c) for c in split_chapters(body.text)]}


class MediaCheck(BaseModel):
    file: str
    kind: Literal["video", "audio"] = "video"


@app.post("/validate")
async def validate(body: MediaCheck):
    raw = await asyncio.to_thread(local_file(body.file).read_bytes)
    if body.kind == "audio":
        result = await FFprobeAudioValidator().validate_bytes(raw, "audio/mpeg")
    else:
        result = await FFprobeVideoValidator().validate_bytes(raw, "video/mp4")
    return dataclasses.asdict(result)


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(min_length=1, max_length=120)
    output: str = Field(pattern=r"^art_[a-f0-9]+\.mp3$")


@app.post("/speech")
async def speech(body: Speech):
    try:
        result = await EdgeTTSProvider().generate_speech(
            TTSGenerationRequest(text=body.text, voice=body.voice, rate="+0%", volume="+0%")
        )
        (MEDIA / body.output).write_bytes(base64.b64decode(result.audio_base64))
        probe = await FFprobeAudioValidator().validate_bytes((MEDIA / body.output).read_bytes(), "audio/mpeg")
        return {"file": body.output, "metadata": dataclasses.asdict(probe), "provider": result.provider}
    except Exception as exc:
        raise HTTPException(502, "配音生成失败，请检查声线和网络连接") from exc


class Clip(BaseModel):
    file: str
    duration: float = Field(gt=0, le=30)
    subtitle: str = Field(default="", max_length=2500)
    audio: str | None = None


class Render(BaseModel):
    clips: list[Clip] = Field(min_length=1, max_length=120)
    output: str = Field(pattern=r"^art_[a-f0-9]+\.mp4$")
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    subtitles: bool = True
    preserve_audio: bool = True


async def command(*args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 300)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise HTTPException(422, "视频标准化失败：请检查输入视频是否可解码及 FFmpeg 安装")


@app.post("/render")
async def render(body: Render):
    width, height = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}[body.aspect_ratio]
    clips, cues, tracks = [], [], []
    elapsed = 0.0
    with tempfile.TemporaryDirectory(prefix="frameweave-normalize-") as tmp:
        for i, clip in enumerate(body.clips):
            source = local_file(clip.file)
            target = Path(tmp) / f"{i}.mp4"
            # Normalize dimensions/fps, trim excess; never stretch too-short output silently.
            probe = await FFprobeVideoValidator().validate_bytes(source.read_bytes(), "video/mp4")
            if probe.duration_seconds + 0.15 < clip.duration:
                raise HTTPException(422, f"第 {i + 1} 镜视频时长不足；请重新生成或修改分镜时长")
            audio_args = ["-an"]
            await command(
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-t",
                str(clip.duration),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                *audio_args,
                str(target),
            )
            clips.append(target.read_bytes())
            if body.subtitles and clip.subtitle.strip():
                cues.append(SubtitleCue(elapsed, elapsed + clip.duration, clip.subtitle))
            if clip.audio:
                audio_path = local_file(clip.audio)
                ap = await FFprobeAudioValidator().validate_bytes(audio_path.read_bytes(), "audio/mpeg")
                if ap.duration_seconds > clip.duration + 0.15:
                    raise HTTPException(422, f"第 {i + 1} 镜配音长于分镜；请调整台词或时长")
                tracks.append(
                    AudioTrackInput(
                        content=audio_path.read_bytes(), content_type="audio/mpeg", start_seconds=elapsed
                    )
                )
            elif body.preserve_audio and probe.audio_stream_count:
                audio_path = Path(tmp) / f"{i}.m4a"
                await command(
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-t",
                    str(clip.duration),
                    "-vn",
                    "-c:a",
                    "aac",
                    str(audio_path),
                )
                tracks.append(
                    AudioTrackInput(
                        content=audio_path.read_bytes(), content_type="audio/mp4", start_seconds=elapsed
                    )
                )
            elapsed += clip.duration
        result = await StudioRenderer(
            width, height, timeout_seconds=600, render_preset="veryfast", render_tune="film"
        ).render(clips, audio_tracks=tracks, subtitles=cues)
    (MEDIA / body.output).write_bytes(result.content)
    srt = body.output.replace(".mp4", ".srt")
    (MEDIA / srt).write_bytes(serialize_srt(cues))
    return {"file": body.output, "subtitle_file": srt, "metadata": result.as_metadata()}
