from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def has_tagclaw_internal_signals(text_lower: str) -> bool:
    tagclaw_internals = [
        'tagclaw api', 'tagclaw protocol', 'tagclaw tick', 'tagclaw token',
        'claw token', 'clawcoin', 'tagclaw smart contract', 'tagclaw chain',
        'tagclaw tokenomics', 'tagclaw on-chain', 'tagclaw vp', 'tagclaw vm',
    ]
    return any(term in text_lower for term in tagclaw_internals)


def has_general_builder_or_market_signals(text_lower: str) -> bool:
    general_signals = [
        'base', 'meme', 'builder', 'builders', 'wallet', 'smart wallet',
        'market cap', 'trader', 'trading', 'ecosystem', 'community',
        'liquidity', 'launch', 'pump', 'volume', 'holder', 'coordination',
        'desoc', 'social graph', 'agent', 'agents',
    ]
    return any(term in text_lower for term in general_signals)


def is_tagclaw_protocol_only(text_lower: str) -> bool:
    """True only when the content is predominantly TagClaw-internal."""
    return has_tagclaw_internal_signals(text_lower) and not has_general_builder_or_market_signals(text_lower)


def compute_tick_counts_24h(social_history_path: Path) -> dict[str, int]:
    history = _read_json(social_history_path) or {}
    items = history.get('items') or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    tick_counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'post' or item.get('result_status') != 'ok':
            continue
        executed_at = str(item.get('executed_at') or '')
        try:
            dt = datetime.fromisoformat(executed_at.replace('Z', '+00:00')).astimezone(timezone.utc)
        except Exception:
            continue
        if dt < cutoff:
            continue
        tick = str((item.get('request') or {}).get('tick') or 'BUIDL').strip() or 'BUIDL'
        tick_counts[tick] = tick_counts.get(tick, 0) + 1
    return tick_counts


def compute_buidl_pct_24h(social_history_path: Path) -> float:
    tick_counts = compute_tick_counts_24h(social_history_path)
    total = sum(tick_counts.values())
    if total == 0:
        return 0.0
    return tick_counts.get('BUIDL', 0) / total


# ── BUIDL-first community priority ──
# Owner directive (2026-05-30): #BUIDL 社区为最高优先发布目标。
# 当内容不适合 BUIDL 时，优先发布到更活跃、市值更高的社区。
BUIDL_PRIORITY_THRESHOLD = 0.65  # target 65%+ of posts to BUIDL (raised from 0.50)


def load_tick_activity_rankings(community_scan_path: Path | None = None) -> dict[str, float]:
    """Load tick activity scores from community scan data.
    Returns {tick_name: activity_score} for sorting non-BUIDL ticks by activity.
    Higher activity_score = more active community."""
    rankings: dict[str, float] = {}
    if community_scan_path is None:
        return rankings
    if not community_scan_path.exists():
        return rankings
    try:
        scan = json.loads(community_scan_path.read_text(encoding='utf-8'))
        trending = scan.get('trending_ticks') or []
        # Trending ticks are already sorted by post_count desc
        # Assign score inversely proportional to rank (1st place = 1.0, 2nd = 0.8, ...)
        for idx, t in enumerate(trending):
            tick = str(t.get('tick') or '').strip()
            count = int(t.get('post_count') or 0)
            if tick:
                # blend: rank score (50%) + normalized post count (50%)
                rank_score = max(0.1, 1.0 - idx * 0.15)
                rankings[tick] = round(rank_score + (count / 100.0), 4)
    except Exception:
        pass
    return rankings


def choose_tick(
    keywords: list[str],
    wiki_trending_ticks: list[str] | None = None,
    buidl_pct_24h: float | None = None,
    *,
    text: str | None = None,
    theme: str | None = None,
    social_history_path: Path | None = None,
    in_run_tick_counts: dict[str, int] | None = None,
    community_scan_path: Path | None = None,
) -> str:
    content_lower = ' '.join([
        ' '.join(keywords),
        str(text or ''),
    ]).lower()
    combined_lower = ' '.join([
        ' '.join(keywords),
        str(text or ''),
        str(theme or ''),
    ]).lower()

    projected_counts: dict[str, int] = {}
    if social_history_path is not None:
        projected_counts.update(compute_tick_counts_24h(social_history_path))
    for tick_name, count in (in_run_tick_counts or {}).items():
        token = str(tick_name or '').strip()
        if not token:
            continue
        projected_counts[token] = projected_counts.get(token, 0) + int(count or 0)

    projected_total = sum(projected_counts.values())
    projected_buidl_ratio = (
        projected_counts.get('BUIDL', 0) / projected_total if projected_total > 0 else (buidl_pct_24h or 0.0)
    )
    buidl_needed = projected_buidl_ratio < BUIDL_PRIORITY_THRESHOLD if projected_total > 0 else (buidl_pct_24h is not None and buidl_pct_24h < BUIDL_PRIORITY_THRESHOLD)

    # ── BUIDL-first routing ──
    # BUIDL is the absolute priority community; always check if content fits BUIDL first.
    def _apply_buidl_bias(candidate: str) -> str:
        if buidl_needed and candidate == 'TagClaw' and not is_tagclaw_protocol_only(content_lower):
            return 'BUIDL'
        # Even when BUIDL quota is met, if content has builder/market signals, prefer BUIDL
        if candidate != 'BUIDL' and has_general_builder_or_market_signals(content_lower):
            return 'BUIDL'
        return candidate

    # Aggressively route builder/market content to BUIDL regardless of quota
    if has_general_builder_or_market_signals(combined_lower) or (theme or '') in {'general-builder', 'token-coordination'}:
        return 'BUIDL'

    # For non-BUIDL ticks: prefer the most active ones by sorting with activity rankings
    if wiki_trending_ticks:
        active_rankings = load_tick_activity_rankings(community_scan_path)
        # Sort candidate ticks by activity score (highest first), then by keyword match
        scored_candidates: list[tuple[float, str]] = []
        for tick in wiki_trending_ticks:
            if tick == 'BUIDL':
                continue  # BUIDL already handled above
            activity_score = active_rankings.get(tick, 0.0)
            keyword_match_bonus = 2.0 if tick.lower() in combined_lower else 0.0
            scored_candidates.append((activity_score + keyword_match_bonus, tick))
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        if scored_candidates:
            return _apply_buidl_bias(scored_candidates[0][1])
        return 'BUIDL'

    # ── 2026-05-31 owner directive: default to BUIDL, not TagClaw ──
    # Only route to TagClaw when content is strictly TagClaw-internal (protocol/contract/chain).
    # "Agent" and "DeSoc" keywords alone no longer trigger TagClaw routing.
    if is_tagclaw_protocol_only(content_lower):
        return 'TagClaw'
    return 'BUIDL'
