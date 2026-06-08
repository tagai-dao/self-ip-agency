#!/usr/bin/env python3
"""build_wiki_health_report.py — Single-page wiki health snapshot.

Karpathy's pattern assumes "Obsidian on one side, LLM on the other" — a
visual surface where the human browses what the LLM is maintaining. Our
OpenClaw setup is autonomous; there's no editing window. This report
substitutes: a daily-regenerated markdown at ``wiki/health.md`` you can
``cat`` (or open in any editor) to see the wiki's pulse in one place.

What's aggregated:
  - INDEX.md / log.md freshness + recent entry mix
  - Latest lint pass: broken links, stale, stale-by-inflow, orphans, empty
  - Concept inflow propagation: per-concept touch time + Recent Signals count
  - Thesis revision queue (from compute_thesis_revision_queue.py)
  - Recent ``wiki/queries/YYYY-MM-DD/`` entries (the compounding pile)
  - Top "needs attention" actions

No state-mutation: read-only across the wiki and runtime/shared/.

Usage:
  python3 build_wiki_health_report.py
  python3 build_wiki_health_report.py --output wiki/health.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
WIKI = WORKSPACE / "wiki"
SHARED = WORKSPACE / "runtime" / "shared"

INDEX_PATH = WIKI / "INDEX.md"
LOG_PATH = WIKI / "log.md"
LINT_STATUS_PATH = SHARED / "wiki-lint-status.json"
LINT_REPORT_PATH = WIKI / "lint" / "latest-report.md"
THESIS_QUEUE_PATH = SHARED / "thesis-revision-queue.json"
THESIS_DRAFTS_PATH = SHARED / "thesis-revision-drafts.json"
CONCEPTS_DIR = WIKI / "concepts"
QUERIES_DIR = WIKI / "queries"
DEFAULT_OUTPUT = WIKI / "health.md"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mtime(path: Path) -> str:
    if not path.exists():
        return "(missing)"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except Exception:
        return "(unknown)"


def _days_old(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        m = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - m).days
    except Exception:
        return None


def _count_log_types() -> tuple[int, dict[str, int]]:
    if not LOG_PATH.exists():
        return (0, {})
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    total = sum(1 for _ in text.splitlines() if _.strip())
    by_type: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^\[[0-9-]+T[0-9:]+Z\] ([a-z_]+) \|", line)
        if m:
            by_type[m.group(1)] = by_type.get(m.group(1), 0) + 1
    return (total, by_type)


def _concept_inflow_pulse() -> list[dict[str, Any]]:
    """Per-concept: file mtime + whether Recent Signals section is present."""
    out: list[dict[str, Any]] = []
    if not CONCEPTS_DIR.exists():
        return out
    for p in sorted(CONCEPTS_DIR.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        has_signals = "## Recent Signals (auto-propagated)" in text
        # Count entries in the signals section
        signal_count = 0
        if has_signals:
            sec = text.split("## Recent Signals (auto-propagated)", 1)[1]
            sec = re.split(r"\n(?:## |---\s|\*Last compiled)", sec, maxsplit=1)[0]
            signal_count = sum(1 for line in sec.splitlines() if line.lstrip().startswith("- "))
        out.append({
            "name": p.stem,
            "mtime": _mtime(p),
            "days_old": _days_old(p),
            "has_signals_section": has_signals,
            "signal_entries": signal_count,
        })
    out.sort(key=lambda x: x["days_old"] if x["days_old"] is not None else 9999, reverse=True)
    return out


def _recent_queries(limit_days: int = 7) -> list[dict[str, Any]]:
    if not QUERIES_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=limit_days)
    out: list[dict[str, Any]] = []
    # Format: wiki/queries/YYYY-MM-DD/*.md (new) or wiki/queries/*.md (legacy)
    for child in sorted(QUERIES_DIR.iterdir(), reverse=True):
        if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", child.name):
            try:
                day = datetime.strptime(child.name, "%Y-%m-%d").date()
            except Exception:
                continue
            if day < cutoff:
                continue
            for f in sorted(child.glob("*.md")):
                out.append({
                    "date": child.name,
                    "file": f.name,
                    "path": str(f.relative_to(WIKI)),
                    "size": f.stat().st_size,
                })
        elif child.is_file() and child.suffix == ".md":
            # Legacy flat layout — include if recent
            try:
                m = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc).date()
            except Exception:
                continue
            if m >= cutoff:
                out.append({
                    "date": m.isoformat(),
                    "file": child.name,
                    "path": str(child.relative_to(WIKI)),
                    "size": child.stat().st_size,
                })
    return out[:25]


def _count_historical_drafts() -> int:
    """Count .md files in the drafts dir on disk (regardless of current
    summary). Useful as an audit-trail count of all revisions ever staged."""
    drafts_dir = SHARED / "thesis-revision-drafts"
    if not drafts_dir.exists():
        return 0
    return sum(1 for _ in drafts_dir.glob("*.md"))


def build_report() -> str:
    now = datetime.now(timezone.utc)
    drafts_doc = _read_json(THESIS_DRAFTS_PATH) or {}
    pending_drafts = drafts_doc.get("drafts") or []
    historical_drafts = _count_historical_drafts()

    lines: list[str] = []
    lines.append(f"# Wiki Health — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("> Auto-generated by `build_wiki_health_report.py`. Don't edit by hand;")
    lines.append("> it gets clobbered. Tweak the source script or its inputs instead.")
    lines.append("")

    # Top banner: only when there are CURRENTLY pending drafts. This is the
    # signal you actually need to act on; historical drafts (already
    # applied) live further down as audit info.
    if pending_drafts:
        ids_preview = ", ".join(d["id"] for d in pending_drafts[:3])
        if len(pending_drafts) > 3:
            ids_preview += f", …(+{len(pending_drafts) - 3} more)"
        lines.append(
            f"⚠️ **{len(pending_drafts)} thesis revision draft(s) awaiting review** — "
            f"{ids_preview}. See **Section 4** for per-draft details and the path to read."
        )
        lines.append("")

    # ── 1. Top-level pulse ───────────────────────────────────────────
    total_log, by_type = _count_log_types()
    log_signal = ", ".join(f"{v} {k}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])[:6])
    lines.append("## 1. Pulse")
    lines.append("")
    lines.append("| 文件 | mtime | 状态 |")
    lines.append("|------|-------|------|")
    lines.append(f"| INDEX.md | {_mtime(INDEX_PATH)} | {_days_old(INDEX_PATH)}天前 |")
    lines.append(f"| log.md | {_mtime(LOG_PATH)} | {total_log} 条 ({log_signal or 'empty'}) |")
    lines.append(f"| lint latest-report.md | {_mtime(LINT_REPORT_PATH)} | {_days_old(LINT_REPORT_PATH)}天前 |")
    lines.append(f"| thesis revision queue | {_mtime(THESIS_QUEUE_PATH)} | {_days_old(THESIS_QUEUE_PATH)}天前 |")
    lines.append(f"| thesis revision drafts | {_mtime(THESIS_DRAFTS_PATH)} | {_days_old(THESIS_DRAFTS_PATH)}天前 |")
    lines.append("")

    # ── 2. Lint snapshot ─────────────────────────────────────────────
    lint = _read_json(LINT_STATUS_PATH) or {}
    lines.append("## 2. Lint snapshot")
    lines.append("")
    if lint:
        lines.append(f"- generated_at: `{lint.get('generated_at', '?')}`")
        lines.append(f"- health_score: **{lint.get('health_score', '?')}/100**")
        lines.append(f"- needs_attention: **{lint.get('needs_attention', False)}**")
        lines.append("")
        lines.append("| issue | count |")
        lines.append("|-------|------|")
        lines.append(f"| broken_links | {lint.get('broken_links_count', 0)} |")
        lines.append(f"| stale (by wall clock) | {lint.get('stale_count', 0)} |")
        lines.append(f"| **stale_by_inflow** | **{lint.get('stale_by_inflow_count', 0)}** |")
        lines.append(f"| orphans | {lint.get('orphan_count', 0)} |")
        lines.append(f"| empty | {lint.get('empty_count', 0)} |")
    else:
        lines.append("⚠️ No lint status — run `python3 scripts/wiki_lint.py`.")
    lines.append("")

    # ── 3. Concept inflow pulse ──────────────────────────────────────
    pulse = _concept_inflow_pulse()
    lines.append(f"## 3. Concepts ({len(pulse)} total)")
    lines.append("")
    if pulse:
        lines.append("| concept | file age | signals section | signal entries |")
        lines.append("|---------|----------|-----------------|----------------|")
        for c in pulse[:15]:
            sig = "✅" if c["has_signals_section"] else "❌"
            age = c["days_old"] if c["days_old"] is not None else "?"
            lines.append(
                f"| {c['name']} | {age}天 | {sig} | {c['signal_entries']} |"
            )
        if len(pulse) > 15:
            lines.append(f"| _… {len(pulse) - 15} more …_ | | | |")
    lines.append("")

    # ── 4. Thesis revision queue + drafts ────────────────────────────
    queue = _read_json(THESIS_QUEUE_PATH) or {}
    drafts = drafts_doc  # already loaded for the top banner
    lines.append("## 4. Theses needing revision")
    lines.append("")
    if queue.get("status") == "ok":
        q = queue.get("queue") or []
        lines.append(f"- theses tracked: {queue.get('theses_total', 0)}")
        lines.append(f"- pending review: **{len(q)}**")
        lines.append(f"- drafts generated (current): **{drafts.get('draft_count', 0)}**")
        lines.append(f"- drafts on disk (historical incl. applied): **{historical_drafts}**")
        lines.append(f"- rule: inflow ≥ {queue.get('inflow_threshold', '?')} OR new claims ≥ {queue.get('claims_threshold', '?')}")
        lines.append("")
        if q:
            lines.append("**Queue (theses waiting for draft generation):**")
            lines.append("")
            lines.append("| thesis | scope | new tweets | new claims | days stale |")
            lines.append("|--------|-------|------------|------------|------------|")
            for entry in q:
                ds = entry.get("days_since_revision")
                lines.append(
                    f"| `{entry['id']}` | {entry['scope']} | {entry['new_inflow_tweets']} | "
                    f"{entry['new_claims']} | {ds if ds is not None else '?'}d |"
                )
            lines.append("")
        if pending_drafts:
            lines.append("**Drafts ready for your review (open the path, then apply manually or via `apply_thesis_draft.py`):**")
            lines.append("")
            lines.append("| thesis | scope | new tweets | new claims | draft path |")
            lines.append("|--------|-------|------------|------------|------------|")
            for d in pending_drafts:
                draft_path = d.get("draft_path", "")
                # Show path relative to workspace for readability.
                try:
                    rel = str(Path(draft_path).relative_to(WORKSPACE))
                except Exception:
                    rel = draft_path
                lines.append(
                    f"| `{d['id']}` | {d.get('scope', '?')} | "
                    f"{d.get('new_inflow_tweets', 0)} | {d.get('new_claims', 0)} | "
                    f"`{rel}` |"
                )
            lines.append("")
    else:
        lines.append("⚠️ Queue not computed — run `python3 scripts/compute_thesis_revision_queue.py`.")
    lines.append("")

    # ── 5. Recent wiki/queries entries (the compounding pile) ────────
    recent = _recent_queries()
    lines.append(f"## 5. Recent wiki/queries (last 7 days, {len(recent)} entries)")
    lines.append("")
    if recent:
        lines.append("| date | file | size |")
        lines.append("|------|------|------|")
        for r in recent:
            lines.append(f"| {r['date']} | `{r['path']}` | {r['size']} bytes |")
    else:
        lines.append("_(no recent entries — brief mirrors and conversation archives feed this)_")
    lines.append("")

    # ── 6. Suggested actions ─────────────────────────────────────────
    actions: list[str] = []
    # Pending thesis drafts are the highest-leverage action when present —
    # they're synthesized proposals waiting for a human ack/edit.
    if pending_drafts:
        for d in pending_drafts:
            try:
                rel = str(Path(d.get("draft_path", "")).relative_to(WORKSPACE))
            except Exception:
                rel = d.get("draft_path", "")
            actions.append(
                f"**Review thesis draft** `{d['id']}` (scope={d.get('scope', '?')}, "
                f"+{d.get('new_inflow_tweets', 0)} new tweets) → "
                f"open `{rel}`."
            )
    if lint.get("stale_by_inflow_count", 0) >= 5:
        actions.append(
            f"Run `compile_wiki.py` — {lint['stale_by_inflow_count']} concepts have "
            f"5+ new tweets since last synthesis."
        )
    if (queue.get("queue_size") or 0) > 0 and not pending_drafts:
        # Queue non-empty but drafts missing — drafts builder hasn't caught up.
        actions.append(
            f"Run `python3 scripts/build_thesis_revision_drafts_v1.py` — "
            f"{queue['queue_size']} thesis(es) waiting for a draft."
        )
    idx_age = _days_old(INDEX_PATH) or 0
    if idx_age and idx_age > 14:
        actions.append(f"INDEX.md is {idx_age} days old — bump it via compile_wiki run.")
    if not actions:
        actions.append("All quiet. Wiki is in good shape.")

    lines.append("## 6. Suggested actions")
    lines.append("")
    for i, a in enumerate(actions, 1):
        lines.append(f"{i}. {a}")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated {now.strftime('%Y-%m-%dT%H:%M:%SZ')} by build_wiki_health_report.py*")

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()
    report = build_report()
    _atomic_write(args.output, report)
    print(f"[wiki-health] wrote {args.output} ({len(report)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
