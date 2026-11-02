"""Official HTTP streaming TTS. No voice design, cloning or activation."""

from __future__ import annotations

import base64
import json
import uuid

import httpx

from studio.providers import ProviderFailure

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
# Explicit system voices; unknown/custom voice IDs never reach the service.
SYSTEM_VOICES = {"zh_female_vv_uranus_bigtts", "zh_male_m191_uranus_bigtts"}


async def synthesize(client, api_key, text, voice, *, resource_id="seed-tts-2.0", speed=1.0):
    if not api_key or not text.strip():
        raise ProviderFailure("VOLC_TTS_NOT_CONFIGURED", "请配置火山 TTS API Key 和台词")
    if voice not in SYSTEM_VOICES:
        raise ProviderFailure("VOICE_POLICY_BLOCKED", "请选择已支持的火山系统音色；不会创建或替换锁定音色")
    try:
        response = await client.post(
            ENDPOINT,
            headers={
                "X-Api-Key": api_key,
                "X-Api-Resource-Id": resource_id,
                "X-Api-Request-Id": str(uuid.uuid4()),
            },
            json={
                "user": {"uid": "frameweave"},
                "req_params": {
                    "text": text,
                    "speaker": voice,
                    "audio_params": {
                        "format": "mp3",
                        "sample_rate": 24000,
                        "speech_rate": round((speed - 1) * 100),
                    },
                },
            },
            follow_redirects=False,
        )
        if response.status_code in {400, 401, 403, 404, 422, 429}:
            raise ProviderFailure("VOLC_TTS_REJECTED", "火山 TTS 未接受请求，请检查权限、资源 ID 和额度")
        response.raise_for_status()
        chunks, completed = [], False
        for line in response.content.splitlines():
            if not line.strip():
                continue
            item = json.loads(line.removeprefix(b"data:").strip())
            if item.get("code") not in {0, 20000000}:
                raise ProviderFailure("VOLC_TTS_FAILED", "火山 TTS 返回失败，未接受不完整音频")
            if item.get("data"):
                chunks.append(base64.b64decode(item["data"], validate=True))
            completed = completed or item.get("code") == 20000000
        if not chunks or not completed:
            raise ValueError("incomplete stream")
        return b"".join(chunks)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise ProviderFailure(
            "SUBMISSION_UNKNOWN", "火山 TTS 响应不完整或结果不明，请核对供应商记录后处理"
        ) from exc
