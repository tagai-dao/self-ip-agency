#!/usr/bin/env python3
"""query_wiki_facts_v1.py — thin query surface over structured wiki artifacts.

CLI modes:
  canonical   Resolve alias/tick/concept -> canonical record
  artifact    Inspect one derived artifact and its provenance sidecar
  events      Read recent wiki events from runtime/shared/wiki-events.jsonl
  health      Aggregate contract + lint + maintenance health
  maintenance Read latest wiki maintenance report
  retrieve    Search retrieval-pack docs with cheap lexical matching
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parent.parent)
SHARED = WORKSPACE / "runtime" / "shared"

import sys
sys.path.insert(0, str(WORKSPACE / "scripts"))

from runtime_utils_v2 import read_json
from wiki_registry import (
    get_all_ticks,
    get_concept_aliases,
    get_concept_category,
    get_concept_wiki_file,
    resolve_concept,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _load_registry() -> dict[str, Any]:
    return read_json(WORKSPACE / "config" / "wiki_topic_registry.json") or {"concepts": {}, "ticks": {}}


def _artifact_catalog() -> dict[str, dict[str, Any]]:
    query_index = read_json(SHARED / "wiki-query-index.json") or {}
    artifacts = query_index.get("artifacts")
    if isinstance(artifacts, list):
        out: dict[str, dict[str, Any]] = {}
        for row in artifacts:
            if isinstance(row, dict) and row.get("artifact"):
                key = str(row["artifact"])
                out[key] = row
                out[key.removesuffix(".json")] = row
        return out

    fallback = {}
    for name in [
        "wiki-contract-verify.json",
        "wiki-contract-alert.json",
        "wiki-execution-brief.json",
        "wiki-lint-status.json",
        "wiki-maintenance-report.json",
        "wiki-maintenance-alert.json",
        "community-heat.json",
        "wiki-query-index.json",
        "wiki-retrieval-pack.json",
    ]:
        path = SHARED / name
        fallback[name] = {
            "artifact": name,
            "path": f"runtime/shared/{name}",
            "exists": path.exists(),
        }
        fallback[name.removesuffix(".json")] = fallback[name]
    return fallback


def query_canonical(name: str) -> dict[str, Any]:
    reg = _load_registry()
    concepts = reg.get("concepts", {})
    ticks = reg.get("ticks", {})
    aliases = get_concept_aliases()
    canonical = resolve_concept(name)

    if canonical in concepts:
        meta = concepts.get(canonical) or {}
        matched_as = "canonical" if canonical == name else "alias"
        return {
            "query": name,
            "found": True,
            "entity_kind": "concept",
            "matched_as": matched_as,
            "canonical_name": canonical,
            "category": get_concept_category(canonical),
            "wiki_file": get_concept_wiki_file(canonical),
            "aliases": meta.get("aliases", []),
        }

    tick_lookup = {t.lower(): t for t in get_all_ticks()}
    tick_name = tick_lookup.get(name.lower())
    if tick_name:
        meta = ticks.get(tick_name) or {}
        return {
            "query": name,
            "found": True,
            "entity_kind": "tick",
            "matched_as": "canonical",
            "canonical_name": tick_name,
            "tracked": bool(meta.get("tracked")),
            "wiki_file": meta.get("wiki_file"),
            "aliases": meta.get("aliases", []),
        }

    alias_hit = aliases.get(name)
    return {
        "query": name,
        "found": False,
        "canonical_name": canonical if canonical != name else None,
        "alias_hit": alias_hit,
        "known_concepts": len(concepts),
        "known_ticks": len(ticks),
    }


def query_artifact(name: str) -> dict[str, Any]:
    catalog = _artifact_catalog()
    row = catalog.get(name) or catalog.get(f"{name}.json")
    if not row:
        return {"query": name, "found": False, "known_artifacts": sorted(k for k in catalog if k.endswith(".json"))}

    path_str = row.get("path") or f"runtime/shared/{row.get('artifact')}"
    path = WORKSPACE / path_str
    data = read_json(path)
    sidecar = read_json(path.parent / f"{path.name}.provenance.json")
    out = dict(row)
    out.update({
        "query": name,
        "found": True,
        "payload_keys": sorted(data.keys()) if isinstance(data, dict) else [],
        "schema": (data or {}).get("schema") if isinstance(data, dict) else None,
        "timestamp": ((data or {}).get("generated_at")
                      or (data or {}).get("verified_at")
                      or (data or {}).get("compiled_at")
                      or (data or {}).get("computed_at")) if isinstance(data, dict) else None,
        "provenance": sidecar,
    })
    return out


def query_events(limit: int = 5, event_type: str | None = None) -> dict[str, Any]:
    events = _read_jsonl(SHARED / "wiki-events.jsonl")
    if event_type:
        events = [e for e in events if str(e.get("event_type")) == event_type]
    events = list(reversed(events))[: max(0, limit)]
    return {
        "limit": limit,
        "event_type": event_type,
        "count": len(events),
        "events": events,
    }


def query_health() -> dict[str, Any]:
    contract = read_json(SHARED / "wiki-contract-alert.json") or {}
    lint = read_json(SHARED / "wiki-lint-status.json") or {}
    maintenance = read_json(SHARED / "wiki-maintenance-alert.json") or {}
    query_index = read_json(SHARED / "wiki-query-index.json") or {}
    health = query_index.get("health") if isinstance(query_index, dict) else None
    return {
        "schema": "wiki-health-query.v1",
        "contract": {
            "status": contract.get("status", "unknown"),
            "severity": contract.get("severity", "unknown"),
            "pass": contract.get("pass"),
            "fail": contract.get("fail"),
        },
        "lint": {
            "health_score": lint.get("health_score"),
            "needs_attention": lint.get("needs_attention"),
            "status": lint.get("status", "unknown"),
        },
        "maintenance": {
            "severity": maintenance.get("severity", "unknown"),
            "action": maintenance.get("action", "unknown"),
            "post_repair_status": maintenance.get("post_repair_status"),
        },
        "query_index_health": health,
        "overall": (health or {}).get("overall", "unknown") if isinstance(health, dict) else "unknown",
    }


def query_maintenance() -> dict[str, Any]:
    report = read_json(SHARED / "wiki-maintenance-report.json")
    alert = read_json(SHARED / "wiki-maintenance-alert.json")
    if not report:
        return {"found": False, "alert": alert}
    return {
        "found": True,
        "generated_at": report.get("generated_at"),
        "overall_status": report.get("overall_status"),
        "degraded_signals": report.get("degraded_signals", []),
        "repair_results": report.get("repair_results", []),
        "steps": report.get("steps", {}),
        "alert": alert,
    }


def _retrieve_docs() -> list[dict[str, Any]]:
    pack = read_json(SHARED / "wiki-retrieval-pack.json") or {}
    docs = pack.get("docs")
    return docs if isinstance(docs, list) else []


def _doc_score(doc: dict[str, Any], terms: list[str]) -> int:
    blob = "\n".join([
        str(doc.get("canonical_name") or ""),
        str(doc.get("artifact_name") or ""),
        str(doc.get("doc_type") or ""),
        str(doc.get("text") or ""),
    ]).lower()
    score = 0
    for term in terms:
        if term in blob:
            score += 2
        if str(doc.get("canonical_name") or "").lower() == term:
            score += 3
    return score


def query_retrieve(query: str | None = None, doc_type: str | None = None, limit: int = 10) -> dict[str, Any]:
    docs = [d for d in _retrieve_docs() if isinstance(d, dict)]
    if doc_type:
        docs = [d for d in docs if str(d.get("doc_type")) == doc_type]
    if query:
        terms = [t for t in query.lower().split() if t]
        ranked = []
        for doc in docs:
            score = _doc_score(doc, terms)
            if score > 0:
                ranked.append((score, doc))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("doc_id") or "")))
        docs = [doc | {"match_score": score} for score, doc in ranked[: max(0, limit)]]
    else:
        docs = docs[: max(0, limit)]
    return {
        "query": query,
        "doc_type": doc_type,
        "limit": limit,
        "count": len(docs),
        "results": docs,
    }


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("canonical")
    p.add_argument("name")

    p = sub.add_parser("artifact")
    p.add_argument("name")

    p = sub.add_parser("events")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--type", dest="event_type", default=None)

    sub.add_parser("health")
    sub.add_parser("maintenance")

    p = sub.add_parser("retrieve")
    p.add_argument("--query", default=None)
    p.add_argument("--doc-type", default=None)
    p.add_argument("--limit", type=int, default=10)
    return ap


def main() -> int:
    args = _build_parser().parse_args()
    if args.cmd == "canonical":
        out = query_canonical(args.name)
    elif args.cmd == "artifact":
        out = query_artifact(args.name)
    elif args.cmd == "events":
        out = query_events(limit=args.limit, event_type=args.event_type)
    elif args.cmd == "health":
        out = query_health()
    elif args.cmd == "maintenance":
        out = query_maintenance()
    elif args.cmd == "retrieve":
        out = query_retrieve(query=args.query, doc_type=args.doc_type, limit=args.limit)
    else:
        raise AssertionError(f"Unhandled command: {args.cmd}")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
