"""MiniMax synchronous TTS; fixed origin, safe errors, no automatic paid retries."""

from __future__ import annotations

import re

import httpx

from studio.providers import ProviderFailure

API_ORIGIN = "https://api.minimaxi.com"
TTS_URL = API_ORIGIN + "/v1/t2a_v2"
VOICES_URL = API_ORIGIN + "/v1/get_voice"


class MiniMaxRejected(ProviderFailure):
    """The provider explicitly rejected the request, so no audio receipt exists."""


def response_context(response, api_key):
    """Keep useful support identifiers without echoing response bodies or secrets."""
    details = [f"HTTP {response.status_code}"]
    for header in ("trace_id", "x-trace-id", "x-request-id"):
        value = response.headers.get(header)
        if (
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value)
            and not (api_key and api_key in value)
        ):
            details.append(f"{header} {value}")
            break
    return "（" + "；".join(details) + "）"


async def request(client, url, api_key, payload, *, synthesis=False):
    if url not in {TTS_URL, VOICES_URL}:
        raise MiniMaxRejected("VOICE_CUSTOMIZATION_DISABLED", "已禁止定制、设计或克隆音色，只能复用现有音色")
    if not api_key:
        raise MiniMaxRejected("AUDIO_NOT_CONFIGURED", "请先保存 MiniMax API Key")
    try:
        response = await client.post(
            url,
            headers={"Authorization": "Bearer " + api_key},
            json=payload,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise ProviderFailure(
            "SUBMISSION_UNKNOWN" if synthesis else "MINIMAX_UNAVAILABLE",
            "MiniMax 配音结果不明，请先核对供应商记录，未自动重试" if synthesis
            else "无法连接 MiniMax，请检查网络后重试",
        ) from exc
    if response.status_code in {400, 401, 403, 404, 413, 422, 429}:
        raise MiniMaxRejected(
            "MINIMAX_REJECTED", "MiniMax 未接受请求，请检查 API Key、音色、参数与额度"
            + response_context(response, api_key),
        )
    if not response.is_success:
        raise ProviderFailure(
            "SUBMISSION_UNKNOWN" if synthesis else "MINIMAX_UNAVAILABLE",
            ("MiniMax 返回异常，配音结果需核对，未自动重试" if synthesis else "MiniMax 音色目录暂不可用")
            + response_context(response, api_key),
        )
    try:
        data = response.json()
        status = data["base_resp"]["status_code"]
        if not isinstance(status, int) or isinstance(status, bool):
            raise ValueError("invalid status")
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderFailure(
            "SUBMISSION_UNKNOWN" if synthesis else "MINIMAX_INVALID",
            ("MiniMax 返回格式异常，请先核对供应商记录" if synthesis else "MiniMax 音色目录格式异常")
            + response_context(response, api_key),
        ) from exc
    if status:
        if synthesis and status in {1000, 1001}:
            raise ProviderFailure(
                "SUBMISSION_UNKNOWN", "MiniMax 配音结果不明，请先核对供应商记录"
                + response_context(response, api_key),
            )
        # Never relay provider status_msg: it may echo input or credentials.
        message = "MiniMax 账户余额不足，请充值当前 Key 所属的 API 账户" if status == 1008 else (
            f"MiniMax 拒绝请求（代码 {status}），请检查密钥、音色和额度"
        )
        raise MiniMaxRejected("MINIMAX_REJECTED", message + response_context(response, api_key))
    return data


async def synthesize(client, api_key, model, text, voice):
    data = await request(client, TTS_URL, api_key, {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {k: v for k, v in voice.items() if v is not None},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        "output_format": "hex",
        "subtitle_enable": False,
    }, synthesis=True)
    try:
        audio = data["data"]
        encoded = audio["audio"]
        if audio.get("status") != 2 or not isinstance(encoded, str) or not 2 <= len(encoded) <= 64_000_000:
            raise ValueError("missing completed audio")
        raw = bytes.fromhex(encoded)
        if not raw:
            raise ValueError("empty audio")
    except (ValueError, TypeError, KeyError) as exc:
        raise ProviderFailure("SUBMISSION_UNKNOWN", "MiniMax 未返回完整音频，请先核对供应商记录") from exc
    extra = data.get("extra_info") or {}
    metadata = {key: extra[key] for key in (
        "audio_length", "audio_sample_rate", "audio_size", "bitrate", "audio_channel",
        "usage_characters", "usage_voice_count", "word_count",
    ) if isinstance(extra.get(key), int) and not isinstance(extra[key], bool)}
    trace_id = data.get("trace_id")
    if isinstance(trace_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", trace_id):
        metadata["trace_id"] = trace_id
    return raw, metadata


async def voices(client, api_key):
    data = await request(client, VOICES_URL, api_key, {"voice_type": "all"})
    result = []
    for kind in ("system_voice", "voice_cloning", "voice_generation"):
        items = data.get(kind) or []
        if not isinstance(items, list):
            raise ProviderFailure("MINIMAX_INVALID", "MiniMax 音色目录格式异常")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("voice_id"), str):
                continue
            result.append({
                "id": item["voice_id"][:160],
                "name": str(item.get("voice_name") or item["voice_id"])[:160],
                "kind": kind,
            })
    return {"voices": result}
