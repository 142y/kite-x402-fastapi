"""Kite chain constants and x402 pricing helpers.

Python port of ``templates/typescript-express/src/kite.ts`` from the official
``gokite-ai/kite-x402-services`` repository: the Kite stablecoins are not part
of the x402 SDK's built-in asset table, so prices declared as ``$0.001`` are
converted here into explicit :class:`x402.schemas.AssetAmount` objects that pin
the asset address and the EIP-712 domain (name/version) the Kite facilitator
expects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from x402.schemas import AssetAmount

#: Facilitator that verifies and settles on both Kite networks. The x402 SDK
#: appends ``/verify``, ``/settle`` and ``/supported``, so keep the ``/v2``
#: prefix — ``https://facilitator.pieverse.io`` without it returns 404s that
#: surface as "settlement failed".
FACILITATOR_URL = "https://facilitator.pieverse.io/v2"

_DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")


@dataclass(frozen=True)
class KiteChain:
    """One Kite network the wrapper can charge on.

    Only the stablecoin the Kite facilitator settles for that network is
    listed: the payer signs an EIP-3009 ``transferWithAuthorization``, so the
    asset must implement EIP-3009 and the EIP-712 domain (name/version) must
    match the token contract exactly.
    """

    network: str
    rpc_url: str
    asset_address: str
    asset_symbol: str
    asset_decimals: int
    eip712_name: str
    eip712_version: str


#: Kite mainnet: Bridged USDC (USDC.e), 6 decimals.
KITE_MAINNET = KiteChain(
    network="eip155:2366",
    rpc_url="https://rpc.gokite.ai",
    asset_address="0x7aB6f3ed87C42eF0aDb67Ed95090f8bF5240149e",
    asset_symbol="USDC.e",
    asset_decimals=6,
    eip712_name="Bridged USDC (Kite AI)",
    eip712_version="2",
)

#: Kite testnet: pieUSD, 18 decimals. Kite Passport sandbox agents pay with this.
KITE_TESTNET = KiteChain(
    network="eip155:2368",
    rpc_url="https://rpc-testnet.gokite.ai",
    asset_address="0x38129cf4CE5E183eFF248F42A7D345Bb1B47621A",
    asset_symbol="pieUSD",
    asset_decimals=18,
    eip712_name="pieUSD",
    eip712_version="1",
)


def kite_chain_by_name(name: str | None) -> KiteChain:
    """Resolve ``KITE_NETWORK`` (``mainnet`` or ``testnet``) to chain config."""
    key = (name or "mainnet").strip()
    if key in ("", "mainnet"):
        return KITE_MAINNET
    if key == "testnet":
        return KITE_TESTNET
    raise ValueError(f'unknown KITE_NETWORK "{name}" (want mainnet or testnet)')


def price_to_asset_amount(price: str | float, chain: KiteChain) -> AssetAmount:
    """Convert a USD price like ``"0.001"`` / ``"$0.001"`` to an :class:`AssetAmount`.

    Integer math on the decimal string, mirroring ``kiteMoneyParser`` in the
    official TypeScript template: float rounding must not creep into the
    amount the buyer is asked to sign. Raises for non-positive prices and for
    more fractional digits than the chain's stablecoin has.
    """
    if isinstance(price, (int, float)):
        text = f"{price:.{chain.asset_decimals}f}"
    else:
        text = price.strip().lstrip("$")
    if not _DECIMAL_RE.match(text) or float(text) <= 0:
        raise ValueError(f"price must be a positive decimal, got {price!r}")

    whole, _, frac = text.partition(".")
    if len(frac) > chain.asset_decimals:
        raise ValueError(
            f"price {text} has more than {chain.asset_decimals} decimals ({chain.asset_symbol})"
        )
    units = int(whole + frac.ljust(chain.asset_decimals, "0"))
    if units <= 0:
        raise ValueError(f"price {price!r} is below one unit of {chain.asset_symbol}")

    return AssetAmount(
        amount=str(units),
        asset=chain.asset_address,
        extra={"name": chain.eip712_name, "version": chain.eip712_version},
    )
