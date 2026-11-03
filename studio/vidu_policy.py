"""Deterministic per-shot Vidu selection, independent of credentials and networking."""
MODELS = {"vidu-video-q2", "vidu-video-q2-pro", "vidu-video-q2-turbo", "vidu-video-q3-pro", "vidu-video-q3-turbo"}


def select_vidu(options, duration, *, has_video_ref=False, default_model="auto", rules=None):
    rules = rules or {}
    audio, bgm = options.get("audio", False), options.get("bgm", False)
    if audio and bgm:
        raise ValueError("audio 和 bgm 不能同时开启")
    if isinstance(duration, bool) or int(duration) != duration or not 1 <= duration <= 16:
        raise ValueError("Vidu 时长必须为 1–16 整秒")
    family = options.get("family", "auto")
    preference = options.get("preference", "default")
    if default_model == "auto":
        default_model = rules.get("default_model", "vidu-video-q3-turbo")
    if default_model not in MODELS:
        raise ValueError("Vidu 默认模型无效")
    if has_video_ref:
        if audio or duration > 10:
            raise ValueError("视频参考只能用 Q2-Pro，不能同时要求 audio 或超过 10 秒")
        required, reason = "q2", "视频参考必须使用 Q2-Pro"
    elif audio:
        required, reason = "q3", "同步音频要求 Q3"
    elif bgm:
        if duration > 10:
            raise ValueError("bgm 只能用 Q2，时长不能超过 10 秒")
        required, reason = "q2", "背景音乐要求 Q2"
    elif duration > 10:
        required, reason = "q3", "超过 10 秒要求 Q3"
    else:
        required, reason = None, "按本镜画质、速度和模型家族偏好选择"
    if required and family not in {"auto", required}:
        raise ValueError("本镜指定的 Q2/Q3 家族与素材、声音或时长硬条件冲突")
    if required and preference == "default" and not has_video_ref:
        default_model = rules.get("audio_model" if audio else "bgm_model" if bgm else "long_model",
                                  "vidu-video-q2-turbo" if bgm else "vidu-video-q3-turbo")
    if has_video_ref:
        model = "vidu-video-q2-pro"
    else:
        chosen = required or (family if family != "auto" else
                              "q2" if preference == "budget" else
                              "q2" if default_model.startswith("vidu-video-q2") else "q3")
        if preference == "quality":
            model = f"vidu-video-{chosen}-pro"
        elif preference in {"speed", "budget"}:
            model = "vidu-video-q2" if preference == "budget" and chosen == "q2" else f"vidu-video-{chosen}-turbo"
        elif default_model.startswith(f"vidu-video-{chosen}"):
            model = default_model
        else:
            model = f"vidu-video-{chosen}-turbo"
    return {"model": model, "reason": reason, "audio": audio, "bgm": bgm,
            "has_video_ref": has_video_ref, "duration": int(duration)}


def validate_payload(body):
    model = body.get("model")
    if model not in MODELS:
        raise ValueError("未知 Vidu 模型")
    allowed = {"model", "prompt", "duration", "aspect_ratio", "resolution", "seed", "payload", "images", "subjects"}
    allowed |= {"bgm"} if model.startswith("vidu-video-q2") else {"audio", "audio_type"}
    if set(body) - allowed:
        raise ValueError("请求包含该模型不支持的字段")
    for subject in body.get("subjects", []):
        if (not isinstance(subject, dict) or set(subject) - {"name", "images", "videos"}
                or not isinstance(subject.get("name"), str) or not subject["name"]):
            raise ValueError("主体参考字段无效")
        if "videos" in subject and model != "vidu-video-q2-pro":
            raise ValueError("仅 Q2-Pro 接受 videos 字段")
    video = bool(body.get("videos")) or any(bool(s.get("videos")) for s in body.get("subjects", []))
    if model.startswith("vidu-video-q2"):
        if "audio" in body or "audio_type" in body:
            raise ValueError("Q2 请求禁止携带 audio 字段")
        if body.get("duration", 5) > 10:
            raise ValueError("Q2 最长 10 秒")
    else:
        if "bgm" in body:
            raise ValueError("Q3 请求禁止携带 bgm 字段")
        if "videos" in body or any("videos" in subject for subject in body.get("subjects", [])):
            raise ValueError("Q3 请求禁止携带 videos 字段")
    if video and model != "vidu-video-q2-pro":
        raise ValueError("视频参考只允许 Q2-Pro；不能静默删除 videos")
    select_vidu({"audio": body.get("audio", False), "bgm": body.get("bgm", False)},
                body.get("duration", 5), has_video_ref=video, default_model=model)
