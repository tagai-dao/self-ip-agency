#!/usr/bin/env python3
"""Regression tests for build_x_tweets_wiki_v1.py."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from build_x_tweets_wiki_v1 import compile_workspace  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def seed_registry(ws: Path) -> None:
    cfg = ws / 'config'
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / 'wiki_topic_registry.json').write_text(json.dumps({
        'schema': 'wiki-topic-registry-v1',
        'concepts': {
            'ATOC': {
                'canonical_name': 'ATOC',
                'aliases': ['AgentInfrastructure'],
                'wiki_file': 'wiki/concepts/ATOC.md',
                'category': 'agent-infra',
            }
        },
        'ticks': {},
    }), encoding='utf-8')
    concepts = ws / 'wiki' / 'concepts'
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / 'ATOC.md').write_text('# ATOC\n', encoding='utf-8')


def test_compile_workspace() -> None:
    ws = Path(tempfile.mkdtemp(prefix='x-tweets-wiki-'))
    seed_registry(ws)
    raw_dir = ws / 'raw' / 'x-tweets' / 'tweets'
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / '123.json').write_text(json.dumps({
        'schema': 'raw-x-tweet.v1',
        'tweet_id': '123',
        'author_handle': '0xNought',
        'created_at': '2026-04-20T00:00:00Z',
        'is_reply': False,
        'text': 'ATOC is real infrastructure for agent systems',
        'url': 'https://x.com/0xNought/status/123',
        'engagement': {'likes': 1, 'retweets': 2, 'replies': 3, 'views': 4},
    }), encoding='utf-8')

    summary = compile_workspace(ws)
    target = ws / 'wiki' / 'synthesis' / 'tweets' / '123.md'
    _assert(summary['status'] == 'ok', f"unexpected status: {summary['status']}")
    _assert(summary['compiled_items'] == 1, f"compiled mismatch: {summary['compiled_items']}")
    _assert(target.exists(), 'compiled markdown missing')
    content = target.read_text(encoding='utf-8')
    _assert('ATOC' in content, 'concept should be present in compiled markdown')


def main() -> int:
    tests = [('compile workspace', test_compile_workspace)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'PASS: {name}')
            passed += 1
        except Exception as e:
            print(f'FAIL: {name}: {e}')
    print(f'SUMMARY: {passed}/{len(tests)} passed')
    return 0 if passed == len(tests) else 1


if __name__ == '__main__':
    raise SystemExit(main())
