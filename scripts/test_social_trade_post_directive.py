#!/usr/bin/env python3
"""Regression tests for social-trade brief -> post_directive wiring."""

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
    ws = Path(tempfile.mkdtemp(prefix="social-trade-postdir-test-"))
    (ws / "runtime" / "trader").mkdir(parents=True, exist_ok=True)
    return ws


def _load_module(ws: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(ws)
    for name in ["run_main_runtime", "runtime_utils"]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("run_main_runtime")


def test_build_social_trade_post_directive_prefers_post_candidate() -> None:
    ws = _prep_workspace()
    _write_json(ws / "runtime" / "trader" / "PENDING_BRIEF.json", {
        "status": "ok",
        "post_candidate": {
            "text": "Social-trade brief live. Focus: $TagClaw $BUIDL. #TagClaw #DeFi",
            "tick": "TagClaw",
            "source": "social-trade-brief",
            "draft_type": "social_trade_brief",
            "target_key": "tagclaw:post-brief-TagClaw",
        },
    })
    mod = _load_module(ws)
    directive = mod.build_social_trade_post_directive(ws / "runtime")
    _assert(isinstance(directive, dict), "directive should be built")
    _assert(directive["tick"] == "TagClaw", "directive should preserve candidate tick")
    _assert(directive["source"] == "social-trade-brief", "directive should preserve source")


if __name__ == "__main__":
    test_build_social_trade_post_directive_prefers_post_candidate()
    print("PASS test_build_social_trade_post_directive_prefers_post_candidate")
