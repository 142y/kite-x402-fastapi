"""Tests for environment configuration."""

from __future__ import annotations

import pytest

from kite_x402.config import load_settings


def test_full_env():
    settings = load_settings(
        {
            "PAY_TO": "0xAbC0000000000000000000000000000000000001",
            "KITE_NETWORK": "testnet",
            "UPSTREAM_URL": "https://api.example.com/base",
            "PRICE_USD": "0.05",
            "UPSTREAM_AUTH_HEADER": "X-Api-Key",
            "UPSTREAM_AUTH_VALUE": "secret",
            "SERVICE_DESCRIPTION": "desc",
            "PORT": "9090",
            "FACILITATOR_URL": "https://facilitator.example/v2",
        }
    )
    assert settings.chain.network == "eip155:2368"
    assert settings.upstream_url == "https://api.example.com/base"
    assert settings.price.amount == "50000000000000000"  # $0.05 in pieUSD units
    assert settings.upstream_auth_header == "X-Api-Key"
    assert settings.facilitator_url == "https://facilitator.example/v2"
    assert settings.port == 9090


def test_defaults():
    settings = load_settings(
        {"PAY_TO": "0xAbC0000000000000000000000000000000000001", "UPSTREAM_URL": "https://api.example.com"}
    )
    assert settings.chain.network == "eip155:2366"  # mainnet
    assert settings.price_usd == "0.001"
    assert settings.price.amount == "1000"
    assert settings.upstream_auth_header == "Authorization"
    assert settings.upstream_auth_value == ""
    assert settings.port == 8080


def test_missing_pay_to():
    with pytest.raises(ValueError, match="PAY_TO is required"):
        load_settings({"UPSTREAM_URL": "https://api.example.com"})


def test_missing_or_bad_upstream_url():
    with pytest.raises(ValueError, match="UPSTREAM_URL is required"):
        load_settings({"PAY_TO": "0xAbC"})
    with pytest.raises(ValueError, match="UPSTREAM_URL is required"):
        load_settings({"PAY_TO": "0xAbC", "UPSTREAM_URL": "ftp://nope.example"})


def test_bad_network():
    with pytest.raises(ValueError, match="unknown KITE_NETWORK"):
        load_settings(
            {"PAY_TO": "0xAbC", "UPSTREAM_URL": "https://api.example.com", "KITE_NETWORK": "x"}
        )
