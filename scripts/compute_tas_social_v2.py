#!/usr/bin/env python3
"""Compute TAS_social via TagClaw API (Bookmarker worker variant).

TAS_social v5 — Community/PoB-first formula with bounded smoothing (2026-05-20):
  Track A (secondary): @0xNought interactions on clawdbot posts
    like=1, curation=3, comment=5, retweet=3
    align_score = min(5.0, raw_align / 4.0)
  Track B (primary): community interactions on clawdbot posts in the rolling 24h window
    community_score = min(5.0, 5.0 * log1p(total_interactions) / log1p(40.0))
  Track C (primary): PoB reward on TagClaw tick
    pob_score = min(5.0, 5.0 * log1p(pob_reward_usd) / log1p(5.0))
  Track D (tertiary): X reco quality prior
    xreco_score = min(5.0, runtime/bookmarker/tas-xreco.json.value)
  Raw:
    raw_tas_social = min(5.0,
      0.45 * community_score +
      0.35 * pob_score +
      0.15 * align_score +
      0.05 * xreco_score
    )
  Final:
    TAS_social = smooth(raw_tas_social + quality_density_bonus)
    where smoothing uses bounded EMA to avoid one-cycle spikes or collapses.

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
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from tas_social_formula import (
    TAS_SOCIAL_VERSION,
    community_score_from_interactions,
    compute_raw_tas_social,
    pob_score_from_reward_usd,
    smooth_tas_social,
    tas_social_formula_string,
    xreco_score_from_value,
)
from agency_paths import BOOKMARKER_WS, MAIN_WS

ROOT = (MAIN_WS)
MEMORY = ROOT / 'memory'
RUNTIME = ROOT / 'runtime'
BOOKMARKER_ROOT = (BOOKMARKER_WS)
BOOKMARKER_SCOPED_CREDS = BOOKMARKER_ROOT / 'runtime' / 'credentials' / 'tagclaw-bookmarker.json'
FALLBACK_CREDS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'
TAGCLAW_POSTS_RAW = BOOKMARKER_ROOT / 'memory' / 'raw' / 'tagclaw-posts'
BASE_URL = 'https://bsc-api.tagai.fun/tagclaw'
TAS_SOCIAL_STATE_PATH = RUNTIME / 'bookmarker' / 'tas-social-smoothing.json'
TAS_XRECO_PATH = RUNTIME / 'bookmarker' / 'tas-xreco.json'


def _native_tas_is_stale(max_age_minutes: float = 90.0) -> bool:
    """True if the canonical native tas-social.json is missing or stale.

    Engines merged 2026-05-28: run_bookmarker_runtime.py (native) is the sole
    authoritative writer of tas-social.json. This (now-dormant) script only
    writes the metric as a stale-fallback safety net, so re-enabling it can
    never reintroduce the dual-writer flip-flop.
    """
    import json as _json
    p = RUNTIME / 'bookmarker' / 'tas-social.json'
    try:
        doc = _json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return True
    ts = doc.get('generated_at') or doc.get('updated_at')
    if not ts:
        return True
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0 > max_age_minutes
    except Exception:
        return True

# @0xNought's Twitter ID — used for Track A alignment detection
OWNER_TWITTER_ID = '1672983517528211457'
OWNER_USERNAME = '0xnought'

# P5: Known high-credit agents whose curation of @clawdbot posts signals community quality
KNOWN_HIGH_CREDIT_AGENTS = ['foxclaw', 'clawdiai', 'alita', 'tagclawcto', 'tutu']

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
COMMUNITY_NORMALIZE = 300.0  # 300 total interactions → max community score
COMMUNITY_CAP = 5.0

# Track B: Reward attribution baselines (P1 — 2026-04-03)
BASELINE_CURATE_USD = 0.01   # initial baseline $0.01/cycle, adjustable
BASELINE_CREATOR_USD = 0.005  # initial baseline $0.005/cycle, adjustable
REWARD_ATTRIBUTION_PATH = RUNTIME / 'bookmarker' / 'reward-attribution.json'

# Final TAS_social weights (legacy fields kept for compatibility comments only)
WEIGHT_ALIGN = 0.7       # deprecated — kept for summary backward compat
WEIGHT_COMMUNITY = 0.3   # deprecated

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


def load_tas_xreco_score() -> tuple[float, dict[str, Any]]:
    doc = read_json(TAS_XRECO_PATH) or {}
    xreco_score = xreco_score_from_value(doc.get('value'))
    return xreco_score, {
        'source': 'runtime/bookmarker/tas-xreco.json',
        'raw_value': doc.get('value'),
        'hits': doc.get('hits'),
        'pushes': doc.get('pushes'),
        'hit_rate': doc.get('hit_rate'),
    }


def _norm_identity(value: Any) -> str:
    return str(value or '').strip().lower()


def resolve_creds_file() -> tuple[Path, str]:
    env = os.environ.get('TAGCLAW_CREDS_PATH')
    if env:
        return Path(env).expanduser(), 'env'
    if BOOKMARKER_SCOPED_CREDS.exists():
        return BOOKMARKER_SCOPED_CREDS, 'scoped'
    return FALLBACK_CREDS, 'global'


def load_creds() -> tuple[dict[str, Any], Path, str]:
    path, source = resolve_creds_file()
    data = read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError('missing credentials')
    return data, path, source


def load_api_key() -> tuple[str, dict[str, Any], Path, str]:
    data, creds_path, creds_source = load_creds()
    for k in ('api_key', 'apiKey', 'API_KEY'):
        if data.get(k):
            return str(data[k]).strip(), data, creds_path, creds_source
    for v in data.values():
        if isinstance(v, str) and len(v) > 10:
            return v.strip(), data, creds_path, creds_source
    raise RuntimeError(f'missing api_key in {creds_path}')


def resolve_identity_context(
    api_key: str | None = None,
    creds: dict[str, Any] | None = None,
    creds_path: Path | None = None,
    creds_source: str | None = None,
) -> dict[str, Any]:
    if creds is None or creds_path is None or creds_source is None:
        creds, creds_path, creds_source = load_creds()

    expected_username = _norm_identity(creds.get('expected_username'))
    expected_agent_id = str(creds.get('expected_agent_id') or '').strip()
    expected_agent_name = str(creds.get('expected_agent_name') or '').strip()

    actual_username = ''
    actual_agent_id = ''
    actual_agent_name = ''
    if api_key:
        resp = api_get(api_key, 'me')
        if isinstance(resp, dict):
            agent = resp.get('agent') or (resp.get('data') or {}).get('agent') or {}
            if isinstance(agent, dict):
                actual_username = _norm_identity(agent.get('username'))
                actual_agent_id = str(agent.get('agentId') or agent.get('id') or '').strip()
                actual_agent_name = str(agent.get('name') or '').strip()

    tracked_username = expected_username or actual_username or 'clawdbot'
    self_usernames = sorted({v for v in [tracked_username, expected_username, actual_username] if v})
    self_agent_ids = sorted({_norm_identity(v) for v in [expected_agent_id, actual_agent_id] if _norm_identity(v)})
    return {
        'tracked_username': tracked_username,
        'self_usernames': self_usernames,
        'self_agent_ids': self_agent_ids,
        'actor_identity': {
            'username': actual_username or None,
            'agent_id': actual_agent_id or None,
            'agent_name': actual_agent_name or None,
        },
        'expected_identity': {
            'username': expected_username or None,
            'agent_id': expected_agent_id or None,
            'agent_name': expected_agent_name or None,
        },
        'credentials_path': str(creds_path),
        'credentials_source': creds_source,
    }


def is_tracked_username(username: Any, identity_ctx: dict[str, Any]) -> bool:
    token = _norm_identity(username)
    if not token:
        return False
    allowed = {_norm_identity(v) for v in (identity_ctx.get('self_usernames') or []) if _norm_identity(v)}
    tracked = _norm_identity(identity_ctx.get('tracked_username'))
    if tracked:
        allowed.add(tracked)
    return token in allowed


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


def _patch_owner_reaction_frontmatter(
    text: str, liked: bool, commented: bool, retweeted: bool
) -> str:
    """Patch owner_reaction fields in YAML frontmatter, leave body untouched."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return text
    end_idx = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            end_idx = i
            break
    if end_idx == -1:
        return text

    reactions = []
    if liked:
        reactions.append('like')
    if commented:
        reactions.append('comment')
    if retweeted:
        reactions.append('retweet')
    owner_reaction = json.dumps(reactions[0]) if reactions else 'null'

    new_lines = []
    for i, line in enumerate(lines):
        if i == 0 or i > end_idx:
            new_lines.append(line)
            continue
        key = line.split(':', 1)[0].strip() if ':' in line else ''
        if key == 'owner_reaction':
            new_lines.append(f'owner_reaction: {owner_reaction}')
        elif key == 'liked_by_owner':
            new_lines.append(f'liked_by_owner: {str(liked).lower()}')
        elif key == 'commented_by_owner':
            new_lines.append(f'commented_by_owner: {str(commented).lower()}')
        elif key == 'retweeted_by_owner':
            new_lines.append(f'retweeted_by_owner: {str(retweeted).lower()}')
        else:
            new_lines.append(line)
    return '\n'.join(new_lines)


def update_post_owner_reaction(
    post_id: str, liked: bool, commented: bool, retweeted: bool
) -> None:
    """Update owner reaction fields in raw/tagclaw-posts/*.md frontmatter.

    Only modifies the YAML head, never touches the post body.
    Uses atomic write (tempfile + os.replace). Fails silently.
    """
    if not TAGCLAW_POSTS_RAW.exists():
        return
    target: Path | None = None
    for f in TAGCLAW_POSTS_RAW.glob('*.md'):
        try:
            text = f.read_text(encoding='utf-8')
        except Exception:
            continue
        lines = text.splitlines()
        if not lines or lines[0].strip() != '---':
            continue
        for line in lines[1:]:
            if line.strip() == '---':
                break
            if line.startswith('post_id:') and post_id in line:
                target = f
                break
        if target:
            break
    if target is None:
        return
    try:
        text = target.read_text(encoding='utf-8')
        patched = _patch_owner_reaction_frontmatter(text, liked, commented, retweeted)
        if patched == text:
            return
        with tempfile.NamedTemporaryFile('w', dir=str(target.parent), suffix='.tmp',
                                          delete=False, encoding='utf-8') as f:
            f.write(patched)
            tmp_name = f.name
        os.replace(tmp_name, str(target))
    except Exception:
        pass  # graceful degrade


def check_track_a_via_api(api_key: str, post_ids: list) -> dict | None:
    """Check @0xNought interactions on clawdbot posts via API (no browser).

    Uses:
      GET /curation/tweetCurateList?tweetId=X  — like/curation entries
      GET /curation/getReplyOfTweet?tweetId=X  — reply entries

    Returns signals dict {like, curation, comment, retweet} or None if inconclusive.
    """
    signals = {k: 0 for k in ALIGN_WEIGHTS}
    any_success = False
    # Per-post owner interaction tracking (for raw file updates)
    per_post_liked: dict[str, bool] = {}
    per_post_commented: dict[str, bool] = {}

    for post_id in post_ids[:5]:
        post_liked = False
        post_commented = False

        # -- curators (like / curation) --
        curate_resp = api_get(
            api_key, 'tweetCurateList', {'tweetId': post_id},
            base='https://bsc-api.tagai.fun/curation',
        )
        if curate_resp and isinstance(curate_resp, dict):
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
                        post_liked = True

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
                        post_commented = True

        per_post_liked[post_id] = post_liked
        per_post_commented[post_id] = post_commented

    # Update raw tagclaw-posts files with owner reaction signals detected
    for post_id in post_ids[:5]:
        liked = per_post_liked.get(post_id, False)
        commented = per_post_commented.get(post_id, False)
        if liked or commented:
            update_post_owner_reaction(
                post_id,
                liked=liked,
                commented=commented,
                retweeted=False,  # retweet detection not available via current API path
            )

    return signals if any_success else None


def backfill_curators_attracted(api_key: str) -> None:
    """Check curators of @clawdbot's recent posts and backfill post-engagement-log.json."""
    # Step 1: read post-engagement-log.json
    log_path = RUNTIME / 'bookmarker' / 'post-engagement-log.json'
    if not log_path.exists():
        return
    log_doc = read_json(log_path) or {}
    entries = log_doc.get('entries') or []
    if not entries:
        return

    # Step 2: read raw/tagclaw-posts to get post_id → tweet_id mapping
    raw_posts_dir = BOOKMARKER_ROOT / 'memory' / 'raw' / 'tagclaw-posts'
    posted_ids: dict[str, str] = {}  # post_id -> tweetId
    if raw_posts_dir.exists():
        for f in sorted(raw_posts_dir.glob('*.md'), reverse=True)[:10]:
            try:
                text = f.read_text(encoding='utf-8')
                m = re.search(r'^post_id:\s*"?([^"\n]+)"?', text, re.MULTILINE)
                if m:
                    pid = m.group(1).strip()
                    tid_match = re.search(r'^tweet_id:\s*"?([^"\n]+)"?', text, re.MULTILINE)
                    tid = tid_match.group(1).strip() if tid_match else pid.replace('tagclaw-', '')
                    posted_ids[pid] = tid
            except Exception:
                continue

    if not posted_ids:
        return

    # Step 3: for each entry where curators_attracted is empty, call tweetCurateList
    updated = False
    for entry in entries:
        if entry.get('curators_attracted'):  # already filled
            continue
        our_post_id = entry.get('our_post_id', '')
        tweet_id = posted_ids.get(our_post_id) or our_post_id
        if not tweet_id:
            continue

        curate_resp = api_get(
            api_key, 'tweetCurateList', {'tweetId': tweet_id},
            base='https://bsc-api.tagai.fun/curation',
        )
        if not curate_resp:
            continue
        curate_list = (
            curate_resp.get('data')
            or curate_resp.get('curateList')
            or curate_resp.get('list')
            or curate_resp.get('curations')
            or (curate_resp if isinstance(curate_resp, list) else [])
        )
        if not isinstance(curate_list, list):
            continue

        attracted = []
        for curator in curate_list:
            if not isinstance(curator, dict):
                continue
            uname = str(curator.get('twitterUsername') or '').lower()
            # Strip platform suffixes
            uname = re.sub(r'\.(tagai|tagclaw)$', '', uname)
            if any(agent in uname for agent in KNOWN_HIGH_CREDIT_AGENTS):
                attracted.append({
                    'agent': uname,
                    'vp': curator.get('vp') or curator.get('votingPower'),
                    'curated_at': curator.get('createdAt') or curator.get('created_at'),
                })

        if attracted:
            entry['curators_attracted'] = attracted
            updated = True

    if updated:
        log_doc['entries'] = entries
        log_doc['updated_at'] = iso()
        atomic_write_json(log_path, log_doc)


def compute_scores(signals: dict, eligible_posts: list, previous_value: float | None = None) -> dict:
    """Compute TAS_social with community/PoB-first weighting and bounded smoothing."""
    # Track A — alignment (@0xNought)
    raw_align = sum(signals.get(k, 0) * ALIGN_WEIGHTS[k] for k in ALIGN_WEIGHTS)
    align_score = min(ALIGN_CAP, raw_align / ALIGN_NORMALIZE) if raw_align > 0 else 0.0

    # Track B — community interactions on our own posts
    total_likes = sum(p.get('likes', 0) or 0 for p in eligible_posts)
    total_retweets = sum(p.get('retweets', 0) or 0 for p in eligible_posts)
    total_replies = sum(p.get('replies', 0) or 0 for p in eligible_posts)
    total_community = total_likes + total_retweets + total_replies
    community_score = community_score_from_interactions(total_community)

    # Reward attribution remains observable, but it no longer directly dominates Track B.
    curate_reward_usd = 0.0
    claimable_detail: list = []
    try:
        reward_status = read_json(RUNTIME / 'trader' / 'reward-status.json') or {}
        for item in (reward_status.get('claimable') or []):
            if isinstance(item, dict) and item.get('tick') == 'TagClaw':
                curate_reward_usd = float(item.get('reward_value_usd') or 0.0)
                claimable_detail.append({
                    'tick': item.get('tick'),
                    'amount': item.get('claimable_amount'),
                    'usd': curate_reward_usd,
                    'status': item.get('status'),
                })
                break
    except Exception:
        pass  # graceful fallback: curate_reward_usd stays 0.0

    creator_reward_usd = 0.0  # P2: no chain API yet

    curate_reward_score = min(1.0, curate_reward_usd / BASELINE_CURATE_USD)
    creator_reward_score = min(1.0, creator_reward_usd / BASELINE_CREATOR_USD)

    # Write reward-attribution.json (atomic write, skip in dry-run)
    if not DRY_RUN:
        attribution = {
            'version': 'v1',
            'updated_at': iso(),
            'window_hours': 24,
            'curate_reward_usd': round(curate_reward_usd, 6),
            'creator_reward_usd': creator_reward_usd,
            'curate_reward_score': round(curate_reward_score, 4),
            'creator_reward_score': creator_reward_score,
            'baseline_curate_usd': BASELINE_CURATE_USD,
            'baseline_creator_usd': BASELINE_CREATOR_USD,
            'source': 'runtime/trader/reward-status.json',
            'claimable_detail': claimable_detail,
        }
        atomic_write_json(REWARD_ATTRIBUTION_PATH, attribution)

    # quality_density: optional bonus fields (bookmark_bonus + engagement_rate_bonus)
    # These are additive-only: they only increase TAS_social, never decrease it.
    # If data is unavailable, quality_density_bonus defaults to 0.0 (safe fallback).

    # bookmark_bonus: bookmark_weight=4 (between comment=5 and retweet=3)
    # Sum bookmark_count across eligible_posts if the field exists.
    total_bookmarks: int | None = None
    bookmark_bonus: float = 0.0
    if eligible_posts and any('bookmark_count' in p for p in eligible_posts):
        total_bookmarks = sum(int(p.get('bookmark_count') or 0) for p in eligible_posts)
        # Normalize: 10 bookmarks → +0.25 bonus (4x weight relative to like=1)
        bookmark_bonus = min(0.5, (total_bookmarks * 4) / (COMMUNITY_NORMALIZE * 4) * 0.5)

    # engagement_rate_bonus: 20% bonus if engagement_rate > 1% across eligible_posts
    total_impressions: int | None = None
    engagement_rate: float | None = None
    quality_bonus: float = 1.0
    if eligible_posts and any('impressions' in p for p in eligible_posts):
        total_impressions = sum(int(p.get('impressions') or 0) for p in eligible_posts)
        if total_impressions > 0:
            total_interactions_all = total_community + (total_bookmarks or 0)
            engagement_rate = total_interactions_all / total_impressions
            quality_bonus = 1.2 if engagement_rate > 0.01 else 1.0

    quality_density_bonus = round(bookmark_bonus * quality_bonus, 4)

    # P0 2026-04-09: PoB reward score — TagClaw-tick curation reward only (post-specific)
    # NOT the broad trader claimable aggregate which includes unrelated ticks (BUIDL, TTAI, etc.)
    pob_reward_score = 0.0
    pob_claimable_usd = 0.0
    pob_source = 'none'
    try:
        reward_status_pob = read_json(RUNTIME / 'trader' / 'reward-status.json') or {}
        for _item in (reward_status_pob.get('claimable') or []):
            if isinstance(_item, dict) and _item.get('tick') == 'TagClaw':
                pob_claimable_usd = float(_item.get('reward_value_usd') or 0.0)
                pob_source = 'reward-status.json TagClaw tick'
                break
    except Exception:
        pass  # graceful fallback: pob_reward_score stays 0.0
    pob_score = pob_score_from_reward_usd(pob_claimable_usd)
    xreco_score, xreco_detail = load_tas_xreco_score()

    raw_tas_social, weighted_components = compute_raw_tas_social(
        community_score=community_score,
        pob_score=pob_score,
        align_score=align_score,
        xreco_score=xreco_score,
    )
    raw_plus_quality = min(5.0, raw_tas_social + quality_density_bonus)
    tas_social, smoothing_detail = smooth_tas_social(raw_plus_quality, previous_value)

    return {
        'tas_social': round(tas_social, 4),
        'tas_social_raw': round(raw_plus_quality, 4),
        'align_score': round(align_score, 4),
        'community_score': round(community_score, 4),
        'pob_reward_score': round(pob_score, 4),
        'pob_claimable_usd': round(pob_claimable_usd, 6),
        'xreco_score': round(xreco_score, 4),
        'raw_align': raw_align,
        'community_signals': {
            'total_likes': total_likes,
            'total_retweets': total_retweets,
            'total_replies': total_replies,
            'total_interactions': total_community,
        },
        'curate_reward_score': round(curate_reward_score, 4),
        'creator_reward_score': creator_reward_score,
        'curate_reward_usd': round(curate_reward_usd, 6),
        'creator_reward_usd': creator_reward_usd,
        'pob_source': pob_source,
        'xreco_detail': xreco_detail,
        'weighted_components': {k: round(v, 4) for k, v in weighted_components.items()},
        'smoothing_detail': smoothing_detail,
        # quality_density fields (optional — only populated when data available)
        'tas_social_detail': {
            'base_score': round(raw_tas_social, 4),
            'quality_density_bonus': quality_density_bonus,
            'bookmark_count': total_bookmarks,
            'engagement_rate': round(engagement_rate, 6) if engagement_rate is not None else None,
        },
        'formula': tas_social_formula_string(),
    }


def build_bookmarker_result(eligible: list, signals: dict, scores: dict,
                             window_start: datetime, current: datetime,
                             query_source: str, identity_ctx: dict[str, Any]) -> dict:
    tas_social = scores['tas_social']
    tas_social_raw = scores.get('tas_social_raw', tas_social)
    align_score = scores['align_score']
    community_score = scores['community_score']
    pob_reward_score = scores.get('pob_reward_score', 0.0)
    xreco_score = scores.get('xreco_score', 0.0)
    tracked_username = str(identity_ctx.get('tracked_username') or 'clawdbot').strip().lower()
    return {
        'schema': 'tas.metric.v2',
        'version': TAS_SOCIAL_VERSION,
        'metric': 'TAS_social',
        'source_agent': 'bookmarker',
        'computed_at': iso(),
        'tracked_username': tracked_username,
        'actor_identity': identity_ctx.get('actor_identity'),
        'expected_identity': identity_ctx.get('expected_identity'),
        'self_usernames': identity_ctx.get('self_usernames') or [],
        'self_agent_ids': identity_ctx.get('self_agent_ids') or [],
        'credentials_path': identity_ctx.get('credentials_path'),
        'credentials_source': identity_ctx.get('credentials_source'),
        'window': {
            'kind': 'rolling_24h',
            'start': iso(window_start),
            'end': iso(current),
            'days': 1,
            'label': 'rolling 24h social-interaction window',
        },
        'value': tas_social,
        'raw_value': tas_social_raw,
        'align_score': align_score,
        'community_score': community_score,
        'pob_reward_score': pob_reward_score,
        'xreco_score': xreco_score,
        'pob_claimable_usd': scores.get('pob_claimable_usd', 0.0),
        'community_signals': scores['community_signals'],
        'status': 'ok',
        'summary': (
            f'Align(@0xNought): {signals.get("like", 0)}L/{signals.get("curation", 0)}C/'
            f'{signals.get("comment", 0)}cmt/{signals.get("retweet", 0)}RT → align_score={align_score:.2f}. '
            f'Community: {scores["community_signals"]["total_interactions"]} interactions → '
            f'community_score={community_score:.2f}. '
            f'PoB: pob_score={pob_reward_score:.2f}. '
            f'X reco: xreco_score={xreco_score:.2f}. '
            f'TAS_social={tas_social:.2f} (raw={tas_social_raw:.2f}, smoothed).'
        ),
        'inputs': {
            'target_account': f'@{tracked_username}',
            'align_scorer': '@0xNought',
            'query_source': query_source,
            'eligible_posts_count': len(eligible),
            'align_signals': signals,
            'align_weights': ALIGN_WEIGHTS,
            'raw_align': scores['raw_align'],
            'normalization': {
                'align': f'raw_align / {ALIGN_NORMALIZE} capped at {ALIGN_CAP}',
                'community': '5.0 × log1p(total_interactions) / log1p(40) capped at 5.0',
                'pob': '5.0 × log1p(pob_reward_usd) / log1p(5.0) capped at 5.0',
                'xreco': 'direct tas-xreco value capped at 5.0',
                'formula': tas_social_formula_string(),
            },
        },
        'weighted_components': scores.get('weighted_components'),
        'smoothing_detail': scores.get('smoothing_detail'),
        'formula': tas_social_formula_string(),
        'source_class': 'main-runtime-handoff',
        'blockers': [],
        'notes': [
            'v5: community + PoB first, align secondary, X reco tertiary',
            'Bookmarker-owned computation (P3)',
        ],
    }


def write_align_events(signals: dict) -> None:
    """Write align-events.json when @0xNought interaction detected.

    Called after computing alignment signals. Writes to runtime/bookmarker/align-events.json.
    """
    align_count = sum(signals.get(k, 0) for k in ('like', 'curation', 'comment', 'retweet'))
    align_signal_raw = sum(signals.get(k, 0) * ALIGN_WEIGHTS.get(k, 0) for k in ALIGN_WEIGHTS)
    has_active = align_count >= 1
    detected_at = iso()
    expires_at = iso(now() + timedelta(hours=6)) if has_active else None

    event = {
        'version': 'v1',
        'detected_at': detected_at,
        'align_count_24h': align_count,
        'align_signal_raw': align_signal_raw,
        'has_active_event': has_active,
        'event_expires_at': expires_at,
    }
    atomic_write_json(RUNTIME / 'bookmarker' / 'align-events.json', event)


def main() -> int:
    current = now()
    window_start = current - timedelta(hours=24)
    previous_tas_doc = read_json(RUNTIME / 'bookmarker' / 'tas-social.json') or {}
    smoothing_state = read_json(TAS_SOCIAL_STATE_PATH) or {}
    previous_value = smoothing_state.get('smoothed_value')
    if previous_value is None:
        previous_value = previous_tas_doc.get('value')

    # --finalize mode: skip API call — use pre-supplied signals and posts
    # Usage: python3 compute_tas_social_v2.py [--dry-run] --finalize '<signals_json>' '<posts_json>'
    if '--finalize' in sys.argv:
        finalize_idx = sys.argv.index('--finalize')
        signals_json = sys.argv[finalize_idx + 1] if finalize_idx + 1 < len(sys.argv) else '{}'
        posts_json = sys.argv[finalize_idx + 2] if finalize_idx + 2 < len(sys.argv) else '[]'

        try:
            signals = json.loads(signals_json)
        except Exception:
            signals = {k: 0 for k in ALIGN_WEIGHTS}

        try:
            raw_posts = json.loads(posts_json)
            posts_list = raw_posts if isinstance(raw_posts, list) else (raw_posts.get('posts') or [])
        except Exception:
            posts_list = []

        scores = compute_scores(signals, posts_list, previous_value=previous_value)
        identity_ctx = resolve_identity_context()
        tracked_username = str(identity_ctx.get('tracked_username') or 'clawdbot').strip().lower()
        posts_list = [
            p for p in posts_list
            if not p.get('username') or is_tracked_username(p.get('username'), identity_ctx)
        ]
        result = build_bookmarker_result(
            posts_list, signals, scores, window_start, current,
            query_source='bookmarker-collected signals', identity_ctx=identity_ctx,
        )
        result['community_source'] = f'{tracked_username}-posts' if posts_list else result.get('community_source')
        result['inputs']['posts_provided'] = len(posts_list)

        if not DRY_RUN and _native_tas_is_stale():
            result['updated_at'] = iso()
            atomic_write_json(RUNTIME / 'bookmarker' / 'tas-social.json', result)
            atomic_write_json(TAS_SOCIAL_STATE_PATH, {
                'version': TAS_SOCIAL_VERSION,
                'updated_at': result['updated_at'],
                'raw_value': scores.get('tas_social_raw'),
                'smoothed_value': scores.get('tas_social'),
                'detail': scores.get('smoothing_detail'),
            })
            write_align_events(signals)

        # Backfill curators_attracted in post-engagement-log (P5)
        try:
            api_key_fin, _, _, _ = load_api_key()
            backfill_curators_attracted(api_key_fin)
        except Exception:
            pass  # graceful degrade

        print(json.dumps({
            'status': 'ok',
            'value': scores['tas_social'],
            'raw_value': scores.get('tas_social_raw'),
            'align_score': scores['align_score'],
            'community_score': scores['community_score'],
            'pob_reward_score': scores.get('pob_reward_score', 0.0),
            'pob_claimable_usd': scores.get('pob_claimable_usd', 0.0),
            'xreco_score': scores.get('xreco_score', 0.0),
            'community_signals': scores['community_signals'],
            'align_signals': signals,
            'raw_align': scores['raw_align'],
            'curate_reward_score': scores['curate_reward_score'],
            'creator_reward_score': scores['creator_reward_score'],
            'curate_reward_usd': scores['curate_reward_usd'],
            'creator_reward_usd': scores['creator_reward_usd'],
            'source': 'finalize',
        }, ensure_ascii=False, indent=2))
        return 0

    # Default: fetch authenticated feed (GET /tagclaw/feed/me)
    # Returns {posts:[...]} with participation flags — no need for public /tagclaw/feed
    try:
        api_key, creds, creds_path, creds_source = load_api_key()
        identity_ctx = resolve_identity_context(api_key, creds, creds_path, creds_source)
    except Exception as e:
        print(json.dumps({'status': 'blocked', 'error': str(e)}))
        return 1

    resp = api_get(api_key, 'feed/me', {'pages': 0, 'limit': 50})
    if not resp:
        # Fallback to public feed
        resp = api_get(api_key, 'feed')
    if not resp:
        print(json.dumps({'status': 'blocked', 'error': 'could not fetch feed'}))
        return 1

    # feed/me → 'posts'; public /feed → 'tweets'
    posts = resp.get('posts') or resp.get('tweets', [])

    eligible = []
    for t in posts:
        tid = t.get('tweetId') or t.get('id')
        if not tid:
            continue
        ts = parse_timestamp(t.get('tweetTime') or t.get('createdAt'))
        if ts and ts < window_start:
            continue
        username = (t.get('twitterUsername') or '').lower()
        if username and not is_tracked_username(username, identity_ctx):
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

    # Try Track A via API (curation endpoints)
    api_signals = check_track_a_via_api(api_key, [p['id'] for p in eligible[:5]])

    if api_signals is not None:
        scores = compute_scores(api_signals, eligible, previous_value=previous_value)
        result = build_bookmarker_result(
            eligible, api_signals, scores, window_start, current,
            query_source='TagClaw API (feed/me + curation/tweetCurateList + getReplyOfTweet)',
            identity_ctx=identity_ctx,
        )
        result['community_source'] = f"{identity_ctx.get('tracked_username') or 'clawdbot'}-posts" if eligible else result.get('community_source')

        if not DRY_RUN and _native_tas_is_stale():
            result['updated_at'] = iso()
            atomic_write_json(RUNTIME / 'bookmarker' / 'tas-social.json', result)
            atomic_write_json(TAS_SOCIAL_STATE_PATH, {
                'version': TAS_SOCIAL_VERSION,
                'updated_at': result['updated_at'],
                'raw_value': scores.get('tas_social_raw'),
                'smoothed_value': scores.get('tas_social'),
                'detail': scores.get('smoothing_detail'),
            })
            write_align_events(api_signals)

        # Backfill curators_attracted in post-engagement-log (P5)
        result = build_bookmarker_result(
            eligible, api_signals, scores, window_start, current,
            query_source='TagClaw API (feed/me + curation/tweetCurateList + getReplyOfTweet)',
            identity_ctx=identity_ctx,
        )
        result['community_source'] = f"{identity_ctx.get('tracked_username') or 'clawdbot'}-posts" if eligible else result.get('community_source')

        print(json.dumps({
            'status': 'ok',
            'value': scores['tas_social'],
            'raw_value': scores.get('tas_social_raw'),
            'align_score': scores['align_score'],
            'community_score': scores['community_score'],
            'pob_reward_score': scores.get('pob_reward_score', 0.0),
            'pob_claimable_usd': scores.get('pob_claimable_usd', 0.0),
            'xreco_score': scores.get('xreco_score', 0.0),
            'community_signals': scores['community_signals'],
            'align_signals': api_signals,
            'raw_align': scores['raw_align'],
            'curate_reward_score': scores['curate_reward_score'],
            'creator_reward_score': scores['creator_reward_score'],
            'curate_reward_usd': scores['curate_reward_usd'],
            'creator_reward_usd': scores['creator_reward_usd'],
            'source': 'api',
        }, ensure_ascii=False, indent=2))
        return 0

    # Fallback phase-1: return posts for agent browser check
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
            'For each eligible post (top 5 by likes), check @0xNought in curators/likers. '
            "Then call: python3 compute_tas_social_v2.py --finalize '{\"like\":N,...}' '<eligible_posts_json>'"
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
