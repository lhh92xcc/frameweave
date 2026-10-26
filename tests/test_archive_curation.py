import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from studio.archive_curation import curate_archive, library_primary
from studio.archive_import import import_archive, sha256
from studio.store import digest


def test_curation_exports_current_candidates_without_promoting_approval(app, client, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    png = Path(__file__).parent / "fixtures/reference.png"
    for name in ["selected.png", "pending.png", "repair.png", "test.png", "withdrawn.png"]:
        shutil.copy2(png, source / name)
    result = import_archive(
        {
            "source_root": str(source),
            "project": {"title": "历史项目", "source": "旧剧本内容完整保留。"},
            "groups": [{"directory": ".", "label": "素材"}],
            "file_metadata": {"withdrawn.png": {"production_approval": "WITHDRAWN"}},
        },
        app.state.store.root,
    )
    store = app.state.store
    project_id = result["project_id"]
    manifest = {
        "project_id": project_id,
        "title": "当前参考库",
        "note": "按新剧本选择；旧稿不混用。",
        "current_script": {
            "content": "新的两集工作剧本。",
            "sha256": digest("新的两集工作剧本。"),
            "chapters": [{"chapter_number": 1, "title": "第一集", "content": "新的两集工作剧本。"}],
        },
        "episodes": [{"id": "EP01", "title": "第一集", "generated": 1, "missing_slots": ["S02"]}],
        "selections": [
            {"source_path": "selected.png", "sha256": "0" * 64, "state": "reference", "label": "限范围参考"},
            {"source_path": "pending.png", "sha256": sha256(png), "state": "candidate", "label": "待复核"},
            {"source_path": "repair.png", "sha256": sha256(png), "state": "repair", "label": "明确返修"},
            {"source_path": "test.png", "sha256": sha256(png), "state": "test", "label": "旧版试片"},
        ],
    }
    with pytest.raises(ValueError, match="哈希不匹配"):
        curate_archive(store, manifest)
    assert "library_curation" not in store.project(project_id)
    manifest["selections"][0]["sha256"] = sha256(png)
    summary = curate_archive(store, manifest)
    assert summary["counts"] == {"history": 1, "candidate": 1, "reference": 1, "repair": 1, "test": 1}
    assert curate_archive(store, manifest) == summary
    p = store.project(project_id)
    assert p["source"] == "新的两集工作剧本。" and p["plan"] is None
    assert not p["automation"] and not p["contract"]["approved"] and not p["plan_approved"]
    artifacts = store.artifacts(project_id)
    assert sum(library_primary(a) for a in artifacts) == 2
    assert all(a["review"] == "pending" and a["archived"] for a in artifacts)
    withdrawn = next(a for a in artifacts if a["title"] == "withdrawn.png")
    assert withdrawn["metadata"]["imported"]["production_approval"] == "WITHDRAWN"
    assert client.post(f"/api/artifacts/{withdrawn['id']}/review", json={"approved": True}).status_code == 422
    before = json.loads((store.root / "imports" / project_id / "before-curation-r1.json").read_text())
    assert before["project"]["source"] == "旧剧本内容完整保留。"
    assert len(list((store.root / "backups").glob("before-curation-*.sqlite3"))) == 1
    for full in [False, True]:
        response = client.get(f"/api/projects/{project_id}/export.zip?include_history={str(full).lower()}")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            selected = json.loads(archive.read("artifacts.json"))
            assert len(selected) == (5 if full else 2)
            if not full:
                assert {a["title"] for a in selected} == {"selected.png", "pending.png"}
                assert all(a["review"] == "pending" for a in selected)
            assert archive.testzip() is None
            assert ("import/before-curation-r1.json" in archive.namelist()) == full
            assert "import/library-curation.json" in archive.namelist()
    assert all(sha256(p) == sha256(png) for p in source.iterdir())
