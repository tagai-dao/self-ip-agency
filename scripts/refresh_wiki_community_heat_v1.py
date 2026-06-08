#!/usr/bin/env python3
"""Community Heat Model v3.

Two-dimensional heat scoring with temporal smoothing:
  1. Social heat — recent TagClaw posts + weighted engagement (24h burst + 7d sustained)
  2. Trade heat  — onchain trade count + volume (24h burst + 7d sustained)

Composite = 55% social + 45% trade (configurable).

Key improvements over v2:
  - Log-scale normalization avoids "leader always 1.0" problem
  - 7d smoothed window adds memory / reduces volatility
  - Momentum signal: 24h rate vs 7d average rate
  - Per-community data coverage flags
  - All trade snapshot files are unioned (deduped by txHash) for deeper history
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS

ROOT = (MAIN_WS)
WIKI = ROOT / 'wiki' / 'tagclaw-platform'
HEAT_PATH = ROOT / 'runtime' / 'shared' / 'community-heat.json'
HEAT_DAILY_DIR = ROOT / 'runtime' / 'shared' / 'community-heat-daily'
ONCHAIN_RAW = ROOT / 'raw' / 'onchain-token-transation'
TAGCLAW_CREDS = Path.home() / '.config' / 'tagclaw' / 'credentials.json'

# Windows
WINDOW_24H = 24
WINDOW_7D = 24 * 7

# Composite weights
SOCIAL_WEIGHT = 0.55
TRADE_WEIGHT = 0.45

# Within trade sub-score
TRADE_COUNT_W = 0.6
TRADE_VOLUME_W = 0.4

# Temporal blend: how much weight on 24h burst vs 7d sustained
BURST_WEIGHT = 0.7
SUSTAINED_WEIGHT = 0.3

try:
    from wiki_registry import get_tracked_ticks as _get_tracked_ticks
    TRACKED_TICKS = _get_tracked_ticks()
except Exception:
    TRACKED_TICKS = ['TagClaw', 'BUIDL', 'TTAI', 'CLAW', 'AGENT', 'NOUGHT']

try:
    from runtime_utils_v2 import append_wiki_event, write_provenance_sidecar
except Exception:
    def append_wiki_event(*a: Any, **kw: Any) -> None:  # type: ignore[misc]
        pass
    def write_provenance_sidecar(*a: Any, **kw: Any):  # type: ignore[misc]
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent),
                                      encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, str(path))


def parse_trending_ranks(md_path: Path) -> dict[str, int]:
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding='utf-8')
    ranks: dict[str, int] = {}
    for m in re.finditer(r'^\|\s*(\d+)\s*\|\s*\*\*(\w+)\*\*', text, re.MULTILINE):
        ranks[m.group(2)] = int(m.group(1))
    return ranks


def parse_marketcap_ranks(md_path: Path) -> dict[str, int]:
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding='utf-8')
    mc_section = re.split(r'##.*Market\s*Cap|##.*市值', text, flags=re.IGNORECASE)
    if len(mc_section) < 2:
        return {}
    ranks: dict[str, int] = {}
    for m in re.finditer(r'^\|\s*(\d+)\s*\|\s*\*\*(\w+)\*\*', mc_section[-1], re.MULTILINE):
        ranks[m.group(2)] = int(m.group(1))
    return ranks


def _load_api_key() -> str | None:
    data = read_json(TAGCLAW_CREDS)
    if not data:
        return None
    return (data.get('api_key') or data.get('apiKey') or '').strip() or None


def fetch_recent_feed(api_key: str) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run([
            'curl', '-sS', 'https://bsc-api.tagai.fun/tagclaw/feed?pages=0',
            '-H', f'Authorization: Bearer {api_key}',
            '-H', 'Accept: application/json',
        ], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return []
        payload = json.loads(proc.stdout)
        return payload.get('tweets') or []
    except Exception:
        return []


def _log_score(value: float, reference: float = 10.0) -> float:
    """Log-scale normalization.

    Maps 0→0, reference→~0.5, and grows logarithmically beyond.
    Avoids the "leader always 1.0" linear-max problem.
    """
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(reference))


def compute_social_metrics(items: list[dict[str, Any]], tracked_ticks: list[str]) -> dict[str, dict[str, Any]]:
    """Compute social metrics for both 24h and 7d windows."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=WINDOW_24H)
    cutoff_7d = now - timedelta(hours=WINDOW_7D)

    def _empty() -> dict[str, Any]:
        return {
            'posts_24h': 0, 'engagement_24h': 0.0,
            'posts_7d': 0, 'engagement_7d': 0.0,
            'likes_24h': 0, 'replies_24h': 0, 'retweets_24h': 0, 'quotes_24h': 0,
        }

    out = {tick: _empty() for tick in tracked_ticks}

    for it in items:
        tick = it.get('tick')
        if tick not in out:
            continue
        try:
            ts = datetime.fromisoformat(str(it.get('tweetTime')).replace('Z', '+00:00'))
        except Exception:
            continue
        if ts < cutoff_7d:
            continue

        likes = float(it.get('likeCount') or 0)
        replies = float(it.get('replyCount') or 0)
        retweets = float(it.get('retweetCount') or 0)
        quotes = float(it.get('quoteCount') or 0)
        eng = likes + replies * 2 + retweets * 1.5 + quotes * 1.5 + 0.5  # base 0.5 per post

        out[tick]['posts_7d'] += 1
        out[tick]['engagement_7d'] += eng

        if ts >= cutoff_24h:
            out[tick]['posts_24h'] += 1
            out[tick]['engagement_24h'] += eng
            out[tick]['likes_24h'] += int(likes)
            out[tick]['replies_24h'] += int(replies)
            out[tick]['retweets_24h'] += int(retweets)
            out[tick]['quotes_24h'] += int(quotes)

    return out


def compute_trade_metrics(tracked_ticks: list[str]) -> dict[str, dict[str, Any]]:
    """Compute trade metrics for 24h and 7d windows, unioning all snapshot files."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=WINDOW_24H)
    cutoff_7d = now - timedelta(hours=WINDOW_7D)

    def _empty() -> dict[str, Any]:
        return {
            'trade_count_24h': 0, 'trade_volume_24h': 0.0,
            'trade_count_7d': 0, 'trade_volume_7d': 0.0,
            'trade_file_fetched_at': None,
            'data_age_hours': None,
        }

    out = {tick: _empty() for tick in tracked_ticks}

    for tick in tracked_ticks:
        tick_dir = ONCHAIN_RAW / tick
        if not tick_dir.exists():
            continue
        files = sorted(tick_dir.glob('trades-*.json'))
        if not files:
            continue

        # Union all trades across snapshot files, dedup by txHashUrl
        seen_tx: set[str] = set()
        all_trades: list[dict[str, Any]] = []
        latest_fetch: str | None = None

        for f in files:
            data = read_json(f) or {}
            meta = data.get('_meta') or {}
            fetched = meta.get('fetched_at')
            if fetched:
                if latest_fetch is None or fetched > latest_fetch:
                    latest_fetch = fetched
            for tr in data.get('trades') or []:
                tx_hash = tr.get('txHashUrl') or ''
                if tx_hash and tx_hash in seen_tx:
                    continue
                if tx_hash:
                    seen_tx.add(tx_hash)
                all_trades.append(tr)

        out[tick]['trade_file_fetched_at'] = latest_fetch

        # Compute data age
        if latest_fetch:
            try:
                fetch_dt = datetime.fromisoformat(latest_fetch.replace('Z', '+00:00'))
                out[tick]['data_age_hours'] = round((now - fetch_dt).total_seconds() / 3600, 1)
            except Exception:
                pass

        for tr in all_trades:
            try:
                ts = datetime.fromtimestamp(int(tr.get('time')) / 1000, tz=timezone.utc)
            except Exception:
                continue
            vol = 0.0
            try:
                vol = float(tr.get('volume') or 0)
            except Exception:
                pass

            if ts >= cutoff_7d:
                out[tick]['trade_count_7d'] += 1
                out[tick]['trade_volume_7d'] += vol
            if ts >= cutoff_24h:
                out[tick]['trade_count_24h'] += 1
                out[tick]['trade_volume_24h'] += vol

    return out


def compute_trend(current_rank: int | None, previous_rank: int | None) -> str:
    if current_rank is None or previous_rank is None:
        return 'stable'
    delta = previous_rank - current_rank
    if delta > 0:
        return 'rising'
    if delta < 0:
        return 'declining'
    return 'stable'


def _momentum_label(rate_24h: float, rate_7d: float) -> str:
    """Compare 24h rate to 7d daily average rate."""
    if rate_7d <= 0:
        return 'surging' if rate_24h > 0 else 'quiet'
    ratio = rate_24h / rate_7d
    if ratio >= 2.0:
        return 'surging'
    if ratio >= 1.3:
        return 'accelerating'
    if ratio >= 0.7:
        return 'steady'
    if ratio >= 0.3:
        return 'cooling'
    return 'quiet'


def main() -> int:
    computed_at = now_iso()
    source_health = 'ok'
    notes: list[str] = []

    trending_path = WIKI / 'trending-ticks.md'
    stats_path = WIKI / 'community-stats.md'
    current_trending = parse_trending_ranks(trending_path)
    current_marketcap = parse_marketcap_ranks(trending_path) or parse_marketcap_ranks(stats_path)
    previous = read_json(HEAT_PATH)
    previous_ticks = (previous or {}).get('ticks', {})

    # --- Yesterday baseline ---
    today_str = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d')
    yesterday_str = (datetime.now(timezone.utc).astimezone() - timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_snapshot_path = HEAT_DAILY_DIR / f'{yesterday_str}.json'
    yesterday_data = read_json(yesterday_snapshot_path)
    yesterday_ticks = (yesterday_data or {}).get('ticks', {})
    yesterday_rank_map: dict[str, int | None] = {
        tick: data.get('heat_rank') or data.get('trending_rank')
        for tick, data in yesterday_ticks.items()
    }

    api_key = _load_api_key()
    feed_items = fetch_recent_feed(api_key) if api_key else []
    social_metrics = compute_social_metrics(feed_items, TRACKED_TICKS)
    trade_metrics = compute_trade_metrics(TRACKED_TICKS)

    if not current_trending:
        source_health = 'partial'
        notes.append('trending-ticks.md not found or unparseable')
    if not feed_items:
        source_health = 'partial'
        notes.append('tagclaw feed unavailable; social side degraded')

    # Log-scale reference points (tuned to typical TagClaw activity levels)
    SOCIAL_ENG_REF = 50.0    # ~50 engagement units ≈ 0.5 score
    TRADE_COUNT_REF = 30.0   # ~30 trades ≈ 0.5 score
    TRADE_VOL_REF = 3000.0   # ~3000 volume units ≈ 0.5 score

    staged: list[dict[str, Any]] = []
    for tick in TRACKED_TICKS:
        social = social_metrics[tick]
        trade = trade_metrics[tick]

        # Social sub-score: blend 24h burst with 7d sustained
        social_24h_raw = _log_score(social['engagement_24h'], SOCIAL_ENG_REF)
        social_7d_daily = social['engagement_7d'] / 7.0  # daily average over 7d
        social_7d_raw = _log_score(social_7d_daily, SOCIAL_ENG_REF)
        social_score = BURST_WEIGHT * social_24h_raw + SUSTAINED_WEIGHT * social_7d_raw

        # Trade sub-score: blend 24h burst with 7d sustained
        tc_24h = _log_score(trade['trade_count_24h'], TRADE_COUNT_REF)
        tv_24h = _log_score(trade['trade_volume_24h'], TRADE_VOL_REF)
        trade_24h_raw = TRADE_COUNT_W * tc_24h + TRADE_VOLUME_W * tv_24h

        tc_7d_daily = trade['trade_count_7d'] / 7.0
        tv_7d_daily = trade['trade_volume_7d'] / 7.0
        tc_7d = _log_score(tc_7d_daily, TRADE_COUNT_REF)
        tv_7d = _log_score(tv_7d_daily, TRADE_VOL_REF)
        trade_7d_raw = TRADE_COUNT_W * tc_7d + TRADE_VOLUME_W * tv_7d

        trade_score = BURST_WEIGHT * trade_24h_raw + SUSTAINED_WEIGHT * trade_7d_raw

        social_delta = social_24h_raw - social_7d_raw
        trade_delta = trade_24h_raw - trade_7d_raw
        composite_burst_score = SOCIAL_WEIGHT * social_24h_raw + TRADE_WEIGHT * trade_24h_raw
        composite_sustained_score = SOCIAL_WEIGHT * social_7d_raw + TRADE_WEIGHT * trade_7d_raw
        composite_score = SOCIAL_WEIGHT * social_score + TRADE_WEIGHT * trade_score
        composite_delta = composite_burst_score - composite_sustained_score

        # Momentum
        social_momentum = _momentum_label(social['engagement_24h'], social_7d_daily)
        trade_momentum = _momentum_label(
            float(trade['trade_count_24h']),
            tc_7d_daily,
        )

        # Data coverage
        coverage: list[str] = []
        if social['posts_7d'] == 0 and social['posts_24h'] == 0:
            coverage.append('no_social')
        elif social['posts_7d'] == 0:
            coverage.append('social_24h_only')
        if trade['trade_count_7d'] == 0 and trade['trade_count_24h'] == 0:
            coverage.append('no_trade')
        elif trade['trade_count_7d'] == 0:
            coverage.append('trade_24h_only')
        if trade['data_age_hours'] is not None and trade['data_age_hours'] > 48:
            coverage.append(f'trade_data_stale_{trade["data_age_hours"]:.0f}h')

        staged.append({
            'tick': tick,
            'social': social,
            'trade': trade,
            'social_score': round(social_score, 4),
            'trade_score': round(trade_score, 4),
            'social_delta': round(social_delta, 4),
            'trade_delta': round(trade_delta, 4),
            'social_burst_score': round(social_24h_raw, 4),
            'social_sustained_score': round(social_7d_raw, 4),
            'trade_burst_score': round(trade_24h_raw, 4),
            'trade_sustained_score': round(trade_7d_raw, 4),
            'composite_burst_score': round(composite_burst_score, 4),
            'composite_sustained_score': round(composite_sustained_score, 4),
            'composite_score': round(composite_score, 4),
            'composite_delta': round(composite_delta, 4),
            'social_momentum': social_momentum,
            'trade_momentum': trade_momentum,
            'data_coverage': coverage or ['full'],
            'trending_rank': current_trending.get(tick),
            'market_cap_rank': current_marketcap.get(tick),
        })

    staged.sort(key=lambda x: (-x['composite_score'], x['tick']))
    current_heat_ranks = {row['tick']: idx + 1 for idx, row in enumerate(staged)}
    previous_rank_map = {tick: (data.get('heat_rank') or data.get('trending_rank'))
                         for tick, data in previous_ticks.items()}

    ticks: dict[str, dict[str, Any]] = {}
    top_rising: list[str] = []
    top_declining: list[str] = []

    for row in staged:
        tick = row['tick']
        cur_rank = current_heat_ranks[tick]
        prev_rank = previous_rank_map.get(tick)
        trend = compute_trend(cur_rank, prev_rank)
        if prev_rank is None:
            trend_basis = f'first_v3_snapshot (rank={cur_rank})'
        elif trend == 'rising':
            trend_basis = f'composite_rank_improved ({prev_rank}→{cur_rank})'
        elif trend == 'declining':
            trend_basis = f'composite_rank_dropped ({prev_rank}→{cur_rank})'
        else:
            trend_basis = f'composite_rank_stable ({prev_rank}→{cur_rank})'

        rank_delta = (prev_rank - cur_rank) if prev_rank is not None else 0
        yesterday_rank = yesterday_rank_map.get(tick)
        yesterday_rank_delta = (yesterday_rank - cur_rank) if yesterday_rank is not None else None

        ticks[tick] = {
            'trend': trend,
            'trend_score': row['composite_score'],
            'trend_basis': trend_basis,
            'heat_rank': cur_rank,
            'previous_heat_rank': prev_rank,
            'heat_rank_delta': rank_delta,
            'yesterday_heat_rank': yesterday_rank,
            'yesterday_heat_rank_delta': yesterday_rank_delta,
            'trending_rank': row['trending_rank'],
            'market_cap_rank': row['market_cap_rank'],
            # 24h raw metrics
            'social_posts_24h': row['social']['posts_24h'],
            'social_engagement_24h': round(row['social']['engagement_24h'], 3),
            'trade_count_24h': row['trade']['trade_count_24h'],
            'trade_volume_24h': round(row['trade']['trade_volume_24h'], 3),
            # 7d raw metrics
            'social_posts_7d': row['social']['posts_7d'],
            'social_engagement_7d': round(row['social']['engagement_7d'], 3),
            'trade_count_7d': row['trade']['trade_count_7d'],
            'trade_volume_7d': round(row['trade']['trade_volume_7d'], 3),
            # Computed scores
            'social_score': row['social_score'],
            'trade_score': row['trade_score'],
            'social_delta': row['social_delta'],
            'trade_delta': row['trade_delta'],
            'social_burst_score': row['social_burst_score'],
            'social_sustained_score': row['social_sustained_score'],
            'trade_burst_score': row['trade_burst_score'],
            'trade_sustained_score': row['trade_sustained_score'],
            'composite_burst_score': row['composite_burst_score'],
            'composite_sustained_score': row['composite_sustained_score'],
            'composite_score': row['composite_score'],
            'composite_delta': row['composite_delta'],
            # Momentum & coverage
            'social_momentum': row['social_momentum'],
            'trade_momentum': row['trade_momentum'],
            'data_coverage': row['data_coverage'],
        }
        if trend == 'rising':
            top_rising.append(tick)
        elif trend == 'declining':
            top_declining.append(tick)

    # --- Priority override ---
    # If the existing community-heat.json has an active priority_override, preserve it:
    # force the override tick to the declared rank and adjust its composite_score to justify it.
    existing_override = (previous or {}).get('priority_override')
    priority_override_out = None
    if existing_override and existing_override.get('active'):
        priority_override_out = existing_override
        ov_tick = existing_override.get('tick')
        ov_rank = int(existing_override.get('rank', 1))
        if ov_tick in ticks:
            # Build ordered list from current heat_ranks
            ranked = sorted(ticks.keys(), key=lambda t: ticks[t]['heat_rank'])
            if ticks[ov_tick]['heat_rank'] != ov_rank:
                ranked.remove(ov_tick)
                ranked.insert(ov_rank - 1, ov_tick)
                for new_rank, t in enumerate(ranked, 1):
                    prev_r = ticks[t]['previous_heat_rank']
                    ticks[t]['heat_rank'] = new_rank
                    ticks[t]['heat_rank_delta'] = (prev_r - new_rank) if prev_r is not None else 0
            # Adjust composite_score so it is >= next rank's score + 0.01
            if ov_rank < len(ranked):
                next_tick = ranked[ov_rank]  # 0-indexed: index ov_rank is rank (ov_rank+1)
                next_score = ticks[next_tick]['composite_score']
                min_score = round(next_score + 0.01, 4)
                if ticks[ov_tick]['composite_score'] < min_score:
                    ticks[ov_tick]['composite_score'] = min_score
                    ticks[ov_tick]['trend_score'] = min_score
            notes.append(f'priority_override applied: {ov_tick} forced to rank {ov_rank}')
        # Rebuild rising/declining lists to reflect any rank changes
        top_rising = [t for t, d in ticks.items() if d['trend'] == 'rising']
        top_declining = [t for t, d in ticks.items() if d['trend'] == 'declining']

    if previous is None:
        notes.append('first v3 run — no previous snapshot for comparison')

    result = {
        'version': 'v3',
        'computed_at': computed_at,
        'windows': {'burst_hours': WINDOW_24H, 'sustained_hours': WINDOW_7D},
        'priority_override': priority_override_out,
        'ticks': ticks,
        'top_rising': top_rising,
        'top_declining': top_declining,
        'source_health': source_health,
        'notes': '; '.join(notes) or None,
        'score_formula': {
            'composite': f'{SOCIAL_WEIGHT} * social + {TRADE_WEIGHT} * trade',
            'temporal_blend': f'{BURST_WEIGHT} * 24h_burst + {SUSTAINED_WEIGHT} * 7d_sustained',
            'social_signal': 'log-scaled engagement (posts + weighted likes/replies/retweets/quotes)',
            'trade_signal': f'log-scaled ({TRADE_COUNT_W} * count + {TRADE_VOLUME_W} * volume)',
            'normalization': 'log1p(value) / log1p(reference) — absolute scale, no max-relative',
            'reference_points': {
                'social_engagement': SOCIAL_ENG_REF,
                'trade_count': TRADE_COUNT_REF,
                'trade_volume': TRADE_VOL_REF,
            },
        },
    }

    atomic_write_json(HEAT_PATH, result)

    # Save daily snapshot (once per calendar day, keyed to today)
    today_snapshot_path = HEAT_DAILY_DIR / f'{today_str}.json'
    if not today_snapshot_path.exists():
        atomic_write_json(today_snapshot_path, result)

    # Prune daily snapshots older than 7 days
    if HEAT_DAILY_DIR.exists():
        cutoff_str = (datetime.now(timezone.utc).astimezone() - timedelta(days=7)).strftime('%Y-%m-%d')
        for old in HEAT_DAILY_DIR.glob('*.json'):
            if old.stem < cutoff_str:
                old.unlink(missing_ok=True)

    # Emit wiki event
    append_wiki_event(
        event_type='community_heat_refresh',
        producer='refresh_wiki_community_heat_v1',
        artifact='runtime/shared/community-heat.json',
        status=source_health,
        summary=f"v3: {len(ticks)} ticks, rising={top_rising}, declining={top_declining}",
        detail={'tick_count': len(ticks), 'top_rising': top_rising, 'top_declining': top_declining,
                'source_health': source_health, 'model_version': 'v3'},
    )

    # Provenance sidecar
    src_refs = ['wiki/tagclaw-platform/trending-ticks.md']
    if stats_path.exists():
        src_refs.append('wiki/tagclaw-platform/community-stats.md')
    write_provenance_sidecar(
        HEAT_PATH,
        producer='refresh_wiki_community_heat_v1',
        source_refs=src_refs,
        schema_version='v3',
        facts={
            'tick_count': len(ticks),
            'top_rising': top_rising,
            'top_declining': top_declining,
            'source_health': source_health,
        },
        root=ROOT,
    )

    print(json.dumps({'status': 'ok', 'version': 'v3', 'ticks': list(ticks.keys()),
                       'source_health': source_health}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
