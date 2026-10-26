"""Private Tencent COS media, verified uploads and temporary local materialization."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import re
import tempfile
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import Field, field_validator

from studio.archive_import import sha256
from studio.models import StrictModel
from studio.store import now


class CloudStorageError(Exception):
    pass


class CosSettings(StrictModel):
    enabled: bool = False
    auto_upload: bool = False
    bucket: str = Field(default="", max_length=100)
    region: str = Field(default="ap-guangzhou", pattern=r"^[a-z][a-z0-9-]{2,63}$")
    prefix: str = Field(default="frameweave", min_length=1, max_length=160)
    secret_id: str | None = Field(default=None, max_length=8192, repr=False)
    secret_key: str | None = Field(default=None, max_length=8192, repr=False)
    token: str | None = Field(default=None, max_length=8192, repr=False)
    clear_keys: bool = False

    @field_validator("bucket")
    @classmethod
    def valid_bucket(cls, value):
        if value and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}-[0-9]{5,20}", value):
            raise ValueError("请输入含 APPID 的 COS 存储桶名")
        return value

    @field_validator("prefix")
    @classmethod
    def valid_prefix(cls, value):
        value = value.strip("/")
        if not value or any(p in {"", ".", ".."} for p in value.split("/")) or "\\" in value:
            raise ValueError("COS 前缀必须是有效的相对路径")
        if not re.fullmatch(r"[a-zA-Z0-9/_-]+", value):
            raise ValueError("COS 前缀只允许字母、数字、斜杠、横线和下划线")
        return value


def safe_error(exc):
    code = exc.get_error_code() if hasattr(exc, "get_error_code") else type(exc).__name__
    if str(code) in {"Unknown", "NoSuchResource"} and hasattr(exc, "get_status_code"):
        code = "NotFound" if str(exc.get_status_code()) == "404" else f"HTTP{exc.get_status_code()}"
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(code))[:80] or "RequestFailed"


class CosStorage:
    def __init__(self, root: Path, client_factory=None):
        self.root = Path(root).resolve()
        self.path = self.root / "cloud-storage.json"
        self.client_factory = client_factory

    def get(self):
        return CosSettings.model_validate_json(self.path.read_text()) if self.path.exists() else CosSettings()

    @staticmethod
    def public(config):
        result = config.model_dump(exclude={"secret_id", "secret_key", "token", "clear_keys"})
        result["configured"] = bool(config.bucket and config.secret_id and config.secret_key)
        return result

    def save(self, config: CosSettings):
        old = self.get()
        data = config.model_dump()
        same_target = (old.bucket, old.region) == (config.bucket, config.region)
        for name in ["secret_id", "secret_key", "token"]:
            if config.clear_keys:
                data[name] = None
            elif not data[name] and same_target:
                data[name] = getattr(old, name)
        data["clear_keys"] = False
        checked = CosSettings.model_validate(data)
        if checked.enabled and not self.public(checked)["configured"]:
            raise ValueError("启用 COS 需要存储桶、地域和完整凭据；更换桶或地域时请重新填写凭据")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=self.root, prefix=".cloud-storage-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(checked.model_dump(), f, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)
        return self.public(checked)

    def client(self):
        config = self.get()
        if not self.public(config)["configured"]:
            raise CloudStorageError("请先配置腾讯云 COS")
        if self.client_factory:
            return self.client_factory(config)
        from qcloud_cos import CosConfig, CosS3Client

        return CosS3Client(
            CosConfig(
                Region=config.region,
                SecretId=config.secret_id,
                SecretKey=config.secret_key,
                Token=config.token,
                Scheme="https",
                Timeout=60,
            )
        )

    def check_ref(self, ref):
        config = self.get()
        if ref.get("backend") != "cos" or (ref.get("bucket"), ref.get("region")) != (
            config.bucket,
            config.region,
        ):
            raise CloudStorageError("素材所属的 COS 桶与当前配置不同，请恢复原配置")
        key = ref.get("key", "")
        if not key.startswith(config.prefix + "/") or any(p in {"", ".", ".."} for p in key.split("/")):
            raise CloudStorageError("素材 COS 路径无效或不在配置前缀内")
        return config

    def verify(self, ref, destination=None, client=None):
        self.check_ref(ref)
        client = client or self.client()
        stream = None
        try:
            result = client.get_object(Bucket=ref["bucket"], Key=ref["key"])
            stream = result["Body"].get_raw_stream()
            checksum, size = hashlib.sha256(), 0
            while chunk := stream.read(1024 * 1024):
                checksum.update(chunk)
                size += len(chunk)
                if destination is not None:
                    destination.write(chunk)
            if size != ref["size"] or checksum.hexdigest() != ref["sha256"]:
                raise CloudStorageError("COS 下载内容与本地原文件的 SHA-256 或大小不符")
        except CloudStorageError:
            raise
        except Exception as exc:
            raise CloudStorageError(f"COS 下载校验失败（{safe_error(exc)}）") from None
        finally:
            if stream is not None:
                stream.close()
        return {**ref, "verified_at": now(), "verification": "full-download-sha256"}

    def upload(self, path: Path, mime=None):
        config, client = self.get(), self.client()
        if not config.enabled:
            raise CloudStorageError("COS 存储尚未启用")
        checksum = sha256(path)
        extension = path.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,10}", path.suffix.lower()) else ".bin"
        ref = {
            "backend": "cos",
            "bucket": config.bucket,
            "region": config.region,
            "key": f"{config.prefix}/objects/{checksum[:2]}/{checksum}{extension}",
            "sha256": checksum,
            "size": path.stat().st_size,
        }
        try:
            # Content-addressed keys are reusable across restarts and duplicate source paths.
            try:
                head = client.head_object(Bucket=config.bucket, Key=ref["key"])
                exists = int(head.get("Content-Length", -1)) == ref["size"]
            except Exception as exc:
                if safe_error(exc) not in {"NoSuchKey", "NoSuchObject", "NotFound", "404"}:
                    raise
                exists = False
            if not exists:
                with path.open("rb") as body:
                    client.put_object(
                        Bucket=config.bucket,
                        Key=ref["key"],
                        Body=body,
                        ContentType=mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        Metadata={"sha256": checksum},
                        EnableMD5=True,
                    )
            return self.verify(ref, client=client)
        except CloudStorageError:
            raise
        except Exception as exc:
            raise CloudStorageError(f"COS 上传失败（{safe_error(exc)}）") from None

    def signed_url(self, ref, download_name=None):
        config = self.check_ref(ref)
        try:
            params = {}
            if download_name:
                name = str(download_name).replace("\r", "").replace("\n", "")
                fallback = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name) or "media"
                params["response-content-disposition"] = (
                    f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
                )
            url = self.client().get_presigned_url(
                Bucket=config.bucket, Key=ref["key"], Method="GET", Expired=900, Params=params
            )
        except Exception as exc:
            raise CloudStorageError(f"COS 临时链接生成失败（{safe_error(exc)}）") from None
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != f"{config.bucket}.cos.{config.region}.myqcloud.com":
            raise CloudStorageError("COS 返回了非预期的下载地址")
        return url

    def backup_artifact(self, store, artifact):
        original = (store.media / artifact["file"]).resolve()
        if not original.is_relative_to(store.media) or not original.is_file():
            raise CloudStorageError("待上传的本地素材不存在")
        refs = {"storage": self.upload(original, artifact["mime"])}
        if artifact.get("thumbnail"):
            thumb = (store.root / artifact["thumbnail"]).resolve()
            if not thumb.is_relative_to(store.root / "thumbnails") or not thumb.is_file():
                raise CloudStorageError("待上传的缩略图不存在")
            refs["thumbnail_storage"] = self.upload(thumb, "image/jpeg")
        if artifact.get("subtitle_file"):
            subtitle = (store.media / artifact["subtitle_file"]).resolve()
            if not subtitle.is_relative_to(store.media) or not subtitle.is_file():
                raise CloudStorageError("待上传的字幕文件不存在")
            refs["subtitle_storage"] = self.upload(subtitle, "application/x-subrip")
        # Preserve a review made while the network upload was running.
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
            current = json.loads(row[0]) if row else None
            if not current or current["file"] != artifact["file"]:
                raise CloudStorageError("上传期间素材发生变化，云端副本已保留，请重新核对")
            current.update(refs)
            current.pop("storage_error", None)
            db.execute(
                "UPDATE artifacts SET data=? WHERE id=?",
                (json.dumps(current, ensure_ascii=False), current["id"]),
            )
        return current

    @contextmanager
    def materialize(self, store, artifact, field="file"):
        ref_field = "subtitle_storage" if field == "subtitle_file" else "storage"
        path = (store.media / artifact[field]).resolve()
        if not path.is_relative_to(store.media):
            raise CloudStorageError("素材本地路径无效")
        if path.is_file():
            yield path
            return
        if not artifact.get(ref_field):
            raise CloudStorageError("素材既无本地文件，也无已校验的 COS 副本")
        with tempfile.TemporaryDirectory(prefix=".cos-", dir=store.media) as directory:
            temporary = Path(directory) / path.name
            with temporary.open("wb") as output:
                self.verify(artifact[ref_field], destination=output)
            yield temporary

    @asynccontextmanager
    async def materialized(self, store, artifacts):
        with ExitStack() as stack:
            paths = []
            for artifact in artifacts:
                paths.append(await asyncio.to_thread(stack.enter_context, self.materialize(store, artifact)))
            yield paths

    def probe(self):
        import uuid

        config, client = self.get(), self.client()
        key = config.prefix + "/.checks/" + uuid.uuid4().hex
        content = b"FrameWeave COS connection check\n"
        try:
            client.put_object(
                Bucket=config.bucket, Key=key, Body=io.BytesIO(content), ContentType="text/plain"
            )
            result = client.get_object(Bucket=config.bucket, Key=key)
            stream = result["Body"].get_raw_stream()
            try:
                if stream.read() != content:
                    raise CloudStorageError("COS 连接检查的读回内容不一致")
            finally:
                stream.close()
        except CloudStorageError:
            raise
        except Exception as exc:
            raise CloudStorageError(f"COS 连接检查失败（{safe_error(exc)}）") from None
        finally:
            try:
                client.delete_object(Bucket=config.bucket, Key=key)
            except Exception:
                pass
        return {"status": "ok", "upload": True, "download_verified": True}

    def lifecycle(self):
        config = self.get()
        try:
            result = self.client().get_bucket_lifecycle(Bucket=config.bucket)
            return {"status": "readable", "rules": result.get("Rule", [])}
        except Exception as exc:
            if safe_error(exc) in {"NoSuchLifecycleConfiguration", "NoSuchLifecycle"}:
                return {"status": "no_rules", "rules": []}
            return {
                "status": "unknown",
                "code": safe_error(exc),
                "message": "无法读取生命周期规则；删除本地副本前需在 COS 控制台确认长期保留前缀不会自动过期",
            }
