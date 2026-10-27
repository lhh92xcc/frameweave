"""FrameWeave-specific gateway behavior without modifying the pinned upstream."""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar

import server
from engine import JobService, safe_error
from schemas import ImageRequest
from seedream import SEEDREAM_MODEL, SEEDREAM_OUTPUT_PRICE_CNY, SeedreamBackend
from storage import GatewayError

paid_auth_fallback = ContextVar("paid_auth_fallback", default=False)
log = logging.getLogger(__name__)


class FrameWeaveJobService(JobService):
    def _new_job(self, request, idempotency_key=None, owner_id=None, **extra):
        extra["frameweave_paid_auth_fallback"] = bool(
            paid_auth_fallback.get()
            and isinstance(request, ImageRequest)
            and request.provider == "gemini"
            and isinstance(idempotency_key, str)
            and idempotency_key.startswith("frameweave-")
        )
        return super()._new_job(request, idempotency_key, owner_id, **extra)

    async def generate_with_quota_fallback(self, job, request, folder, remaining, reference_paths, on_text=None):
        try:
            return await super().generate_with_quota_fallback(
                job, request, folder, remaining, reference_paths, on_text
            )
        except GatewayError as error:
            enabled = [account for account in self.accounts.items.values() if account.enabled]
            attempts = job.get("attempts") or []
            safe_attempts = all(
                isinstance(attempt, dict)
                and attempt.get("status") == "failed"
                and not attempt.get("result_uncertain")
                and isinstance(attempt.get("error"), dict)
                and attempt["error"].get("code") == "authentication_failed"
                for attempt in attempts
            )
            if not (
                job.get("frameweave_paid_auth_fallback") is True
                and error.code in {"accounts_unavailable", "authentication_failed"}
                and len(enabled) >= 2
                and all(isinstance(account.error, dict) and account.error.get("code") == "authentication_failed"
                        for account in enabled)
                and safe_attempts
                and not job.get("images")
                and not getattr(error, "partial_paths", None)
                and self.seedream.configured
            ):
                raise

        job["fallback_provider"] = "seedream"
        job["fallback_reason"] = "gemini_accounts_authentication_failed"
        job["billing"] = self._new_seedream_billing()
        attempt = {"provider": "seedream", "model": SEEDREAM_MODEL, "started_at": time.time(), "status": "running"}
        job.setdefault("attempts", []).append(attempt)
        self._persist()

        def record_usage(count):
            billing = job["billing"]
            billing["generated_images"] += count
            billing["estimated_amount_cny"] = round(billing["generated_images"] * SEEDREAM_OUTPUT_PRICE_CNY, 2)
            try:
                self._persist()
            except OSError:
                log.error("Could not persist Seedream usage for job %s.", job["id"])

        try:
            paths = await SeedreamBackend(self.seedream.api_key, self.settings.timeout).generate(
                request, folder, remaining, reference_paths, on_billed_images=record_usage
            )
        except Exception as exc:
            error = safe_error(exc)
            if getattr(exc, "partial_paths", None):
                error.partial_paths = exc.partial_paths
            attempt.update(status="failed", error=error.as_dict(), finished_at=time.time())
            self._persist()
            raise error from None
        attempt.update(status="succeeded", finished_at=time.time())
        self._persist()
        return paths


server.JobService = FrameWeaveJobService


def create_app(settings=None, backend_factory=None):
    app = server.create_app(settings, backend_factory)

    @app.middleware("http")
    async def frameweave_paid_policy(request, call_next):
        enabled = (
            request.method == "POST"
            and request.url.path == "/api/jobs"
            and request.headers.get("X-FrameWeave-Paid-Auth-Fallback") == "allow"
        )
        token = paid_auth_fallback.set(enabled)
        try:
            return await call_next(request)
        finally:
            paid_auth_fallback.reset(token)

    return app


app = create_app()
