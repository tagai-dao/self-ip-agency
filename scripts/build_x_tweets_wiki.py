#!/usr/bin/env python3
"""build_x_tweets_wiki.py — compile raw/x-tweets into wiki/synthesis/tweets."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR / 'lib'))

from x_fetch_utils import collect_concept_matches  # noqa: E402

RAW_DIR_RELPATH = Path('raw/x-tweets/tweets')
OUT_DIR_RELPATH = Path('wiki/synthesis/tweets')
LOG_RELPATH = Path('wiki/log.md')


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, suffix='.tmp', delete=False, encoding='utf-8') as f:
        f.write(text)
        tmp = f.name
    Path(tmp).replace(path)


def load_raw_tweets(workspace: Path) -> list[dict[str, Any]]:
    raw_dir = workspace / RAW_DIR_RELPATH
    items: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return items
    for path in sorted(raw_dir.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            data['_source_path'] = str(path.relative_to(workspace))
            items.append(data)
        except Exception:
            continue
    return items


def build_markdown(item: dict[str, Any], concept_matches: list[str]) -> str:
    tweet_id = item.get('tweet_id') or 'unknown'
    author = item.get('author_handle') or 'unknown'
    title = f'Tweet {tweet_id}'
    created_at = item.get('created_at') or ''
    excerpt = (item.get('text') or '').strip().replace('\r', '')
    if len(excerpt) > 280:
        excerpt = excerpt[:277] + '...'
    concepts_yaml = ', '.join(concept_matches)
    tags = ['x', 'tweet' if not item.get('is_reply') else 'reply', 'guided-sync']
    tags_yaml = ', '.join(tags)
    quote = item.get('quote') or {}
    engagement = item.get('engagement') or {}

    body = f"""---
title: {title}
type: tweet-synthesis
tweet_id: \"{tweet_id}\"
author: \"{author}\"
created_at: {created_at}
is_reply: {str(bool(item.get('is_reply'))).lower()}
source_file: {item.get('_source_path', '')}
concepts: [{concepts_yaml}]
tags: [{tags_yaml}]
updated: {datetime.now(timezone.utc).date().isoformat()}
---

# Summary

{excerpt or 'No text available.'}

## Core Claims

- Source tweet captured via guided X sync bootstrap.
- This page is a first-pass synthesis compiled from raw data.

## Concepts

"""
    if concept_matches:
        body += ''.join(f'- {c}\n' for c in concept_matches)
    else:
        body += '- None resolved yet\n'

    body += f"""

## Engagement

- Likes: {engagement.get('likes', 0)}
- Retweets: {engagement.get('retweets', 0)}
- Replies: {engagement.get('replies', 0)}
- Views: {engagement.get('views', 0)}

"""
    if quote:
        body += f"""## Quoted Context

- @{quote.get('author_handle') or 'unknown'}: {quote.get('text') or ''}

"""

    body += f"""## Source

- URL: {item.get('url') or ''}
- Raw file: `{item.get('_source_path', '')}`
"""
    return body


def load_alias_map(workspace: Path) -> dict[str, str]:
    path = workspace / 'config' / 'wiki_topic_registry.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for canonical, meta in (data.get('concepts') or {}).items():
        out[canonical] = canonical
        for alias in meta.get('aliases') or []:
            out[str(alias)] = canonical
    return out


def compile_workspace(workspace: Path) -> dict[str, Any]:
    items = load_raw_tweets(workspace)
    out_dir = workspace / OUT_DIR_RELPATH
    out_dir.mkdir(parents=True, exist_ok=True)
    alias_map = load_alias_map(workspace)
    summary = {
        'schema': 'build-x-tweets-wiki.v1',
        'status': 'ok',
        'compiled_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'raw_items': len(items),
        'compiled_items': 0,
        'files': [],
        'warnings': [],
    }

    for item in items:
        text = item.get('text') or ''
        quote = (item.get('quote') or {}).get('text') or ''
        concepts = collect_concept_matches(f'{text}\n{quote}', alias_map)
        tweet_id = item.get('tweet_id') or 'unknown'
        md = build_markdown(item, concepts)
        target = out_dir / f'{tweet_id}.md'
        atomic_write_text(target, md)
        summary['compiled_items'] += 1
        summary['files'].append(str(target.relative_to(workspace)))

    if items:
        log_path = workspace / LOG_RELPATH
        existing = log_path.read_text(encoding='utf-8') if log_path.exists() else '# Wiki Log\n\n'
        entry = f"- {summary['compiled_at']}: compiled {summary['compiled_items']} guided X tweet synthesis pages from raw/x-tweets/.\n"
        if entry not in existing:
            atomic_write_text(log_path, existing + entry)
    else:
        summary['status'] = 'deferred'
        summary['warnings'].append('no raw/x-tweets/tweets/*.json files found')

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Compile raw/x-tweets into wiki synthesis pages')
    parser.add_argument('--workspace', default=str(WORKSPACE))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    summary = compile_workspace(Path(args.workspace).expanduser().resolve())
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"build-x-tweets-wiki: {summary['status']} | raw={summary['raw_items']} | compiled={summary['compiled_items']}")
        for w in summary['warnings']:
            print(f'warning: {w}')
    return 0 if summary['status'] in {'ok', 'deferred'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
