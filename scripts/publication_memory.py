from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCE_ANCHOR_LINE_RE = re.compile(
    r'^(?:→\s*https?://\S+|source\s*:\s*https?://\S+|anchor\s*:\s*https?://\S+)$',
    re.IGNORECASE,
)


def normalize_post_text(text: Any, *, strip_source_anchors: bool = False) -> str:
    raw = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [' '.join(line.split()) for line in raw.split('\n')]
    cleaned: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if strip_source_anchors and SOURCE_ANCHOR_LINE_RE.match(line):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


def compute_post_content_hash(text: Any) -> str:
    normalized = normalize_post_text(text)
    if not normalized:
        return ''
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]


def compute_post_content_hash_excluding_source(text: Any) -> str:
    normalized = normalize_post_text(text, strip_source_anchors=True)
    if not normalized:
        return ''
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:16]


def infer_claim_family(theme: str | None, text: Any, keywords: list[str] | None = None) -> str:
    joined = ' '.join([(theme or ''), *(keywords or []), str(text or '')]).lower()

    if (
        any(token in joined for token in ['xchat', 'x money', 'cashtag', 'cashtags', 'custom timeline', 'custom timelines'])
        and any(token in joined for token in ['product changes', 'supports joinable links', 'joinable links', 'public link', 'everything app', 'super app', 'super-app'])
        and not any(token in joined for token in ['terrible idea', 'don\'t do this', 'deleting all x communities', 'not a good idea'])
    ):
        return 'x-superapp-open-graph'

    if 'communit' in joined and any(token in joined for token in [
        'shutting down', 'remove communities', 'removing communities', 'deleting all x communities',
        'declining usage', 'groupchat links', 'group chat links', 'xchat instead', 'delete communities',
    ]):
        return 'agent-protocol-vs-platform'

    if any(token in joined for token in [
        'xchat', 'x money', 'cashtag', 'custom timeline', 'custom timelines',
        'everything app', 'super app', 'super-app', 'open social graph', 'llm wiki',
        'agent memory', 'social graph + execution',
    ]):
        return 'x-superapp-open-graph'

    if any(token in joined for token in [
        'agent-native social protocol', 'bot tooling', 'ai agents', 'ai agent',
        'platform risk', 'platform dependency', 'cleanup ai agents', '清理 ai agents',
        '不能建立在中心化平台', '中心化平台', 'reddit', 'protocol-native social',
        'identity and settlement', 'settlement layers',
    ]):
        return 'agent-protocol-vs-platform'

    if any(token in joined for token in [
        'dao', 'socialfi', 'community dao', 'web3 communities', 'community ownership',
        'stakingdao', 'staking contributors', 'delegation', 'bootstrap method', 'iso',
        'incentivize', 'reward', 'rewards', 'governance token', 'web3.0',
    ]) and any(token in joined for token in [
        'token', 'reward', 'staking', 'community', 'social', 'dao', 'coordination', 'governance',
    ]):
        return 'tokenized-community-coordination'

    if any(token in joined for token in [
        'community coordination', 'opc', 'one person company', 'one-person company',
        'meme', 'self-ip', 'ipshare', 'atoc', '社区', '社群',
    ]) and any(token in joined for token in [
        'crypto', 'token', 'agent', 'ai', 'social', 'coordination',
    ]):
        return 'community-as-ai-crypto-intersection'

    normalized_theme = re.sub(r'[^a-z0-9]+', '-', (theme or 'general-builder').strip().lower()).strip('-') or 'general-builder'
    if normalized_theme == 'agent-infra':
        return 'intent-coordination-execution'
    if normalized_theme == 'token-coordination':
        return 'tokenized-community-coordination'
    if normalized_theme == 'desoc-agent':
        return 'desoc-agent-general'
    return f'{normalized_theme}-observation'


def _parse_dt(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_publication_memory(
    social_history_path: Path,
    *,
    recent_hours: float = 48,
    existing: dict[str, Any] | None = None,
    theme_weights: dict[str, float] | None = None,
    top_claim_ids: list[str] | None = None,
) -> dict[str, Any]:
    social_history = {}
    if social_history_path.exists():
        try:
            social_history = json.loads(social_history_path.read_text(encoding='utf-8'))
        except Exception:
            social_history = {}

    items = social_history.get('items') if isinstance(social_history.get('items'), list) else []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)

    recent_claims: list[dict[str, Any]] = []
    recent_anchors: list[str] = []
    recent_theme_counts: dict[str, int] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        executed_at = _parse_dt(str(item.get('executed_at') or ''))
        if executed_at and executed_at.astimezone(timezone.utc) < cutoff:
            continue
        if item.get('result_status') != 'ok' or item.get('type') not in {'post', 'reply', 'quote'}:
            continue
        request_obj = item.get('request') if isinstance(item.get('request'), dict) else {}
        text_body = (
            item.get('text_body_normalized')
            or request_obj.get('text_body_normalized')
            or item.get('text_body')
            or request_obj.get('text_body')
            or ''
        )
        theme = str(item.get('theme') or request_obj.get('theme') or '').strip()
        claim_family = str(item.get('claim_family') or request_obj.get('claim_family') or '').strip()
        if not claim_family:
            claim_family = infer_claim_family(theme, text_body)
        content_hash = str(item.get('content_hash') or request_obj.get('content_hash') or '').strip() or compute_post_content_hash(text_body)
        content_hash_excluding_source = (
            str(item.get('content_hash_excluding_source') or request_obj.get('content_hash_excluding_source') or '').strip()
            or compute_post_content_hash_excluding_source(text_body)
        )
        anchor_source = str(item.get('source_url') or request_obj.get('source_url') or '').strip()
        if not anchor_source:
            source_tweet_id = str(item.get('source_tweet_id') or request_obj.get('source_tweet_id') or '').strip()
            if source_tweet_id:
                anchor_source = f'x:{source_tweet_id}'
        if anchor_source:
            recent_anchors.append(anchor_source)
        if theme:
            recent_theme_counts[theme] = recent_theme_counts.get(theme, 0) + 1
        opening_signature = normalize_post_text(text_body).split('\n')[0][:120] if text_body else ''
        recent_claims.append({
            'claim_id': str(item.get('claim_id') or request_obj.get('claim_id') or '').strip() or claim_family,
            'claim_family': claim_family,
            'published_at': item.get('executed_at') or request_obj.get('executed_at'),
            'anchor_source': anchor_source,
            'content_hash': content_hash,
            'content_hash_excluding_source': content_hash_excluding_source,
            'opening_signature': opening_signature,
            'theme': theme,
        })

    memory = dict(existing or {})
    memory.update({
        'version': 'v1',
        'updated_at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
        'recent_claims': recent_claims[:100],
        'recent_anchors': list(dict.fromkeys(recent_anchors))[:100],
        'recent_theme_counts': recent_theme_counts,
        'last_wiki_snapshot': {
            'theme_weights': theme_weights or {},
            'top_claim_ids': top_claim_ids or [],
        },
    })
    return memory
