#!/usr/bin/env python3
"""build_full_index.py — expand ``wiki/INDEX.md`` to the full wiki catalog.

Karpathy's design treats INDEX.md as the LLM's primary entry point when
answering a query — "read the index first to find relevant pages, then
drill into them." Our INDEX has been concept-only (30 entries out of
14,000+ wiki pages), so the LLM and a human browsing the wiki both miss
99.8% of the content.

This script regenerates INDEX.md with four sections sized for "moderate
scale" (≤500 lines, no RAG needed):

  1. Concepts        — every wiki/concepts/*.md (~30-50 entries)
  2. Theses          — every wiki/theses/*.md   (~2-10 entries)
  3. People          — top 100 by 180-day affinity, sourced from
                       runtime/bookmarker/x-reco-author-affinity-180d.json
  4. Recent queries  — wiki/queries/<last-14-days>/*.md

Runs daily after wiki-health-report-daily so the INDEX always reflects
the freshest concept compile + thesis revisions + bookmarker affinity.
Emits ``index_built`` to ``runtime/shared/wiki-events.jsonl``.

Usage:
  python3 build_full_index.py
  python3 build_full_index.py --top-people 50
  python3 build_full_index.py --query-window-days 7
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
sys.path.insert(0, str(WORKSPACE / "scripts"))
from runtime_utils import append_wiki_event, path_ref  # noqa: E402

WIKI = WORKSPACE / "wiki"
INDEX_PATH = WIKI / "INDEX.md"
CONCEPTS_DIR = WIKI / "concepts"
THESES_DIR = WIKI / "theses"
QUERIES_DIR = WIKI / "queries"
AFFINITY_PATH = WORKSPACE / "runtime" / "bookmarker" / "x-reco-author-affinity-180d.json"
DECISION_INDEX_PATH = WORKSPACE / "runtime" / "shared" / "decision-index.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Shallow YAML reader — string-typed scalars only (lists are joined)."""
    fm: dict[str, str] = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end < 0:
        return fm
    block = text[3:end]
    for line in block.splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        fm[key] = val
    return fm


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4 :].lstrip("\n")


def _extract_summary(body: str, max_chars: int = 80) -> str:
    """Best substantive opener for the page. Tries, in order:

      1. First normal paragraph (skipping HTML comments, headings, hr,
         table rows, list markers)
      2. First blockquote line (``> ``) — concept pages like
         BookmarkCuration lead with a single descriptive blockquote and
         have no other prose

    Returns a single-line summary trimmed to ``max_chars``.
    """
    paragraph: list[str] = []
    blockquote_line: str | None = None

    def _skip(line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        if s.startswith("<!--"):
            return True
        if s.startswith("#") or s.startswith("---") or s.startswith("|"):
            return True
        if s.startswith("- ") or s.startswith("* ") or re.match(r"^\d+\.", s):
            return True
        return False

    for line in body.splitlines():
        s = line.strip()
        if not s:
            if paragraph:
                break
            continue
        # Capture the first blockquote in case there's no paragraph below.
        if s.startswith(">"):
            if blockquote_line is None:
                cleaned = re.sub(r"^>\s*", "", s).strip()
                # Skip blockquote rows that are just metadata like
                # "**来源数量**: 17 条" — they aren't narrative.
                if cleaned and not cleaned.startswith("**来源") and not cleaned.startswith("**类型"):
                    blockquote_line = cleaned
            if paragraph:
                break
            continue
        if _skip(line):
            if paragraph:
                break
            continue
        paragraph.append(s)

    text = " ".join(paragraph) if paragraph else (blockquote_line or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text or "_(no body summary)_"


# ──────────────────────────────────────────────────────────────────────
# Section builders
# ──────────────────────────────────────────────────────────────────────

def render_concepts() -> list[str]:
    lines = ["## Concepts"]
    lines.append("")
    if not CONCEPTS_DIR.exists():
        lines.append("_(no wiki/concepts/ directory)_")
        return lines
    files = sorted(CONCEPTS_DIR.glob("*.md"))
    lines.append(f"_{len(files)} concept pages._ Drill into any page via the link.")
    lines.append("")
    lines.append("| Concept | Last compiled | Core idea |")
    lines.append("|---------|--------------|-----------|")
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(text)
        title = fm.get("title") or p.stem
        last = (fm.get("last_compiled_at") or fm.get("updated") or "?").split("T")[0]
        body = _strip_frontmatter(text)
        summary = _extract_summary(body, max_chars=80)
        rel = p.relative_to(WIKI).as_posix()
        lines.append(f"| [{title}]({rel}) | {last} | {summary} |")
    return lines


def render_theses() -> list[str]:
    lines = ["## Theses"]
    lines.append("")
    if not THESES_DIR.exists():
        lines.append("_(no wiki/theses/ directory)_")
        return lines
    files = [p for p in sorted(THESES_DIR.glob("*.md")) if not p.name.startswith("README")]
    if not files:
        lines.append("_(none)_")
        return lines
    lines.append(f"_{len(files)} thesis pages._ Tier-3 of the LLM-Wiki ladder.")
    lines.append("")
    lines.append("| Thesis | Scope | Status | Confidence | Last revised |")
    lines.append("|--------|-------|--------|-----------|--------------|")
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(text)
        title = fm.get("title") or p.stem
        scope = fm.get("scope") or "?"
        status = fm.get("status") or "?"
        confidence = fm.get("confidence") or "?"
        last = (fm.get("last_revised_at") or fm.get("first_compiled_at") or "?").split("T")[0]
        rel = p.relative_to(WIKI).as_posix()
        lines.append(f"| [{title}]({rel}) | {scope} | {status} | {confidence} | {last} |")
    return lines


def render_recent_decisions(limit: int = 20) -> list[str]:
    """Recent agent decisions from the decision-memory ledger (decision-index.json)."""
    lines = ["## Recent Decisions"]
    lines.append("")
    if not DECISION_INDEX_PATH.exists():
        lines.append("_(no decision-index.json — run build_decisions_synthesis_v1.py)_")
        return lines
    try:
        data = json.loads(DECISION_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        lines.append("_(decision-index JSON malformed)_")
        return lines
    decisions = data.get("decisions") or []
    by_kind = data.get("by_kind") or {}
    if not decisions:
        lines.append("_(none)_")
        return lines
    kind_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    lines.append(f"_{data.get('count', len(decisions))} decisions in ledger ({kind_summary}). Newest {min(limit, len(decisions))} below; full ledger: `runtime/shared/decision-index.json`._")
    lines.append("")
    lines.append("| When | Agent | Kind | Action | Outcome |")
    lines.append("|------|-------|------|--------|---------|")
    for d in decisions[:limit]:
        when = (d.get("decided_at") or "?").split("T")[0]
        agent = d.get("agent") or "?"
        kind = d.get("kind") or "?"
        action = (d.get("action") or "").replace("|", "\\|")[:80]
        outcome = d.get("outcome") or "?"
        lines.append(f"| {when} | {agent} | {kind} | {action} | {outcome} |")
    return lines


def render_top_people(top_n: int) -> list[str]:
    lines = [f"## People — top {top_n} by 180-day affinity"]
    lines.append("")
    if not AFFINITY_PATH.exists():
        lines.append(f"_(no affinity data at {AFFINITY_PATH.relative_to(WORKSPACE)})_")
        return lines
    try:
        data = json.loads(AFFINITY_PATH.read_text(encoding="utf-8"))
    except Exception:
        lines.append("_(affinity JSON malformed)_")
        return lines
    authors = data.get("authors") or {}
    if not authors:
        lines.append("_(empty affinity map)_")
        return lines
    ranked = sorted(
        authors.items(),
        key=lambda kv: -float(kv[1].get("weighted_affinity_180d") or 0),
    )
    top = ranked[:top_n]
    lines.append(
        f"_{len(authors)} authors tracked, {len(top)} shown. Ranking signal: "
        f"`weighted_affinity_180d = bookmarks×1.0 + likes×0.4 + comments×1.5`._"
    )
    lines.append("")
    lines.append("| # | Handle | Affinity 180d | BM 180d | Likes 180d | Tier | Last interaction |")
    lines.append("|---|--------|---------------|---------|------------|------|------------------|")
    for i, (handle, a) in enumerate(top, 1):
        affinity = float(a.get("weighted_affinity_180d") or 0)
        bm = int(a.get("bookmarks_180d") or 0)
        lk = int(a.get("likes_180d") or 0)
        tier = a.get("creator_tier") or "?"
        last_int = (a.get("last_interaction_at") or "?").split("T")[0]
        people_page = WIKI / "synthesis" / "people" / f"{handle}.md"
        if people_page.exists():
            rel = people_page.relative_to(WIKI).as_posix()
            handle_md = f"[@{handle}]({rel})"
        else:
            handle_md = f"@{handle}"
        lines.append(
            f"| {i} | {handle_md} | {affinity:.2f} | {bm} | {lk} | {tier} | {last_int} |"
        )
    return lines


def render_recent_queries(window_days: int) -> list[str]:
    lines = [f"## Recent queries (last {window_days} days)"]
    lines.append("")
    if not QUERIES_DIR.exists():
        lines.append("_(no wiki/queries/ directory)_")
        return lines
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date()
    by_date: dict[str, list[Path]] = {}
    for child in QUERIES_DIR.iterdir():
        if child.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name):
            try:
                day = datetime.strptime(child.name, "%Y-%m-%d").date()
            except Exception:
                continue
            if day < cutoff:
                continue
            for f in sorted(child.glob("*.md")):
                by_date.setdefault(child.name, []).append(f)
        elif child.is_file() and child.suffix == ".md":
            try:
                m = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc).date()
            except Exception:
                continue
            if m >= cutoff:
                by_date.setdefault(m.isoformat(), []).append(child)
    if not by_date:
        lines.append(f"_(no entries in last {window_days} days)_")
        return lines
    total = sum(len(v) for v in by_date.values())
    lines.append(f"_{total} entries across {len(by_date)} day(s)._ Compounding pile from briefs + conversations.")
    lines.append("")
    lines.append("| Date | File | Size |")
    lines.append("|------|------|------|")
    for day in sorted(by_date.keys(), reverse=True):
        for f in by_date[day]:
            try:
                size = f.stat().st_size
            except Exception:
                size = 0
            rel = f.relative_to(WIKI).as_posix()
            label = f.name
            lines.append(f"| {day} | [{label}]({rel}) | {size:,} bytes |")
    return lines


# ──────────────────────────────────────────────────────────────────────
# Top-level
# ──────────────────────────────────────────────────────────────────────

def build_index(top_people: int, query_window_days: int) -> str:
    now = datetime.now(timezone.utc)
    out: list[str] = []
    out.append("# Wiki Index")
    out.append("")
    out.append(f"> Auto-generated by `build_full_index.py` at {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    out.append(">")
    out.append("> Karpathy LLM-Wiki principle #6: INDEX is the LLM's catalog-of-everything when")
    out.append("> answering a query. Browse here first, drill into pages via the links.")
    out.append("")
    sections = [
        render_concepts(),
        render_theses(),
        render_recent_decisions(),
        render_top_people(top_people),
        render_recent_queries(query_window_days),
    ]
    for s in sections:
        out.extend(s)
        out.append("")
    out.append("---")
    out.append(f"*Generated {now.strftime('%Y-%m-%dT%H:%M:%SZ')} by build_full_index.py*")
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top-people", type=int, default=100,
                   help="How many people to include in the People section")
    p.add_argument("--query-window-days", type=int, default=14,
                   help="How far back to include wiki/queries entries")
    p.add_argument("--output", type=Path, default=INDEX_PATH,
                   help="Where to write the index (default: wiki/INDEX.md)")
    args = p.parse_args()

    content = build_index(args.top_people, args.query_window_days)
    _atomic_write(args.output, content)

    section_counts = {
        "concepts": sum(1 for _ in CONCEPTS_DIR.glob("*.md")) if CONCEPTS_DIR.exists() else 0,
        "theses": sum(1 for p in THESES_DIR.glob("*.md") if not p.name.startswith("README"))
        if THESES_DIR.exists() else 0,
        "people_shown": args.top_people,
    }
    try:
        append_wiki_event(
            event_type="index_built",
            producer="build_full_index",
            artifact=path_ref(args.output, WORKSPACE),
            status="ok",
            summary=(
                f"concepts={section_counts['concepts']} "
                f"theses={section_counts['theses']} "
                f"people={section_counts['people_shown']}"
            ),
            detail={
                "section_counts": section_counts,
                "query_window_days": args.query_window_days,
                "char_count": len(content),
            },
        )
    except Exception as exc:  # noqa: BLE001 — non-fatal
        print(f"[wiki-index] event emit failed: {exc}", file=sys.stderr)

    print(
        f"[wiki-index] wrote {args.output} "
        f"({len(content):,} chars, concepts={section_counts['concepts']}, "
        f"theses={section_counts['theses']}, people={args.top_people})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
