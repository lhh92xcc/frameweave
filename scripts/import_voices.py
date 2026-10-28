#!/usr/bin/env python3
"""Preserve existing activated voices and audio locally; never contacts a provider."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.store import Store  # noqa: E402
from studio.voice_library import VoiceLibrary  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Private JSON manifest containing a voices list")
    parser.add_argument("--data", type=Path, default=ROOT / ".local")
    args = parser.parse_args()
    library = VoiceLibrary(Store(args.data))
    manifest = json.loads(args.manifest.read_text())
    ids = []
    for item in manifest["voices"]:
        item = dict(item)
        item["preview"] = args.manifest.parent / item["preview"]
        if isinstance(item["provenance"], str):
            item["provenance"] = json.loads((args.manifest.parent / item["provenance"]).read_text())
        ids.append(library.import_activated(**item)["id"])
    backup = library.root / "locked-voices-backup.zip"
    library.export(backup)
    backup.chmod(0o600)
    print(json.dumps({"saved": ids, "backup": str(backup), "paid_requests": 0}, ensure_ascii=False))
