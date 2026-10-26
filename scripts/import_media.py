#!/usr/bin/env python3
"""Import explicitly listed local media groups without modifying their sources."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.archive_import import import_archive, inventory  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data", type=Path, default=ROOT / ".local")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.dry_run:
        files = inventory(manifest)
        print(
            json.dumps({"files": len(files), "bytes": sum(e["path"].stat().st_size for e in files)}, indent=2)
        )
    else:
        import dev

        if any(dev.healthy(s[3], s[0]) for s in dev.specs()[0]):
            raise SystemExit("请先停止 FrameWeave 原生服务或 OrbStack 容器，再导入本地数据库。")
        print(json.dumps(import_archive(manifest, args.data), ensure_ascii=False, indent=2))
