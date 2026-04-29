#!/usr/bin/env python3
"""Tests for the on-chain BNB fallback valuation in run_trader_runtime.py.

Validates that _extract_portfolio_usd falls through to the on-chain path
when /me lacks portfolio_usd / balanceUsd / tokens[].value_usd fields.
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_trader_runtime as trader


class TestExtractPortfolioUsdPrimary(unittest.TestCase):
    """Priority 1 & 2: /me fields present → no fallback needed."""

    def test_direct_portfolio_usd_field(self):
        agent = {"ethAddr": "0xabc", "portfolio_usd": 42.5}
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertEqual(val, 42.5)
        self.assertEqual(src, "me.portfolio_usd")

    def test_balance_usd_field(self):
        agent = {"ethAddr": "0xabc", "balanceUsd": 10.0}
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertEqual(val, 10.0)
        self.assertEqual(src, "me.balanceUsd")

    def test_token_level_sum(self):
        agent = {
            "ethAddr": "0xabc",
            "tokens": [
                {"symbol": "BNB", "value_usd": 30.0},
                {"symbol": "BUIDL", "value_usd": 12.5},
            ],
        }
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertEqual(val, 42.5)
        self.assertEqual(src, "me.tokens[].value_usd")

    def test_none_agent(self):
        val, src = trader._extract_portfolio_usd(None, [])
        self.assertIsNone(val)
        self.assertEqual(src, "unavailable")


class TestExtractPortfolioUsdOnchainFallback(unittest.TestCase):
    """Priority 3: /me has no USD fields → fall back to BNB on-chain."""

    @patch("run_trader_runtime._fetch_bnb_price_usd")
    @patch("run_trader_runtime._fetch_bnb_balance_onchain")
    def test_fallback_computes_portfolio(self, mock_balance, mock_price):
        """BNB balance 0.1 × $600 = $60."""
        mock_balance.return_value = 0.1
        mock_price.return_value = 600.0

        agent = {"ethAddr": "0xDeadBeef000000000000000000000000000000aB", "op": 5, "vp": 3}
        val, src = trader._extract_portfolio_usd(agent, [])

        self.assertEqual(val, 60.0)
        self.assertEqual(src, "onchain_bnb_fallback")
        mock_balance.assert_called_once_with("0xDeadBeef000000000000000000000000000000aB")
        mock_price.assert_called_once()

    @patch("run_trader_runtime._fetch_bnb_price_usd")
    @patch("run_trader_runtime._fetch_bnb_balance_onchain")
    def test_fallback_zero_balance(self, mock_balance, mock_price):
        """Zero BNB balance → portfolio $0 (not None)."""
        mock_balance.return_value = 0.0
        mock_price.return_value = 600.0

        agent = {"ethAddr": "0xabc123", "op": 1}
        # 0x-prefix check: wallet must start with 0x
        val, src = trader._extract_portfolio_usd(agent, [])
        # "0xabc123" starts with 0x, so fallback triggers
        self.assertEqual(val, 0.0)
        self.assertEqual(src, "onchain_bnb_fallback")

    @patch("run_trader_runtime._fetch_bnb_price_usd")
    @patch("run_trader_runtime._fetch_bnb_balance_onchain")
    def test_fallback_balance_fetch_fails(self, mock_balance, mock_price):
        """If on-chain balance fetch fails → returns None (truthful degraded)."""
        mock_balance.return_value = None
        mock_price.return_value = 600.0

        agent = {"ethAddr": "0xDeadBeef", "op": 5}
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertIsNone(val)
        self.assertEqual(src, "unavailable")

    @patch("run_trader_runtime._fetch_bnb_price_usd")
    @patch("run_trader_runtime._fetch_bnb_balance_onchain")
    def test_fallback_price_fetch_fails(self, mock_balance, mock_price):
        """If price fetch fails → returns None (truthful degraded)."""
        mock_balance.return_value = 0.5
        mock_price.return_value = None

        agent = {"ethAddr": "0xDeadBeef", "op": 5}
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertIsNone(val)
        self.assertEqual(src, "unavailable")

    def test_fallback_no_wallet_address(self):
        """Agent dict present but no wallet address → skip fallback."""
        agent = {"op": 5, "vp": 3}
        val, src = trader._extract_portfolio_usd(agent, [])
        self.assertIsNone(val)
        self.assertEqual(src, "unavailable")


class TestTasTradeWithFallback(unittest.TestCase):
    """End-to-end: tas_trade should produce a score via the fallback path."""

    @patch("run_trader_runtime._fetch_bnb_price_usd")
    @patch("run_trader_runtime._fetch_bnb_balance_onchain")
    def test_tas_trade_not_partial_with_fallback(self, mock_balance, mock_price):
        """When fallback succeeds, tas_trade should be 'ok', not 'partial'."""
        mock_balance.return_value = 0.08  # 0.08 BNB
        mock_price.return_value = 600.0   # $600/BNB → portfolio = $48

        agent = {"ethAddr": "0xDeadBeef", "op": 5, "vp": 3}
        portfolio_usd, source = trader._extract_portfolio_usd(agent, [])

        self.assertIsNotNone(portfolio_usd)
        self.assertAlmostEqual(portfolio_usd, 48.0, places=2)

        tas_value, detail = trader._compute_tas_trade(portfolio_usd, 0.0)
        self.assertIsNotNone(tas_value)
        self.assertGreater(tas_value, 0)
        # portfolio_norm = min(48/50, 1) = 0.96
        # tas = 5 * 0.9 * 0.96 = 4.32
        self.assertAlmostEqual(tas_value, 4.32, places=2)


class TestFetchBnbBalanceOnchain(unittest.TestCase):
    """Unit tests for _fetch_bnb_balance_onchain with mocked HTTP."""

    @patch("urllib.request.urlopen")
    def test_normal_balance(self, mock_urlopen):
        # 0.5 BNB in wei = 500000000000000000 = 0x6f05b59d3b20000
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": "0x6f05b59d3b20000",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = trader._fetch_bnb_balance_onchain("0xDeadBeef")
        self.assertAlmostEqual(result, 0.5, places=6)

    @patch("urllib.request.urlopen")
    def test_zero_balance(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": "0x0",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = trader._fetch_bnb_balance_onchain("0xDeadBeef")
        self.assertEqual(result, 0.0)

    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_network_failure(self, mock_urlopen):
        result = trader._fetch_bnb_balance_onchain("0xDeadBeef")
        self.assertIsNone(result)


class TestFetchBnbPriceUsd(unittest.TestCase):
    """Unit tests for _fetch_bnb_price_usd with mocked HTTP."""

    @patch("urllib.request.urlopen")
    def test_plain_number_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"612.45"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = trader._fetch_bnb_price_usd()
        self.assertAlmostEqual(result, 612.45, places=2)

    @patch("urllib.request.urlopen")
    def test_json_wrapper_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"price": 605.0}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = trader._fetch_bnb_price_usd()
        self.assertAlmostEqual(result, 605.0, places=2)

    @patch("urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_network_failure(self, mock_urlopen):
        result = trader._fetch_bnb_price_usd()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
