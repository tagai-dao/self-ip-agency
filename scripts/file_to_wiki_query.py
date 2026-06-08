#!/usr/bin/env python3
"""file_to_wiki_query.py — File a generated brief / digest into wiki/queries/.

Karpathy LLM-Wiki principle #4: "good answers can be filed back into the
wiki as new pages." Our daily trader social brief and bookmarker X reco
digest are exactly that — high-density synthesis the agents produce, but
they currently land in runtime/* and disappear from the knowledge base.

This adapter is the bridge. Each brief generator calls it once at the
end of its run; the adapter writes a markdown copy into
``wiki/queries/YYYY-MM-DD/<source>-<HHMM>.md`` with proper frontmatter
(title, type=wiki-query, source, tags, related_concepts) and emits a
structured event to ``runtime/shared/wiki-events.jsonl``.

Output filename is deterministic per (date, source, HHMM) so the same
brief regenerated within the same minute overwrites rather than
duplicates. Different brief runs throughout the day each get their own
file — this is the compounding Karpathy wants.

Usage as a library:

    from file_to_wiki_query import file_brief_to_wiki
    file_brief_to_wiki(
        source_md_path=Path('runtime/trader/social-brief-2026-05-21.md'),
        source_agent='trader',
        title='Trader Social Brief — 2026-05-21T01:23',
        tags=['social-brief', 'trader', 'on-chain'],
        related_concepts=['[[concepts/AgentInfrastructure]]',
                          '[[concepts/TokenEconomy]]'],
    )

Or as a CLI:

    python3 file_to_wiki_query.py \\
        --source-md runtime/trader/social-brief-2026-05-21.md \\
        --source-agent trader \\
        --title "Trader Social Brief — 2026-05-21" \\
        --tags social-brief trader on-chain \\
        --related "[[concepts/AgentInfrastructure]]" "[[concepts/TokenEconomy]]"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
WIKI_QUERIES_DIR = WORKSPACE / "wiki" / "queries"
sys.path.insert(0, str(WORKSPACE / "scripts"))
from runtime_utils import append_wiki_event, path_ref  # noqa: E402


def _yaml_list(items: list[str]) -> str:
    """Render a YAML inline list with quoted entries. Wikilinks contain
    [[ and ]] which YAML treats literally inside double quotes."""
    if not items:
        return "[]"
    parts = []
    for v in items:
        s = str(v).replace('"', '\\"')
        parts.append(f'"{s}"')
    return "[" + ", ".join(parts) + "]"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9一-鿿]+", "-", text.lower()).strip("-")
    return s[:60] or "untitled"


def file_brief_to_wiki(
    source_md_path: Path,
    source_agent: str,
    title: str,
    tags: list[str],
    related_concepts: list[str] | None = None,
    *,
    file_stem: str | None = None,
    date_override: str | None = None,
) -> Path | None:
    """Copy ``source_md_path`` content into wiki/queries/YYYY-MM-DD/...md
    with frontmatter; append log entry; return the written path.

    ``file_stem`` lets the caller pin the filename (useful for runs that
    happen multiple times per day — pass ``f"trader-social-{HHMM}"``).
    When omitted, derived from ``title``.

    ``date_override`` (YYYY-MM-DD) controls which date subdir the file
    lands in. Defaults to UTC today, which is right for live briefs but
    wrong for backfills — the conversation archiver passes the session's
    original date so historical sessions spread across the calendar.
    """
    if not source_md_path.exists():
        print(f"[file_to_wiki_query] source missing: {source_md_path}", file=sys.stderr)
        return None
    try:
        body = source_md_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[file_to_wiki_query] read failed: {e}", file=sys.stderr)
        return None

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = (date_override or now.strftime("%Y-%m-%d")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = now.strftime("%Y-%m-%d")
    stem = (file_stem or _slug(title)).strip("-")

    out_dir = WIKI_QUERIES_DIR / date_str
    out_path = out_dir / f"{stem}.md"

    related = related_concepts or []
    frontmatter = (
        "---\n"
        f"title: \"{title.replace(chr(34), chr(39))}\"\n"
        "type: wiki-query\n"
        f"subtype: brief-mirror\n"
        f"date: {date_str}\n"
        f"generated_at: {ts}\n"
        f"source_agent: {source_agent}\n"
        f"source_file: {source_md_path}\n"
        f"tags: {_yaml_list(tags)}\n"
        f"related_concepts: {_yaml_list(related)}\n"
        "---\n\n"
    )

    # If the source body already starts with `# Title`, keep it. Otherwise
    # prepend one so the rendered page has a top-level heading.
    body_stripped = body.lstrip()
    if not body_stripped.startswith("# "):
        body_stripped = f"# {title}\n\n" + body_stripped

    _atomic_write(out_path, frontmatter + body_stripped)
    append_wiki_event(
        event_type="wiki_query_mirror",
        producer="file_to_wiki_query",
        entity=title,
        artifact=path_ref(out_path, WORKSPACE),
        status="ok",
        summary=f"source={source_agent} tags={len(tags)} related={len(related)}",
        detail={
            "source_agent": source_agent,
            "query_type": "mirror",
            "subtype": "brief-mirror",
            "generated_at": ts,
        },
    )
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-md", required=True, help="Path to source markdown")
    p.add_argument("--source-agent", required=True,
                   choices=["main", "trader", "bookmarker", "claude-dispatch"],
                   help="Which agent produced this brief")
    p.add_argument("--title", required=True)
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--related", nargs="*", default=[])
    p.add_argument("--file-stem", default=None,
                   help="Pin filename stem; defaults to slug of title")
    args = p.parse_args()

    out = file_brief_to_wiki(
        source_md_path=Path(args.source_md),
        source_agent=args.source_agent,
        title=args.title,
        tags=args.tags,
        related_concepts=args.related,
        file_stem=args.file_stem,
    )
    if not out:
        return 1
    print(f"[file_to_wiki_query] written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
