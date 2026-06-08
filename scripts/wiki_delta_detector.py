#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fallback_items import load_x_sync_with_fallback
from publication_memory import infer_claim_family
from agency_paths import BOOKMARKER_WS, MAIN_WS

BOOKMARKER_ROOT = (BOOKMARKER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'bookmarker'
WIKI_EXECUTION_BRIEF = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-execution-brief.json'


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
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def normalize_theme_name(name: str | None) -> str:
    raw = str(name or '').strip()
    if not raw:
        return 'general-builder'
    lowered = raw.lower()
    mapping = {
        'agent-infrastructure': 'agent-infra',
        'agent infrastructure': 'agent-infra',
        'projects': 'general-builder',
        'project': 'general-builder',
        'misc': 'general-builder',
        'tokeneconomy': 'token-coordination',
        'token economy': 'token-coordination',
        'desoc': 'desoc-agent',
        'agenteconomy': 'agent-infra',
        'agent economy': 'agent-infra',
    }
    return mapping.get(lowered, raw)


def infer_theme_from_text(text: Any, source_type: str | None = None) -> str:
    joined = f"{source_type or ''} {text or ''}".lower()
    if any(term in joined for term in [
        'xchat', 'x money', 'cashtag', 'cashtags', 'stablecoin', 'settlement', 'token', 'incentive',
    ]):
        return 'token-coordination'
    if any(term in joined for term in [
        'ai agents', 'ai agent', 'social graph', 'desoc', '去中心化社交', 'agent-native social protocol',
        'platform', 'community', 'opc', 'self-ip',
    ]):
        return 'desoc-agent'
    if any(term in joined for term in [
        'openclaw', 'intent', 'orchestration', 'protocol layer', 'coordination layer', 'agentos',
    ]):
        return 'agent-infra'
    return 'general-builder'


def extract_supporting_points(text: str) -> list[str]:
    compact = [seg.strip(' -•') for seg in str(text or '').replace('\r', '\n').split('\n') if seg.strip()]
    points: list[str] = []
    for seg in compact:
        if len(seg) < 12:
            continue
        points.append(seg[:180])
        if len(points) >= 3:
            break
    return points


def build_current_theme_weights(topic_brief: dict[str, Any], wiki_brief: dict[str, Any], x_items: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, list[str]]]:
    weights: dict[str, float] = {}
    sources_by_theme: dict[str, list[str]] = {}

    for topic in topic_brief.get('topics') or []:
        if not isinstance(topic, dict):
            continue
        theme = normalize_theme_name(topic.get('name'))
        score = float(topic.get('relevance_score') or 0.0)
        if score > 0:
            weights[theme] = weights.get(theme, 0.0) + score

    for idx, item in enumerate((wiki_brief.get('top_themes') or [])[:8]):
        if not isinstance(item, dict):
            continue
        theme = normalize_theme_name(item.get('name'))
        heat = float(item.get('heat_score') or 0.0)
        weights[theme] = weights.get(theme, 0.0) + heat * 10.0
        if theme not in sources_by_theme:
            sources_by_theme[theme] = []
        sources_by_theme[theme].append(f'wiki:{theme}:{idx + 1}')

    for item in x_items:
        if not isinstance(item, dict):
            continue
        theme = infer_theme_from_text(item.get('text') or '', item.get('source_type'))
        src = f"x:{item.get('id')}" if item.get('id') else ''
        weights[theme] = weights.get(theme, 0.0) + 1.0
        if src:
            sources_by_theme.setdefault(theme, []).append(src)

    for key in list(sources_by_theme.keys()):
        sources_by_theme[key] = list(dict.fromkeys(sources_by_theme[key]))[:8]
    return weights, sources_by_theme


def build_claim_delta(item: dict[str, Any], recent_claim_families: set[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    source_id = str(item.get('id') or '').strip()
    text = str(item.get('text') or '').strip()
    if not source_id or not text:
        return None
    lowered_text = text.lower()
    if lowered_text.startswith('rt @'):
        return None
    if text.startswith('http://') or text.startswith('https://'):
        return None
    if text.count('http') >= 1 and len(text) < 60:
        return None
    if text.count('http') >= 1 and sum(ch.isalpha() for ch in text) < 45:
        return None
    if len(text) < 45:
        return None
    theme = infer_theme_from_text(text, item.get('source_type'))
    claim_family = infer_claim_family(theme, text)
    supporting_sources = [f'x:{source_id}']
    supporting_points = extract_supporting_points(text)
    lowered = text.lower()

    if claim_family == 'general-builder-observation' and len(supporting_points) < 2:
        return None
    if claim_family == 'desoc-agent-general' and len(supporting_points) < 2:
        return None
    if claim_family == 'desoc-agent-general' and not any(token in lowered for token in ['dao', 'socialfi', 'community', 'token', 'governance', 'agent', 'ai', 'protocol']):
        return None
    if claim_family in recent_claim_families and len(text) <= 80:
        return None

    delta_type = 'strengthened'
    if claim_family not in recent_claim_families:
        delta_type = 'new_theme' if theme not in {'general-builder'} and len(text) < 90 else 'strengthened'
    if sum(1 for token in ['xchat', 'x money', 'cashtag', 'cashtags', 'social', 'crypto', 'ai'] if token in lowered) >= 3:
        delta_type = 'new_connection'
    if claim_family in recent_claim_families and any(token in lowered for token in ['launched', '推出', '公测', '上线', 'supports', 'supports joinable links']):
        delta_type = 'validated'

    novelty_score = 0.88 if claim_family not in recent_claim_families else 0.45
    if delta_type == 'new_connection':
        novelty_score = max(novelty_score, 0.9)
    evidence_density = min(1.0, max(0.3, len(supporting_points) / 3.0 + (0.15 if len(text) > 140 else 0.0)))

    return {
        'claim_id': claim_family,
        'claim_family': claim_family,
        'theme': theme,
        'delta_type': delta_type,
        'novelty_score': round(novelty_score, 4),
        'evidence_density': round(evidence_density, 4),
        'supporting_sources': supporting_sources,
        'wiki_nodes': [theme],
        'supporting_points': supporting_points,
        'source_excerpt': text[:240],
    }


def generate_wiki_delta(
    bookmaker_root: Path = BOOKMARKER_ROOT,
    main_root: Path = MAIN_ROOT,
    runtime: Path = RUNTIME,
    publication_memory: dict[str, Any] | None = None,
    *,
    topic_brief_doc: dict[str, Any] | None = None,
    topic_extraction_doc: dict[str, Any] | None = None,
    x_sync_doc: dict[str, Any] | None = None,
    wiki_brief_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    topic_brief = topic_brief_doc or read_json(bookmaker_root / 'memory' / 'topic-brief-payload.json') or {}
    topic_extraction = topic_extraction_doc or read_json(bookmaker_root / 'memory' / 'topic-extraction-latest.json') or {}
    if x_sync_doc is not None:
        x_sync = x_sync_doc
    else:
        x_sync, _, _ = load_x_sync_with_fallback(bookmaker_root / 'memory')
    wiki_brief = wiki_brief_doc or read_json(WIKI_EXECUTION_BRIEF) or {}
    publication_memory = publication_memory or read_json(runtime / 'publication-memory.json') or {}

    last_publish_at = None
    recent_claims = publication_memory.get('recent_claims') or []
    for item in recent_claims:
        if not isinstance(item, dict):
            continue
        dt = parse_dt(item.get('published_at'))
        if dt and (last_publish_at is None or dt > last_publish_at):
            last_publish_at = dt
    if last_publish_at is None:
        last_publish_at = datetime.now(timezone.utc) - timedelta(hours=48)

    x_items = [item for item in (x_sync.get('data') or []) if isinstance(item, dict)]
    theme_weights, sources_by_theme = build_current_theme_weights(topic_brief, wiki_brief, x_items)
    prev_theme_weights = ((publication_memory.get('last_wiki_snapshot') or {}).get('theme_weights') or {}) if isinstance(publication_memory, dict) else {}
    recent_claim_families = {
        str(item.get('claim_family') or '').strip()
        for item in recent_claims if isinstance(item, dict) and str(item.get('claim_family') or '').strip()
    }

    theme_deltas: list[dict[str, Any]] = []
    for theme, weight in sorted(theme_weights.items(), key=lambda kv: kv[1], reverse=True):
        prev_weight = float(prev_theme_weights.get(theme) or 0.0)
        new_sources = list(dict.fromkeys(sources_by_theme.get(theme, [])))
        if prev_weight <= 0 and weight > 0:
            delta_type = 'new_theme'
        elif weight >= prev_weight + 2.0 or len(new_sources) >= 2:
            delta_type = 'strengthened'
        else:
            continue
        theme_deltas.append({
            'theme': theme,
            'delta_type': delta_type,
            'strength_score': round(min(1.0, weight / 10.0), 4),
            'evidence_count': len(new_sources),
            'new_sources': new_sources[:8],
            'why_it_changed': f'{theme} gained fresh evidence from wiki/topic/X signals',
        })

    claim_deltas: list[dict[str, Any]] = []
    suppressed_noise: list[dict[str, Any]] = []
    for item in x_items[:12]:
        source_id = str(item.get('id') or '').strip()
        source_ref = f'x:{source_id}' if source_id else ''
        text = str(item.get('text') or '').strip()
        claim_delta = build_claim_delta(item, recent_claim_families)
        if claim_delta is None:
            if source_ref:
                suppressed_noise.append({'source': source_ref, 'reason': 'title-only duplicate anchor without new claim'})
            continue
        claim_family = claim_delta.get('claim_family')
        if claim_family in {c.get('claim_family') for c in claim_deltas}:
            if source_ref:
                suppressed_noise.append({'source': source_ref, 'reason': 'duplicate claim family in same cycle'})
            continue
        if len(text) <= 40 and claim_family in recent_claim_families:
            suppressed_noise.append({'source': source_ref, 'reason': 'short source reiterates already published claim family'})
            continue
        claim_deltas.append(claim_delta)

    snapshot = {
        'theme_weights': {k: round(v, 4) for k, v in sorted(theme_weights.items(), key=lambda kv: kv[1], reverse=True)[:10]},
        'top_claim_ids': [str(item.get('claim_id')) for item in claim_deltas[:5] if item.get('claim_id')],
    }
    payload = {
        'version': 'v1',
        'generated_at': now_iso(),
        'window': {
            'since_last_publish_at': last_publish_at.astimezone().isoformat(timespec='seconds'),
            'topic_timestamp': topic_brief.get('timestamp') or topic_extraction.get('timestamp'),
            'x_sync_timestamp': x_sync.get('fetched_at'),
        },
        'theme_deltas': theme_deltas,
        'claim_deltas': claim_deltas,
        'suppressed_noise': suppressed_noise,
        'snapshot': snapshot,
        'meta': {
            'x_items_seen': len(x_items),
            'recent_claim_family_count': len(recent_claim_families),
            'source': 'bookmarker-phase1',
        },
    }
    return payload


def main() -> int:
    payload = generate_wiki_delta()
    atomic_write_json(RUNTIME / 'wiki-delta.json', payload)
    print(json.dumps({'status': 'ok', 'path': str(RUNTIME / 'wiki-delta.json'), 'theme_deltas': len(payload.get('theme_deltas') or []), 'claim_deltas': len(payload.get('claim_deltas') or [])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
