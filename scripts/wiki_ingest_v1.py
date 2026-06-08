#!/usr/bin/env python3
"""Wiki Ingest v1 — Karpathy-style ingest for Obsidian Living Wiki.

Ingests a single content item (bookmark / tweet / Reddit / xiaohongshu)
into the relevant entity pages of ~/Obsidian/MyVault/wiki/.

Usage:
  python3 wiki_ingest_v1.py \\
    --vault ~/Obsidian/MyVault \\
    --source-title "推文标题" \\
    --source-url "https://x.com/..." \\
    --source-type "bookmark|tweet|reddit|xiaohongshu" \\
    --content "原文内容或摘要" \\
    --tags "AgentInfrastructure,DeSocProtocols" \\
    --dry-run   # 可选，不实际写入，只输出 patch 预览

Output JSON: {ok, entities_updated, log_appended, queries_saved, dry_run}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENTITY_TEMPLATE = """\
---
title: "{name}"
type: entity
updated: {date}
source_count: 0
tags: [{name_lower}, wiki]
---

# {name}

## 核心概念
（LLM 维护，人类只读）

## 关键项目与工具

## 重要观点与争议

## 最新动态（滚动更新，保留最近 5 条）

## 与 0xNought 工作的关联

## 相关实体

## 来源日志
（每次 ingest 后追加一行：YYYY-MM-DD | source_title | source_url）
"""

MAX_RECENT = 5


def now_date() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(path.parent),
                                     suffix='.tmp', delete=False,
                                     encoding='utf-8') as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return None


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter, return (meta_dict, body_text)."""
    if not text.startswith('---'):
        return {}, text
    end = text.find('\n---', 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip('\n')
    meta: dict[str, Any] = {}
    for line in fm_block.splitlines():
        if ':' in line:
            k, _, v = line.partition(':')
            k = k.strip()
            v = v.strip()
            # Parse simple types
            if v.isdigit():
                meta[k] = int(v)
            elif v.startswith('[') and v.endswith(']'):
                inner = v[1:-1]
                meta[k] = [x.strip() for x in inner.split(',') if x.strip()]
            else:
                meta[k] = v.strip('"\'')
    return meta, body


def render_frontmatter(meta: dict[str, Any]) -> str:
    lines = ['---']
    for k, v in meta.items():
        if isinstance(v, list):
            tags_str = ', '.join(str(x) for x in v)
            lines.append(f'{k}: [{tags_str}]')
        elif isinstance(v, int):
            lines.append(f'{k}: {v}')
        else:
            lines.append(f'{k}: "{v}"')
    lines.append('---')
    return '\n'.join(lines)


def update_section(text: str, section_heading: str, new_entry: str,
                   max_items: int | None = None) -> str:
    """Append new_entry under section_heading. If max_items set, keep only last N."""
    pattern = rf'(## {re.escape(section_heading)}\n)(.*?)(?=\n## |\Z)'
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        # Section not found; append at end
        return text.rstrip() + f'\n\n## {section_heading}\n{new_entry}\n'

    heading_part = match.group(1)
    body_part = match.group(2)

    existing_lines = [l for l in body_part.strip().splitlines() if l.strip()]
    existing_lines.append(new_entry.strip())

    if max_items is not None:
        existing_lines = existing_lines[-max_items:]

    new_body = '\n'.join(existing_lines)
    replacement = f'{heading_part}{new_body}\n'
    return text[:match.start()] + replacement + text[match.end():]


def update_entity_page(
    entity_path: Path,
    source_title: str,
    source_url: str,
    source_type: str,
    content: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Update one entity page with a new ingest. Returns patch info."""
    today = now_date()
    text = read_text(entity_path)
    created = False

    if text is None:
        # Create new entity page
        name = entity_path.stem
        text = ENTITY_TEMPLATE.format(
            name=name, date=today, name_lower=name.lower()
        )
        created = True

    meta, body = parse_frontmatter(text)
    source_count = int(meta.get('source_count', 0)) + 1
    meta['source_count'] = source_count
    meta['updated'] = today

    # Update 最新动态 (keep last 5)
    recent_entry = f'- {today} | [{source_title}]({source_url}) ({source_type})'
    body = update_section(body, '最新动态（滚动更新，保留最近 5 条）', recent_entry, MAX_RECENT)

    # Update 来源日志
    log_entry = f'{today} | {source_title} | {source_url}'
    body = update_section(body, '来源日志', log_entry)

    new_text = render_frontmatter(meta) + '\n\n' + body.lstrip('\n')
    patch = {
        'entity': entity_path.name,
        'created': created,
        'source_count_after': source_count,
        'recent_entry': recent_entry,
    }

    if not dry_run:
        atomic_write(entity_path, new_text)

    return patch


def update_index(index_path: Path, updated_entities: list[str], dry_run: bool) -> bool:
    """Update source_count and last_updated columns in index.md for changed entities."""
    text = read_text(index_path)
    if text is None:
        return False

    today = now_date()
    for entity_name in updated_entities:
        # Match table row containing the entity wikilink
        pattern = rf'(\[\[{re.escape(entity_name)}\]\][^\|]*\|[^\|]*\|)\s*\d*\s*\|[^\|]*\|'
        # Simpler: find line with entity and update last two columns
        lines = text.splitlines()
        new_lines = []
        for line in lines:
            if f'[[{entity_name}]]' in line:
                # Parse cells
                cells = line.split('|')
                if len(cells) >= 5:
                    # | Page | Summary | Source Count | Last Updated |
                    # cells[0] = '', cells[1]=Page, cells[2]=Summary, cells[3]=Count, cells[4]=Date, cells[5]=''
                    try:
                        count_val = int(cells[3].strip()) + 1
                    except Exception:
                        count_val = 1
                    cells[3] = f' {count_val} '
                    cells[4] = f' {today} '
                    line = '|'.join(cells)
            new_lines.append(line)
        text = '\n'.join(new_lines)

    if not dry_run:
        atomic_write(index_path, text)
    return True


def append_wiki_log(log_path: Path, source_title: str, dry_run: bool) -> bool:
    today = now_date()
    entry = f'\n## [{today}] ingest | {source_title}\n'
    text = read_text(log_path) or '# Wiki Log\n\n（追加日志从这里开始）\n'
    new_text = text.rstrip('\n') + entry

    if not dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(log_path, new_text)
    return True


def save_query(queries_dir: Path, source_title: str, source_url: str,
               content: str, entities: list[str], dry_run: bool) -> str | None:
    """Save analysis insight to queries/ if content is non-trivial (>50 chars)."""
    if len(content.strip()) < 50:
        return None

    today = now_date()
    slug = re.sub(r'[^\w\-]', '-', source_title.lower())[:40].strip('-')
    filename = f'{today}-{slug}.md'
    path = queries_dir / filename

    entity_links = ' '.join(f'[[{e}]]' for e in entities)
    query_text = f"""---
date: {today}
source_title: "{source_title}"
source_url: "{source_url}"
entities: [{', '.join(entities)}]
---

# {source_title}

## 关键结论

{content.strip()}

## 相关实体
{entity_links}

## 来源
- [{source_title}]({source_url})
"""

    if not dry_run:
        queries_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(path, query_text)
    return filename


def main() -> int:
    parser = argparse.ArgumentParser(description='Wiki Ingest v1 — Karpathy-style ingest')
    parser.add_argument('--vault', required=True, help='Obsidian vault path (use ~ for home)')
    parser.add_argument('--source-title', required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--source-type', default='bookmark',
                        choices=['bookmark', 'tweet', 'reddit', 'xiaohongshu'])
    parser.add_argument('--content', required=True, help='Content summary (≤200 chars recommended)')
    parser.add_argument('--tags', required=True,
                        help='Comma-separated entity page names, e.g. AgentInfrastructure,DeSocProtocols')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print patch preview without writing files')
    args = parser.parse_args()

    vault_path = Path(args.vault).expanduser().resolve()
    if not vault_path.exists():
        print(json.dumps({'ok': False, 'error': f'vault not found: {vault_path}'}))
        return 1

    wiki_dir = vault_path / 'wiki'
    entities_dir = wiki_dir / 'entities'
    queries_dir = wiki_dir / 'queries'
    index_path = wiki_dir / 'index.md'
    log_path = wiki_dir / 'log.md'

    if not wiki_dir.exists() and not args.dry_run:
        print(json.dumps({'ok': False, 'error': f'wiki dir not found: {wiki_dir}'}))
        return 1

    entity_names = [t.strip() for t in args.tags.split(',') if t.strip()]
    if not entity_names:
        print(json.dumps({'ok': False, 'error': 'no entity tags provided'}))
        return 1

    entities_updated: list[str] = []
    patches: list[dict] = []

    for entity_name in entity_names:
        entity_file = entities_dir / f'{entity_name}.md'
        try:
            patch = update_entity_page(
                entity_path=entity_file,
                source_title=args.source_title,
                source_url=args.source_url,
                source_type=args.source_type,
                content=args.content,
                dry_run=args.dry_run,
            )
            entities_updated.append(entity_name)
            patches.append(patch)
        except Exception as e:
            patches.append({'entity': entity_name, 'error': str(e)})

    # Update index
    index_updated = update_index(index_path, entities_updated, args.dry_run) if index_path.exists() else False

    # Append to log
    log_appended = append_wiki_log(log_path, args.source_title, args.dry_run)

    # Save query insight
    query_saved = save_query(
        queries_dir=queries_dir,
        source_title=args.source_title,
        source_url=args.source_url,
        content=args.content,
        entities=entities_updated,
        dry_run=args.dry_run,
    )

    result = {
        'ok': True,
        'entities_updated': entities_updated,
        'patches': patches,
        'index_updated': index_updated,
        'log_appended': log_appended,
        'queries_saved': query_saved,
        'dry_run': args.dry_run,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
