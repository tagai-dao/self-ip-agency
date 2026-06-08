#!/usr/bin/env python3
"""Regression tests for query_wiki_facts_v1.py."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prep_workspace() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="query-wiki-test-"))
    (ws / "scripts").mkdir(parents=True, exist_ok=True)
    (ws / "runtime" / "shared").mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    (ws / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (ws / "wiki" / "concepts" / "ATOC.md").write_text("# ATOC\n", encoding="utf-8")
    _write_json(ws / "config" / "wiki_topic_registry.json", {
        "concepts": {
            "ATOC": {
                "canonical_name": "ATOC",
                "aliases": ["AgentInfrastructure"],
                "category": "infrastructure",
                "wiki_file": "wiki/concepts/ATOC.md",
            }
        },
        "ticks": {
            "TagClaw": {
                "canonical_name": "TagClaw",
                "tracked": True,
                "wiki_file": "wiki/tagclaw-platform/trending-ticks.md",
            }
        },
    })
    _write_json(ws / "runtime" / "shared" / "wiki-query-index.json", {
        "health": {"overall": "ok"},
        "artifacts": [
            {
                "artifact": "wiki-execution-brief.json",
                "path": "runtime/shared/wiki-execution-brief.json",
                "exists": True,
            }
        ],
    })
    _write_json(ws / "runtime" / "shared" / "wiki-execution-brief.json", {
        "schema": "wiki-execution-brief-v1",
        "compiled_at": "2026-06-08T00:00:00Z",
        "top_themes": [{"name": "ATOC"}],
    })
    _write_json(ws / "runtime" / "shared" / "wiki-execution-brief.json.provenance.json", {
        "schema": "provenance-sidecar-v1",
        "producer": "build_wiki_execution_brief_v1",
        "artifact_ref": "runtime/shared/wiki-execution-brief.json",
        "generated_at": "2026-06-08T00:00:01Z",
    })
    (ws / "runtime" / "shared" / "wiki-events.jsonl").write_text(
        json.dumps({
            "ts": "2026-06-08T00:00:02Z",
            "event_type": "contract_verify",
            "producer": "verify_wiki_runtime_contract_v1",
            "status": "ok",
            "summary": "contract ok",
        }) + "\n",
        encoding="utf-8",
    )
    _write_json(ws / "runtime" / "shared" / "wiki-contract-alert.json", {
        "status": "ok",
        "severity": "clear",
        "pass": 10,
        "fail": 0,
    })
    _write_json(ws / "runtime" / "shared" / "wiki-lint-status.json", {
        "status": "ok",
        "health_score": 95,
        "needs_attention": False,
    })
    _write_json(ws / "runtime" / "shared" / "wiki-maintenance-alert.json", {
        "severity": "clear",
        "action": "none",
        "post_repair_status": "ok",
    })
    _write_json(ws / "runtime" / "shared" / "wiki-maintenance-report.json", {
        "generated_at": "2026-06-08T00:00:03Z",
        "overall_status": "ok",
        "degraded_signals": [],
        "repair_results": [],
        "steps": {},
    })
    _write_json(ws / "runtime" / "shared" / "wiki-retrieval-pack.json", {
        "docs": [
            {"doc_id": "entity:concept:ATOC", "doc_type": "entity", "canonical_name": "ATOC", "text": "ATOC TagClaw"},
            {"doc_id": "artifact:wiki-execution-brief.json", "doc_type": "artifact", "artifact_name": "wiki-execution-brief.json", "text": "execution brief"},
        ]
    })
    return ws


def _load_module(workspace: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(workspace)
    if "query_wiki_facts_v1" in sys.modules:
        del sys.modules["query_wiki_facts_v1"]
    if "wiki_registry" in sys.modules:
        del sys.modules["wiki_registry"]
    target = HERE / "query_wiki_facts_v1.py"
    (workspace / "scripts" / "query_wiki_facts_v1.py").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    return importlib.import_module("query_wiki_facts_v1")


def test_canonical_alias() -> None:
    mod = _load_module(_prep_workspace())
    out = mod.query_canonical("AgentInfrastructure")
    _assert(out["found"] is True, "alias should resolve")
    _assert(out["canonical_name"] == "ATOC", "alias should canonicalize to ATOC")


def test_artifact_lookup() -> None:
    mod = _load_module(_prep_workspace())
    out = mod.query_artifact("wiki-execution-brief")
    _assert(out["found"] is True, "artifact should be found")
    _assert(out["schema"] == "wiki-execution-brief-v1", "schema should surface")
    _assert(isinstance(out["provenance"], dict), "provenance should be returned")


def test_retrieve_filtering() -> None:
    mod = _load_module(_prep_workspace())
    out = mod.query_retrieve(query="ATOC", doc_type="entity", limit=5)
    _assert(out["count"] == 1, "one entity doc should match")
    _assert(out["results"][0]["doc_id"] == "entity:concept:ATOC", "ATOC entity should rank first")


def test_health_summary() -> None:
    mod = _load_module(_prep_workspace())
    out = mod.query_health()
    _assert(out["overall"] == "ok", "health should aggregate to ok")
    _assert(out["contract"]["severity"] == "clear", "contract severity should surface")


if __name__ == "__main__":
    tests = [
        test_canonical_alias,
        test_artifact_lookup,
        test_retrieve_filtering,
        test_health_summary,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
