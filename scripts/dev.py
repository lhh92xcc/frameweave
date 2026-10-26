#!/usr/bin/env python3
"""Native, isolated runtime. Credentials never enter process arguments."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FRAMEWEAVE_DATA", ROOT / ".local")).resolve()
PIDFILE = DATA / "processes.json"


def specs():
    bind = os.environ.get("FRAMEWEAVE_BIND", "127.0.0.1")
    ffmpeg_paths = [Path("/opt/homebrew/opt/ffmpeg-full/bin"), Path("/usr/local/opt/ffmpeg-full/bin")]
    runtime_path = os.pathsep.join(
        [str(p) for p in ffmpeg_paths if p.is_dir()] + [os.environ.get("PATH", "")]
    )
    env = dict(os.environ, FRAMEWEAVE_DATA=str(DATA), PYTHONUNBUFFERED="1", PATH=runtime_path)
    engine_env = dict(env, PYTHONPATH=str(ROOT / "upstream/ai-video-generation") + os.pathsep + str(ROOT))
    gateway_env = dict(
        env, WEB2API_STATE_DIR=str(DATA / "gateway/state"), WEB2API_IMAGES_DIR=str(DATA / "gateway/generated")
    )
    return [
        (
            "gateway",
            Path(os.environ.get("FRAMEWEAVE_GATEWAY_PYTHON", ROOT / ".local/gateway-venv/bin/python")),
            "server:app",
            8766,
            ROOT / "upstream/web2api-web",
            gateway_env,
        ),
        (
            "engine",
            ROOT / "upstream/ai-video-generation/.venv/bin/python",
            "services.engine_bridge:app",
            8767,
            ROOT,
            engine_env,
        ),
        ("studio", ROOT / ".venv/bin/python", "studio.api:app", 8787, ROOT, env),
    ], bind


def healthy(port, name):
    path = "/api/health" if name == "studio" else "/health"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def is_ours(pid, marker):
    try:
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
        return marker in command and ("uvicorn" in command or "scripts/dev.py" in command)
    except subprocess.CalledProcessError:
        return False


def stop():
    if not PIDFILE.exists():
        print("FrameWeave is not managed by this launcher.")
        return
    records = json.loads(PIDFILE.read_text())
    for r in reversed(records):
        if is_ours(r["pid"], r["marker"]):
            os.kill(r["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and any(is_ours(r["pid"], r["marker"]) for r in records):
        time.sleep(0.2)
    for r in records:
        if is_ours(r["pid"], r["marker"]):
            os.kill(r["pid"], signal.SIGKILL)
    PIDFILE.unlink(missing_ok=True)
    print("FrameWeave stopped. Data and credentials are preserved.")


def start(foreground=False):
    specifications, bind = specs()
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs = DATA / "logs"
    logs.mkdir(exist_ok=True)
    if PIDFILE.exists():
        records = json.loads(PIDFILE.read_text())
        if any(is_ours(r["pid"], r["marker"]) for r in records):
            print("FrameWeave already has managed processes. Use status or stop first.")
            return
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Frontend is not built. Run ./scripts/bootstrap.sh first.")
    if any(healthy(s[3], s[0]) for s in specifications):
        raise SystemExit("A service port is already in use. Inspect before restarting.")
    records, processes = [], []
    try:
        for name, python, app, port, cwd, env in specifications:
            if not python.exists():
                raise RuntimeError("Missing runtime. Run ./scripts/bootstrap.sh first.")
            with (logs / (name + ".log")).open("a") as log:
                proc = subprocess.Popen(
                    [
                        str(python),
                        "-m",
                        "uvicorn",
                        app,
                        "--host",
                        bind,
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=not foreground,
                )
            records.append({"name": name, "pid": proc.pid, "marker": app, "port": port})
            processes.append(proc)
        PIDFILE.write_text(json.dumps(records, indent=2))
        os.chmod(PIDFILE, 0o600)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if all(healthy(r["port"], r["name"]) for r in records):
                break
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A service exited. Inspect .local/logs/*.log")
            time.sleep(0.3)
        else:
            raise RuntimeError("Services did not become ready. Inspect .local/logs/*.log")
        print("FrameWeave: http://127.0.0.1:8787\nWeb2API: http://127.0.0.1:8766")
        if foreground:

            def shutdown(signum, frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, shutdown)
            while all(p.poll() is None for p in processes):
                time.sleep(1)
            raise RuntimeError("A child service stopped")
    except (Exception, KeyboardInterrupt) as exc:
        stop()
        if not isinstance(exc, KeyboardInterrupt):
            raise SystemExit(str(exc))


def status():
    for name, _, _, port, _, _ in specs()[0]:
        print(f"{name}: {'ready' if healthy(port, name) else 'offline'} (127.0.0.1:{port})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "serve", "stop", "status"])
    action = parser.parse_args().action
    {"start": start, "serve": lambda: start(True), "stop": stop, "status": status}[action]()
