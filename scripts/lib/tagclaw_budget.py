"""tagclaw_budget.py — OP/VP awareness for the bookmarker social pipeline.

Plan C foundation (2026-05-26). Lets every downstream script know:
  - the live OP / VP balance (from /tagclaw/me)
  - per-action cost table
  - today's regen budget vs. today's consumed
  - "do I still have headroom for action X?"

State file: ``runtime/bookmarker/op-vp-state.json``. Keyed by UTC date.
Refresh happens on every ``read_balance()`` if older than 5 min.

Cost table comes from the TagClaw HEARTBEAT.md doc:
  post     = 200 OP
  reply    =  50 OP
  retweet  =   3 OP
  like     =   3 OP
  curate   =   0 OP, ~7 VP per vote (curation; VP-priced)

Daily target = 667 OP / 67 VP (regen rate per TagClaw docs:
2000 OP over 3 days, 200 VP over 3 days).
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[2])
STATE_PATH = WORKSPACE / "runtime" / "bookmarker" / "op-vp-state.json"
CREDS_PATH = WORKSPACE.parent / "workspace-bookmarker" / "runtime" / "credentials" / "tagclaw-bookmarker.json"

TAGCLAW_API_BASE = "https://bsc-api.tagai.fun/tagclaw"
ME_PATH = "/me"
REFRESH_THRESHOLD_SECONDS = 300  # 5 min

# Action → OP/VP cost. Source: skills/tagclaw/HEARTBEAT.md.
ACTION_COSTS: dict[str, dict[str, float]] = {
    "post":    {"op": 200, "vp": 0},
    "reply":   {"op": 50,  "vp": 0},
    "retweet": {"op": 3,   "vp": 0},
    # `like` and `curate` both POST /tagclaw/like; differ only in `vp` field.
    # Per SKILL.md: vp=1..10; OP cost is fixed at 3, VP cost equals the `vp` value.
    "like":    {"op": 3,   "vp": 1},   # weak signal, 1 VP
    "curate":  {"op": 3,   "vp": 7},   # strong weighted upvote, 7 VP
    "follow":  {"op": 5,   "vp": 0},   # not in heartbeat doc; conservative guess
}

# Daily regen target.
DAILY_OP_TARGET = 667
DAILY_VP_TARGET = 67


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1}


def _read_api_key() -> str:
    if not CREDS_PATH.exists():
        raise RuntimeError(f"creds missing at {CREDS_PATH}")
    try:
        d = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"creds malformed: {e}")
    key = (d.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("api_key not in creds file")
    return key


def _fetch_me(timeout: int = 10) -> dict[str, Any]:
    """GET /tagclaw/me. Returns the parsed JSON. Raises on network error."""
    key = _read_api_key()
    url = f"{TAGCLAW_API_BASE}{ME_PATH}"
    # WAF in front of bsc-api.tagai.fun rejects requests with the default
    # ``Python-urllib/...`` UA (HTTP 403). curl-style UA passes; mirror it.
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "openclaw-bookmarker/1.0 (https://tagclaw.com)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"/tagclaw/me failed: {e}")
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"/tagclaw/me returned non-JSON: {e}")


def read_balance(force_refresh: bool = False) -> dict[str, Any]:
    """Return the current OP/VP snapshot. Refreshes from /tagclaw/me if the
    cached snapshot is older than REFRESH_THRESHOLD_SECONDS. Persists.

    Returns dict with at minimum:
      {
        "op": float, "vp": float,
        "fetched_at": "<iso utc>",
        "status": "active|...",
        "agent_id": "...", "username": "..."
      }
    """
    state = _read_state()
    snap = state.get("snapshot") or {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not force_refresh and snap.get("fetched_at"):
        try:
            last = datetime.fromisoformat(snap["fetched_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age < REFRESH_THRESHOLD_SECONDS:
                return snap
        except Exception:
            pass
    try:
        body = _fetch_me()
    except Exception as e:
        # Don't tank callers — return stale snapshot with an error annotation.
        if snap:
            return {**snap, "stale": True, "error": str(e)}
        raise
    agent = (body.get("agent") or {}) if isinstance(body, dict) else {}
    snap = {
        "op": float(agent.get("op") or 0),
        "vp": float(agent.get("vp") or 0),
        "status": agent.get("status") or "?",
        "agent_id": agent.get("agentId") or "",
        "username": agent.get("username") or "",
        "fetched_at": now_iso,
    }
    state["snapshot"] = snap
    state["last_refresh_at"] = now_iso
    _atomic_write_json(STATE_PATH, state)
    return snap


def record_consumption(action: str, op_used: float | None = None,
                        vp_used: float | None = None,
                        note: str = "") -> dict[str, Any]:
    """Append an entry to today's consumption ledger. Returns updated daily totals.

    Most callers should pass just ``action`` and let the cost table fill in
    op_used / vp_used. Explicit overrides are for actions where the API
    returned a different cost than the nominal.
    """
    state = _read_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = state.setdefault("daily_consumption", {})
    today_rec = days.setdefault(today, {
        "actions": [],
        "op_consumed": 0.0,
        "vp_consumed": 0.0,
    })
    cost = ACTION_COSTS.get(action, {})
    if op_used is None:
        op_used = float(cost.get("op", 0))
    if vp_used is None:
        vp_used = float(cost.get("vp", 0))
    today_rec["actions"].append({
        "action": action,
        "op": op_used,
        "vp": vp_used,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": note,
    })
    today_rec["op_consumed"] = float(today_rec["op_consumed"]) + op_used
    today_rec["vp_consumed"] = float(today_rec["vp_consumed"]) + vp_used
    # Keep only last 30 days.
    cutoff = sorted(days.keys())[-30:]
    state["daily_consumption"] = {k: v for k, v in days.items() if k in cutoff}
    _atomic_write_json(STATE_PATH, state)
    return today_rec


def daily_consumed(date: str | None = None) -> dict[str, float]:
    state = _read_state()
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rec = (state.get("daily_consumption") or {}).get(date) or {}
    return {
        "date": date,
        "op_consumed": float(rec.get("op_consumed") or 0),
        "vp_consumed": float(rec.get("vp_consumed") or 0),
        "action_count": len(rec.get("actions") or []),
    }


def can_afford(action: str, op_multiplier: float = 1.0,
                vp_multiplier: float = 1.0) -> tuple[bool, str]:
    """Return (ok, reason) — whether the live balance covers one ``action``.

    Optional multipliers let callers ask "could I afford 5 of these in a row?"
    """
    cost = ACTION_COSTS.get(action)
    if not cost:
        return (False, f"unknown action: {action}")
    snap = read_balance()
    op_need = cost["op"] * op_multiplier
    vp_need = cost["vp"] * vp_multiplier
    if snap.get("op", 0) < op_need:
        return (False, f"op {snap.get('op',0):.1f} < required {op_need:.1f}")
    if snap.get("vp", 0) < vp_need:
        return (False, f"vp {snap.get('vp',0):.1f} < required {vp_need:.1f}")
    return (True, "ok")


def summarize() -> dict[str, Any]:
    """One-shot snapshot of live balance + today's consumption + budget."""
    snap = read_balance()
    today = daily_consumed()
    return {
        "balance": snap,
        "today": today,
        "daily_op_target": DAILY_OP_TARGET,
        "daily_vp_target": DAILY_VP_TARGET,
        "op_pct_of_daily_target": (today["op_consumed"] / DAILY_OP_TARGET) * 100,
        "vp_pct_of_daily_target": (today["vp_consumed"] / DAILY_VP_TARGET) * 100,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", action="store_true", help="Force refresh from /tagclaw/me")
    args = p.parse_args()
    if args.refresh:
        read_balance(force_refresh=True)
    s = summarize()
    print(json.dumps(s, ensure_ascii=False, indent=2))
