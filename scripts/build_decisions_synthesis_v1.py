#!/usr/bin/env python3
"""build_decisions_synthesis_v1.py — Tier-3 decision-memory for the self-IP Wiki.

Ingests the agents' EXISTING decision trails (read-only; no agent behaviour
change) and normalizes them into:

  - ``runtime/shared/decision-index.json`` — machine-readable decision ledger
    (schema ``self-ip-decision-index-v1``) + provenance sidecar.
  - ``wiki/synthesis/decisions/<YYYY-MM>/<date>-<agent>-<kind>-<seq>.md`` —
    human-readable records, ONLY for *significant* decisions (executed actions,
    strategy shifts, authorization changes) so the layer stays readable.

Sources (all read-only):
  - runtime/shared/strategy-ledger.jsonl        (main: strategy shifts)
  - runtime/main/tas-decisions-*.jsonl          (main: metric-justified shifts)
  - runtime/main/last-decision.json             (main: social/treasury authz)
  - runtime/trader/executions-*.json            (trader: trade/claim decisions)
  - runtime/bookmarker/planned-action-log.jsonl (bookmarker: curation actions)

Constitution: see schema/decision-rules.md. This script NEVER reads or writes
wiki/identity/ (identity-safety). Concept linking goes through wiki_registry —
no local alias maps.

Usage:
  python3 scripts/build_decisions_synthesis_v1.py [--window-days 90] [--max-index 500] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parent.parent)
SHARED = WORKSPACE / "runtime" / "shared"
DECISIONS_DIR = WORKSPACE / "wiki" / "synthesis" / "decisions"
OUTPUT_PATH = SHARED / "decision-index.json"

WINDOW_DAYS_DEFAULT = 90
MAX_INDEX_DEFAULT = 500
SCHEMA = "self-ip-decision-index-v1"
PRODUCER = "build_decisions_synthesis_v1"

# ── Optional shared helpers (graceful fallback chain: v2 → v1 → no-op) ──
try:
    from runtime_utils_v2 import append_wiki_event, write_provenance_sidecar, path_ref  # type: ignore
except Exception:  # pragma: no cover - defensive
    try:
        # Older-generation utils (present in the installer source repo).
        from runtime_utils import append_wiki_event, path_ref  # type: ignore
    except Exception:
        def append_wiki_event(*a: Any, **kw: Any) -> None:  # type: ignore[misc]
            return None

        def path_ref(path: Path, root: Path) -> str:  # type: ignore[misc]
            try:
                return str(path.relative_to(root))
            except ValueError:
                return str(path)

    def write_provenance_sidecar(*a: Any, **kw: Any):  # type: ignore[misc]
        return None  # v2-only; provenance sidecar skipped when unavailable

try:
    from wiki_registry import resolve_concept  # type: ignore
except Exception:  # pragma: no cover - defensive
    def resolve_concept(name: str) -> str:  # type: ignore[misc]
        return name


# ── Helpers ──

def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def _link_concept(*candidates: Any) -> list[str]:
    """Resolve any non-empty candidate strings to canonical concepts (registry)."""
    seen: list[str] = []
    for c in candidates:
        if not c or not isinstance(c, str):
            continue
        try:
            canon = resolve_concept(c)
        except Exception:
            canon = c
        if canon and canon not in seen:
            seen.append(canon)
    return seen


def _mk_id(agent: str, decided_at: str, kind: str, salt: str) -> str:
    day = (decided_at or "")[:10].replace("-", "") or "00000000"
    h = hashlib.sha1(f"{agent}|{decided_at}|{kind}|{salt}".encode("utf-8")).hexdigest()[:6]
    return f"decision-{agent}-{day}-{kind}-{h}"


def _record(
    *, agent: str, decided_at: str, kind: str, action: str, rationale: str,
    outcome: str = "pending", outcome_detail: str = "", metric_context: dict | None = None,
    linked_concepts: list[str] | None = None, source_ref: str = "", salt: str = "",
    significant: bool = False,
) -> dict[str, Any]:
    return {
        "id": _mk_id(agent, decided_at, kind, salt or action),
        "agent": agent,
        "decided_at": decided_at,
        "kind": kind,
        "action": (action or "").strip()[:280],
        "rationale": (rationale or "").strip()[:600],
        "metric_context": metric_context or None,
        "outcome": outcome,
        "outcome_detail": (outcome_detail or "").strip()[:280],
        "linked_concepts": linked_concepts or [],
        "source_refs": [source_ref] if source_ref else [],
        "status": "active",
        "_significant": significant,
    }


# ── Ingest per source ──

def ingest_main_strategy(records: list[dict[str, Any]]) -> None:
    for e in _read_jsonl(SHARED / "strategy-ledger.jsonl"):
        ts = e.get("generated_at") or e.get("cycle_id") or ""
        action = e.get("strategy_action") or ""
        if not action:
            continue
        # High-frequency AutoResearch strategy churn: index-only (queryable + shown
        # in the INDEX "Recent Decisions" table), no individual .md page each.
        records.append(_record(
            agent="main", decided_at=ts, kind="strategy-shift",
            action=action.replace("_", " "),
            rationale=e.get("planning_focus", ""),
            metric_context={"metric": "TAS", "targets": e.get("target_metrics", []),
                            "confidence": e.get("confidence")},
            source_ref="runtime/shared/strategy-ledger.jsonl",
            salt=e.get("strategy_id", ""),
            significant=False,
        ))


def ingest_main_tas(records: list[dict[str, Any]]) -> None:
    for p in sorted(SHARED.parent.glob("main/tas-decisions-*.jsonl")):
        for e in _read_jsonl(p):
            ts = e.get("timestamp") or e.get("date") or ""
            action = e.get("strategy_action") or ""
            if not action:
                continue
            si = e.get("social_intent") or {}
            tp = e.get("treasury_policy") or {}
            # Per-cycle TAS metric log — index-only enrichment, never its own .md
            # page (strategy-ledger is the canonical strategy-shift stream).
            records.append(_record(
                agent="main", decided_at=ts, kind="strategy-eval",
                action=action.replace("_", " "),
                rationale=f"social={si.get('status','?')} treasury={tp.get('status','?')}"
                          + (f" focus={si.get('topic_focus')}" if si.get("topic_focus") else ""),
                metric_context={"metric": "TAS", "before": e.get("previous_tas"),
                                "after": e.get("tas_total"), "delta": e.get("tas_delta")},
                outcome="ok",
                linked_concepts=_link_concept(si.get("topic_focus")),
                source_ref=path_ref(p, WORKSPACE),
                salt=str(e.get("tas_total")),
                significant=False,
            ))


def ingest_main_last_decision(records: list[dict[str, Any]]) -> None:
    e = _read_json(WORKSPACE / "runtime" / "main" / "last-decision.json")
    if not isinstance(e, dict):
        return
    ts = e.get("updated_at") or e.get("cycle_id") or ""
    reason = e.get("reason")
    rationale = " ".join(reason) if isinstance(reason, list) else (reason or "")
    sd = e.get("social_decision")
    td = e.get("treasury_decision")
    if sd:
        records.append(_record(
            agent="main", decided_at=ts, kind="social-intent",
            action=f"social: {sd} (mode={e.get('mode','?')})", rationale=rationale,
            outcome="ok" if sd in ("authorize",) else "skipped", outcome_detail=str(sd),
            source_ref="runtime/main/last-decision.json", salt="social", significant=True,
        ))
    if td:
        records.append(_record(
            agent="main", decided_at=ts, kind="treasury-policy",
            action=f"treasury: {td} (mode={e.get('mode','?')})", rationale=rationale,
            outcome="ok" if td in ("allow",) else "skipped", outcome_detail=str(td),
            source_ref="runtime/main/last-decision.json", salt="treasury", significant=True,
        ))


def ingest_trader_executions(records: list[dict[str, Any]]) -> None:
    for p in sorted((WORKSPACE / "runtime" / "trader").glob("executions-*.json")):
        d = _read_json(p)
        items = d.get("items") if isinstance(d, dict) else None
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            ts = it.get("ts") or ""
            status = (it.get("status") or "").lower()
            action = it.get("action") or ""
            tick = it.get("tick") or ""
            usd = it.get("usd")
            records.append(_record(
                agent="trader", decided_at=ts, kind="trade-execution",
                action=f"{action} {tick}".strip() + (f" (${usd:.2f})" if isinstance(usd, (int, float)) else ""),
                rationale=it.get("trigger_reason", ""),
                outcome=status or "pending",
                outcome_detail=it.get("trigger_reason", ""),
                linked_concepts=_link_concept(tick),
                source_ref=path_ref(p, WORKSPACE),
                salt=it.get("id", "") or f"{action}{tick}{ts}",
                significant=(status == "ok"),  # executed trades get an .md page
            ))


def ingest_bookmarker_actions(records: list[dict[str, Any]]) -> None:
    p = WORKSPACE / "runtime" / "bookmarker" / "planned-action-log.jsonl"
    for e in _read_jsonl(p):
        ts = e.get("ts") or ""
        action = e.get("action") or ""
        if not action:
            continue
        status = (e.get("status") or "").lower()
        records.append(_record(
            agent="bookmarker", decided_at=ts, kind="curation",
            action=f"{action}" + (f" {e.get('target_tweet_id')}" if e.get("target_tweet_id") else ""),
            rationale=e.get("intent_reason", ""),
            outcome=status or "pending",
            outcome_detail=e.get("detail", "") or "",
            source_ref="runtime/bookmarker/planned-action-log.jsonl",
            salt=str(e.get("target_tweet_id", "")) + ts,
            # Individual curation actions stay index-only (queryable) — too granular
            # for their own wiki page; they already live in planned-action-log.jsonl.
            significant=False,
        ))


# ── Significant .md record rendering ──

def render_md(rec: dict[str, Any]) -> str:
    mc = rec.get("metric_context") or {}
    mc_line = ""
    if mc:
        parts = [f"{k}={v}" for k, v in mc.items() if v not in (None, [], "")]
        mc_line = ", ".join(parts)
    fm = [
        "---",
        f"id: {rec['id']}",
        f"agent: {rec['agent']}",
        f"decided_at: {rec['decided_at']}",
        f"kind: {rec['kind']}",
        f"action: {json.dumps(rec['action'], ensure_ascii=False)}",
        f"outcome: {rec['outcome']}",
        f"status: {rec['status']}",
    ]
    if rec.get("linked_concepts"):
        fm.append("linked_concepts:")
        for c in rec["linked_concepts"]:
            fm.append(f"  - {c}")
    else:
        fm.append("linked_concepts: []")
    fm.append("---")
    body = [
        "",
        f"# [{rec['agent']}] {rec['action']}",
        "",
        "## Decision",
        rec["action"] or "_(none)_",
        "",
        "## Rationale",
        rec["rationale"] or "_(none recorded)_",
        "",
        "## Outcome",
        f"{rec['outcome']}" + (f" — {rec['outcome_detail']}" if rec.get("outcome_detail") else ""),
    ]
    if mc_line:
        body += ["", "## Metric context", mc_line]
    if rec.get("linked_concepts"):
        body += ["", "## Related concepts", " ".join(f"[[{c}]]" for c in rec["linked_concepts"])]
    if rec.get("source_refs"):
        body += ["", "## Source", " · ".join(rec["source_refs"])]
    body += ["", f"<!-- auto-compiled by {PRODUCER}; read-only synthesis of agent decision trails -->", ""]
    return "\n".join(fm) + "\n".join(body) + "\n"


def write_significant_md(rec: dict[str, Any], dry_run: bool) -> str | None:
    dt = _parse_dt(rec["decided_at"])
    if not dt:
        return None
    month = dt.strftime("%Y-%m")
    day = dt.strftime("%Y-%m-%d")
    out_dir = DECISIONS_DIR / month
    fname = f"{day}-{rec['agent']}-{rec['kind']}-{rec['id'].split('-')[-1]}.md"
    out = out_dir / fname
    if out.exists():  # idempotent: never clobber an existing record
        return path_ref(out, WORKSPACE)
    if dry_run:
        return path_ref(out, WORKSPACE)
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(render_md(rec), encoding="utf-8")
    return path_ref(out, WORKSPACE)


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        tmp = f.name
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS_DEFAULT,
                    help="how far back the index ledger retains decisions")
    ap.add_argument("--md-window-days", type=int, default=14,
                    help="only significant decisions within this recent window get an .md page")
    ap.add_argument("--max-index", type=int, default=MAX_INDEX_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records: list[dict[str, Any]] = []
    for fn in (ingest_main_strategy, ingest_main_tas, ingest_main_last_decision,
               ingest_trader_executions, ingest_bookmarker_actions):
        try:
            fn(records)
        except Exception as exc:  # one bad source must not sink the build
            print(f"WARNING: {fn.__name__} failed: {exc}", file=sys.stderr)

    # Dedup by id (keep first seen)
    by_id: dict[str, dict[str, Any]] = {}
    for r in records:
        by_id.setdefault(r["id"], r)
    records = list(by_id.values())

    # Window filter + sort newest first
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.window_days)
    def _dt_key(r: dict[str, Any]) -> datetime:
        return _parse_dt(r["decided_at"]) or datetime.min.replace(tzinfo=timezone.utc)
    records = [r for r in records if (_parse_dt(r["decided_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    records.sort(key=_dt_key, reverse=True)

    # .md pages are reserved for genuinely page-worthy decisions (executed trades +
    # main authorization changes). High-frequency streams (strategy churn, per-cycle
    # TAS evals, individual curations) stay index-only — fully queryable and shown
    # in the INDEX "Recent Decisions" table, but not one wiki page each.

    # Significant .md records: only the RECENT window (full 90d history stays in
    # the index). Keeps wiki/synthesis/decisions/ a browsable "recent decisions"
    # view instead of hundreds of near-identical strategy-flip pages. Hard cap is
    # a backfill backstop.
    md_cutoff = datetime.now(timezone.utc) - timedelta(days=args.md_window_days)
    md_candidates = [
        x for x in records
        if x.get("_significant")
        and (_parse_dt(x["decided_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= md_cutoff
    ]
    md_written: list[str] = []
    for r in md_candidates[:120]:
        ref = write_significant_md(r, args.dry_run)
        if ref:
            md_written.append(ref)

    # Index (cap, strip private flag)
    index_records = []
    for r in records[: args.max_index]:
        rr = {k: v for k, v in r.items() if not k.startswith("_")}
        index_records.append(rr)

    by_agent: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in index_records:
        by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    index = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": args.window_days,
        "count": len(index_records),
        "by_agent": by_agent,
        "by_kind": by_kind,
        "significant_md_count": len(md_written),
        "decisions": index_records,
    }

    if args.dry_run:
        print(json.dumps({"status": "dry-run", "count": len(index_records),
                          "md_would_write": len(md_written), "by_agent": by_agent,
                          "by_kind": by_kind}, ensure_ascii=False, indent=2))
        return 0

    atomic_write_json(OUTPUT_PATH, index)
    try:
        write_provenance_sidecar(
            OUTPUT_PATH, PRODUCER,
            source_refs=["runtime/shared/strategy-ledger.jsonl", "runtime/main/tas-decisions-*.jsonl",
                         "runtime/main/last-decision.json", "runtime/trader/executions-*.json",
                         "runtime/bookmarker/planned-action-log.jsonl"],
            schema_version=SCHEMA,
            facts={"count": len(index_records), "by_agent": by_agent, "by_kind": by_kind,
                   "significant_md": len(md_written)},
            root=WORKSPACE,
        )
    except Exception:
        pass
    try:
        append_wiki_event("decisions_compiled", PRODUCER,
                          artifact=path_ref(OUTPUT_PATH, WORKSPACE), status="ok",
                          summary=f"decisions={len(index_records)} md={len(md_written)}",
                          detail={"by_agent": by_agent, "by_kind": by_kind})
    except Exception:
        pass

    print(json.dumps({"status": "ok", "count": len(index_records),
                      "md_written": len(md_written), "by_agent": by_agent,
                      "by_kind": by_kind, "output": path_ref(OUTPUT_PATH, WORKSPACE)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
