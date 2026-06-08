#!/usr/bin/env python3
"""wiki_priors.py — knowledge-guided priors for the strategy bandit.

Turns blind uniform-random arm selection into wiki-informed weighted choice:
  - target_agents  ← top author affinity (engage who actually resonates, not a
                     stale hardcoded list the owner has zero affinity with)
  - arm-value bias ← past decision outcomes (favor what worked; down-weight
                     known-bad), from the decision-memory ledger.

Every reader is guarded: missing/empty wiki data degrades to NEUTRAL (uniform),
so the bandit never breaks or skews when an artifact is absent. Read-only — this
module never writes wiki/runtime state.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any, Iterable

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parent.parent)
RUNTIME = WORKSPACE / "runtime"
AFFINITY_PATH = RUNTIME / "bookmarker" / "x-reco-author-affinity-180d.json"
DECISION_INDEX_PATH = RUNTIME / "shared" / "decision-index.json"

_OK_OUTCOMES = {"ok", "success", "succeeded"}
_BAD_OUTCOMES = {"failed", "skipped", "error", "blocked"}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def top_affinity_agents(n: int = 3, fallback: Iterable[str] | None = None) -> list[str]:
    """Top-N X authors by combined_affinity (180d). Falls back to `fallback`
    (or []) if the affinity artifact is missing/empty."""
    fb = list(fallback or [])
    d = _read_json(AFFINITY_PATH)
    authors = (d or {}).get("authors") if isinstance(d, dict) else None
    if not isinstance(authors, dict) or not authors:
        return fb
    ranked = sorted(
        authors.items(),
        key=lambda kv: -float((kv[1] or {}).get("combined_affinity") or 0.0),
    )
    out = [name for name, _ in ranked[:n] if name]
    return out or fb


def decision_outcome_weights(
    values: list[str], *, agent: str | None = None, kind: str | None = None
) -> dict[str, float]:
    """Per-value weight from past decision outcomes (ok → up, bad → down).
    Returns {value: weight in [0.25, 2.0]}; uniform 1.0 where there's no signal.
    Matching is substring-on-action (arm values rarely equal decision actions
    verbatim), so this is a SOFT bias, not a hard filter. Graceful on no data."""
    base = {v: 1.0 for v in values}
    d = _read_json(DECISION_INDEX_PATH)
    decisions = (d or {}).get("decisions") if isinstance(d, dict) else None
    if not isinstance(decisions, list) or not decisions:
        return base
    for v in values:
        ok = bad = 0
        vl = str(v).lower()
        for dec in decisions:
            if not isinstance(dec, dict):
                continue
            if agent and dec.get("agent") != agent:
                continue
            if kind and dec.get("kind") != kind:
                continue
            if vl in str(dec.get("action") or "").lower():
                o = str(dec.get("outcome") or "").lower()
                if o in _OK_OUTCOMES:
                    ok += 1
                elif o in _BAD_OUTCOMES:
                    bad += 1
        if ok or bad:
            base[v] = max(0.25, min(2.0, 1.0 + 0.5 * (ok - bad)))
    return base


def app_feedback_index() -> dict[str, dict]:
    """#5: read back the unified per-app reward ledger (kind=app-feedback rows that
    build_decisions_synthesis_v1.ingest_app_feedback writes). Returns
    {app: {metric, value, target, outcome, decided_at}} for the LATEST row per app —
    so any consumer (a dashboard, a future per-app arm, a health check) has one place
    to read 'how is each app doing'. Graceful: {} when there's no decision-index."""
    d = _read_json(DECISION_INDEX_PATH)
    decisions = (d or {}).get("decisions") if isinstance(d, dict) else None
    if not isinstance(decisions, list):
        return {}
    out: dict[str, dict] = {}
    for dec in decisions:  # decisions are newest-first; keep first seen per app
        if not isinstance(dec, dict) or dec.get("kind") != "app-feedback":
            continue
        mc = dec.get("metric_context") or {}
        app = mc.get("app")
        if not app or app in out:
            continue
        out[app] = {"metric": mc.get("metric"), "value": mc.get("value"),
                    "target": mc.get("target"), "outcome": dec.get("outcome"),
                    "decided_at": dec.get("decided_at")}
    return out


def weighted_choice(values: list[str], weights: dict[str, float]) -> str:
    """Weighted random pick; uniform if weights are absent/degenerate."""
    if not values:
        raise ValueError("weighted_choice: empty values")
    ws = [max(0.0, float(weights.get(v, 1.0))) for v in values]
    if sum(ws) <= 0:
        return random.choice(values)
    return random.choices(values, weights=ws, k=1)[0]


if __name__ == "__main__":  # quick manual check
    print("top_affinity_agents(3):", top_affinity_agents(3, fallback=["foxclaw"]))
    print("credit weights:", decision_outcome_weights(["hold", "buy_small"], agent="trader"))
    print("engagement weights:", decision_outcome_weights(["none", "reply_to_top_agents"], agent="bookmarker"))
