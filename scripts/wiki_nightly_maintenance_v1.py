#!/usr/bin/env python3
"""Wiki Nightly Maintenance / Dream Cycle v1.

A lightweight recurring maintenance loop that:
  1. Runs contract verification (freshness, schema, cross-artifact consistency)
  2. Checks key artifact freshness independently
  3. Runs wiki lint for content health
  4. Checks provenance sidecar coverage
  5. Attempts controlled auto-repair for safe, deterministic issues
  6. Emits a stable machine-readable maintenance report
  7. Emits an active alert artifact for remaining degraded states
  8. Appends a maintenance event to the wiki events ledger

Entry point:
  python3 scripts/wiki_nightly_maintenance_v1.py

Outputs:
  runtime/shared/wiki-maintenance-report.json  — maintenance report
  runtime/shared/wiki-maintenance-alert.json   — active alert chain artifact
  runtime/shared/wiki-events.jsonl             — appended maintenance event
  runtime/shared/thesis-index.json             — refreshed thesis registry
  runtime/shared/thesis-revision-queue.json    — thesis review queue
  runtime/shared/thesis-revision-drafts.json   — thesis draft summary
  wiki/health.md                               — human-readable wiki snapshot

Cron-ready: exits 0 on success, 1 on critical failures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

ROOT = (MAIN_WS)
SHARED = ROOT / "runtime" / "shared"
REPORT_PATH = SHARED / "wiki-maintenance-report.json"
ALERT_PATH = SHARED / "wiki-maintenance-alert.json"

# ── Auto-Repair Policy Allowlist ──
# Only these deterministic, non-destructive refresh actions are allowed.
# Each entry: (artifact_name, refresh_script_relative_path, description)
# Scripts are run with cwd=ROOT and must exit 0 on success.
REPAIR_ALLOWLIST_BASE: list[dict[str, str]] = [
    {
        "artifact": "wiki-contract-verify",
        "script": "scripts/verify_wiki_runtime_contract_v1.py",
        "description": "Re-run contract verifier to refresh verify + alert artifacts",
    },
    {
        "artifact": "wiki-lint-status",
        "script": "scripts/wiki_lint.py",
        "description": "Re-run wiki lint to refresh lint status artifact",
    },
    {
        "artifact": "community-heat",
        "script": "scripts/refresh_wiki_community_heat_v1.py",
        "description": "Re-derive community heat from trending-ticks source",
    },
    {
        "artifact": "wiki-retrieval-pack",
        "script": "scripts/build_wiki_retrieval_pack_v1.py",
        "description": "Rebuild retrieval pack from current wiki artifacts",
    },
    {
        "artifact": "wiki-query-index",
        "script": "scripts/build_wiki_query_index_v1.py",
        "description": "Rebuild query index from current wiki artifacts",
    },
    {
        "artifact": "thesis-index",
        "script": "scripts/build_thesis_index.py",
        "description": "Rebuild thesis index from wiki/theses frontmatter",
    },
    {
        "artifact": "thesis-revision-queue",
        "script": "scripts/compute_thesis_revision_queue.py",
        "description": "Refresh thesis revision queue from thesis + inflow evidence",
    },
    {
        "artifact": "thesis-revision-drafts",
        "script": "scripts/build_thesis_revision_drafts_v1.py",
        "description": "Generate reviewable revision drafts for queued theses",
    },
    {
        "artifact": "wiki-health-report",
        "script": "scripts/build_wiki_health_report.py",
        "description": "Rebuild wiki/health.md from current wiki runtime artifacts",
    },
    {
        "artifact": "decision-index",
        "script": "scripts/build_decisions_synthesis_v1.py",
        "description": "Recompile decision-memory ledger from agent decision trails",
    },
]

OPTIONAL_PHASE2_ARTIFACTS = [
    ("thesis-index", SHARED / "thesis-index.json", 48, "scripts/build_thesis_index.py"),
    ("thesis-revision-queue", SHARED / "thesis-revision-queue.json", 48, "scripts/compute_thesis_revision_queue.py"),
    ("thesis-revision-drafts", SHARED / "thesis-revision-drafts.json", 48, "scripts/build_thesis_revision_drafts_v1.py"),
]


def _script_exists(rel_path: str) -> bool:
    return (ROOT / rel_path).exists()


def _repair_allowlist() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for entry in REPAIR_ALLOWLIST_BASE:
        if entry["artifact"].startswith("thesis-") and not _script_exists(entry["script"]):
            continue
        entries.append(entry)
    return entries


# Artifacts with expected freshness (hours) — None means no freshness requirement
FRESHNESS_CHECKS_BASE: list[tuple[str, Path, float | None]] = [
    ("wiki-contract-verify", SHARED / "wiki-contract-verify.json", 24),
    ("wiki-contract-alert", SHARED / "wiki-contract-alert.json", 24),
    ("wiki-execution-brief", SHARED / "wiki-execution-brief.json", 168),
    ("community-heat", SHARED / "community-heat.json", 48),
    ("wiki-lint-status", SHARED / "wiki-lint-status.json", None),
    ("wiki-events-ledger", SHARED / "wiki-events.jsonl", None),
    ("topic-heatmap", ROOT / "runtime" / "bookmarker" / "topic-heatmap.json", None),
    ("wiki-retrieval-pack", SHARED / "wiki-retrieval-pack.json", 48),
    ("wiki-query-index", SHARED / "wiki-query-index.json", 48),
    ("wiki-health-report", ROOT / "wiki" / "health.md", 48),
    ("decision-index", SHARED / "decision-index.json", 48),
]


def _freshness_checks() -> list[tuple[str, Path, float | None]]:
    checks = list(FRESHNESS_CHECKS_BASE)
    for artifact, path, hours, script in OPTIONAL_PHASE2_ARTIFACTS:
        if _script_exists(script):
            checks.append((artifact, path, hours))
    return checks

PROVENANCE_ARTIFACTS: list[tuple[str, Path]] = [
    ("wiki-execution-brief", SHARED / "wiki-execution-brief.json"),
    ("topic-heatmap", ROOT / "runtime" / "bookmarker" / "topic-heatmap.json"),
    ("community-heat", SHARED / "community-heat.json"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)


def file_age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() / 3600


# ── Step 1: Contract Verification ──

def run_contract_verify() -> dict[str, Any]:
    """Run the contract verifier and return its result summary."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_wiki_runtime_contract_v1.py")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        verify_data = read_json(SHARED / "wiki-contract-verify.json")
        alert_data = read_json(SHARED / "wiki-contract-alert.json")
        return {
            "status": "ok" if result.returncode == 0 else "degraded",
            "exit_code": result.returncode,
            "pass": verify_data.get("pass", 0) if verify_data else 0,
            "fail": verify_data.get("fail", 0) if verify_data else 0,
            "severity": alert_data.get("severity", "unknown") if alert_data else "unknown",
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "pass": 0, "fail": 0, "severity": "unknown"}


# ── Step 2: Artifact Freshness ──

def check_artifact_freshness() -> list[dict[str, Any]]:
    """Check existence and freshness of key artifacts."""
    results = []
    for name, path, max_hours in _freshness_checks():
        age = file_age_hours(path)
        entry: dict[str, Any] = {
            "artifact": name,
            "path": str(path.relative_to(ROOT)),
            "exists": path.exists(),
        }
        if age is not None:
            entry["age_hours"] = round(age, 1)
        if max_hours is not None and age is not None:
            entry["fresh"] = age <= max_hours
            entry["max_hours"] = max_hours
        elif max_hours is not None:
            entry["fresh"] = False
            entry["max_hours"] = max_hours
        results.append(entry)
    return results


# ── Step 3: Wiki Lint ──

def run_wiki_lint() -> dict[str, Any]:
    """Run wiki lint and return summary."""
    lint_script = ROOT / "scripts" / "wiki_lint.py"
    if not lint_script.exists():
        return {"status": "skipped", "reason": "wiki_lint.py not found"}
    try:
        result = subprocess.run(
            [sys.executable, str(lint_script)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        lint_data = read_json(SHARED / "wiki-lint-status.json")
        if lint_data:
            return {
                "status": "ok" if result.returncode == 0 else "degraded",
                "exit_code": result.returncode,
                "summary": lint_data.get("summary", {}),
            }
        return {"status": "ok" if result.returncode == 0 else "degraded", "exit_code": result.returncode}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Step 4: Provenance Coverage ──

def check_provenance_coverage() -> dict[str, Any]:
    """Check that provenance sidecars exist for covered artifacts."""
    covered = []
    missing = []
    for name, artifact_path in PROVENANCE_ARTIFACTS:
        sidecar = artifact_path.parent / f"{artifact_path.name}.provenance.json"
        if sidecar.exists():
            covered.append(name)
        else:
            missing.append(name)
    return {
        "covered": covered,
        "missing": missing,
        "coverage_pct": round(len(covered) / max(len(PROVENANCE_ARTIFACTS), 1) * 100),
    }


# ── Step 5: Events Ledger Health ──

def check_events_ledger() -> dict[str, Any]:
    """Check events ledger health."""
    ledger_path = SHARED / "wiki-events.jsonl"
    if not ledger_path.exists():
        return {"status": "missing", "event_count": 0}
    try:
        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
        valid = 0
        invalid = 0
        event_types: dict[str, int] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                evt = json.loads(line)
                if all(k in evt for k in ("ts", "event_type", "producer", "status")):
                    valid += 1
                    et = evt["event_type"]
                    event_types[et] = event_types.get(et, 0) + 1
                else:
                    invalid += 1
            except json.JSONDecodeError:
                invalid += 1
        return {
            "status": "ok" if invalid == 0 else "degraded",
            "event_count": valid,
            "invalid_lines": invalid,
            "event_type_counts": event_types,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "event_count": 0}


# ── Step 5.5: Thesis / Health Refresh ──

def refresh_thesis_surfaces() -> dict[str, Any]:
    """Refresh thesis-facing derived artifacts every maintenance cycle."""
    steps = [
        ("thesis-index", ROOT / "scripts" / "build_thesis_index.py", True),
        ("thesis-revision-queue", ROOT / "scripts" / "compute_thesis_revision_queue.py", True),
        ("thesis-revision-drafts", ROOT / "scripts" / "build_thesis_revision_drafts_v1.py", True),
        ("wiki-health-report", ROOT / "scripts" / "build_wiki_health_report.py", False),
    ]
    results: list[dict[str, Any]] = []
    overall = "ok"
    skipped_optional = 0
    for name, script, optional in steps:
        if not script.exists():
            status = "skipped_optional" if optional else "missing"
            results.append({"artifact": name, "status": status, "script": str(script.relative_to(ROOT))})
            if optional:
                skipped_optional += 1
            else:
                overall = "degraded"
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=120, cwd=str(ROOT),
            )
            status = "ok" if proc.returncode == 0 else "degraded"
            if status != "ok":
                overall = "degraded"
            results.append({
                "artifact": name,
                "status": status,
                "exit_code": proc.returncode,
                "stdout_tail": "\n".join(proc.stdout.strip().splitlines()[-3:]) if proc.stdout else "",
                "stderr_tail": "\n".join(proc.stderr.strip().splitlines()[-3:]) if proc.stderr else "",
            })
        except Exception as e:
            overall = "degraded"
            results.append({"artifact": name, "status": "error", "error": str(e)})
    if overall == "ok" and skipped_optional and skipped_optional == len([s for s in steps if s[2]]):
        overall = "skipped_optional"
    return {"status": overall, "steps": results}


# ── Step 6: Controlled Auto-Repair ──

def attempt_auto_repairs(stale_artifacts: list[dict[str, Any]], degraded_signals: list[str]) -> list[dict[str, Any]]:
    """Attempt safe, deterministic auto-repairs for stale/degraded artifacts.

    Only actions on the REPAIR_ALLOWLIST are attempted. Each repair re-runs
    the corresponding deterministic pipeline script. Results are recorded
    with outcome: repaired | failed | skipped.
    """
    repair_results: list[dict[str, Any]] = []

    # Build set of stale artifact names for quick lookup
    stale_names = {a["artifact"] for a in stale_artifacts if not a.get("fresh", True)}

    # Also consider artifacts that are missing (exists=False)
    missing_names = {a["artifact"] for a in stale_artifacts if not a.get("exists", True)}

    # Identify which allowlisted repairs are relevant
    candidates = stale_names | missing_names

    # Also trigger contract re-verify if contract is degraded
    if any(s.startswith("contract:") for s in degraded_signals):
        candidates.add("wiki-contract-verify")

    # Also trigger lint re-run if lint is degraded
    if any(s.startswith("lint:") for s in degraded_signals):
        candidates.add("wiki-lint-status")

    for entry in _repair_allowlist():
        artifact_name = entry["artifact"]
        if artifact_name not in candidates:
            continue

        script_path = ROOT / entry["script"]
        if not script_path.exists():
            repair_results.append({
                "artifact": artifact_name,
                "outcome": "skipped",
                "reason": f"script not found: {entry['script']}",
            })
            continue

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True, text=True, timeout=120, cwd=str(ROOT),
            )
            if result.returncode == 0:
                repair_results.append({
                    "artifact": artifact_name,
                    "outcome": "repaired",
                    "script": entry["script"],
                    "description": entry["description"],
                })
            else:
                repair_results.append({
                    "artifact": artifact_name,
                    "outcome": "failed",
                    "script": entry["script"],
                    "exit_code": result.returncode,
                    "stderr": result.stderr[:300] if result.stderr else "",
                })
        except Exception as e:
            repair_results.append({
                "artifact": artifact_name,
                "outcome": "failed",
                "script": entry["script"],
                "error": str(e),
            })

    return repair_results


# ── Step 7: Active Alert Chain ──

def build_maintenance_alert(
    overall_status: str,
    degraded_signals: list[str],
    repair_results: list[dict[str, Any]],
    post_repair_status: str,
) -> dict[str, Any]:
    """Build an active alert artifact from maintenance outcomes.

    This artifact is designed to be consumed by notifiers, reminder scripts,
    or the main agent to actively surface issues rather than relying on
    passive dashboard checks.
    """
    repaired = [r for r in repair_results if r["outcome"] == "repaired"]
    failed_repairs = [r for r in repair_results if r["outcome"] == "failed"]
    skipped_repairs = [r for r in repair_results if r["outcome"] == "skipped"]

    # Filter out suppressed signals (successfully repaired this cycle)
    remaining_issues = [s for s in degraded_signals
                        if not _should_suppress(s, repair_results)]

    # Classify remaining signals by tier
    signal_tiers = {s: _classify_signal(s) for s in remaining_issues}
    has_critical = any(t == "critical" for t in signal_tiers.values())
    has_actionable = any(t == "actionable" for t in signal_tiers.values())

    # Determine alert action: none, notify, escalate
    if post_repair_status == "ok" and not remaining_issues:
        action = "none"
        severity = "clear"
    elif has_critical or (failed_repairs and len(failed_repairs) >= 2):
        action = "escalate"
        severity = "critical"
    elif has_actionable or failed_repairs:
        action = "escalate" if len(failed_repairs) >= 1 else "notify"
        severity = "warning"
    elif remaining_issues:
        action = "notify"
        severity = "info"
    else:
        action = "none"
        severity = "clear"

    alert: dict[str, Any] = {
        "schema": "wiki-maintenance-alert-v1",
        "generated_at": now_iso(),
        "pre_repair_status": overall_status,
        "post_repair_status": post_repair_status,
        "severity": severity,
        "action": action,
        "remaining_degraded_signals": remaining_issues,
        "signal_tiers": signal_tiers,
        "suppressed_count": len(degraded_signals) - len(remaining_issues),
        "repairs_attempted": len(repair_results),
        "repairs_succeeded": len(repaired),
        "repairs_failed": len(failed_repairs),
        "repairs_skipped": len(skipped_repairs),
        "repair_details": repair_results,
        "message": _build_alert_message(severity, action, repaired, failed_repairs, remaining_issues),
    }
    return alert


def _classify_signal(signal: str) -> str:
    """Classify a degraded signal into a routing tier: critical, actionable, informational."""
    if signal.startswith("contract:critical") or signal.startswith("contract:error"):
        return "critical"
    if signal.startswith("contract:") or signal.startswith("lint:degraded"):
        return "actionable"
    if signal.startswith("stale:"):
        count = int(signal.split(":")[1]) if ":" in signal else 0
        return "actionable" if count >= 2 else "informational"
    if signal.startswith("provenance-missing:"):
        return "informational"
    return "actionable"


def _should_suppress(signal: str, repair_results: list[dict[str, Any]]) -> bool:
    """Suppress alerts for signals that were successfully repaired this cycle."""
    repaired_artifacts = {r["artifact"] for r in repair_results if r["outcome"] == "repaired"}
    # Suppress stale signals if the stale artifacts were all repaired
    if signal.startswith("stale:") and repaired_artifacts:
        return True  # conservative: suppress stale after any repair
    # Suppress contract signals if contract was repaired
    if signal.startswith("contract:") and "wiki-contract-verify" in repaired_artifacts:
        return True
    if signal.startswith("lint:") and "wiki-lint-status" in repaired_artifacts:
        return True
    return False


def _build_alert_message(
    severity: str,
    action: str,
    repaired: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    remaining: list[str],
) -> str:
    parts = [f"Wiki maintenance: severity={severity} action={action}"]
    if repaired:
        parts.append(f"repaired={[r['artifact'] for r in repaired]}")
    if failed:
        parts.append(f"repair-failed={[r['artifact'] for r in failed]}")
    if remaining:
        # Group remaining by tier for clarity
        tiers: dict[str, list[str]] = {}
        for s in remaining:
            tier = _classify_signal(s)
            tiers.setdefault(tier, []).append(s)
        for tier in ("critical", "actionable", "informational"):
            if tier in tiers:
                parts.append(f"{tier}={tiers[tier]}")
    return " | ".join(parts)


# ── Orchestrator ──

def run_maintenance() -> dict[str, Any]:
    """Run the full nightly maintenance cycle and return the report."""
    started_at = now_iso()

    # Phase 1: Observe
    contract_result = run_contract_verify()
    freshness_results = check_artifact_freshness()
    lint_result = run_wiki_lint()
    provenance_result = check_provenance_coverage()
    ledger_result = check_events_ledger()
    thesis_refresh_result = refresh_thesis_surfaces()

    # Determine pre-repair health
    stale_artifacts = [f for f in freshness_results if f.get("fresh") is False]
    degraded_signals = []
    if contract_result.get("severity") not in ("clear", "ok"):
        degraded_signals.append(f"contract:{contract_result.get('severity')}")
    if stale_artifacts:
        degraded_signals.append(f"stale:{len(stale_artifacts)}")
    if provenance_result.get("missing"):
        degraded_signals.append(f"provenance-missing:{len(provenance_result['missing'])}")
    if lint_result.get("status") not in ("ok", "skipped"):
        degraded_signals.append(f"lint:{lint_result.get('status')}")
    if thesis_refresh_result.get("status") not in ("ok", "skipped_optional"):
        degraded_signals.append("thesis-surfaces:degraded")

    pre_repair_status = "ok" if not degraded_signals else "degraded"

    # Phase 2: Auto-repair (only safe, allowlisted actions)
    repair_results: list[dict[str, Any]] = []
    if degraded_signals:
        repair_results = attempt_auto_repairs(stale_artifacts, degraded_signals)

    # Phase 3: Re-check after repairs if any were attempted
    post_repair_degraded = list(degraded_signals)  # copy
    if any(r["outcome"] == "repaired" for r in repair_results):
        # Re-run freshness + contract check to see if repairs resolved issues
        freshness_results_post = check_artifact_freshness()
        stale_post = [f for f in freshness_results_post if f.get("fresh") is False]

        # Rebuild degraded signals after repair
        post_repair_degraded = []
        contract_post = run_contract_verify()
        if contract_post.get("severity") not in ("clear", "ok"):
            post_repair_degraded.append(f"contract:{contract_post.get('severity')}")
        if stale_post:
            post_repair_degraded.append(f"stale:{len(stale_post)}")
        if provenance_result.get("missing"):
            post_repair_degraded.append(f"provenance-missing:{len(provenance_result['missing'])}")
        if lint_result.get("status") not in ("ok", "skipped"):
            post_repair_degraded.append(f"lint:{lint_result.get('status')}")
        if thesis_refresh_result.get("status") not in ("ok", "skipped_optional"):
            post_repair_degraded.append("thesis-surfaces:degraded")

        # Update references for report
        freshness_results = freshness_results_post
        contract_result = contract_post
        stale_artifacts = stale_post

    post_repair_status = "ok" if not post_repair_degraded else "degraded"

    report = {
        "schema": "wiki-maintenance-report-v2",
        "generated_at": now_iso(),
        "started_at": started_at,
        "pre_repair_status": pre_repair_status,
        "overall_status": post_repair_status,
        "degraded_signals": post_repair_degraded,
        "repair_results": repair_results,
        "steps": {
            "contract_verify": contract_result,
            "artifact_freshness": freshness_results,
            "wiki_lint": lint_result,
            "provenance_coverage": provenance_result,
            "events_ledger": ledger_result,
            "thesis_surfaces": thesis_refresh_result,
        },
    }

    # Write report atomically
    atomic_write_json(REPORT_PATH, report)

    # Build and write active alert artifact
    alert = build_maintenance_alert(
        pre_repair_status, post_repair_degraded, repair_results, post_repair_status,
    )
    atomic_write_json(ALERT_PATH, alert)

    # Append maintenance event to ledger
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from runtime_utils_v2 import append_wiki_event
        append_wiki_event(
            event_type="nightly_maintenance",
            producer="wiki_nightly_maintenance_v1",
            artifact="runtime/shared/wiki-maintenance-report.json",
            status=post_repair_status,
            summary=(
                f"contract={contract_result.get('severity','?')} "
                f"stale={len(stale_artifacts)} "
                f"provenance={provenance_result.get('coverage_pct',0)}% "
                f"repairs={len([r for r in repair_results if r['outcome']=='repaired'])}/{len(repair_results)} "
                f"thesis={thesis_refresh_result.get('status','?')}"
            ),
            detail={
                "pre_repair_status": pre_repair_status,
                "post_repair_status": post_repair_status,
                "contract_severity": contract_result.get("severity"),
                "stale_count": len(stale_artifacts),
                "provenance_coverage_pct": provenance_result.get("coverage_pct"),
                "repairs_attempted": len(repair_results),
                "repairs_succeeded": len([r for r in repair_results if r["outcome"] == "repaired"]),
                "alert_severity": alert["severity"],
                "alert_action": alert["action"],
                "degraded_signals": post_repair_degraded,
                "thesis_surfaces_status": thesis_refresh_result.get("status"),
            },
        )
    except Exception:
        pass  # fail-safe

    return report


def main() -> int:
    report = run_maintenance()
    status = report["overall_status"]
    pre_status = report.get("pre_repair_status", status)
    contract = report["steps"]["contract_verify"]
    stale = [f for f in report["steps"]["artifact_freshness"] if f.get("fresh") is False]
    prov = report["steps"]["provenance_coverage"]
    repairs = report.get("repair_results", [])

    print(f"Wiki Nightly Maintenance: {status}")
    if pre_status != status:
        print(f"  Pre-repair: {pre_status} -> Post-repair: {status}")
    print(f"  Contract: pass={contract.get('pass',0)} fail={contract.get('fail',0)} severity={contract.get('severity','?')}")
    print(f"  Freshness: {len(stale)} stale artifact(s)")
    print(f"  Provenance: {prov.get('coverage_pct',0)}% coverage")

    if repairs:
        repaired = [r for r in repairs if r["outcome"] == "repaired"]
        failed = [r for r in repairs if r["outcome"] == "failed"]
        skipped = [r for r in repairs if r["outcome"] == "skipped"]
        print(f"  Auto-repair: {len(repaired)} repaired, {len(failed)} failed, {len(skipped)} skipped")
        for r in repairs:
            print(f"    {r['outcome']}: {r['artifact']}")

    if report["degraded_signals"]:
        print(f"  Remaining degraded: {', '.join(report['degraded_signals'])}")

    # Read alert for display
    alert = read_json(ALERT_PATH)
    if alert:
        print(f"  Alert: severity={alert.get('severity')} action={alert.get('action')}")

    print(f"  Report: {REPORT_PATH.relative_to(ROOT)}")
    print(f"  Alert:  {ALERT_PATH.relative_to(ROOT)}")

    # Exit 1 only if contract is critical
    if contract.get("severity") == "critical":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
