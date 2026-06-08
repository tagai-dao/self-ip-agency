#!/usr/bin/env python3
"""pick_hourly_action_v1.py — choose ONE action to execute this hour.

Plan C foundation. Runs hourly :40 (before bookmarker-social-execution :45).
Reads ``runtime/bookmarker/daily-action-budget.json`` (per-day quotas),
picks the next action based on:

  1. Time-of-day pacing — spread actions across waking hours (08-23 CST)
     instead of dumping the whole quota at midnight.
  2. Action variety — never pick the same action type twice in a row when
     other types still have headroom.
  3. Live balance — re-check OP/VP; if balance dropped below the action's
     cost, fall back to a cheaper action or 'noop'.

Output: ``runtime/bookmarker/next-action-intent.json`` — schema:
  {
    "action": "post" | "reply" | "like" | "retweet" | "curate" | "noop",
    "reason": "...",
    "balance_at_pick": {...},
    "remaining_today": {...},
    "generated_at": "..."
  }

The executor (or a thin wrapper) reads this intent and executes ONE
action accordingly. After successful execution the executor must call
``tagclaw_budget.record_consumption(action)`` and decrement
``remaining_today[action]`` in the budget file. (We provide a helper:
``commit_picked_action`` at the bottom of this file.)

Usage:
  python3 pick_hourly_action_v1.py
  python3 pick_hourly_action_v1.py --dry-run
  python3 pick_hourly_action_v1.py --commit-action like   # mark one done
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(WORKSPACE / "scripts"))
from lib.tagclaw_budget import ACTION_COSTS, read_balance  # noqa: E402
from runtime_utils import append_wiki_event, path_ref  # noqa: E402

BUDGET_PATH = WORKSPACE / "runtime" / "bookmarker" / "daily-action-budget.json"
INTENT_PATH = WORKSPACE / "runtime" / "bookmarker" / "next-action-intent.json"

# Activity window in Asia/Shanghai. Outside this window every pick is noop.
ACTIVE_HOUR_START_CST = int(os.environ.get("PLAN_ACTIVE_HOUR_START") or 8)
ACTIVE_HOUR_END_CST = int(os.environ.get("PLAN_ACTIVE_HOUR_END") or 23)


def _now_cst() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _last_action_type(budget: dict[str, Any]) -> str | None:
    ledger = budget.get("ledger") or []
    if not ledger:
        return None
    last = ledger[-1]
    return last.get("action") if isinstance(last, dict) else None


def _pacing_pressure(remaining: dict[str, int]) -> int:
    """Total actions left to spread across remaining hours of the day."""
    return sum(int(v) for v in remaining.values() if isinstance(v, (int, float)))


def _within_active_window() -> bool:
    h = _now_cst().hour
    return ACTIVE_HOUR_START_CST <= h <= ACTIVE_HOUR_END_CST


def _hours_left_in_window() -> int:
    h = _now_cst().hour
    if h > ACTIVE_HOUR_END_CST:
        return 0
    return max(1, ACTIVE_HOUR_END_CST - h + 1)


def pick_next_action(budget: dict[str, Any], balance: dict[str, float]) -> dict[str, Any]:
    """Return the intent dict for the next action."""
    remaining = dict(budget.get("remaining_today") or {})
    total_left = _pacing_pressure(remaining)
    last_action = _last_action_type(budget)
    hours_left = _hours_left_in_window()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not _within_active_window():
        return {
            "action": "noop",
            "reason": f"outside active window ({ACTIVE_HOUR_START_CST}-{ACTIVE_HOUR_END_CST} CST)",
            "balance_at_pick": balance,
            "remaining_today": remaining,
            "generated_at": now_iso,
        }
    if total_left <= 0:
        return {
            "action": "noop",
            "reason": "daily quota fully consumed",
            "balance_at_pick": balance,
            "remaining_today": remaining,
            "generated_at": now_iso,
        }

    # Greedy priority order: post > reply > curate > retweet > like.
    # But avoid picking the same action twice in a row when alternatives
    # remain. Time-of-day pacing: if there are many actions left and few
    # hours, lean toward cheaper actions first (like/retweet) to spread.
    priority = ["post", "reply", "curate", "retweet", "like"]

    # Compute action densities — leftover per hour for each.
    densities = {
        a: (remaining.get(a, 0) / max(hours_left, 1))
        for a in priority
    }
    # Sort priority: rotate so action that's most overdue (highest density)
    # comes first, EXCEPT we still want post/reply to have priority when
    # available since they're the substantive content.
    def _rank(action: str) -> tuple[int, float]:
        base = priority.index(action)
        return (base, -densities.get(action, 0))
    ordered = sorted(priority, key=_rank)

    for candidate in ordered:
        qty_left = int(remaining.get(candidate, 0) or 0)
        if qty_left <= 0:
            continue
        if candidate == last_action and len([a for a in remaining if (remaining.get(a) or 0) > 0]) > 1:
            continue  # avoid back-to-back same type when alternatives exist
        cost = ACTION_COSTS.get(candidate, {"op": 0, "vp": 0})
        op_ok = float(balance.get("op") or 0) >= cost["op"]
        vp_ok = float(balance.get("vp") or 0) >= cost["vp"]
        if not (op_ok and vp_ok):
            continue
        return {
            "action": candidate,
            "reason": f"quota left={qty_left}, density={densities.get(candidate,0):.2f}/h, last={last_action!r}",
            "balance_at_pick": balance,
            "remaining_today": remaining,
            "generated_at": now_iso,
        }

    return {
        "action": "noop",
        "reason": "no action can be funded by current balance",
        "balance_at_pick": balance,
        "remaining_today": remaining,
        "generated_at": now_iso,
    }


def commit_picked_action(action: str, note: str = "") -> dict[str, Any]:
    """Decrement ``remaining_today[action]`` and append a ledger entry.

    Called by the executor AFTER it successfully fires the action.
    Idempotent in the sense that the executor must track its own
    success/failure; this function trusts the caller.
    """
    budget = _read_json(BUDGET_PATH) or {}
    rem = budget.setdefault("remaining_today", {})
    if action in rem:
        rem[action] = max(0, int(rem[action]) - 1)
    ledger = budget.setdefault("ledger", [])
    ledger.append({
        "action": action,
        "committed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": note,
    })
    _atomic_write_json(BUDGET_PATH, budget)
    return budget


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--commit-action", help="Mark one action as completed (for testing/manual fixup)")
    args = p.parse_args()

    if args.commit_action:
        budget = commit_picked_action(args.commit_action, note="manual --commit-action")
        print(f"[picker] committed {args.commit_action}; remaining={budget.get('remaining_today')}")
        return 0

    budget = _read_json(BUDGET_PATH)
    if not budget:
        print("[picker] no daily-action-budget.json — run plan_daily_actions_v1.py first", file=sys.stderr)
        return 1

    balance = read_balance()
    intent = pick_next_action(budget, balance)
    print(json.dumps(intent, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    _atomic_write_json(INTENT_PATH, intent)
    try:
        append_wiki_event(
            event_type="hourly_action_intent",
            producer="pick_hourly_action_v1",
            artifact=path_ref(INTENT_PATH, WORKSPACE),
            status="ok",
            summary=f"action={intent['action']}",
            detail={
                "action": intent["action"],
                "reason": intent["reason"],
                "remaining": intent["remaining_today"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[picker] event emit failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
