#!/usr/bin/env python3
"""propagate_inflow_to_concepts.py — Bridge Tier-1 atoms into Tier-2 concepts.

Closes the "Karpathy gap" between weekly compile_wiki.py runs: while raw
tweet pages keep landing in wiki/synthesis/tweets/ every hour, the
concept pages in wiki/concepts/ only get refreshed by the weekly LLM
compile. That gap leaves concept pages 1-6 days behind the actual inflow
at all times.

What this script does:
  - Scans wiki/synthesis/tweets/*.md for tweets that have a primary_theme
  - For each theme, finds the matching wiki/concepts/<Theme>.md
  - Appends one-line summary entries under a managed
    "## Recent Signals (auto-propagated)" section
  - Idempotent: tweet_id is included so re-runs don't duplicate
  - Caps the section at MAX_ENTRIES so concept pages stay readable
  - Tail of the page (compile footer) is preserved

These appended signals are a *stop-gap*. The weekly wiki-compile-weekly
cron supersedes them by re-synthesizing the concept body, at which point
this section gets rebuilt fresh on the next propagation pass.

Usage:
  python3 propagate_inflow_to_concepts.py
  python3 propagate_inflow_to_concepts.py --dry-run
  python3 propagate_inflow_to_concepts.py --window-days 14
  python3 propagate_inflow_to_concepts.py --max-entries 20
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
WIKI = WORKSPACE / "wiki"
CONCEPTS_DIR = WIKI / "concepts"
TWEETS_DIR = WIKI / "synthesis" / "tweets"

SECTION_HEADER = "## Recent Signals (auto-propagated)"
SECTION_NOTE = (
    "> 由 propagate_inflow_to_concepts.py 自动维护，每条带 tweet_id 去重。"
    "下次 compile_wiki 全量重写本页时会合并这些信号到正文中。"
)
MAX_ENTRIES_DEFAULT = 30
WINDOW_DAYS_DEFAULT = 14


def parse_frontmatter(text: str) -> dict[str, str]:
    """Tiny YAML-ish frontmatter parser. Matches the same surface area
    wiki_lint.py / write_wiki_query.py use — flat string values only."""
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


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def collect_recent_tweets(window_days: int) -> dict[str, list[dict[str, Any]]]:
    """Walk wiki/synthesis/tweets/ once and bucket by primary_theme.

    Returns ``{theme: [{tweet_id, author, created_at, text, url}, ...]}``
    sorted newest first per theme. Tweets older than ``window_days`` are
    dropped to keep the propagation cost bounded.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    out: dict[str, list[dict[str, Any]]] = {}
    if not TWEETS_DIR.exists():
        return out

    for tw in TWEETS_DIR.glob("*.md"):
        try:
            text = tw.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        theme = (fm.get("primary_theme") or fm.get("theme") or "").strip()
        if not theme:
            continue
        created_str = (fm.get("created_at") or "").strip()
        if not created_str:
            continue
        try:
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if created_dt < cutoff:
            continue

        tweet_id = (fm.get("tweet_id") or tw.stem).strip()
        author = (fm.get("author") or "").strip()
        url = (fm.get("source_url") or "").strip()
        if not url and author and tweet_id:
            url = f"https://x.com/{author}/status/{tweet_id}"
        # Body is everything after the second '---'; first non-empty
        # content line approximates the tweet text.
        body_start = text.find("\n---", 3)
        body = text[body_start + 4:] if body_start > 0 else text
        snippet = ""
        for line in body.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(">"):
                continue
            snippet = s
            break

        out.setdefault(theme, []).append({
            "tweet_id": tweet_id,
            "author": author,
            "created_at": created_str,
            "created_dt": created_dt,
            "text": snippet,
            "url": url,
        })

    # Sort each bucket newest-first by created_at.
    for theme in out:
        out[theme].sort(key=lambda x: x["created_dt"], reverse=True)
    return out


def render_signals_section(entries: list[dict[str, Any]], max_entries: int) -> str:
    lines = [SECTION_HEADER, "", SECTION_NOTE, ""]
    seen: set[str] = set()
    rendered = 0
    for e in entries:
        tid = e["tweet_id"]
        if tid in seen:
            continue
        seen.add(tid)
        day = e["created_at"][:10]
        snippet = (e["text"] or "").replace("\n", " ")
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        author = f"@{e['author']}" if e["author"] else "@?"
        link = f"[{tid}]({e['url']})" if e["url"] else f"`{tid}`"
        lines.append(f"- {day} {author} — {snippet} · {link}")
        rendered += 1
        if rendered >= max_entries:
            break
    return "\n".join(lines) + "\n"


def replace_or_append_section(body: str, new_section: str) -> str:
    """Replace an existing 'Recent Signals (auto-propagated)' section, or
    append it just before any trailing '*Last compiled: ...*' footer.

    Concept pages from compile_wiki.py end with a one-line italic footer
    like ``*Last compiled: 2026-04-10 · Sources: 20 bookmarks*`` — we
    keep it as the page's tail anchor.
    """
    # Find the start of an existing managed section.
    sec_re = re.compile(
        r"\n+## Recent Signals \(auto-propagated\)\n(?:.|\n)*?(?=\n## |\n---\s|\n\*Last compiled|\Z)",
        re.MULTILINE,
    )
    if sec_re.search(body):
        return sec_re.sub("\n\n" + new_section, body, count=1)

    # Otherwise insert before the trailing compile footer (if any).
    footer_re = re.compile(r"\n(\*Last compiled[^\n]*)\n*\Z")
    m = footer_re.search(body)
    if m:
        head = body[: m.start()].rstrip() + "\n\n"
        tail = "\n" + m.group(1) + "\n"
        return head + new_section + tail

    # Fallback: append at the very end.
    return body.rstrip() + "\n\n" + new_section


def propagate(window_days: int, max_entries: int, dry_run: bool) -> dict[str, Any]:
    if not CONCEPTS_DIR.exists():
        return {"status": "no_concepts_dir", "concepts_touched": 0}

    by_theme = collect_recent_tweets(window_days)
    concepts_touched = 0
    skipped_themes: list[str] = []
    summary: list[dict[str, Any]] = []

    for concept_path in sorted(CONCEPTS_DIR.glob("*.md")):
        theme = concept_path.stem  # filename == theme name (e.g. AgentInfrastructure)
        entries = by_theme.get(theme)
        if not entries:
            skipped_themes.append(theme)
            continue
        try:
            original = concept_path.read_text(encoding="utf-8")
        except Exception:
            continue
        new_section = render_signals_section(entries, max_entries)
        updated = replace_or_append_section(original, new_section)
        if updated == original:
            summary.append({"theme": theme, "status": "unchanged", "entries": len(entries)})
            continue
        if not dry_run:
            atomic_write(concept_path, updated)
        concepts_touched += 1
        summary.append({"theme": theme, "status": "updated", "entries": len(entries)})

    return {
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": window_days,
        "max_entries": max_entries,
        "concepts_touched": concepts_touched,
        "concepts_skipped_no_inflow": len(skipped_themes),
        "dry_run": dry_run,
        "details": summary,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-days", type=int, default=WINDOW_DAYS_DEFAULT)
    p.add_argument("--max-entries", type=int, default=MAX_ENTRIES_DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    result = propagate(args.window_days, args.max_entries, args.dry_run)
    print(
        f"[propagate-inflow] {'(dry-run) ' if args.dry_run else ''}"
        f"concepts_touched={result['concepts_touched']} "
        f"skipped={result['concepts_skipped_no_inflow']} "
        f"window={args.window_days}d max={args.max_entries}"
    )
    for d in result["details"]:
        if d["status"] == "updated":
            print(f"  ✓ {d['theme']:30s} +{d['entries']} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
