"""TokenHub Vidu protocol from the pinned upstream adapter; no live validation."""
from __future__ import annotations

import asyncio
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image

from studio.cloud_storage import CloudStorageError, CosStorage
from studio.providers import ProviderFailure
from studio.vidu_policy import MODELS as MODELS
from studio.vidu_policy import select_vidu, validate_payload

ENDPOINT = "https://tokenhub.tencentmaas.com"


def unpack(data):
    if not isinstance(data, dict):
        raise ValueError("invalid response")
    for key in ("data", "result", "output"):
        value = data.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            data = {**data, **value}
    return data


async def request(provider, route, method, path, payload=None):
    try:
        response = await provider.client.request(
            method, ENDPOINT + path, json=payload,
            headers={"Authorization": "Bearer " + route.api_key}, follow_redirects=False,
        )
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("redirect")
        return unpack(response.json())
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ProviderFailure("TOKENHUB_REQUEST_FAILED", "TokenHub 未返回有效结果；提交结果不明请先核对，已有任务可重试查询") from exc


async def generate(provider, job, route, prompt, refs, duration):
    if route.base_url != ENDPOINT:
        raise ProviderFailure("TOKENHUB_ENDPOINT", "TokenHub 接口地址必须为 https://tokenhub.tencentmaas.com")
    if not route.api_key:
        raise ProviderFailure("TOKENHUB_CREDENTIALS", "请填写 TokenHub API Key", True)
    snapshot = job.get("snapshot", {})
    options = snapshot.get("entity", {}).get("vidu", {})
    rules = snapshot.get("pipeline_rules", {}).get("vidu", {})
    videos = job["runtime"].get("vidu_video_inputs", []) if options.get("reference_previous_video") else []
    if options.get("reference_previous_video") and not videos:
        raise ProviderFailure("TOKENHUB_INPUT", "视频参考绑定缺失，不能退回纯图片生成")
    try:
        decision = select_vidu(options, duration, has_video_ref=bool(videos), default_model=route.model, rules=rules)
    except ValueError as exc:
        raise ProviderFailure("TOKENHUB_DURATION" if "秒" in str(exc) else "TOKENHUB_POLICY", str(exc)) from exc
    route_index = next((i for i, item in enumerate(snapshot.get("routes", {}).get("video", []))
                        if (item["provider"], item["model"], item["base_url"], item["transport"])
                        == (route.provider, route.model, route.base_url, route.transport)), 0)
    saved = snapshot.get("vidu_decisions", {}).get(str(route_index))
    if saved is not None and saved != decision:
        raise ProviderFailure("TOKENHUB_POLICY", "实际素材与已批准的镜头分流快照不一致")
    if not refs:
        raise ProviderFailure("TOKENHUB_INPUT", "缺少已批准关键帧")
    for path in refs:
        try:
            with Image.open(path) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("unsupported image")
        except (OSError, ValueError) as exc:
            raise ProviderFailure("TOKENHUB_IMAGE", "TokenHub 图片需为 PNG、JPEG 或 WebP") from exc
    for item in videos:
        if item["artifact_id"] not in snapshot.get("dependencies", []):
            raise ProviderFailure("TOKENHUB_INPUT", "视频参考不在批准绑定内")
        artifact = provider.store.artifact(item["artifact_id"])
        if artifact["kind"] != "video" or artifact["review"] != "approved":
            raise ProviderFailure("TOKENHUB_INPUT", "视频参考尚未批准")
        path = (provider.store.media / item["file"]).resolve()
        if not path.is_relative_to(provider.store.media.resolve()) or not path.is_file():
            raise ProviderFailure("TOKENHUB_INPUT", "视频参考文件无效")
        if path.suffix.lower() != ".mp4" or path.stat().st_size > rules.get("reference_max_mb", 100) * 1024 * 1024:
            raise ProviderFailure("TOKENHUB_INPUT", "视频参考必须为 MP4，且大小不能超过规则上限")
        metadata = await provider.engine_request("/validate", {"file": item["file"]})
        if not 0 < metadata.get("duration_seconds", 0) <= rules.get("reference_max_seconds", 5):
            raise ProviderFailure("TOKENHUB_INPUT", "视频参考超过规则时长上限；未截断原参考或提交")

    task = job["runtime"].get("tokenhub_task")
    if task and task["call"] != job["runtime"]["active_call"]:
        task = None
    if task and task.get("decision") is not None and task["decision"] != decision:
        raise ProviderFailure("TOKENHUB_POLICY", "已提交任务的分流决策发生变化，禁止自动重新提交")
    if not task:
        if job["runtime"].get("cloud_submitting"):
            raise ProviderFailure("SUBMISSION_UNKNOWN", "TokenHub 上次提交结果不明，请先核对任务和账单")
        cloud = CosStorage(provider.store.root)
        try:
            image_urls, video_urls = [], []
            for path in refs:
                ref = await asyncio.to_thread(cloud.upload, path)
                image_urls.append(await asyncio.to_thread(cloud.signed_url, ref))
            for item in videos:
                ref = await asyncio.to_thread(cloud.upload, provider.store.media / item["file"])
                video_urls.append(await asyncio.to_thread(cloud.signed_url, ref))
        except CloudStorageError as exc:
            raise ProviderFailure("TOKENHUB_STORAGE", "TokenHub 需要可访问的首帧链接，请在云端存储配置并启用 COS") from exc
        # Only a temporary object capability is sent, never the COS signing keys.
        model = decision["model"]
        payload = {"model": model, "prompt": prompt, "duration": int(duration), "resolution": rules.get("resolution", "720p")}
        if model.startswith("vidu-video-q2"):
            payload["bgm"] = decision["bgm"]
        else:
            payload["audio"] = decision["audio"]
        if videos or len(refs) > 1:
            path = "/v1/wand/vidu/reference-to-video"
            payload["subjects"] = [{"id": f"reference_{i + 1}", "images": [url]} for i, url in enumerate(image_urls)]
            if video_urls:
                payload["subjects"].append({"id": "previous_video", "videos": video_urls})
        else:
            path = "/v1/wand/vidu/image-to-video"
            payload["images"] = image_urls
        try:
            validate_payload(payload)
        except ValueError as exc:
            raise ProviderFailure("TOKENHUB_POLICY", str(exc)) from exc
        provider.checkpoint(job, vidu_decision=decision, cloud_submitting=True)
        result = await request(provider, route, "POST", path, payload)
        task_id = next((result[k] for k in ("task_id", "id", "taskId") if result.get(k)), None)
        if not isinstance(task_id, (str, int)) or isinstance(task_id, bool):
            raise ProviderFailure("SUBMISSION_UNKNOWN", "TokenHub 未返回任务 ID，请先核对任务和账单")
        task = {"id": str(task_id), "call": job["runtime"]["active_call"], "decision": decision}
        provider.checkpoint(job, tokenhub_task=task, cloud_submitting=False)
    for _ in range(rules.get("poll_limit", 720)):
        provider.checkpoint(job, tokenhub_task=task)
        if rules.get("poll_protocol", "get_tasks") == "post_task_get":
            result = await request(provider, route, "POST", "/v1/wand/vidu/task/get", {"task_id": task["id"]})
        else:
            result = await request(provider, route, "GET", "/v1/wand/vidu/tasks/" + quote(task["id"], safe=""))
        status = str(result.get("status") or result.get("state") or result.get("task_status") or "").lower()
        if status in {"failed", "failure", "cancelled", "canceled", "error"}:
            raise ProviderFailure("TOKENHUB_TASK_FAILED", "TokenHub 视频任务失败，请在供应商控制台查看原因")
        if status in {"success", "succeeded", "completed", "complete", "done"}:
            creations = result.get("creations") or [result]
            url = None
            if isinstance(creations, list):
                for item in creations:
                    if isinstance(item, dict):
                        url = next((item[k] for k in ("url", "video_url", "output_url", "output_uri", "download_url") if isinstance(item.get(k), str) and item[k]), None)
                        if url:
                            break
            parsed = urlsplit(url or "")
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ProviderFailure("TOKENHUB_RESULT_INVALID", "TokenHub 未返回有效 HTTPS 视频地址")
            try:
                response = await provider.client.get(url, follow_redirects=False)
                response.raise_for_status()
                if response.is_redirect or not response.content:
                    raise ValueError("invalid media")
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderFailure("TOKENHUB_DOWNLOAD_FAILED", "视频下载失败；重试继续查询已有 TokenHub 任务") from exc
            actual_model = task.get("decision", decision)["model"]
            group = "q2_pro" if actual_model == "vidu-video-q2-pro" else "q2" if actual_model.startswith("vidu-video-q2") else "q3_pro" if actual_model.endswith("pro") else "q3_turbo"
            credits = result.get("credits")
            credits = credits if isinstance(credits, (int, float)) and not isinstance(credits, bool) and 0 <= credits < 1e12 else None
            pricing = snapshot.get("pipeline_rules", {}).get("billing", {})
            unit = pricing.get(group + "_per_credit")
            usage = {"model_group": group, "credits": credits, "configured_price_per_credit": unit,
                     "estimated_cost": credits * unit if credits is not None and unit is not None else None,
                     "currency": pricing.get("currency", "CNY"), "source": "provider_credits_and_user_price"}
            return {"content": response.content, "mime": "video/mp4", "metadata": {"provider_job_id": task["id"], "billing": usage},
                    "actual_route": {"transport": "cloud", "provider": "tokenhub-vidu",
                                     "model": task.get("decision", decision)["model"], "paid": True}}
        if status not in {"created", "queued", "pending", "running", "processing", "submitted", "waiting", "queueing"}:
            raise ProviderFailure("TOKENHUB_STATUS_UNKNOWN", "TokenHub 任务状态未知；保留任务 ID，未重新提交")
        await asyncio.sleep(rules.get("poll_seconds", 5))
    raise ProviderFailure("TOKENHUB_POLL_TIMEOUT", "TokenHub 任务尚未完成；重试继续查询原任务")
