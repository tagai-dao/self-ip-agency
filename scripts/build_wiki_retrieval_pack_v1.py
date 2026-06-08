#!/usr/bin/env python3
"""build_wiki_retrieval_pack_v1.py — Build retrieval-oriented index/pack v1.

Produces runtime/shared/wiki-retrieval-pack.json: a set of compact, retrieval-friendly
documents synthesized from existing wiki structured assets. Each document is a
self-contained text+metadata chunk suitable for agent consumption, semantic search,
or future indexing without needing a DB/vector backend.

Document types:
  - entity      : one doc per registered concept/tick (registry + brief + heat signals)
  - artifact    : one doc per tracked artifact (metadata + provenance + explainability)
  - event_window: one doc summarizing the recent event window (last N events, grouped)
  - health_digest: one doc summarizing current system health (contract + lint + maintenance)

Usage:
    python3 scripts/build_wiki_retrieval_pack_v1.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
SHARED = WORKSPACE / 'runtime' / 'shared'
PACK_PATH = SHARED / 'wiki-retrieval-pack.json'

sys.path.insert(0, str(WORKSPACE / 'scripts'))
from runtime_utils_v2 import atomic_write_json, read_json, now_iso, path_ref, append_wiki_event


# ── Helpers ──

def _age_hours(ts_str: str | None) -> float | None:
    """Compute hours since a timestamp string."""
    if not ts_str:
        return None
    text = ts_str.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(text)
        now = datetime.now(timezone.utc).astimezone()
        return round((now - dt).total_seconds() / 3600, 1)
    except Exception:
        return None


def _load_registry() -> dict[str, Any]:
    return read_json(WORKSPACE / 'config' / 'wiki_topic_registry.json') or {'concepts': {}, 'ticks': {}}


def _read_jsonl_last(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    """Read last N lines of a JSONL file (most recent first)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding='utf-8').strip().splitlines()
        out: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ── Document Builders ──

def _build_entity_docs() -> list[dict[str, Any]]:
    """One retrieval doc per registered concept and tracked tick.

    Each doc merges: registry metadata, execution brief theme data (if any),
    topic heatmap score (if any), and community heat (if any).
    """
    reg = _load_registry()
    brief = read_json(SHARED / 'wiki-execution-brief.json') or {}
    heatmap = read_json(WORKSPACE / 'runtime' / 'bookmarker' / 'topic-heatmap.json') or {}
    community = read_json(SHARED / 'community-heat.json') or {}

    # Index brief themes by name for fast lookup
    theme_by_name: dict[str, dict[str, Any]] = {}
    for theme in brief.get('top_themes', []):
        theme_by_name[theme.get('name', '')] = theme

    # Index heatmap community_fit_scores
    fit_scores: dict[str, float] = heatmap.get('community_fit_scores', {})

    # Index community heat ticks
    heat_ticks: dict[str, dict[str, Any]] = community.get('ticks', {})

    docs: list[dict[str, Any]] = []

    # Concepts
    for canonical, meta in reg.get('concepts', {}).items():
        text_parts: list[str] = [f"Concept: {canonical}"]
        if meta.get('aliases'):
            text_parts.append(f"Aliases: {', '.join(meta['aliases'])}")
        text_parts.append(f"Category: {meta.get('category', 'unknown')}")

        theme = theme_by_name.get(canonical)
        if theme:
            if theme.get('core_stance'):
                text_parts.append(f"Stance: {theme['core_stance']}")
            if theme.get('agent_action'):
                text_parts.append(f"Agent action: {theme['agent_action']}")
            if theme.get('align_hook'):
                text_parts.append(f"Discussion hook: {theme['align_hook']}")
            text_parts.append(f"Heat score: {theme.get('heat_score', 0)}")

        fit = fit_scores.get(canonical)
        if fit is not None:
            text_parts.append(f"Community fit: {fit}")

        docs.append({
            "doc_type": "entity",
            "doc_id": f"entity:concept:{canonical}",
            "entity_kind": "concept",
            "canonical_name": canonical,
            "category": meta.get('category'),
            "text": "\n".join(text_parts),
            "source_refs": [f"config/wiki_topic_registry.json",
                            "runtime/shared/wiki-execution-brief.json",
                            "runtime/bookmarker/topic-heatmap.json"],
        })

    # Tracked ticks
    for tick_name, meta in reg.get('ticks', {}).items():
        if not meta.get('tracked'):
            continue
        text_parts = [f"Tick: {tick_name}", f"Tracked: true"]

        heat = heat_ticks.get(tick_name)
        if heat:
            text_parts.append(f"Trend: {heat.get('trend', 'unknown')}")
            text_parts.append(f"Trend score: {heat.get('trend_score', 'N/A')}")
            text_parts.append(f"Trending rank: {heat.get('trending_rank', 'N/A')}")
            if heat.get('trend_basis'):
                text_parts.append(f"Trend basis: {heat['trend_basis']}")

        docs.append({
            "doc_type": "entity",
            "doc_id": f"entity:tick:{tick_name}",
            "entity_kind": "tick",
            "canonical_name": tick_name,
            "text": "\n".join(text_parts),
            "source_refs": ["config/wiki_topic_registry.json",
                            "runtime/shared/community-heat.json"],
        })

    return docs


def _build_artifact_docs() -> list[dict[str, Any]]:
    """One retrieval doc per tracked wiki artifact with provenance/explainability."""
    ARTIFACTS = [
        ("wiki-contract-verify.json", "Contract verification report", "verified_at"),
        ("wiki-contract-alert.json", "Contract alert signal", "verified_at"),
        ("wiki-execution-brief.json", "Execution brief — top themes and stances", "compiled_at"),
        ("wiki-lint-status.json", "Wiki lint health scores", "generated_at"),
        ("wiki-lint-latest.json", "Latest lint report snapshot", None),
        ("wiki-maintenance-report.json", "Nightly maintenance report", "generated_at"),
        ("wiki-maintenance-alert.json", "Maintenance alert signal", "generated_at"),
        ("community-heat.json", "Community heat / tick trends", "computed_at"),
    ]
    docs: list[dict[str, Any]] = []

    for filename, description, ts_field in ARTIFACTS:
        path = SHARED / filename
        if not path.exists():
            continue
        data = read_json(path)
        if not data:
            continue

        ts = data.get(ts_field) if ts_field else None
        age = _age_hours(ts)

        text_parts = [f"Artifact: {filename}", f"Description: {description}"]
        if data.get('schema'):
            text_parts.append(f"Schema: {data['schema']}")
        if ts:
            text_parts.append(f"Timestamp: {ts}")
        if age is not None:
            text_parts.append(f"Age: {age}h")

        # Extract key signals depending on artifact type
        if 'status' in data:
            text_parts.append(f"Status: {data['status']}")
        if 'severity' in data:
            text_parts.append(f"Severity: {data['severity']}")
        if 'pass' in data and 'fail' in data:
            text_parts.append(f"Pass: {data['pass']}, Fail: {data['fail']}")
        if 'overall_status' in data:
            text_parts.append(f"Overall status: {data['overall_status']}")
        if 'health_score' in data:
            text_parts.append(f"Health score: {data['health_score']}")

        # Provenance sidecar
        sidecar_path = path.parent / f"{path.name}.provenance.json"
        prov = read_json(sidecar_path)
        source_refs = [f"runtime/shared/{filename}"]
        if prov:
            text_parts.append(f"Producer: {prov.get('producer', 'unknown')}")
            if prov.get('source_refs'):
                text_parts.append(f"Source refs: {', '.join(prov['source_refs'])}")
                source_refs.extend(prov['source_refs'])
            if prov.get('facts'):
                facts_str = "; ".join(f"{k}={v}" for k, v in prov['facts'].items())
                text_parts.append(f"Provenance facts: {facts_str}")

        docs.append({
            "doc_type": "artifact",
            "doc_id": f"artifact:{filename}",
            "artifact_name": filename,
            "schema": data.get('schema'),
            "timestamp": ts,
            "age_hours": age,
            "has_provenance": prov is not None,
            "text": "\n".join(text_parts),
            "source_refs": source_refs,
        })

    return docs


def _build_event_window_doc(limit: int = 20) -> dict[str, Any] | None:
    """One retrieval doc summarizing the recent event window."""
    events = _read_jsonl_last(SHARED / 'wiki-events.jsonl', limit=limit)
    if not events:
        return None

    # Group by event_type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        et = e.get('event_type', 'unknown')
        by_type.setdefault(et, []).append(e)

    text_parts = [f"Recent event window ({len(events)} events)"]
    for et, group in sorted(by_type.items()):
        ok_count = sum(1 for e in group if e.get('status') == 'ok')
        fail_count = len(group) - ok_count
        latest = group[0]  # already sorted most-recent-first
        text_parts.append(f"  {et}: {len(group)} events (ok={ok_count}, fail={fail_count}), latest: {latest.get('ts', '?')}")
        if latest.get('summary'):
            text_parts.append(f"    Latest summary: {latest['summary']}")

    ts_range_start = events[-1].get('ts', '?') if events else '?'
    ts_range_end = events[0].get('ts', '?') if events else '?'

    return {
        "doc_type": "event_window",
        "doc_id": "event_window:recent",
        "event_count": len(events),
        "event_types": list(by_type.keys()),
        "ts_range": [ts_range_start, ts_range_end],
        "text": "\n".join(text_parts),
        "source_refs": ["runtime/shared/wiki-events.jsonl"],
    }


def _build_decision_window_doc(limit: int = 25) -> dict[str, Any] | None:
    """One retrieval doc summarizing the recent decision-memory ledger so agents
    can recall 'what did we recently decide, and how did it turn out'."""
    try:
        idx = json.loads((SHARED / 'decision-index.json').read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(idx, dict):
        return None
    decisions = idx.get('decisions') or []
    if not decisions:
        return None
    by_kind = idx.get('by_kind') or {}
    text_parts = [f"Recent agent decisions ({idx.get('count', len(decisions))} in ledger; "
                  + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())) + ")"]
    for d in decisions[:limit]:
        when = (d.get('decided_at') or '?').split('T')[0]
        line = f"  [{when}] {d.get('agent','?')}/{d.get('kind','?')}: {d.get('action','')} → {d.get('outcome','?')}"
        if d.get('rationale'):
            line += f" ({d['rationale'][:120]})"
        text_parts.append(line)
    return {
        "doc_type": "decision_window",
        "doc_id": "decision_window:recent",
        "decision_count": idx.get('count', len(decisions)),
        "by_kind": by_kind,
        "text": "\n".join(text_parts),
        "source_refs": ["runtime/shared/decision-index.json"],
    }


def _build_health_digest_doc() -> dict[str, Any]:
    """One retrieval doc summarizing current system health."""
    alert = read_json(SHARED / 'wiki-contract-alert.json')
    maint = read_json(SHARED / 'wiki-maintenance-alert.json')
    maint_report = read_json(SHARED / 'wiki-maintenance-report.json')
    lint = read_json(SHARED / 'wiki-lint-status.json')

    text_parts = ["System health digest"]

    # Contract
    if alert:
        text_parts.append(f"Contract: status={alert.get('status')}, severity={alert.get('severity')}, pass={alert.get('pass')}, fail={alert.get('fail')}")
        if alert.get('failing_checks'):
            text_parts.append(f"  Failing checks: {', '.join(alert['failing_checks'])}")
    else:
        text_parts.append("Contract: artifact missing")

    # Lint
    if lint:
        text_parts.append(f"Lint: health_score={lint.get('health_score')}, needs_attention={lint.get('needs_attention')}")
        for key in ('broken_links_count', 'stale_count', 'orphan_count', 'empty_count'):
            if key in lint:
                text_parts.append(f"  {key}: {lint[key]}")
    else:
        text_parts.append("Lint: artifact missing")

    # Maintenance
    if maint:
        text_parts.append(f"Maintenance: severity={maint.get('severity')}, action={maint.get('action')}")
        text_parts.append(f"  Pre-repair: {maint.get('pre_repair_status')}, Post-repair: {maint.get('post_repair_status')}")
        text_parts.append(f"  Repairs: attempted={maint.get('repairs_attempted', 0)}, succeeded={maint.get('repairs_succeeded', 0)}, failed={maint.get('repairs_failed', 0)}")
    else:
        text_parts.append("Maintenance: artifact missing")

    # Maintenance report steps summary
    if maint_report:
        steps = maint_report.get('steps', {})
        cv = steps.get('contract_verify', {})
        if cv:
            text_parts.append(f"Maintenance contract verify: pass={cv.get('pass')}, fail={cv.get('fail')}")
        pc = steps.get('provenance_coverage', {})
        if pc:
            text_parts.append(f"Provenance coverage: {pc.get('coverage_pct', '?')}%")
            if pc.get('missing'):
                text_parts.append(f"  Missing provenance: {', '.join(pc['missing'])}")

    # Overall
    contract_ok = alert and alert.get('status') == 'ok' and alert.get('severity') == 'clear'
    maint_ok = maint and maint.get('severity') == 'clear'
    lint_ok = lint and not lint.get('needs_attention')
    overall = 'ok' if (contract_ok and maint_ok and lint_ok) else 'degraded'
    text_parts.append(f"Overall: {overall}")

    return {
        "doc_type": "health_digest",
        "doc_id": "health_digest:current",
        "overall": overall,
        "text": "\n".join(text_parts),
        "source_refs": [
            "runtime/shared/wiki-contract-alert.json",
            "runtime/shared/wiki-maintenance-alert.json",
            "runtime/shared/wiki-maintenance-report.json",
            "runtime/shared/wiki-lint-status.json",
        ],
    }


# ── Pack Builder ──

def build_retrieval_pack() -> dict[str, Any]:
    """Build the complete retrieval pack."""
    docs: list[dict[str, Any]] = []

    # Entity docs (concepts + tracked ticks)
    docs.extend(_build_entity_docs())

    # Artifact explainability docs
    docs.extend(_build_artifact_docs())

    # Event window doc
    event_doc = _build_event_window_doc(limit=20)
    if event_doc:
        docs.append(event_doc)

    # Decision window doc (recent agent decisions + outcomes)
    decision_doc = _build_decision_window_doc(limit=25)
    if decision_doc:
        docs.append(decision_doc)

    # Health digest doc
    docs.append(_build_health_digest_doc())

    # Compute type counts
    type_counts: dict[str, int] = {}
    for d in docs:
        dt = d.get('doc_type', 'unknown')
        type_counts[dt] = type_counts.get(dt, 0) + 1

    return {
        "schema": "wiki-retrieval-pack-v1",
        "generated_at": now_iso(),
        "doc_count": len(docs),
        "doc_type_counts": type_counts,
        "docs": docs,
    }


def _upstream_mtime() -> float:
    """Return the most recent mtime across upstream source artifacts."""
    sources = [
        WORKSPACE / 'config' / 'wiki_topic_registry.json',
        SHARED / 'wiki-execution-brief.json',
        SHARED / 'community-heat.json',
        SHARED / 'wiki-contract-alert.json',
        SHARED / 'wiki-maintenance-report.json',
        SHARED / 'wiki-lint-status.json',
        SHARED / 'wiki-events.jsonl',
    ]
    latest = 0.0
    for s in sources:
        try:
            latest = max(latest, s.stat().st_mtime)
        except OSError:
            pass
    return latest


def _pack_is_fresh() -> bool:
    """Return True if the pack exists and is newer than all upstream sources."""
    if not PACK_PATH.exists():
        return False
    try:
        pack_mtime = PACK_PATH.stat().st_mtime
    except OSError:
        return False
    return pack_mtime > _upstream_mtime()


def main() -> int:
    # Skip rebuild if pack is already fresh (upstream unchanged)
    force = '--force' in sys.argv
    if not force and _pack_is_fresh():
        print(json.dumps({"status": "skipped", "reason": "pack is fresh",
                           "path": str(PACK_PATH.relative_to(WORKSPACE))}, indent=2))
        return 0

    pack = build_retrieval_pack()
    atomic_write_json(PACK_PATH, pack)

    # Emit event
    append_wiki_event(
        event_type="retrieval_pack_build",
        producer="build_wiki_retrieval_pack_v1",
        artifact=path_ref(PACK_PATH, WORKSPACE),
        status="ok",
        summary=f"docs={pack['doc_count']} types={pack['doc_type_counts']}",
    )

    print(json.dumps({
        "status": "ok",
        "path": str(PACK_PATH.relative_to(WORKSPACE)),
        "doc_count": pack["doc_count"],
        "doc_type_counts": pack["doc_type_counts"],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
