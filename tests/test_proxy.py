"""Tests for the proxy helpers (pure URL/header logic)."""

from __future__ import annotations

import httpx

from kite_x402.proxy import (
    upstream_request_headers,
    upstream_response_headers,
    upstream_target_url,
)


class TestTargetUrl:
    def test_strips_v1_prefix(self):
        assert (
            upstream_target_url("https://api.open-meteo.com", "/v1/forecast", "latitude=52.52")
            == "https://api.open-meteo.com/forecast?latitude=52.52"
        )

    def test_nested_path(self):
        assert (
            upstream_target_url("https://api.example.com", "/v1/a/b/c", "")
            == "https://api.example.com/a/b/c"
        )

    def test_preserves_upstream_base_path(self):
        assert (
            upstream_target_url("https://api.example.com/v2/base", "/v1/users", "")
            == "https://api.example.com/v2/base/users"
        )

    def test_root_path(self):
        assert upstream_target_url("https://api.example.com", "/v1", "") == "https://api.example.com"


class TestRequestHeaders:
    def test_strips_payment_and_hop_by_hop_headers(self):
        headers = upstream_request_headers(
            {
                "Host": "wrapper.test",
                "Accept": "application/json",
                "Payment-Signature": "cGF5bG9hZA==",
                "Connection": "keep-alive",
                "Transfer-Encoding": "chunked",
                "Content-Length": "12",
            },
            "Authorization",
            None,
        )
        assert headers == {"Accept": "application/json"}

    def test_injects_upstream_credential_and_replaces_caller_value(self):
        headers = upstream_request_headers(
            {"Accept": "*/*", "Authorization": "Bearer caller-forged"},
            "Authorization",
            "Bearer sk-secret",
        )
        assert headers == {"Accept": "*/*", "Authorization": "Bearer sk-secret"}

    def test_no_credential_caller_header_passes_through(self):
        # Official-template behaviour: without an upstream credential the
        # caller's own credential header is forwarded as-is.
        headers = upstream_request_headers(
            {"Authorization": "Bearer caller"}, "Authorization", ""
        )
        assert headers == {"Authorization": "Bearer caller"}


class TestResponseHeaders:
    def test_strips_hop_by_hop_and_encoding(self):
        headers = upstream_response_headers(
            httpx.Headers(
                {
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                    "Transfer-Encoding": "chunked",
                    "Connection": "close",
                }
            )
        )
        assert {k.lower(): v for k, v in headers.items()} == {
            "content-type": "application/json"
        }
