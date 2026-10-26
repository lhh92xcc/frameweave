# FrameWeave implementation plan

## Outcome
Unified local image/video production studio, Chinese UI, six workflow templates, immutable approved Markdown production contract, chapters/episodes/shots/assets, persistent resumable jobs, web-first and explicit paid fallback routing, review and final renders. Source modules stay unmodified as pinned Git submodules. Repo: lhh92xcc/frameweave (public). Runtime uses native processes by default; optional container runtime is OrbStack.

## Boundaries
- `studio/`: application API, SQLite persistence, durable single-worker scheduler, routing, production contract and workflow rules.
- `services/engine_bridge.py`: adapter to the unmodified ai-video-generation chapter parser, media validation, TTS and FFmpeg assembly. `services/renderer.py` isolates canvas/subtitle compatibility.
- `upstream/web2api-web`: isolated web-account and cloud video/image gateway; private state outside submodule.
- `frontend/`: rebuilt Vue/TypeScript studio; shared API client; project, pipeline, assets, jobs, settings.
- `scripts/`: bootstrap, process lifecycle, upstream checks/updates and compatibility verification.

## Acceptance evidence to gather
- [x] Public remote recursively cloned into a clean directory; pinned revisions, bootstrap, production build, 27 backend tests and Ruff passed without local source/runtime files.
- [x] Contract editing/approval/version invalidation and exact prompt snapshot tests.
- [x] Actual upstream chapter parsing and actual FFmpeg render of local test media, CJK subtitles, three canvases and audio.
- [x] Durable task idempotency, restart recovery, pause/resume/cancel/retry, receipt recovery.
- [x] Web text/image/video connector contracts; cloud text/image and gateway official video routing. External account availability remains unverified.
- [x] Reference identity bindings and reviewed keyframe gate before video; all clips reviewed before render.
- [x] All six templates create real projects with tailored constraints.
- [x] Responsive UI browser verification; source import, contract, storyboard, settings and final render/export. Failure/recovery covered at API/provider level.
- [x] Native startup verified. OrbStack image build and healthy service startup verified.
- [x] Upstream sync script, actual new-commit/rollback tests, CI configuration and documentation.
- [x] Public GitHub repo synchronized under `lhh92xcc/frameweave`; tracked-file review excludes private credentials, runtime databases, user media and dependency environments.

No real external model output or quality claim will be inferred from mocked tests. User supplies model credentials in Settings.
