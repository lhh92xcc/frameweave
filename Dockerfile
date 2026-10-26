FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates procps && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY upstream/ai-video-generation/pyproject.toml upstream/ai-video-generation/uv.lock upstream/ai-video-generation/
RUN uv sync --project upstream/ai-video-generation --locked --no-dev
COPY upstream/web2api-web/requirements.txt upstream/web2api-web/
RUN uv venv .local/gateway-venv && uv pip install --python .local/gateway-venv/bin/python -r upstream/web2api-web/requirements.txt
# Keep dependency environment outside the data volume.
RUN mv .local/gateway-venv /opt/frameweave-gateway-venv
COPY studio/ studio/
COPY services/ services/
COPY scripts/ scripts/
COPY upstream/ upstream/
COPY upstream-lock.json ./
COPY --from=frontend /build/dist frontend/dist
ENV FRAMEWEAVE_BIND=0.0.0.0 FRAMEWEAVE_DATA=/data FRAMEWEAVE_GATEWAY_PYTHON=/opt/frameweave-gateway-venv/bin/python PYTHONUNBUFFERED=1
EXPOSE 8787 8766
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=3)"
CMD ["python", "scripts/dev.py", "serve"]
