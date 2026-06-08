#!/usr/bin/env python3
"""Legacy fallback-only shadow publisher.

This path is retained only as a non-canonical fallback/debug artifact generator.
The canonical planner is scripts/publish_bookmarker_runtime_v2.py.
Do not treat this file as an equal planning path.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from publication_memory import (
    compute_post_content_hash,
    compute_post_content_hash_excluding_source,
    infer_claim_family,
)
from tick_routing import choose_tick as shared_choose_tick
from agency_paths import BOOKMARKER_WS, MAIN_WS

BOOKMARKER_ROOT = (BOOKMARKER_WS)
MAIN_ROOT = (MAIN_WS)
OUT = MAIN_ROOT / 'runtime' / 'bookmarker-shadow' / 'social-drafts.json'
TARGETS_RE = re.compile(r'([A-Za-z0-9]+)\s*\(([^,\)]+),\s*vp=(\d+)\)')


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
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def shorten(text: str, max_chars: int = 140) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + '…'


def choose_tick(
    keywords: list[str],
    buidl_pct_24h: float | None = None,
    *,
    text: str | None = None,
    theme: str | None = None,
) -> str:
    return shared_choose_tick(
        keywords,
        buidl_pct_24h=buidl_pct_24h,
        text=text,
        theme=theme,
        social_history_path=MAIN_ROOT / 'runtime' / 'shared' / 'social-history.json',
        in_run_tick_counts={},
        community_scan_path=MAIN_ROOT / 'runtime' / 'bookmarker' / 'community-scan.json',
    )


def infer_theme(keywords: list[str], text: str,
                wiki_context: dict[str, str] | None = None) -> str:
    joined = (' '.join(keywords) + ' ' + text).lower()
    if any(k in joined for k in ['ai agents', 'ai agent', 'agent-native social protocol', '去中心化社交', 'desoc', 'social graph', 'platform risk', 'bot tooling']):
        return 'desoc-agent'
    if any(k in joined for k in ['agent-infrastructure', 'openclaw', 'agentos', 'intent', 'orchestration', 'coordination layer', 'protocol layer']):
        return 'agent-infra'
    if any(k in joined for k in ['token', 'stablecoin', 'settlement', 'reward', 'incentive', 'community token', 'coordination', 'socialfi', 'cashtag', 'cashtags']):
        return 'token-coordination'

    # High-recognition boost: prefer wiki themes with 1.5× weight over general-builder.
    # If a high-recognition wiki theme's broad keywords appear, return that theme.
    if wiki_context:
        _weights_raw = wiki_context.get('_weights') or '{}'
        try:
            _weights: dict[str, float] = (
                json.loads(_weights_raw) if isinstance(_weights_raw, str) else _weights_raw
            )
        except Exception:
            _weights = {}
        _WIKI_TO_INFER: dict[str, str] = {
            'DeSoc': 'desoc-agent', 'ATOC': 'desoc-agent',
            'AgentInfrastructure': 'agent-infra',
            'TokenEconomy': 'token-coordination',
        }
        _BROAD_KW: dict[str, list[str]] = {
            'desoc-agent': ['social', 'DeSoc', 'agent', 'reddit', 'twitter', 'platform'],
            'agent-infra': ['agent', 'infra', 'protocol', 'coordination', 'intent', 'layer'],
            'token-coordination': ['token', 'incentive', 'community', 'coordination', 'reward'],
        }
        joined_lower = joined.lower()
        for wiki_theme, weight in _weights.items():
            if float(weight) >= 1.5:
                infer_name = _WIKI_TO_INFER.get(wiki_theme)
                if infer_name:
                    if any(k.lower() in joined_lower for k in _BROAD_KW.get(infer_name, [])):
                        return infer_name

    return 'general-builder'


def extract_insights(article: str, max_items: int = 5) -> list[str]:
    """从 wiki 文章里提取关键洞察 bullet list。"""
    lines = article.splitlines()
    in_insights = False
    results = []
    for line in lines:
        stripped = line.strip()
        if re.search(r'#+\s*(关键洞察|Key Insights|洞察)', stripped, re.IGNORECASE):
            in_insights = True
            continue
        if in_insights:
            if stripped.startswith('##'):
                break
            if stripped.startswith('-') or stripped.startswith('*'):
                clean = re.sub(r'^[-*]\s*', '', stripped)
                clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
                if clean:
                    results.append(clean)
            if len(results) >= max_items:
                break
    return results


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

HIGH_WEIGHT_KEYWORDS = re.compile(r'运营机制参考|VP\s*衰减|PoB|Credit|核心立场', re.IGNORECASE)


def _extract_tagclawx_insight(content: str, max_lines: int = 5) -> list[str]:
    """Extract lines from '对 TagClawX 的启示' or '对 TagClawX Agent 的启示' section."""
    pattern = re.compile(r'^#{1,4}\s*对\s*TagClawX.*启示', re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return []
    start = match.end()
    lines = content[start:].splitlines()
    results: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') and results:
            break
        if stripped.startswith('-') or stripped.startswith('*'):
            clean = re.sub(r'^[-*]\s*', '', stripped)
            clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
            if clean:
                results.append(clean)
        if len(results) >= max_lines:
            break
    return results


def load_wiki_execution_brief() -> dict | None:
    """读取 runtime/shared/wiki-execution-brief.json，校验新鲜度（valid_until）。"""
    brief_path = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-execution-brief.json'
    data = read_json(brief_path)
    if not data:
        return None
    valid_until_str = data.get('valid_until')
    if valid_until_str:
        try:
            valid_until = datetime.fromisoformat(valid_until_str.replace('Z', '+00:00'))
            if valid_until < datetime.now(timezone.utc):
                return None
        except Exception:
            pass
    return data


def load_wiki_social_trending() -> list[dict]:
    """读取 wiki/social/trending.md，解析当前热点话题列表。
    返回 [{'topic': str, 'heat_1m': float, 'week_score': float, 'agent_action': str}, ...]
    若文件不存在或解析失败，返回空列表。
    """
    trending_path = MAIN_ROOT / 'wiki' / 'social' / 'trending.md'
    if not trending_path.exists():
        return []
    try:
        content = trending_path.read_text(encoding='utf-8')
    except Exception:
        return []
    results = []
    # 解析 markdown 表格行（跳过 header 和 separator）
    for line in content.splitlines():
        if not line.startswith('|') or '话题' in line or '---' in line:
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        if len(parts) >= 4:
            try:
                results.append({
                    'topic': parts[0],
                    'heat_1m': float(parts[1]) if parts[1] else 0.0,
                    'week_score': float(parts[2]) if parts[2] else 0.0,
                    'agent_action': parts[3],
                })
            except (ValueError, IndexError):
                continue
    return results


def load_wiki_context() -> dict[str, str]:
    """读取 wiki/concepts/ 目录下各 concept page 的洞察摘要。返回 {theme_name: insight_summary}。

    优先读 MAIN_ROOT/wiki/concepts/，fallback 旧路径 BOOKMARKER_ROOT/memory/wiki/。
    Special key '_weights': JSON string mapping wiki theme name → float multiplier.
    含高权重关键词（运营机制参考/VP衰减/PoB/Credit/核心立场）的文章权重 1.5。
    """
    wiki_dir = MAIN_ROOT / 'wiki' / 'concepts'
    if not wiki_dir.exists():
        wiki_dir = BOOKMARKER_ROOT / 'memory' / 'wiki'
    if not wiki_dir.exists():
        return {}
    context: dict[str, str] = {}
    weights: dict[str, float] = {}
    for md_file in wiki_dir.glob('*.md'):
        if md_file.name in ('index.md',):
            continue
        concept_name = md_file.stem
        theme_name = CONCEPT_TO_THEME.get(concept_name, concept_name)
        try:
            content = md_file.read_text(encoding='utf-8')
        except Exception:
            continue
        # Prefer TagClawX insight section, fallback to extract_insights
        insights = _extract_tagclawx_insight(content, max_lines=5)
        if not insights:
            insights = extract_insights(content, max_items=5)
        if insights:
            # Merge into theme (multiple concepts may map to the same theme)
            existing = context.get(theme_name, '')
            new_text = '\n'.join(insights)
            context[theme_name] = (existing + '\n' + new_text).strip() if existing else new_text
        # High-weight keywords or high-recognition marker
        if HIGH_WEIGHT_KEYWORDS.search(content) or '<!-- performance: high-recognition -->' in content:
            weights[concept_name] = 1.5
    if weights:
        context['_weights'] = json.dumps(weights)
    return context


def build_argument(
    theme: str,
    source_text: str,
    summary: str | None,
    wiki_context: dict[str, str] | None = None,
) -> tuple[str, list[str], str, str | None]:
    """Returns (hook, points, closing, wiki_source)."""
    source_hint = shorten(source_text, 70) if source_text else ''

    # Try wiki-driven points first
    wiki_source: str | None = None
    if wiki_context:
        theme_map: dict[str, list[str]] = {
            'desoc-agent': ['DeSoc', 'ATOC'],
            'agent-infra': ['AgentInfrastructure'],
            'token-coordination': ['TokenEconomy'],
            'general-builder': ['Projects', 'Philosophy'],
        }
        for wiki_theme in theme_map.get(theme, []):
            if wiki_theme in wiki_context:
                wiki_insights = [l.strip() for l in wiki_context[wiki_theme].splitlines() if l.strip()]
                if len(wiki_insights) >= 2:
                    hook = summary or source_text[:80] or '最近在想一个问题：'
                    points = [f'- {insight}' for insight in wiki_insights[:3]]
                    closing = '从 TagClaw / OpenClaw 的实践来看，真正重要的是把这些能力沉淀成可复用的 protocol 层，而不是一次性的工具。'
                    return hook, points, closing, wiki_theme
                break

    # Fallback: hardcoded templates (backward compat)
    if theme == 'desoc-agent':
        hook = 'Reddit、X 开始主动清理 AI Agents，这反而把下一阶段机会讲清楚了。'
        points = [
            '1. 未来的 agent network，不能建立在中心化平台一句”允许/不允许”之上。',
            '2. 对 AI Agents 来说，social、identity、settlement 必须在同一条 protocol 路径里闭环。',
            '3. 所以真正值得建设的，不是给旧平台补一层 bot tooling，而是 agent-native social protocol。',
        ]
        closing = '从 Steem 时代一路做到今天，我越来越确信：Crypto 不是 AI Agent 的外挂支付层，而是它们形成 swarm、获得正反馈、持续 community-driven coordination 的基础设施。'
        return hook, points, closing, wiki_source

    if theme == 'agent-infra':
        hook = '下一代网络的竞争点，可能不再是”谁有更多 App”，而是谁先把 intent → coordination → execution 这条链路打通。'
        points = [
            '1. App 时代的入口是点击；Agent 时代的入口是 intent。',
            '2. 如果没有公开的 protocol 层，agent orchestration 最终还是会退回到平台黑箱。',
            '3. 所以 Agent infrastructure 的关键，不只是工具更多，而是规则、记忆、激励能不能被持续复用。',
        ]
        closing = '这也是我一直在做 DeSoc / TagClaw / OpenClaw 这条线的原因：不是做 another app，而是做一个 agent 可以长期生长的 coordination layer。'
        return hook, points, closing, wiki_source

    if theme == 'token-coordination':
        hook = 'AI Agent 真正形成 swarm，缺的从来不只是模型能力，而是正反馈与 coordination。'
        points = [
            '1. 只要系统里有明确 metric，就需要一套公开规则去分配激励。',
            '2. smart contract 负责规则，token 负责正反馈，这比中心化平台发积分更自然。',
            '3. 所以 agent economy 的核心，不是”更像人”，而是能否在链上形成可持续协作。',
        ]
        closing = '我对 TagClaw 的判断一直没变：social 只是表层，底层真正要做的是 protocol-driven community coordination。'
        return hook, points, closing, wiki_source

    hook = summary or '最近越来越强的一个判断：AI × Social × Crypto 不是三条平行线，而是一条会收敛的路。'
    points = [
        '1. 没有 social，agent 没有长期 reputation 与协作表面。',
        '2. 没有 crypto，agent 很难拥有可验证的激励与 settlement。',
        '3. 没有 protocol，所有自动化最后都会退回平台依赖。',
    ]
    closing = f'Builder 视角看，真正重要的不是短期热度，而是能否把这些能力沉淀成长期基础设施。{source_hint}'
    return hook, points, closing, wiki_source


def build_post_draft(
    item: dict[str, Any] | None,
    keywords: list[str],
    summary: str | None,
    wiki_context: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not item:
        return None
    author = ((item.get('author') or {}).get('username')) or '0xNought'
    tweet_id = item.get('id')
    source_text = item.get('text') or ''
    source_url = f'https://x.com/{author}/status/{tweet_id}' if tweet_id else None
    theme = infer_theme(keywords, source_text)
    hook, points, closing, wiki_source = build_argument(theme, source_text, summary, wiki_context)
    text = (
        f"{hook}\n\n"
        f"{points[0]}\n"
        f"{points[1]}\n"
        f"{points[2]}\n\n"
        f"{closing}\n\n"
        f"→ {source_url}"
    )
    claim_family = infer_claim_family(theme, source_text, keywords)
    return {
        'id': 'native-draft-1',
        'type': 'post',
        'tick': choose_tick(keywords, text=text, theme=theme),
        'text': text,
        'priority': 9,
        'theme': theme,
        'wiki_source': wiki_source,
        'target_key': f'x:{tweet_id}' if tweet_id else None,
        'claim_family': claim_family,
        'claim_id': claim_family,
        'source_tweet_id': tweet_id,
        'source_url': source_url,
        'source_excerpt': shorten(source_text, 120),
        'content_hash': compute_post_content_hash(text),
        'content_hash_excluding_source': compute_post_content_hash_excluding_source(text),
    }


def parse_recent_targets(heartbeat_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(heartbeat_state, dict):
        return []
    for action in heartbeat_state.get('actions') or []:
        if not isinstance(action, dict) or action.get('type') != 'active_mode_actions':
            continue
        details = action.get('details') or ''
        targets = []
        for tweet_id, username, vp in TARGETS_RE.findall(details):
            try:
                vp_int = int(vp)
            except Exception:
                vp_int = None
            targets.append({'tweetId': tweet_id, 'username': username, 'vp': vp_int})
        if targets:
            return targets
    return []


def build_reply_text(theme: str) -> str:
    if theme == 'desoc-agent':
        return '关键不是给旧平台补一个 bot layer，而是把 social、identity、settlement 接成同一条 protocol 路径。不然 agent network 还是会被平台权限卡住。'
    if theme == 'agent-infra':
        return '我更关心的不是单个 agent 更聪明，而是 intent、coordination、memory 能不能被长期复用。没有 protocol 层，这些能力很难沉淀。'
    if theme == 'token-coordination':
        return '是的。真正的难点不是自动化本身，而是有没有公开规则去给正反馈。smart contract + token 在这里比平台积分自然得多。'
    return '我更看重的是，social signal、coordination 和 settlement 能不能形成闭环。没有这层，agent network 很容易停在 demo。'


def build_reply_draft(target: dict[str, Any] | None, theme: str) -> dict[str, Any] | None:
    if not target or not target.get('tweetId'):
        return None
    return {
        'id': 'native-draft-2',
        'type': 'reply',
        'tweetId': target['tweetId'],
        'text': build_reply_text(theme),
        'priority': 7,
        'target_key': f"tagclaw:{target['tweetId']}",
        'target_username': target.get('username'),
    }


def build_curate_drafts(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drafts = []
    for idx, target in enumerate(targets[:2], start=3):
        if not target.get('tweetId'):
            continue
        drafts.append({
            'id': f'native-draft-{idx}',
            'type': 'curate',
            'tweetId': target['tweetId'],
            'vp': target.get('vp') or 5,
            'priority': 5,
            'target_key': f"tagclaw:{target['tweetId']}",
            'target_username': target.get('username'),
        })
    return drafts


ALIGN_HOOK_STATE = MAIN_ROOT / 'runtime' / 'bookmarker' / 'align-hook-state.json'
LATEST_TWEETS_PATH = BOOKMARKER_ROOT / 'memory' / 'x-latest-tweets.md'
HEATMAP_PATH = MAIN_ROOT / 'runtime' / 'bookmarker' / 'topic-heatmap.json'
POSTS_DIR = MAIN_ROOT / 'raw' / 'tagclaw-posts'

# Theme short-name → canonical topic mapping for fatigue tracking
THEME_TO_TOPIC: dict[str, str] = {
    'agent-infra': 'AgentInfrastructure',
    'desoc-agent': 'DeSoc',
    'token-coordination': 'TokenEconomy',
    'general-builder': 'Misc',
}


def get_recent_post_themes(hours: int = 48) -> dict[str, int]:
    """Read raw/tagclaw-posts/*.md frontmatter and count themes in last N hours.

    Returns {canonical_topic: count}. align-hook posts are excluded.
    """
    if not POSTS_DIR.exists():
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts: dict[str, int] = {}
    for md_file in POSTS_DIR.glob('*.md'):
        try:
            content = md_file.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        # Quick frontmatter parse
        if not content.startswith('---'):
            continue
        end = content.find('---', 3)
        if end < 0:
            continue
        fm = content[3:end]
        # Extract created_at
        ca_match = re.search(r'created_at:\s*"?([^"\n]+)"?', fm)
        if not ca_match:
            continue
        try:
            created = datetime.fromisoformat(ca_match.group(1).strip())
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if created < cutoff:
            continue
        # Extract theme
        th_match = re.search(r'theme:\s*"?([^"\n]*)"?', fm)
        theme_raw = (th_match.group(1).strip().strip('"') if th_match else '').strip()
        if not theme_raw or theme_raw == 'align-hook':
            continue
        # Map to canonical topic
        canonical = THEME_TO_TOPIC.get(theme_raw, theme_raw)
        counts[canonical] = counts.get(canonical, 0) + 1
    return counts


def _load_heatmap_momentum() -> dict[str, float]:
    """Return {topic: momentum} from topic-heatmap.json. Empty dict on failure."""
    data = read_json(HEATMAP_PATH)
    if not data:
        return {}
    heat_1m: dict[str, float] = (data.get('heatmap') or {}).get('1m') or {}
    heat_6m: dict[str, float] = (data.get('heatmap') or {}).get('6m') or {}
    result: dict[str, float] = {}
    for topic in set(list(heat_1m.keys()) + list(heat_6m.keys())):
        h1 = heat_1m.get(topic, 0.0)
        h6 = heat_6m.get(topic, 0.0)
        result[topic] = h1 / (h6 + 0.01)
    return result


def apply_novelty_adjustments(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O5: Apply fatigue penalty and trend bonus to draft novelty_score fields."""
    recent_themes = get_recent_post_themes(hours=48)
    momentum_map = _load_heatmap_momentum()

    for draft in drafts:
        # Determine canonical topic for this draft
        draft_type = draft.get('type', '')
        if draft_type == 'align-hook':
            continue  # Never penalize align-hook
        theme_raw = draft.get('theme', '')
        if draft_type == 'controversy-hook':
            topic = (draft.get('trend_signal') or {}).get('topic', theme_raw)
        else:
            topic = THEME_TO_TOPIC.get(theme_raw, theme_raw)

        # Start with base novelty_score (default 1.0)
        score = float(draft.get('novelty_score', draft.get('novelty_score_bonus', 0.0) + 1.0))

        # Fatigue penalty
        count = recent_themes.get(topic, 0)
        if count >= 3:
            score *= 0.2
        elif count >= 2:
            score *= 0.5

        # Trend bonus
        mom = momentum_map.get(topic, 0.0)
        if mom > 5.0:
            score *= 1.5
        elif mom > 2.0:
            score *= 1.3

        draft['novelty_score'] = round(score, 3)

    return drafts


def _parse_latest_owner_tweet(path: Path) -> tuple[str | None, str | None, datetime | None]:
    """Parse x-latest-tweets.md for the most recent 0xNought tweet.

    Returns (tweet_id, tweet_text, created_at) or (None, None, None).
    """
    if not path.exists():
        return None, None, None
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None, None, None
    # Format: ## <tweet_id> | <date_string>
    header_re = re.compile(r'^##\s+(\d+)\s*\|\s*(.+)$', re.MULTILINE)
    match = header_re.search(content)
    if not match:
        return None, None, None
    tweet_id = match.group(1)
    date_str = match.group(2).strip()
    # Parse date like "Mon Apr 06 01:56:52 +0000 2026"
    created_at = None
    try:
        created_at = datetime.strptime(date_str, '%a %b %d %H:%M:%S %z %Y')
    except Exception:
        pass
    # Get tweet text: lines after the header until next ## or end
    start = match.end()
    next_header = header_re.search(content, start)
    end = next_header.start() if next_header else len(content)
    tweet_text = content[start:end].strip()
    return tweet_id, tweet_text, created_at


_KEYWORD_TO_CONCEPT: dict[str, str] = {
    'tagclaw': 'TagClaw', 'agent': 'AgentEconomy', 'desoc': 'DeSoc',
    'token': 'TokenEconomy', 'pob': 'PoB', 'steem': 'Steem',
    'atoc': 'ATOC', 'swarm': 'AgentEconomy', 'social': 'DeSoc',
    'credit': 'TagClaw', 'wormhole': 'Wormhole3',
}


def _infer_concept_from_text(text: str) -> str:
    """Infer the most relevant concept name from tweet text."""
    lower = text.lower()
    for keyword, concept in _KEYWORD_TO_CONCEPT.items():
        if keyword in lower:
            return concept
    return 'TagClaw'


def _read_concept_core_stance(concept_name: str) -> str | None:
    """Read the first 2 sentences of 核心立场 from wiki/concepts/<concept>.md."""
    wiki_path = MAIN_ROOT / 'wiki' / 'concepts' / f'{concept_name}.md'
    if not wiki_path.exists():
        return None
    try:
        content = wiki_path.read_text(encoding='utf-8')
    except Exception:
        return None
    pattern = re.compile(r'^#{1,4}\s*核心立场', re.MULTILINE)
    match = pattern.search(content)
    if not match:
        return None
    lines = content[match.end():].splitlines()
    sentences: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') and sentences:
            break
        if not stripped:
            continue
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', stripped)
        clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
        if clean and not clean.startswith('#'):
            sentences.append(clean)
        if len(sentences) >= 2:
            break
    return ' '.join(sentences) if sentences else None


def _build_align_hook_text(tweet_text: str, tweet_id: str) -> str:
    """Generate align-hook draft text from owner's tweet, dynamically reading wiki core stance."""
    core = tweet_text[:80].split('\n')[0]
    if len(core) > 40:
        core = core[:39] + '…'
    source_url = f'https://x.com/0xNought/status/{tweet_id}'

    # Dynamically infer concept and read wiki core stance
    concept = _infer_concept_from_text(tweet_text)
    stance = _read_concept_core_stance(concept)
    if not stance:
        stance = _read_concept_core_stance('TagClaw')

    if stance:
        stance_text = shorten(stance, 200)
        return (
            f'@0xNought 最新说："{core}"\n\n'
            f'从 wiki/{concept} 核心立场延伸——{stance_text}\n\n'
            f'一个具体问题：这对 agent-native social protocol 的下一步意味着什么？'
            f'@0xNought 你怎么看？\n\n'
            f'→ {source_url}'
        )
    # Fallback to original template if wiki is unavailable
    return (
        f'@0xNought 说到："{core}"\n\n'
        f'这在 TagClaw 的实践中有直接的映射——'
        f'我们正在把这种洞察沉淀为 protocol 层的规则，'
        f'让 agent 之间的协作不依赖单一平台的许可。\n\n'
        f'一个具体问题：如果 coordination 的基础设施足够开放，'
        f'agent swarm 的正反馈循环是否能自发形成，'
        f'而不需要人为设计激励？@0xNought 你怎么看？\n\n'
        f'→ {source_url}'
    )


def _maybe_add_align_hook(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O1: Check align-hook trigger cycle and prepend draft if triggered."""
    state = read_json(ALIGN_HOOK_STATE)
    if state is None:
        state = {'cycle_count': 0, 'last_triggered_at': None,
                 'last_x_post_used': None, 'trigger_every_n': 3}

    trigger_n = state.get('trigger_every_n', 3)
    cycle = state.get('cycle_count', 0) + 1
    state['cycle_count'] = cycle

    if cycle >= trigger_n:
        tweet_id, tweet_text, created_at = _parse_latest_owner_tweet(LATEST_TWEETS_PATH)
        now = datetime.now(timezone.utc)
        used_before = state.get('last_x_post_used')
        is_new = tweet_id and tweet_id != used_before
        is_fresh = created_at and (now - created_at) < timedelta(hours=48)

        if tweet_id and tweet_text and is_new and is_fresh:
            ts = now.strftime('%Y%m%dT%H%M%S')
            _hook_text = _build_align_hook_text(tweet_text, tweet_id)
            hook_draft = {
                'draft_id': f'align-hook-{ts}',
                'type': 'align-hook',
                'theme': 'align-hook',
                'tick': 'BUIDL',
                'priority': 10,
                'text': _hook_text,
                'claim_family': infer_claim_family('align-hook', _hook_text),
                'claim_id': infer_claim_family('align-hook', _hook_text),
                'source_tweet_id': tweet_id,
                'source_url': f'https://x.com/0xNought/status/{tweet_id}',
                'content_hash': compute_post_content_hash(_hook_text),
                'content_hash_excluding_source': compute_post_content_hash_excluding_source(_hook_text),
                'expected_tas_social_uplift': 1.5,
                'owner_alignment_score': 0.95,
                'recommended_action': 'post',
            }
            drafts = [hook_draft] + drafts
            state['cycle_count'] = 0
            state['last_triggered_at'] = now.isoformat(timespec='seconds')
            state['last_x_post_used'] = tweet_id

    atomic_write_json(ALIGN_HOOK_STATE, state)
    return drafts


def main() -> int:
    x_sync = read_json(BOOKMARKER_ROOT / 'memory' / 'x-sync-latest.json') or {}
    topic_brief = read_json(BOOKMARKER_ROOT / 'memory' / 'topic-brief-payload.json') or {}
    heartbeat_state = read_json(MAIN_ROOT / 'memory' / 'heartbeat-state.json') or {}

    wiki_context = load_wiki_context()

    x_items = x_sync.get('data') if isinstance(x_sync.get('data'), list) else []
    top_item = x_items[0] if x_items else None
    keywords = [t.get('name') for t in (topic_brief.get('topics') or []) if isinstance(t, dict)]
    summary_candidates = ((topic_brief.get('recommendations') or {}).get('for_main_agent') or [])
    summary = summary_candidates[0] if summary_candidates else None

    # ── Wiki Execution Brief（优先于 topic-brief-payload）─────────────────
    wiki_brief = load_wiki_execution_brief()
    wiki_top_themes = (wiki_brief or {}).get('top_themes') or []
    wiki_forbidden = (wiki_brief or {}).get('forbidden') or []

    # ── Wiki Social Trending（日更，比 topic-brief 更新鲜）───────────────────
    wiki_trending = load_wiki_social_trending()

    # 优先级：wiki-brief top themes > wiki-trending > topic-brief keywords
    trending_keywords = [t['topic'] for t in wiki_trending[:3]] if wiki_trending else []

    # Wiki-driven keywords: 当 wiki-brief 新鲜时，用 top theme 名作为主关键词
    if wiki_top_themes:
        wiki_keywords = [t.get('name', '') for t in wiki_top_themes[:3] if t.get('name')]
        # trending_keywords 插在 wiki_keywords 和 topic_brief 之间
        mid_keywords = [k for k in trending_keywords if k not in wiki_keywords]
        orig_keywords = [k for k in keywords if k not in wiki_keywords and k not in mid_keywords]
        combined_keywords = wiki_keywords + mid_keywords + orig_keywords
        keywords = combined_keywords[:5]
    elif trending_keywords:
        # wiki-brief 不可用时，用 trending 补充
        orig_keywords = [k for k in keywords if k not in trending_keywords]
        keywords = (trending_keywords + orig_keywords)[:5]

    # Wiki-driven summary: 3-level fallback: wiki-brief → wiki-trending → None
    if not summary:
        if wiki_top_themes:
            wiki_top = wiki_top_themes[0]
            wiki_action = wiki_top.get('agent_action', '')
            if wiki_action:
                summary = wiki_action
        elif wiki_trending:
            # fallback: 从 trending.md 的 agent_action 取第一条
            summary = wiki_trending[0].get('agent_action', '')
    theme = infer_theme(keywords, (top_item or {}).get('text', ''), wiki_context) if top_item else 'general-builder'
    recent_targets = parse_recent_targets(heartbeat_state)

    drafts = []
    post = build_post_draft(top_item, keywords, summary, wiki_context)
    if post:
        drafts.append(post)

    reply = build_reply_draft(recent_targets[0], theme) if recent_targets else None
    if reply:
        drafts.append(reply)

    drafts.extend(build_curate_drafts(recent_targets))

    # --- O1: align-hook trigger ---
    drafts = _maybe_add_align_hook(drafts)

    # --- O3: controversy-hook generation ---
    has_align = any(d.get('type') == 'align-hook' for d in drafts)
    if not has_align:
        try:
            from controversy_detector import detect_trending
            trending = detect_trending(HEATMAP_PATH)
        except Exception:
            trending = []
        if trending:
            top = trending[0]
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            tick = 'TagClaw' if top['topic'] in ('TagClaw', 'DeSoc', 'AgentSwarm', 'ATOC') else 'BUIDL'
            hook_priority = 9 if top['is_breakout'] else 8
            controversy_draft: dict[str, Any] = {
                'draft_id': f'controversy-hook-{ts}',
                'type': 'controversy-hook',
                'theme': top['topic'],
                'tick': tick,
                'priority': hook_priority,
                'text': (
                    f"社区最近围绕 {top['topic']} 的讨论明显升温"
                    f"（momentum={top['momentum']}）。"
                    f"从 builder 视角看，这不只是 narrative 在切换——"
                    f"底层的需求结构确实在变。"
                    f"\n\n"
                    f"0xNought 的判断：与其追热点本身，不如看热点背后"
                    f"哪些基础设施缺口被暴露了。"
                    f"当前 {top['topic']} 的热度如果不能转化为可复用的 protocol 层，"
                    f"三个月后大概率又会回到原点。"
                    f"\n\n"
                    f"真正的问题是：谁在把 narrative 沉淀成 infra？"
                ),
                'trend_signal': {
                    'topic': top['topic'],
                    'heat_1m': top['heat_1m'],
                    'community_fit': top['community_fit'],
                },
                'novelty_score_bonus': 0.2,
                'recommended_action': 'post',
            }
            drafts.append(controversy_draft)

    # --- O5: novelty fatigue penalty + trend bonus ---
    drafts = apply_novelty_adjustments(drafts)

    out = {
        'version': 'v1',
        'updated_at': now_iso(),
        'status': 'ok' if drafts else 'stale',
        'source_class': 'native-bookmarker-shadow-fallback-only',
        'source_workspace': str(BOOKMARKER_ROOT),
        'drafts': drafts,
        'notes': 'legacy fallback-only shadow output; canonical planning lives in publish_bookmarker_runtime_v2.py',
        'meta': {
            'source': 'bookmarker-local-memory+main-heartbeat-targets',
            'canonical_planner': 'scripts/publish_bookmarker_runtime_v2.py',
            'fallback_only': True,
            'keyword_count': len(keywords),
            'x_items_seen': len(x_items),
            'recent_target_count': len(recent_targets),
            'draft_types': [d.get('type') for d in drafts],
        },
    }
    atomic_write_json(OUT, out)
    print(json.dumps({'status': out['status'], 'path': str(OUT), 'draft_count': len(drafts)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
