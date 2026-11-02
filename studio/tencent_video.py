"""Tencent VCLM Vidu adapter. Offline tests do not establish provider availability."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from studio.providers import ProviderFailure

ENDPOINT = "https://vclm.tencentcloudapi.com"
MODELS = {"viduq2-pro", "viduq2-turbo", "viduq3-pro", "viduq3-turbo"}


def signed_headers(action, body, secret_id, secret_key, timestamp):
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
    canonical = "POST\n/\n\ncontent-type:application/json\nhost:vclm.tencentcloudapi.com\n\ncontent-type;host\n"
    canonical += hashlib.sha256(body).hexdigest()
    scope = date + "/vclm/tc3_request"
    message = "TC3-HMAC-SHA256\n" + str(timestamp) + "\n" + scope + "\n" + hashlib.sha256(canonical.encode()).hexdigest()
    def mac(key, value):
        return hmac.new(key, value.encode(), hashlib.sha256).digest()
    key = mac(mac(mac(("TC3" + secret_key).encode(), date), "vclm"), "tc3_request")
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json", "Host": "vclm.tencentcloudapi.com",
        "X-TC-Action": action, "X-TC-Version": "2024-05-23", "X-TC-Timestamp": str(timestamp),
        "Authorization": f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={signature}",
    }


async def request(provider, route, action, payload):
    secret_id, secret_key = route.api_key.split(":", 1)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    try:
        response = await provider.client.post(
            ENDPOINT, content=body,
            headers=signed_headers(action, body, secret_id, secret_key, int(time.time())),
            follow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()["Response"]
        if not isinstance(result, dict) or "Error" in result:
            raise ValueError("provider error")
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        # Never expose raw provider errors, credentials, response bodies or signed requests.
        raise ProviderFailure("TENCENT_REQUEST_FAILED", "腾讯云请求未得到有效结果；已有任务可继续查询，提交结果不明时请先核对腾讯云任务与账单") from exc


async def generate(provider, job, route, prompt, refs, duration):
    if route.base_url != ENDPOINT:
        raise ProviderFailure("TENCENT_ENDPOINT", "腾讯云视频接口地址必须为 https://vclm.tencentcloudapi.com")
    if not route.api_key or ":" not in route.api_key or not all(route.api_key.split(":", 1)):
        raise ProviderFailure("TENCENT_CREDENTIALS", "请填写腾讯云 SecretId:SecretKey", True)
    if route.model not in MODELS:
        raise ProviderFailure("TENCENT_MODEL", "请选择腾讯云 Vidu Q2/Q3 Pro 或 Turbo", True)
    if len(prompt) > 2000:
        raise ProviderFailure("TENCENT_PROMPT_LIMIT", "腾讯云 Vidu 提示词最多 2000 字符；完整规范与绑定超过上限，未截断或提交")
    if len(refs) != 1:
        raise ProviderFailure("TENCENT_REFERENCES", "腾讯云首帧模式需要且仅支持一张已批准关键帧；不会丢弃额外参考或将其当成尾帧")
    if int(duration) != duration or not 1 <= duration <= (16 if route.model.startswith("viduq3") else 10):
        raise ProviderFailure("TENCENT_DURATION", "腾讯云 Q2 首帧视频支持 1–10 整秒，Q3 支持 1–16 整秒")
    if refs[0].stat().st_size > 50 * 1024 * 1024:
        raise ProviderFailure("TENCENT_IMAGE_LIMIT", "腾讯云首帧图片超过 50 MB 上限")
    task = job["runtime"].get("tencent_task")
    if task and task["call"] != job["runtime"]["active_call"]:
        task = None
    if not task:
        if job["runtime"].get("cloud_submitting"):
            raise ProviderFailure("SUBMISSION_UNKNOWN", "腾讯云上次提交结果不明，请先核对任务和账单")
        payload = {
            "Model": route.model, "Prompt": prompt,
            "Images": [base64.b64encode(refs[0].read_bytes()).decode()],
            "Duration": int(duration), "Resolution": "720p", "Audio": False, "Bgm": False, "IsRec": False,
        }
        provider.checkpoint(job, cloud_submitting=True)
        result = await request(provider, route, "SubmitImageToVideoViduJob", payload)
        task_id = result.get("JobId")
        if not isinstance(task_id, str) or not task_id:
            raise ProviderFailure("SUBMISSION_UNKNOWN", "腾讯云没有返回任务 ID，请先核对任务与账单")
        task = {"id": task_id, "call": job["runtime"]["active_call"]}
        provider.checkpoint(job, tencent_task=task, cloud_submitting=False)
    for _ in range(720):
        provider.checkpoint(job, tencent_task=task)
        result = await request(provider, route, "DescribeImageToVideoViduJob", {"JobId": task["id"]})
        status = result.get("Status")
        if status == "FAIL":
            raise ProviderFailure("TENCENT_TASK_FAILED", "腾讯云视频任务失败，请在腾讯云控制台查看原因")
        if status == "DONE":
            url = result.get("ResultVideoUrl", "")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderFailure("TENCENT_RESULT_INVALID", "腾讯云未返回有效 HTTPS 视频地址")
            try:
                # No signed headers or credentials on the media origin, including redirects.
                response = await provider.client.get(url, follow_redirects=False)
                response.raise_for_status()
                if response.is_redirect or not response.content:
                    raise ValueError("invalid media")
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderFailure("TENCENT_DOWNLOAD_FAILED", "视频下载失败；重试只查询并下载已有腾讯云任务") from exc
            return {"content": response.content, "mime": "video/mp4", "metadata": {"provider_job_id": task["id"]}}
        if status not in {"WAIT", "RUN"}:
            raise ProviderFailure("TENCENT_STATUS_UNKNOWN", "腾讯云任务状态未知；保留任务 ID，未重新提交")
        await asyncio.sleep(5)
    raise ProviderFailure("TENCENT_POLL_TIMEOUT", "腾讯云任务仍未完成；重试继续查询原任务")
