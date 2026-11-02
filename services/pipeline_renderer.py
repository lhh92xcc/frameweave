"""Post-produced voice/music/SFX and two ASS layers, using approved local inputs."""
from __future__ import annotations

import dataclasses
import math

from app.media.audio_validation import FFprobeAudioValidator
from app.media.video_validation import FFprobeVideoValidator
from fastapi import HTTPException


def ass_time(seconds):
    total = max(0, round(seconds * 100))
    hours, rest = divmod(total, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centis:02}"


def ass_text(text):
    # No user text can introduce override tags or extra Dialogue events.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")


def ass_color(value):
    return "&H00" + value[5:7] + value[3:5] + value[1:3]


def ass_header(width, height, style, font_size=None, alignment=2):
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{style.font},{font_size or style.font_size},{ass_color(style.color)},&H000000FF,{ass_color(style.outline_color)},&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},{alignment},20,20,{style.margin},1\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def interval(start, end, duration, policy, warnings, label):
    if start >= duration and policy == "trim":
        warnings.append(f"{label} 超出时长，已丢弃")
        return None
    if start < 0 or end <= start:
        raise HTTPException(422, f"{label} 时间范围无效")
    if end > duration:
        if policy == "error":
            raise HTTPException(422, f"{label} 超出视频时长")
        warnings.append(f"{label} 超出时长，已{'丢弃' if start >= duration else '截断'}")
    return (start, min(end, duration)) if start < duration else None


def subtitle_ass(cues, width, height, style):
    rows = [ass_header(width, height, style)]
    for start, end, text in cues:
        rows.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{ass_text(text)}\n")
    return "".join(rows)


def danmaku_ass(cues, duration, width, height, rules, warnings):
    rows = [ass_header(width, height, rules.subtitles, rules.danmaku.font_size, 7)]
    # A lane is only reused after its previous text has fully left the screen.
    lanes = [0.0] * rules.danmaku.lanes
    usable = [i for i in range(len(lanes)) if rules.danmaku.top + i * rules.danmaku.gap + rules.danmaku.font_size < height]
    if cues and not usable:
        raise HTTPException(422, "弹幕轨道超出画布，请调整顶部位置或轨道间距")
    for index, cue in enumerate(sorted(cues, key=lambda c: c.start)):
        lane = min(usable, key=lambda i: lanes[i])
        start = max(cue.start, lanes[lane])
        if start != cue.start:
            warnings.append(f"弹幕 {index + 1} 避免重叠，延后到 {start:.2f} 秒")
        end = start + (cue.duration or rules.danmaku.duration)
        clipped = interval(start, end, duration, rules.render.timeline_overflow, warnings, f"弹幕 {index + 1}")
        if clipped is None:
            continue
        start, end = clipped
        lanes[lane] = end
        y = rules.danmaku.top + lane * rules.danmaku.gap
        travel = max(400, len(cue.text) * rules.danmaku.font_size)
        tag = f"{{\\move({width},{y},-{travel},{y})\\1c{ass_color(cue.color)}&}}"
        rows.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{tag}{ass_text(cue.text)}\n")
    return "".join(rows)


async def render_pipeline(body, media, local_file, command):
    rules = body.pipeline_rules
    width, height = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}[body.aspect_ratio]
    duration = sum(clip.duration for clip in body.clips)
    stem = body.output.removesuffix(".mp4")
    files = {name: media / f"{stem}_{name}" for name in ("raw.mp4", "speech_track.mp3", "mix_audio.mp3", "subtitle.ass", "danmaku.ass")}
    warnings, cues, speech, native = [], [], [], []
    video_inputs, video_filters, elapsed = [], [], 0.0
    for i, clip in enumerate(body.clips):
        path = local_file(clip.file)
        probe = await FFprobeVideoValidator().validate_bytes(path.read_bytes(), "video/mp4")
        if probe.duration_seconds + 0.15 < clip.duration:
            raise HTTPException(422, f"第 {i + 1} 镜视频不足要求时长，禁止伸长掩盖")
        video_inputs += ["-i", str(path)]
        video_filters.append(f"[{i}:v]trim=duration={clip.duration},setpts=PTS-STARTPTS,fps={rules.render.fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]")
        end = clip.speech_end if clip.speech_end is not None else clip.duration
        span = interval(clip.speech_start, end, clip.duration, rules.render.timeline_overflow, warnings, f"第 {i + 1} 镜台词")
        if span:
            start, end = span
            if rules.subtitles.enabled and clip.subtitle.strip():
                cues.append((elapsed + start, elapsed + end, clip.subtitle))
            if clip.audio:
                audio_path = local_file(clip.audio)
                probe_audio = await FFprobeAudioValidator().validate_bytes(audio_path.read_bytes(), "audio/mpeg")
                if probe_audio.duration_seconds > end - start + 0.15:
                    if rules.render.timeline_overflow == "error":
                        raise HTTPException(422, f"第 {i + 1} 镜人声超过台词时间轴")
                    warnings.append(f"第 {i + 1} 镜人声已截断到台词结束时间")
                speech.append((audio_path, elapsed + start, min(end - start, probe_audio.duration_seconds)))
        if body.preserve_audio and probe.audio_stream_count:
            native.append((path, elapsed, clip.duration))
        elapsed += clip.duration
    video_filters.append("".join(f"[v{i}]" for i in range(len(body.clips))) + f"concat=n={len(body.clips)}:v=1:a=0[v]")
    await command("ffmpeg", "-y", "-v", "error", *video_inputs, "-filter_complex", ";".join(video_filters), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-t", str(duration), str(files["raw.mp4"]))
    # Assemble and normalize separately, preserving timing from the shared dialogue cues.
    inputs, filters = [], []
    for i, (path, start, length) in enumerate(speech):
        inputs += ["-i", str(path)]
        normalization = "loudnorm=I=-16:TP=-1.5:LRA=11," if rules.audio.normalize_speech else ""
        filters.append(f"[{i}:a]atrim=duration={length},asetpts=PTS-STARTPTS,{normalization}alimiter=limit=0.95,adelay={round(start * 1000)}:all=1[a{i}]")
    if speech:
        filters.append("".join(f"[a{i}]" for i in range(len(speech))) + f"amix=inputs={len(speech)}:normalize=0,apad=whole_dur={duration},atrim=duration={duration},alimiter=limit=0.95[a]")
        await command("ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters), "-map", "[a]", "-t", str(duration), "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", str(files["speech_track.mp3"]))
    else:
        await command("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration), "-c:a", "libmp3lame", str(files["speech_track.mp3"]))
    mix_inputs, mix_filters, labels = ["-i", str(files["speech_track.mp3"])], ["[0:a]asetpts=PTS-STARTPTS[m0]"], ["[m0]"]
    next_index = 1
    if body.bgm:
        path = local_file(body.bgm)
        await FFprobeAudioValidator().validate_bytes(path.read_bytes(), "audio/mpeg")
        mix_inputs += ["-stream_loop", "-1", "-i", str(path)]
        speaking = "+".join(f"between(t\\,{start:.3f}\\,{start + length:.3f})" for _, start, length in speech) or "0"
        level = f"if(gt({speaking}\\,0)\\,{math.pow(10, rules.audio.duck_db / 20):.8f}\\,{math.pow(10, rules.audio.bgm_db / 20):.8f})"
        mix_filters.append(f"[{next_index}:a]atrim=duration={duration},asetpts=PTS-STARTPTS,volume='{level}':eval=frame[m{next_index}]")
        labels.append(f"[m{next_index}]")
        next_index += 1
    for effect in body.sound_effects:
        path = local_file(effect.file)
        probe = await FFprobeAudioValidator().validate_bytes(path.read_bytes(), "audio/mpeg")
        span = interval(effect.time, effect.time + probe.duration_seconds, duration, rules.render.timeline_overflow, warnings, "音效")
        if span is None:
            continue
        start, end = span
        mix_inputs += ["-i", str(path)]
        mix_filters.append(f"[{next_index}:a]atrim=duration={end-start},asetpts=PTS-STARTPTS,volume={effect.volume},adelay={round(start*1000)}:all=1[m{next_index}]")
        labels.append(f"[m{next_index}]")
        next_index += 1
    for path, start, length in native:
        mix_inputs += ["-i", str(path)]
        mix_filters.append(f"[{next_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,adelay={round(start*1000)}:all=1[m{next_index}]")
        labels.append(f"[m{next_index}]")
        next_index += 1
    mix_filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,apad=whole_dur={duration},atrim=duration={duration},alimiter=limit=0.95[mix]")
    await command("ffmpeg", "-y", "-v", "error", *mix_inputs, "-filter_complex", ";".join(mix_filters), "-map", "[mix]", "-t", str(duration), "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", str(files["mix_audio.mp3"]))
    files["subtitle.ass"].write_text(subtitle_ass(cues, width, height, rules.subtitles))
    danmaku = body.danmaku if rules.danmaku.enabled else []
    files["danmaku.ass"].write_text(danmaku_ass(danmaku, duration, width, height, rules, warnings))
    def escape(path):
        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
    layers = []
    if rules.subtitles.enabled:
        layers.append(f"ass='{escape(files['subtitle.ass'])}'")
    if danmaku:
        layers.append(f"ass='{escape(files['danmaku.ass'])}'")
    await command("ffmpeg", "-y", "-v", "error", "-i", str(files["raw.mp4"]), "-i", str(files["mix_audio.mp3"]), "-vf", ",".join(layers) or "null", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-crf", str(rules.render.crf), "-preset", rules.render.preset, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{rules.render.audio_kbps}k", "-t", str(duration), "-movflags", "+faststart", str(media / body.output))
    final = await FFprobeVideoValidator().validate_bytes((media / body.output).read_bytes(), "video/mp4")
    meta = dataclasses.asdict(final)
    return {"file": body.output, "subtitle_file": files["subtitle.ass"].name,
            "metadata": {**meta, "ffprobe": meta, "input_clip_count": len(body.clips), "subtitle_count": len(cues),
                         "warnings": warnings, "pipeline_files": {name: path.name for name, path in files.items()},
                         "pipeline_order": ["video", "tts", "mix", "subtitles", "danmaku", "compose"]}}
