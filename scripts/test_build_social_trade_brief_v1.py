#!/usr/bin/env python3
"""Regression tests for build_social_trade_brief_v1.py."""

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
    ws = Path(tempfile.mkdtemp(prefix="social-brief-test-"))
    for sub in [
        "runtime/trader",
        "runtime/shared",
        "wiki/queries",
        "scripts",
    ]:
        (ws / sub).mkdir(parents=True, exist_ok=True)
    for name in ["file_to_wiki_query.py", "runtime_utils.py", "runtime_utils_v2.py"]:
        (ws / "scripts" / name).write_text((HERE / name).read_text(encoding="utf-8"), encoding="utf-8")
    _write_json(ws / "runtime" / "trader" / "latest.json", {"status": "ok"})
    _write_json(ws / "runtime" / "trader" / "wallet-snapshot.json", {"portfolio_usd": 123.45})
    _write_json(ws / "runtime" / "trader" / "reward-status.json", {
        "claimable_usd_total": 4.2,
        "claimable": [
            {"tick": "TagClaw", "reward_value_usd": 3.0},
            {"tick": "BUIDL", "reward_value_usd": 1.2},
        ],
    })
    _write_json(ws / "runtime" / "trader" / "tas-trade.json", {"status": "ok", "value": 3.4})
    _write_json(ws / "runtime" / "trader" / "risk-status.json", {"risk_flags": ["none"]})
    _write_json(ws / "runtime" / "shared" / "community-heat.json", {
        "ticks": {
            "TagClaw": {"trend_score": 2.1, "trending_rank": 1},
            "BUIDL": {"trend_score": 1.4, "trending_rank": 2},
        }
    })
    _write_json(ws / "runtime" / "shared" / "wiki-execution-brief.json", {
        "top_themes": [{"name": "ATOC"}, {"name": "TagAI"}],
        "credit_strategy": {"recommended_tokens": ["TagClaw", "BUIDL", "TTAI"]},
    })
    return ws


def _load_module(ws: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(ws)
    os.environ["OPENCLAW_MAIN_WORKSPACE"] = str(ws)
    for name in ["build_social_trade_brief_v1", "agency_paths", "file_to_wiki_query", "runtime_utils_v2"]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("build_social_trade_brief_v1")


def test_build_brief_contract() -> None:
    ws = _prep_workspace()
    mod = _load_module(ws)
    brief = mod.build_brief()
    _assert(brief["schema"] == "trader.social-brief.v1", "schema should match")
    _assert("TagClaw" in brief["cashtags"], "brief should include recommended tokens")
    _assert(brief["claim_recommended"] is True, "claim should be recommended at $4.2")
    _assert(len(brief["summary_bullets"]) >= 3, "brief should have summary bullets")
    _assert(isinstance(brief.get("post_candidate"), dict), "brief should include a post_candidate")
    _assert(str((brief.get("post_candidate") or {}).get("source")) == "social-trade-brief",
            "post_candidate should declare social-trade-brief source")


if __name__ == "__main__":
    test_build_brief_contract()
    print("PASS test_build_brief_contract")
