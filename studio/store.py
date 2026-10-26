from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:20]


def digest(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class Conflict(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.media = self.root / "media"
        self.media.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "studio.sqlite3"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS job_status ON jobs(status);
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS artifact_entity ON artifacts(project_id,json_extract(data, '$.kind'),json_extract(data, '$.entity_id'));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, created REAL NOT NULL, message TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def projects(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM projects ORDER BY rowid DESC")]

    def project(self, project_id):
        with self.db() as db:
            row = db.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError("项目不存在")
        return json.loads(row[0])

    def save_project(self, project, expected=None):
        project["updated_at"] = now()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if expected is not None:
                row = db.execute("SELECT data FROM projects WHERE id=?", (project["id"],)).fetchone()
                if row and json.loads(row[0]).get("version", 0) != expected:
                    raise Conflict("项目已更新，请刷新后重试")
            project["version"] = project.get("version", 0) + 1
            db.execute(
                "INSERT OR REPLACE INTO projects VALUES (?,?)",
                (project["id"], json.dumps(project, ensure_ascii=False)),
            )
        return project

    def jobs(self, project_id=None):
        with self.db() as db:
            if project_id:
                rows = db.execute(
                    "SELECT data FROM jobs WHERE project_id=? ORDER BY rowid DESC", (project_id,)
                )
            else:
                rows = db.execute("SELECT data FROM jobs ORDER BY rowid DESC")
            return [json.loads(r[0]) for r in rows]

    def job(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("任务不存在")
        return json.loads(row[0])

    def save_job(self, job):
        job["updated_at"] = now()
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?)",
                (
                    job["id"],
                    job["project_id"],
                    job["status"],
                    job["fingerprint"],
                    json.dumps(job, ensure_ascii=False),
                ),
            )
        return job

    def enqueue(self, project_id, stage, entity_id, snapshot, force=False):
        fingerprint = digest(
            {"project": project_id, "stage": stage, "entity": entity_id, "snapshot": snapshot}
        )
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT data FROM jobs WHERE fingerprint=? ORDER BY rowid DESC", (fingerprint,)
            ).fetchall()
            for row in rows:
                prior = json.loads(row[0])
                if prior["status"] in {"queued", "running"} or (not force and prior["status"] == "succeeded"):
                    return prior
            job = {
                "id": uid("job"),
                "project_id": project_id,
                "stage": stage,
                "entity_id": entity_id,
                "status": "queued",
                "fingerprint": fingerprint,
                "snapshot": snapshot,
                "runtime": {},
                "attempt": len(rows) + 1,
                "created_at": now(),
                "error": None,
            }
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (job["id"], project_id, "queued", fingerprint, json.dumps(job, ensure_ascii=False)),
            )
        return job

    def artifact(self, artifact_id):
        with self.db() as db:
            row = db.execute("SELECT data FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError("素材不存在")
        return json.loads(row[0])

    def artifacts(self, project_id=None):
        with self.db() as db:
            rows = db.execute(
                "SELECT data FROM artifacts"
                + (" WHERE project_id=?" if project_id else "")
                + " ORDER BY rowid DESC",
                (project_id,) if project_id else (),
            )
            return [json.loads(r[0]) for r in rows]

    def latest_artifact(self, project_id, kind, entity_id):
        with self.db() as db:
            row = db.execute(
                "SELECT data FROM artifacts WHERE project_id=? AND json_extract(data, '$.kind')=? AND json_extract(data, '$.entity_id')=? ORDER BY rowid DESC LIMIT 1",
                (project_id, kind, entity_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_artifact(self, artifact):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO artifacts VALUES (?,?,?)",
                (artifact["id"], artifact["project_id"], json.dumps(artifact, ensure_ascii=False)),
            )
        return artifact

    def event(self, job_id, message):
        with self.db() as db:
            db.execute("INSERT INTO events(job_id,created,message) VALUES (?,?,?)", (job_id, now(), message))

    def events(self, job_id):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT created,message FROM events WHERE job_id=? ORDER BY id", (job_id,)
                )
            ]
