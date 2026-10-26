import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from studio.archive_import import import_archive, inventory, sha256


def test_archive_migration_preserves_bytes_qc_visibility_and_never_approves(app, client, tmp_path):
    source = tmp_path / "original"
    (source / "references").mkdir(parents=True)
    (source / "videos").mkdir()
    png = Path(__file__).parent / "fixtures/reference.png"
    shutil.copy2(png, source / "references/EP01-S02-old.png")
    shutil.copy2(png, source / "references/EP01-S02-copy.png")
    (source / "references/prop.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920"><rect width="1080" height="1920" fill="white"/></svg>'
    )
    (source / "videos/EP01-S02-invalid.mp4").write_bytes(b"damaged legacy file")
    (source / "references/.env").write_text("secret=never-copy")
    (source / "notes.md").write_text("历史候选，原制作审批 PENDING。")
    manifest = {
        "source_root": str(source),
        "project": {"title": "历史网剧", "source": "原有剧本全文原样保留，尚未批准制作。"},
        "groups": [
            {"directory": "references", "label": "制作版参考图"},
            {"directory": "videos", "label": "早期试片"},
        ],
        "documents": ["notes.md"],
        "file_metadata": {
            "references/EP01-S02-old.png": {
                "quality_status": "FAIL",
                "production_approval": "PENDING",
                "recorded_sha256": "0" * 64,
            }
        },
    }
    before = {p: sha256(p) for p in source.rglob("*") if p.is_file()}
    result = import_archive(manifest, app.state.store.root)
    assert result["total"] == 4 and result["counts"] == {"image": 3, "video": 1}
    assert result["validation_errors"] == ["videos/EP01-S02-invalid.mp4"]
    assert result["record_hash_mismatches"] == ["references/EP01-S02-old.png"]
    assert import_archive(manifest, app.state.store.root)["already_imported"]
    assert len(app.state.store.projects()) == 1
    assert all(sha256(p) == value for p, value in before.items())
    p = client.get("/api/projects/" + result["project_id"]).json()
    assert not p["contract"]["approved"] and not p["plan_approved"] and not p["automation"]
    assert len(p["artifacts"]) == 4
    artifacts = client.get("/api/artifacts").json()
    assert all(a["archived"] and not a["stale"] and a["review"] == "pending" for a in artifacts)
    old = next(a for a in artifacts if a["title"] == "EP01-S02-old.png")
    assert old["metadata"]["imported"]["quality_status"] == "FAIL"
    assert old["metadata"]["imported"]["record_hash_matches"] is False
    assert old["metadata"]["imported"]["shot_id"] == "EP01-S02"
    assert client.get(old["thumbnail_url"]).headers["content-type"] == "image/jpeg"
    assert client.get(old["url"]).content == png.read_bytes()
    svg = next(a for a in artifacts if a["title"] == "prop.svg")
    assert svg["metadata"]["format"] == "SVG" and svg["metadata"]["width"] == 1080
    assert "validation_error" not in svg["metadata"]
    assert "sandbox" in client.get(svg["url"]).headers["content-security-policy"]
    assert client.post(f"/api/artifacts/{old['id']}/review", json={"approved": True}).status_code == 422
    assert all(j["stage"] == "archive" and j["status"] == "succeeded" for j in p["jobs"])
    data = client.get(f"/api/projects/{p['id']}/export.zip")
    with zipfile.ZipFile(io.BytesIO(data.content)) as archive:
        names = archive.namelist()
        assert "import/documents/notes.md" in names
        assert not any(".env" in name or "settings" in name for name in names)
        assert len([n for n in names if n.startswith("media/")]) == 3  # equal-byte files stored once
        report = json.loads(archive.read("import/manifest.json"))
        assert len(report["files"]) == 4
    assert not list((app.state.store.root / "exports").glob("*.zip"))


def test_archive_inventory_rejects_outside_source_root(tmp_path):
    (tmp_path / "source").mkdir()
    with pytest.raises(ValueError, match="导入路径无效"):
        inventory({"source_root": str(tmp_path / "source"), "groups": [{"directory": "..", "label": "bad"}]})
