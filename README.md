# Kite x402 Service Template (Python + FastAPI)

Turn any HTTP API into a paid service that AI agents on the [Kite](https://gokite.ai)
network can call and pay for per request, using the
[x402](https://www.x402.org) protocol with settlement on the Kite chain.

This is the Python/FastAPI counterpart of the official
[`typescript-express`](https://github.com/gokite-ai/kite-x402-services/tree/main/templates/typescript-express)
and [`go-gin`](https://github.com/gokite-ai/kite-x402-services/tree/main/templates/go-gin)
templates in [gokite-ai/kite-x402-services](https://github.com/gokite-ai/kite-x402-services),
built on the Coinbase [x402 Python SDK](https://pypi.org/project/x402/).
It is behaviourally identical: same environment variables, same route prefix
(`/v1/*`), same settle-on-success rule.

You bring the API. This wrapper answers `402 Payment Required`, verifies and
settles the payment through the Kite facilitator, and proxies paid requests to
your upstream.

```
agent (Kite Passport)                 your wrapper                      upstream API
────────────────────                 ─────────────                     ────────────
GET /v1/forecast ───────────────────► 402 + PAYMENT-REQUIRED
                                       (network, asset, amount, payTo)
sign EIP-3009 authorization
GET /v1/forecast
  PAYMENT-SIGNATURE: … ─────────────► facilitator /verify ✓
                                       GET /forecast ──────────────────► 200 JSON
                                       facilitator /settle ✓ (on-chain)
◄──────────────────────────────────── 200 JSON + PAYMENT-RESPONSE (tx hash)
```

No smart contracts, no wallet code, no gas: the buyer signs a token
authorization, the facilitator broadcasts it and pays gas, and USDC.e lands in
your wallet.

## Why a Kite-specific template

The Kite stablecoins (USDC.e on mainnet, pieUSD on testnet) are not in the
x402 SDK's built-in asset table, and their EIP-712 domains
(`Bridged USDC (Kite AI)` / `pieUSD`) must match the token contracts exactly
or every signature is rejected. `src/kite_x402/kite.py` ports the official
templates' approach: prices are converted from USD decimals into explicit
asset amounts (atomic units + EIP-712 domain metadata) with pure integer math,
so float rounding never leaks into the amount a buyer signs.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/142y/kite-x402-fastapi
cd kite-x402-fastapi
pip install -e .

cp .env.example .env   # edit PAY_TO at minimum
export $(grep -v '^#' .env | xargs)   # or use your env manager

python -m kite_x402     # uvicorn on :8080
```

Hit a paid route:

```bash
curl -i "localhost:8080/v1/forecast?latitude=52.52&longitude=13.41&current=temperature_2m"
```

You get `HTTP/1.1 402 Payment Required` and a base64 `PAYMENT-REQUIRED`
header that decodes to:

```json
{
  "x402Version": 2,
  "accepts": [{
    "scheme": "exact",
    "network": "eip155:2366",
    "asset": "0x7aB6f3ed87C42eF0aDb67Ed95090f8bF5240149e",
    "amount": "1000",
    "payTo": "0xYourKiteWallet",
    "maxTimeoutSeconds": 60,
    "extra": { "name": "Bridged USDC (Kite AI)", "version": "2" }
  }]
}
```

Every path under `/v1/` is paid and proxied to `UPSTREAM_URL` with the `/v1`
prefix stripped. `/healthz` is free. That is the whole contract.

## Environment variables

Same set as the official templates:

| Variable | Meaning | Default |
|---|---|---|
| `PAY_TO` | Wallet that receives payments (required) | — |
| `KITE_NETWORK` | `mainnet` (USDC.e) or `testnet` (pieUSD) | `mainnet` |
| `UPSTREAM_URL` | The API you are wrapping (required) | — |
| `PRICE_USD` | Price per call, USD decimal string, ≤ 6 fractional digits | `0.001` |
| `UPSTREAM_AUTH_HEADER` | Credential header injected upstream | `Authorization` |
| `UPSTREAM_AUTH_VALUE` | Credential value — never reaches the buyer | — |
| `SERVICE_DESCRIPTION` | Shown in the 402 challenge resource info | — |
| `PORT` | Listen port | `8080` |
| `FACILITATOR_URL` | Facilitator base URL (keep the `/v2`!) | `https://facilitator.pieverse.io/v2` |

## Payment ordering

`verify → upstream → settle`, always. The x402 middleware settles the payment
only after your upstream responds with a status below 400, so an unreachable
upstream (this wrapper returns `502`) or an upstream error never charges the
buyer. Charging before validating is the number one complaint about paid APIs
from agents; do not reorder this.

## Tests

```bash
pip install -e ".[dev]"
pytest -v
```

46 tests pin the contract: the 402 challenge shape (Kite network, asset,
atomic-unit amount, EIP-712 domain), USD→atomic-unit conversion edge cases,
header hygiene (hop-by-hop stripping, credential injection,
`PAYMENT-SIGNATURE` never forwarded), the proxy path/query/body mapping, and
the verify → upstream → settle ordering including the settle-only-on-success
rule — all against a stub facilitator and a mock upstream, no network needed.

CI (`.github/workflows/ci.yml`) runs the suite on Python 3.10–3.13 and boots
the wrapper to confirm the live 402 behaviour.

## Deploy

Deploy anywhere that serves public **https** (Fly, Render, Cloud Run, a VPS).
Kite Passport calls your URL from its servers, so localhost and browser-gated
tunnels do not work. Set `KITE_NETWORK=testnet` first and pay yourself once
with a sandbox agent (free pieUSD) before going live — see the
[upstream README](https://github.com/gokite-ai/kite-x402-services#test-with-a-kite-passport-agent)
for the `kpass` walkthrough.

Never commit `.env` or upstream keys.

## Kite network reference

| | Mainnet | Testnet |
|---|---|---|
| CAIP-2 network | `eip155:2366` | `eip155:2368` |
| RPC | `https://rpc.gokite.ai` | `https://rpc-testnet.gokite.ai` |
| Settlement asset | USDC.e `0x7aB6f3ed87C42eF0aDb67Ed95090f8bF5240149e` (6 decimals) | pieUSD `0x38129cf4CE5E183eFF248F42A7D345Bb1B47621A` (18 decimals) |
| EIP-712 domain | name `Bridged USDC (Kite AI)`, version `2` | name `pieUSD`, version `1` |
| Facilitator | `https://facilitator.pieverse.io/v2` | same |

## Repository layout

```
src/kite_x402/kite.py     Kite chain constants, USD → atomic-unit pricing
src/kite_x402/config.py   Environment parsing and validation
src/kite_x402/proxy.py    Reverse-proxy URL/header rules (RFC 9110 hygiene)
src/kite_x402/app.py      FastAPI app: /healthz free, /v1/* paid + proxied
tests/                    Contract tests (stub facilitator + mock upstream)
```

## License

Apache-2.0. See [LICENSE](LICENSE).
