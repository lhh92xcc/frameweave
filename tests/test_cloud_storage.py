import io
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from scripts.cos_media import migrate
from studio.archive_curation import curate_archive
from studio.archive_import import import_archive, sha256
from studio.cloud_storage import CloudStorageError, CosSettings, CosStorage


class MissingObject(Exception):
    def get_error_code(self):
        return "NoSuchResource"

    def get_status_code(self):
        return 404


class FakeCos:
    def __init__(self):
        self.objects = {}
        self.corrupt = False

    def head_object(self, *, Key, **kwargs):
        if Key not in self.objects:
            raise MissingObject()
        return {"Content-Length": str(len(self.objects[Key]))}

    def put_object(self, *, Key, Body, **kwargs):
        self.objects[Key] = Body.read()

    def get_object(self, *, Key, **kwargs):
        raw = self.objects[Key]
        stream = io.BytesIO(b"x" * len(raw) if self.corrupt else raw)

        class Body:
            def get_raw_stream(self):
                return stream

        return {"Body": Body()}

    def delete_object(self, *, Key, **kwargs):
        self.objects.pop(Key, None)

    def get_presigned_url(self, *, Bucket, Key, **kwargs):
        return f"https://{Bucket}.cos.ap-guangzhou.myqcloud.com/{Key}?test-signature=fake"

    def get_bucket_lifecycle(self, **kwargs):
        raise PermissionError("sensitive provider response must not escape")


def configured(root):
    fake = FakeCos()
    cloud = CosStorage(root, client_factory=lambda config: fake)
    cloud.save(
        CosSettings(
            enabled=True, bucket="test-media-1234567890", secret_id="private-id", secret_key="private-key"
        )
    )
    return cloud, fake


def test_cos_credentials_are_write_only_and_target_bound(client, app):
    cloud, _ = configured(app.state.store.root)
    response = client.get("/api/storage")
    assert response.json()["configured"]
    assert "private" not in response.text
    assert not any(k in response.json() for k in ["secret_id", "secret_key", "token"])
    assert stat.S_IMODE(cloud.path.stat().st_mode) == 0o600
    public = response.json()
    public.pop("usage")
    public.pop("configured")
    assert client.put("/api/storage", json=public).status_code == 200
    assert cloud.get().secret_key == "private-key"
    public["bucket"] = "different-target-1234567890"
    assert client.put("/api/storage", json=public).status_code == 422
    assert cloud.get().bucket == "test-media-1234567890"


def test_cos_does_not_accept_a_header_or_etag_as_file_verification(tmp_path):
    cloud, fake = configured(tmp_path)
    file = tmp_path / "original.png"
    file.write_bytes(b"original media")
    first = cloud.upload(file)
    assert first["verification"] == "full-download-sha256"
    fake.corrupt = True
    with pytest.raises(CloudStorageError, match="SHA-256"):
        cloud.upload(file)
    assert file.read_bytes() == b"original media"
    with pytest.raises(CloudStorageError, match="不同"):
        cloud.signed_url({**first, "bucket": "another-bucket-1234567890"})


def test_selected_cos_migration_keeps_local_copies_and_serves_without_them(
    app, client, tmp_path, monkeypatch
):
    store = app.state.store
    cloud, fake = configured(store.root)
    app.state.workflow.cloud = cloud
    monkeypatch.setattr("scripts.cos_media.CosStorage", lambda root: cloud)
    source = tmp_path / "source"
    source.mkdir()
    png = Path(__file__).parent / "fixtures/reference.png"
    states = ["reference", "candidate", "repair", "test", "history"]
    for state in states:
        shutil.copy2(png, source / f"{state}.png")
    imported = import_archive(
        {
            "source_root": str(source),
            "project": {"title": "COS迁移夹具", "source": "明确标注的离线测试图片。"},
            "groups": [{"directory": ".", "label": "测试"}],
        },
        store.root,
    )
    pid = imported["project_id"]
    curate_archive(
        store,
        {
            "project_id": pid,
            "note": "离线夹具",
            "episodes": [],
            "selections": [
                {"source_path": f"{state}.png", "sha256": sha256(png), "state": state, "label": state}
                for state in states[:-1]
            ],
        },
    )
    before = {p.name: sha256(p) for p in store.media.iterdir()}
    report = migrate(store, pid, set(states[:-1]), upload=True, workers=2)
    assert report["originals"] == 4 and report["local_originals_retained"] == 4
    assert report["local_deletion"] is False
    assert report["lifecycle"]["status"] == "unknown"
    assert "sensitive" not in json.dumps(report)
    assert {p.name: sha256(p) for p in store.media.iterdir()} == before
    rows = store.artifacts(pid)
    assert all(a["review"] == "pending" for a in rows)
    assert sum(bool(a.get("storage")) for a in rows) == 4
    reference = next(a for a in rows if a["metadata"]["curation"]["state"] == "reference")
    # Only test fixtures are removed to exercise a future cloud-only read, never real user files.
    for file in store.media.iterdir():
        file.unlink()
    response = client.get(f"/api/artifacts/{reference['id']}/content", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://test-media-1234567890.cos.ap-guangzhou.myqcloud.com/"
    )
    assert response.headers["cache-control"] == "no-store"
    thumbnail = client.get(f"/api/artifacts/{reference['id']}/thumbnail", follow_redirects=False)
    assert thumbnail.status_code == 307
    exported = client.get(f"/api/projects/{pid}/export.zip")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        exported_rows = json.loads(archive.read("artifacts.json"))
        assert {a["metadata"]["curation"]["state"] for a in exported_rows} == {"reference", "candidate"}
        assert archive.testzip() is None
    assert list(store.media.iterdir()) == []
    assert all(sha256(p) == sha256(png) for p in source.iterdir())
    assert cloud.probe()["download_verified"]
    assert not any(".checks/" in key for key in fake.objects)
