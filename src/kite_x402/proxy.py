"""Reverse proxy helpers: everything under ``/v1/`` is proxied to the upstream.

The upstream credential is injected here and never reaches the caller; the
``PAYMENT-SIGNATURE`` header is stripped before forwarding, and hop-by-hop
headers are dropped in both directions per RFC 9110.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

#: Headers that must not be forwarded between proxy hops (RFC 9110 §7.6.1).
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

#: Request headers the proxy owns and must not forward upstream.
STRIPPED_REQUEST_HEADERS = HOP_BY_HOP | {"payment-signature", "payment-required"}

#: Response headers that describe the proxy's own transfer, not the payload.
STRIPPED_RESPONSE_HEADERS = HOP_BY_HOP | {"content-encoding"}


def upstream_target_url(upstream_url: str, path: str, query_string: str) -> str:
    """Map ``/v1/<path>`` on the wrapper to ``<upstream_url>/<path>``.

    The leading ``/v1`` prefix is stripped, the upstream's own base path (if
    any) is preserved, and the caller's query string is forwarded verbatim.
    """
    tail = path[len("/v1"):] if path.startswith("/v1") else path
    parts = urlsplit(upstream_url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path.rstrip("/") + tail,
            query_string,
            "",
        )
    )


def upstream_request_headers(
    incoming: dict[str, str],
    auth_header: str,
    auth_value: str | None,
) -> dict[str, str]:
    """Filter the incoming headers and inject the upstream credential.

    Matches the official templates: the caller's credential header is replaced
    by the proxy-injected one only when an upstream credential is configured.
    """
    headers = {
        k: v for k, v in incoming.items() if k.lower() not in STRIPPED_REQUEST_HEADERS
    }
    if auth_value:
        # Drop any caller-supplied value for the credential header first.
        headers = {k: v for k, v in headers.items() if k.lower() != auth_header.lower()}
        headers[auth_header] = auth_value
    return headers


def upstream_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Filter the upstream response headers before echoing them to the caller."""
    return {
        k: v for k, v in headers.items() if k.lower() not in STRIPPED_RESPONSE_HEADERS
    }
