"""Tests for Kite chain constants and USD -> AssetAmount conversion."""

from __future__ import annotations

import pytest

from kite_x402.kite import (
    FACILITATOR_URL,
    KITE_MAINNET,
    KITE_TESTNET,
    kite_chain_by_name,
    price_to_asset_amount,
)


class TestChains:
    def test_mainnet_constants(self):
        assert KITE_MAINNET.network == "eip155:2366"
        assert KITE_MAINNET.asset_symbol == "USDC.e"
        assert KITE_MAINNET.asset_decimals == 6
        assert KITE_MAINNET.eip712_name == "Bridged USDC (Kite AI)"
        assert KITE_MAINNET.eip712_version == "2"

    def test_testnet_constants(self):
        assert KITE_TESTNET.network == "eip155:2368"
        assert KITE_TESTNET.asset_symbol == "pieUSD"
        assert KITE_TESTNET.asset_decimals == 18
        assert KITE_TESTNET.eip712_name == "pieUSD"
        assert KITE_TESTNET.eip712_version == "1"

    def test_facilitator_url_keeps_v2_prefix(self):
        assert FACILITATOR_URL == "https://facilitator.pieverse.io/v2"

    @pytest.mark.parametrize(
        ("name", "chain"),
        [
            (None, KITE_MAINNET),
            ("", KITE_MAINNET),
            ("mainnet", KITE_MAINNET),
            ("testnet", KITE_TESTNET),
            ("  testnet  ", KITE_TESTNET),
        ],
    )
    def test_kite_chain_by_name(self, name, chain):
        assert kite_chain_by_name(name) is chain

    def test_unknown_network_rejected(self):
        with pytest.raises(ValueError, match="unknown KITE_NETWORK"):
            kite_chain_by_name("devnet")


class TestPriceConversion:
    def test_mainnet_usdc_six_decimals(self):
        amount = price_to_asset_amount("0.001", KITE_MAINNET)
        assert amount.amount == "1000"
        assert amount.asset == KITE_MAINNET.asset_address
        assert amount.extra == {"name": "Bridged USDC (Kite AI)", "version": "2"}

    def test_dollar_prefix_accepted(self):
        assert price_to_asset_amount("$0.001", KITE_MAINNET).amount == "1000"

    def test_whole_dollar(self):
        assert price_to_asset_amount("1", KITE_MAINNET).amount == "1000000"

    def test_full_precision(self):
        assert price_to_asset_amount("0.000001", KITE_MAINNET).amount == "1"

    def test_testnet_eighteen_decimals(self):
        # pieUSD has 18 decimals: $0.001 -> 10^15 atomic units.
        assert price_to_asset_amount("0.001", KITE_TESTNET).amount == "1000000000000000"

    def test_numeric_input(self):
        assert price_to_asset_amount(0.001, KITE_MAINNET).amount == "1000"

    def test_too_many_decimals_rejected(self):
        with pytest.raises(ValueError, match="more than 6 decimals"):
            price_to_asset_amount("0.0000001", KITE_MAINNET)

    @pytest.mark.parametrize("bad", ["0", "-1", "$-0.5", "abc", "", "$"])
    def test_non_positive_or_garbage_rejected(self, bad):
        with pytest.raises(ValueError, match="positive decimal"):
            price_to_asset_amount(bad, KITE_MAINNET)

    def test_sub_unit_price_rejected(self):
        # Any positive price with <= 18 fractional digits is at least one
        # atomic unit; anything finer is rejected (here: by the decimals check).
        with pytest.raises(ValueError, match="decimals|below one unit"):
            price_to_asset_amount("0.0000000000000000005", KITE_TESTNET)
