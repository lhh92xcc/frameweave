"""Tencent TMS/IMS/VMS TC3 moderation boundary; safe redacted diagnostics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx

from studio.providers import ProviderFailure

SERVICE = {
    "text": ("tms.tencentcloudapi.com", "tms", "2020-12-29", "TextModeration"),
    "image": ("ims.tencentcloudapi.com", "ims", "2020-12-29", "ImageModeration"),
}


def _headers(host, service, version, action, body, sid, sk):
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    canonical = f"POST\n/\n\ncontent-type:application/json\nhost:{host}\n\ncontent-type;host\n{hashlib.sha256(body).hexdigest()}"
    scope = f"{date}/{service}/tc3_request"
    msg = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    def mac(k, v):
        return hmac.new(k, v.encode(), hashlib.sha256).digest()

    key = mac(mac(mac(("TC3" + sk).encode(), date), service), "tc3_request")
    sig = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(ts),
        "Authorization": f"TC3-HMAC-SHA256 Credential={sid}/{scope}, SignedHeaders=content-type;host, Signature={sig}",
    }


async def moderate(client, kind, content, secret_id, secret_key, biz_type=""):
    if kind not in SERVICE or not secret_id or not secret_key:
        raise ProviderFailure("MODERATION_NOT_CONFIGURED", "腾讯内容安全缺少 SecretId/SecretKey", True)
    host, service, version, action = SERVICE[kind]
    payload = {"BizType": biz_type, "DataId": "frameweave"}
    payload["Content" if kind == "text" else "FileContent"] = base64.b64encode(
        content if isinstance(content, bytes) else str(content).encode()
    ).decode()
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    try:
        r = await client.post(
            "https://" + host,
            content=body,
            headers=_headers(host, service, version, action, body, secret_id, secret_key),
            follow_redirects=False,
        )
        r.raise_for_status()
        out = r.json().get("Response", {})
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ProviderFailure("MODERATION_REQUEST_FAILED", "腾讯内容安全请求失败，未放行内容") from exc
    if "Error" in out:
        raise ProviderFailure("MODERATION_REJECTED", "腾讯内容安全未完成审核，已停止后续生产")
    suggestion = out.get("Suggestion", "Review")
    return {
        "suggestion": suggestion,
        "label": out.get("Label"),
        "score": out.get("Score"),
        "request_id": out.get("RequestId"),
    }


async def request(client, config, action, payload):
    host, service, version = "vm.tencentcloudapi.com", "vm", "2020-12-29"
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = _headers(host, service, version, action, body, config.secret_id, config.secret_key)
    headers["X-TC-Region"] = config.region
    try:
        response = await client.post("https://" + host, content=body, headers=headers, follow_redirects=False)
        response.raise_for_status()
        result = response.json()["Response"]
        if "Error" in result:
            raise ValueError("rejected")
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ProviderFailure("MODERATION_REQUEST_FAILED", "腾讯视频审核失败，未放行内容") from exc


async def check(providers, job, kind, content):
    """Only Pass proceeds. Video task identity survives retries and worker restarts."""
    import asyncio
    from pathlib import Path

    from studio.cloud_storage import CosStorage
    from studio.store import digest

    config = providers.settings.get().moderation
    if not config.enabled:
        return
    if not config.secret_id or not config.secret_key:
        raise ProviderFailure("MODERATION_NOT_CONFIGURED", "请填写腾讯内容安全 CAM 密钥")
    raw = content.read_bytes() if isinstance(content, Path) else str(content).encode()
    key = digest({"kind": kind, "sha": hashlib.sha256(raw).hexdigest(), "biz": config.biz_type})
    records = job["runtime"].setdefault("moderation", {})
    record = records.setdefault(key, {})
    if record.get("suggestion") == "Pass":
        return
    if kind != "video":
        record.update(
            await moderate(providers.client, kind, raw, config.secret_id, config.secret_key, config.biz_type)
        )
    else:
        if not record.get("task_id"):
            if record.get("submitting"):
                raise ProviderFailure("SUBMISSION_UNKNOWN", "视频审核提交结果不明，请先核对腾讯云任务")
            cloud = CosStorage(providers.store.root)
            ref = await asyncio.to_thread(cloud.upload, content, "video/mp4")
            url = cloud.signed_url(ref)
            record["submitting"] = True
            providers.checkpoint(job, moderation=records)
            result = await request(
                providers.client,
                config,
                "CreateVideoModerationTask",
                {
                    "BizType": config.biz_type,
                    "Type": "VIDEO",
                    "Tasks": [{"DataId": key, "Input": {"Type": "URL", "Url": url}}],
                },
            )
            item = (result.get("Results") or [{}])[0]
            if not item.get("TaskId"):
                raise ProviderFailure(
                    "MODERATION_REQUEST_FAILED", "视频审核未返回任务 ID，请先核对腾讯云任务"
                )
            record.update(task_id=item["TaskId"], submitting=False)
            providers.checkpoint(job, moderation=records)
        for _ in range(60):
            result = await request(
                providers.client, config, "DescribeTaskDetail", {"TaskId": record["task_id"]}
            )
            if result.get("Status") == "FINISH":
                record["suggestion"] = result.get("Suggestion", "Review")
                break
            if result.get("Status") in {"ERROR", "CANCEL", "CANCELLED"}:
                raise ProviderFailure("MODERATION_REQUEST_FAILED", "视频审核未完成，已停止后续生产")
            await asyncio.sleep(5)
        else:
            raise ProviderFailure("MODERATION_PENDING", "视频仍在审核，重试将继续查询原任务")
    providers.checkpoint(job, moderation=records)
    if record.get("suggestion") != "Pass":
        raise ProviderFailure(
            "MODERATION_REVIEW" if record.get("suggestion") == "Review" else "MODERATION_BLOCKED",
            "内容审核未通过或需人工复核，已停止后续生产；请在腾讯云核查",
        )
