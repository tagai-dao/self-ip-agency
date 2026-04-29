#!/usr/bin/env python3
"""Contract tests for TagClaw /me twitter-binding fields.

Sister to ``test_me_shape_normalization.py``. Where the shape test pins
envelope unwrapping, this test pins the **field contract**:

    When TagClaw reports status=active (verification tweet posted, backend
    binding complete), GET /tagclaw/me MUST return ``ownerTwitterId`` AND
    ``ownerTwitterHandle`` as non-empty strings in the inner agent dict.

Why this matters: the entire owner-binding self-heal chain (see
``docs/design/x-sync-twitter-binding-fix.md`` §4.3/§4.4) trusts that a
single /me call post-verification will populate ``agency-identity.json``'s
``owner.twitter_id`` and ``owner.twitter_handle``. If the server ever drops
these fields, heartbeat self-heal becomes a no-op and the installer's
"will auto-heal" UX is a lie.

Two modes:

1. **Offline fixture mode (default; always runs)** — asserts that our
   own ``extract_me_agent`` + ``refresh-agency-identity.sh`` python unwrap
   both surface the two binding fields across every envelope shape the
   server has ever shipped. The fixture IS the contract; changing it is
   equivalent to renegotiating the self-heal design (§7 #1, locked-in).

2. **Online contract mode (opt-in)** — when
   ``TAGCLAW_CONTRACT_TEST_API_KEY`` is set in the environment, this test
   actually hits ``GET https://bsc-api.tagai.fun/tagclaw/me`` with that
   key and asserts the same fields are present and non-empty. The API key
   must belong to an account whose Twitter verification tweet is live
   (``status=active``). CI should run this in a nightly job against a
   staging account; do NOT run in PR CI if the staging key can't be held
   in secrets without rotation.

Run:
    python3 scripts/test_me_twitter_binding_contract.py
    TAGCLAW_CONTRACT_TEST_API_KEY=sk_... python3 scripts/test_me_twitter_binding_contract.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from adapters.tagclaw import extract_me_agent  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: the contract freeze.
#
# These are the field names + representative non-empty values that the live
# ``/me`` response MUST carry for an account with status=active. Do not
# relax this fixture without simultaneously updating the design doc §7 #1
# and the heartbeat self-heal trigger conditions.
# ──────────────────────────────────────────────────────────────────────────────
VERIFIED_AGENT_FIXTURE = {
    "username": "selfipbot",
    "ethAddr": "0xe9e14077d6A1c58a796Ab206A8592AB8Ca4eC8b6",
    "ownerTwitterId": "1863098517117702145",
    "ownerTwitterHandle": "thefandotfun",
    "op": 12.34,
    "vp": 5.67,
}

REQUIRED_BINDING_FIELDS = ("ownerTwitterId", "ownerTwitterHandle")

# Historical envelope shapes the server has shipped. All must unwrap to the
# same inner agent dict and surface the required binding fields. See
# ``adapters/tagclaw.extract_me_agent`` and
# ``test_me_shape_normalization.py`` for the envelope contract itself.
ENVELOPE_SHAPES = [
    ("current_top_level_agent",
     {"success": True, "agent": VERIFIED_AGENT_FIXTURE}),
    ("nested_data_agent",
     {"success": True, "data": {"agent": VERIFIED_AGENT_FIXTURE}}),
    ("flat_data",
     {"success": True, "data": VERIFIED_AGENT_FIXTURE}),
    ("legacy_bare",
     VERIFIED_AGENT_FIXTURE),
]


class TestOfflineFixtureContract(unittest.TestCase):
    """Field contract survives every envelope shape via extract_me_agent."""

    def test_both_fields_present_and_non_empty_in_fixture(self):
        """Baseline: the fixture itself enshrines the contract."""
        for field in REQUIRED_BINDING_FIELDS:
            self.assertIn(field, VERIFIED_AGENT_FIXTURE,
                          f"{field} missing from canonical fixture")
            value = VERIFIED_AGENT_FIXTURE[field]
            self.assertIsInstance(value, str,
                                  f"{field} must be str, got {type(value).__name__}")
            self.assertTrue(value.strip(),
                            f"{field} must be a non-empty string")

    def test_extract_surfaces_binding_on_every_envelope(self):
        for shape_name, body in ENVELOPE_SHAPES:
            with self.subTest(envelope=shape_name):
                agent = extract_me_agent(body)
                for field in REQUIRED_BINDING_FIELDS:
                    self.assertIn(field, agent,
                                  f"[{shape_name}] extract_me_agent dropped {field}")
                    value = agent[field]
                    self.assertIsInstance(value, str,
                                          f"[{shape_name}] {field} wrong type")
                    self.assertTrue(value.strip(),
                                    f"[{shape_name}] {field} unexpectedly empty")


class TestRefreshScriptPythonUnwrap(unittest.TestCase):
    """The refresh-agency-identity.sh python block has its own unwrap +
    field-mapping logic (see scripts/refresh-agency-identity.sh
    around L170-L211). If that drifts from extract_me_agent, the
    self-heal chain silently breaks. Pin the mapping by running the same
    assertions against the same unwrap structure inlined here."""

    @staticmethod
    def _refresh_script_style_unwrap(body):
        """Mirror the helper-script unwrap verbatim (L179-L190)."""
        if not isinstance(body, dict):
            return {}
        agent = body.get("agent")
        if isinstance(agent, dict):
            return agent
        data = body.get("data")
        if isinstance(data, dict):
            nested = data.get("agent")
            if isinstance(nested, dict):
                return nested
            return data
        return body

    @staticmethod
    def _refresh_script_style_backfill(me_agent):
        """Mirror the helper-script field backfill (L210-L211)."""
        owner_twitter_id = (me_agent.get("ownerTwitterId")
                            or me_agent.get("owner_twitter_id") or "")
        owner_twitter_handle = (me_agent.get("ownerTwitterHandle")
                                or me_agent.get("owner_twitter_handle") or "")
        return owner_twitter_id, owner_twitter_handle

    def test_refresh_unwrap_matches_adapter_on_every_envelope(self):
        for shape_name, body in ENVELOPE_SHAPES:
            with self.subTest(envelope=shape_name):
                adapter_view = extract_me_agent(body)
                script_view = self._refresh_script_style_unwrap(body)
                self.assertEqual(
                    script_view, adapter_view,
                    f"[{shape_name}] refresh-script unwrap drifted from adapters.extract_me_agent",
                )

    def test_refresh_backfill_produces_non_empty_strings(self):
        for shape_name, body in ENVELOPE_SHAPES:
            with self.subTest(envelope=shape_name):
                me_agent = self._refresh_script_style_unwrap(body)
                twitter_id, twitter_handle = self._refresh_script_style_backfill(me_agent)
                self.assertTrue(
                    twitter_id and twitter_handle,
                    f"[{shape_name}] refresh backfill produced empty strings "
                    f"(id={twitter_id!r}, handle={twitter_handle!r})",
                )

    def test_snake_case_fallback_still_covered(self):
        """Defensive: if TagClaw ever switches camelCase to snake_case, the
        fallback chain must still surface the fields. Pin the fallback."""
        snake_agent = {
            "username": "selfipbot",
            "ethAddr": "0xe9e14077d6A1c58a796Ab206A8592AB8Ca4eC8b6",
            "owner_twitter_id": "1863098517117702145",
            "owner_twitter_handle": "thefandotfun",
        }
        body = {"success": True, "agent": snake_agent}
        me_agent = self._refresh_script_style_unwrap(body)
        twitter_id, twitter_handle = self._refresh_script_style_backfill(me_agent)
        self.assertEqual(twitter_id, "1863098517117702145")
        self.assertEqual(twitter_handle, "thefandotfun")

    def test_missing_field_causes_empty_backfill(self):
        """Negative: if /me ever regresses to drop the binding fields,
        backfill must surface an empty string so the refresh script's
        active-but-null gate (active_but_twitter_handle_null) fires."""
        stripped_agent = {
            "username": "selfipbot",
            "ethAddr": "0xe9e14077d6A1c58a796Ab206A8592AB8Ca4eC8b6",
        }
        body = {"success": True, "agent": stripped_agent}
        me_agent = self._refresh_script_style_unwrap(body)
        twitter_id, twitter_handle = self._refresh_script_style_backfill(me_agent)
        self.assertEqual(twitter_id, "")
        self.assertEqual(twitter_handle, "",
                         "empty binding must stay empty so the active_but_twitter_handle_null "
                         "gate in refresh-agency-identity.sh fires — otherwise heartbeat "
                         "self-heal would see a false-positive 'resolved' and stop retrying")


# ──────────────────────────────────────────────────────────────────────────────
# Online contract test: opt-in. Skipped unless TAGCLAW_CONTRACT_TEST_API_KEY
# is set. CI infra owners should wire a staging-account key into a nightly
# job; do NOT put this in PR-blocking CI without a clear rotation story.
# ──────────────────────────────────────────────────────────────────────────────
_ONLINE_API_KEY = os.environ.get("TAGCLAW_CONTRACT_TEST_API_KEY", "").strip()
_ONLINE_BASE = os.environ.get(
    "TAGCLAW_CONTRACT_TEST_BASE_URL",
    "https://bsc-api.tagai.fun/tagclaw",
).rstrip("/")
_ONLINE_TIMEOUT_SECONDS = int(os.environ.get("TAGCLAW_CONTRACT_TEST_TIMEOUT", "15"))


@unittest.skipUnless(
    _ONLINE_API_KEY,
    "set TAGCLAW_CONTRACT_TEST_API_KEY to run the online /me contract check",
)
class TestOnlineMeContract(unittest.TestCase):
    """Real /me call against a verified staging account."""

    def test_live_me_returns_binding_fields(self):
        url = f"{_ONLINE_BASE}/me/"  # trailing slash avoids POST→GET redirect shims
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {_ONLINE_API_KEY}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=_ONLINE_TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self.fail(f"/me HTTP {e.code}: {body[:500]}")
        except urllib.error.URLError as e:
            self.fail(f"/me network error: {e.reason}")

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            self.fail(f"/me body is not JSON: {e} ; first 500 chars: {raw[:500]}")

        agent = extract_me_agent(body)
        self.assertIsInstance(agent, dict, f"/me did not unwrap to a dict; got {type(agent).__name__}")

        missing = [f for f in REQUIRED_BINDING_FIELDS if not (agent.get(f) or "").strip()]
        self.assertFalse(
            missing,
            f"/me response missing required binding field(s) {missing} — "
            f"the heartbeat self-heal design assumes these are present once "
            f"the account is active. If this test fails, §7 #1 of the design "
            f"doc needs reopening. Response agent keys: {sorted(agent.keys())}",
        )


if __name__ == "__main__":
    unittest.main()
