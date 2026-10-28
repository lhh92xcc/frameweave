from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REFERENCE_ROLES = {
    "character": {"general", "front", "side", "full_body", "costume", "accessory"},
    "location": {"general", "wide", "reverse", "spatial"},
    "prop": {"general", "detail", "held"},
    "product": {"general", "detail", "held"},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    mode: Literal["drama", "commerce", "avatar", "explainer", "fantasy", "global"] = "drama"
    source: str = Field(min_length=8, max_length=2_000_000)
    brief: str = Field(default="", max_length=20_000)
    episodes: int = Field(default=3, ge=1, le=50)
    duration: int = Field(default=60, ge=5, le=600)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    language: str = Field(default="简体中文", max_length=80)
    style: str = Field(default="电影感、自然光、克制表演", max_length=1000)


class ContractEdit(StrictModel):
    markdown: str = Field(min_length=100, max_length=100_000)
    expected_revision: int = Field(ge=0)


class Asset(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["character", "location", "prop", "product"]
    description: str = Field(min_length=5, max_length=2000)
    prompt: str = Field(min_length=5, max_length=4000)


class Shot(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=5, max_length=4000)
    image_prompt: str = Field(min_length=5, max_length=4000)
    video_prompt: str = Field(min_length=5, max_length=4000)
    duration: int = Field(ge=1, le=30)
    narration: str = Field(default="", max_length=1000)
    dialogue: str = Field(default="", max_length=1500)
    asset_ids: list[str] = Field(default_factory=list, max_length=14)


class Episode(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    synopsis: str = Field(min_length=5, max_length=3000)
    source_chapters: list[int] = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1, max_length=120)


class ProductionPlan(StrictModel):
    assets: list[Asset] = Field(max_length=120)
    episodes: list[Episode] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def references_exist(self):
        ids = [x.id for x in self.assets] + [e.id for e in self.episodes]
        ids += [s.id for e in self.episodes for s in e.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("素材、集和分镜的 ID 必须全局唯一")
        assets = {a.id for a in self.assets}
        for episode in self.episodes:
            for shot in episode.shots:
                if set(shot.asset_ids) - assets:
                    raise ValueError(f"{shot.id} 引用了不存在的素材")
                if len(shot.asset_ids) != len(set(shot.asset_ids)):
                    raise ValueError("分镜不能重复绑定素材")
        return self


class Route(StrictModel):
    transport: Literal["gateway", "cloud"] = "gateway"
    provider: str = Field(default="gemini", pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    model: str = Field(default="default", min_length=1, max_length=160)
    base_url: str = Field(default="", max_length=2048)
    api_key: str | None = Field(default=None, max_length=8192, repr=False)
    clear_key: bool = False
    qc_max_cost_cny: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        from urllib.parse import urlsplit

        if not value:
            return value
        url = urlsplit(value)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("模型地址需为不含凭据和查询参数的 HTTP(S) 地址")
        return value.rstrip("/")


class SpeechControls(StrictModel):
    speed: float = Field(default=1, ge=0.5, le=2, allow_inf_nan=False)
    vol: float = Field(default=1, gt=0, le=10, allow_inf_nan=False)
    pitch: int = Field(default=0, ge=-12, le=12)
    emotion: Literal["happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm"] | None = None


class VoiceProfile(SpeechControls):
    voice_id: str = Field(min_length=1, max_length=160)
    library_id: str | None = Field(default=None, pattern=r"^voice_[a-f0-9]{20}$")
    source: Literal["recording"] | None = None


class AudioCast(StrictModel):
    voices: dict[str, VoiceProfile] = Field(default_factory=dict, max_length=120)
    speakers: dict[str, str] = Field(default_factory=dict, max_length=6000)
    expected_version: int = Field(ge=0)


class RoutingSettings(StrictModel):
    allow_paid: bool = False
    text: list[Route] = Field(default_factory=lambda: [Route()], min_length=1, max_length=6)
    image: list[Route] = Field(
        default_factory=lambda: [Route(model="gemini-pro")], min_length=1, max_length=6
    )
    vision: list[Route] = Field(default_factory=lambda: [Route()], min_length=1, max_length=6)
    image_qc_attempts: int = Field(default=3, ge=1, le=5)
    image_qc_budget_cny: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    video: list[Route] = Field(
        default_factory=lambda: [Route(provider="jimeng-web", model="jimeng-seedance-2.0-mini")],
        min_length=1,
        max_length=6,
    )
    voice: str = Field(default="zh-CN-XiaoxiaoNeural", min_length=1, max_length=160)
    audio_mode: Literal["native", "edge", "cloud", "silent"] = "native"
    audio: Route = Field(default_factory=lambda: Route(transport="cloud", model="tts-1"))
    speech: SpeechControls = Field(default_factory=SpeechControls)
    audio_revision: int = Field(default=0, ge=0)
    subtitles: bool = True

    @model_validator(mode="after")
    def minimax_origin(self):
        if self.audio.provider == "minimax" and (
            self.audio.transport != "cloud"
            or self.audio.base_url != "https://api.minimaxi.com/v1"
            or self.audio.model not in {"speech-2.8-turbo", "speech-2.8-hd"}
        ):
            raise ValueError("MiniMax 配音须使用官方地址 https://api.minimaxi.com/v1 和 speech-2.8 模型")
        return self


class StageRequest(StrictModel):
    stage: Literal["requirements", "plan", "reference", "keyframe", "video", "audio", "render"]
    entity_id: str = Field(default="", max_length=80)
    force: bool = False
    reference_source_id: str | None = Field(default=None, min_length=1, max_length=80)


class ShotReferences(StrictModel):
    artifact_ids: list[str] = Field(default_factory=list, max_length=9)
    previous_shot_id: str | None = Field(default=None, max_length=64)
    expected_version: int = Field(ge=0)


class ReferenceRoleEdit(StrictModel):
    role: str = Field(min_length=1, max_length=40)


class Review(StrictModel):
    approved: bool
    note: str = Field(default="", max_length=2000)
    expected_review: Literal["pending"] | None = None


class PlanEdit(StrictModel):
    plan: ProductionPlan
    expected_revision: int = Field(ge=0)


class RunRequest(StrictModel):
    enabled: bool = True
