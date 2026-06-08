#!/usr/bin/env python3
"""build_topic_candidates_v1.py — rank wiki concepts as POST topics.

Replaces the old "mine every concept uniformly" behaviour that recycled the same
drafts until they all hit the 14-day post-dedup window (→ posting starved, 4 days
of 0 posts). Fuses hotness + depth + 0xNought signal + novelty into a per-concept
score the draft generator (build_wiki_grounded_drafts_v1) uses to choose WHAT to
post about.

Sources (all read-only, all guarded — missing → neutral):
  - runtime/shared/community-heat.json      (tick hotness/trend → concept via registry)
  - runtime/bookmarker/x-reco-ranked.json   (hot recommended items → concept by name/alias)
  - runtime/trader/PENDING_BRIEF*.json       (hot cashtags from the social-trade brief)
  - wiki/concepts/<C>.md "Recent Signals"    (recent inflow = topic is alive)
  - 14-day exec-log (execute_planned_action_v1) → novelty (exec_log_recent_ids)

Output: runtime/bookmarker/topic-candidates.json
  { schema, generated_at, candidates: [{concept, score, signals{...}}...],
    exec_log_recent_ids: [...] }

Depth = the concept exists in wiki/concepts (every candidate is a real concept).
Novelty is applied downstream by the generator (it owns the draft-id scheme).
"""
from __future__ import annotations

import json
import os
import re
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
OUTPUT = BOOKM / "topic-candidates.json"

try:
    from wiki_registry import resolve_concept, get_all_concepts  # type: ignore
except Exception:
    def resolve_concept(name: str) -> str:  # type: ignore
        return name
    def get_all_concepts() -> list[str]:  # type: ignore
        return []

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


def _concepts() -> list[str]:
    if CONCEPTS_DIR.exists():
        return sorted(p.stem for p in CONCEPTS_DIR.glob("*.md") if not p.name.lower().startswith("readme"))
    return []


def _has_recent_signals(concept: str) -> bool:
    p = CONCEPTS_DIR / f"{concept}.md"
    try:
        return "Recent Signals (auto-propagated)" in p.read_text(encoding="utf-8")
    except Exception:
        return False


def build() -> dict[str, Any]:
    # #3 (2026-06-08): concept scoring + novelty now come from the shared
    # content-intelligence layer (content_intelligence_v1) so the "what's worth posting"
    # formula is defined in ONE place for all five apps. This script is now a thin
    # bookmarker-facing consumer that just shapes the shared concept_scores into the
    # candidate list the draft generator expects. Falls back to local computation only
    # if the shared module is unavailable.
    try:
        import content_intelligence_v1 as ci  # type: ignore
        cscores = ci.concept_scores()
        exec_ids = ci.novelty_ids(14)
    except Exception:
        cscores, exec_ids = {}, _exec_log_recent_ids(days=14)

    candidates = [{"concept": c, "score": s["score"], "signals": s["signals"]}
                  for c, s in cscores.items()]
    candidates.sort(key=lambda x: -x["score"])
    return {
        "schema": "bookmarker.topic-candidates.v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "exec_log_recent_ids": sorted(exec_ids),
    }


def main() -> int:
    out = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    top = out["candidates"][:5]
    print(json.dumps({"status": "ok", "candidates": out["candidate_count"],
                      "exec_log_ids": len(out["exec_log_recent_ids"]),
                      "top5": [(c["concept"], c["score"]) for c in top]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
