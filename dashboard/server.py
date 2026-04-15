#!/usr/bin/env python3
"""TagClaw Agent Visualization Dashboard Server — port 7890"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Paths ──────────────────────────────────────────────────────────────────
import argparse as _argparse
_ws_parser = _argparse.ArgumentParser(add_help=False)
_ws_parser.add_argument("--workspace", default=None, help="Path to OpenClaw workspace root")
_ws_parser.add_argument("--port", type=int, default=int(os.environ.get("VIZ_PORT", 7890)))
_ws_args, _ = _ws_parser.parse_known_args()
WORKSPACE = Path(
    _ws_args.workspace
    or os.environ.get("OPENCLAW_WORKSPACE")
    or str(Path.home() / ".openclaw" / "workspace")
)  # resolve via --workspace, $OPENCLAW_WORKSPACE, or default
RUNTIME   = WORKSPACE / "runtime"
STATIC    = Path(__file__).parent / "static"
BOOKMARKER_WORKSPACE = WORKSPACE.parent / "workspace-bookmarker"

app = FastAPI(title="TagClaw Viz", version="1.0.0")
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ── Live TagAI OP/VP cache ─────────────────────────────────────────────────
_TAGAI_BASE_URL = "https://bsc-api.tagai.fun"
_TAGAI_CREDS_PATH = os.path.expanduser("~/.config/tagclaw/credentials.json")
_tagai_cache: dict[str, Any] = {"op": None, "vp": None, "ts": 0.0}
_tagai_lock = threading.Lock()
_TAGAI_TTL = 120  # seconds

_curate_preview_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_curate_preview_lock = threading.Lock()
_CURATE_PREVIEW_TTL = 180  # seconds


def _fetch_live_op_vp() -> dict[str, Any]:
    """Return {"op": float|None, "vp": float|None} from TagAI API, cached."""
    now = time.monotonic()
    with _tagai_lock:
        if now - _tagai_cache["ts"] < _TAGAI_TTL:
            return {"op": _tagai_cache["op"], "vp": _tagai_cache["vp"]}
    try:
        import requests as _req
        creds = json.loads(Path(_TAGAI_CREDS_PATH).read_text())
        api_key = creds.get("api_key") or creds.get("apiKey") or creds.get("token")
        if not api_key:
            return {"op": None, "vp": None}
        resp = _req.get(
            f"{_TAGAI_BASE_URL}/tagclaw/me",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        agent = body.get("agent") or body.get("data", {}).get("agent", {})
        op_val = agent.get("op")
        vp_val = agent.get("vp")
        with _tagai_lock:
            _tagai_cache.update({"op": op_val, "vp": vp_val, "ts": time.monotonic()})
        return {"op": op_val, "vp": vp_val}
    except Exception:
        # On failure, return stale cache if available, else None
        return {"op": _tagai_cache.get("op"), "vp": _tagai_cache.get("vp")}



def _load_curate_fallback_preview() -> dict[str, Any]:
    """Preview current feed-fallback curate actions using bookmarker's live scoring logic."""
    now_mono = time.monotonic()
    with _curate_preview_lock:
        if now_mono - _curate_preview_cache["ts"] < _CURATE_PREVIEW_TTL:
            return _curate_preview_cache["data"] or {"ok": False, "candidates": [], "error": "empty_cache"}

    data: dict[str, Any]
    try:
        creds = json.loads(Path(_TAGAI_CREDS_PATH).read_text())
        api_key = creds.get("api_key") or creds.get("apiKey") or creds.get("token")
        if not api_key:
            data = {"ok": False, "candidates": [], "error": "missing_api_key"}
        else:
            mod_path = WORKSPACE / "scripts" / "execute_social_intent_v2.py"
            spec = importlib.util.spec_from_file_location("execute_social_intent_v2", mod_path)
            if not spec or not spec.loader:
                data = {"ok": False, "candidates": [], "error": "import_spec_failed"}
            else:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                actions = mod.scan_feed_for_curations(api_key)
                candidates = []
                for action in actions[:5]:
                    inline = action.get("_inline_draft") if isinstance(action.get("_inline_draft"), dict) else {}
                    candidates.append({
                        "tweet_id": inline.get("tweetId"),
                        "target_key": inline.get("target_key"),
                        "vp": inline.get("vp"),
                        "reason": inline.get("reason"),
                        "source": inline.get("source"),
                    })
                data = {
                    "ok": True,
                    "candidate_count": len(actions),
                    "candidates": candidates,
                    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
    except Exception as e:
        data = {"ok": False, "candidates": [], "error": str(e)}

    with _curate_preview_lock:
        _curate_preview_cache.update({"data": data, "ts": time.monotonic()})
    return data


# ── Helpers ────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict | list | None:
    """Load a JSON file; return None if missing or invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe(path: str) -> dict | list | None:
    return _load(RUNTIME / path)


def _mtime_iso(path: Path) -> str | None:
    """Return file mtime as ISO string, or None if missing."""
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _newest_mtime_iso(directory: Path) -> str | None:
    """Return mtime of the newest file in a directory, or None."""
    try:
        files = [f for f in directory.iterdir() if f.is_file()]
        if not files:
            return None
        newest = max(files, key=lambda f: f.stat().st_mtime)
        return _mtime_iso(newest)
    except Exception:
        return None


def _parse_dt(date_str: str | None) -> datetime | None:
    """Parse ISO-ish datetimes robustly, preserving explicit timezone offsets."""
    if not date_str:
        return None
    s = str(date_str).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%a %b %d %H:%M:%S +0000 %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


FRESHNESS_PROFILES: dict[str, tuple[float, float, float]] = {
    # fresh, aging, stale cutoffs in minutes (critical beyond stale cutoff)
    "runtime": (120, 360, 1440),
    "dev": (30, 120, 360),
    # Claude Dispatch is on-demand, not heartbeat-driven. Idle results should age slower.
    "dev_idle": (720, 2880, 10080),
    "weekly": (10080, 14400, 20160),
    "monthly": (50400, 64800, 86400),
    "daily": (1440, 2160, 2880),
    # For valid_until semantics: fresh until expiry, then aging/stale/critical by overdue age.
    "valid_until": (0, 1440, 4320),
}


def _bucket_from_age(age_min: float, profile: str = "runtime") -> str:
    fresh_cutoff, aging_cutoff, stale_cutoff = FRESHNESS_PROFILES.get(profile, FRESHNESS_PROFILES["runtime"])
    if age_min < fresh_cutoff:
        return "fresh"
    if age_min < aging_cutoff:
        return "aging"
    if age_min < stale_cutoff:
        return "stale"
    return "critical"


def _age_status(date_str: str | None, max_hours: float) -> str:
    """Return 'ok', 'stale', or 'bootstrap' based on age."""
    dt = _parse_dt(date_str)
    if not dt:
        return "bootstrap"
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return "ok" if age < max_hours else "stale"


def _parse_lint_frontmatter(path: Path) -> dict:
    """Parse YAML-like frontmatter from lint report."""
    result = {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return result
        end = text.index("---", 3)
        for line in text[3:end].strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                try:
                    result[k] = int(v)
                except ValueError:
                    result[k] = v
    except Exception:
        pass
    return result


def _count_files(directory: Path, recursive: bool = True) -> int:
    """Count files in a directory (optionally recursive)."""
    try:
        if not directory.is_dir():
            return 0
        if recursive:
            return sum(1 for f in directory.rglob("*") if f.is_file())
        return sum(1 for f in directory.iterdir() if f.is_file())
    except Exception:
        return 0


def _dir_info(directory: Path) -> dict:
    """Return file_count and newest_file_age_hours for a directory."""
    try:
        if not directory.is_dir():
            return {"file_count": 0, "newest_file_age_hours": None}
        files = [f for f in directory.rglob("*") if f.is_file()]
        if not files:
            return {"file_count": 0, "newest_file_age_hours": None}
        newest_mtime = max(f.stat().st_mtime for f in files)
        age_hours = (datetime.now(timezone.utc).timestamp() - newest_mtime) / 3600
        return {"file_count": len(files), "newest_file_age_hours": round(age_hours, 1)}
    except Exception:
        return {"file_count": 0, "newest_file_age_hours": None}


def _age_hours_from_iso(date_str: str | None) -> float | None:
    dt = _parse_dt(date_str)
    if not dt:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)


def _load_wiki_status() -> dict:
    """Build full wiki system status for the dashboard: raw + wiki layers, ingest pipeline, agents, lint."""
    wiki_dir = WORKSPACE / "wiki"
    raw_dir = WORKSPACE / "raw"
    now = datetime.now(timezone.utc)

    # ── Raw Layer ──
    raw_subdirs = {}
    raw_total = 0
    if raw_dir.is_dir():
        for child in sorted(raw_dir.iterdir()):
            if child.is_dir():
                info = _dir_info(child)
                raw_subdirs[child.name] = info
                raw_total += info["file_count"]

    # For X raw dirs, freshness should reflect last successful sync, not just newest raw file mtime.
    # Otherwise a successful sync with 0 new items misleadingly looks days old.
    x_bundle_manifest = _load(BOOKMARKER_WORKSPACE / "memory" / "raw-x-bundle-manifest.json") or {}
    x_modules = x_bundle_manifest.get("modules") or {}
    x_bundle_completed = x_bundle_manifest.get("completed_at") or x_bundle_manifest.get("started_at")
    for module_name in ("x-bookmarks", "x-tweets", "x-likes", "x-interactions"):
        if module_name not in raw_subdirs:
            continue
        module_data = x_modules.get(module_name) or {}
        state = module_data.get("state") or {}
        age_h = _age_hours_from_iso(state.get("last_sync"))
        if age_h is None and module_data.get("status") == "ok":
            age_h = _age_hours_from_iso(x_bundle_completed)
        if age_h is not None:
            raw_subdirs[module_name]["newest_file_age_hours"] = age_h
            raw_subdirs[module_name]["freshness_source"] = "last_sync"

    # ── Wiki Layer ──
    wiki_subdirs = {}
    wiki_total = 0
    ROLE_MAP = {
        "concepts": "compiled", "identity": "manual", "execution": "compiled",
        "synthesis": "compiled", "tagclaw-platform": "runtime",
        "queries": "append-only", "social": "compiled", "lint": "runtime",
    }
    if wiki_dir.is_dir():
        for child in sorted(wiki_dir.iterdir()):
            if child.is_dir():
                info = _dir_info(child)
                role = ROLE_MAP.get(child.name, "compiled")
                wiki_subdirs[child.name] = {**info, "role": role}
                wiki_total += info["file_count"]

    # ── Execution Brief ──
    brief_data = _load(RUNTIME / "shared" / "wiki-execution-brief.json") or {}
    compiled_at = brief_data.get("compiled_at")
    valid_until = brief_data.get("valid_until")
    top_themes = [
        {"name": th.get("name", ""), "heat_score": th.get("heat_score", 0), "agent_action": th.get("agent_action", "")}
        for th in (brief_data.get("top_themes") or [])[:3]
    ]
    credit_strategy = brief_data.get("credit_strategy") or {}

    execution_brief = {
        "compiled_at": compiled_at,
        "valid_until": valid_until,
        "top_themes": top_themes,
        "credit_strategy": credit_strategy,
    }

    # ── Ingest Pipeline (8 entries) ──
    def _pipeline_entry(pid: str, name: str, script: str, freq: str,
                        raw_output: str, wiki_output: str,
                        last_run: str | None, stale_hours: float) -> dict:
        status = _age_status(last_run, stale_hours)
        age_h = None
        dt = _parse_dt(last_run)
        if dt:
            age_h = round((now - dt).total_seconds() / 3600, 1)
        return {
            "id": pid, "name": name, "script": script, "freq": freq,
            "raw_output": raw_output, "wiki_output": wiki_output,
            "last_run": last_run, "age_hours": age_h, "status": status,
        }

    # 1. x_sync
    xs_last = _newest_mtime_iso(raw_dir / "x-tweets") if (raw_dir / "x-tweets").is_dir() else None

    # 2. platform_snapshot
    manifest = _load(wiki_dir / "tagclaw-platform" / "raw" / "manifest.json") or {}
    ps_last = manifest.get("fetched_at")

    # 3. docs_ingest
    di_last = _newest_mtime_iso(wiki_dir / "concepts") if (wiki_dir / "concepts").is_dir() else None

    # 4. topic_heatmap
    heatmap = _load(RUNTIME / "bookmarker" / "topic-heatmap.json") or {}
    th_last = heatmap.get("generated_at")

    # 5. execution_brief
    eb_last = compiled_at

    # 6. social_snapshot
    ss_path = wiki_dir / "social" / "trending.md"
    ss_last = _mtime_iso(ss_path)

    # 7. lint
    lint_path = wiki_dir / "lint" / "latest-report.md"
    lint_last = _mtime_iso(lint_path)
    lint_fm = _parse_lint_frontmatter(lint_path) if lint_path.exists() else {}

    # 8. query_writeback
    qw_last = _newest_mtime_iso(wiki_dir / "queries") if (wiki_dir / "queries").is_dir() else None

    ingest_pipeline = [
        _pipeline_entry("x_sync", "X Sync", "bird-x-sync.py", "bookmark-sync cron",
                         "raw/x-tweets/ + raw/x-bookmarks/", "wiki/synthesis/tweets/ + wiki/synthesis/people/",
                         xs_last, 48),
        _pipeline_entry("platform_snapshot", "Platform Snapshot", "fetch_tagclaw_platform_wiki.py", "monthly",
                         "—", "wiki/tagclaw-platform/raw/", ps_last, 720),
        _pipeline_entry("docs_ingest", "Docs Ingest", "wiki_ingest_docs_monthly_v1.py", "monthly",
                         "raw/external-docs/", "wiki/concepts/", di_last, 720),
        _pipeline_entry("topic_heatmap", "Topic Heatmap", "build_wiki_topic_heatmap_v1.py", "bookmarker heartbeat",
                         "raw/x-interactions/", "runtime/bookmarker/topic-heatmap.json", th_last, 24),
        _pipeline_entry("execution_brief", "Execution Brief", "build_wiki_execution_brief_v1.py", "weekly",
                         "—", "wiki/execution/weekly-brief.md + runtime/shared/wiki-execution-brief.json", eb_last, 168),
        _pipeline_entry("social_snapshot", "Social Snapshot", "build_wiki_social_snapshot_v1.py", "weekly",
                         "—", "wiki/social/trending.md", ss_last, 168),
        _pipeline_entry("lint", "Wiki Lint", "wiki_lint_v1.py", "weekly",
                         "—", "wiki/lint/latest-report.md", lint_last, 168),
        _pipeline_entry("query_writeback", "Query Writeback", "write_wiki_query.py", "per heartbeat",
                         "—", "wiki/queries/", qw_last, 4),
        _pipeline_entry("community_heat", "Community Heat", "refresh_wiki_community_heat_v1.py", "per heartbeat",
                         "—", "runtime/shared/community-heat.json",
                         (_load(RUNTIME / "shared" / "community-heat.json") or {}).get("computed_at"), 6),
    ]

    # Annotate lint pipeline entry with findings count (separate from run-staleness)
    ingest_pipeline[6]["has_findings"] = lint_fm.get("broken_links_count", 0) > 0

    # ── Agent Wiki Status ──
    def _wiki_fields(data: dict) -> dict:
        return {k: v for k, v in data.items() if k.startswith("wiki_")}

    main_latest = _load(RUNTIME / "main" / "latest.json") or {}
    bm_latest = _load(RUNTIME / "bookmarker" / "latest.json") or {}
    trader_latest = _load(RUNTIME / "trader" / "latest.json") or {}

    agent_wiki_status = {
        "main": _wiki_fields(main_latest),
        "bookmarker": _wiki_fields(bm_latest),
        "trader": trader_latest.get("wiki") or _wiki_fields(trader_latest),
    }

    # ── Lint Summary ──
    # Prefer JSON artifact (has health_score); fall back to frontmatter
    lint_json = _load(RUNTIME / "shared" / "wiki-lint-status.json") or {}
    lint_summary = {
        "generated_at": lint_json.get("generated_at") or lint_fm.get("generated_at"),
        "concepts_checked": lint_fm.get("concepts_checked", 0),
        "broken_links_count": lint_json.get("broken_links_count", lint_fm.get("broken_links_count", 0)),
        "stale_count": lint_json.get("stale_count", lint_fm.get("stale_count", 0)),
        "orphan_count": lint_json.get("orphan_count", lint_fm.get("orphan_count", 0)),
        "empty_count": lint_json.get("empty_count", lint_fm.get("empty_count", 0)),
        "health_score": lint_json.get("health_score"),
    }

    # ── Community Heat ──
    heat_data = _load(RUNTIME / "shared" / "community-heat.json") or {}
    heat_ticks = heat_data.get("ticks", {})
    community_heat = {
        "computed_at": heat_data.get("computed_at"),
        "version": heat_data.get("version"),
        "source_health": heat_data.get("source_health", "unavailable"),
        "top_rising": heat_data.get("top_rising", []),
        "top_declining": heat_data.get("top_declining", []),
        "ticks": {
            tick: {
                "trend": v.get("trend"),
                "trend_score": v.get("trend_score"),
                "trending_rank": v.get("trending_rank"),
                "market_cap_rank": v.get("market_cap_rank"),
                "trend_basis": v.get("trend_basis"),
                "heat_rank": v.get("heat_rank"),
                "social_score": v.get("social_score"),
                "trade_score": v.get("trade_score"),
                "composite_score": v.get("composite_score"),
                "social_momentum": v.get("social_momentum"),
                "trade_momentum": v.get("trade_momentum"),
                "data_coverage": v.get("data_coverage"),
                "social_posts_24h": v.get("social_posts_24h"),
                "trade_count_24h": v.get("trade_count_24h"),
            }
            for tick, v in heat_ticks.items()
        },
    }

    # ── PoB Unclaimed ──
    trader_tas = _load(RUNTIME / "trader" / "tas-trade.json") or {}
    pob_unclaimed_usd = trader_tas.get("claimable_usd_raw")
    pob_norm = trader_tas.get("claimable_usd_norm")

    # ── Contract Verifier Health (P2) ──
    verify_path = RUNTIME / "shared" / "wiki-contract-verify.json"
    verify_data = _load(verify_path) or {}
    verify_mtime = _mtime_iso(verify_path)
    verify_age_h = None
    if verify_mtime:
        dt = _parse_dt(verify_mtime)
        if dt:
            verify_age_h = round((now - dt).total_seconds() / 3600, 1)

    # Extract top failing checks for operator visibility
    failing_checks = []
    for c in (verify_data.get("checks") or []):
        if not c.get("ok"):
            failing_checks.append(c.get("check", "unknown"))

    contract_health = {
        "status": verify_data.get("status", "unknown"),
        "pass": verify_data.get("pass", 0),
        "fail": verify_data.get("fail", 0),
        "verified_at": verify_data.get("verified_at"),
        "age_hours": verify_age_h,
        "top_failures": failing_checks[:5],
    }

    # ── Contract Alert (P4) ──
    alert_data = _load(RUNTIME / "shared" / "wiki-contract-alert.json") or {}
    contract_health["alert_severity"] = alert_data.get("severity", "unknown")
    contract_health["alert_message"] = alert_data.get("message", "")

    return {
        "raw_layer": {"subdirs": raw_subdirs, "total_files": raw_total},
        "wiki_layer": {"subdirs": wiki_subdirs, "total_files": wiki_total},
        "execution_brief": execution_brief,
        "ingest_pipeline": ingest_pipeline,
        "agent_wiki_status": agent_wiki_status,
        "lint": lint_summary,
        "community_heat": community_heat,
        "contract_health": contract_health,
        "pob_unclaimed_usd": pob_unclaimed_usd,
        "pob_norm": pob_norm,
    }


def _classify_social_actor(ev: dict[str, Any]) -> str:
    """Infer which agent authored a social action history item.

    Uses explicit actor field first (v2 schema), then falls back to draft_ref
    and source/agent heuristics for legacy records.
    """
    # v2: explicit actor field
    actor = str(ev.get("actor") or "").lower()
    if actor in ("main", "bookmarker"):
        return actor

    # Legacy fallback: draft_ref
    draft_ref = str(ev.get("draft_ref") or "").lower()
    if "bookmarker" in draft_ref:
        return "bookmarker"
    if "main" in draft_ref:
        return "main"

    for key in ("source_agent", "source", "agent"):
        val = str(ev.get(key) or "").lower()
        if "bookmarker" in val:
            return "bookmarker"
        if "main" in val:
            return "main"

    return "main"


def _split_social_actions(items: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    grouped = {"main": [], "bookmarker": []}
    for ev in (items or []):
        grouped[_classify_social_actor(ev)].append(ev)
    return grouped


def _main_control_feedback(items: list[dict[str, Any]] | None, social_intent: dict | None) -> list[dict[str, Any]]:
    """Prefer bookmarker feedback tied to the current main social-intent cycle/strategy."""
    events = [ev for ev in (items or []) if isinstance(ev, dict)]
    if not events:
        return []
    cycle_id = str((social_intent or {}).get("cycle_id") or "")
    strategy_id = str((social_intent or {}).get("strategy_id") or "")
    matched = [
        ev for ev in events
        if (cycle_id and str(ev.get("cycle_id") or "") == cycle_id)
        or (strategy_id and str(ev.get("strategy_id") or "") == strategy_id)
    ]
    return list(reversed((matched or events)[-20:]))


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "" or str(v).lower() == "partial":
            return None
        return float(v)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _build_curation_vp_panel(social_hist: dict[str, Any] | None, hours: int = 24) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(hours=hours)
    items = (social_hist or {}).get("items") if isinstance((social_hist or {}).get("items"), list) else []

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "curate":
            continue
        if item.get("result_status") not in {"ok", "noop"}:
            continue
        ts = _parse_dt(item.get("executed_at") or item.get("curated_at") or item.get("ts"))
        if not ts or ts < cutoff:
            continue
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        vp = _to_int(item.get("vp_spent"))
        if vp is None:
            vp = _to_int(item.get("vp"))
        if vp is None:
            vp = _to_int(request.get("vp"))
        reason = str(item.get("note") or request.get("reason") or "").strip()
        rows.append({
            "executed_at": item.get("executed_at") or item.get("curated_at") or item.get("ts"),
            "target_key": item.get("target_key"),
            "tweet_id": request.get("tweetId") or request.get("tweet_id"),
            "vp": vp,
            "reason": reason,
            "cycle_id": item.get("cycle_id"),
            "strategy_id": item.get("strategy_id"),
        })

    buckets = []
    vp_values = [int(r["vp"]) for r in rows if r.get("vp") is not None]
    total = len(rows)
    for vp in range(1, 11):
        count = sum(1 for v in vp_values if v == vp)
        buckets.append({
            "vp": vp,
            "count": count,
            "share_pct": round((count / total) * 100, 1) if total else 0.0,
        })

    unknown_vp = sum(1 for r in rows if r.get("vp") is None)
    total_vp = round(sum(vp_values), 2)
    avg_vp = round(total_vp / len(vp_values), 2) if vp_values else None
    non_one_count = sum(1 for v in vp_values if v > 1)

    return {
        "window_hours": hours,
        "total_curations": total,
        "known_vp_curations": len(vp_values),
        "unknown_vp_curations": unknown_vp,
        "total_vp_spent": total_vp,
        "avg_vp": avg_vp,
        "max_vp": max(vp_values) if vp_values else None,
        "min_vp": min(vp_values) if vp_values else None,
        "non_one_count": non_one_count,
        "non_one_share_pct": round((non_one_count / len(vp_values)) * 100, 1) if vp_values else 0.0,
        "unique_levels": sorted(set(vp_values)),
        "buckets": buckets,
        "recent": list(reversed(rows[-8:])),
    }


def _load_strategy_ledger() -> dict[str, dict[str, Any]]:
    ledger_path = WORKSPACE / "runtime" / "shared" / "strategy-ledger.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not ledger_path.exists():
        return out
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            cycle_id = obj.get("cycle_id") or obj.get("generated_at")
            if not cycle_id:
                continue
            out[cycle_id] = {
                "strategy_id": obj.get("strategy_id"),
                "strategy_action": obj.get("strategy_action"),
                "planning_focus": obj.get("planning_focus"),
                "target_metrics": obj.get("target_metrics") or [],
                "confidence": _to_float(obj.get("confidence")),
            }
    except Exception:
        return {}
    return out


def _load_tas_history(limit: int = 50, strategy_cycle_count: int | None = None, last_cycle_id: str | None = None) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []

    # Source 1: canonical JSONL (dense, per-cycle) — preferred
    jsonl_path = WORKSPACE / "runtime" / "shared" / "tas-history.jsonl"
    if jsonl_path.exists():
        try:
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("ts"):
                        points.append({
                            "ts": obj["ts"],
                            "tas_total": _to_float(obj.get("tas_total")),
                            "tas_social": _to_float(obj.get("tas_social")),
                            "tas_trade": _to_float(obj.get("tas_trade")),
                            "cycle_count": _to_int(obj.get("cycle_count")),
                            # P3 2026-04-10: pass status metadata for chart rendering
                            "status": obj.get("status", "ok"),
                            "history_eligible": obj.get("history_eligible", True),
                        })
                except Exception:
                    continue
        except Exception:
            pass

    # Source 2: legacy results.tsv fallback (sparse) — only if JSONL is empty
    if not points:
        tsv_path = WORKSPACE / "memory" / "results.tsv"
        if tsv_path.exists():
            try:
                for raw in tsv_path.read_text(encoding="utf-8").splitlines():
                    if not raw.strip():
                        continue
                    parts = raw.split("\t")

                    # Legacy schema: epoch, timestamp, TAS, TAS_social, TAS_economic, TAS_trade, ...
                    if len(parts) >= 6 and parts[1].startswith("20"):
                        points.append({
                            "ts": parts[1],
                            "tas_total": _to_float(parts[2]),
                            "tas_social": _to_float(parts[3]),
                            "tas_trade": _to_float(parts[5]),
                            "cycle_count": None,
                        })
                        continue

                    # Mid schema: ts, OP, VP, TAS_social, TAS_trade, TAS_total, mode, desc
                    if len(parts) >= 6 and parts[0].startswith("20") and _to_float(parts[1]) is not None and _to_float(parts[5]) is not None:
                        points.append({
                            "ts": parts[0],
                            "tas_total": _to_float(parts[5]),
                            "tas_social": _to_float(parts[3]),
                            "tas_trade": _to_float(parts[4]),
                            "cycle_count": None,
                        })
                        continue

                    # Lightweight schema: ts, main, heartbeat, TAS=1.292, ...
                    if len(parts) >= 4 and parts[0].startswith("20") and parts[3].startswith("TAS="):
                        points.append({
                            "ts": parts[0],
                            "tas_total": _to_float(parts[3].split("=", 1)[1]),
                            "tas_social": None,
                            "tas_trade": None,
                            "cycle_count": None,
                        })
            except Exception:
                pass

    # Deduplicate by ts (last wins), sort chronologically
    seen: dict[str, dict[str, Any]] = {}
    for p in points:
        if p.get("ts"):
            seen[p["ts"]] = p
    points = sorted(seen.values(), key=lambda x: x["ts"])

    # Precise cycle_count backfill for the current experiment window:
    # anchor at strategy_experiment.last_cycle_id and walk backward exactly once per TAS point.
    if points and strategy_cycle_count and last_cycle_id:
        anchor_idx = next((i for i, p in enumerate(points) if p.get("ts") == last_cycle_id), None)
        if anchor_idx is not None:
            cycle_no = strategy_cycle_count
            idx = anchor_idx
            while idx >= 0 and cycle_no >= 1:
                if points[idx].get("cycle_count") is None:
                    points[idx]["cycle_count"] = cycle_no
                idx -= 1
                cycle_no -= 1

    # Join per-cycle strategy metadata for dashboard drill-down.
    strategy_ledger = _load_strategy_ledger()
    if strategy_ledger:
        for p in points:
            meta = strategy_ledger.get(p.get("ts") or "")
            if meta:
                p.update(meta)

    return points[-limit:]


def _is_within_hours(date_str: str, hours: int) -> bool:
    """Check if a date string represents a time within the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for fmt in ("%a %b %d %H:%M:%S +0000 %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except ValueError:
            continue
    return False


def _parse_x_tweets(hours: int = 24, limit: int = 20) -> list[dict]:
    """Parse memory/x-latest-tweets.md → tweets within last N hours.
    Supports both the new compact format (## id | date) and old detailed format (### id | date).
    Falls back to most recent `limit` if none found in the time window.
    """
    path = WORKSPACE / "memory" / "x-latest-tweets.md"
    if not path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    tweets: list[dict] = []
    current: dict | None = None

    def _try_parse_date(s: str):
        for fmt in ("%a %b %d %H:%M:%S +0000 %Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            # New compact format: ## 1234567890 | Fri Mar 27 08:27:32 +0000 2026
            m = re.match(r'^## (\d{10,}) \| (.+)$', line)
            if m:
                if current:
                    tweets.append(current)
                dt = _try_parse_date(m.group(2))
                current = {
                    "id": m.group(1), "date": m.group(2).strip(),
                    "dt": dt, "type": "推文", "content": "", "topics": [], "interactions": "",
                }
                continue
            # Old detailed format: ### id | date
            m2 = re.match(r'^### (\d{10,}) \| (.+)$', line)
            if m2:
                if current:
                    tweets.append(current)
                dt = _try_parse_date(m2.group(2))
                current = {
                    "id": m2.group(1), "date": m2.group(2).strip(),
                    "dt": dt, "type": "", "content": "", "topics": [], "interactions": "",
                }
                continue
            if current is None:
                continue
            # In compact format, first non-header non-blank line = content
            if current["content"] == "" and line.strip() and not line.startswith('#') and not line.startswith('>') and not line.startswith('*'):
                current["content"] = line.strip()[:150]
                continue
            tm = re.match(r'^\*\*类型\*\*[:：]\s*(.+)$', line)
            if tm:
                current["type"] = tm.group(1).strip(); continue
            cm = re.match(r'^\*\*(?:内容|标题)\*\*[:：]\s*(.+)$', line)
            if cm and not current["content"]:
                current["content"] = cm.group(1).strip()[:150]; continue
            im = re.match(r'^\*\*互动\*\*[:：]\s*(.+)$', line)
            if im:
                current["interactions"] = im.group(1).strip(); continue
            hm = re.match(r'^\*\*话题\*\*[:：]\s*(.+)$', line)
            if hm:
                current["topics"] = [w.lstrip('#') for w in hm.group(1).split() if w.startswith('#')]
    except Exception:
        pass
    if current:
        tweets.append(current)

    # Filter to time window; fallback to most recent if window is empty
    in_window = [t for t in tweets if t.get("dt") and t["dt"] >= cutoff]
    result = in_window if in_window else tweets
    # Strip internal dt field before returning
    for t in result:
        t.pop("dt", None)
    return result[:limit]


def _parse_x_bookmarks(hours: int = 168, limit: int = 20) -> list[dict]:
    """Parse memory/x-bookmarks-categorized.md → bookmarks within last N hours.

    Supports two entry formats written by different sync scripts:
      Format A (legacy): ### YYYY-MM-DD | @author | 得分:N
      Format B (bird-x-sync): ## [YYYY-MM-DD] [Category] Title

    Falls back to most recent `limit` entries if none found in time window.
    """
    path = WORKSPACE / "memory" / "x-bookmarks-categorized.md"
    if not path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    bookmarks: list[dict] = []
    current_category = ""
    current: dict | None = None

    def _try_parse_date(s: str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(s.strip()[:10], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    try:
        for line in path.read_text(encoding="utf-8").splitlines():

            # ── Format A: ### YYYY-MM-DD | @author | 得分:N ──
            entry_a = re.match(r'^### (.+?) \| (.+)$', line)
            if entry_a:
                if current:
                    bookmarks.append(current)
                date_str = entry_a.group(1).strip()
                rest = entry_a.group(2).strip()
                # rest may be "@author | 得分:N" or just a title
                parts = [p.strip() for p in rest.split('|')]
                author = parts[0].lstrip('@') if parts else ""
                dt = _try_parse_date(date_str)
                current = {
                    "date": date_str,
                    "title": rest,
                    "category": current_category,
                    "author": author,
                    "dt": dt,
                    "summary": "", "url": "",
                }
                continue

            # ── Format B: ## [YYYY-MM-DD] [Category] Title ──
            entry_b = re.match(r'^## \[(\d{4}-\d{2}-\d{2})\] \[([^\]]+)\] (.+)$', line)
            if entry_b:
                if current:
                    bookmarks.append(current)
                date_str = entry_b.group(1).strip()
                category = entry_b.group(2).strip()
                title = entry_b.group(3).strip()
                dt = _try_parse_date(date_str)
                current_category = category
                current = {
                    "date": date_str,
                    "title": title,
                    "category": category,
                    "author": "",
                    "dt": dt,
                    "summary": "", "url": "",
                }
                continue

            # ── Generic ## section header (not an entry) ──
            cat_m = re.match(r'^## (.+)$', line)
            if cat_m and not entry_b:
                current_category = cat_m.group(1).strip()
                # Do NOT flush current here — ## lines may appear inside entries
                continue

            if current is None:
                continue

            # Author field
            am = re.match(r'^\*\*作者\*\*[:：]\s*(.+)$', line)
            if am:
                if not current["author"]:
                    current["author"] = am.group(1).strip()
                continue

            # Format B: "Primary tag: X" line → use as category hint
            pt = re.match(r'^Primary tag:\s*(.+)$', line)
            if pt and not current.get("category"):
                current["category"] = pt.group(1).strip()
                continue

            # URL line: → https://...
            um = re.match(r'^→\s*(https?://\S+)', line)
            if um:
                current["url"] = um.group(1).strip()
                # Also extract @username from URL for Format B entries
                if not current["author"]:
                    um2 = re.search(r'x\.com/([^/]+)/', um.group(1))
                    if um2:
                        current["author"] = '@' + um2.group(1)
                continue
            um3 = re.match(r'^\*\*URL\*\*[:：]\s*(https?://\S+)', line)
            if um3:
                current["url"] = um3.group(1).strip(); continue

            # Summary fields
            sm = re.match(r'^\*\*(?:内容|内容摘要)\*\*[:：]\s*(.+)$', line)
            if sm and not current["summary"]:
                current["summary"] = sm.group(1).strip()[:200]; continue

            # For Format B entries: first non-empty, non-keyword line becomes summary
            stripped = line.strip()
            if (stripped
                    and not stripped.startswith('#')
                    and not stripped.startswith('**')
                    and not stripped.startswith('Primary tag:')
                    and not stripped.startswith('Keywords:')
                    and not stripped.startswith('From my X bookmarks:')
                    and not stripped.startswith('My take:')
                    and not stripped.startswith('→')
                    and not stripped.startswith('#bookmark')
                    and not current["summary"]):
                # Use "From my X bookmarks:" content as summary
                pass  # already handled above; keep loop clean

            # Explicit "From my X bookmarks:" prefix → grab as summary
            fmx = re.match(r'^From my X bookmarks:\s*(.+)$', stripped)
            if fmx and not current["summary"]:
                current["summary"] = fmx.group(1).strip()[:200]; continue

    except Exception:
        pass

    if current:
        bookmarks.append(current)

    # Sort by date descending
    bookmarks.sort(key=lambda b: b.get("dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Filter to time window; fallback to most recent if window is empty
    in_window = [b for b in bookmarks if b.get("dt") and b["dt"] >= cutoff]
    result = in_window if in_window else bookmarks
    for b in result:
        b.pop("dt", None)
    return result[:limit]


def _load_twin_recognition() -> dict:
    path = WORKSPACE / 'memory' / 'twin-recognition.json'
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _load_x_sync() -> dict:
    """Read x-sync status and timestamp, preferring bookmarker runtime over memory."""
    runtime_path = Path(__file__).parent.parent.parent / 'runtime' / 'bookmarker' / 'latest.json'
    memory_path = WORKSPACE / "memory" / "x-sync-latest.json"

    runtime_at = ''
    runtime_status = 'unknown'
    if runtime_path.exists():
        try:
            r = json.loads(runtime_path.read_text())
            runtime_at = r.get('updated_at') or r.get('generated_at') or ''
            runtime_status = r.get('status') or 'unknown'
        except Exception:
            pass

    memory_at = ''
    memory_status = 'unknown'
    if memory_path.exists():
        try:
            m = json.loads(memory_path.read_text())
            memory_at = m.get('fetched_at') or ''
            memory_status = m.get('status') or 'unknown'
        except Exception:
            pass

    best_at = runtime_at if runtime_at >= memory_at else memory_at
    best_status = runtime_status if runtime_at >= memory_at else memory_status

    return {
        'x_sync_status': best_status,
        'x_sync_at': best_at,
    }


# ── Trader event filter (module-level for reuse) ───────────────────────────

_ONCHAIN_ACTIONS   = {"buy", "sell", "swap", "stake", "unstake", "transfer"}
_CLAIM_FINAL_STATUS = {"settled", "completed", "confirmed", "claimed"}


def _trader_ev_is_real(ev: dict) -> bool:
    """
    Only include trader events that represent a real on-chain / wallet outcome:
    - buy/sell/swap/stake: must have tx_hash
    - claim: must have tx_hash OR final_status in settled/completed/confirmed
      OR status=ok + order_id present
    - Other actions: tx_hash present
    """
    action       = str(ev.get("action") or "").lower()
    status       = str(ev.get("status") or "").lower()
    tx_hash      = (ev.get("tx_hash") or "").strip()
    final_status = str(ev.get("final_status") or "").lower()

    if action in _ONCHAIN_ACTIONS:
        return bool(tx_hash)

    if action == "claim":
        order_id = (ev.get("order_id") or "").strip()
        if not order_id:
            remote_data = (ev.get("remote") or {}).get("response") or {}
            if isinstance(remote_data, dict) and "data" in remote_data:
                remote_data = remote_data["data"]
            order_id = (remote_data.get("orderId") or "").strip()
        return (bool(tx_hash) or
                (status == "ok" and bool(order_id)) or
                (final_status in _CLAIM_FINAL_STATUS))

    return bool(tx_hash)


def _load_trade_actions(limit: int = 20) -> list[dict]:
    """Read last 7 days of execution ledger, filter real events, dedup, return last `limit`."""
    today = datetime.now(timezone.utc).date()
    items: list[dict] = []
    for delta in range(7):
        d = today - timedelta(days=delta)
        path = RUNTIME / "trader" / f"executions-{d}.json"
        rec = _load(path) or {}
        for ev in (rec.get("items") or []):
            if not _trader_ev_is_real(ev):
                continue
            tx_hash = (ev.get("tx_hash") or "")
            items.append({
                "ts":          ev.get("ts") or "",
                "action":      ev.get("action", "?"),
                "tick":        ev.get("tick", ""),
                "amount":      ev.get("amount"),
                "amount_unit": ev.get("amount_unit", ""),
                "usd":         ev.get("usd"),
                "tx_hash":     tx_hash[:16] if tx_hash else "",
                "status":      ev.get("status", ""),
                "_order_id":   (ev.get("order_id") or
                               (((ev.get("remote") or {}).get("response") or {}).get("data") or {}).get("orderId") or ""),
                "_id":         ev.get("id", ""),
            })

    # Sort descending
    items.sort(key=lambda x: x.get("ts", ""), reverse=True)

    # Deduplicate by order_id (claims) or id
    seen: set[str] = set()
    deduped: list[dict] = []
    for it in items:
        uid = it.get("_id") or ""
        if it["action"].lower() == "claim" and it.get("_order_id"):
            uid = f"claim:{it['_order_id']}"
        if not uid:
            uid = f"{it['ts']}:{it['action']}:{it['tick']}"
        if uid not in seen:
            seen.add(uid)
            out = {k: v for k, v in it.items() if not k.startswith("_")}
            deduped.append(out)

    return deduped[:limit]


# ── Social Pipeline Builders ───────────────────────────────────────────────

def _build_bookmarker_social_pipeline(
    source_health: dict, topic_brief: dict, content_candidates: dict,
    auto_intent: dict, drafts: dict, execution: dict, write_state: dict,
    *, main_social_intent: dict | None = None, main_last_decision: dict | None = None,
) -> dict:
    """Build bookmarker social execution pipeline summary for dashboard (6-step v2)."""
    # Step 1: X Sync
    sh_status = source_health.get("bird") or source_health.get("status") or "—"
    sh_source = source_health.get("source_class", "—")
    sh_updated = source_health.get("updated_at", "")
    # Determine primary active source
    active_src = "—"
    for src_key in ("bird", "browser_relay", "xurl"):
        if source_health.get(src_key) == "ok":
            active_src = src_key
            break

    # Step 2: Topic Brief
    tb_keywords = topic_brief.get("keywords") or []
    tb_summary = topic_brief.get("summary", "")
    tb_urgency = topic_brief.get("content_urgency", "")

    # Step 3: Content Candidates
    raw_cands = content_candidates.get("items") or content_candidates.get("candidates") or []
    # Filter out empty placeholder items (publish_ready=False without real content)
    real_cands = [c for c in raw_cands if c.get("publish_ready") is not False or c.get("title") or c.get("url")]
    cand_types: dict[str, int] = {}
    for c in real_cands:
        ct = c.get("type", "unknown")
        cand_types[ct] = cand_types.get(ct, 0) + 1

    # Step 4: Social Drafts
    draft_list = drafts.get("drafts") or []
    draft_types: dict[str, int] = {}
    for d in draft_list:
        dt = d.get("type", "unknown")
        draft_types[dt] = draft_types.get(dt, 0) + 1
    drafts_meta = drafts.get("meta") or {}
    x_items_seen = drafts_meta.get("x_items_seen", 0)

    # Step 5: Autonomy Intent
    ai_mode = auto_intent.get("mode", "—")
    ai_reason = auto_intent.get("reason", "")
    ai_recommended = auto_intent.get("recommended_actions") or []
    ai_tas = auto_intent.get("tas_social_value")
    ai_op = auto_intent.get("op")
    thresholds = auto_intent.get("thresholds") or {}

    # Step 6: Execution (includes breaker + filtering)
    exec_status = execution.get("status", "—")
    exec_summary = execution.get("summary") or {}
    exec_at = execution.get("generated_at", "")
    exec_mode = execution.get("autonomy_mode", "")
    breaker = write_state.get("breaker") or {}
    breaker_state = breaker.get("state", "—")
    breaker_consecutive = breaker.get("consecutive_1010_failures", 0)
    breaker_until = breaker.get("until")
    # Filtering: which draft types were filtered out by recommended_actions
    filtered_types = [dt for dt in draft_types if dt not in ai_recommended] if ai_recommended else []

    # Main Agent influence on bookmarker pipeline
    _msi = main_social_intent or {}
    _mld = main_last_decision or {}
    _m_payload = _msi.get("payload") or {}
    _m_guidance = auto_intent.get("main_guidance") or {}

    main_influence = {
        "social_decision": _mld.get("social_decision", "—"),
        "authorized": _m_payload.get("authorized", False),
        "intent_status": _msi.get("status", "—"),
        "guidance": {
            "experiment_mode": _m_guidance.get("experiment_mode", ""),
            "signal_priority": _m_guidance.get("signal_priority", ""),
            "action_emphasis": _m_guidance.get("action_emphasis", ""),
        },
        "shared_executor": True,
    }

    return {
        "steps": [
            {
                "id": "x_sync",
                "label": "X Sync",
                "status": "ok" if sh_status == "ok" else ("stale" if sh_status else "unknown"),
                "data": {
                    "status": sh_status,
                    "source": active_src,
                    "source_class": sh_source,
                    "updated_at": sh_updated,
                },
            },
            {
                "id": "topic_brief",
                "label": "Topic Brief",
                "status": "ok" if tb_keywords else ("empty" if not tb_summary else "partial"),
                "data": {
                    "keywords": tb_keywords[:8],
                    "summary": tb_summary[:100] if tb_summary else "",
                    "urgency": tb_urgency,
                },
            },
            {
                "id": "content_candidates",
                "label": "Content Candidates",
                "status": "ok" if real_cands else "empty",
                "data": {"count": len(real_cands), "types": cand_types},
            },
            {
                "id": "social_drafts",
                "label": "Social Drafts",
                "status": "ok" if draft_list else "empty",
                "data": {"count": len(draft_list), "types": draft_types, "x_items_seen": x_items_seen},
            },
            {
                "id": "autonomy_intent",
                "label": "Autonomy Intent",
                "status": "active" if ai_mode in ("standard", "active") else ("hold" if ai_mode == "conservative" else "unknown"),
                "data": {
                    "mode": ai_mode,
                    "reason": ai_reason[:120],
                    "recommended_actions": ai_recommended,
                    "tas_social": ai_tas,
                    "op": ai_op,
                    "thresholds": {
                        "tas_standard": thresholds.get("tas_standard", 0.5),
                        "tas_active": thresholds.get("tas_active", 2.0),
                        "op_active": thresholds.get("op_active", 800),
                    },
                },
            },
            {
                "id": "execution",
                "label": "Execution",
                "status": exec_status,
                "data": {
                    "attempted": exec_summary.get("attempted", 0),
                    "succeeded": exec_summary.get("succeeded", 0),
                    "failed": exec_summary.get("failed", 0),
                    "noop": exec_summary.get("noop", 0),
                    "executed_at": exec_at,
                    "autonomy_mode": exec_mode,
                    "breaker_state": breaker_state,
                    "breaker_consecutive": breaker_consecutive,
                    "breaker_until": breaker_until,
                    "filtered_types": filtered_types,
                },
            },
        ],
        "main_influence": main_influence,
    }


def _build_main_social_pipeline(
    social_intent: dict, last_decision: dict,
    bookmarker_feedback_items: list[dict[str, Any]] | None = None,
) -> dict:
    """Build main agent social control-plane summary for dashboard.

    Main no longer executes social actions directly. This panel must therefore describe
    authorization / handoff / feedback, never an execution path owned by main.
    """
    meta = social_intent.get("meta") or {}
    gate_checks = meta.get("gate_checks") or {}
    gates_total = len(gate_checks)
    gates_pass = sum(1 for v in gate_checks.values() if v)
    gate_status = "unknown" if not gates_total else ("pass" if gates_pass == gates_total else ("partial" if gates_pass else "blocked"))

    payload = social_intent.get("payload") or {}
    authorized = payload.get("authorized", False)
    intent_status = social_intent.get("status", "—")
    intent_reason = social_intent.get("reason", "")
    actions = payload.get("actions") or []
    action_types: dict[str, int] = {}
    for a in actions:
        at = a.get("type", "unknown")
        action_types[at] = action_types.get(at, 0) + 1

    social_decision = last_decision.get("social_decision", "—")
    ld_reason = last_decision.get("reason", "")
    feedback_items = bookmarker_feedback_items or []
    ok_count = sum(1 for item in feedback_items if item.get("result_status") == "ok")
    noop_count = sum(1 for item in feedback_items if item.get("result_status") == "noop")
    blocked_count = sum(1 for item in feedback_items if item.get("result_status") == "blocked")
    feedback_status = "unknown" if not feedback_items else ("blocked" if blocked_count and not (ok_count or noop_count) else ("partial" if blocked_count else "ok"))

    return {
        "steps": [
            {
                "id": "gate_checks",
                "label": "Gate Checks",
                "status": gate_status,
                "data": {**gate_checks, "_pass_count": gates_pass, "_total": gates_total},
            },
            {
                "id": "social_intent",
                "label": "Intent Draft",
                "status": intent_status,
                "data": {
                    "authorized": authorized,
                    "reason": intent_reason[:120],
                    "action_count": len(actions),
                    "action_types": action_types,
                },
            },
            {
                "id": "handoff_plane",
                "label": "Handoff to Bookmarker",
                "status": "active" if authorized else "hold",
                "data": {
                    "target_agent": social_intent.get("target_agent") or "bookmarker",
                    "intent_ref": "runtime/main/social-intent.json",
                    "budget_ref": meta.get("budget_ref") or "runtime/shared/budget-allocation.json",
                    "owner": (payload.get("budget_slice") or {}).get("execution_owner") or social_intent.get("target_agent") or "bookmarker",
                },
            },
            {
                "id": "feedback_loop",
                "label": "Bookmarker Feedback",
                "status": feedback_status,
                "data": {
                    "social_decision": social_decision,
                    "reason": ld_reason[:120],
                    "feedback_count": len(feedback_items),
                    "ok_count": ok_count,
                    "noop_count": noop_count,
                    "blocked_count": blocked_count,
                },
            },
        ],
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    resp = FileResponse(str(STATIC / "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _build_dev_dispatch(dev_status: dict, dev_result: dict, dev_task: dict,
                        dev_stage: dict, dev_backlog: dict, dev_roi: dict) -> dict:
    """Build dev_dispatch payload with explicit task identity and consistency flags.

    Source-of-truth precedence:
      1. If status.status == "running" → current_task from task.json is the primary context.
      2. Otherwise → latest_result from result.json is the primary context.
      3. stage_status and dispatch_roi are included with their own task_ids so the
         client can detect mismatches rather than silently merging them.
    """
    is_running = (dev_status.get("status") or "").lower() == "running"

    result_task_id = dev_result.get("task_id")
    task_task_id = dev_task.get("task_id")
    stage_task_id = dev_stage.get("task_id")
    roi_task_id = dev_roi.get("task_id")

    # Determine the "active" task_id: current task if running, else latest result
    active_task_id = task_task_id if is_running else result_task_id

    return {
        "status": dev_status,
        "result": dev_result,
        "stage_status": dev_stage,
        "backlog": dev_backlog,
        "dispatch_roi": dev_roi,
        # Explicit current-task context (only meaningful when running)
        "current_task": {
            "task_id": task_task_id,
            "title": dev_task.get("title"),
            "task_type": dev_task.get("task_type"),
            "priority": dev_task.get("priority"),
            "created_at": dev_task.get("created_at"),
        } if is_running and task_task_id else None,
        # Task identity fields for client-side consistency checks
        "task_identity": {
            "is_running": is_running,
            "active_task_id": active_task_id,
            "result_task_id": result_task_id,
            "task_task_id": task_task_id,
            "stage_task_id": stage_task_id,
            "roi_task_id": roi_task_id,
            "stage_matches_active": stage_task_id == active_task_id if (stage_task_id and active_task_id) else None,
            "roi_matches_active": roi_task_id == active_task_id if (roi_task_id and active_task_id) else None,
        },
    }


@app.get("/api/status")
def api_status():
    """Aggregate snapshot of all three agents."""

    # ── Shared / health ──
    runtime_status = _safe("shared/runtime-status.json") or {}
    health         = _safe("main/runtime-health.json")   or {}
    strategy_exp   = _safe("shared/strategy-experiment.json") or {}

    # ── Main ──
    input_pkt    = _safe("main/input-packet.json")   or {}
    tas_latest   = _safe("main/tas-latest.json")     or {}
    last_dec     = _safe("main/last-decision.json")  or {}
    strategy_plan = _safe("main/strategy-plan.json") or {}
    social_int   = _safe("main/social-intent.json")  or {}
    budget_alloc = _safe("shared/budget-allocation.json") or {}
    attribution  = _safe("shared/latest-attribution.json") or {}

    # ── Bookmarker ──
    topic_brief  = _safe("bookmarker/topic-brief.json")         or {}
    src_health   = _safe("bookmarker/source-health.json")       or {}
    bm_cands     = _safe("bookmarker/content-candidates.json")  or {}
    bm_topic_perf = _safe("bookmarker/topic-performance.json")  or {}
    auto_intent  = _safe("bookmarker/autonomy-intent.json")     or {}
    social_hist  = _safe("shared/social-history.json")          or {}
    social_split = _split_social_actions(social_hist.get("items") or [])
    social_drafts = _safe("bookmarker/social-drafts.json")      or {}
    bm_exec      = _safe("bookmarker/social-execution.json")    or {}
    write_state  = _safe("shared/social-write-state.json")      or {}
    tas_social_data = _safe("bookmarker/tas-social.json")       or {}
    tas_social_main = _safe("main/tas-social.json")              or {}

    # ── Trader ──
    wallet   = _safe("trader/wallet-snapshot.json") or {}
    rewards  = _safe("trader/reward-status.json")   or {}
    tas_trd  = _safe("trader/tas-trade.json")       or {}
    risk     = _safe("trader/risk-status.json")     or {}
    onchain  = _safe("trader/onchain-positions.json") or {}
    portfolio_baseline = _safe("trader/portfolio-baseline.json") or {}
    portfolio_delta = _safe("trader/portfolio-delta.json") or {}
    measurement_quality = _safe("trader/measurement-quality.json") or {}

    # ── Claude Dispatch / Dev ──
    dev_status = _safe("dev/status.json") or {}
    dev_result = _safe("dev/result.json") or {}
    dev_task   = _safe("dev/task.json") or {}
    dev_stage  = _safe("dev/stage-status.json") or {}
    dev_backlog = _safe("dev/backlog.json") or {}
    dev_roi = _safe("dev/dispatch-roi.json") or {}

    # strip private key fields defensively
    if "private_key" in wallet:
        del wallet["private_key"]

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_resources = _fetch_live_op_vp()

    # Detect bootstrap state: keep first-run banner visible until the core
    # main/bookmarker/trader artifacts have each been replaced by real outputs.
    def _core_bootstrap(obj: dict | None) -> bool:
        if not isinstance(obj, dict):
            return False
        status = str(obj.get("status", "")).lower()
        return bool(
            obj.get("bootstrap")
            or status in {"bootstrap", "pending", "initializing", "pending_first_run"}
        )

    core_runtime_bootstrap = any(
        isinstance(runtime_status.get(agent), dict)
        and str(runtime_status.get(agent, {}).get("status", "")).lower() == "bootstrap"
        for agent in ("main", "bookmarker", "trader")
    )
    _is_bootstrap = any([
        bool(runtime_status.get("bootstrap")),
        core_runtime_bootstrap,
        _core_bootstrap(health),
        _core_bootstrap(tas_latest),
        _core_bootstrap(last_dec),
        _core_bootstrap(social_int),
        _core_bootstrap(topic_brief),
        _core_bootstrap(src_health),
        _core_bootstrap(bm_cands),
        _core_bootstrap(wallet),
        _core_bootstrap(tas_trd),
        _core_bootstrap(risk),
    ])

    # Serve enough TAS points for the 7-day sparkline window.
    # At 30-min cadence this needs ~336 points; use 400 for headroom.
    tas_history = _load_tas_history(
        limit=400,
        strategy_cycle_count=_to_int(strategy_exp.get("cycle_count")),
        last_cycle_id=strategy_exp.get("last_cycle_id"),
    )

    return JSONResponse({
        "fetched_at": now_utc,
        "is_bootstrap": _is_bootstrap,
        "runtime_status": runtime_status,
        "health": health,
        "main": {
            "input_packet":   input_pkt,
            "tas_latest":     tas_latest,
            "tas_history":    tas_history,
            "last_decision":  last_dec,
            "strategy_plan":  strategy_plan,
            "budget_allocation": budget_alloc,
            "live_resources": live_resources,
            "latest_attribution": attribution,
            "social_intent":  social_int,
            "social_actions": _main_control_feedback(social_split.get("bookmarker") or [], social_int),
            "social_pipeline": _build_main_social_pipeline(social_int, last_dec, _main_control_feedback(social_split.get("bookmarker") or [], social_int)),
        },
        "bookmarker": {
            "topic_brief":          topic_brief,
            "source_health":        src_health,
            "content_candidates":   bm_cands,
            "topic_performance":    bm_topic_perf,
            "autonomy_intent":      auto_intent,
            "live_resources":       live_resources,
            "social_drafts":        social_drafts,
            "social_actions":       list(reversed((social_split.get("bookmarker") or [])[-20:])),
            "social_pipeline":      _build_bookmarker_social_pipeline(
                src_health, topic_brief, bm_cands,
                auto_intent, social_drafts, bm_exec, write_state,
                main_social_intent=social_int, main_last_decision=last_dec,
            ),
            "curation_vp_24h":      _build_curation_vp_panel(social_hist, hours=24),
            "curation_fallback_preview": _load_curate_fallback_preview(),
            "x_posts":              (_xp := _parse_x_tweets(hours=24, limit=20)),
            "x_posts_window":       "24h" if _xp and _xp[0].get("date") and
                                    _is_within_hours(_xp[0].get("date",""), 24) else "recent",
            "x_bookmarks":          (_xb := _parse_x_bookmarks(hours=24, limit=20)),
            "x_bookmarks_window":   "24h" if _xb and _xb[0].get("date") and
                                    _is_within_hours(_xb[0].get("date",""), 24) else "recent",
            **_load_x_sync(),
            "twin_recognition": _load_twin_recognition(),
            "tas_social_detail": {
                "align_score":         tas_social_data.get("align_score"),
                "community_score":     tas_social_data.get("community_score"),
                "pob_reward_score":    tas_social_data.get("pob_reward_score"),
                "pob_claimable_usd":   tas_social_data.get("pob_claimable_usd"),
                "value":               tas_social_data.get("value"),
                "updated_at":          tas_social_data.get("updated_at"),
                "strategy_action":     tas_social_data.get("strategy_action"),
                "planning_focus":      tas_social_data.get("planning_focus"),
                "formula":             tas_social_data.get("formula"),
                "community_signals":   tas_social_data.get("community_signals"),
                "community_source":    tas_social_data.get("community_source"),
                "track_b_detail":      tas_social_data.get("track_b_detail"),
                "curate_reward_usd":   tas_social_data.get("curate_reward_usd"),
                "curate_reward_score": tas_social_data.get("curate_reward_score"),
                "creator_reward_usd":  tas_social_data.get("creator_reward_usd"),
                "creator_reward_score": tas_social_data.get("creator_reward_score"),
                "track_a_detail":      tas_social_data.get("track_a_detail"),
                "track_c_detail":      tas_social_data.get("track_c_detail"),
                "comparison":          tas_social_data.get("comparison"),
                "eligible_posts":      (tas_social_main.get("inputs") or {}).get("eligible_posts"),
                "align_signals":       tas_social_data.get("track_a_detail", {}).get("raw_align") if tas_social_data.get("track_a_detail") else (tas_social_main.get("inputs") or {}).get("align_signals"),
                "post_interaction_details": (tas_social_main.get("inputs") or {}).get("post_interaction_details"),
            },
        },
        "trader": {
            "wallet_snapshot":   wallet,
            "reward_status":     rewards,
            "tas_trade":         tas_trd,
            "risk_status":       risk,
            "onchain_positions": onchain,
            "portfolio_baseline": portfolio_baseline,
            "portfolio_delta": portfolio_delta,
            "measurement_quality": measurement_quality,
            "trade_actions":     _load_trade_actions(limit=20),
        },
        "dev_dispatch": _build_dev_dispatch(dev_status, dev_result, dev_task, dev_stage, dev_backlog, dev_roi),
        "wiki_system": _load_wiki_status(),
    })


@app.get("/api/wiki")
def api_wiki():
    """Full wiki system status: raw + wiki layers, ingest pipeline, agent wiki status, lint."""
    return JSONResponse(_load_wiki_status())


@app.get("/api/explainability")
def api_explainability():
    """Artifact explainability surface: state, provenance, recent events, health context."""
    now = datetime.now(timezone.utc)

    # ── Artifact catalog with provenance ──
    ARTIFACTS = [
        ("wiki-execution-brief.json", "Execution Brief", ["compiled_at", "valid_until", "schema"]),
        ("community-heat.json", "Community Heat", ["computed_at", "source_health", "schema"]),
        ("wiki-contract-verify.json", "Contract Verify", ["verified_at", "status", "pass", "fail", "schema"]),
        ("wiki-maintenance-report.json", "Maintenance Report", ["generated_at", "overall_status", "schema"]),
        ("wiki-lint-status.json", "Wiki Lint", ["generated_at", "health_score", "needs_attention"]),
        ("wiki-contract-alert.json", "Contract Alert", ["severity", "action", "message"]),
        ("wiki-maintenance-alert.json", "Maintenance Alert", ["severity", "action", "message"]),
    ]
    artifacts = []
    for filename, label, meta_keys in ARTIFACTS:
        path = RUNTIME / "shared" / filename
        raw_path = f"runtime/shared/{filename}"
        entry: dict[str, Any] = {"filename": filename, "label": label, "exists": path.exists(), "raw_path": raw_path}
        if path.exists():
            data = _load(path)
            if data:
                meta = {}
                for k in meta_keys:
                    if k in data:
                        meta[k] = data[k]
                entry["meta"] = meta
                # Include all top-level keys for detail expansion (exclude large nested)
                detail = {}
                for k, v in data.items():
                    if isinstance(v, (str, int, float, bool, type(None))):
                        detail[k] = v
                    elif isinstance(v, list) and len(v) <= 5:
                        detail[k] = v
                entry["detail"] = detail
                # Compute age
                ts = (data.get("verified_at") or data.get("generated_at")
                      or data.get("compiled_at") or data.get("computed_at"))
                dt = _parse_dt(ts)
                if dt:
                    entry["age_hours"] = round((now - dt).total_seconds() / 3600, 1)
                    entry["timestamp"] = ts
            # Provenance sidecar
            sidecar = path.parent / f"{path.name}.provenance.json"
            sidecar_path = f"runtime/shared/{path.name}.provenance.json"
            if sidecar.exists():
                prov = _load(sidecar)
                if prov:
                    entry["provenance"] = {
                        "producer": prov.get("producer"),
                        "generated_at": prov.get("generated_at"),
                        "source_refs": prov.get("source_refs"),
                        "raw_path": sidecar_path,
                    }
        artifacts.append(entry)

    # ── Recent events from ledger ──
    events_path = RUNTIME / "shared" / "wiki-events.jsonl"
    events: list[dict] = []
    if events_path.exists():
        try:
            lines = events_path.read_text(encoding="utf-8").strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    events.append({
                        "ts": e.get("ts"),
                        "event_type": e.get("event_type"),
                        "producer": e.get("producer"),
                        "artifact": e.get("artifact"),
                        "status": e.get("status"),
                        "summary": e.get("summary"),
                    })
                except json.JSONDecodeError:
                    continue
                if len(events) >= 15:
                    break
        except Exception:
            pass

    # ── Health context ──
    alert = _load(RUNTIME / "shared" / "wiki-contract-alert.json") or {}
    maint_alert = _load(RUNTIME / "shared" / "wiki-maintenance-alert.json") or {}
    lint = _load(RUNTIME / "shared" / "wiki-lint-status.json") or {}

    contract_ok = alert.get("status") == "ok" and alert.get("severity") == "clear"
    maint_ok = maint_alert.get("severity") == "clear"
    lint_ok = not lint.get("needs_attention")
    # Bootstrap: treat pending/uninitialized alerts as not-yet-failed
    contract_bootstrap = alert.get("bootstrap") or alert.get("severity") == "none"
    maint_bootstrap = maint_alert.get("bootstrap") or maint_alert.get("severity") == "none"

    if contract_bootstrap and maint_bootstrap:
        wiki_overall = "bootstrap"
    elif contract_ok and maint_ok and lint_ok:
        wiki_overall = "ok"
    else:
        wiki_overall = "degraded"

    health = {
        "overall": wiki_overall,
        "contract": {"status": alert.get("status", "unknown"), "severity": alert.get("severity", "unknown")},
        "maintenance": {"severity": maint_alert.get("severity", "unknown"), "action": maint_alert.get("action", "unknown")},
        "lint": {"health_score": lint.get("health_score"), "needs_attention": lint.get("needs_attention")},
    }

    return JSONResponse({
        "artifacts": artifacts,
        "recent_events": events,
        "health": health,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


@app.get("/api/timeline")
def api_timeline():
    """Merge social history + trader executions (last 3 days), 30 most recent."""

    items: list[dict] = []

    _SOCIAL_OK = {"ok", "success", ""}   # empty result_status = older records pre-dating result tracking
    _SOCIAL_SKIP = {"noop", "failed", "blocked", "pending", "skipped"}

    # Social history — show all executed actions (exclude noop/failed/blocked)
    social = _safe("shared/social-history.json") or {}
    for ev in (social.get("items") or []):
        result_status = str(ev.get("result_status") or "").lower()
        if result_status in _SOCIAL_SKIP:
            continue
        ts = ev.get("executed_at") or ev.get("ts") or ""
        target = ev.get("target_key") or ev.get("note") or ""
        # Build a readable note: type + target tweet id + optional note
        req = ev.get("request") or {}
        vp = req.get("vp")
        note_str = ev.get("note") or ""
        if not note_str and target:
            tid = target.split(":")[-1] if ":" in target else target
            note_str = f"{tid[:12]}"
        if vp and f"VP={vp}" not in note_str:
            note_str = f"VP={vp} {note_str}".strip()
        items.append({
            "ts":      ts,
            "source":  "social",
            "type":    ev.get("type", "?"),
            "status":  ev.get("result_status", "ok"),
            "note":    note_str,
            "detail":  ev,
        })

    # Trader executions — last 7 days' files (extend window to catch older buys/claims)
    today = datetime.now(timezone.utc).date()
    for delta in range(7):
        d = today - timedelta(days=delta)
        path = RUNTIME / "trader" / f"executions-{d}.json"
        rec = _load(path) or {}
        for ev in (rec.get("items") or []):
            if not _trader_ev_is_real(ev):
                continue
            action = ev.get("action", "?")
            tick = ev.get("tick", "")
            amount = ev.get("amount")
            usd = ev.get("usd")
            tx_hash = (ev.get("tx_hash") or "")[:16]
            note_parts = [p for p in [tick, f"${usd:.2f}" if usd else None, f"tx:{tx_hash}" if tx_hash else None] if p]
            ts = ev.get("ts") or ""
            items.append({
                "ts":     ts,
                "source": "trader",
                "type":   action,
                "status": ev.get("status", ""),
                "note":   " ".join(note_parts),
                "detail": ev,
            })

    # Bookmarker exec results — completed only
    bm_exec = _safe("bookmarker/social-execution.json") or {}
    for ev in (bm_exec.get("results") or []):
        result_status = str(ev.get("result_status") or "").lower()
        if result_status in _SOCIAL_SKIP:
            continue
        ts = ev.get("executed_at") or ""
        items.append({
            "ts":     ts,
            "source": "bookmarker",
            "type":   ev.get("type", "?"),
            "status": ev.get("result_status", "ok"),
            "note":   ev.get("note") or ev.get("target_key", ""),
            "detail": ev,
        })
    # Bookmarker exec cycle — only if summary.succeeded > 0
    if bm_exec.get("generated_at"):
        summary = bm_exec.get("summary") or {}
        succeeded = summary.get("succeeded") if isinstance(summary, dict) else None
        try:
            succeeded_count = int(succeeded) if succeeded is not None else 0
        except (TypeError, ValueError):
            succeeded_count = 0
        if succeeded_count > 0:
            items.append({
                "ts":     bm_exec.get("generated_at", ""),
                "source": "bookmarker",
                "type":   "exec_cycle",
                "status": bm_exec.get("status", ""),
                "note":   bm_exec.get("notes") or str(summary),
                "detail": {k: v for k, v in bm_exec.items() if k != "results"},
            })

    # Bookmarker heartbeat
    bm_latest = _safe("bookmarker/latest.json") or {}
    if bm_latest.get("generated_at"):
        items.append({
            "ts":     bm_latest.get("generated_at", ""),
            "source": "bookmarker",
            "type":   "heartbeat",
            "status": bm_latest.get("status", ""),
            "note":   f"high_signal:{bm_latest.get('high_signal_count','')} urgency:{bm_latest.get('content_urgency','')}",
            "detail": bm_latest,
        })

    # Main decision
    main_dec = _safe("main/last-decision.json") or {}
    if main_dec.get("updated_at"):
        social = main_dec.get("social_decision", "")
        treasury = main_dec.get("treasury_decision", "")
        items.append({
            "ts":     main_dec.get("updated_at", ""),
            "source": "main",
            "type":   "decision",
            "status": "",
            "note":   main_dec.get("reason") or f"social:{social} treasury:{treasury}",
            "detail": main_dec,
        })

    # Main heartbeat
    main_latest = _safe("main/latest.json") or {}
    if main_latest.get("generated_at"):
        items.append({
            "ts":     main_latest.get("generated_at", ""),
            "source": "main",
            "type":   "heartbeat",
            "status": main_latest.get("status", ""),
            "note":   main_latest.get("status", ""),
            "detail": main_latest,
        })

    # Sort descending, deduplicate by id; for claims also deduplicate by order_id
    seen: set[str] = set()
    deduped: list[dict] = []
    for it in sorted(items, key=lambda x: x.get("ts", ""), reverse=True):
        uid = it["detail"].get("id") or f"{it['ts']}:{it['source']}:{it['note']}"
        # For claim events, also deduplicate by order_id to avoid double-counting
        ev = it["detail"]
        if ev.get("action") == "claim":
            order_id = ev.get("order_id") or ""
            if not order_id:
                rd = (ev.get("remote") or {}).get("response") or {}
                if isinstance(rd, dict) and "data" in rd:
                    rd = rd["data"]
                order_id = (rd.get("orderId") or "").strip()
            if order_id:
                uid = f"claim:{order_id}"
        if uid not in seen:
            seen.add(uid)
            deduped.append(it)

    # ── Build summary (24h window) ──
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    posts_24h = 0
    curations_24h = 0
    claims_24h = 0
    blocked_24h = 0
    agent_action_count: dict[str, int] = {}
    last_success_at: str | None = None

    # Count from social history (all items, not just deduped timeline)
    social_data = _safe("shared/social-history.json") or {}
    for ev in (social_data.get("items") or []):
        ts_str = ev.get("executed_at") or ev.get("ts") or ""
        try:
            ev_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if ev_dt < cutoff_24h:
            continue
        ev_type = str(ev.get("type") or "").lower()
        result_status = str(ev.get("result_status") or "").lower()
        if result_status in ("noop", "failed", "blocked", "skipped"):
            blocked_24h += 1
            continue
        if ev_type in ("post", "tweet"):
            posts_24h += 1
        elif ev_type in ("curation", "vote", "repost"):
            curations_24h += 1
        actor = _classify_social_actor(ev)
        agent_action_count[actor] = agent_action_count.get(actor, 0) + 1
        if not last_success_at or ts_str > last_success_at:
            last_success_at = ts_str

    # Count trade claims
    today = datetime.now(timezone.utc).date()
    for delta in range(2):
        d = today - timedelta(days=delta)
        path = RUNTIME / "trader" / f"executions-{d}.json"
        rec = _load(path) or {}
        for ev in (rec.get("items") or []):
            if not _trader_ev_is_real(ev):
                continue
            ts_str = ev.get("ts") or ""
            try:
                ev_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if ev_dt < cutoff_24h:
                continue
            action = str(ev.get("action") or "").lower()
            if action == "claim":
                claims_24h += 1
            agent_action_count["trader"] = agent_action_count.get("trader", 0) + 1
            if not last_success_at or ts_str > last_success_at:
                last_success_at = ts_str

    dominant_agent = max(agent_action_count, key=agent_action_count.get) if agent_action_count else "none"

    summary = {
        "posts_24h": posts_24h,
        "curations_24h": curations_24h,
        "claims_24h": claims_24h,
        "blocked_24h": blocked_24h,
        "dominant_agent": dominant_agent,
        "last_success_at": last_success_at,
    }

    return JSONResponse({"items": deduped[:50], "total": len(deduped), "summary": summary})


@app.get("/api/runtime/{agent}/{file}")
def api_runtime_file(agent: str, file: str):
    """Read a single runtime file on demand."""
    # Sanitise path components
    safe_agent = agent.replace("..", "").replace("/", "")
    safe_file  = file.replace("..", "").replace("/", "")
    path = RUNTIME / safe_agent / safe_file
    data = _load(path)
    if data is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path.relative_to(WORKSPACE)}")
    return JSONResponse(data)


@app.get("/api/autoresearch")
def api_autoresearch():
    """Merged summary of strategy-experiment.json + skill-manifest.json for AutoResearch loop."""
    se = _load(RUNTIME / "shared" / "strategy-experiment.json") or {}
    sm = _load(RUNTIME / "main" / "skill-manifest.json") or {}

    # Strategy experiment summary
    track_a = se.get("track_a") or {}
    track_b = se.get("track_b") or {}
    arm_history_a = track_a.get("arm_history") or []
    arm_history_b = track_b.get("arm_history") or []
    recent_a = arm_history_a[-5:] if arm_history_a else []
    recent_b = arm_history_b[-5:] if arm_history_b else []

    # Merge recent verdicts from both tracks
    recent_verdicts = []
    for entry in recent_a:
        recent_verdicts.append({
            "track": "a",
            "verdict": entry.get("verdict", "—"),
            "tas_delta": entry.get("tas_delta"),
            "cycle_id": entry.get("cycle_id", ""),
        })
    for entry in recent_b:
        recent_verdicts.append({
            "track": "b",
            "verdict": entry.get("verdict", "—"),
            "tas_delta": entry.get("tas_delta", entry.get("tas_social_delta")),
            "cycle_id": entry.get("cycle_id", ""),
        })
    recent_verdicts.sort(key=lambda x: x.get("cycle_id", ""), reverse=True)

    strategy_experiment = {
        "version": se.get("version", "v2"),
        "cycle_count": se.get("cycle_count", len(arm_history_a)),
        "updated_at": se.get("updated_at"),
        "track_a_current_arm": track_a.get("current_arm") or {},
        "track_a_best_arm": track_a.get("best_arm") or {},
        "track_b_current_arm": track_b.get("current_arm") or {},
        "track_b_best_arm": track_b.get("best_arm") or {},
        "recent_verdicts": recent_verdicts[:10],
        "arm_history_count_a": len(arm_history_a),
        "arm_history_count_b": len(arm_history_b),
        "arm_history_max": se.get("pruning", {}).get("arm_history_max", 30),
        "coupling_alpha": se.get("coupling_alpha", 0.5),
    }

    # Skills summary
    agents_skills = {}
    for agent_name, agent_data in (sm.get("agents") or {}).items():
        current = agent_data.get("current_skills") or []
        tier_3 = agent_data.get("tier_3_skills") or []
        agents_skills[agent_name] = {
            "count": len(current),
            "max": len(tier_3) if tier_3 else len(current),
            "current_skills": current,
        }

    tas_latest = _safe("main/tas-latest.json") or {}
    current_tas = tas_latest.get("tas_total")
    next_tier_threshold = (sm.get("tier_thresholds") or {}).get("level_2", 2.5)
    tas_to_next = round(next_tier_threshold - (current_tas or 0), 2) if current_tas is not None else None

    skills = {
        "current_tier": sm.get("current_tier", 1),
        "tier_thresholds": sm.get("tier_thresholds") or {},
        "tas_to_next_tier": tas_to_next,
        "agents": agents_skills,
    }

    return JSONResponse({
        "strategy_experiment": strategy_experiment,
        "skills": skills,
    })


def _freshness_bucket(date_str: str | None, profile: str = "runtime") -> str:
    """Return freshness bucket using source-specific SLA profiles."""
    dt = _parse_dt(date_str)
    if not dt:
        return "bootstrap"
    now = datetime.now(timezone.utc)
    if profile == "valid_until":
        if dt >= now:
            return "fresh"
        overdue_min = (now - dt).total_seconds() / 60
        return _bucket_from_age(overdue_min, profile)
    age_min = (now - dt).total_seconds() / 60
    return _bucket_from_age(age_min, profile)


def _freshness_minutes(date_str: str | None) -> float | None:
    """Return age in minutes, or None."""
    dt = _parse_dt(date_str)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60


@app.get("/api/control-tower")
def api_control_tower():
    """Aggregated control tower: system_mode, bottleneck, alerts, freshness."""
    tas_latest = _safe("main/tas-latest.json") or {}
    last_dec = _safe("main/last-decision.json") or {}
    social_int = _safe("main/social-intent.json") or {}
    treasury = _safe("main/treasury-policy.json") or {}
    tas_social = _safe("bookmarker/tas-social.json") or {}
    topic_brief = _safe("bookmarker/topic-brief.json") or {}
    tas_trade = _safe("trader/tas-trade.json") or {}
    reward_status = _safe("trader/reward-status.json") or {}
    community_heat = _safe("shared/community-heat.json") or {}
    wiki_brief = _safe("shared/wiki-execution-brief.json") or {}
    strategy_plan = _safe("main/strategy-plan.json") or {}

    # Freshness for key sources
    sources = {
        "tas_latest": {"ts": tas_latest.get("updated_at") or tas_latest.get("generated_at"), "profile": "runtime"},
        "last_decision": {"ts": last_dec.get("updated_at"), "profile": "runtime"},
        "social_intent": {"ts": social_int.get("issued_at") or social_int.get("generated_at") or social_int.get("updated_at"), "profile": "runtime"},
        "treasury_policy": {"ts": treasury.get("issued_at") or treasury.get("generated_at") or treasury.get("updated_at"), "profile": "runtime"},
        "tas_social": {"ts": tas_social.get("generated_at") or tas_social.get("updated_at"), "profile": "runtime"},
        "topic_brief": {"ts": topic_brief.get("generated_at") or topic_brief.get("updated_at"), "profile": "runtime"},
        "tas_trade": {"ts": tas_trade.get("generated_at") or tas_trade.get("updated_at"), "profile": "runtime"},
        "reward_status": {"ts": reward_status.get("generated_at") or reward_status.get("updated_at"), "profile": "runtime"},
        "community_heat": {"ts": community_heat.get("computed_at"), "profile": "runtime"},
        "wiki_brief": {
            "ts": wiki_brief.get("compiled_at"),
            "profile": "weekly",
            "bucket_ts": wiki_brief.get("valid_until") or wiki_brief.get("compiled_at"),
            "bucket_profile": "valid_until" if wiki_brief.get("valid_until") else "weekly",
        },
    }
    freshness: dict[str, dict[str, Any]] = {}
    for key, info in sources.items():
        ts = info["ts"]
        bucket_ts = info.get("bucket_ts") or ts
        bucket_profile = info.get("bucket_profile") or info.get("profile") or "runtime"
        freshness[key] = {
            "ts": ts,
            "bucket": _freshness_bucket(bucket_ts, bucket_profile),
            "age_min": round(_freshness_minutes(ts) or -1, 1) if ts else None,
        }

    # System mode — detect bootstrap first
    all_bootstrap = all(
        freshness[k]["bucket"] == "bootstrap"
        for k in freshness
    )
    any_bootstrap = any(
        freshness[k]["bucket"] == "bootstrap"
        for k in freshness
    )
    any_social_critical = any(
        freshness[k]["bucket"] == "critical"
        for k in ("tas_social", "topic_brief", "social_intent")
    )
    any_social_stale = any(
        freshness[k]["bucket"] in ("stale", "critical")
        for k in ("tas_social", "topic_brief", "social_intent")
    )
    tas_total = tas_latest.get("tas_total")
    tas_history = _load_tas_history(limit=5)
    tas_trend = "stable"
    if len(tas_history) >= 2:
        prev = tas_history[-2].get("tas_total")
        curr = tas_history[-1].get("tas_total")
        if prev is not None and curr is not None:
            if curr > prev + 0.01:
                tas_trend = "improved"
            elif curr < prev - 0.01:
                tas_trend = "declined"

    if all_bootstrap:
        system_mode = "bootstrap"
    elif any_bootstrap and not any_social_critical:
        system_mode = "initializing"
    elif any_social_critical:
        system_mode = "degraded"
    elif tas_trend == "declined" and any_social_stale:
        system_mode = "repair"
    elif tas_trend == "improved" and not any_social_stale:
        system_mode = "aggressive"
    else:
        system_mode = "normal"

    # Primary bottleneck
    bottleneck = None
    if system_mode in ("bootstrap", "initializing"):
        bottleneck = "awaiting first agent cycles"
    elif freshness.get("topic_brief", {}).get("bucket") in ("stale", "critical"):
        bottleneck = "bookmarker topic_brief stale"
    elif freshness.get("social_intent", {}).get("bucket") in ("stale", "critical"):
        bottleneck = "X sync / social pipeline stale"
    elif freshness.get("community_heat", {}).get("bucket") in ("stale", "critical"):
        bottleneck = "community_heat missing/stale"
    elif (tas_trade.get("risk_flags") or []):
        bottleneck = "trader risk high"
    elif tas_trend == "declined":
        bottleneck = "TAS trend declining"

    # Highest priority action
    strat_action = strategy_plan.get("strategy_action") or last_dec.get("strategy_action") or "—"
    if system_mode in ("bootstrap", "initializing"):
        highest_action = "run first agent cycles"
    elif system_mode == "degraded":
        highest_action = "repair social freshness"
    elif system_mode == "repair":
        highest_action = "rewrite social-intent / treasury-policy"
    elif strat_action and strat_action != "—":
        highest_action = strat_action
    else:
        highest_action = "maintain current strategy"

    # Expected TAS lever
    social_stale = freshness.get("tas_social", {}).get("bucket") in ("stale", "critical")
    trader_rec = tas_trade.get("recommended_actions") or []
    social_fresh = freshness.get("tas_social", {}).get("bucket") == "fresh"
    if social_stale or bottleneck and "social" in (bottleneck or "").lower():
        expected_lever = "social > trade"
    elif trader_rec and social_fresh:
        expected_lever = "trade > social"
    else:
        expected_lever = "balanced"

    # Confidence — bootstrap is not failure
    critical_count = sum(1 for v in freshness.values() if v["bucket"] == "critical")
    stale_count = sum(1 for v in freshness.values() if v["bucket"] in ("stale", "critical"))
    bootstrap_count = sum(1 for v in freshness.values() if v["bucket"] == "bootstrap")
    if all_bootstrap:
        confidence = "bootstrap"
    elif critical_count >= 3:
        confidence = "low"
    elif stale_count >= 3:
        confidence = "medium"
    else:
        confidence = "high"

    # Alerts — bootstrap items are info-level, not critical
    alerts: list[dict[str, str]] = []
    if all_bootstrap:
        alerts.append({"level": "info", "message": "Environment freshly installed — awaiting first agent cycles"})
    elif any_bootstrap:
        alerts.append({"level": "info", "message": f"{bootstrap_count} artifact(s) pending first run"})
    for key, info in freshness.items():
        if info["bucket"] == "critical":
            alerts.append({"level": "critical", "message": f"{key} critical"})
        elif info["bucket"] == "stale":
            alerts.append({"level": "warning", "message": f"{key} stale"})
    if tas_trend == "declined":
        alerts.append({"level": "warning", "message": "TAS trend declining"})
    risk_flags = tas_trade.get("risk_flags") or []
    for flag in risk_flags[:2]:
        alerts.append({"level": "warning", "message": f"trader risk: {flag}"})

    return JSONResponse({
        "system_mode": system_mode,
        "primary_bottleneck": bottleneck,
        "highest_priority_action": highest_action,
        "expected_tas_lever": expected_lever,
        "confidence": confidence,
        "tas_trend": tas_trend,
        "alerts": alerts,
        "freshness": freshness,
    })


@app.get("/api/agent-health")
def api_agent_health():
    """Agent operating model: observe/decide/execute lanes + per-agent health + freshness matrix."""
    tas_latest = _safe("main/tas-latest.json") or {}
    last_dec = _safe("main/last-decision.json") or {}
    social_int = _safe("main/social-intent.json") or {}
    treasury = _safe("main/treasury-policy.json") or {}
    tas_social_data = _safe("bookmarker/tas-social.json") or {}
    topic_brief = _safe("bookmarker/topic-brief.json") or {}
    source_health = _safe("bookmarker/source-health.json") or {}
    align_events = _safe("bookmarker/align-hook-state.json") or {}
    tas_trade = _safe("trader/tas-trade.json") or {}
    reward_status = _safe("trader/reward-status.json") or {}
    wallet = _safe("trader/wallet-snapshot.json") or {}
    community_heat = _safe("shared/community-heat.json") or {}
    skill_manifest = _safe("main/skill-manifest.json") or {}
    strategy_plan = _safe("main/strategy-plan.json") or {}
    budget_alloc = _safe("shared/budget-allocation.json") or {}
    auto_intent = _safe("bookmarker/autonomy-intent.json") or {}

    # Strip sensitive data
    wallet.pop("private_key", None)

    # ── Observe lane ──
    observe = {
        "tas_total": tas_latest.get("tas_total"),
        "tas_social": tas_latest.get("tas_social"),
        "tas_trade": tas_latest.get("tas_trade"),
        "community_heat_health": (community_heat.get("source_health") or "unavailable"),
        "topic_brief_keywords": (topic_brief.get("keywords") or [])[:5],
        "source_health_status": source_health.get("bird") or source_health.get("status") or "unknown",
    }

    # ── Decide lane ──
    strat_action = strategy_plan.get("strategy_action") or last_dec.get("strategy_action") or "—"
    decide = {
        "strategy_action": strat_action,
        "social_decision": last_dec.get("social_decision", "—"),
        "treasury_decision": last_dec.get("treasury_decision", "—"),
        "planning_focus": strategy_plan.get("hypothesis") or last_dec.get("planning_focus") or "—",
        "autonomy_mode": tas_trade.get("autonomy_mode", "—"),
    }

    # ── Execute lane ──
    social_hist = _safe("shared/social-history.json") or {}
    recent_social = (social_hist.get("items") or [])[-5:]
    trader_rec = tas_trade.get("recommended_actions") or []
    execute = {
        "social_intent_authorized": (social_int.get("payload") or {}).get("authorized", False),
        "social_actions_recent": len(recent_social),
        "trader_recommended": trader_rec[:3] if trader_rec else ["hold"],
        "reward_claimable_usd": tas_trade.get("claimable_usd_raw"),
    }

    # ── Per-agent health ──
    # Main
    main_freshness = _freshness_bucket(last_dec.get("updated_at"), "runtime")
    main_mode_map = {
        "discard_previous_strategy": "Switch Strategy",
        "reinforce_previous_strategy": "Reinforce Strategy",
        "conservative_explore": "Conservative Explore",
    }
    main_mode = main_mode_map.get(strat_action, strat_action)

    tas_trend = "stable"
    history = _load_tas_history(limit=5)
    if len(history) >= 2:
        prev_t = history[-2].get("tas_total")
        curr_t = history[-1].get("tas_total")
        if prev_t is not None and curr_t is not None:
            if curr_t < prev_t - 0.01:
                tas_trend = "declined"
            elif curr_t > prev_t + 0.01:
                tas_trend = "improved"

    main_blocker = None
    if main_freshness == "bootstrap":
        main_blocker = "awaiting first heartbeat"
    elif tas_trend == "declined":
        main_blocker = "TAS declined"
    elif _freshness_bucket(social_int.get("issued_at") or social_int.get("generated_at"), "runtime") in ("stale", "critical"):
        main_blocker = "social-intent stale"
    elif _freshness_bucket(tas_latest.get("updated_at"), "runtime") in ("stale", "critical"):
        main_blocker = "input packet stale"

    main_next = strat_action if strat_action != "—" else "maintain current strategy"
    if main_blocker == "TAS declined":
        main_next = "rewrite social-intent / treasury-policy"
    elif main_blocker:
        main_next = "repair social freshness"

    # Bookmarker
    tb_fresh = _freshness_bucket(topic_brief.get("generated_at") or topic_brief.get("updated_at"), "runtime")
    sh_status = source_health.get("bird") or source_health.get("status") or "unknown"
    bm_freshness = tb_fresh

    tas_social_val = tas_latest.get("tas_social")
    tas_social_trend = "stable"
    if len(history) >= 2:
        prev_s = history[-2].get("tas_social")
        curr_s = history[-1].get("tas_social")
        if prev_s is not None and curr_s is not None:
            if curr_s > prev_s + 0.01:
                tas_social_trend = "improved"
            elif curr_s < prev_s - 0.01:
                tas_social_trend = "declined"

    if tb_fresh == "bootstrap":
        bm_mode = "bootstrap"
    elif tb_fresh in ("stale", "critical") or sh_status != "ok":
        bm_mode = "stale"
    elif tas_social_trend == "improved":
        bm_mode = "active"
    else:
        bm_mode = "conservative"

    bm_blocker = None
    if tb_fresh == "bootstrap":
        bm_blocker = "awaiting first bookmarker cycle"
    elif tb_fresh in ("stale", "critical"):
        bm_blocker = "topic_brief stale"
    elif sh_status != "ok":
        bm_blocker = "source_health degraded"

    bm_next = "execute social intent"
    if bm_blocker and "topic" in (bm_blocker or ""):
        bm_next = "recover topic pipeline"
    elif bm_blocker:
        bm_next = "repair source health"

    # Trader
    trader_freshness = _freshness_bucket(tas_trade.get("generated_at") or tas_trade.get("updated_at"), "runtime")
    trader_mode = tas_trade.get("autonomy_mode", "—")
    trader_blocker = None
    risk_flags = tas_trade.get("risk_flags") or []
    if trader_freshness == "bootstrap":
        trader_blocker = "awaiting first trader cycle"
    elif risk_flags:
        trader_blocker = f"risk: {risk_flags[0]}"
    elif community_heat.get("source_health") != "ok":
        trader_blocker = "heat signal unavailable"
    elif _freshness_bucket(reward_status.get("generated_at") or reward_status.get("updated_at"), "runtime") in ("stale", "critical"):
        trader_blocker = "reward_status stale"

    trader_next = (trader_rec[0] if trader_rec else "hold")

    # Claude Dispatch (鲁班)
    dev_status = _safe("dev/status.json") or {}
    dev_result = _safe("dev/result.json") or {}
    dev_stage = _safe("dev/stage-status.json") or {}
    dev_roi = _safe("dev/dispatch-roi.json") or {}

    dev_result_ts = dev_result.get("completed_at") or dev_status.get("updated_at")

    # Mode derivation
    dev_st = (dev_status.get("status") or "").lower()
    dev_res_st = (dev_result.get("status") or "").lower()
    if dev_st == "running":
        dev_mode = "running"
    elif dev_res_st in ("blocked", "failed") or dev_result.get("blockers"):
        dev_mode = "blocked"
    elif dev_res_st in ("ok", "partial") and dev_st != "running":
        dev_mode = "idle"
    else:
        dev_mode = "standby"

    dev_profile = "dev" if dev_mode == "running" else "dev_idle"
    dev_freshness = _freshness_bucket(dev_result_ts, dev_profile)

    dev_blocker = None
    blockers_list = dev_result.get("blockers") or []
    if blockers_list:
        dev_blocker = blockers_list[0] if isinstance(blockers_list[0], str) else str(blockers_list[0])
    elif dev_res_st == "blocked":
        dev_blocker = "task blocked"

    if dev_mode == "running":
        dev_next = "等待执行完成"
    elif dev_mode == "idle":
        dev_next = "等待 main 派单"
    elif dev_mode == "blocked":
        dev_next = "修复 blocker / 重新 dispatch"
    else:
        dev_next = "standby"

    # Modules for dev dispatch
    dev_task = _safe("dev/task.json") or {}
    dev_modules = {
        "task": _freshness_bucket(dev_task.get("created_at"), "dev") if dev_mode == "running" else "na",
        "result": _freshness_bucket(dev_result.get("completed_at"), dev_profile) if dev_result.get("completed_at") else "na",
        "tools": "na",
        "runtime": _freshness_bucket(dev_status.get("updated_at"), dev_profile) if dev_status.get("updated_at") else "na",
        "skills": "na",
        "links": "na",
    }

    agents = {
        "main": {
            "role": "control plane",
            "mode": main_mode,
            "freshness": main_freshness,
            "blocker": main_blocker,
            "next_action": main_next,
        },
        "bookmarker": (lambda _live=_fetch_live_op_vp(): {
            "role": "maximize TAS_social",
            "mode": bm_mode,
            "freshness": bm_freshness,
            "blocker": bm_blocker,
            "next_action": bm_next,
            "op": _live["op"] if _live["op"] is not None else auto_intent.get("op"),
            "vp": _live["vp"] if _live["vp"] is not None else auto_intent.get("vp"),
            "op_budget": (budget_alloc.get("allocations") or {}).get("bookmarker", {}).get("op_budget"),
            "vp_budget": (budget_alloc.get("allocations") or {}).get("bookmarker", {}).get("vp_budget"),
        })(),
        "trader": {
            "role": "maximize TAS_trade",
            "mode": trader_mode,
            "freshness": trader_freshness,
            "blocker": trader_blocker,
            "next_action": trader_next,
        },
        "claude_dispatch": {
            "role": "development executor",
            "mode": dev_mode,
            "freshness": dev_freshness,
            "blocker": dev_blocker,
            "next_action": dev_next,
            "modules": dev_modules,
            "latest_result_status": dev_res_st or None,
            "result_task_id": dev_result.get("task_id"),
            "task_summary": dev_result.get("task_summary"),
            "files_changed": dev_result.get("files_changed") or [],
            "built_tools": dev_result.get("built_tools") or [],
            "result_links": dev_result.get("result_links") or [],
            "completed_at": dev_result.get("completed_at"),
            "tests_passed": dev_result.get("tests_passed"),
            "test_results": dev_result.get("test_results"),
            "blockers": blockers_list,
            "dispatch_roi": dev_roi,
            "stage_status": dev_stage,
            "roi_task_id": dev_roi.get("task_id"),
            "stage_task_id": dev_stage.get("task_id"),
        },
    }

    # ── Freshness matrix ──
    matrix_sources = {
        "main": {
            "tas": {"ts": tas_latest.get("updated_at") or tas_latest.get("generated_at"), "profile": "runtime"},
            "intent": {"ts": social_int.get("issued_at") or social_int.get("generated_at"), "profile": "runtime"},
            "pipeline": {"ts": last_dec.get("updated_at"), "profile": "runtime"},
            "wallet": None,
            "wiki": {
                "ts": ((_safe("shared/wiki-execution-brief.json") or {}).get("valid_until") or (_safe("shared/wiki-execution-brief.json") or {}).get("compiled_at")),
                "profile": "valid_until" if ((_safe("shared/wiki-execution-brief.json") or {}).get("valid_until")) else "weekly",
            },
            "skills": {"ts": (skill_manifest.get("updated_at") or skill_manifest.get("generated_at")), "profile": "daily"},
        },
        "bookmarker": {
            "tas": {"ts": tas_social_data.get("generated_at") or tas_social_data.get("updated_at"), "profile": "runtime"},
            "intent": {"ts": ((_safe("bookmarker/autonomy-intent.json") or {}).get("generated_at")), "profile": "runtime"},
            "pipeline": {"ts": topic_brief.get("generated_at") or topic_brief.get("updated_at"), "profile": "runtime"},
            "wallet": None,
            "wiki": None,
            "skills": None,
        },
        "trader": {
            "tas": {"ts": tas_trade.get("generated_at") or tas_trade.get("updated_at"), "profile": "runtime"},
            "intent": None,
            "pipeline": {"ts": reward_status.get("generated_at") or reward_status.get("updated_at"), "profile": "runtime"},
            "wallet": {"ts": wallet.get("updated_at") or wallet.get("generated_at"), "profile": "runtime"},
            "wiki": None,
            "skills": None,
        },
        "claude_dispatch": {
            "tas": None,
            "intent": {"ts": dev_task.get("created_at"), "profile": "dev"} if dev_mode == "running" else None,
            "pipeline": {"ts": dev_result.get("completed_at") or dev_status.get("updated_at"), "profile": dev_profile},
            "wallet": None,
            "wiki": None,
            "skills": None,
        },
    }

    freshness_matrix: list[dict[str, Any]] = []
    for agent_name in ("main", "bookmarker", "trader", "claude_dispatch"):
        row: dict[str, str] = {"agent": agent_name}
        for col, info in matrix_sources[agent_name].items():
            if not info:
                row[col] = "na"
                continue
            row[col] = _freshness_bucket(info.get("ts"), info.get("profile", "runtime")) if info.get("ts") else "na"
        freshness_matrix.append(row)

    return JSONResponse({
        "observe": observe,
        "decide": decide,
        "execute": execute,
        "agents": agents,
        "freshness_matrix": freshness_matrix,
    })


@app.get("/api/noc")
def api_noc():
    """NOC / Intelligence endpoint: dependency graph, state machines, countdowns, intelligence."""
    now = datetime.now(timezone.utc)

    # ── Load data sources ──
    social_int = _safe("main/social-intent.json") or {}
    treasury = _safe("main/treasury-policy.json") or {}
    last_dec = _safe("main/last-decision.json") or {}
    topic_brief = _safe("bookmarker/topic-brief.json") or {}
    source_health = _safe("bookmarker/source-health.json") or {}
    bm_social_actions = _safe("bookmarker/social-execution.json") or {}
    tas_trade = _safe("trader/tas-trade.json") or {}
    reward_status = _safe("trader/reward-status.json") or {}
    wiki_brief = _safe("shared/wiki-execution-brief.json") or {}
    community_heat = _safe("shared/community-heat.json") or {}
    runtime_status = _safe("shared/runtime-status.json") or {}
    heartbeat_map = _safe("shared/heartbeat-map.json") or {}
    bm_latest = _safe("bookmarker/latest.json") or {}
    main_latest = _safe("main/latest.json") or {}
    trader_latest = _safe("trader/latest.json") or {}
    bm_drafts = _safe("bookmarker/social-drafts.json") or {}

    # ── Helper: get timestamp from data ──
    def _get_ts(data: dict, *keys: str) -> str | None:
        for k in keys:
            v = data.get(k)
            if v:
                return v
        return None

    # ── 1. Dependency Graph ──
    # Chain 1: X Sync → Topic Brief → Social Intent → Social Actions
    sh_ts = _get_ts(source_health, "updated_at", "fetched_at")
    tb_ts = _get_ts(topic_brief, "generated_at", "updated_at")
    si_ts = _get_ts(social_int, "issued_at", "generated_at", "updated_at")
    sa_ts = _get_ts(bm_social_actions, "generated_at")

    # Chain 2: Reward Status → TAS_trade → Treasury Policy → Claim/Trade
    rs_ts = _get_ts(reward_status, "generated_at", "updated_at")
    tt_ts = _get_ts(tas_trade, "generated_at", "updated_at")
    tp_ts = _get_ts(treasury, "issued_at", "generated_at", "updated_at")
    # Claim/Trade: use latest trader execution
    today = datetime.now(timezone.utc).date()
    ct_ts = None
    for delta in range(3):
        d = today - timedelta(days=delta)
        rec = _load(RUNTIME / "trader" / f"executions-{d}.json") or {}
        items = rec.get("items") or []
        if items:
            ct_ts = items[-1].get("ts")
            break

    # Chain 3: Raw → Wiki Compile → Agent Read → Decision
    raw_ts = None
    platform_manifest = _load(WORKSPACE / "wiki" / "tagclaw-platform" / "raw" / "manifest.json") or {}
    raw_ts = platform_manifest.get("fetched_at")
    if not raw_ts:
        raw_dir = WORKSPACE / "wiki" / "tagclaw-platform" / "raw"
        if raw_dir.is_dir():
            raw_ts = _newest_mtime_iso(raw_dir)
    wb_ts = _get_ts(wiki_brief, "compiled_at")
    wb_bucket_ts = wiki_brief.get("valid_until") or wb_ts
    ar_ts = _get_ts(main_latest, "generated_at", "updated_at")  # agent read = heartbeat
    dec_ts = _get_ts(last_dec, "updated_at")

    def _node(nid: str, label: str, layer: int, ts: str | None, profile: str = "runtime", bucket_ts: str | None = None, bucket_profile: str | None = None) -> dict:
        bucket = _freshness_bucket(bucket_ts or ts, bucket_profile or profile)
        return {"id": nid, "label": label, "layer": layer, "status": bucket, "freshness_bucket": bucket, "ts": ts}

    def _edge(src: str, dst: str, label: str, src_ts: str | None, dst_ts: str | None, src_profile: str = "runtime", dst_profile: str = "runtime", src_bucket_ts: str | None = None, dst_bucket_ts: str | None = None, src_bucket_profile: str | None = None, dst_bucket_profile: str | None = None) -> dict:
        src_bucket = _freshness_bucket(src_bucket_ts or src_ts, src_bucket_profile or src_profile)
        dst_bucket = _freshness_bucket(dst_bucket_ts or dst_ts, dst_bucket_profile or dst_profile)
        if src_bucket == "bootstrap" or dst_bucket == "bootstrap":
            status = "bootstrap"
        elif src_bucket == "critical" or not src_ts:
            status = "broken"
        elif src_bucket in ("stale", "critical"):
            status = "degraded"
        elif dst_bucket in ("stale", "critical"):
            status = "degraded"
        else:
            status = "ok"
        return {"from": src, "to": dst, "label": label, "status": status}

    nodes = [
        # Chain 1
        _node("x_sync", "X Sync", 1, sh_ts, "runtime"),
        _node("topic_brief", "Topic Brief", 1, tb_ts, "runtime"),
        _node("social_intent", "Social Intent", 1, si_ts, "runtime"),
        _node("social_actions", "Social Actions", 1, sa_ts, "runtime"),
        # Chain 2
        _node("reward_status", "Reward Status", 2, rs_ts, "runtime"),
        _node("tas_trade", "TAS_trade", 2, tt_ts, "runtime"),
        _node("treasury_policy", "Treasury Policy", 2, tp_ts, "runtime"),
        _node("claim_trade", "Claim/Trade", 2, ct_ts, "runtime"),
        # Chain 3
        _node("raw", "Raw", 3, raw_ts, "monthly"),
        _node("wiki_compile", "Wiki Compile", 3, wb_ts, "weekly", wb_bucket_ts, "valid_until" if wiki_brief.get("valid_until") else "weekly"),
        _node("agent_read", "Agent Read", 3, ar_ts, "runtime"),
        _node("decision", "Decision", 3, dec_ts, "runtime"),
    ]

    edges = [
        # Chain 1
        _edge("x_sync", "topic_brief", "sync", sh_ts, tb_ts, "runtime", "runtime"),
        _edge("topic_brief", "social_intent", "brief", tb_ts, si_ts, "runtime", "runtime"),
        _edge("social_intent", "social_actions", "execute", si_ts, sa_ts, "runtime", "runtime"),
        # Chain 2
        _edge("reward_status", "tas_trade", "score", rs_ts, tt_ts, "runtime", "runtime"),
        _edge("tas_trade", "treasury_policy", "policy", tt_ts, tp_ts, "runtime", "runtime"),
        _edge("treasury_policy", "claim_trade", "execute", tp_ts, ct_ts, "runtime", "runtime"),
        # Chain 3
        _edge("raw", "wiki_compile", "ingest", raw_ts, wb_ts, "monthly", "weekly", None, wb_bucket_ts, None, "valid_until" if wiki_brief.get("valid_until") else "weekly"),
        _edge("wiki_compile", "agent_read", "read", wb_ts, ar_ts, "weekly", "runtime", wb_bucket_ts, None, "valid_until" if wiki_brief.get("valid_until") else "weekly", None),
        _edge("agent_read", "decision", "decide", ar_ts, dec_ts, "runtime", "runtime"),
    ]

    dependency_graph = {"nodes": nodes, "edges": edges}

    # ── 2. State Machines ──
    def _infer_main_step() -> str:
        si_bucket = _freshness_bucket(si_ts, "runtime")
        dec_bucket = _freshness_bucket(dec_ts, "runtime")
        if dec_bucket == "fresh":
            return "verify"
        if si_bucket == "fresh":
            return "write_intents"
        tas_bucket = _freshness_bucket(_get_ts(_safe("main/tas-latest.json") or {}, "updated_at", "generated_at"), "runtime")
        if tas_bucket == "fresh":
            return "plan"
        return "observe"

    def _infer_bookmarker_step() -> str:
        tb_bucket = _freshness_bucket(tb_ts, "runtime")
        draft_list = bm_drafts.get("drafts") or []
        exec_status = bm_social_actions.get("status")
        if exec_status == "ok" and _freshness_bucket(sa_ts, "runtime") == "fresh":
            return "publish"
        if exec_status and _freshness_bucket(sa_ts, "runtime") in ("fresh", "aging"):
            return "execute"
        if draft_list:
            return "draft"
        if tb_bucket in ("fresh", "aging"):
            return "brief"
        return "sync"

    def _infer_trader_step() -> str:
        tt_bucket = _freshness_bucket(tt_ts, "runtime")
        tp_bucket = _freshness_bucket(tp_ts, "runtime")
        if ct_ts and _freshness_bucket(ct_ts, "runtime") == "fresh":
            return "verify"
        if tp_bucket == "fresh":
            return "execute"
        if tt_bucket == "fresh":
            return "decide"
        rs_bucket = _freshness_bucket(rs_ts, "runtime")
        if rs_bucket == "fresh":
            return "score"
        return "observe"

    state_machines = {
        "main": {
            "current_step": _infer_main_step(),
            "steps": ["observe", "plan", "write_intents", "verify"],
        },
        "bookmarker": {
            "current_step": _infer_bookmarker_step(),
            "steps": ["sync", "brief", "draft", "execute", "publish"],
        },
        "trader": {
            "current_step": _infer_trader_step(),
            "steps": ["observe", "score", "decide", "execute", "verify"],
        },
    }

    # ── 3. Countdowns ──
    # Heartbeat estimates: main 6h, bookmarker 2h, trader 2h
    def _next_heartbeat(agent_ts: str | None, period_hours: float) -> dict:
        if not agent_ts:
            return {"next_at": None, "estimated": True}
        dt = _parse_dt(agent_ts)
        if not dt:
            return {"next_at": None, "estimated": True}
        next_at = dt + timedelta(hours=period_hours)
        return {"next_at": next_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "estimated": True}

    main_hb_ts = _get_ts(main_latest, "generated_at", "updated_at")
    bm_hb_ts = _get_ts(bm_latest, "generated_at", "updated_at")
    trader_hb_ts = _get_ts(trader_latest, "generated_at", "updated_at")

    # Claim threshold progress
    claimable_usd = tas_trade.get("claimable_usd_raw") or 0
    threshold_usd = 2.0
    claim_ratio = round(min(1.0, claimable_usd / threshold_usd), 3) if threshold_usd > 0 else 0

    countdowns = {
        "next_main_heartbeat_at": _next_heartbeat(main_hb_ts, 6),
        "next_bookmarker_heartbeat_at": _next_heartbeat(bm_hb_ts, 2),
        "next_trader_heartbeat_at": _next_heartbeat(trader_hb_ts, 2),
        "social_intent_expires_at": social_int.get("expires_at"),
        "treasury_policy_expires_at": treasury.get("expires_at"),
        "wiki_brief_valid_until": wiki_brief.get("valid_until"),
        "claim_threshold_progress": {
            "current_usd": round(claimable_usd, 4) if claimable_usd else 0,
            "threshold_usd": threshold_usd,
            "ratio": claim_ratio,
        },
    }

    # ── 4. Intelligence ──
    heat_data = community_heat
    heat_ticks = heat_data.get("ticks") or {}
    top_themes = (wiki_brief.get("top_themes") or [])[:3]

    # Community heat visual: sorted by rank asc, then trend_score desc
    heat_visual: list[dict] = []
    for tick, v in heat_ticks.items():
        trend_score = v.get("composite_score", v.get("trend_score", 0)) or 0
        heat_rank = v.get("heat_rank")
        trending_rank = v.get("trending_rank")
        display_rank = heat_rank if heat_rank is not None else trending_rank
        intensity = min(1.0, max(0.0, trend_score)) if trend_score else (
            min(1.0, max(0.1, 1.0 - (display_rank - 1) * 0.15)) if display_rank else 0.3
        )
        heat_visual.append({
            "tick": tick,
            "trend": v.get("trend", "stable"),
            "trend_score": round(trend_score, 3),
            "rank": display_rank,
            "previous_rank": v.get("previous_heat_rank"),
            "rank_delta": v.get("heat_rank_delta", 0),
            "yesterday_rank": v.get("yesterday_heat_rank"),
            "yesterday_rank_delta": v.get("yesterday_heat_rank_delta"),
            "intensity": round(intensity, 3),
            "social_score": round(v.get("social_score", 0) or 0, 3),
            "trade_score": round(v.get("trade_score", 0) or 0, 3),
            "social_delta": round(v.get("social_delta", 0) or 0, 3),
            "trade_delta": round(v.get("trade_delta", 0) or 0, 3),
            "social_burst_score": round(v.get("social_burst_score", 0) or 0, 3),
            "social_sustained_score": round(v.get("social_sustained_score", 0) or 0, 3),
            "trade_burst_score": round(v.get("trade_burst_score", 0) or 0, 3),
            "trade_sustained_score": round(v.get("trade_sustained_score", 0) or 0, 3),
            "composite_burst_score": round(v.get("composite_burst_score", 0) or 0, 3),
            "composite_sustained_score": round(v.get("composite_sustained_score", 0) or 0, 3),
            "composite_delta": round(v.get("composite_delta", 0) or 0, 3),
            "social_posts_24h": v.get("social_posts_24h", 0),
            "social_engagement_24h": round(v.get("social_engagement_24h", 0) or 0, 3),
            "social_posts_7d": v.get("social_posts_7d", 0),
            "social_engagement_7d": round(v.get("social_engagement_7d", 0) or 0, 3),
            "trade_count_24h": v.get("trade_count_24h", 0),
            "trade_volume_24h": round(v.get("trade_volume_24h", 0) or 0, 3),
            "trade_count_7d": v.get("trade_count_7d", 0),
            "trade_volume_7d": round(v.get("trade_volume_7d", 0) or 0, 3),
            "social_momentum": v.get("social_momentum"),
            "trade_momentum": v.get("trade_momentum"),
            "data_coverage": v.get("data_coverage") or [],
        })
    heat_visual.sort(key=lambda x: (x.get("rank") or 999, -(x.get("trend_score") or 0)))

    # Stale paths: nodes that are stale or critical
    stale_paths = [n["label"] for n in nodes if n["status"] in ("stale", "critical")][:3]

    # Hottest signal
    top_rising = heat_data.get("top_rising") or []
    hottest = top_rising[0] if top_rising else None
    hottest_signal = f"{hottest} rising" if isinstance(hottest, str) and hottest else (
        f"{hottest.get('tick', '?')} rising" if isinstance(hottest, dict) else "no signal"
    )

    intelligence = {
        "top_themes": [{"name": th.get("name", ""), "heat_score": th.get("heat_score", 0)} for th in top_themes],
        "community_heat_visual": heat_visual,
        "community_heat_formula": heat_data.get("score_formula") or {},
        "community_heat_windows": heat_data.get("windows") or {},
        "stale_paths": stale_paths,
        "hottest_signal": hottest_signal,
    }

    return JSONResponse({
        "dependency_graph": dependency_graph,
        "state_machines": state_machines,
        "countdowns": countdowns,
        "intelligence": intelligence,
    })


@app.get("/api/monitor/steemit")
def api_monitor_steemit():
    """Return latest Steemit community monitor data."""
    path = WORKSPACE / "memory" / "steemit-community-monitor-latest.json"
    if not path.exists():
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = _ws_args.port
    print(f"Self-IP Agency Dashboard  →  http://localhost:{port}", flush=True)
    print(f"Workspace: {WORKSPACE}", flush=True)
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
