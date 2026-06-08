#!/usr/bin/env python3
"""Shared runtime utility functions extracted from V1 publish_runtime for V2 reuse."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def normalize_status(value: str | None, default: str = "stale") -> str:
    if value in {"ok", "partial", "blocked", "stale"}:
        return value
    if value in {"error", "failed", "fail"}:
        return "blocked"
    return default


def normalize_optional_exec_status(value: str | None) -> str | None:
    return value if value in {"ok", "partial", "blocked", "stale"} else None


def path_ref(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)



BALANCE_RE = re.compile(r"^-\s+([A-Za-z0-9_]+):\s+`?([^`\n]+)`?\s*$", re.MULTILINE)
WALLET_RE = re.compile(r"^-\s+Wallet:\s+`([^`]+)`", re.MULTILINE)
REWARD_LINE_RE = re.compile(
    r"^\s*-\s+(?P<tick>[A-Za-z0-9_]+):\s+claimable\s+`(?P<amount>[^`]+)`\s+\|\s+price_usd\s+`(?P<price>[^`]+)`\s+\|\s+reward_value_usd\s+`(?P<usd>[^`]+)`\s+\|\s+(?P<action>[^\n]+)$",
    re.MULTILINE,
)


def parse_markdown_balances(text: str | None) -> tuple[str | None, dict[str, str], dict[str, str]]:
    wallet = None
    balances: dict[str, str] = {}
    rewards: dict[str, str] = {}
    if not text:
        return wallet, balances, rewards
    wallet_match = WALLET_RE.search(text)
    if wallet_match:
        wallet = wallet_match.group(1)

    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Balances"):
            section = "balances"
            continue
        if stripped.startswith("## Claimable rewards snapshot"):
            section = "rewards"
            continue
        if stripped.startswith("## "):
            section = None
            continue
        m = re.match(r"^-\s+([A-Za-z0-9_]+):\s+`?([^`]+?)`?\s*$", stripped)
        if not m or not section:
            continue
        key, val = m.group(1), m.group(2)
        if section == "balances":
            balances[key] = val
        elif section == "rewards":
            rewards[key] = val
    return wallet, balances, rewards



def analyze_social_action_selection(
    drafts_obj: dict[str, Any] | None,
    max_actions: int,
    recently_executed: set[str] | None = None,
    recent_noop_curates: set[str] | None = None,
    mix_order: list[str] | None = None,
    max_per_type: dict[str, int] | None = None,
) -> dict[str, Any]:
    drafts = drafts_obj.get("drafts") if isinstance(drafts_obj, dict) else None
    if not isinstance(drafts, list):
        return {
            "actions": [],
            "draft_count": 0,
            "selection_reason": "drafts_missing",
            "suppressed": {},
            "selected_ids": [],
        }

    recent = recently_executed or set()
    recent_noops = recent_noop_curates or set()
    order = mix_order or ["post", "reply", "curate", "like"]
    per_type_caps = max_per_type or {"post": 1, "reply": 1, "curate": 1, "like": 1}

    ranked_drafts = [d for d in drafts if isinstance(d, dict)]
    ranked_drafts.sort(key=social_action_sort_key, reverse=True)

    selected_target_keys: set[str] = set()
    selected_ids: set[str] = set()
    type_counts: dict[str, int] = {k: 0 for k in per_type_caps}
    actions: list[dict[str, Any]] = []
    suppressed = {
        "recent_target": 0,
        "recent_noop_curate": 0,
        "duplicate_target": 0,
        "type_cap": 0,
        "missing_id": 0,
        "duplicate_id": 0,
    }

    for desired_type in order:
        if len(actions) >= max_actions:
            break
        for draft in ranked_drafts:
            draft_id = draft.get("id")
            draft_type = draft.get("type")
            if draft_type != desired_type:
                continue
            if not draft_id:
                suppressed["missing_id"] += 1
                continue
            if draft_id in selected_ids:
                suppressed["duplicate_id"] += 1
                continue
            target_key = draft_target_key(draft)
            if target_key and target_key in recent:
                suppressed["recent_target"] += 1
                continue
            if draft_type == "curate" and target_key and target_key in recent_noops:
                suppressed["recent_noop_curate"] += 1
                continue
            if target_key and target_key in selected_target_keys:
                suppressed["duplicate_target"] += 1
                continue
            if type_counts.get(draft_type, 0) >= int(per_type_caps.get(draft_type, 1)):
                suppressed["type_cap"] += 1
                continue
            actions.append({
                "type": draft_type,
                "count": 1,
                "content_candidate_ref": None,
                "draft_ref": f"runtime/bookmarker/social-drafts.json#{draft_id}",
                "reply_target_ref": None,
                "priority": draft.get("priority"),
                # Propagate metadata so dedup chain works even if deref_draft fails later.
                "draft_type": draft.get("draft_type") or "",
                "source": draft.get("source") or "",
            })
            selected_ids.add(draft_id)
            type_counts[draft_type] = type_counts.get(draft_type, 0) + 1
            if target_key:
                selected_target_keys.add(target_key)
            if len(actions) >= max_actions:
                break

    if actions:
        selection_reason = "actions_selected"
    elif not ranked_drafts:
        selection_reason = "no_drafts"
    elif suppressed["recent_target"] or suppressed["recent_noop_curate"]:
        selection_reason = "cooldown_or_policy_suppressed"
    elif suppressed["duplicate_target"]:
        selection_reason = "duplicate_target_suppressed"
    elif suppressed["type_cap"]:
        selection_reason = "type_cap_suppressed"
    else:
        selection_reason = "selection_constraints_suppressed"

    return {
        "actions": actions,
        "draft_count": len(ranked_drafts),
        "selection_reason": selection_reason,
        "suppressed": {k: v for k, v in suppressed.items() if v},
        "selected_ids": sorted(selected_ids),
    }



def build_social_actions_from_drafts(
    drafts_obj: dict[str, Any] | None,
    max_actions: int,
    recently_executed: set[str] | None = None,
    recent_noop_curates: set[str] | None = None,
    mix_order: list[str] | None = None,
    max_per_type: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    return analyze_social_action_selection(
        drafts_obj,
        max_actions,
        recently_executed,
        recent_noop_curates,
        mix_order,
        max_per_type,
    )["actions"]



MODE_RANK = {
    "blocked-runtime": 0,
    "conservative": 1,
    "vp-flush": 1,
    "vp-drain": 1,
    "active": 2,
    "mid-active": 3,
    "super-active": 4,
}

# P0 daily resource targets (OP = operation points, VP = vote power)
DAILY_OP_MIN = 670.0
DAILY_VP_MIN = 67.0

# OP cost per action type (mirrors execute_social_intent_v2.OP_COST)
_OP_COST_PER_TYPE: dict[str, float] = {
    "post": 200.0,
    "reply": 50.0,
    "like": 3.0,
    "curate": 3.0,
    "retweet": 4.0,
}


def compute_daily_consumption(runtime_path: "Path") -> dict:
    """FIX-1: Compute today's consumed OP/VP from social-history.json.

    Returns daily_op_consumed, daily_vp_consumed, resource_floor_met, and pacing
    signals.  Used by run_main_runtime.py to detect when the P0 daily floor
    (OP>=670, VP>=67) has not been met and force-mode accordingly.
    """
    from pathlib import Path as _Path
    history = read_json(_Path(runtime_path) / "shared" / "social-history.json") or {}
    items = history.get("items") or []
    today_str = datetime.now(timezone.utc).date().isoformat()

    daily_op = 0.0
    daily_vp = 0.0

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("result_status") != "ok":
            continue
        executed_at_str = item.get("executed_at") or ""
        # Fast-path: ISO date prefix check (UTC date)
        if not executed_at_str.startswith(today_str):
            dt = parse_dt(executed_at_str)
            if dt is None or dt.astimezone(timezone.utc).date().isoformat() != today_str:
                continue
        action_type = item.get("type") or ""
        daily_op += _OP_COST_PER_TYPE.get(action_type, 0.0)
        if action_type in ("curate", "like"):
            daily_vp += float(item.get("vp") or item.get("vp_spent") or 0)

    op_floor_met = daily_op >= DAILY_OP_MIN
    vp_floor_met = daily_vp >= DAILY_VP_MIN
    resource_floor_met = op_floor_met and vp_floor_met

    # Pacing: how much is needed per remaining 10-min heartbeat cycle to hit floor
    now_dt = datetime.now(timezone.utc)
    start_of_day = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes_elapsed = max(1.0, (now_dt - start_of_day).total_seconds() / 60.0)
    minutes_remaining = max(1.0, 1440.0 - minutes_elapsed)
    cycles_remaining = max(1.0, minutes_remaining / 10.0)
    op_needed_per_cycle = max(0.0, DAILY_OP_MIN - daily_op) / cycles_remaining
    vp_needed_per_cycle = max(0.0, DAILY_VP_MIN - daily_vp) / cycles_remaining

    return {
        "daily_op_consumed": round(daily_op, 2),
        "daily_vp_consumed": round(daily_vp, 2),
        "daily_op_target": DAILY_OP_MIN,
        "daily_vp_target": DAILY_VP_MIN,
        "op_floor_met": op_floor_met,
        "vp_floor_met": vp_floor_met,
        "resource_floor_met": resource_floor_met,
        "op_needed_per_cycle": round(op_needed_per_cycle, 2),
        "vp_needed_per_cycle": round(vp_needed_per_cycle, 2),
        "cycles_remaining_today": round(cycles_remaining, 1),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def compute_main_mode(op: float | None, vp: float | None, *, resource_floor_unmet: bool = False) -> str:
    """FIX-5: compute mode with resource floor override.

    When resource_floor_unmet=True and the normal heuristic would return
    'conservative', the mode is forced up to 'vp-flush' so social execution
    is always enabled when daily P0 targets (OP>=670, VP>=67) are behind pace.
    """
    if op is None or vp is None:
        return "blocked-runtime"
    if op > 1200 and vp > 150:
        return "super-active"
    if op > 1000 and vp > 120:
        return "mid-active"
    if op > 800 and vp > 100:
        return "active"
    # vp-flush: 低OP + 高VP → 策展优先，全力消耗VP避免浪费
    # FIX-5: also trigger when daily resource floor is unmet (catch-up mode)
    if op <= 800 and vp >= 150:
        return "vp-flush"
    if resource_floor_unmet and op <= 800:
        return "vp-flush"
    # vp-drain: VP高但OP不足以触发活跃模式时，策展为主但可发1帖
    if vp >= 180 and op >= 100 and op < 800:
        return "vp-drain"
    if vp >= 150 and op < 200:
        return "vp-drain"
    # FIX-2/5: when resource floor is unmet, never stay in conservative (death loop)
    if resource_floor_unmet:
        return "active"
    return "conservative"


def gate_allows_mode(current_mode: str, required_mode: str) -> bool:
    return MODE_RANK.get(current_mode, 0) >= MODE_RANK.get(required_mode, 0)


def draft_target_key(draft: dict[str, Any]) -> str | None:
    if draft.get("target_key"):
        return str(draft.get("target_key"))
    draft_type = draft.get("type")
    if draft_type in {"reply", "curate", "like"}:
        tweet_id = draft.get("tweetId") or draft.get("tweet_id")
        return f"tagclaw:{tweet_id}" if tweet_id else None
    if draft_type == "post":
        source_tweet_id = draft.get("source_tweet_id")
        return f"x:{source_tweet_id}" if source_tweet_id else None
    return None


def recent_executed_target_keys(history_obj: dict[str, Any] | None, cooldown_hours: int) -> set[str]:
    out: set[str] = set()
    if not isinstance(history_obj, dict):
        return out
    now_dt = datetime.now(timezone.utc).astimezone()
    cutoff = now_dt - timedelta(hours=max(cooldown_hours, 0))
    for item in history_obj.get("items") or []:
        if not isinstance(item, dict):
            continue
        result_status = item.get("result_status", "ok")
        if result_status != "ok":
            continue
        key = item.get("target_key")
        executed_at = parse_dt(item.get("executed_at"))
        if not key or not executed_at:
            continue
        if executed_at >= cutoff:
            out.add(str(key))
    return out


def recent_noop_curate_target_keys(history_obj: dict[str, Any] | None, cooldown_hours: int) -> set[str]:
    out: set[str] = set()
    if not isinstance(history_obj, dict):
        return out
    now_dt = datetime.now(timezone.utc).astimezone()
    cutoff = now_dt - timedelta(hours=max(cooldown_hours, 0))
    for item in history_obj.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("result_status") != "noop" or item.get("type") != "curate":
            continue
        key = item.get("target_key")
        executed_at = parse_dt(item.get("executed_at"))
        if not key or not executed_at:
            continue
        if executed_at >= cutoff:
            out.add(str(key))
    return out


def social_action_sort_key(draft: dict[str, Any]) -> tuple[int, int]:
    type_rank = {"post": 5, "reply": 4, "curate": 3, "like": 2, "hold": 1}
    return (int(draft.get("priority") or 0), type_rank.get(str(draft.get("type")), 0))


# ── Provenance Sidecar ──

SIDECAR_SCHEMA = "provenance-sidecar-v1"


def write_provenance_sidecar(
    artifact_path: Path,
    producer: str,
    *,
    source_refs: list[str] | None = None,
    schema_version: str | None = None,
    facts: dict[str, Any] | None = None,
    root: Path | None = None,
) -> Path:
    """Write a compact provenance sidecar JSON next to a derived artifact.

    The sidecar is written to ``<artifact_path>.provenance.json`` using atomic
    write so concurrent readers never see a partial file.

    Parameters
    ----------
    artifact_path:
        Absolute or workspace-relative path to the derived artifact.
    producer:
        Script/module that produced the artifact (e.g. ``build_wiki_execution_brief_v1``).
    source_refs:
        List of source paths/identifiers the artifact was derived from.
    schema_version:
        Version string of the artifact's own schema (e.g. ``wiki-execution-brief-v1``).
    facts:
        Optional dict of compact intermediate facts worth preserving.
    root:
        Workspace root for computing relative paths. Defaults to the
        parent-of-parent of this script.

    Returns
    -------
    Path to the written sidecar file.
    """
    ws = root or Path(__file__).resolve().parent.parent
    ap = Path(artifact_path)
    sidecar_path = ap.parent / f"{ap.name}.provenance.json"
    sidecar: dict[str, Any] = {
        "schema": SIDECAR_SCHEMA,
        "artifact_ref": path_ref(ap, ws),
        "generated_at": now_iso(),
        "producer": producer,
    }
    if schema_version:
        sidecar["artifact_schema"] = schema_version
    if source_refs:
        sidecar["source_refs"] = source_refs
    if facts:
        sidecar["facts"] = facts
    atomic_write_json(sidecar_path, sidecar)
    return sidecar_path


# ── Wiki Events Ledger ──

WIKI_EVENTS_PATH = Path(os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace")) / "runtime" / "shared" / "wiki-events.jsonl"


def append_wiki_event(
    event_type: str,
    producer: str,
    *,
    entity: str | None = None,
    artifact: str | None = None,
    status: str = "ok",
    summary: str = "",
    detail: dict[str, Any] | None = None,
    ledger_path: Path | None = None,
) -> None:
    """Append a single structured event to the wiki events ledger (JSONL).

    Safe: failures are swallowed to avoid disrupting the calling pipeline.
    Uses file-append mode — each write is a single line, safe for concurrent readers.
    """
    path = ledger_path or WIKI_EVENTS_PATH
    event = {
        "ts": now_iso(),
        "event_type": event_type,
        "producer": producer,
    }
    if entity:
        event["entity"] = entity
    if artifact:
        event["artifact"] = artifact
    event["status"] = status
    if summary:
        event["summary"] = summary
    if detail:
        event["detail"] = detail
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # never disrupt calling pipeline

