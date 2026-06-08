#!/usr/bin/env python3
"""Controversy detector: identifies trending topics from topic-heatmap.json.

Used by publish_native_social_drafts_v1.py to generate controversy-hook drafts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS


HEATMAP_DEFAULT = (MAIN_WS / 'runtime' / 'bookmarker' / 'topic-heatmap.json')

MAIN_WORKSPACE = (MAIN_WS)
WIKI_EXECUTION_BRIEF = MAIN_WORKSPACE / 'runtime' / 'shared' / 'wiki-execution-brief.json'
WIKI_SOCIAL_TRENDING = MAIN_WORKSPACE / 'wiki' / 'social' / 'trending.md'


def load_wiki_open_questions() -> list[str]:
    """从 wiki-execution-brief.json 提取各话题的 controversy_hook 列表。
    返回 ['话题A 的争议点...', '话题B 的争议点...', ...]
    若不可用，返回空列表。
    """
    try:
        import json as _json
        data = _json.loads(WIKI_EXECUTION_BRIEF.read_text(encoding='utf-8'))
    except Exception:
        return []
    questions = []
    for theme in (data.get('top_themes') or []):
        hook = theme.get('controversy_hook', '')
        if hook:
            questions.append(hook)
    return questions


def load_wiki_trending_topics() -> list[str]:
    """从 wiki/social/trending.md 读取当前热点话题名列表。"""
    try:
        content = WIKI_SOCIAL_TRENDING.read_text(encoding='utf-8')
    except Exception:
        return []
    topics = []
    for line in content.splitlines():
        if not line.startswith('|') or '话题' in line or '---' in line:
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        if parts and parts[0]:
            topics.append(parts[0])
    return topics


def detect_trending(heatmap_path: Path = HEATMAP_DEFAULT) -> list[dict[str, Any]]:
    """Return trending topics sorted by momentum descending.

    A topic is trending if heat_1m > 0.15 AND community_fit_score > 0.5.

    Returns: [{topic, heat_1m, heat_6m, community_fit, momentum, is_breakout}, ...]
    """
    if not heatmap_path.exists():
        return []
    try:
        data = json.loads(heatmap_path.read_text(encoding='utf-8'))
    except Exception:
        return []

    heat_1m: dict[str, float] = (data.get('heatmap') or {}).get('1m') or {}
    heat_6m: dict[str, float] = (data.get('heatmap') or {}).get('6m') or {}
    fit_scores: dict[str, float] = data.get('community_fit_scores') or {}

    results: list[dict[str, Any]] = []
    for topic, h1m in heat_1m.items():
        if h1m <= 0.15:
            continue
        cfit = fit_scores.get(topic, 0.0)
        if cfit <= 0.5:
            continue
        h6m = heat_6m.get(topic, 0.0)
        momentum = h1m / (h6m + 0.01)
        results.append({
            'topic': topic,
            'heat_1m': h1m,
            'heat_6m': h6m,
            'community_fit': cfit,
            'momentum': round(momentum, 3),
            'is_breakout': momentum > 3.0,
        })

    results.sort(key=lambda x: x['momentum'], reverse=True)

    # Wiki-enriched: 若 wiki 有 controversy_hook，加入结果并优先排序
    wiki_questions = load_wiki_open_questions()
    wiki_trending_topics = load_wiki_trending_topics()

    # 对 results 列表，若某话题在 wiki_trending_topics 中 → 提升优先级
    for item in results:
        if item.get('topic') in wiki_trending_topics:
            item['wiki_boosted'] = True
            item['momentum'] = item.get('momentum', 0) * 1.3  # wiki trending boost

    # 将 wiki_questions 作为额外的候选争议话题（不依赖 heatmap 阈值）
    if wiki_questions and not results:
        # heatmap 无热点时，用 wiki 预编译的争议点兜底
        results = [{
            'topic': f'wiki-controversy-{i}',
            'momentum': 0.5,
            'heat_1m': 0.3,
            'community_fit': 0.6,
            'controversy_hook': q,
            'source': 'wiki-precompiled'
        } for i, q in enumerate(wiki_questions[:2])]

    # 重新排序（momentum 降序）
    results = sorted(results, key=lambda x: x.get('momentum', 0), reverse=True)

    return results


if __name__ == '__main__':
    print('=== Trending Topics ===')
    for t in detect_trending():
        src = '(wiki)' if t.get('source') == 'wiki-precompiled' else ''
        print(f"  {t['topic']}: momentum={t.get('momentum',0):.2f} {src}")
    print('=== Wiki Open Questions ===')
    for q in load_wiki_open_questions():
        print(f'  - {q}')
