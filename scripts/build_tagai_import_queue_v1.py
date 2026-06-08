#!/usr/bin/env python3
"""build_tagai_import_queue_v1.py - derive a TagAI import queue from the trader brief.

This is the first half of the Phase-2 brief -> TagAI import lane: build a
stable candidate queue from the social-trade brief plus shared content
intelligence. It does not call any external API yet.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agency_paths import MAIN_WS

WORKSPACE = MAIN_WS
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"

import sys
sys.path.insert(0, str(WORKSPACE / "scripts"))
from runtime_utils_v2 import read_json  # type: ignore


QUEUE_PATH = RUNTIME_TRADER / "tagai-import-queue.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _safe_score(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def build_queue() -> dict[str, Any]:
    brief = (
        read_json(RUNTIME_TRADER / "PENDING_BRIEF.json")
        or read_json(RUNTIME_TRADER / "PENDING_BRIEF.claimed.json")
        or {}
    )
    cint = read_json(RUNTIME_SHARED / "content-intelligence.json") or {}
    concept_scores = cint.get("concept_scores") if isinstance(cint.get("concept_scores"), dict) else {}

    cashtags = [str(x).strip() for x in (brief.get("cashtags") or []) if str(x).strip()]
    theme_names = [str(x).strip() for x in (brief.get("theme_names") or []) if str(x).strip()]

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    explicit_candidates = brief.get("import_candidates") if isinstance(brief.get("import_candidates"), list) else []

    for raw in explicit_candidates:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("tick") or raw.get("value") or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        import_info = raw.get("import_info") if isinstance(raw.get("import_info"), dict) else {}
        entries.append({
            "candidate_id": str(raw.get("candidate_id") or f"tagai-import:{value.lower()}"),
            "kind": str(raw.get("kind") or "tick"),
            "value": value,
            "priority": round(_safe_score(raw.get("priority")) or (2.0 + _safe_score((concept_scores.get(value) or {}).get("score"))), 4),
            "source": str(raw.get("source") or "social-trade-brief.import_candidates"),
            "source_ref": str(raw.get("source_ref") or "runtime/trader/PENDING_BRIEF*.json#import_candidates"),
            "api_action": "community.import",
            "import_info": import_info,
            "signature_message": raw.get("signature_message"),
            "eth_signature": raw.get("eth_signature"),
            "extra_headers": raw.get("extra_headers") if isinstance(raw.get("extra_headers"), dict) else None,
            "body_overrides": raw.get("body_overrides") if isinstance(raw.get("body_overrides"), dict) else None,
        })

    for tick in cashtags[:6]:
        if tick in seen:
            continue
        seen.add(tick)
        entries.append({
            "candidate_id": f"tagai-import:{tick.lower()}",
            "kind": "tick",
            "value": tick,
            "priority": round(2.0 + _safe_score((concept_scores.get(tick) or {}).get("score")), 4),
            "source": "social-trade-brief",
            "source_ref": "runtime/trader/PENDING_BRIEF*.json",
            "api_action": "community.import",
        })

    for concept in theme_names[:4]:
        if concept in seen:
            continue
        seen.add(concept)
        entries.append({
            "candidate_id": f"tagai-import:{concept.lower()}",
            "kind": "concept",
            "value": concept,
            "priority": round(1.0 + _safe_score((concept_scores.get(concept) or {}).get("score")), 4),
            "source": "wiki-theme",
            "source_ref": "runtime/shared/content-intelligence.json",
            "api_action": "community.import",
        })

    entries.sort(key=lambda row: (-_safe_score(row.get("priority")), str(row.get("value") or "")))

    status = "ok" if entries else "partial"
    return {
        "schema": "tagai-import-queue.v1",
        "generated_at": now_iso(),
        "status": status,
        "queue_kind": "brief-to-tagai-import",
        "brief_ref": "runtime/trader/PENDING_BRIEF*.json",
        "content_intelligence_ref": "runtime/shared/content-intelligence.json",
        "entry_count": len(entries),
        "entries": entries,
        "notes": "Queue for the real TagAI import executor. Entries without import_info remain planning candidates and will be blocked honestly at execution time.",
    }


def main() -> int:
    queue = build_queue()
    atomic_write_json(QUEUE_PATH, queue)
    print(json.dumps({
        "status": "ok",
        "entry_count": queue.get("entry_count"),
        "path": str(QUEUE_PATH.relative_to(WORKSPACE)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
