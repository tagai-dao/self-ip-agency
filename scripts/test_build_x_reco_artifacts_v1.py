#!/usr/bin/env python3
"""Regression tests for build_x_reco_artifacts_v1.py."""

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


def _prep_workspace() -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="xreco-test-"))
    main_ws = root / "workspace"
    bookmarker_ws = root / "workspace-bookmarker"
    for ws in (main_ws, bookmarker_ws):
        (ws / "runtime" / "bookmarker").mkdir(parents=True, exist_ok=True)
        (ws / "runtime" / "shared").mkdir(parents=True, exist_ok=True)
        (ws / "config").mkdir(parents=True, exist_ok=True)
        (ws / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (main_ws / "wiki" / "concepts" / "ATOC.md").write_text("# ATOC\n", encoding="utf-8")
    _write_json(main_ws / "config" / "wiki_topic_registry.json", {
        "concepts": {
            "ATOC": {"canonical_name": "ATOC", "aliases": ["AgentInfrastructure"], "category": "infra", "wiki_file": "wiki/concepts/ATOC.md"},
            "TagAI": {"canonical_name": "TagAI", "aliases": [], "category": "app", "wiki_file": "wiki/concepts/ATOC.md"},
        },
        "ticks": {"TagClaw": {"canonical_name": "TagClaw", "tracked": True, "wiki_file": "wiki/concepts/ATOC.md"}},
    })
    _write_json(bookmarker_ws / "memory" / "x-sync-latest.json", {
        "data": [
            {
                "id": "1",
                "text": "TagClaw builders are converging on ATOC patterns fast",
                "author": {"username": "builder1"},
                "source_type": "bookmark",
                "createdAt": "2026-06-08T00:00:00Z",
                "public_metrics": {"like_count": 10, "retweet_count": 2, "reply_count": 1},
            },
            {
                "id": "2",
                "text": "TagAI needs better coordination loops around AgentInfrastructure",
                "author": {"username": "builder1"},
                "source_type": "like",
                "createdAt": "2026-06-08T01:00:00Z",
                "public_metrics": {"like_count": 4, "retweet_count": 1, "reply_count": 0},
            },
            {
                "id": "3",
                "text": "A personal note from 0xNought that should not count as reco",
                "author": {"username": "0xNought"},
                "source_type": "tweet",
                "createdAt": "2026-06-08T02:00:00Z",
                "public_metrics": {"like_count": 30},
            },
        ]
    })
    return main_ws, bookmarker_ws


def _load_module(main_ws: Path, bookmarker_ws: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(main_ws)
    os.environ["OPENCLAW_MAIN_WORKSPACE"] = str(main_ws)
    os.environ["OPENCLAW_BOOKMARKER_WORKSPACE"] = str(bookmarker_ws)
    os.environ["OPENCLAW_TRADER_WORKSPACE"] = str(main_ws.parent / "workspace-trader")
    for name in [
        "build_x_reco_artifacts_v1",
        "agency_paths",
        "wiki_registry",
        "fallback_items",
    ]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("build_x_reco_artifacts_v1")


def test_build_xreco_outputs() -> None:
    main_ws, bookmarker_ws = _prep_workspace()
    mod = _load_module(main_ws, bookmarker_ws)
    author_affinity = mod.build_author_affinity(mod.load_items()[1])
    ranked = mod.build_ranked(mod.load_items()[1], author_affinity)
    tas = mod.build_tas_xreco(ranked, used_fallback=False)

    _assert(author_affinity["author_count"] == 1, "only non-owner author should count")
    _assert("builder1" in author_affinity["authors"], "builder1 affinity should be present")
    _assert(ranked["ranked_count"] == 2, "two non-owner items should rank")
    _assert(ranked["hits"] >= 1, "concept/tick hits should be detected")
    _assert(tas["pushes"] == 2, "tas should count ranked pushes")
    _assert(tas["status"] in {"insufficient_data", "ok"}, "tas status should be valid")


if __name__ == "__main__":
    test_build_xreco_outputs()
    print("PASS test_build_xreco_outputs")
