#!/usr/bin/env python3
"""Regression tests for build_tagai_import_queue_v1.py."""

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
    ws = Path(tempfile.mkdtemp(prefix="tagai-queue-test-"))
    (ws / "runtime" / "trader").mkdir(parents=True, exist_ok=True)
    (ws / "runtime" / "shared").mkdir(parents=True, exist_ok=True)
    _write_json(ws / "runtime" / "trader" / "PENDING_BRIEF.json", {
        "cashtags": ["TagClaw", "BUIDL"],
        "theme_names": ["ATOC", "TagAI"],
    })
    _write_json(ws / "runtime" / "shared" / "content-intelligence.json", {
        "concept_scores": {
            "TagClaw": {"score": 3.2},
            "BUIDL": {"score": 2.1},
            "ATOC": {"score": 4.0},
            "TagAI": {"score": 3.4},
        }
    })
    return ws


def _load_module(ws: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(ws)
    os.environ["OPENCLAW_MAIN_WORKSPACE"] = str(ws)
    for name in ["build_tagai_import_queue_v1", "agency_paths", "runtime_utils_v2"]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("build_tagai_import_queue_v1")


def test_build_queue_contract() -> None:
    ws = _prep_workspace()
    mod = _load_module(ws)
    queue = mod.build_queue()
    _assert(queue["schema"] == "tagai-import-queue.v1", "schema should match")
    _assert(queue["entry_count"] >= 2, "queue should contain brief-derived entries")
    _assert(all(entry.get("api_action") == "community.import" for entry in queue["entries"]),
            "queue entries should declare the real import action")
    _assert(queue["entries"][0]["priority"] >= queue["entries"][-1]["priority"], "entries should be sorted by priority")


if __name__ == "__main__":
    test_build_queue_contract()
    print("PASS test_build_queue_contract")
