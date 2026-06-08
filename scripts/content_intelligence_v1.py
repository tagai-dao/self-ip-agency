#!/usr/bin/env python3
"""content_intelligence_v1.py — the shared content-intelligence layer.

#3 (2026-06-08): five apps (autonomous-post, X-Reco digest, social-trade brief,
brief->TagAI import, EN->CN translation) all do the SAME thing — *select content*
(concepts / authors / tweets / articles) to act on — but each reimplemented its own
scoring ad-hoc (build_topic_candidates_v1 scored concepts; translate_en_bookmarks_v1
re-derived author-affinity; the digest tuned weights in isolation). That duplication
means the "what's worth attention" definition drifted per app and could only be tuned
in one place at a time.

This module is the single home for the shared scoring primitives, fused on the report's
formula: **热度(heat) × 深度(concept depth) × 0xNought-affinity × 新颖度(novelty)**.
It exposes them as importable functions AND emits a language-agnostic artifact
`runtime/shared/content-intelligence.json` so apps in OTHER workspaces (the bookmarker
translation pipeline) consume one shared index without fragile cross-workspace imports.

Primitives (all read-only, all guarded — missing source → neutral/empty):
  - author_affinity_index()   handle -> {affinity, affinity_norm, strong, last_interaction_at}
                              the canonical 0xNought-engagement index (from x-reco-author-affinity-180d)
  - community_heat_index()    concept -> {trend_score, rising}  (from community-heat.json via registry)
  - xreco_concept_hits()      concept -> hot-reco mention count  (from x-reco-ranked.json)
  - brief_cashtag_concepts()  set of concepts hot in the trader social brief
  - novelty_ids()             14-day exec-log ids (a content id IN this set is NOT novel)
  - concept_scores()          concept -> {score, signals{...}}  (the generalized topic ranker)

Consumers: build_topic_candidates_v1.py (①), translate_en_bookmarks_v1.py +
translation_usefulness_eval_v1.py (⑤), import_brief_tweets_to_tagai (④). The shared
WEIGHTS dict is the one tuning point for all of them.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parent.parent)
WIKI = WORKSPACE / "wiki"
CONCEPTS_DIR = WIKI / "concepts"
SHARED = WORKSPACE / "runtime" / "shared"
TRADER = WORKSPACE / "runtime" / "trader"
BOOKM = WORKSPACE / "runtime" / "bookmarker"
AUTHOR_AFFINITY_PATH = BOOKM / "x-reco-author-affinity-180d.json"
OUTPUT = SHARED / "content-intelligence.json"

# The shared scoring formula — ONE tuning point for all five apps.
WEIGHTS = {
    "heat": 2.0,            # community-heat trend strength
    "heat_rising": 0.5,     # bonus when a concept is rising
    "xreco_hit": 0.3,       # per hot X-Reco item mentioning the concept (capped)
    "xreco_hit_cap": 1.5,
    "brief_cashtag": 0.7,   # concept is a hot cashtag in the trade brief
    "recent_signals": 0.6,  # concept page has fresh inflow (alive)
    "base": 1.0,
}

try:
    from wiki_registry import resolve_concept  # type: ignore
except Exception:
    def resolve_concept(name: str) -> str:  # type: ignore
        return name

try:
    from execute_planned_action_v1 import _exec_log_recent_ids  # type: ignore
except Exception:
    def _exec_log_recent_ids(days: int = 14) -> set:  # type: ignore
        return set()


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def concepts() -> list[str]:
    if CONCEPTS_DIR.exists():
        return sorted(p.stem for p in CONCEPTS_DIR.glob("*.md")
                      if not p.name.lower().startswith("readme"))
    return []


# ── shared primitives ──────────────────────────────────────────────

def author_affinity_index() -> dict[str, dict]:
    """handle(lowercased, no @) -> {affinity, affinity_norm, strong, last_interaction_at}.
    The canonical 0xNought-engagement signal, normalized once here so every app ranks
    authors on the same scale."""
    d = _read_json(AUTHOR_AFFINITY_PATH) or {}
    raw = {}
    for h, v in (d.get("authors") or {}).items():
        if not isinstance(v, dict):
            continue
        aff = v.get("combined_affinity")
        if aff is None:
            aff = v.get("weighted_affinity_180d", 0)
        raw[h.lstrip("@").lower()] = {
            "affinity": float(aff or 0),
            "strong": bool(v.get("strong_signal_creator")),
            "last_interaction_at": v.get("last_interaction_at"),
        }
    max_aff = max((a["affinity"] for a in raw.values()), default=0.0)
    for a in raw.values():
        a["affinity_norm"] = round(a["affinity"] / max_aff, 4) if max_aff > 0 else 0.0
    return raw


def community_heat_index() -> dict[str, dict]:
    """concept -> {trend_score, rising} from community-heat.json (ticks resolved to concepts)."""
    heat = _read_json(SHARED / "community-heat.json") or {}
    out = {}
    for tick, meta in (heat.get("ticks") or {}).items():
        if not isinstance(meta, dict):
            continue
        c = resolve_concept(tick)
        out[c] = {
            "trend_score": float(meta.get("trend_score") or 0.0),
            "rising": bool((meta.get("heat_rank_delta") or 0) > 0 or meta.get("trend") == "rising"),
        }
    return out


def xreco_concept_hits(concept_list: list[str] | None = None) -> dict[str, int]:
    """concept -> count of hot X-Reco ranked items mentioning it."""
    cs = concept_list if concept_list is not None else concepts()
    xreco = _read_json(BOOKM / "x-reco-ranked.json") or {}
    items = xreco.get("ranked_items") or xreco.get("ranked") or []
    hits: dict[str, int] = {}
    for it in (items if isinstance(items, list) else []):
        blob = json.dumps(it, ensure_ascii=False).lower()
        for c in cs:
            if c.lower() in blob:
                hits[c] = hits.get(c, 0) + 1
    return hits


def brief_cashtag_concepts() -> set[str]:
    brief = None
    for name in ("PENDING_BRIEF.json", "PENDING_BRIEF.claimed.json"):
        brief = _read_json(TRADER / name) or brief
    return {resolve_concept(str(c)) for c in ((brief or {}).get("cashtags") or [])}


def novelty_ids(days: int = 14) -> set:
    return _exec_log_recent_ids(days=days)


def concept_scores() -> dict[str, dict]:
    """concept -> {score, signals{...}} — the generalized topic ranker (heat × depth ×
    reco × brief × alive). Depth is implicit: every key is a real wiki concept."""
    cs = concepts()
    heat = community_heat_index()
    hits = xreco_concept_hits(cs)
    cash = brief_cashtag_concepts()
    out = {}
    for c in cs:
        signals: dict[str, Any] = {}
        score = WEIGHTS["base"]
        hm = heat.get(c)
        if hm:
            score += WEIGHTS["heat"] * hm["trend_score"]
            if hm["rising"]:
                score += WEIGHTS["heat_rising"]
            signals["heat_trend_score"] = round(hm["trend_score"], 3)
        if hits.get(c):
            score += min(WEIGHTS["xreco_hit_cap"], WEIGHTS["xreco_hit"] * hits[c])
            signals["xreco_hits"] = hits[c]
        if c in cash:
            score += WEIGHTS["brief_cashtag"]
            signals["brief_cashtag"] = True
        if _has_recent_signals(c):
            score += WEIGHTS["recent_signals"]
            signals["recent_signals"] = True
        out[c] = {"score": round(score, 4), "signals": signals}
    return out


def _has_recent_signals(concept: str) -> bool:
    p = CONCEPTS_DIR / f"{concept}.md"
    try:
        return "Recent Signals (auto-propagated)" in p.read_text(encoding="utf-8")
    except Exception:
        return False


# ── artifact builder ───────────────────────────────────────────────

def build() -> dict[str, Any]:
    aff = author_affinity_index()
    cscores = concept_scores()
    nov = sorted(novelty_ids(14))
    top_authors = sorted(aff.items(), key=lambda kv: kv[1]["affinity"], reverse=True)[:30]
    return {
        "schema": "shared.content-intelligence.v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weights": WEIGHTS,
        "author_affinity": aff,
        "top_authors": [{"handle": h, "affinity": round(v["affinity"], 4),
                         "strong": v["strong"]} for h, v in top_authors],
        "concept_scores": cscores,
        "novelty_exec_log_ids": nov,
        "sources": {
            "author_affinity": str(AUTHOR_AFFINITY_PATH.relative_to(WORKSPACE)),
            "community_heat": "runtime/shared/community-heat.json",
            "x_reco_ranked": "runtime/bookmarker/x-reco-ranked.json",
            "trade_brief": "runtime/trader/PENDING_BRIEF*.json",
        },
    }


def main() -> int:
    out = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    top_c = sorted(out["concept_scores"].items(), key=lambda kv: -kv[1]["score"])[:5]
    print(json.dumps({"status": "ok",
                      "authors": len(out["author_affinity"]),
                      "concepts": len(out["concept_scores"]),
                      "novelty_ids": len(out["novelty_exec_log_ids"]),
                      "top5_concepts": [(c, s["score"]) for c, s in top_c],
                      "top5_authors": [a["handle"] for a in out["top_authors"][:5]]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
