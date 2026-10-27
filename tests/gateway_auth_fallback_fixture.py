"""Offline gateway integration fixture, run in the isolated gateway interpreter."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "upstream/web2api-web")]

from engine import Settings  # noqa: E402
from schemas import CredentialsRequest  # noqa: E402
from storage import GatewayError  # noqa: E402

import services.gateway_adapter as adapter  # noqa: E402


class OfflineGemini:
    failures = {}

    def __init__(self, credentials, settings):
        self.token = credentials.secure_1psid
        self.ready = False

    async def connect(self):
        self.ready = True
        return []

    async def generate(self, request, folder, remaining, reference_paths=None, on_text=None):
        code = self.failures.get(self.token)
        if code:
            raise GatewayError(code, "offline failure", 503)
        path = folder / "gemini-offline.png"
        path.write_bytes(PNG)
        return [path]

    async def close(self):
        pass


class OfflineSeedream:
    calls = []
    fail = False

    def __init__(self, api_key, timeout):
        assert api_key == "offline-key"

    async def generate(self, request, folder, remaining, reference_paths=None, on_billed_images=None):
        self.calls.append((request.prompt, [path.read_bytes() for path in reference_paths or []]))
        if self.fail:
            raise GatewayError("seedream_network_error", "offline error", 502)
        on_billed_images(1)
        path = folder / "seedream-offline.png"
        path.write_bytes(PNG)
        return [path]


async def request(app, method, path, data=None, headers=()):
    body = json.dumps(data).encode() if data is not None else b""
    sent = False
    events = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Future()

    async def send(event):
        events.append(event)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    status = next(event["status"] for event in events if event["type"] == "http.response.start")
    raw = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return status, json.loads(raw)


async def main(root):
    adapter.SeedreamBackend = OfflineSeedream
    app = adapter.create_app(Settings(root / "state", root / "images", retry_cooldown=0.01), OfflineGemini)
    async with app.router.lifespan_context(app):
        service = app.state.service
        for name in ("first", "second"):
            await service.accounts.save(name, name, CredentialsRequest(secure_1psid=name))
        service.seedream.save("offline-key")
        reference = service.references.save([("offline.png", PNG)], None)[0]["id"]
        body = {"prompt": "approved offline markdown", "reference_images": [reference]}
        accounts = list(service.accounts.items.values())

        async def submit(key, paid=True):
            headers = [(b"idempotency-key", key.encode())]
            if paid:
                headers.append((b"x-frameweave-paid-auth-fallback", b"allow"))
            status, job = await request(app, "POST", "/api/jobs", body, headers)
            assert status == 202
            for _ in range(500):
                job = service.jobs[job["id"]]
                if job["status"] in {"succeeded", "failed", "cancelled"}:
                    return job
                await asyncio.sleep(0.01)
            raise AssertionError("offline job did not finish")

        for account in accounts:
            account.error = {"code": "authentication_failed"}
        denied = await submit("frameweave-no-paid", False)
        assert denied["error"]["code"] == "accounts_unavailable" and not OfflineSeedream.calls
        denied_native = await submit("native-job", True)
        assert denied_native["error"]["code"] == "accounts_unavailable" and not OfflineSeedream.calls
        accounts[1].enabled = False
        one_account = await submit("frameweave-one")
        assert one_account["error"]["code"] == "accounts_unavailable" and not OfflineSeedream.calls
        accounts[1].enabled = True

        success = await submit("frameweave-authorized")
        assert success["status"] == "succeeded" and success["fallback_provider"] == "seedream"
        assert success["frameweave_paid_auth_fallback"] is True
        assert success["fallback_reason"] == "gemini_accounts_authentication_failed"
        assert success["billing"]["generated_images"] == 1
        assert OfflineSeedream.calls == [(body["prompt"], [PNG])]
        assert (await submit("frameweave-authorized"))["id"] == success["id"]
        assert len(OfflineSeedream.calls) == 1

        for account in accounts:
            account.error = None
        OfflineGemini.failures = {"first": "authentication_failed", "second": "authentication_failed"}
        fresh = await submit("frameweave-fresh-failures")
        assert fresh["status"] == "succeeded" and fresh["fallback_provider"] == "seedream"
        assert [item.get("error", {}).get("code") for item in fresh["attempts"][:2]] == [
            "authentication_failed", "authentication_failed"
        ]
        assert len(OfflineSeedream.calls) == 2

        for account in accounts:
            account.error = None
        OfflineGemini.failures = {"first": "authentication_failed"}
        healthy = await submit("frameweave-one-healthy")
        assert healthy["status"] == "succeeded" and not healthy.get("fallback_provider")
        assert len(OfflineSeedream.calls) == 2

        for account in accounts:
            account.error = None
        OfflineGemini.failures = {"first": "upstream_timeout", "second": "authentication_failed"}
        uncertain = await submit("frameweave-timeout")
        assert uncertain["status"] == "failed" and len(OfflineSeedream.calls) == 2
        assert any(attempt.get("result_uncertain") for attempt in uncertain["attempts"])

        for account in accounts:
            account.error = {"code": "authentication_failed"}
        service.seedream.clear()
        missing = await submit("frameweave-missing-key")
        assert missing["error"]["code"] == "accounts_unavailable" and len(OfflineSeedream.calls) == 2
        service.seedream.save("offline-key")
        OfflineSeedream.fail = True
        failed = await submit("frameweave-seedream-failed")
        assert failed["status"] == "failed" and failed["fallback_provider"] == "seedream"
        assert failed["attempts"][-1]["error"]["code"] == "seedream_network_error"


PNG = (ROOT / "tests/fixtures/reference.png").read_bytes()
asyncio.run(main(Path(sys.argv[1])))
