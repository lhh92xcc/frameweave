"""Apply an explicit, hash-checked selection to a private imported archive."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from studio.archive_import import sha256
from studio.store import digest, now

STATES = {"reference", "candidate", "repair", "test", "history"}


def library_primary(artifact):
    selection = artifact.get("metadata", {}).get("curation")
    # Pending current candidates are useful work in progress, not discarded history.
    # Visibility/export never changes their QC or production approval.
    return not selection or selection["state"] in {"reference", "candidate"}


def curate_archive(store, manifest):
    """Run with the service stopped; preserve files, approvals and a rollback snapshot."""
    project = store.project(manifest["project_id"])
    artifacts = store.artifacts(project["id"])
    if not project.get("import_summary") or any(not a.get("archived") for a in artifacts):
        raise ValueError("只允许整理尚未进入当前生产的导入档案")
    fingerprint = digest(manifest)
    if project.get("library_curation", {}).get("hash") == fingerprint:
        return project["library_curation"]
    by_path = {a["metadata"]["imported"]["source_path"]: a for a in artifacts}
    selections = {}
    for entry in manifest["selections"]:
        path = entry["source_path"]
        if path in selections or path not in by_path or entry["state"] not in STATES:
            raise ValueError("选择清单包含重复、缺失路径或无效用途")
        artifact = by_path[path]
        if sha256(store.media / artifact["file"]) != entry["sha256"]:
            raise ValueError(f"选择文件哈希不匹配：{path}")
        if entry["state"] == "reference" and (
            artifact["metadata"].get("validation_error")
            or artifact["metadata"]["imported"].get("record_hash_matches") is False
        ):
            raise ValueError(f"存在未解决的文件校验问题：{path}")
        selections[path] = entry
    source = manifest.get("current_script")
    if source and digest(source["content"]) != source["sha256"]:
        raise ValueError("当前剧本哈希不匹配")
    revision = project.get("library_curation", {}).get("revision", 0) + 1
    directory = store.root / "imports" / project["id"]
    directory.mkdir(parents=True, exist_ok=True)
    backup = store.root / "backups" / f"before-curation-{project['id']}-r{revision}.sqlite3"
    backup.parent.mkdir(exist_ok=True)
    with sqlite3.connect(backup) as target, store.db() as db:
        db.backup(target)
    (directory / f"before-curation-r{revision}.json").write_text(
        json.dumps({"project": project, "artifacts": artifacts}, ensure_ascii=False, indent=2)
    )
    counts = Counter()
    exclusions = manifest.get("exclusions", {})
    for artifact in artifacts:
        path = artifact["metadata"]["imported"]["source_path"]
        selection = selections.get(path) or {
            "state": "history",
            "label": "历史排除",
            "reason": exclusions.get(path, "未被当前制作线选用"),
        }
        artifact["metadata"]["curation"] = {
            **selection,
            "revision": revision,
            "curated_at": now(),
        }
        counts[selection["state"]] += 1
    summary = {
        "revision": revision,
        "hash": fingerprint,
        "counts": dict(counts),
        "note": manifest["note"],
        "source_chat": manifest.get("source_chat"),
        "curated_at": now(),
        "original_total": len(artifacts),
    }
    if source:
        # A different story version must not keep a previous version's storyboard.
        project.update(
            source=source["content"],
            chapters=source["chapters"],
            episodes_count=len(manifest["episodes"]),
            plan=None,
            plan_revision=project["plan_revision"] + 1,
            plan_approved=False,
        )
        project["contract"]["approved"] = False
        (directory / "current-season-script.md").write_text(source["content"])
    project.update(
        title=manifest.get("title", project["title"]),
        library_curation=summary,
        reference_catalog=manifest["episodes"],
        automation=False,
        updated_at=now(),
        version=project["version"] + 1,
    )
    (directory / "library-curation.json").write_text(
        json.dumps({"summary": summary, "selection": manifest}, ensure_ascii=False, indent=2)
    )
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM projects WHERE id=?", (project["id"],)).fetchone()
        if not row or json.loads(row[0])["version"] != project["version"] - 1:
            raise ValueError("项目在整理期间发生变化，请停止服务后重试")
        db.execute(
            "UPDATE projects SET data=? WHERE id=?", (json.dumps(project, ensure_ascii=False), project["id"])
        )
        db.executemany(
            "UPDATE artifacts SET data=? WHERE id=?",
            [(json.dumps(a, ensure_ascii=False), a["id"]) for a in artifacts],
        )
    return summary
