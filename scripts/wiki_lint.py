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


WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace"))

# Phase 4: resolver-pack integration
try:
    from load_resolver_context import load_context, is_write_allowed, ResolverContext
except ImportError:
    load_context = None  # type: ignore[assignment]
    is_write_allowed = None  # type: ignore[assignment]
    ResolverContext = None  # type: ignore[assignment,misc]

RESOLVER_TASK = 'lint-wiki'


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
    """Extract frontmatter key-value pairs (simple flat parsing)."""
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
    """Return text after frontmatter."""
    if not text.startswith('---'):
        return text
    end = text.find('\n---', 3)
    if end == -1:
        return text
    return text[end + 4:]


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[linkname]] from text. Handles [[link|alias]] and [[link#section]]."""
    links: list[str] = []
    for m in re.finditer(r'\[\[([^\]]+)\]\]', text):
        raw = m.group(1)
        # Obsidian escapes the alias pipe as '\|' inside markdown tables; treat
        # it the same as a normal alias separator so the target isn't left with a
        # trailing backslash (was a false-positive broken-link source).
        raw = raw.replace('\\|', '|')
        # Strip alias: [[link|alias]] -> link
        raw = raw.split('|')[0]
        # Strip section: [[link#section]] -> link
        raw = raw.split('#')[0]
        raw = raw.rstrip('\\').strip()
        if raw:
            links.append(raw)
    return links


def days_since(date_str: str) -> int | None:
    """Return days since a date string (YYYY-MM-DD or ISO)."""
    try:
        d = date.fromisoformat(date_str[:10])
        return (date.today() - d).days
    except Exception:
        return None


def check_broken_links(concepts_dir: Path, synthesis_dir: Path) -> list[dict]:
    """Find [[wikilinks]] pointing to nonexistent pages."""
    # Build set of known page stems + aliases from frontmatter
    known: set[str] = set()
    if concepts_dir.exists():
        for p in concepts_dir.glob('*.md'):
            known.add(p.stem)
            # Also collect aliases from frontmatter
            try:
                content = p.read_text(encoding='utf-8')
                if content.startswith('---'):
                    fm_end = content.find('---', 3)
                    if fm_end > 0:
                        fm = content[3:fm_end]
                        for line in fm.splitlines():
                            line = line.strip()
                            if line.startswith('- ') and not ':' in line:
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
            # Exclude people/ and community-profiles/ prefixes
            if link.startswith('people/') or link.startswith('community-profiles/'):
                continue
            # Normalize: strip concepts/ prefix if present
            normalized = link.replace('concepts/', '')
            if normalized not in known:
                issues.append({
                    'source_file': page.name,
                    'broken_link': link,
                })
    return issues


def check_stale(concepts_dir: Path, threshold_days: int = 30) -> list[dict]:
    """Find concept pages not updated in > threshold_days."""
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


def check_stale_by_inflow(
    concepts_dir: Path,
    synthesis_dir: Path,
    min_inflow: int = 5,
) -> list[dict]:
    """Detect concept pages that look "fresh" but have N+ new tweets in
    their theme since they were last compiled.

    The existing ``check_stale`` only catches pages whose frontmatter
    ``last_compiled_at`` / ``updated`` is past a wall-clock threshold —
    it can't see *inflow staleness* (the concept's theme has accumulated
    20+ new tweets but the concept page itself hasn't been re-synthesized).
    This is the Karpathy "wiki rotting under new sources" failure mode:
    Tier-1 atoms compound but Tier-2 summary doesn't roll forward.

    For each concept page, count tweets under ``synthesis/tweets/`` whose
    ``primary_theme`` matches the concept name and whose ``created_at`` is
    later than the concept's ``last_compiled_at``. ≥ ``min_inflow`` ⇒ warning.
    """
    issues: list[dict] = []
    if not concepts_dir.exists() or not synthesis_dir.exists():
        return issues
    tweets_dir = synthesis_dir / 'tweets'
    if not tweets_dir.exists():
        return issues

    # Pre-index synthesis/tweets/ once by primary_theme → list[created_at].
    # 12k+ files * O(1) frontmatter parse = ~1–2s wall time, no per-concept
    # full rescan.
    theme_to_tweet_ts: dict[str, list[str]] = {}
    for tw in tweets_dir.glob('*.md'):
        try:
            text = tw.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        fm = parse_frontmatter(text)
        theme = (fm.get('primary_theme') or fm.get('theme') or '').strip()
        created_at = (fm.get('created_at') or '').strip()
        if theme and created_at:
            theme_to_tweet_ts.setdefault(theme, []).append(created_at)

    for page in sorted(concepts_dir.glob('*.md')):
        try:
            text = page.read_text(encoding='utf-8')
        except Exception:
            continue
        fm = parse_frontmatter(text)
        last_str = (
            fm.get('last_compiled_at')
            or fm.get('updated')
            or fm.get('graph_sync_at')
            or ''
        )
        if not last_str:
            continue
        theme = page.stem  # filename without .md == theme name in this wiki
        candidates = theme_to_tweet_ts.get(theme) or []
        # ISO-8601 strings sort lexicographically when fields share precision.
        new_count = sum(1 for ts in candidates if ts > last_str)
        if new_count >= min_inflow:
            issues.append({
                'file': page.name,
                'theme': theme,
                'last_compiled_at': last_str,
                'new_tweets_since': new_count,
            })
    return issues


def check_orphans(concepts_dir: Path, index_path: Path) -> list[dict]:
    """Find concepts not referenced by any other concept page and not in INDEX.md."""
    if not concepts_dir.exists():
        return []

    # Count inbound references for each concept
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

    # Check INDEX.md
    index_text = ''
    if index_path.exists():
        try:
            index_text = index_path.read_text(encoding='utf-8')
        except Exception:
            pass

    issues: list[dict] = []
    for stem, count in sorted(ref_count.items()):
        if count == 0 and stem not in index_text and f'[[{stem}]]' not in index_text and f'concepts/{stem}' not in index_text:
            issues.append({'file': f'{stem}.md', 'inbound_refs': 0})
    return issues


def check_empty(concepts_dir: Path, min_words: int = 100) -> list[dict]:
    """Find concepts where body word count < min_words."""
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
    stale_by_inflow: list[dict] | None = None,
) -> str:
    """Build wiki/lint/latest-report.md content."""
    stale_by_inflow = stale_by_inflow or []
    lines = [
        '---',
        f'generated_at: {now_iso()}',
        f'concepts_checked: {concepts_checked}',
        f'broken_links_count: {len(broken_links)}',
        f'stale_count: {len(stale)}',
        f'stale_by_inflow_count: {len(stale_by_inflow)}',
        f'orphan_count: {len(orphans)}',
        f'empty_count: {len(empty)}',
        '---',
        '',
    ]

    # Broken links
    lines.append(f'## 断链（{len(broken_links)}个）')
    if broken_links:
        lines.append('| 来源文件 | 断链 |')
        lines.append('|---------|------|')
        for bl in broken_links:
            lines.append(f'| {bl["source_file"]} | {bl["broken_link"]} |')
    else:
        lines.append('无')
    lines.append('')

    # Stale
    lines.append(f'## 过期页面（{len(stale)}个）')
    if stale:
        lines.append('| 文件 | 原因 |')
        lines.append('|------|------|')
        for s in stale:
            reason = s.get('reason', f'{s.get("days_since_update", "?")} 天未更新')
            lines.append(f'| {s["file"]} | {reason} |')
    else:
        lines.append('无')
    lines.append('')

    # Orphans
    lines.append(f'## 孤儿页面（{len(orphans)}个）')
    if orphans:
        lines.append('| 文件 | 入链数 |')
        lines.append('|------|--------|')
        for o in orphans:
            lines.append(f'| {o["file"]} | {o["inbound_refs"]} |')
    else:
        lines.append('无')
    lines.append('')

    # Empty
    lines.append(f'## 空内容页面（{len(empty)}个）')
    if empty:
        lines.append('| 文件 | 字数 |')
        lines.append('|------|------|')
        for e in empty:
            lines.append(f'| {e["file"]} | {e["word_count"]} |')
    else:
        lines.append('无')
    lines.append('')

    # Stale-by-inflow: concept page hasn't moved but theme has new tweets since.
    # The "Karpathy gap" — Tier-1 keeps growing, Tier-2 doesn't roll forward.
    lines.append(f'## 待重编（按 inflow，{len(stale_by_inflow)}个）')
    if stale_by_inflow:
        lines.append('| 概念 | 主题 | 上次编译 | 之后新增推文 |')
        lines.append('|------|------|----------|---------------|')
        for s in sorted(stale_by_inflow, key=lambda x: -x.get('new_tweets_since', 0)):
            lines.append(
                f'| {s["file"]} | {s["theme"]} | {s["last_compiled_at"]} | '
                f'{s["new_tweets_since"]} |'
            )
    else:
        lines.append('无')
    lines.append('')

    return '\n'.join(lines)


def compute_health_score(
    concepts_checked: int,
    broken_links_count: int,
    stale_count: int,
    orphan_count: int,
    empty_count: int,
) -> float:
    """health_score = 100 - broken_links_pct×30 - stale_pct×20 - orphan_pct×10 - empty_pct×10"""
    if concepts_checked == 0:
        return 100.0
    bl_pct = broken_links_count / max(concepts_checked, 1)
    st_pct = stale_count / concepts_checked
    or_pct = orphan_count / concepts_checked
    em_pct = empty_count / concepts_checked
    score = 100 - bl_pct * 30 - st_pct * 20 - or_pct * 10 - em_pct * 10
    return max(0.0, min(100.0, round(score, 1)))


def _load_resolver() -> dict[str, Any] | None:
    """Load resolver context for lint-wiki task (Phase 4).

    Returns resolver metadata dict on success, None if unavailable.
    The lint workflow degrades gracefully if resolver-pack is missing.
    """
    if load_context is None:
        return None
    ctx = load_context(RESOLVER_TASK)
    if not ctx.valid:
        print(f'[wiki-lint] resolver: degraded — {ctx.error}')
        return None
    print(f'[wiki-lint] resolver: using task={ctx.task_name} (pack {ctx.pack_version}, generated {ctx.pack_generated_at})')
    if ctx.missing:
        print(f'[wiki-lint] resolver: {len(ctx.missing)} load paths missing (degraded)')
    return {
        'task_name': ctx.task_name,
        'pack_version': ctx.pack_version,
        'pack_generated_at': ctx.pack_generated_at,
        'load_paths': ctx.load_paths,
        'protected_writes': ctx.protected_writes,
        'missing': ctx.missing,
        '_ctx': ctx,
    }


def _check_write_guard(resolver_meta: dict[str, Any] | None, rel_path: str) -> bool:
    """Check if writing to rel_path is allowed by the resolver write guard.

    Returns True if write is allowed (or if resolver is not loaded).
    """
    if resolver_meta is None or is_write_allowed is None:
        return True
    ctx = resolver_meta['_ctx']
    return is_write_allowed(ctx, rel_path)


def main() -> int:
    parser = argparse.ArgumentParser(description='Wiki Lint v1 — concept health check')
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

    # Phase 4: load resolver context
    resolver_meta = _load_resolver()

    if not concepts_dir.exists():
        print('[wiki-lint] concepts/ directory not found, nothing to check')
        return 1

    concept_files = list(concepts_dir.glob('*.md'))
    concepts_checked = len(concept_files)

    broken_links = check_broken_links(concepts_dir, synthesis_dir)
    stale = check_stale(concepts_dir)
    orphans = check_orphans(concepts_dir, index_path)
    empty = check_empty(concepts_dir)
    stale_by_inflow = check_stale_by_inflow(concepts_dir, synthesis_dir)

    # Print summary
    print(f'[wiki-lint] {concepts_checked} concepts checked')
    print(f'[wiki-lint] broken_links: {len(broken_links)}, stale: {len(stale)}, '
          f'stale_by_inflow: {len(stale_by_inflow)}, orphans: {len(orphans)}, empty: {len(empty)}')

    # Phase 4: enforce write guard on output paths
    report_rel = 'wiki/lint/latest-report.md'
    status_rel = 'runtime/shared/wiki-lint-status.json'

    report_allowed = _check_write_guard(resolver_meta, report_rel)
    status_allowed = _check_write_guard(resolver_meta, status_rel)

    # Verify protected paths are NOT writable (identity guard check)
    identity_guard_ok = True
    if resolver_meta is not None:
        for protected_test in ['wiki/identity/persona.md', 'wiki/identity/key-positions.md']:
            if _check_write_guard(resolver_meta, protected_test):
                print(f'[wiki-lint] WARNING: write guard failed — {protected_test} should be protected')
                identity_guard_ok = False
            else:
                print(f'[wiki-lint] write guard: {protected_test} correctly protected ✓')

    # Write report
    report_path = wiki_dir / 'lint' / 'latest-report.md'
    if report_allowed:
        report_md = build_report_md(
            concepts_checked, broken_links, stale, orphans, empty, stale_by_inflow,
        )
        atomic_write(report_path, report_md)
        print(f'[wiki-lint] report: {report_path}')
    else:
        print(f'[wiki-lint] report: BLOCKED by write guard ({report_rel})')

    # Write status JSON
    health_score = compute_health_score(
        concepts_checked, len(broken_links), len(stale), len(orphans), len(empty),
    )
    # Stale-by-inflow doesn't deduct from the health score yet — keep
    # backward compatibility for any downstream consumer that reads the
    # score — but surface the count so dashboards/agents can act on it.
    status: dict[str, Any] = {
        'generated_at': now_iso(),
        'health_score': health_score,
        'broken_links_count': len(broken_links),
        'stale_count': len(stale),
        'stale_by_inflow_count': len(stale_by_inflow),
        'orphan_count': len(orphans),
        'empty_count': len(empty),
        'needs_attention': health_score < 80 or len(stale_by_inflow) >= 5,
    }

    # Phase 4: embed resolver metadata in status output
    if resolver_meta is not None:
        status['resolver'] = {
            'task_name': resolver_meta['task_name'],
            'pack_version': resolver_meta['pack_version'],
            'pack_generated_at': resolver_meta['pack_generated_at'],
            'load_paths_count': len(resolver_meta['load_paths']),
            'missing_count': len(resolver_meta['missing']),
            'identity_guard_ok': identity_guard_ok,
        }

    status_path = workspace / 'runtime' / 'shared' / 'wiki-lint-status.json'
    if status_allowed:
        atomic_write_json(status_path, status)
    else:
        print(f'[wiki-lint] status: BLOCKED by write guard ({status_rel})')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
