"""Local, copy-only migration of a media archive. Never submits generation jobs."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps

from studio.models import ProductionPlan, ProjectCreate
from studio.store import Store, digest, now, uid

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.exists():
        raise ValueError(f"导入路径无效：{relative}")
    return path


def inventory(manifest):
    root = Path(manifest["source_root"]).resolve()
    result, seen = [], set()
    for group in manifest["groups"]:
        directory = inside(root, group["directory"])
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            result.append({"path": path, "relative": relative, "group": group["label"]})
    return result


def classify(path):
    text = path.as_posix().lower()
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return "audio", "配音 / 音频"
    if suffix in VIDEO_EXTENSIONS:
        return ("render", "历史试片") if "preview" in path.stem.lower() else ("video", "镜头视频 / 测试片")
    if "post" in path.parts:
        return "reference", "后期道具 / 字幕图层"
    if any(part in path.parts for part in ("qc", "chatgpt", "review-frames", "review-crops", "smoke-review")):
        return "reference", "审核证据 / 抽帧"
    if "keyframe" in text or "/frames/" in text:
        return "keyframe", "关键帧 / 候选图"
    return "reference", "角色 / 场景 / 道具参考"


def metadata(path, suffix, thumbnail):
    if suffix == ".svg":
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as exc:
            raise ValueError("无效 SVG") from exc
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("无效 SVG 根元素")
        dimensions = {}
        for name in ("width", "height"):
            value = re.fullmatch(r"(\d+(?:\.\d+)?)(?:px)?", root.get(name, ""))
            if value:
                dimensions[name] = float(value[1])
        return {"format": "SVG", **dimensions}
    if suffix in IMAGE_EXTENSIONS:
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            data = {"width": source.width, "height": source.height, "format": source.format}
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((480, 720))
            image.save(thumbnail, "JPEG", quality=80)
        return data
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    body = json.loads(probe.stdout)
    video = next((s for s in body["streams"] if s["codec_type"] == "video"), {})
    return {
        "duration_seconds": float(body.get("format", {}).get("duration", 0)),
        "width": video.get("width"),
        "height": video.get("height"),
        "audio_stream_count": sum(s["codec_type"] == "audio" for s in body["streams"]),
    }


def import_archive(manifest, data_root):
    """Call with FrameWeave stopped. Publish the project only after copy verification."""
    store = Store(Path(data_root))
    source_root = Path(manifest["source_root"]).resolve()
    files = inventory(manifest)
    for entry in files:
        entry["sha256"] = sha256(entry["path"])
    documents = []
    for relative in manifest.get("documents", []):
        source = inside(source_root, relative)
        if source.suffix.lower() not in {".md", ".txt", ".json", ".srt"} or source.name.startswith(".env"):
            raise ValueError("只允许导入显式指定的制作文档")
        documents.append({"path": relative, "sha256": sha256(source)})
    manifest_hash = digest(
        {
            "manifest": manifest,
            "files": [(e["relative"], e["sha256"]) for e in files],
            "documents": documents,
        }
    )
    for project in store.projects():
        if project.get("import_summary", {}).get("manifest_hash") == manifest_hash:
            return {"project_id": project["id"], "already_imported": True, **project["import_summary"]}
    body = ProjectCreate.model_validate(manifest["project"])
    plan = ProductionPlan.model_validate(manifest["plan"]).model_dump() if manifest.get("plan") else None
    chapters = manifest.get("chapters") or [
        {"chapter_number": 1, "title": "历史制作资料", "content": body.source}
    ]
    if plan:
        if len(plan["episodes"]) != body.episodes:
            raise ValueError("导入分集数量不匹配")
        if any(sum(s["duration"] for s in ep["shots"]) != body.duration for ep in plan["episodes"]):
            raise ValueError("导入分镜时长与制作要求不匹配")
        known = {c["chapter_number"] for c in chapters}
        if {n for ep in plan["episodes"] for n in ep["source_chapters"]} != known:
            raise ValueError("导入分镜未覆盖全部章节")
    if not files:
        raise ValueError("没有找到可导入媒体")
    project_id, job_id = uid("project"), uid("job")
    archive_dir = store.root / "imports" / project_id
    archive_dir.mkdir(parents=True, mode=0o700)
    thumbs = store.root / "thumbnails"
    thumbs.mkdir(exist_ok=True, mode=0o700)
    backups = store.root / "backups"
    backups.mkdir(exist_ok=True, mode=0o700)
    with sqlite3.connect(backups / f"before-import-{project_id}.sqlite3") as backup, store.db() as db:
        db.backup(backup)
    contract_md = manifest.get("contract_markdown") or body.source
    contract = {"revision": 0, "markdown": contract_md, "hash": digest(contract_md), "approved": False}
    project = body.model_dump()
    project["episodes_count"] = project.pop("episodes")
    project.update(
        id=project_id,
        created_at=now(),
        updated_at=now(),
        version=1,
        chapters=chapters,
        contract=contract,
        contract_history=[],
        plan=plan,
        plan_revision=1 if plan else 0,
        plan_contract_hash=contract["hash"],
        plan_approved=False,
        automation=False,
        paused=False,
        cancelled=False,
    )
    rows, checks, errors, record_mismatches = [], [], [], []
    counts, groups = Counter(), Counter()
    original_metadata = manifest.get("file_metadata", {})
    for index, entry in enumerate(files, 1):
        source, relative = entry["path"], entry["relative"]
        sha = entry["sha256"]
        suffix = source.suffix.lower()
        target = store.media / ("legacy_" + sha + suffix)
        if not target.exists():
            shutil.copy2(source, target)
        if sha256(target) != sha:
            raise ValueError(f"文件复制校验失败：{relative}")
        artifact_id = uid("art")
        thumbnail = thumbs / (sha + ".jpg")
        kind, role = classify(source)
        try:
            meta = metadata(source, suffix, thumbnail)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Keep damaged historical files, recording failure instead of losing them.
            meta = {"validation_error": type(exc).__name__}
            errors.append(relative)
        imported = {
            **original_metadata.get(relative, {}),
            "source_path": relative,
            "original_filename": source.name,
            "sha256": sha,
            "group": entry["group"],
            "role": role,
            "size_bytes": source.stat().st_size,
            "source_modified_at": source.stat().st_mtime,
        }
        if imported.get("recorded_sha256"):
            imported["record_hash_matches"] = imported["recorded_sha256"].lower() == sha
            if not imported["record_hash_matches"]:
                imported["record_warning"] = "原审核记录的文件哈希与现文件不同，原结论不能用于当前文件。"
                record_mismatches.append(relative)
        shot = re.search(r"(?:EP?|E)(\d{1,2})[-_]([SL]\d{1,2}[A-Z]?)", source.name, re.I)
        if shot:
            imported["shot_id"] = f"EP{int(shot[1]):02d}-{shot[2].upper()}"
        artifact = {
            "id": artifact_id,
            "project_id": project_id,
            "job_id": job_id,
            "entity_id": "legacy_" + digest(relative)[:24],
            "title": source.name,
            "kind": kind,
            "file": target.name,
            "mime": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            "contract_hash": contract["hash"],
            "contract_revision": 0,
            "plan_revision": project["plan_revision"],
            "dependencies": [],
            "review": "pending",
            "archived": True,
            "created_at": source.stat().st_mtime,
            "route": {"provider": "legacy-import", "model": "原始素材", "paid": False},
            "metadata": {**meta, "imported": imported},
        }
        if suffix in IMAGE_EXTENSIONS and thumbnail.is_file():
            artifact["thumbnail"] = thumbnail.relative_to(store.root).as_posix()
        rows.append(artifact)
        checks.append(
            {"source_path": relative, "artifact_id": artifact_id, "file": target.name, "sha256": sha}
        )
        counts[
            "image" if suffix in IMAGE_EXTENSIONS else "video" if suffix in VIDEO_EXTENSIONS else "audio"
        ] += 1
        groups[entry["group"]] += 1
        if index % 100 == 0 or index == len(files):
            print(f"已复制并校验 {index}/{len(files)}", flush=True)
    for document in documents:
        relative = document["path"]
        source = inside(source_root, relative)
        target = archive_dir / "documents" / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(target) != document["sha256"]:
            raise ValueError(f"制作文档复制校验失败：{relative}")
    summary = {
        "manifest_hash": manifest_hash,
        "counts": dict(counts),
        "groups": dict(groups),
        "total": len(files),
        "bytes": sum(e["path"].stat().st_size for e in files),
        "documents": len(documents),
        "validation_errors": errors,
        "record_hash_mismatches": record_mismatches,
        "note": manifest.get("note", "历史素材保持原记录，制作用途待审核。"),
        "imported_at": now(),
    }
    project["import_summary"] = summary
    job = {
        "id": job_id,
        "project_id": project_id,
        "stage": "archive",
        "entity_id": "",
        "status": "succeeded",
        "fingerprint": manifest_hash,
        "attempt": 1,
        "created_at": now(),
        "finished_at": now(),
        "error": None,
        "runtime": {"imported": True},
        "snapshot": {"contract": contract, "source_root": str(source_root), "summary": summary},
        "effective_prompt": "历史文件迁移记录；未提交任何模型生成请求。",
    }
    report = {
        "project_id": project_id,
        "source_root": str(source_root),
        "summary": summary,
        "files": checks,
        "documents": documents,
    }
    (archive_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO projects VALUES (?,?)", (project_id, json.dumps(project, ensure_ascii=False)))
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?)",
            (job_id, project_id, "succeeded", manifest_hash, json.dumps(job, ensure_ascii=False)),
        )
        db.executemany(
            "INSERT INTO artifacts VALUES (?,?,?)",
            [(a["id"], project_id, json.dumps(a, ensure_ascii=False)) for a in rows],
        )
    return {"project_id": project_id, **summary}
