"""Canvas-aware assembly around the unmodified upstream renderer.

The pinned engine assumes portrait output and inserts an empty filter before
subtitles. Override only command assembly; retain its validation, subtitle
preparation, audio mixing, output probing and result metadata.
"""

from __future__ import annotations

import asyncio
from time import monotonic

from app.rendering.ffmpeg_renderer import FFmpegRenderError, FFmpegVideoRenderer


class StudioRenderer(FFmpegVideoRenderer):
    def __init__(self, width: int, height: int, **kwargs):
        super().__init__(**kwargs)
        self.width, self.height = width, height

    @staticmethod
    def _normalize_audio_tracks(audio_content, audio_content_type, audio_tracks, video_duration):
        # Studio episodes permit up to 120 shots, each with its own narration.
        # Validate every track with upstream rules without its 32-track batch cap.
        if audio_content is None and not audio_content_type and audio_tracks and len(audio_tracks) > 32:
            return tuple(
                track
                for offset in range(0, len(audio_tracks), 32)
                for track in FFmpegVideoRenderer._normalize_audio_tracks(
                    None, None, audio_tracks[offset : offset + 32], video_duration
                )
            )
        return FFmpegVideoRenderer._normalize_audio_tracks(
            audio_content, audio_content_type, audio_tracks, video_duration
        )

    async def _run_ffmpeg(
        self,
        input_paths,
        output_path,
        audio_paths=(),
        audio_tracks=(),
        subtitle_path=None,
        video_duration=0,
        clip_durations=(),
    ):
        command = [self.binary, "-hide_banner", "-loglevel", "error", "-y"]
        for path in input_paths:
            command.extend(["-i", str(path)])
        for path, track in zip(audio_paths, audio_tracks, strict=True):
            if track.loop:
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(path)])
        filters = [
            f"[{i}:v]fps=30,scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
            for i in range(len(input_paths))
        ]
        labels = "".join(f"[v{i}]" for i in range(len(input_paths)))
        filters.append(f"{labels}concat=n={len(input_paths)}:v=1:a=0[vconcat]")
        subtitle = self._subtitle_filter(subtitle_path) if subtitle_path else "null"
        filters.append(f"[vconcat]{subtitle}[vout]")
        if audio_paths:
            filters.append(self._audio_filter(audio_tracks, video_duration, len(input_paths)))
        command.extend(["-filter_complex", ";".join(filters), "-map", "[vout]"])
        if audio_paths:
            command.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"])
        else:
            command.append("-an")
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                self.render_preset,
                "-crf",
                str(self.render_crf),
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "cfr",
                "-r",
                "30",
                "-movflags",
                "+faststart",
            ]
        )
        if self.render_tune:
            command.extend(["-tune", self.render_tune])
        command.extend(["-t", f"{video_duration:.3f}", str(output_path)])
        started = monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError as exc:
            raise FFmpegRenderError("FFMPEG_UNAVAILABLE", "找不到 FFmpeg，请重新安装运行环境") from exc
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
        except (TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise FFmpegRenderError("FFMPEG_TIMEOUT", "合成超时，请缩短单集长度后重试") from exc
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-1000:]
            raise FFmpegRenderError("FFMPEG_RENDER_FAILED", f"合成失败：{detail}")
        return max(1, round((monotonic() - started) * 1000)), tuple(command)
