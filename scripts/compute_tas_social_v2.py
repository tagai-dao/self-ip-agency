#!/usr/bin/env python3
"""Compute TAS_social via TagClaw API.

TAS_social v2 — Dual-track formula (2026-03-25):
  Track A (alignment, weight=0.7): @0xNought interactions on clawdbot posts
    like=1, curation=3, comment=5, retweet=3
    align_score = min(5.0, raw_align / 4.0)
  Track B (community, weight=0.3): ALL users' interactions on clawdbot posts in 24h window
    community_score = min(5.0, total_community_interactions / 20.0 * 5.0)
  Final: TAS_social = min(5.0, 0.7 * align_score + 0.3 * community_score)

API (2026-03-29):
  Feed:    GET /tagclaw/feed/me       (auth; returns {posts:[...], page, hasMore})
  Curate:  GET /curation/tweetCurateList?tweetId=X   (Track A — who curated)
  Replies: GET /curation/getReplyOfTweet?tweetId=X   (Track A — who replied)

Usage:
  python3 scripts/compute_tas_social_v2.py [--dry-run]
  python3 scripts/compute_tas_social_v2.py --finalize '<signals_json>' '<posts_json>'
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace"))
MEMORY = ROOT / 'memory'
RUNTIME = ROOT / 'runtime'
CREDENTIALS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
BASE_URL = 'https://bsc-api.tagai.fun/tagclaw'
CURATION_BASE_URL = 'https://bsc-api.tagai.fun/curation'

# @0xNought's Twitter ID — used for Track A alignment detection
OWNER_TWITTER_ID = '1672983517528211457'
OWNER_USERNAME = '0xnought'

# Track A: @0xNought alignment weights
ALIGN_WEIGHTS = {
    'like': 1,
    'curation': 3,
    'comment': 5,
    'retweet': 3,
}
ALIGN_NORMALIZE = 4.0   # raw_align / 4.0 → align_score
ALIGN_CAP = 5.0

# Track B: Community normalization
COMMUNITY_NORMALIZE = 20.0  # 20 total interactions → max community score
COMMUNITY_CAP = 5.0

# Final TAS_social weights
WEIGHT_ALIGN = 0.7
WEIGHT_COMMUNITY = 0.3

DRY_RUN = '--dry-run' in sys.argv


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, str(path))


def load_api_key() -> str:
    data = read_json(CREDENTIALS)
    if not data:
        raise RuntimeError('missing credentials')
    for k in ('api_key', 'apiKey', 'API_KEY'):
        if data.get(k):
            return str(data[k]).strip()
    for v in data.values():
        if isinstance(v, str) and len(v) > 10:
            return v.strip()
    raise RuntimeError('missing api_key in credentials')


def api_get(api_key: str, endpoint: str, params: dict | None = None,
            base: str = BASE_URL) -> dict | None:
    url = f'{base}/{endpoint}'
    if params:
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{url}?{query}'
    try:
        proc = subprocess.run(
            ['curl', '-sS', url,
             '-H', f'Authorization: Bearer {api_key}',
             '-H', 'Accept: application/json',
             '-H', 'User-Agent: Mozilla/5.0'],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except Exception:
        return None


def parse_timestamp(ts: Any) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except Exception:
            return None
    return None


def check_track_a_via_api(api_key: str, post_ids: list) -> dict | None:
    """Check @0xNought interactions on clawdbot posts via API (no browser).

    Uses:
      GET /curation/tweetCurateList?tweetId=X  — like/curation entries
      GET /curation/getReplyOfTweet?tweetId=X  — reply entries

    Returns signals dict {like, curation, comment, retweet} or None if API is inconclusive.
    """
    signals = {k: 0 for k in ALIGN_WEIGHTS}
    any_success = False

    for post_id in post_ids[:5]:
        # -- curators (like / curation) --
        curate_resp = api_get(
            api_key, 'tweetCurateList', {'tweetId': post_id},
            base='https://bsc-api.tagai.fun/curation',
        )
        if curate_resp and isinstance(curate_resp, dict):
            # Accept multiple possible envelope shapes
            curate_list = (
                curate_resp.get('data')
                or curate_resp.get('curateList')
                or curate_resp.get('list')
                or curate_resp.get('curations')
                or (curate_resp if isinstance(curate_resp, list) else [])
            )
            if isinstance(curate_list, list):
                any_success = True
                for entry in curate_list:
                    if not isinstance(entry, dict):
                        continue
                    tid = str(entry.get('twitterId') or entry.get('userId') or '')
                    uname = str(entry.get('twitterUsername') or '').lower()
                    if tid == OWNER_TWITTER_ID or uname == OWNER_USERNAME:
                        signals['like'] += 1

        # -- replies --
        reply_resp = api_get(
            api_key, 'getReplyOfTweet', {'tweetId': post_id, 'pages': 0},
            base='https://bsc-api.tagai.fun/curation',
        )
        if reply_resp and isinstance(reply_resp, dict):
            reply_list = (
                reply_resp.get('tweets')
                or reply_resp.get('data')
                or reply_resp.get('list')
                or reply_resp.get('replies')
                or (reply_resp if isinstance(reply_resp, list) else [])
            )
            if isinstance(reply_list, list):
                any_success = True
                for entry in reply_list:
                    if not isinstance(entry, dict):
                        continue
                    tid = str(entry.get('twitterId') or entry.get('userId') or '')
                    uname = str(entry.get('twitterUsername') or '').lower()
                    if tid == OWNER_TWITTER_ID or uname == OWNER_USERNAME:
                        signals['comment'] += 1

    return signals if any_success else None


def compute_scores(signals: dict, eligible_posts: list) -> dict:
    """Compute dual-track TAS_social scores."""
    # Track A — alignment (@0xNought)
    raw_align = sum(signals.get(k, 0) * ALIGN_WEIGHTS[k] for k in ALIGN_WEIGHTS)
    align_score = min(ALIGN_CAP, raw_align / ALIGN_NORMALIZE) if raw_align > 0 else 0.0

    # Track B — community (all users, summed from eligible_posts)
    total_likes = sum(p.get('likes', 0) or 0 for p in eligible_posts)
    total_retweets = sum(p.get('retweets', 0) or 0 for p in eligible_posts)
    total_replies = sum(p.get('replies', 0) or 0 for p in eligible_posts)
    total_community = total_likes + total_retweets + total_replies
    community_score = min(COMMUNITY_CAP, (total_community / COMMUNITY_NORMALIZE) * COMMUNITY_CAP)

    # Final TAS_social
    tas_social = min(5.0, WEIGHT_ALIGN * align_score + WEIGHT_COMMUNITY * community_score)

    return {
        'tas_social': round(tas_social, 4),
        'align_score': round(align_score, 4),
        'community_score': round(community_score, 4),
        'raw_align': raw_align,
        'community_signals': {
            'total_likes': total_likes,
            'total_retweets': total_retweets,
            'total_replies': total_replies,
            'total_interactions': total_community,
        },
    }


def build_result(eligible: list, signals: dict, scores: dict,
                 window_start: datetime, current: datetime,
                 query_source: str) -> dict:
    tas_social = scores['tas_social']
    align_score = scores['align_score']
    community_score = scores['community_score']
    # P4: detect unchanged value vs previous run — signals flat-line is expected, not broken
    _prev = read_json(MEMORY / 'tas-social-latest.json') or {}
    _prev_val = _prev.get('value')
    _unchanged = _prev_val is not None and abs(float(_prev_val) - tas_social) < 1e-6
    result: dict = {
        'schema': 'tas.metric.v2',
        'metric': 'TAS_social',
        'source_agent': 'main',
        'computed_at': iso(),
        'window': {
            'kind': 'rolling_24h',
            'start': iso(window_start),
            'end': iso(current),
            'days': 1,
            'label': 'rolling 24h social-interaction window',
        },
        'value': tas_social,
        'status': 'ok',
        'summary': (
            f'{len(eligible)} posts in 24h window. '
            f'Align(@0xNought): {signals.get("like", 0)}L/{signals.get("curation", 0)}C/'
            f'{signals.get("comment", 0)}cmt/{signals.get("retweet", 0)}RT → align_score={align_score:.2f}. '
            f'Community: {scores["community_signals"]["total_interactions"]} interactions → '
            f'community_score={community_score:.2f}. '
            f'TAS_social={tas_social:.2f} (0.7×{align_score:.2f}+0.3×{community_score:.2f}).'
        ),
        'inputs': {
            'target_account': '@clawdbot',
            'align_scorer': '@0xNought',
            'query_source': query_source,
            'eligible_posts': eligible[:10],
            'align_signals': signals,
            'align_weights': ALIGN_WEIGHTS,
            'raw_align': scores['raw_align'],
            'align_score': align_score,
            'community_signals': scores['community_signals'],
            'community_score': community_score,
            'normalization': {
                'align': f'raw_align / {ALIGN_NORMALIZE} capped at {ALIGN_CAP}',
                'community': f'total_interactions / {COMMUNITY_NORMALIZE} * {COMMUNITY_CAP} capped at {COMMUNITY_CAP}',
                'formula': f'TAS_social = min(5.0, {WEIGHT_ALIGN}×align + {WEIGHT_COMMUNITY}×community)',
            },
        },
        'formula': f'TAS_social = min(5.0, {WEIGHT_ALIGN}×align_score + {WEIGHT_COMMUNITY}×community_score)',
        'blockers': [],
        'notes': [
            'v2: dual-track TAS_social (alignment + community)',
            'Align track: @0xNought interactions only',
            'Community track: all users interactions in 24h window',
        ],
    }
    # P4: tag unchanged heartbeats so dashboard can distinguish flat-line (correct) from broken
    if _unchanged:
        result['unchanged'] = True
        result['previous_value'] = float(_prev_val)
    return result


def write_result(result: dict) -> None:
    atomic_write_json(MEMORY / 'tas-social-latest.json', result)
    runtime_result = dict(result)
    runtime_result['source_class'] = 'main-runtime-handoff'
    runtime_result['legacy_source_ref'] = 'memory/tas-social-latest.json'
    runtime_result['updated_at'] = iso()
    atomic_write_json(RUNTIME / 'main' / 'tas-social.json', runtime_result)


def main() -> int:
    current = now()
    window_start = current - timedelta(hours=24)

    try:
        api_key = load_api_key()
    except Exception as e:
        print(json.dumps({'status': 'blocked', 'error': str(e)}))
        return 1

    # Fetch authenticated feed (GET /tagclaw/feed/me)
    # Returns {posts:[...], page, hasMore} with participation flags (liked, curated, etc.)
    resp = api_get(api_key, 'feed/me', {'pages': 0, 'limit': 50})
    if not resp:
        print(json.dumps({'status': 'blocked', 'error': 'could not fetch /tagclaw/feed/me'}))
        return 1

    # feed/me returns 'posts' (unlike public /feed which returns 'tweets')
    posts = resp.get('posts', [])

    # Filter to 24h window — clawdbot's own posts only
    eligible = []
    for t in posts:
        tid = t.get('tweetId') or t.get('id')
        if not tid:
            continue
        ts = parse_timestamp(t.get('tweetTime') or t.get('createdAt'))
        if ts and ts < window_start:
            continue
        username = (t.get('twitterUsername') or '').lower()
        if username and username != 'clawdbot':
            continue
        eligible.append({
            'id': str(tid),
            'created_at': iso(ts) if ts else None,
            'content': str(t.get('content') or '')[:100],
            'likes': int(t.get('likeCount') or 0),
            'retweets': int(t.get('retweetCount') or 0),
            'replies': int(t.get('replyCount') or 0),
            'tick': t.get('tick', ''),
        })

    if '--finalize' in sys.argv:
        # Called by agent after manual browser checks
        # Usage: python3 compute_tas_social_v2.py --finalize '<signals_json>' '<posts_json>'
        finalize_idx = sys.argv.index('--finalize')
        signals_json = sys.argv[finalize_idx + 1] if finalize_idx + 1 < len(sys.argv) else '{}'
        posts_json = sys.argv[finalize_idx + 2] if finalize_idx + 2 < len(sys.argv) else '[]'

        try:
            signals = json.loads(signals_json)
        except Exception:
            signals = {k: 0 for k in ALIGN_WEIGHTS}

        try:
            post_details = json.loads(posts_json)
        except Exception:
            post_details = []

        scores = compute_scores(signals, eligible)
        result = build_result(
            eligible, signals, scores, window_start, current,
            query_source='TagClaw API (feed/me) + manual browser signals',
        )
        result['inputs']['post_interaction_details'] = post_details

        if not DRY_RUN:
            write_result(result)

        print(json.dumps({
            'status': 'ok',
            'value': scores['tas_social'],
            'align_score': scores['align_score'],
            'community_score': scores['community_score'],
            'align_signals': signals,
            'community_signals': scores['community_signals'],
            'source': 'finalize',
        }, ensure_ascii=False, indent=2))
        return 0

    # Default: try Track A via API (curation/tweetCurateList + getReplyOfTweet)
    # No browser needed; falls back to phase-1 output if API check is inconclusive.
    api_signals = check_track_a_via_api(api_key, [p['id'] for p in eligible[:5]])

    if api_signals is not None:
        # Full computation via API — no browser required
        scores = compute_scores(api_signals, eligible)
        result = build_result(
            eligible, api_signals, scores, window_start, current,
            query_source='TagClaw API (feed/me + curation/tweetCurateList + getReplyOfTweet)',
        )

        if not DRY_RUN:
            write_result(result)

        print(json.dumps({
            'status': 'ok',
            'value': scores['tas_social'],
            'align_score': scores['align_score'],
            'community_score': scores['community_score'],
            'align_signals': api_signals,
            'community_signals': scores['community_signals'],
            'source': 'api',
        }, ensure_ascii=False, indent=2))
        return 0

    # Fallback: phase-1 output — agent must check browser for Track A
    community_total = sum(p['likes'] + p['retweets'] + p['replies'] for p in eligible)
    community_score_preview = min(COMMUNITY_CAP, (community_total / COMMUNITY_NORMALIZE) * COMMUNITY_CAP)

    output = {
        'phase': 'posts_ready',
        'window': {'start': iso(window_start), 'end': iso(current)},
        'eligible_posts': eligible[:10],
        'total_posts_in_feed': len(posts),
        'total_eligible': len(eligible),
        'community_preview': {
            'total_interactions': community_total,
            'community_score_preview': round(community_score_preview, 4),
            'note': 'Track B computed; Track A API check inconclusive — browser fallback needed',
        },
        'instructions': (
            'For each eligible post (top 5 by likes), open browser to https://tagai.fun/post-detail/{id}, '
            'find @0xNought in curators/likers. Record signals: like/curation/comment/retweet. '
            "Then call: python3 compute_tas_social_v2.py --finalize '{\"like\":N,...}' '[]'"
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
