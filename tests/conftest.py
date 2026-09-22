"""Shared fixtures: settings, a stub facilitator, a fake upstream and the app."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from x402.schemas import SettleResponse, VerifyResponse

from kite_x402.app import create_app
from kite_x402.config import load_settings
from kite_x402.kite import KITE_MAINNET

PAY_TO = "0x139Ee8B5f6f9aa326AfDe675A0E4eB269b3ADc19"
PAYER = "0x9990000000000000000000000000000000009999"
TX_HASH = "0x5b7d3a4c1e2f6098a7b6c5d4e3f2019283a4b5c6d7e8f901a2b3c4d5e6f7081"


BASE_ENV = {
    "PAY_TO": PAY_TO,
    "KITE_NETWORK": "mainnet",
    "UPSTREAM_URL": "http://upstream.test",
    "PRICE_USD": "0.001",
    "SERVICE_DESCRIPTION": "test upstream behind x402 on Kite",
}


class StubFacilitator:
    """Facilitator stand-in that records the call order and always approves."""

    def __init__(self, *, verify_valid: bool = True, settle_success: bool = True) -> None:
        self.calls: list[str] = []
        self.verify_valid = verify_valid
        self.settle_success = settle_success
        self.verify_args: list[tuple[object, object]] = []
        self.settle_args: list[tuple[object, object]] = []

    async def verify(self, payload, requirements):  # noqa: ANN001
        self.calls.append("verify")
        self.verify_args.append((payload, requirements))
        return VerifyResponse(
            is_valid=self.verify_valid,
            invalid_reason=None if self.verify_valid else "stub rejection",
            payer=PAYER,
        )

    async def settle(self, payload, requirements):  # noqa: ANN001
        self.calls.append("settle")
        self.settle_args.append((payload, requirements))
        return SettleResponse(
            success=self.settle_success,
            transaction=TX_HASH,
            network=KITE_MAINNET.network,
            payer=PAYER,
            amount=None,
        )

    async def supported(self):  # noqa: ANN201
        self.calls.append("supported")
        return {"eip155:2366": ["exact"], "eip155:2368": ["exact"]}

    def get_supported(self):  # noqa: ANN201
        """Sync accessor used by the middleware's startup initialization."""
        from x402.schemas import SupportedKind, SupportedResponse

        return SupportedResponse(
            kinds=[
                SupportedKind(x402_version=2, network="eip155:2366", scheme="exact"),
                SupportedKind(x402_version=2, network="eip155:2368", scheme="exact"),
            ]
        )


@pytest.fixture()
def settings():
    return load_settings(dict(BASE_ENV))


@pytest.fixture()
def stub_facilitator():
    return StubFacilitator()


def make_payment_signature_header() -> str:
    """A well-formed x402 v2 payment payload echoing the 402 challenge.

    The v2 ``PAYMENT-SIGNATURE`` header carries a base64 JSON body with the
    scheme payload plus the ``accepted`` requirement it fulfils — exactly what
    a buyer SDK sends back after receiving the 402 challenge.
    """
    accepted = {
        "scheme": "exact",
        "network": KITE_MAINNET.network,
        "asset": KITE_MAINNET.asset_address,
        "amount": "1000",
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 60,
        "extra": {"name": KITE_MAINNET.eip712_name, "version": KITE_MAINNET.eip712_version},
    }
    payload = {
        "x402Version": 2,
        "payload": {
            "authorization": {
                "from": PAYER,
                "to": PAY_TO,
                "value": "1000",
                "validAfter": "0",
                "validBefore": "999999999999999999",
                "nonce": "0x" + "11" * 32,
            },
            "signature": "0x" + "ab" * 65,
        },
        "accepted": accepted,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.fixture()
def payment_signature():
    return make_payment_signature_header()


def make_upstream_client(handler):  # noqa: ANN001, ANN201
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture()
def app_client(stub_facilitator):
    """App wired to a stub facilitator and an echo upstream, plus call log."""

    upstream_log: list[str] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        query = request.url.query
        if isinstance(query, bytes):
            query = query.decode("latin-1")
        upstream_log.append(f"{request.method} {request.url.path}?{query}")
        if request.url.path == "/boom":
            return httpx.Response(500, json={"error": "internal"})
        if request.url.path == "/notfound":
            return httpx.Response(404, json={"error": "missing"})
        return httpx.Response(
            200,
            json={
                "path": request.url.path,
                "query": query,
                "authorization": request.headers.get("Authorization"),
                "payment_signature_forwarded": request.headers.get("Payment-Signature"),
            },
        )

    upstream_client = make_upstream_client(upstream_handler)
    app = create_app(
        load_settings(dict(BASE_ENV)),
        upstream_client=upstream_client,
        facilitator=stub_facilitator,
    )

    with TestClient(app) as client:
        yield client, stub_facilitator, upstream_log
