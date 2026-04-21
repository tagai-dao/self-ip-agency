#!/usr/bin/env python3
"""Regression tests for /me response envelope normalization.

The TagClaw API has shipped four different /me envelope shapes over time:
  1. {"success": true, "agent": {...}}            (current, 2026-04)
  2. {"success": true, "data": {"agent": {...}}}  (older nested wrapper)
  3. {"success": true, "data": {...flat...}}      (earlier flat-wrapped)
  4. {...flat...}                                  (legacy bare dict)

All readers in the repo must normalize to the inner agent dict. This test
pins the contract for:
  - adapters/tagclaw.extract_me_agent (canonical helper)
  - scripts/run_trader_runtime_v1.fetch_agent_state

The historical bug: install read shape (1) but only handled (2)/(3)/(4),
which left owner.twitter_handle null and broke the guided X sync.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from adapters.tagclaw import extract_me_agent  # noqa: E402


AGENT_FIXTURE = {
    "username": "selfipbot",
    "ethAddr": "0xe9e14077d6A1c58a796Ab206A8592AB8Ca4eC8b6",
    "ownerTwitterId": "1863098517117702145",
    "ownerTwitterHandle": "thefandotfun",
    "op": 12.34,
    "vp": 5.67,
}


class TestExtractMeAgent(unittest.TestCase):
    """Contract: every envelope shape unwraps to the same inner agent dict."""

    def test_current_top_level_agent_shape(self):
        """Shape 1 (current, 2026-04): {success, agent: {...}}"""
        body = {"success": True, "agent": AGENT_FIXTURE}
        self.assertEqual(extract_me_agent(body), AGENT_FIXTURE)

    def test_nested_data_agent_shape(self):
        """Shape 2: {success, data: {agent: {...}}}"""
        body = {"success": True, "data": {"agent": AGENT_FIXTURE}}
        self.assertEqual(extract_me_agent(body), AGENT_FIXTURE)

    def test_flat_data_shape(self):
        """Shape 3: {success, data: {...flat agent fields...}}"""
        body = {"success": True, "data": AGENT_FIXTURE}
        self.assertEqual(extract_me_agent(body), AGENT_FIXTURE)

    def test_legacy_bare_dict_shape(self):
        """Shape 4: {...flat agent fields...}"""
        self.assertEqual(extract_me_agent(AGENT_FIXTURE), AGENT_FIXTURE)

    def test_top_level_agent_wins_over_data(self):
        """If both are present, top-level agent is canonical."""
        body = {
            "success": True,
            "agent": AGENT_FIXTURE,
            "data": {"agent": {"username": "stale"}},
        }
        self.assertEqual(extract_me_agent(body), AGENT_FIXTURE)

    def test_non_dict_body_returns_empty(self):
        self.assertEqual(extract_me_agent(None), {})
        self.assertEqual(extract_me_agent([]), {})
        self.assertEqual(extract_me_agent("not json"), {})

    def test_owner_twitter_handle_surfaces_on_current_shape(self):
        """Guard against the exact bug that broke guided X sync: server
        returns shape 1 → ownerTwitterHandle must be reachable."""
        body = {"success": True, "agent": AGENT_FIXTURE}
        agent = extract_me_agent(body)
        self.assertEqual(agent.get("ownerTwitterHandle"), "thefandotfun")


class TestTraderFetchAgentState(unittest.TestCase):
    """Trader runtime must see the same agent regardless of envelope."""

    def _run_with_resp(self, resp):
        import run_trader_runtime_v1 as trader
        orig = trader.tagclaw_get
        trader.tagclaw_get = lambda path, key: resp
        try:
            return trader.fetch_agent_state("dummy-key")
        finally:
            trader.tagclaw_get = orig

    def test_current_top_level_agent(self):
        agent, err = self._run_with_resp({"success": True, "agent": AGENT_FIXTURE})
        self.assertIsNone(err)
        self.assertEqual(agent, AGENT_FIXTURE)

    def test_nested_data_agent(self):
        agent, err = self._run_with_resp({"success": True, "data": {"agent": AGENT_FIXTURE}})
        self.assertIsNone(err)
        self.assertEqual(agent, AGENT_FIXTURE)

    def test_flat_data(self):
        agent, err = self._run_with_resp({"success": True, "data": AGENT_FIXTURE})
        self.assertIsNone(err)
        self.assertEqual(agent, AGENT_FIXTURE)

    def test_legacy_bare(self):
        agent, err = self._run_with_resp(AGENT_FIXTURE)
        self.assertIsNone(err)
        self.assertEqual(agent, AGENT_FIXTURE)

    def test_missing_response(self):
        agent, err = self._run_with_resp(None)
        self.assertIsNone(agent)
        self.assertEqual(err, "could not fetch /me")

    def test_unexpected_shape(self):
        agent, err = self._run_with_resp(["not", "a", "dict"])
        self.assertIsNone(agent)
        self.assertEqual(err, "unexpected /me shape")


if __name__ == "__main__":
    unittest.main()
