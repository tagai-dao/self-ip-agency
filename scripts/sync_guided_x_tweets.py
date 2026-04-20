#!/usr/bin/env python3
"""sync_guided_x_tweets.py — bootstrap owner X tweets into raw/x-tweets.

Canonical path:
- prefer a browser-guided URL manifest (future OpenClaw browser/chirp handoff)
- fall back to zero-credential public RSS discovery for bootstrap usability
- fetch each discovered tweet via FxTwitter-style structured API
- write immutable raw artifacts under raw/x-tweets/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR / 'lib'))

from x_fetch_utils import (  # noqa: E402
    FetchError,
    discover_tweet_urls,
    fetch_tweet_via_fxtwitter,
    normalize_fxtwitter_tweet,
    parse_tweet_url,
)

RAW_DIR_RELPATH = Path('raw/x-tweets')
IDENTITY_RELPATH = Path('config/agency-identity.json')


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, suffix='.tmp', delete=False, encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
        tmp = f.name
    os.replace(tmp, path)


def load_identity_handle(workspace: Path) -> str | None:
    identity_path = workspace / IDENTITY_RELPATH
    if not identity_path.exists():
        return None
    try:
        data = json.loads(identity_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    owner = data.get('owner') or {}
    handle = owner.get('twitter_handle') or owner.get('twitter_id')
    return str(handle).strip() if handle else None


def ensure_dirs(workspace: Path) -> Path:
    raw_dir = workspace / RAW_DIR_RELPATH
    (raw_dir / 'tweets').mkdir(parents=True, exist_ok=True)
    (raw_dir / 'sync-runs').mkdir(parents=True, exist_ok=True)
    return raw_dir


def run_sync(workspace: Path, handle: str, lookback_days: int, include_replies: bool, dry_run: bool) -> dict[str, Any]:
    raw_dir = ensure_dirs(workspace)
    started_at = datetime.now(timezone.utc)
    discovered, discovery_meta = discover_tweet_urls(workspace, handle, lookback_days=lookback_days, include_replies=include_replies)
    warnings = list(discovery_meta.get('warnings') or [])

    result: dict[str, Any] = {
        'schema': 'guided-x-sync-run.v1',
        'status': 'ok',
        'handle': handle,
        'lookback_days': int(lookback_days),
        'include_replies': bool(include_replies),
        'started_at': started_at.isoformat(timespec='seconds'),
        'completed_at': None,
        'discovery_method': discovery_meta.get('provider') or 'unknown',
        'guided_manifest_path': discovery_meta.get('guided_manifest_path') or '',
        'tweet_urls_found': len(discovered),
        'items_written': 0,
        'items_skipped_existing': 0,
        'items_failed_fetch': 0,
        'raw_dir': str(raw_dir.relative_to(workspace)),
        'warnings': warnings,
        'blockers': [],
        'files': [],
    }

    if not handle:
        result['status'] = 'blocked'
        result['blockers'].append('missing_owner_twitter_handle')
    elif not discovered:
        result['status'] = 'deferred'
        result['blockers'].append('no tweet URLs discovered; complete guided browser step or provide guided manifest')

    if result['status'] in {'blocked', 'deferred'}:
        result['completed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return result

    for item in discovered:
        _, tweet_id = parse_tweet_url(item.url)
        target = raw_dir / 'tweets' / f'{tweet_id}.json'
        if target.exists():
            result['items_skipped_existing'] += 1
            result['files'].append({'tweet_id': tweet_id, 'path': str(target.relative_to(workspace)), 'status': 'skipped_existing'})
            continue
        try:
            tweet = fetch_tweet_via_fxtwitter(item.url)
            normalized = normalize_fxtwitter_tweet(
                item.url,
                tweet,
                discovery_source=result['discovery_method'],
                lookback_days=lookback_days,
                reply_hint=item.is_reply_hint,
            )
            if not dry_run:
                atomic_write_json(target, normalized)
            result['items_written'] += 1
            result['files'].append({'tweet_id': tweet_id, 'path': str(target.relative_to(workspace)), 'status': 'written'})
        except FetchError as e:
            result['items_failed_fetch'] += 1
            result['warnings'].append(str(e))
            result['files'].append({'tweet_id': tweet_id, 'url': item.url, 'status': 'fetch_failed', 'error': str(e)})

    if result['items_written'] == 0 and result['items_skipped_existing'] == 0:
        result['status'] = 'blocked' if result['items_failed_fetch'] > 0 else 'deferred'
    elif result['items_failed_fetch'] > 0:
        result['status'] = 'partial'
    else:
        result['status'] = 'ok'

    result['completed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')

    if not dry_run:
        meta = {
            'schema': 'raw-meta.v3',
            'source_url': f'https://x.com/{handle}',
            'description': 'Guided X bootstrap sync for owner tweets/replies',
            'fetched_at': result['completed_at'],
            'status': result['status'],
            'pages_fetched': result['tweet_urls_found'],
            'discovery_method': result['discovery_method'],
            'guided_manifest_path': result['guided_manifest_path'],
            'warnings': result['warnings'],
        }
        manifest = {
            'schema': 'raw-manifest.v2',
            'source_family': f'https://x.com/{handle}',
            'description': 'Owner X tweets bootstrap manifest',
            'fetched_at': result['completed_at'],
            'pages_fetched': result['items_written'],
            'pages_total': result['tweet_urls_found'],
            'status': result['status'],
            'files': result['files'],
        }
        run_name = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')
        atomic_write_json(raw_dir / '_meta.json', meta)
        atomic_write_json(raw_dir / '_manifest.json', manifest)
        atomic_write_json(raw_dir / 'sync-runs' / f'{run_name}.json', result)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Bootstrap guided X tweets into raw/x-tweets')
    parser.add_argument('--workspace', default=str(WORKSPACE), help='Workspace / repo root path')
    parser.add_argument('--handle', default='', help='Optional X handle override')
    parser.add_argument('--lookback-days', type=int, default=3)
    parser.add_argument('--include-replies', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--json', action='store_true', help='Print JSON summary')
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    handle = args.handle.strip() or (load_identity_handle(workspace) or '')
    result = run_sync(workspace, handle, args.lookback_days, args.include_replies, args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"guided-x-sync: {result['status']} | handle={result['handle'] or '?'} | found={result['tweet_urls_found']} | written={result['items_written']} | skipped={result['items_skipped_existing']} | failed={result['items_failed_fetch']}")
        if result['blockers']:
            print('blockers:')
            for b in result['blockers']:
                print(f'  - {b}')
        if result['warnings']:
            print('warnings:')
            for w in result['warnings'][:10]:
                print(f'  - {w}')

    return 0 if result['status'] in {'ok', 'partial', 'deferred'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
