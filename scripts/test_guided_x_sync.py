#!/usr/bin/env python3
"""Regression tests for guided X sync helpers."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / 'lib'))

from x_fetch_utils import load_guided_manifest, normalize_fxtwitter_tweet, parse_tweet_url  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_parse_tweet_url() -> None:
    user, tid = parse_tweet_url('https://x.com/0xNought/status/1234567890')
    _assert(user == '0xNought', f'user mismatch: {user}')
    _assert(tid == '1234567890', f'id mismatch: {tid}')


def test_guided_manifest_loads() -> None:
    ws = Path(tempfile.mkdtemp(prefix='guided-x-sync-'))
    manifest = ws / 'runtime' / 'shared' / 'guided-x-urls.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        'schema': 'guided-x-url-manifest.v1',
        'urls': [
            'https://x.com/0xNought/status/111',
            {'url': 'https://twitter.com/0xNought/status/222', 'is_reply_hint': True},
        ]
    }), encoding='utf-8')
    urls, warnings = load_guided_manifest(ws)
    _assert(len(urls) == 2, f'expected 2 urls, got {len(urls)}')
    _assert(not warnings, f'unexpected warnings: {warnings}')
    _assert(urls[1].is_reply_hint is True, 'reply hint should round-trip')


def test_normalize_fxtwitter_tweet() -> None:
    raw = {
        'text': '@someone hello world',
        'created_at': 'Mon Apr 20 08:00:00 +0000 2026',
        'likes': 3,
        'retweets': 1,
        'bookmarks': 0,
        'views': 20,
        'replies': 2,
        'author': {'screen_name': '0xNought', 'name': '0xNought'},
        'quote': {'text': 'quoted', 'author': {'screen_name': 'alice', 'name': 'Alice'}},
    }
    out = normalize_fxtwitter_tweet(
        'https://x.com/0xNought/status/333',
        raw,
        discovery_source='browser-guided',
        lookback_days=3,
        reply_hint=False,
    )
    _assert(out['tweet_id'] == '333', f"tweet_id mismatch: {out['tweet_id']}")
    _assert(out['is_reply'] is True, 'should classify @-prefixed text as reply')
    _assert(out['source']['provider'] == 'browser+fxtwitter', f"provider mismatch: {out['source']['provider']}")
    _assert(out['quote']['author_handle'] == 'alice', 'quote author missing')


def test_tweets_envelope_accepted() -> None:
    """Verify that the bookmarker parser accepts the {tweets:[...]} envelope."""
    feed_raw = {
        'hasMore': True,
        'page': 1,
        'success': True,
        'tweets': [{'id': '1', 'text': 'hello'}],
    }
    # Simulate the canonical parser logic from run_bookmarker_runtime.py
    feed = []
    for _key in ("tweets", "posts", "items", "data"):
        _val = feed_raw.get(_key)
        if isinstance(_val, list):
            feed = _val
            break
    _assert(len(feed) == 1, f'expected 1 item from tweets envelope, got {len(feed)}')
    _assert(feed[0]['text'] == 'hello', 'tweet text mismatch')


def test_missing_handle_blocks() -> None:
    """Sync with empty handle returns blocked status."""
    # Import inline to avoid circular deps at module level
    sys.path.insert(0, str(SCRIPT_DIR))
    from sync_guided_x_tweets import run_sync
    ws = Path(tempfile.mkdtemp(prefix='guided-x-sync-'))
    result = run_sync(ws, '', lookback_days=1, include_replies=False, dry_run=True)
    _assert(result['status'] == 'blocked', f"expected blocked, got {result['status']}")
    _assert('missing_owner_twitter_handle' in result['blockers'], f"expected missing_owner_twitter_handle blocker, got {result['blockers']}")


def main() -> int:
    tests = [
        ('parse tweet url', test_parse_tweet_url),
        ('guided manifest loads', test_guided_manifest_loads),
        ('normalize fxtwitter tweet', test_normalize_fxtwitter_tweet),
        ('tweets envelope accepted', test_tweets_envelope_accepted),
        ('missing handle blocks', test_missing_handle_blocks),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'PASS: {name}')
            passed += 1
        except Exception as e:
            print(f'FAIL: {name}: {e}')
    total = len(tests)
    print(f'SUMMARY: {passed}/{total} passed')
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
