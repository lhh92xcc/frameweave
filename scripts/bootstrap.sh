#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/opt/ffmpeg-full/bin:$HOME/.local/bin:$PATH"
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v node >/dev/null || { echo 'Node.js 22+ is required.'; exit 1; }
command -v ffmpeg >/dev/null || { echo 'Install FFmpeg (macOS: brew install ffmpeg-full).'; exit 1; }
if [[ "${FRAMEWEAVE_KEEP_UPSTREAM_PINS:-0}" != "1" ]]; then
  git submodule update --init --recursive
fi
uv sync --locked --dev
uv sync --project upstream/ai-video-generation --locked --dev
uv venv --allow-existing .local/gateway-venv
uv pip install --python .local/gateway-venv/bin/python -r upstream/web2api-web/requirements.txt
(cd frontend && npm ci && npm run build)
python3 scripts/upstream.py lock
printf 'Ready. Run: python3 scripts/dev.py start\n'
