"""Exercise actual Git upgrades/rollback without installing nested test runtimes."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import upstream


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.mark.parametrize("compatible", [True, False])
def test_upgrade_keeps_new_pin_during_install_and_restores_on_failure(tmp_path, monkeypatch, compatible):
    origin, root = tmp_path / "origin", tmp_path / "studio"
    origin.mkdir()
    root.mkdir()
    for path in [origin, root]:
        git(path, "init", "-b", "main")
        git(path, "config", "user.name", "FrameWeave test")
        git(path, "config", "user.email", "test@example.invalid")
    (origin / "version").write_text("old")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "old")
    old = git(origin, "rev-parse", "HEAD")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(origin), "upstream/engine")
    git(root, "commit", "-am", "pin old engine")
    (origin / "version").write_text("new")
    git(origin, "commit", "-am", "new")
    new = git(origin, "rev-parse", "HEAD")
    scripts = root / "scripts"
    scripts.mkdir()
    bootstrap = scripts / "bootstrap.sh"
    bootstrap.write_text(
        "#!/bin/sh\nset -eu\n"
        'if [ "${FRAMEWEAVE_KEEP_UPSTREAM_PINS:-0}" != 1 ]; then git submodule update; fi\n'
        "git -C upstream/engine rev-parse HEAD >> installed-pins\n"
    )
    bootstrap.chmod(0o755)
    python = root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit " + ("0\n" if compatible else "1\n"))
    python.chmod(0o755)
    monkeypatch.setattr(upstream, "ROOT", root)
    monkeypatch.setattr(upstream, "LOCK", root / "upstream-lock.json")
    monkeypatch.setattr(upstream, "MODULES", ["engine"])
    monkeypatch.setitem(
        sys.modules,
        "dev",
        SimpleNamespace(
            PIDFILE=tmp_path / "absent-processes.json",
            specs=lambda: ([], "127.0.0.1"),
        ),
    )
    if compatible:
        upstream.update(["engine"])
        assert git(root / "upstream/engine", "rev-parse", "HEAD") == new
        assert (root / "installed-pins").read_text().splitlines() == [new]
    else:
        with pytest.raises(SystemExit, match="Previous source revisions restored"):
            upstream.update(["engine"])
        assert git(root / "upstream/engine", "rev-parse", "HEAD") == old
        assert (root / "installed-pins").read_text().splitlines() == [new, old]
    lock = json.loads((root / "upstream-lock.json").read_text())
    assert lock["engine"]["commit"] == (new if compatible else old)
