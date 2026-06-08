#!/usr/bin/env python3
"""plan_daily_actions_v1.py — allocate the day's action budget.

Plan C foundation. Runs once daily (cron 06:00) and writes
``runtime/bookmarker/daily-action-budget.json`` with target counts for each
action type. The hourly action picker reads this file and tracks which
actions are still owed by end-of-day.

Default mix (balanced for substance + interaction, ≈667 OP + ≈70 VP):

  | action  | qty | OP/each | total OP | VP/each | total VP |
  |---------|-----|---------|----------|---------|----------|
  | post    | 2   | 200     | 400      | 0       | 0        |
  | reply   | 5   | 50      | 250      | 0       | 0        |
  | like    | 8   | 3       | 24       | 0       | 0        |
  | retweet | 3   | 3       | 9        | 0       | 0        |
  | curate  | 10  | 0       | 0        | 7       | 70       |
  |---------|-----|---------|----------|---------|----------|
  | TOTAL   | 28  |         | 683      |         | 70       |

This sits just over 667 OP / 67 VP — intentional small buffer to absorb
the case where a curate vote weights >7 VP.

If live balance falls below a quota line, that quota gets capped to what
the balance can fund. The next day's regen restores headroom.

Usage:
  python3 plan_daily_actions_v1.py
  python3 plan_daily_actions_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(WORKSPACE / "scripts"))
from lib.tagclaw_budget import (  # noqa: E402
    ACTION_COSTS, DAILY_OP_TARGET, DAILY_VP_TARGET, read_balance,
)
from runtime_utils import append_wiki_event, path_ref  # noqa: E402

BUDGET_PATH = WORKSPACE / "runtime" / "bookmarker" / "daily-action-budget.json"

# Default mix, tunable via env vars (e.g. PLAN_DAILY_POSTS=3).
DEFAULT_MIX: dict[str, int] = {
    "post":    int(os.environ.get("PLAN_DAILY_POSTS")    or 2),
    "reply":   int(os.environ.get("PLAN_DAILY_REPLIES")  or 5),
    "like":    int(os.environ.get("PLAN_DAILY_LIKES")    or 8),
    "retweet": int(os.environ.get("PLAN_DAILY_RETWEETS") or 3),
    "curate":  int(os.environ.get("PLAN_DAILY_CURATES")  or 10),
}


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)


def _planned_cost(mix: dict[str, int]) -> dict[str, float]:
    """Sum OP / VP required if the full mix executes."""
    total_op = sum(ACTION_COSTS[a]["op"] * n for a, n in mix.items() if a in ACTION_COSTS)
    total_vp = sum(ACTION_COSTS[a]["vp"] * n for a, n in mix.items() if a in ACTION_COSTS)
    return {"op": total_op, "vp": total_vp}


def _cap_to_balance(mix: dict[str, int], balance: dict[str, float]) -> tuple[dict[str, int], list[str]]:
    """If live balance can't cover the full mix, scale down high-OP actions
    first (post → reply → retweet → like) and high-VP actions (curate).
    Returns (capped_mix, [notes])."""
    capped = dict(mix)
    notes: list[str] = []
    # OP capping: shrink post quota first if OP < required.
    target_op = _planned_cost(capped)["op"]
    available_op = float(balance.get("op") or 0)
    while target_op > available_op and capped.get("post", 0) > 0:
        capped["post"] -= 1
        notes.append("OP shortfall — reduced 'post' quota")
        target_op = _planned_cost(capped)["op"]
    while target_op > available_op and capped.get("reply", 0) > 0:
        capped["reply"] -= 1
        notes.append("OP shortfall — reduced 'reply' quota")
        target_op = _planned_cost(capped)["op"]
    # VP capping for curate.
    target_vp = _planned_cost(capped)["vp"]
    available_vp = float(balance.get("vp") or 0)
    while target_vp > available_vp and capped.get("curate", 0) > 0:
        capped["curate"] -= 1
        notes.append("VP shortfall — reduced 'curate' quota")
        target_vp = _planned_cost(capped)["vp"]
    return capped, notes


def build_budget(force_refresh: bool = False) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    balance = read_balance(force_refresh=force_refresh)
    planned_mix = dict(DEFAULT_MIX)
    capped_mix, notes = _cap_to_balance(planned_mix, balance)
    cost = _planned_cost(capped_mix)
    return {
        "schema": "bookmarker.daily-action-budget.v1",
        "date": today,
        "generated_at": now_iso,
        "balance_snapshot": balance,
        "planned_mix": planned_mix,
        "approved_mix": capped_mix,
        "approved_cost": cost,
        "remaining_today": dict(capped_mix),  # picker decrements this
        "daily_op_target": DAILY_OP_TARGET,
        "daily_vp_target": DAILY_VP_TARGET,
        "cap_notes": notes,
        "ledger": [],  # picker appends entries as it commits actions
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    budget = build_budget(force_refresh=True)
    print(json.dumps({
        "date": budget["date"],
        "balance": budget["balance_snapshot"],
        "approved_mix": budget["approved_mix"],
        "approved_cost": budget["approved_cost"],
        "cap_notes": budget["cap_notes"],
    }, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    _atomic_write_json(BUDGET_PATH, budget)
    try:
        append_wiki_event(
            event_type="daily_action_budget_built",
            producer="plan_daily_actions_v1",
            artifact=path_ref(BUDGET_PATH, WORKSPACE),
            status="ok",
            summary=(
                f"approved op={budget['approved_cost']['op']:.0f}/"
                f"{DAILY_OP_TARGET} vp={budget['approved_cost']['vp']:.0f}/"
                f"{DAILY_VP_TARGET}"
            ),
            detail={
                "approved_mix": budget["approved_mix"],
                "balance": {
                    "op": budget["balance_snapshot"].get("op"),
                    "vp": budget["balance_snapshot"].get("vp"),
                },
                "cap_notes": budget["cap_notes"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-budget] event emit failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
