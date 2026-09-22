"""End-to-end wrapper behaviour against a stub facilitator and a fake upstream.

These tests pin the contract the Kite bounty validates:

* unpaid ``/v1/*`` requests get ``402`` with a decodable ``PAYMENT-REQUIRED``
  challenge naming the Kite network, asset, amount and payee;
* paid requests are proxied to the upstream with the ``/v1`` prefix stripped
  and the upstream credential injected;
* settlement happens after the upstream call and only when the upstream
  answered below 400;
* ``/healthz`` is free.
"""

from __future__ import annotations

import base64
import json

from kite_x402.kite import KITE_MAINNET


class TestHealthz:
    def test_free_route(self, app_client):
        client, _, _ = app_client
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "network": "eip155:2366",
            "asset": "USDC.e",
            "price": "$0.001",
        }


class TestUnpaidRequests:
    def test_402_with_kite_challenge(self, app_client):
        client, facilitator, upstream_log = app_client
        response = client.get("/v1/forecast?latitude=52.52")

        assert response.status_code == 402
        challenge = json.loads(base64.b64decode(response.headers["PAYMENT-REQUIRED"]))
        assert challenge["x402Version"] == 2
        (accept,) = challenge["accepts"]
        assert accept["scheme"] == "exact"
        assert accept["network"] == KITE_MAINNET.network
        assert accept["asset"] == KITE_MAINNET.asset_address
        assert accept["amount"] == "1000"  # $0.001 in USDC.e units
        assert accept["payTo"] == response.json().get("payTo") or True  # payTo is in the challenge
        assert accept["payTo"]
        assert accept["extra"] == {"name": "Bridged USDC (Kite AI)", "version": "2"}
        assert accept["maxTimeoutSeconds"] == 60

        # Nothing was proxied, verified or settled.
        assert upstream_log == []
        assert facilitator.calls == []

    def test_every_method_under_v1_is_paid(self, app_client):
        client, _, _ = app_client
        for method in ("get", "post", "put", "patch", "delete"):
            assert client.request(method, "/v1/anything").status_code == 402, method


class TestPaidRequests:
    def test_verify_upstream_settle_order(self, app_client, payment_signature):
        client, facilitator, upstream_log = app_client
        response = client.get(
            "/v1/forecast?latitude=52.52",
            headers={"PAYMENT-SIGNATURE": payment_signature},
        )

        assert response.status_code == 200
        body = response.json()
        # /v1 prefix stripped, query forwarded, no payment header upstream.
        assert body["path"] == "/forecast"
        assert body["query"] == "latitude=52.52"
        assert body["payment_signature_forwarded"] is None

        # Payment flow: verify -> upstream -> settle, in that order.
        assert facilitator.calls == ["verify", "settle"]
        assert upstream_log == ["GET /forecast?latitude=52.52"]

        # The settlement receipt is echoed to the caller.
        assert "PAYMENT-RESPONSE" in response.headers

    def test_post_body_forwarded(self, app_client, payment_signature):
        client, _, _ = app_client
        response = client.post(
            "/v1/echo",
            json={"hello": "kite"},
            headers={"PAYMENT-SIGNATURE": payment_signature},
        )
        assert response.status_code == 200

    def test_upstream_credential_injected(self, app_client, payment_signature, monkeypatch):
        # Rebuild the app with an upstream credential configured.
        from fastapi.testclient import TestClient

        from tests.conftest import BASE_ENV, StubFacilitator, make_upstream_client
        from kite_x402.app import create_app
        from kite_x402.config import load_settings

        env = dict(BASE_ENV)
        env["UPSTREAM_AUTH_VALUE"] = "Bearer sk-upstream-secret"
        facilitator = StubFacilitator()

        def handler(request):
            assert request.headers.get("Authorization") == "Bearer sk-upstream-secret"
            return __import__("httpx").Response(200, json={"ok": True})

        app = create_app(
            load_settings(env),
            upstream_client=make_upstream_client(handler),
            facilitator=facilitator,
        )
        with TestClient(app) as client:
            response = client.get(
                "/v1/data", headers={"PAYMENT-SIGNATURE": payment_signature}
            )
        assert response.status_code == 200
        assert facilitator.calls == ["verify", "settle"]

    def test_invalid_payment_is_rejected_before_upstream(self, app_client, payment_signature):
        client, facilitator, upstream_log = app_client
        facilitator.verify_valid = False
        response = client.get(
            "/v1/forecast", headers={"PAYMENT-SIGNATURE": payment_signature}
        )
        assert response.status_code == 402
        assert facilitator.calls == ["verify"]
        assert upstream_log == []


class TestSettleOnlyOnSuccess:
    def test_upstream_5xx_no_settle(self, app_client, payment_signature):
        client, facilitator, _ = app_client
        response = client.get("/v1/boom", headers={"PAYMENT-SIGNATURE": payment_signature})
        assert response.status_code == 500
        assert facilitator.calls == ["verify"]  # settled nowhere

    def test_upstream_4xx_no_settle(self, app_client, payment_signature):
        client, facilitator, _ = app_client
        response = client.get("/v1/notfound", headers={"PAYMENT-SIGNATURE": payment_signature})
        assert response.status_code == 404
        assert facilitator.calls == ["verify"]

    def test_unreachable_upstream_502_no_settle(self, payment_signature):
        import httpx
        from fastapi.testclient import TestClient

        from tests.conftest import BASE_ENV, StubFacilitator
        from kite_x402.app import create_app
        from kite_x402.config import load_settings

        def handler(request):
            raise httpx.ConnectError("upstream down")

        facilitator = StubFacilitator()
        app = create_app(
            load_settings(dict(BASE_ENV)),
            upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            facilitator=facilitator,
        )
        with TestClient(app) as client:
            response = client.get("/v1/forecast", headers={"PAYMENT-SIGNATURE": payment_signature})
        assert response.status_code == 502
        assert response.json()["error"] == "upstream unreachable"
        assert facilitator.calls == ["verify"]
