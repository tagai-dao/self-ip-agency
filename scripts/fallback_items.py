from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def extract_items_from_archives(memory_root: Path, max_items: int = 30) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    archive_files = [
        (memory_root / 'x-posts-archive.md', 'tweet'),
        (memory_root / 'x-bookmarks-categorized.md', 'bookmark'),
    ]
    for archive_path, source_type in archive_files:
        if not archive_path.exists():
            continue
        try:
            content = archive_path.read_text(encoding='utf-8')
        except Exception:
            continue
        sections = content.split('\n## ')
        recent = sections[-max_items:] if len(sections) > max_items else sections[1:]
        for section in recent:
            lines = section.strip().split('\n')
            if not lines:
                continue
            text_lines: list[str] = []
            for line in lines[1:]:
                if line.startswith('Primary tag:'):
                    continue
                if line.startswith('Keywords:'):
                    continue
                text_lines.append(line)
            text = '\n'.join(text_lines).strip()
            if not text or len(text) < 20:
                continue
            pseudo_id = f"archive-{hashlib.md5(text[:200].encode()).hexdigest()[:12]}"
            items.append({
                'id': pseudo_id,
                'text': text,
                'author': {'username': '0xNought' if source_type == 'tweet' else 'unknown'},
                'url': '',
                'createdAt': '',
                'source_type': source_type,
                '_fallback': True,
            })
    return items


def load_x_sync_with_fallback(memory_root: Path, max_items: int = 30) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    x_sync = read_json(memory_root / 'x-sync-latest.json') or {}
    x_items = [item for item in (x_sync.get('data') or []) if isinstance(item, dict)]
    used_fallback = False
    if not x_items:
        x_items = extract_items_from_archives(memory_root, max_items=max_items)
        if x_items:
            used_fallback = True
    x_sync_doc = dict(x_sync)
    x_sync_doc['data'] = x_items
    if used_fallback:
        x_sync_doc['_fallback_items_used'] = True
        x_sync_doc['_fallback_item_count'] = len(x_items)
    return x_sync_doc, x_items, used_fallback
