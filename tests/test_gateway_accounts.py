import json

import httpx
from fastapi.testclient import TestClient

from studio.api import create_app


def test_account_proxy_uses_local_gateway_and_keeps_credentials_private(tmp_path, caplog):
    accounts = []
    credentials = {}
    calls = []

    def handler(request):
        assert request.url.host == "127.0.0.1"
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/accounts" and request.method == "POST":
            body = json.loads(request.content)
            account = {
                "id": f"offline_{len(accounts) + 1}",
                "name": body["name"],
                "enabled": True,
                "state": "configured",
            }
            accounts.append(account)
            credentials[account["id"]] = body["secure_1psid"]
            return httpx.Response(201, json=account)
        if request.url.path == "/api/accounts" and request.method == "GET":
            return httpx.Response(200, json={"data": accounts})
        if request.url.path == "/api/accounts/offline_2" and request.method == "PUT":
            body = json.loads(request.content)
            accounts[1].update(name=body["name"], enabled=body["enabled"], state="disabled")
            return httpx.Response(200, json=accounts[1])
        if request.url.path == "/api/accounts/offline_1/connect" and request.method == "POST":
            accounts[0]["state"] = "ready"
            return httpx.Response(200, json={"connected": True, "models": []})
        return httpx.Response(200, json={"data": []})

    app = create_app(tmp_path, start_worker=False, transport=httpx.MockTransport(handler))
    app.state.providers.gateway = "http://127.0.0.1:8766"
    with TestClient(app) as client:
        for name, secret in [("离线账号甲", "offline-secret-a"), ("离线账号乙", "offline-secret-b")]:
            response = client.post("/api/gateway/accounts", json={"name": name, "secure_1psid": secret})
            assert response.status_code == 201 and secret not in response.text

        catalog = client.get("/api/gateway/catalog")
        assert [account["name"] for account in catalog.json()["accounts"]] == ["离线账号甲", "离线账号乙"]
        assert "offline-secret-a" not in catalog.text and "offline-secret-b" not in catalog.text
        assert "offline-secret-a" not in client.get("/api/settings").text
        assert client.post("/api/gateway/accounts/offline_1/connect").status_code == 200
        assert (
            client.put(
                "/api/gateway/accounts/offline_2", json={"name": "离线账号乙", "enabled": False}
            ).status_code
            == 200
        )
        assert accounts[0]["enabled"] and accounts[0]["state"] == "ready"
        assert not accounts[1]["enabled"] and credentials["offline_2"] == "offline-secret-b"

        before = len(calls)
        denied = client.post(
            "/api/gateway/accounts",
            json={"name": "third", "secure_1psid": "offline-secret-c"},
            headers={"Origin": "https://attacker.invalid"},
        )
        assert denied.status_code == 403 and len(calls) == before
    assert all(secret not in caplog.text for secret in credentials.values())


def test_offline_seedream_proxy_syncs_and_does_not_disclose_credentials(tmp_path, caplog):
    secret = "offline-seedream-secret"
    state = {"configured": False}
    calls = []

    def handler(request):
        assert request.url.host == "127.0.0.1"
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            assert json.loads(request.content)["api_key"] == secret
            state["configured"] = True
        elif request.method == "DELETE":
            state["configured"] = False
        return httpx.Response(200, json={
            **state, "model": "doubao-seedream-5-0-flash-260915",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "output_price_cny": 0.12, "api_key": secret,
        })

    app = create_app(tmp_path, start_worker=False, transport=httpx.MockTransport(handler))
    app.state.providers.gateway = "http://127.0.0.1:8766"
    with TestClient(app) as client:
        assert not client.get("/api/gateway/seedream").json()["configured"]
        result = client.put("/api/gateway/seedream", json={"api_key": secret})
        assert result.json()["configured"] and secret not in result.text
        assert client.get("/api/gateway/seedream").json()["configured"]
        assert not client.delete("/api/gateway/seedream").json()["configured"]
        count = len(calls)
        assert client.post("/api/gateway/seedream").status_code == 404
        assert client.get("/api/gateway/seedream/extra").status_code == 404
        assert client.put("/api/gateway/seedream", json={"api_key": secret},
                          headers={"Origin": "https://attacker.invalid"}).status_code == 403
        assert len(calls) == count
    assert secret not in caplog.text


def test_offline_seedream_proxy_redacts_upstream_errors_and_invalid_responses(tmp_path):
    secret = "offline-sensitive-secret"
    responses = iter([
        httpx.Response(400, json={"error": secret}),
        httpx.Response(200, json={"configured": True, "model": secret}),
    ])
    app = create_app(tmp_path, start_worker=False, transport=httpx.MockTransport(lambda _: next(responses)))
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/api/gateway/seedream")
            assert response.is_error and secret not in response.text
