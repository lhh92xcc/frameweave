#!/usr/bin/env python3
"""Check and update pinned upstreams without mixing studio customizations into them."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["ai-video-generation", "web2api-web"]
LOCK = ROOT / "upstream-lock.json"


def git(module, *args):
    return subprocess.check_output(["git", "-C", str(ROOT / "upstream" / module), *args], text=True).strip()


def lock():
    data = {
        m: {
            "url": git(m, "remote", "get-url", "origin").removesuffix(".git"),
            "commit": git(m, "rev-parse", "HEAD"),
            "branch": "main",
            "path": "upstream/" + m,
        }
        for m in MODULES
    }
    LOCK.write_text(json.dumps(data, indent=2) + "\n")
    return data


def check(modules):
    for m in modules:
        git(m, "fetch", "origin", "main")
        current = git(m, "rev-parse", "HEAD")
        target = git(m, "rev-parse", "origin/main")
        behind = git(m, "rev-list", "--count", "HEAD..origin/main")
        print(f"{m}: {current[:12]} → {target[:12]} ({behind} new commits)")


def update(modules):
    sys.path.insert(0, str(ROOT / "scripts"))
    import dev

    if dev.PIDFILE.exists():
        records = json.loads(dev.PIDFILE.read_text())
        if any(dev.is_ours(r["pid"], r["marker"]) for r in records):
            raise SystemExit("Stop FrameWeave before updating: python3 scripts/dev.py stop")
    if any(dev.healthy(s[3], s[0]) for s in dev.specs()[0]):
        raise SystemExit(
            "Stop running services before updating. Native: python3 scripts/dev.py stop; "
            "OrbStack: docker --context orbstack compose down"
        )
    previous = {m: git(m, "rev-parse", "HEAD") for m in modules}
    for m in modules:
        if git(m, "status", "--porcelain", "--untracked-files=no"):
            raise SystemExit(f"{m} has local changes. Preserve them before updating.")
    check(modules)
    try:
        for m in modules:
            git(m, "checkout", "--detach", "origin/main")
        subprocess.run(
            [str(ROOT / "scripts/bootstrap.sh")],
            cwd=ROOT,
            check=True,
            env={**os.environ, "FRAMEWEAVE_KEEP_UPSTREAM_PINS": "1"},
        )
        subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q"], cwd=ROOT, check=True)
        lock()
        print("Updated and verified. Review git diff, then commit the submodule pins and upstream-lock.json.")
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        for m, ref in previous.items():
            git(m, "checkout", "--detach", ref)
        lock()
        subprocess.run(
            [str(ROOT / "scripts/bootstrap.sh")],
            cwd=ROOT,
            check=False,
            env={**os.environ, "FRAMEWEAVE_KEEP_UPSTREAM_PINS": "1"},
        )
        raise SystemExit(
            "Compatibility verification failed. Previous source revisions restored; inspect output before retrying."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "update", "lock"])
    parser.add_argument("module", nargs="?", default="all", choices=["all", *MODULES])
    args = parser.parse_args()
    modules = MODULES if args.module == "all" else [args.module]
    if args.action == "lock":
        lock()
    elif args.action == "check":
        check(modules)
    else:
        update(modules)
