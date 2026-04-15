#!/usr/bin/env python3
"""Wiki Lint — Health check for wiki/concepts/ directory.

Checks:
  1. Broken wikilinks — [[link]] targets not found in concepts/ or synthesis/
  2. Stale concepts — last_compiled_at or updated > 30 days ago
  3. Orphan concepts — not referenced by any other concept page and not in INDEX.md
  4. Empty concepts — body word count < 100

Outputs:
  - wiki/lint/latest-report.md  (human-readable)
  - runtime/shared/wiki-lint-status.json (machine-readable)

Usage:
  python3 wiki_lint.py
  python3 wiki_lint.py --wiki-dir /path/to/wiki --workspace /path/to/workspace
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent


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


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=str(path.parent),
                                     suffix='.tmp', delete=False,
                                     encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
        tmp = f.name
    os.replace(tmp, path)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end == -1:
        return {}
    fm_block = text[3:end]
    result: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ':' in line and not line.strip().startswith('-'):
            key, _, val = line.partition(':')
            result[key.strip()] = val.strip().strip('"\'')
    return result


def get_body(text: str) -> str:
    if not text.startswith('---'):
        return text
    end = text.find('\n---', 3)
    if end == -1:
        return text
    return text[end + 4:]


def extract_wikilinks(text: str) -> list[str]:
    links: list[str] = []
    for m in re.finditer(r'\[\[([^\]]+)\]\]', text):
        raw = m.group(1)
        raw = raw.split('|')[0]
        raw = raw.split('#')[0]
        raw = raw.strip()
        if raw:
            links.append(raw)
    return links


def days_since(date_str: str) -> int | None:
    try:
        d = date.fromisoformat(date_str[:10])
        return (date.today() - d).days
    except Exception:
        return None


def check_broken_links(concepts_dir: Path, synthesis_dir: Path) -> list[dict]:
    known: set[str] = set()
    if concepts_dir.exists():
        for p in concepts_dir.glob('*.md'):
            known.add(p.stem)
            try:
                content = p.read_text(encoding='utf-8')
                if content.startswith('---'):
                    fm_end = content.find('---', 3)
                    if fm_end > 0:
                        fm = content[3:fm_end]
                        for line in fm.splitlines():
                            line = line.strip()
                            if line.startswith('- ') and ':' not in line:
                                alias = line.lstrip('- ').strip()
                                if alias:
                                    known.add(alias)
            except Exception:
                pass
    if synthesis_dir.exists():
        for sub in synthesis_dir.iterdir():
            if sub.is_dir():
                for p in sub.glob('*.md'):
                    known.add(f'{sub.name}/{p.stem}')
                    known.add(p.stem)

    issues: list[dict] = []
    if not concepts_dir.exists():
        return issues

    for page in sorted(concepts_dir.glob('*.md')):
        try:
            text = page.read_text(encoding='utf-8')
        except Exception:
            continue
        for link in extract_wikilinks(text):
            if link.startswith('people/') or link.startswith('community-profiles/'):
                continue
            normalized = link.replace('concepts/', '')
            if normalized not in known:
                issues.append({
                    'source_file': page.name,
                    'broken_link': link,
                })
    return issues


def check_stale(concepts_dir: Path, threshold_days: int = 30) -> list[dict]:
    issues: list[dict] = []
    if not concepts_dir.exists():
        return issues
    for page in sorted(concepts_dir.glob('*.md')):
        try:
            text = page.read_text(encoding='utf-8')
        except Exception:
            continue
        fm = parse_frontmatter(text)
        date_str = fm.get('last_compiled_at') or fm.get('updated') or ''
        if not date_str:
            issues.append({'file': page.name, 'reason': 'missing last_compiled_at/updated'})
            continue
        days = days_since(date_str)
        if days is not None and days > threshold_days:
            issues.append({'file': page.name, 'days_since_update': days})
    return issues


def check_orphans(concepts_dir: Path, index_path: Path) -> list[dict]:
    if not concepts_dir.exists():
        return []
    all_stems = {p.stem for p in concepts_dir.glob('*.md')}
    ref_count: dict[str, int] = {s: 0 for s in all_stems}

    for page in concepts_dir.glob('*.md'):
        try:
            text = page.read_text(encoding='utf-8')
        except Exception:
            continue
        for link in extract_wikilinks(text):
            normalized = link.replace('concepts/', '').split('/')[0]
            if normalized in ref_count and normalized != page.stem:
                ref_count[normalized] += 1

    index_text = ''
    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding='utf-8')
        except Exception:
            pass

    issues: list[dict] = []
    for stem, count in sorted(ref_count.items()):
        if count == 0 and stem not in index_text and f'[[{stem}]]' not in index_text:
            issues.append({'file': f'{stem}.md', 'inbound_refs': 0})
    return issues


def check_empty(concepts_dir: Path, min_words: int = 100) -> list[dict]:
    issues: list[dict] = []
    if not concepts_dir.exists():
        return issues
    for page in sorted(concepts_dir.glob('*.md')):
        try:
            text = page.read_text(encoding='utf-8')
        except Exception:
            continue
        body = get_body(text)
        word_count = len(body.split())
        if word_count < min_words:
            issues.append({'file': page.name, 'word_count': word_count})
    return issues


def build_report_md(
    concepts_checked: int,
    broken_links: list[dict],
    stale: list[dict],
    orphans: list[dict],
    empty: list[dict],
) -> str:
    lines = [
        '---',
        f'generated_at: {now_iso()}',
        f'concepts_checked: {concepts_checked}',
        f'broken_links_count: {len(broken_links)}',
        f'stale_count: {len(stale)}',
        f'orphan_count: {len(orphans)}',
        f'empty_count: {len(empty)}',
        '---',
        '',
    ]

    lines.append(f'## Broken Links ({len(broken_links)})')
    if broken_links:
        lines.append('| Source | Broken Link |')
        lines.append('|--------|-------------|')
        for bl in broken_links:
            lines.append(f'| {bl["source_file"]} | {bl["broken_link"]} |')
    else:
        lines.append('None')
    lines.append('')

    lines.append(f'## Stale Pages ({len(stale)})')
    if stale:
        lines.append('| File | Reason |')
        lines.append('|------|--------|')
        for s in stale:
            reason = s.get('reason', f'{s.get("days_since_update", "?")} days since update')
            lines.append(f'| {s["file"]} | {reason} |')
    else:
        lines.append('None')
    lines.append('')

    lines.append(f'## Orphan Pages ({len(orphans)})')
    if orphans:
        lines.append('| File | Inbound Refs |')
        lines.append('|------|-------------|')
        for o in orphans:
            lines.append(f'| {o["file"]} | {o["inbound_refs"]} |')
    else:
        lines.append('None')
    lines.append('')

    lines.append(f'## Empty Pages ({len(empty)})')
    if empty:
        lines.append('| File | Word Count |')
        lines.append('|------|-----------|')
        for e in empty:
            lines.append(f'| {e["file"]} | {e["word_count"]} |')
    else:
        lines.append('None')
    lines.append('')

    return '\n'.join(lines)


def compute_health_score(
    concepts_checked: int,
    broken_links_count: int,
    stale_count: int,
    orphan_count: int,
    empty_count: int,
) -> float:
    if concepts_checked == 0:
        return 100.0
    bl_pct = broken_links_count / max(concepts_checked, 1)
    st_pct = stale_count / concepts_checked
    or_pct = orphan_count / concepts_checked
    em_pct = empty_count / concepts_checked
    score = 100 - bl_pct * 30 - st_pct * 20 - or_pct * 10 - em_pct * 10
    return max(0.0, min(100.0, round(score, 1)))


def main() -> int:
    parser = argparse.ArgumentParser(description='Wiki Lint — concept health check')
    parser.add_argument('--wiki-dir', default=str(WORKSPACE / 'wiki'),
                        help='Path to wiki/ directory')
    parser.add_argument('--workspace', default=str(WORKSPACE),
                        help='Workspace root')
    args = parser.parse_args()

    wiki_dir = Path(args.wiki_dir)
    workspace = Path(args.workspace)
    concepts_dir = wiki_dir / 'concepts'
    synthesis_dir = wiki_dir / 'synthesis'
    index_path = wiki_dir / 'INDEX.md'

    if not concepts_dir.exists():
        print('[wiki-lint] concepts/ directory not found, nothing to check')
        return 1

    concept_files = list(concepts_dir.glob('*.md'))
    concepts_checked = len(concept_files)

    broken_links = check_broken_links(concepts_dir, synthesis_dir)
    stale = check_stale(concepts_dir)
    orphans = check_orphans(concepts_dir, index_path)
    empty = check_empty(concepts_dir)

    print(f'[wiki-lint] {concepts_checked} concepts checked')
    print(f'[wiki-lint] broken_links: {len(broken_links)}, stale: {len(stale)}, orphans: {len(orphans)}, empty: {len(empty)}')

    report_path = wiki_dir / 'lint' / 'latest-report.md'
    report_md = build_report_md(concepts_checked, broken_links, stale, orphans, empty)
    atomic_write(report_path, report_md)
    print(f'[wiki-lint] report: {report_path}')

    health_score = compute_health_score(
        concepts_checked, len(broken_links), len(stale), len(orphans), len(empty),
    )
    status: dict[str, Any] = {
        'generated_at': now_iso(),
        'health_score': health_score,
        'broken_links_count': len(broken_links),
        'stale_count': len(stale),
        'orphan_count': len(orphans),
        'empty_count': len(empty),
        'needs_attention': health_score < 80,
    }

    status_path = workspace / 'runtime' / 'shared' / 'wiki-lint-status.json'
    atomic_write_json(status_path, status)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
