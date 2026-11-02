"""Versioned user production defaults; provider capabilities remain hard boundaries."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
STAGES = ["validate", "video", "tts", "mix", "subtitles", "danmaku", "compose"]


def default_document():
    return (ROOT / "docs/PRODUCTION_PIPELINE_RULES.md").read_text()


class RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ViduRules(RuleModel):
    default_model: Literal["vidu-video-q2", "vidu-video-q2-pro", "vidu-video-q2-turbo", "vidu-video-q3-pro", "vidu-video-q3-turbo"] = Field(default="vidu-video-q3-turbo", title="无硬限制时默认模型")
    audio_model: Literal["vidu-video-q3-pro", "vidu-video-q3-turbo"] = Field(default="vidu-video-q3-turbo", title="需要同步声音时的 Q3 模型")
    bgm_model: Literal["vidu-video-q2", "vidu-video-q2-pro", "vidu-video-q2-turbo"] = Field(default="vidu-video-q2-turbo", title="需要背景音乐时的 Q2 模型")
    long_model: Literal["vidu-video-q3-pro", "vidu-video-q3-turbo"] = Field(default="vidu-video-q3-turbo", title="11–16 秒镜头模型")
    resolution: Literal["720p", "1080p"] = Field(default="720p", title="生成分辨率")
    reference_max_seconds: float = Field(default=5, gt=0, le=5, title="视频参考最长秒数（供应商上限 5 秒）")
    reference_max_mb: int = Field(default=100, ge=1, le=100, title="视频参考最大 MB（上限 100 MB）")
    poll_protocol: Literal["post_task_get", "get_tasks"] = Field(default="post_task_get", title="查询协议（文档默认 POST task/get；旧适配 GET tasks/id）")
    poll_seconds: float = Field(default=5, ge=1, le=60, title="任务查询间隔（秒）")
    poll_limit: int = Field(default=720, ge=1, le=17280, title="单轮最大查询次数")


class AudioRules(RuleModel):
    post_production: bool = Field(default=True, title="独立生成 TTS 并后置混音")
    preserve_vidu_audio: bool = Field(default=False, title="混音时额外保留 Vidu 预览原声")
    normalize_speech: bool = Field(default=True, title="人声音量标准化和峰值限制")
    bgm_db: float = Field(default=-14, ge=-60, le=0, title="无人声时 BGM 音量 dB（默认区间 -15 至 -12）")
    duck_db: float = Field(default=-20, ge=-60, le=0, title="有人声时 BGM 音量 dB")
    tts_failure: Literal["stop", "silent_marked"] = Field(default="stop", title="TTS 明确失败处理（缺锁定声音和未知提交始终停止）")

    @model_validator(mode="after")
    def duck(self):
        if self.duck_db > self.bgm_db:
            raise ValueError("有人声时 BGM 音量不能高于无人声时")
        return self


class SubtitleRules(RuleModel):
    enabled: bool = Field(default=True, title="生成底部台词 ASS 字幕")
    font: str = Field(default="Microsoft YaHei", min_length=1, max_length=80, pattern=r"^[\w \-\u4e00-\u9fff]+$", title="字幕字体（缺字时使用已安装 CJK 字体回退）")
    font_size: int = Field(default=30, ge=8, le=160, title="字幕字号")
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$", title="字幕颜色")
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$", title="描边颜色")
    outline: int = Field(default=2, ge=0, le=10, title="描边宽度")
    shadow: int = Field(default=1, ge=0, le=10, title="阴影宽度")
    margin: int = Field(default=30, ge=0, le=300, title="底部边距")


class DanmakuRules(RuleModel):
    enabled: bool = Field(default=True, title="有弹幕时间轴时叠加滚动 ASS")
    duration: float = Field(default=5, gt=0, le=60, title="默认滚动秒数")
    lanes: int = Field(default=4, ge=1, le=20, title="自动分配轨道数")
    top: int = Field(default=180, ge=0, le=2000, title="第一轨高度")
    gap: int = Field(default=80, ge=20, le=300, title="轨道间距")
    font_size: int = Field(default=30, ge=8, le=100, title="弹幕字号")


class RenderRules(RuleModel):
    timeline_overflow: Literal["trim", "error"] = Field(default="trim", title="时间轴超出视频时长处理（截断/丢弃会记录）")
    crf: int = Field(default=23, ge=0, le=51, title="H.264 CRF")
    preset: Literal["ultrafast", "veryfast", "fast", "medium", "slow"] = Field(default="medium", title="编码预设")
    audio_kbps: int = Field(default=128, ge=32, le=320, title="AAC 码率 kbps")
    fps: int = Field(default=30, ge=12, le=60, title="成片帧率")


class BillingRules(RuleModel):
    q2_per_credit: float | None = Field(default=None, ge=0, title="Q2 普通每积分价格（未知留空）")
    q2_pro_per_credit: float | None = Field(default=None, ge=0, title="Q2 Pro 每积分价格（未知留空）")
    q3_pro_per_credit: float | None = Field(default=None, ge=0, title="Q3 Pro 每积分价格（未知留空）")
    q3_turbo_per_credit: float | None = Field(default=None, ge=0, title="Q3 Turbo 每积分价格（未知留空）")
    currency: Literal["CNY", "USD"] = Field(default="CNY", title="价格币种")


class PipelineRules(RuleModel):
    vidu: ViduRules = Field(default_factory=ViduRules, title="视频分流与输入限制")
    audio: AudioRules = Field(default_factory=AudioRules, title="TTS、人声、BGM 与音效")
    subtitles: SubtitleRules = Field(default_factory=SubtitleRules, title="底部 ASS 字幕")
    danmaku: DanmakuRules = Field(default_factory=DanmakuRules, title="滚动 ASS 弹幕")
    render: RenderRules = Field(default_factory=RenderRules, title="时间轴与 FFmpeg 合成")
    billing: BillingRules = Field(default_factory=BillingRules, title="分组计费记录")
    instructions: str = Field(default_factory=default_document, min_length=100, max_length=100000, title="完整制作规则说明（默认保留用户原文）")


class PipelineEdit(RuleModel):
    rules: PipelineRules
    expected_revision: int = Field(ge=0)


def public_rules(settings):
    return {"rules": settings.pipeline.model_dump(), "defaults": PipelineRules().model_dump(),
            "revision": settings.pipeline_revision, "schema": PipelineRules.model_json_schema(),
            "stages": STAGES,
            "hard_limits": ["视频参考只能 Q2-Pro；Q2 最长 10 秒、Q3 最长 16 秒", "audio 与 bgm 互斥，Q2 不接受 audio/audio_type，Q3 不接受 bgm/videos", "不得删除实际参考素材来绕过硬限制", "保留锁定三个人声与猫叫，禁止新建定制声音；缺失音色停止", "提交结果不明先核对，不重复扣费", "实际价格、积分和模型能力须以供应商为准；查询协议尚需真实账号验证"],
            "precedence": "接口能力和固定音色限制 → 已批准项目规范 → 结构化规则 → 分镜偏好。文字说明供规划及审核；改变执行参数请同时修改对应表单。"}
