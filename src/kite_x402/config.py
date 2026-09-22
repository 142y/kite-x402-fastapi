"""Environment configuration for the Kite x402 FastAPI wrapper.

Reads the same environment variables as the official TypeScript/Express and
Go/Gin templates in ``gokite-ai/kite-x402-services`` so operators can move
between templates without changing their deployment environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .kite import FACILITATOR_URL, KiteChain, kite_chain_by_name, price_to_asset_amount
from x402.schemas import AssetAmount


def _env(key: str, fallback: str = "") -> str:
    return (os.environ.get(key) or "").strip() or fallback


@dataclass(frozen=True)
class Settings:
    """Validated wrapper configuration parsed from the environment."""

    pay_to: str
    chain: KiteChain
    upstream_url: str
    price_usd: str
    price: AssetAmount
    upstream_auth_header: str
    upstream_auth_value: str
    service_description: str
    port: int
    facilitator_url: str


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``env`` (or ``os.environ``).

    Raises ``ValueError`` with an actionable message when a required variable
    is missing or malformed, so misconfiguration fails fast at startup.
    """
    get = (lambda k, d="": (env.get(k) or "").strip() or d) if env is not None else _env

    pay_to = get("PAY_TO")
    if not pay_to:
        raise ValueError("PAY_TO is required: the Kite wallet address that receives payments")

    upstream_url = get("UPSTREAM_URL")
    if urlparse(upstream_url).scheme not in ("http", "https"):
        raise ValueError("UPSTREAM_URL is required, e.g. https://api.example.com")

    chain = kite_chain_by_name(get("KITE_NETWORK", "mainnet"))

    price_usd = get("PRICE_USD", "0.001")
    price = price_to_asset_amount(price_usd, chain)

    return Settings(
        pay_to=pay_to,
        chain=chain,
        upstream_url=upstream_url,
        price_usd=price_usd,
        price=price,
        upstream_auth_header=get("UPSTREAM_AUTH_HEADER", "Authorization"),
        upstream_auth_value=get("UPSTREAM_AUTH_VALUE"),
        service_description=get("SERVICE_DESCRIPTION", "Paid API wrapped for the Kite network"),
        port=int(get("PORT", "8080")),
        facilitator_url=get("FACILITATOR_URL", FACILITATOR_URL),
    )
