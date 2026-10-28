"""Immutable, local records of activated MiniMax voices. Never creates remote voices."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from studio.models import VoiceProfile
from studio.providers import ProviderFailure
from studio.store import Conflict, now

# Published starter system IDs. Additional system IDs are cached only from get_voice's system_voice category.
SYSTEM_VOICE_IDS = {
    "cute_boy", "clever_boy", "lovely_girl", "Chinese (Mandarin)_Cute_Spirit",
    "male-qn-qingse", "male-qn-daxuesheng", "female-chengshu",
    "Chinese (Mandarin)_Gentleman", "Chinese (Mandarin)_Humorous_Elder",
}


class VoiceLibrary:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "voice-library"
        self.root.mkdir(exist_ok=True, mode=0o700)

    def entries(self):
        with self.store.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM voice_library ORDER BY rowid")]

    def entry(self, id_):
        with self.store.db() as db:
            row = db.execute("SELECT data FROM voice_library WHERE id=?", (id_,)).fetchone()
        if row is None:
            raise KeyError("已锁定音色不存在，请恢复资源库备份；不会自动重新设计音色")
        return json.loads(row[0])

    @staticmethod
    def public(entry):
        result = {k: entry[k] for k in (
            "id", "name", "provider", "voice_id", "profile", "locked", "activated",
            "model", "created_at", "source_project_id", "source_role_id", "review",
        )}
        result["preview_url"] = f"/api/audio/voice-library/{entry['id']}/preview"
        return result

    def validate_profile(self, profile):
        if profile.get("library_id"):
            entry = self.entry(profile["library_id"])
            if profile["voice_id"] != entry["voice_id"] or not entry["locked"] or not entry["activated"]:
                raise Conflict("资源库音色已锁定，不能替换声线 ID；请选择原有已激活音色")

    def cache_system_voices(self, voices):
        ids = sorted({v["id"] for v in voices if v.get("kind") == "system_voice"})
        path = self.root / "system-voices.json"
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(ids, output, ensure_ascii=False)
        os.replace(temporary, path)

    def allowed_voice_ids(self):
        ids = set(SYSTEM_VOICE_IDS)
        cache = self.root / "system-voices.json"
        if cache.exists():
            try:
                cached = json.loads(cache.read_text())
                if isinstance(cached, list):
                    ids.update(v for v in cached if isinstance(v, str))
            except (ValueError, OSError):
                pass
        ids.update(e["voice_id"] for e in self.entries() if e["activated"] and e["locked"])
        return ids

    def require_reusable_voice(self, profile):
        self.validate_profile(profile)
        if profile.get("source") == "recording":
            raise ProviderFailure("RECORDED_AUDIO_ONLY", "此角色使用已有录音或猫叫，请复用或导入音轨，不提交 TTS")
        if profile["voice_id"] not in self.allowed_voice_ids():
            raise ProviderFailure(
                "VOICE_POLICY_BLOCKED", "已禁止新增定制音色；请选择免音色费的系统音色或资源库已激活音色。未知 ID 不会提交合成",
            )

    def preview(self, id_):
        entry = self.entry(id_)
        path = (self.root / entry["id"] / "preview.mp3").resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise KeyError("音色试听文件缺失，请恢复备份")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["preview_sha256"]:
            raise Conflict("音色试听校验失败，请恢复备份")
        return path

    def import_activated(self, *, name, profile, preview, receipt, provenance,
                         source_project_id, source_role_id, model="speech-2.8-turbo"):
        """Local import only; receipt must include the original first-use voice charge."""
        profile = VoiceProfile.model_validate(profile).model_dump(exclude_none=True)
        profile.pop("library_id", None)
        if receipt.get("usage_voice_count") != 1 or not receipt.get("trace_id"):
            raise ValueError("必须提供首次使用音色的计费回执，不能把未激活音色标为已付费")
        if not name.strip() or len(name) > 160:
            raise ValueError("请填写有效音色名称")
        voice_id = profile["voice_id"]
        id_ = "voice_" + hashlib.sha256(voice_id.encode()).hexdigest()[:20]
        audio = Path(preview).read_bytes()
        if not audio:
            raise ValueError("试听文件不能为空")
        entry = {
            "id": id_, "name": name, "provider": "minimax", "voice_id": voice_id,
            "profile": profile, "locked": True, "activated": True, "model": model,
            "created_at": now(), "source_project_id": source_project_id,
            "source_role_id": source_role_id, "review": "pending",
            "preview_sha256": hashlib.sha256(audio).hexdigest(),
            "activation_receipt": receipt, "provenance": provenance,
        }
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT data FROM voice_library WHERE voice_id=?", (voice_id,)).fetchone()
            if prior:
                prior = json.loads(prior[0])
                if any(prior[k] != entry[k] for k in (
                    "name", "profile", "preview_sha256", "activation_receipt",
                    "source_project_id", "source_role_id", "model", "provenance",
                )):
                    raise Conflict("已锁定音色不能被覆盖或重建")
                self.preview(prior["id"])
                return prior
            folder = self.root / id_
            folder.mkdir(exist_ok=True, mode=0o700)
            for filename, content in (
                ("preview.mp3", audio),
                ("record.json", json.dumps(entry, ensure_ascii=False, indent=2).encode()),
            ):
                temporary = folder / (filename + ".tmp")
                fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, folder / filename)
            db.execute("INSERT INTO voice_library VALUES (?,?,?)", (id_, voice_id, json.dumps(entry, ensure_ascii=False)))
        return entry

    def export(self, path):
        entries = self.entries()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("voice-library.json", json.dumps({"schema": 1, "voices": entries}, ensure_ascii=False, indent=2))
            for entry in entries:
                archive.write(self.preview(entry["id"]), entry["id"] + "/preview.mp3")
