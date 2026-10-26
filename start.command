#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -f .venv/bin/python ] || [ ! -f frontend/dist/index.html ]; then
  ./scripts/bootstrap.sh
fi
python3 scripts/dev.py start
open http://127.0.0.1:8787
