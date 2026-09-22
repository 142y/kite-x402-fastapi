"""Kite x402 service template (Python + FastAPI).

Wraps an existing HTTP API behind x402 payments settled on the Kite chain.
Requests to ``/v1/*`` return HTTP 402 until the caller attaches a valid
``PAYMENT-SIGNATURE``; the payment is verified by the Kite facilitator, the
request is proxied to ``UPSTREAM_URL``, and the payment is settled only if the
upstream answered with a non-error status (verify -> upstream -> settle).

Behaviourally identical to the official TypeScript/Express and Go/Gin
templates in ``gokite-ai/kite-x402-services``: same environment variables,
same route prefix, same settle-on-success rule.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from x402 import x402ResourceServer
from x402.http import HTTPFacilitatorClient
from x402.http.middleware.fastapi import payment_middleware
from x402.mechanisms.evm.exact import ExactEvmServerScheme

from .config import Settings, load_settings
from .proxy import (
    upstream_request_headers,
    upstream_response_headers,
    upstream_target_url,
)

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def create_app(
    settings: Settings | None = None,
    *,
    upstream_client: httpx.AsyncClient | None = None,
    facilitator: Any | None = None,
) -> FastAPI:
    """Build the wrapper app.

    ``upstream_client`` and ``facilitator`` are injection points for tests;
    production code leaves them as ``None`` and the real HTTP clients are used.
    """
    settings = settings or load_settings()

    # 1. Facilitator + Kite pricing. Prices are passed as explicit AssetAmounts
    #    (atomic units + EIP-712 domain) because the Kite stablecoins are not
    #    in the x402 SDK's default asset table.
    facilitator = facilitator or HTTPFacilitatorClient(
        {"url": settings.facilitator_url}
    )
    server = x402ResourceServer(facilitator)
    server.register(settings.chain.network, ExactEvmServerScheme())

    app = FastAPI(
        title="Kite x402 wrapper",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # 2. Which routes cost money, and how much. Everything under /v1/ is paid.
    routes = {
        "* /v1/*": {
            "accepts": {
                "scheme": "exact",
                "price": settings.price,
                "network": settings.chain.network,
                "payTo": settings.pay_to,
                "maxTimeoutSeconds": 60,
            },
            "description": settings.service_description,
            "mimeType": "application/json",
        }
    }
    middleware = payment_middleware(routes, server)

    @app.middleware("http")
    async def x402_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
        return await middleware(request, call_next)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "network": settings.chain.network,
            "asset": settings.chain.asset_symbol,
            "price": f"${settings.price_usd}",
        }

    # 3. Proxy paid requests to the API being wrapped. The upstream credential
    #    is injected here and never reaches the caller.
    owns_client = upstream_client is None
    client = upstream_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    @app.api_route("/v1/{upstream_path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(upstream_path: str, request: Request) -> Response:
        # scope["query_string"] is raw bytes and keeps multi-value params intact.
        query_string = request.scope.get("query_string", b"")
        if isinstance(query_string, bytes):
            query_string = query_string.decode("latin-1")
        target = upstream_target_url(settings.upstream_url, request.url.path, query_string)
        headers = upstream_request_headers(
            dict(request.headers),
            settings.upstream_auth_header,
            settings.upstream_auth_value,
        )
        body = await request.body()
        try:
            upstream_response = await client.request(
                request.method,
                target,
                headers=headers,
                content=body if request.method not in ("GET", "HEAD") else None,
            )
        except httpx.HTTPError as err:
            # 502 is >= 400, so the payment middleware does not settle the charge.
            return JSONResponse(
                status_code=502, content={"error": "upstream unreachable", "detail": str(err)}
            )

        return Response(
            status_code=upstream_response.status_code,
            headers=upstream_response_headers(upstream_response.headers),
            content=upstream_response.content,
        )

    if owns_client:

        @app.on_event("shutdown")
        async def close_upstream_client() -> None:
            await client.aclose()

    return app


def _module_app() -> FastAPI:
    """Build the app from the environment (``kite_x402.app:app``).

    Lazy so that importing the module (e.g. for tests or tooling) does not
    require ``PAY_TO`` etc. to be set; misconfiguration then surfaces at
    server start-up instead, matching the official templates.
    """
    return create_app()


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "app":
        return _module_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """Run the wrapper with uvicorn (``python -m kite_x402``)."""
    import os

    import uvicorn

    port = int((os.environ.get("PORT") or "8080").strip())
    uvicorn.run("kite_x402.app:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
