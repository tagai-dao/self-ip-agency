#!/usr/bin/env python3
"""build_wiki_query_index.py — Build compact aggregated wiki query index.

Produces runtime/shared/wiki-query-index.json: a single file that aggregates
the current state of all wiki structured assets for cheap lookups.

This index is refreshable (re-run to update) and compact (references, not payloads).
Operators, agents, and dashboards can read this one file instead of stitching
across 10+ artifacts.

Usage:
    python3 scripts/build_wiki_query_index.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os as _os; WORKSPACE = Path(_os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace"))
SHARED = WORKSPACE / 'runtime' / 'shared'
INDEX_PATH = SHARED / 'wiki-query-index.json'

sys.path.insert(0, str(WORKSPACE / 'scripts'))
from runtime_utils import atomic_write_json, read_json, now_iso, path_ref, append_wiki_event


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
        delta = now - dt
        return round(delta.total_seconds() / 3600, 1)
    except Exception:
        return None


def _build_registry_summary() -> dict[str, Any]:
    """Compact registry summary: concept count, tick list, alias count."""
    reg = read_json(WORKSPACE / 'config' / 'wiki_topic_registry.json')
    if not reg:
        return {"status": "missing"}
    concepts = reg.get('concepts', {})
    ticks = reg.get('ticks', {})
    tracked = [n for n, m in ticks.items() if m.get('tracked')]
    total_aliases = sum(len(m.get('aliases', [])) for m in concepts.values())
    return {
        "concept_count": len(concepts),
        "tick_count": len(ticks),
        "tracked_ticks": tracked,
        "alias_count": total_aliases,
        "updated_at": reg.get("updated_at"),
    }


def _build_artifact_catalog() -> list[dict[str, Any]]:
    """Catalog of wiki artifacts with existence, age, and provenance status."""
    ARTIFACTS = [
        ("wiki-contract-verify.json", "Contract verification report"),
        ("wiki-contract-alert.json", "Contract alert signal"),
        ("wiki-execution-brief.json", "Execution brief (themes, stances)"),
        ("wiki-lint-status.json", "Wiki lint health scores"),
        ("wiki-lint-latest.json", "Latest lint report snapshot"),
        ("wiki-maintenance-report.json", "Nightly maintenance report"),
        ("wiki-maintenance-alert.json", "Maintenance alert signal"),
        ("community-heat.json", "Community heat / tick trends"),
    ]
    catalog: list[dict[str, Any]] = []
    for filename, description in ARTIFACTS:
        path = SHARED / filename
        entry: dict[str, Any] = {
            "artifact": filename,
            "description": description,
            "path": f"runtime/shared/{filename}",
            "exists": path.exists(),
        }
        if path.exists():
            data = read_json(path)
            if data:
                # Pick the most relevant timestamp field
                ts = (data.get("verified_at") or data.get("generated_at")
                      or data.get("compiled_at") or data.get("computed_at"))
                entry["timestamp"] = ts
                entry["age_hours"] = _age_hours(ts)
                entry["schema"] = data.get("schema")
            # Check provenance sidecar
            sidecar = path.parent / f"{path.name}.provenance.json"
            entry["has_provenance"] = sidecar.exists()
        catalog.append(entry)
    return catalog


def _build_health_snapshot() -> dict[str, Any]:
    """Quick health snapshot from alert artifacts."""
    alert = read_json(SHARED / 'wiki-contract-alert.json')
    maint = read_json(SHARED / 'wiki-maintenance-alert.json')
    lint = read_json(SHARED / 'wiki-lint-status.json')

    contract_ok = alert and alert.get("status") == "ok" and alert.get("severity") == "clear"
    maint_ok = maint and maint.get("severity") == "clear"
    lint_ok = lint and not lint.get("needs_attention")

    return {
        "contract_status": alert.get("status", "unknown") if alert else "unknown",
        "contract_severity": alert.get("severity", "unknown") if alert else "unknown",
        "contract_pass": alert.get("pass") if alert else None,
        "contract_fail": alert.get("fail") if alert else None,
        "maintenance_severity": maint.get("severity", "unknown") if maint else "unknown",
        "maintenance_action": maint.get("action", "unknown") if maint else "unknown",
        "lint_health_score": lint.get("health_score") if lint else None,
        "lint_needs_attention": lint.get("needs_attention") if lint else None,
        "overall": "ok" if (contract_ok and maint_ok and lint_ok) else "degraded",
    }


def _build_recent_events(limit: int = 10) -> list[dict[str, Any]]:
    """Last N events from the ledger (compact: ts, type, status, summary only)."""
    ledger = SHARED / 'wiki-events.jsonl'
    if not ledger.exists():
        return []
    lines = ledger.read_text(encoding='utf-8').strip().splitlines()
    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            events.append({
                "ts": e.get("ts"),
                "event_type": e.get("event_type"),
                "status": e.get("status"),
                "summary": e.get("summary"),
            })
        except json.JSONDecodeError:
            continue
        if len(events) >= limit:
            break
    return events


def _upstream_mtime() -> float:
    """Return the most recent mtime across upstream source artifacts.

    Used to decide if the index needs rebuilding.
    """
    sources = [
        WORKSPACE / 'config' / 'wiki_topic_registry.json',
        SHARED / 'wiki-contract-alert.json',
        SHARED / 'wiki-maintenance-alert.json',
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


def _index_is_fresh() -> bool:
    """Return True if the index exists and is newer than all upstream sources."""
    if not INDEX_PATH.exists():
        return False
    try:
        index_mtime = INDEX_PATH.stat().st_mtime
    except OSError:
        return False
    return index_mtime > _upstream_mtime()


def build_index() -> dict[str, Any]:
    """Build the complete query index."""
    return {
        "schema": "wiki-query-index-v1",
        "generated_at": now_iso(),
        "registry": _build_registry_summary(),
        "artifacts": _build_artifact_catalog(),
        "health": _build_health_snapshot(),
        "recent_events": _build_recent_events(limit=10),
    }


def main() -> int:
    # Skip rebuild if index is already fresh (upstream unchanged)
    force = '--force' in sys.argv
    if not force and _index_is_fresh():
        print(json.dumps({"status": "skipped", "reason": "index is fresh",
                           "path": str(INDEX_PATH.relative_to(WORKSPACE))}, indent=2))
        return 0

    index = build_index()
    atomic_write_json(INDEX_PATH, index)

    # Emit event
    append_wiki_event(
        event_type="query_index_build",
        producer="build_wiki_query_index",
        artifact=path_ref(INDEX_PATH, WORKSPACE),
        status="ok",
        summary=f"artifacts={len(index['artifacts'])} health={index['health']['overall']}",
    )

    print(json.dumps({"status": "ok", "path": str(INDEX_PATH.relative_to(WORKSPACE)),
                       "artifacts": len(index["artifacts"]),
                       "health": index["health"]["overall"]}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
