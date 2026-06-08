#!/usr/bin/env python3
"""Regression tests for execute_tagai_import_v1.py."""

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
    ws = Path(tempfile.mkdtemp(prefix="tagai-exec-test-"))
    (ws / "runtime" / "trader").mkdir(parents=True, exist_ok=True)
    (ws / "config").mkdir(parents=True, exist_ok=True)
    _write_json(ws / "config" / "agency-identity.json", {
        "agent": {"eth_addr": "0x1111111111111111111111111111111111111111"},
        "wallet": {"address": "0x1111111111111111111111111111111111111111"},
    })
    return ws


def _load_module(ws: Path):
    os.environ["OPENCLAW_WORKSPACE"] = str(ws)
    os.environ["OPENCLAW_MAIN_WORKSPACE"] = str(ws)
    os.environ["TAGAI_IMPORT_MAX_PER_RUN"] = "4"
    for name in ["execute_tagai_import_v1", "agency_paths", "runtime_utils_v2"]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("execute_tagai_import_v1")


def test_plain_candidates_are_blocked_honestly() -> None:
    ws = _prep_workspace()
    _write_json(ws / "runtime" / "trader" / "tagai-import-queue.json", {
        "schema": "tagai-import-queue.v1",
        "generated_at": "2026-06-08T00:00:00Z",
        "entries": [
            {"candidate_id": "tagai-import:atoc", "kind": "concept", "value": "ATOC", "priority": 5.0},
            {"candidate_id": "tagai-import:buidl", "kind": "tick", "value": "BUIDL", "priority": 4.0},
        ],
    })
    mod = _load_module(ws)
    mod._fetch_community_detail = lambda tick: {"tick": tick, "token": "0xtoken", "pair": "0xpair", "isImport": False}
    result = mod.execute_imports()
    _assert(result["execution_mode"] == "tagai-community-import-api", "should use real API execution mode")
    _assert(result["ok"] == 0, "plain planning candidates should not fake a successful import")
    _assert(result["total"] == 0, "blocked prereqs should remain neutral for reliability")
    _assert(result["blocked"] == 2, "entries missing import prereqs should be blocked")
    _assert(all(item["status"] == "blocked_missing_import_prereqs" for item in result["items"]),
            "plain candidates should become blocked_missing_import_prereqs")


def test_complete_import_candidate_submits_real_flow() -> None:
    ws = _prep_workspace()
    _write_json(ws / "runtime" / "trader" / "tagai-import-queue.json", {
        "schema": "tagai-import-queue.v1",
        "generated_at": "2026-06-08T00:00:00Z",
        "entries": [
            {
                "candidate_id": "tagai-import:buidl",
                "kind": "tick",
                "value": "BUIDL",
                "priority": 4.0,
                "import_info": {
                    "tick": "BUIDL",
                    "token": "0xToken000000000000000000000000000000000001",
                    "pair": "0xPair0000000000000000000000000000000000001",
                    "transferHash": "0xHash000000000000000000000000000000000000000000000000000000000001",
                    "distributionPeriod": 30,
                    "distributionAmount": "1000000",
                },
                "signature_message": "import BUIDL",
            }
        ],
    })
    mod = _load_module(ws)
    mod._fetch_community_detail = lambda tick: {"tick": tick, "token": "0xToken", "pair": "0xPair", "isImport": False}
    mod._sign_message = lambda message, ctx: "0xSigned"

    calls: list[tuple[str, str, dict | None]] = []

    def fake_curl(method: str, url: str, headers=None, payload=None, timeout_seconds=30):
        calls.append((method, url, payload))
        if url.endswith("/community/importCommunity"):
            return {"ok": True, "http_status": 200, "response": {}, "error": None}
        if "/community/checkImportTokenDeployed?" in url:
            return {"ok": True, "http_status": 200, "response": {"status": "queued", "transferHash": "0xHash"}, "error": None}
        raise AssertionError(f"unexpected url: {url}")

    mod._curl_json = fake_curl
    result = mod.execute_imports()
    _assert(result["ok"] == 1, "complete import candidate should submit successfully")
    _assert(result["total"] == 1, "real API POST attempt should count toward reliability")
    _assert(result["blocked"] == 0, "complete candidate should not be blocked")
    _assert(result["items"][0]["status"] == "ok", "entry should be marked ok")
    _assert(any(url.endswith("/community/importCommunity") for _m, url, _p in calls),
            "executor should call the real import endpoint")
    payloads = [payload for _m, url, payload in calls if url.endswith("/community/importCommunity")]
    _assert(payloads and payloads[0]["signature"] == "0xSigned", "signed request should be sent to import endpoint")


if __name__ == "__main__":
    test_plain_candidates_are_blocked_honestly()
    test_complete_import_candidate_submits_real_flow()
    print("PASS test_plain_candidates_are_blocked_honestly")
    print("PASS test_complete_import_candidate_submits_real_flow")
