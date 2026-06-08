#!/usr/bin/env python3
"""execute_tagai_import_v1.py - execute the Phase-2 TagAI import queue via real API calls.

This script consumes runtime/trader/tagai-import-queue.json and attempts to submit
real TagAI community-import jobs against `/community/importCommunity`.

Important contract note:
  - The public import endpoint requires on-chain import context (`importInfo`) and
    Eth-signature validation. The current Phase-2 planner often only knows
    tick/concept candidates, so this executor is intentionally honest:
      * if a queue entry includes the required import fields, it submits the real API call
      * if those fields are missing, it emits a structured blocked/skipped result
        instead of pretending the import succeeded
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from agency_paths import MAIN_WS

WORKSPACE = MAIN_WS
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"
TAGCLAW_SKILL_ENV = WORKSPACE / "skills" / "tagclaw" / ".env"
WALLET_SKILL_ENV = WORKSPACE / "skills" / "tagclaw-wallet" / ".env"
IDENTITY_PATH = WORKSPACE / "config" / "agency-identity.json"
CREDENTIALS_PATH = Path.home() / ".config" / "tagclaw" / "credentials.json"
WALLET_CLI = Path.home() / "tagclaw-wallet" / "bin" / "wallet.js"

import sys
sys.path.insert(0, str(WORKSPACE / "scripts"))
from runtime_utils_v2 import atomic_write_json, path_ref, read_json  # type: ignore


QUEUE_PATH = RUNTIME_TRADER / "tagai-import-queue.json"
LATEST_PATH = RUNTIME_TRADER / "tagai-import-latest.json"
RESULT_PREFIX = "tagai-import-results-"
MAX_PER_RUN = max(1, int(os.environ.get("TAGAI_IMPORT_MAX_PER_RUN", "4")))
RECENT_HISTORY_FILES = max(1, int(os.environ.get("TAGAI_IMPORT_RECENT_FILES", "12")))
TAGAI_BASE_URL = (os.environ.get("TAGAI_IMPORT_API_BASE") or "https://bsc-api.tagai.fun").rstrip("/")
DEFAULT_POLL_COUNT = max(0, int(os.environ.get("TAGAI_IMPORT_POLL_COUNT", "2")))
DEFAULT_POLL_SLEEP_SECONDS = max(0.0, float(os.environ.get("TAGAI_IMPORT_POLL_SLEEP_SECONDS", "1.5")))
DEFAULT_INCLUDE_BEARER = os.environ.get("TAGAI_IMPORT_INCLUDE_BEARER", "").strip().lower() in {"1", "true", "yes"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            data[key.strip()] = value
    except Exception:
        return {}
    return data


def _recent_imported_ids() -> set[str]:
    seen: set[str] = set()
    docs: list[dict[str, Any]] = []

    latest = read_json(LATEST_PATH)
    if isinstance(latest, dict):
        docs.append(latest)

    history = sorted(
        RUNTIME_TRADER.glob(f"{RESULT_PREFIX}*.json"),
        key=lambda p: p.name,
        reverse=True,
    )[:RECENT_HISTORY_FILES]
    for path in history:
        doc = read_json(path)
        if isinstance(doc, dict):
            docs.append(doc)

    for doc in docs:
        for value in doc.get("imported_candidate_ids") or []:
            if value:
                seen.add(str(value))
        for item in doc.get("items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("status") != "ok":
                continue
            candidate_id = str(item.get("candidate_id") or "").strip()
            if candidate_id:
                seen.add(candidate_id)
    return seen


def _normalize_queue_entries(queue: dict[str, Any]) -> list[dict[str, Any]]:
    entries = queue.get("entries")
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _read_private_key_from_wallet_env() -> str | None:
    env = _read_dotenv(WALLET_SKILL_ENV)
    for key, value in env.items():
        key_upper = key.upper()
        if "PRIVATE_KEY" not in key_upper:
            continue
        value = str(value).strip()
        if value.startswith("0x") and len(value) >= 66:
            return value
    return None


def _resolve_eth_addr() -> str | None:
    wallet_snapshot = read_json(RUNTIME_TRADER / "wallet-snapshot.json") or {}
    for value in [
        wallet_snapshot.get("wallet_address"),
        ((_read_dotenv(TAGCLAW_SKILL_ENV) or {}).get("TAGCLAW_ETH_ADDR")),
        (((read_json(IDENTITY_PATH) or {}).get("agent") or {}).get("eth_addr")),
        (((read_json(IDENTITY_PATH) or {}).get("wallet") or {}).get("address")),
        ((read_json(CREDENTIALS_PATH) or {}).get("address") if CREDENTIALS_PATH.exists() else None),
    ]:
        text = str(value or "").strip()
        if text.startswith("0x"):
            return text
    return None


def _resolve_tagclaw_api_key() -> str | None:
    env = _read_dotenv(TAGCLAW_SKILL_ENV)
    key = str(env.get("TAGCLAW_API_KEY") or "").strip()
    if key:
        return key
    creds = read_json(CREDENTIALS_PATH) if CREDENTIALS_PATH.exists() else None
    if isinstance(creds, dict):
        for field in ("api_key", "apiKey", "token"):
            value = str(creds.get(field) or "").strip()
            if value:
                return value
    return None


def _execution_context() -> dict[str, Any]:
    return {
        "eth_addr": _resolve_eth_addr(),
        "tagclaw_api_key": _resolve_tagclaw_api_key(),
        "wallet_private_key": _read_private_key_from_wallet_env(),
        "wallet_cli": str(WALLET_CLI),
        "tagclaw_skill_env_exists": TAGCLAW_SKILL_ENV.exists(),
        "wallet_skill_env_exists": WALLET_SKILL_ENV.exists(),
    }


def _curl_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    cmd = ["curl", "-sS", "-X", method.upper(), url]
    for key, value in (headers or {}).items():
        cmd.extend(["-H", f"{key}: {value}"])
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False)
        cmd.extend(["-H", "Content-Type: application/json", "-d", body])
    cmd.extend(["-w", "\n__HTTP_STATUS__:%{http_code}"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except Exception as exc:
        return {"ok": False, "http_status": None, "error": str(exc), "response": None}

    raw = proc.stdout or ""
    stderr = (proc.stderr or "").strip()
    status_marker = "__HTTP_STATUS__:"
    http_status = None
    body_text = raw
    if status_marker in raw:
        body_text, suffix = raw.rsplit(status_marker, 1)
        try:
            http_status = int(suffix.strip().splitlines()[0])
        except Exception:
            http_status = None
    body_text = body_text.strip()
    try:
        parsed = json.loads(body_text) if body_text else {}
    except Exception:
        parsed = {"raw": body_text} if body_text else {}

    if proc.returncode == 0 and http_status is not None and 200 <= http_status < 300:
        return {"ok": True, "http_status": http_status, "response": parsed, "error": None}
    return {
        "ok": False,
        "http_status": http_status,
        "response": parsed,
        "error": stderr or (body_text[:500] if body_text else "curl failed"),
    }


def _sign_message(message: str, ctx: dict[str, Any]) -> str | None:
    private_key = str(ctx.get("wallet_private_key") or "").strip()
    wallet_cli = str(ctx.get("wallet_cli") or "")
    if not private_key or not wallet_cli or not Path(wallet_cli).exists():
        return None
    cmd = ["node", wallet_cli, "sign", "--private-key", private_key, "--message", message]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return None
        data = json.loads((proc.stdout or "").strip() or "{}")
        signature = str(data.get("signature") or "").strip()
        return signature or None
    except Exception:
        return None


def _fetch_community_detail(tick: str) -> dict[str, Any] | None:
    query = urlencode({"tick": tick})
    resp = _curl_json("GET", f"{TAGAI_BASE_URL}/community/detail?{query}", timeout_seconds=20)
    if not resp["ok"] or not isinstance(resp.get("response"), dict):
        return None
    return resp["response"]


def _poll_import_status(transfer_hash: str, polls: int, sleep_seconds: float) -> dict[str, Any] | None:
    if not transfer_hash or polls <= 0:
        return None
    out = None
    for idx in range(polls):
        query = urlencode({"transferHash": transfer_hash})
        resp = _curl_json("GET", f"{TAGAI_BASE_URL}/community/checkImportTokenDeployed?{query}", timeout_seconds=20)
        candidate = resp.get("response")
        if resp["ok"] and candidate not in ({}, [], None):
            out = {
                "attempt": idx + 1,
                "http_status": resp.get("http_status"),
                "response": candidate,
            }
            break
        if idx + 1 < polls:
            time.sleep(sleep_seconds)
    return out


def _derive_import_request(entry: dict[str, Any], ctx: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    value = str(entry.get("value") or "").strip()
    detail = _fetch_community_detail(value) if value else None
    import_info = entry.get("import_info") if isinstance(entry.get("import_info"), dict) else {}
    import_info = dict(import_info)

    if value and not import_info.get("tick"):
        import_info["tick"] = value
    if detail:
        if not import_info.get("token") and detail.get("token"):
            import_info["token"] = detail.get("token")
        if not import_info.get("pair") and detail.get("pair"):
            import_info["pair"] = detail.get("pair")
        if detail.get("isImport") is True:
            return None, ["already_imported_community"], {
                "community_detail": {
                    "tick": detail.get("tick") or value,
                    "token": detail.get("token"),
                    "pair": detail.get("pair"),
                    "isImport": detail.get("isImport"),
                    "version": detail.get("version"),
                    "listed": detail.get("listed"),
                }
            }

    missing: list[str] = []
    eth_addr = str(entry.get("eth_addr") or ctx.get("eth_addr") or "").strip()
    if not eth_addr:
        missing.append("eth_addr")

    required_import_keys = ["tick", "token", "pair", "transferHash", "distributionPeriod", "distributionAmount"]
    for key in required_import_keys:
        value_obj = import_info.get(key)
        if value_obj in (None, "", [], {}):
            missing.append(f"import_info.{key}")

    detail_summary = None
    if detail:
        detail_summary = {
            "tick": detail.get("tick") or value,
            "token": detail.get("token"),
            "pair": detail.get("pair"),
            "isImport": detail.get("isImport"),
            "version": detail.get("version"),
            "listed": detail.get("listed"),
        }

    if missing:
        return None, missing, {"community_detail": detail_summary, "import_info": import_info}

    message = str(entry.get("signature_message") or os.environ.get("TAGAI_IMPORT_SIGNATURE_MESSAGE") or "").strip()
    signature = str(entry.get("eth_signature") or os.environ.get("TAGAI_IMPORT_ETH_SIGNATURE") or "").strip()
    if not signature and message:
        signature = str(_sign_message(message, ctx) or "").strip()
    if not signature:
        missing.append("eth_signature")
        return None, missing, {"community_detail": detail_summary, "import_info": import_info}

    payload: dict[str, Any] = {
        "ethAddr": eth_addr,
        "importInfo": import_info,
    }
    payload["signature"] = signature
    if message:
        payload["infoStr"] = message

    extra_body = entry.get("body_overrides") if isinstance(entry.get("body_overrides"), dict) else {}
    payload.update(extra_body)

    headers = {"Content-Type": "application/json"}
    if DEFAULT_INCLUDE_BEARER and ctx.get("tagclaw_api_key"):
        headers["Authorization"] = f"Bearer {ctx['tagclaw_api_key']}"
    extra_headers = entry.get("extra_headers") if isinstance(entry.get("extra_headers"), dict) else {}
    for key, header_value in extra_headers.items():
        headers[str(key)] = str(header_value)

    return {
        "url": f"{TAGAI_BASE_URL}/community/importCommunity",
        "payload": payload,
        "headers": headers,
        "transfer_hash": str(import_info.get("transferHash") or ""),
        "detail": detail_summary,
    }, [], {"community_detail": detail_summary, "import_info": import_info}


def execute_imports() -> dict[str, Any]:
    imported_at = now_iso()
    queue = read_json(QUEUE_PATH) or {}
    queue_entries = _normalize_queue_entries(queue if isinstance(queue, dict) else {})
    queue_ref = path_ref(QUEUE_PATH, WORKSPACE)
    already_imported = _recent_imported_ids()
    ctx = _execution_context()

    attempted = 0
    ok = 0
    failed = 0
    skipped = 0
    blocked = 0
    imported_ids: list[str] = []
    items: list[dict[str, Any]] = []

    for idx, entry in enumerate(queue_entries):
        candidate_id = str(entry.get("candidate_id") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        value = str(entry.get("value") or "").strip()
        priority = entry.get("priority")
        row: dict[str, Any] = {
            "candidate_id": candidate_id,
            "kind": kind,
            "value": value,
            "priority": priority,
            "source": entry.get("source"),
            "source_ref": entry.get("source_ref"),
            "api_action": "community.import",
            "execution": {
                "mode": "tagai-community-import-api",
                "ordinal": idx + 1,
            },
        }

        if not candidate_id or not value:
            blocked += 1
            row.update({
                "status": "blocked_missing_candidate_fields",
                "imported": False,
                "reason": "missing_candidate_id_or_value",
            })
            items.append(row)
            continue

        if candidate_id in already_imported:
            skipped += 1
            row.update({
                "status": "skipped_duplicate",
                "imported": False,
                "reason": "already_imported_recently",
            })
            items.append(row)
            continue

        if ok >= MAX_PER_RUN:
            skipped += 1
            row.update({
                "status": "deferred",
                "imported": False,
                "reason": f"max_per_run_reached:{MAX_PER_RUN}",
            })
            items.append(row)
            continue

        request, missing, debug = _derive_import_request(entry, ctx)
        if debug:
            row["resolution"] = debug

        if missing == ["already_imported_community"]:
            skipped += 1
            row.update({
                "status": "skipped_already_imported_community",
                "imported": False,
                "reason": "community_detail_reports_isImport=true",
            })
            items.append(row)
            continue

        if missing:
            blocked += 1
            row.update({
                "status": "blocked_missing_import_prereqs",
                "imported": False,
                "reason": "missing_real_import_prerequisites",
                "missing": missing,
            })
            items.append(row)
            continue

        attempted += 1
        remote = _curl_json("POST", request["url"], headers=request["headers"], payload=request["payload"], timeout_seconds=45)
        row["request"] = {
            "url": request["url"],
            "payload": request["payload"],
            "headers": sorted(request["headers"].keys()),
        }
        row["remote"] = remote

        if not remote["ok"]:
            failed += 1
            row.update({
                "status": "failed",
                "imported": False,
                "reason": "tagai_import_api_failed",
            })
            items.append(row)
            continue

        status_probe = _poll_import_status(
            request.get("transfer_hash") or "",
            polls=int(entry.get("status_poll_count") or DEFAULT_POLL_COUNT),
            sleep_seconds=float(entry.get("status_poll_sleep_seconds") or DEFAULT_POLL_SLEEP_SECONDS),
        )
        if status_probe is not None:
            row["status_probe"] = status_probe

        ok += 1
        imported_ids.append(candidate_id)
        already_imported.add(candidate_id)
        row.update({
            "status": "ok",
            "imported": True,
            "reason": "tagai_import_api_accepted",
        })
        items.append(row)

    if failed > 0:
        status = "partial"
    elif ok > 0:
        status = "ok"
    elif blocked > 0:
        status = "partial"
    elif queue_entries:
        status = "ok"
    else:
        status = "partial"

    return {
        "schema": "tagai-import-results.v1",
        "imported_at": imported_at,
        "status": status,
        "execution_mode": "tagai-community-import-api",
        "queue_ref": queue_ref,
        "queue_generated_at": queue.get("generated_at") if isinstance(queue, dict) else None,
        "queue_entry_count": len(queue_entries),
        "max_per_run": MAX_PER_RUN,
        "total": attempted,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "blocked": blocked,
        "imported_candidate_ids": imported_ids,
        "items": items,
        "context": {
            "eth_addr_present": bool(ctx.get("eth_addr")),
            "wallet_private_key_present": bool(ctx.get("wallet_private_key")),
            "tagclaw_api_key_present": bool(ctx.get("tagclaw_api_key")),
            "wallet_cli_present": Path(str(ctx.get("wallet_cli") or "")).exists(),
        },
        "notes": [
            "Real API executor for /community/importCommunity is active.",
            "Entries without importInfo or Eth-signature prerequisites are emitted as structured blocked results instead of local fake-success imports.",
            "Duplicate candidates and already-imported communities do not count against import_ok_rate.",
        ],
    }


def main() -> int:
    results = execute_imports()
    result_path = RUNTIME_TRADER / f"{RESULT_PREFIX}{now_stamp()}.json"
    atomic_write_json(result_path, results)

    latest = dict(results)
    latest["latest_result_ref"] = path_ref(result_path, WORKSPACE)
    atomic_write_json(LATEST_PATH, latest)

    print(json.dumps({
        "status": results.get("status"),
        "execution_mode": results.get("execution_mode"),
        "total": results.get("total"),
        "ok": results.get("ok"),
        "failed": results.get("failed"),
        "skipped": results.get("skipped"),
        "blocked": results.get("blocked"),
        "latest_path": path_ref(LATEST_PATH, WORKSPACE),
        "result_path": path_ref(result_path, WORKSPACE),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
