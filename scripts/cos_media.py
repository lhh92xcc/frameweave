#!/usr/bin/env python3
"""Upload an explicit current-library selection to COS; never delete local files."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.archive_import import sha256  # noqa: E402
from studio.cloud_storage import CosStorage  # noqa: E402
from studio.store import Store, now  # noqa: E402


def migrate(store, project_id, states, *, upload=False, workers=4):
    cloud = CosStorage(store.root)
    project = store.project(project_id)
    selected = [
        a
        for a in store.artifacts(project_id)
        if a.get("metadata", {}).get("curation", {}).get("state") in states
    ]
    if not selected:
        raise ValueError("当前选择没有素材")
    config = cloud.get()
    manifest = {
        "project_id": project_id,
        "bucket": config.bucket,
        "region": config.region,
        "prefix": config.prefix,
        "states": sorted(states),
        "local_deletion": False,
        "created_at": now(),
        "originals": [],
    }
    for artifact in selected:
        path = (store.media / artifact["file"]).resolve()
        if not path.is_relative_to(store.media) or not path.is_file():
            raise ValueError(f"本地文件缺失：{artifact['id']}")
        checksum = sha256(path)
        imported = artifact.get("metadata", {}).get("imported", {})
        if imported.get("sha256") and imported["sha256"] != checksum:
            raise ValueError(f"导入后的文件哈希变化：{artifact['id']}")
        manifest["originals"].append(
            {
                "id": artifact["id"],
                "file": artifact["file"],
                "sha256": checksum,
                "size": path.stat().st_size,
                "state": artifact["metadata"]["curation"]["state"],
            }
        )
    directory = store.root / "migrations" / project_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (directory / "cos-plan.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not upload:
        return {
            "originals": len(selected),
            "bytes": sum(r["size"] for r in manifest["originals"]),
            "counts": dict(Counter(r["state"] for r in manifest["originals"])),
            "plan": str(directory / "cos-plan.json"),
            "local_deletion": False,
        }
    completed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(cloud.backup_artifact, store, a): a["id"] for a in selected}
        for future in as_completed(futures):
            artifact = future.result()
            completed.append(artifact)
            print(
                json.dumps(
                    {"verified": len(completed), "total": len(selected), "artifact_id": artifact["id"]}
                ),
                flush=True,
            )
    # A recovery index contains the selected media and production metadata, never settings or credentials.
    snapshot = {
        "record_type": "frameweave_cos_library",
        "project": project,
        "artifacts": sorted(completed, key=lambda a: a["id"]),
        "manifest": manifest,
    }
    snapshot_path = directory / "cos-library-index.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    index = cloud.upload(snapshot_path, "application/json")
    report = {
        "project_id": project_id,
        "bucket": config.bucket,
        "region": config.region,
        "prefix": config.prefix,
        "originals": len(completed),
        "original_bytes": sum(a["storage"]["size"] for a in completed),
        "thumbnails": sum(bool(a.get("thumbnail_storage")) for a in completed),
        "counts": dict(Counter(a["metadata"]["curation"]["state"] for a in completed)),
        "verification": "full-download-sha256",
        "local_deletion": False,
        "local_originals_retained": sum((store.media / a["file"]).is_file() for a in completed),
        "production_approval_changed": False,
        "recovery_index": index,
        "completed_at": now(),
        "lifecycle": cloud.lifecycle(),
    }
    (directory / "cos-upload-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "upload"])
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / ".local")
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--states",
        nargs="+",
        choices=["reference", "candidate", "repair", "test"],
        default=["reference", "candidate", "repair", "test"],
    )
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    result = migrate(
        Store(args.data_dir),
        args.project,
        set(args.states),
        upload=args.action == "upload",
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
