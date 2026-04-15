#!/usr/bin/env python3
"""Shared wiki utilities for main/bookmarker/trader agents.

Provides a unified interface to read wiki/concepts/ pages and extract
structured insights for agent decision-making.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
WIKI_ROOT = WORKSPACE / 'wiki' / 'concepts'

CONCEPT_TO_THEME: dict[str, str] = {
    'TagClaw': 'tagclaw', 'TagAI': 'tagclaw', 'PoB': 'tagclaw',
    'DeSoc': 'desoc-agent', 'ATOC': 'atoc-agent',
    'AgentEconomy': 'agent-infra', 'TokenEconomy': 'token-economy',
    'ICM': 'token-economy', 'Philosophy': 'general-builder',
    'Steem': 'tagclaw', 'Wormhole3': 'tagclaw',
    'SocialFi': 'desoc-agent', 'CommunityDAO': 'tagclaw',
    'Bitcoin': 'general-builder', 'MarketTrading': 'token-economy',
    'BuilderLife': 'general-builder', 'AttentionEconomy': 'desoc-agent',
    'NomadVerse': 'general-builder', 'NomadLife': 'general-builder',
    'Web3Identity': 'desoc-agent', 'Misc': 'general-builder',
}

THEME_TO_CONCEPTS: dict[str, list[str]] = {}
for _c, _t in CONCEPT_TO_THEME.items():
    THEME_TO_CONCEPTS.setdefault(_t, []).append(_c)


def list_concepts() -> list[str]:
    if not WIKI_ROOT.exists():
        return []
    return sorted(p.stem for p in WIKI_ROOT.glob('*.md') if p.stem != 'index')


def load_concept(name: str) -> str | None:
    path = WIKI_ROOT / f'{name}.md'
    if not path.exists():
        return None
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return None


def _extract_section(content: str, section_name: str, max_lines: int = 10) -> str | None:
    pattern = re.compile(
        r'^#{1,4}\s*' + re.escape(section_name),
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return None
    start = match.end()
    lines = content[start:].splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') and result:
            break
        if stripped:
            result.append(stripped)
        if len(result) >= max_lines:
            break
    return '\n'.join(result) if result else None


def get_concept_insight(name: str, section: str = 'Agent Insights') -> str | None:
    content = load_concept(name)
    if not content:
        return None
    result = _extract_section(content, section)
    if result:
        return result
    for alt in ('Agent Implications', 'Insights', 'Key Takeaways'):
        result = _extract_section(content, alt)
        if result:
            return result
    return None


def get_concept_core_stance(name: str) -> str | None:
    content = load_concept(name)
    if not content:
        return None
    text = _extract_section(content, 'Core Position', max_lines=15)
    if not text:
        text = _extract_section(content, 'Core Stance', max_lines=15)
    if not text:
        return None
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
        if clean:
            sentences.append(clean)
        if len(sentences) >= 2:
            break
    return ' '.join(sentences) if sentences else None


def get_top_wiki_topic_from_heatmap(heatmap_path: str | Path) -> str | None:
    path = Path(heatmap_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    heat_1m: dict[str, float] = (data.get('heatmap') or {}).get('1m') or {}
    community_fit: dict[str, float] = data.get('community_fit_scores') or {}
    best_topic: str | None = None
    best_score: float = -1.0
    for topic, heat in heat_1m.items():
        fit = community_fit.get(topic, 0.0)
        if fit <= 0:
            continue
        score = float(heat) * 0.7 + float(fit) * 0.3
        if score > best_score:
            best_score = score
            best_topic = topic
    return best_topic
