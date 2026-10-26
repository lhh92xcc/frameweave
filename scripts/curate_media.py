#!/usr/bin/env python3
"""Apply an explicit selection manifest to a local imported media project."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.archive_curation import curate_archive  # noqa: E402
from studio.store import Store  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data", type=Path, default=ROOT / ".local")
    args = parser.parse_args()
    import dev

    if any(dev.healthy(s[3], s[0]) for s in dev.specs()[0]):
        raise SystemExit("请先停止 FrameWeave 原生服务或 OrbStack 容器，再整理本地数据库。")
    result = curate_archive(Store(args.data), json.loads(args.manifest.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
