#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fallback_items import load_x_sync_with_fallback
from publication_memory import infer_claim_family
from agency_paths import BOOKMARKER_WS, MAIN_WS

PHRASE_BLACKLIST = [
    'my read:',
    'the value here is',
    'this suggests',
    'this highlights',
    'what this means is',
    'this extends the broader line of thought',
]

ANNOUNCEMENT_PREFIXES = [
    "today we're launching",
    'today we are launching',
    'introducing ',
    'ladies and gentlemen, today',
    'this feature allows',
    'today we\'re announcing',
]

BOOKMARKER_ROOT = (BOOKMARKER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'bookmarker'


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


def source_map_from_x_sync(x_sync: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in x_sync.get('data') or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get('id') or '').strip()
        if source_id:
            result[f'x:{source_id}'] = item
    return result


def infer_theme_from_theme_pack(theme_names: list[str]) -> str:
    joined = ' '.join(theme_names).lower()
    if any(token in joined for token in ['agent', 'atoc', 'desoc']):
        return 'agent-infra'
    if any(token in joined for token in ['token', 'economy', 'payments']):
        return 'token-coordination'
    return 'general-builder'


def clean_source_excerpt(text: str) -> str:
    lines = []
    for raw in str(text or '').replace('\r', '\n').split('\n'):
        cleaned = clean_supporting_text(raw)
        if cleaned:
            lines.append(cleaned)
    return ' '.join(lines)[:260].strip()


def infer_claim_family_override(family: str, source_text: str) -> str:
    lowered = (source_text or '').lower()
    if family in {'general-builder-observation', 'general-builder-general', 'general-builder'}:
        if any(token in lowered for token in ['monetization', 'remove my monetization', 'payouts went from', 'targeted me', 'x payouts', '$8,000 every 2 weeks', '$1,000']):
            return 'agent-protocol-vs-platform'
    return family


def build_claim_text(theme: str, family: str, source_text: str, delta_type: str) -> str:
    lowered = source_text.lower()
    if family == 'x-superapp-open-graph':
        if any(token in lowered for token in ['custom timeline', 'custom timelines', 'home tab', 'pin a specific topic']):
            return 'Most builders are still reading X as a content app when it is becoming a programmable routing layer for attention and coordination.'
        if any(token in lowered for token in ['communities', 'community']) and any(token in lowered for token in ['xchat', 'joinable links', 'groupchat', 'group chat', 'deprecat', 'remove', 'delete', 'declining usage']):
            return 'The important X shift is not a chat upgrade but the replacement of thick community surfaces with thinner coordination rails.'
        if any(token in lowered for token in ['x money', 'cashtag', 'cashtags', 'payment', 'wallet']):
            return 'Most people still read these X moves as feature expansion when they are really early signs of graph, payment, and execution converging.'
        return 'X is no longer just iterating on social features; the real repricing is happening at the coordination layer underneath them.'
    if family == 'agent-protocol-vs-platform':
        return 'The real bottleneck for agent networks is still platform dependency, not whether models can generate more output.'
    if family == 'community-as-ai-crypto-intersection':
        if any(token in lowered for token in ['中本聪', '一人公司', 'opc', '100+ 点对点电子现金系统', '点对点电子现金系统', '电子现金']):
            return 'Most builders still romanticize solo agent capability when the harder compounding problem is coordination.'
        return 'The durable edge in AI × Crypto will be built around coordination, not just tooling.'
    if family == 'intent-coordination-execution':
        if any(token in lowered for token in ['sub-agent', 'sub-agents', 'latent space', 'multiplayer', 'llm-driven game', 'gradient bang', 'hacker news', 'cli usage is allowed', 'still blocked', 'orchestration', 'execution loop', 'system loop']):
            return 'Most people still read these systems as agent demos when the real leverage is in keeping intent, coordination, and execution inside one loop.'
        return 'Most builders still underrate orchestration quality when the real leverage is in keeping intent, coordination, and execution inside one loop.'
    if delta_type == 'validated':
        return f'This cycle is less about repeating the {theme} thesis than showing where it is starting to compound.'
    if theme == 'token-coordination' or any(token in lowered for token in ['token', 'cashtag', 'x money', 'settlement']):
        return 'The market still treats token and settlement as extras when they are becoming structural parts of the stack.'
    if theme == 'agent-infra':
        return 'The next layer of leverage is being mispriced at the coordination rail, not the app surface.'
    return f'The stronger signal this cycle is not volume in {theme} but where the stack is starting to reprice.'


def clean_supporting_text(text: str) -> str:
    cleaned = ' '.join(str(text or '').replace('\r', '\n').replace('\n', ' ').split()).strip()
    lowered = cleaned.lower()
    if not cleaned:
        return ''
    if cleaned.startswith('→ http') or cleaned.startswith('http://') or cleaned.startswith('https://'):
        return ''
    if any(phrase in lowered for phrase in PHRASE_BLACKLIST):
        return ''
    if len(cleaned) < 18:
        return ''
    return cleaned[:220]


def is_announcement_copy(text: str) -> bool:
    lowered = str(text or '').strip().lower()
    return any(lowered.startswith(prefix) for prefix in ANNOUNCEMENT_PREFIXES)


def classify_supporting_point(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ['xchat', 'x money', 'cashtag', 'cashtags', 'custom timelines', 'api', 'payment', 'wallet', 'sub-agent', 'sub-agents', 'multiplayer', 'llm-driven game', 'latent space', 'tooling support', 'execution support', 'cli usage', 'shared coordination space']):
        return 'product_signal'
    if any(token in lowered for token in ['therefore', 'means', 'implies', 'converging', 'coordination', 'operating path', 'settlement', 'protocol', 'infrastructure sovereignty', 'coordination rail', 'system loop', 'execution loop', 'orchestration', 'platform-controlled monetization', 'revocable control surfaces', 'dependency, not audience growth', 'platform pressure']):
        return 'structural_inference'
    return 'source_fact'


def infer_x_superapp_structural_inference(lowered: str) -> str:
    if any(token in lowered for token in ['communities', 'community']) and any(token in lowered for token in ['xchat', 'joinable links', 'groupchat', 'group chat', 'deprecat', 'remove', 'delete', 'declining usage']):
        return 'This is not just a chat feature rollout; it is a replacement of community surface with a thinner coordination rail controlled by the platform.'
    if any(token in lowered for token in ['custom timeline', 'custom timelines', 'pin a specific topic', 'home tab', '75 topics']):
        return 'This is not just a discovery feature; it turns the social graph into a more programmable routing surface for attention and coordination.'
    if any(token in lowered for token in ['x money', 'cashtag', 'cashtags', 'payment', 'wallet']):
        return 'This is not just product expansion; it is X wiring social graph directly into payment and execution rails.'
    if any(token in lowered for token in ['llm wiki', 'x api', 'agent memory', 'wikiwise', 'grok', 'cursor', 'open social graph']):
        return 'This is not just another app-layer tool; it pushes X toward a programmable distribution surface for agents and builders.'
    return 'The deeper shift is toward a graph + execution + settlement stack, not isolated feature launches.'


def distill_supporting_point(text: str, point_type: str) -> str:
    cleaned = clean_supporting_text(text)
    lowered = cleaned.lower()
    if not cleaned:
        return ''

    if point_type == 'source_fact':
        if any(token in lowered for token in ['today we\'re launching', 'today we are launching', 'introducing']) and any(token in lowered for token in ['custom timeline', 'custom timelines', 'home tab', 'pin a specific topic']):
            return 'X is shipping native controls for routing attention by topic inside the social graph.'
        if any(token in lowered for token in ['this feature allows', 'supports joinable links', 'public link']) and 'xchat' in lowered:
            return 'X is turning group formation into a native on-platform distribution surface through XChat links.'
        if '中本聪' in lowered and any(token in lowered for token in ['一人公司', 'opc']):
            return 'Even the strongest one-person-builder myth still runs into coordination limits.'
        if any(token in lowered for token in ['100+ 点对点电子现金系统', '点对点电子现金系统']) or ('100+' in lowered and '电子现金' in lowered):
            return 'Across many peer-to-peer cash experiments, fully rebuilding the stack alone rarely works.'
        if any(token in lowered for token in ['communities', 'community', 'groupchat links', 'group chat links', 'shutting down', 'declining usage']) and 'xchat' in lowered:
            return 'X is removing native community surfaces and redirecting users toward XChat links instead.'
        if any(token in lowered for token in ['x payouts', 'payouts went from', 'remove my monetization', 'targeted me']):
            return 'Platform-controlled monetization can still be reduced or removed by pressure from the surrounding platform environment.'
        if any(token in lowered for token in ['sub-agents in (latent) space', 'sub-agents', 'latent space']):
            return 'Sub-agents are starting to operate inside shared coordination environments rather than isolated runs.'
        if 'side project' in lowered:
            return 'Builders are starting to prototype tighter orchestration loops instead of isolated agent behaviors.'
        if 'hacker news' in lowered:
            return 'The coordination-loop thesis is starting to attract mainstream technical attention, not just niche agent interest.'
        if any(token in lowered for token in ['boris said', 'cli usage is allowed', 'still blocked', 'usage is allowed']):
            return 'Tooling support is no longer the main question; the harder problem is whether the system can stay coherent as execution scales.'
        if any(token in lowered for token in ['massively multiplayer', 'llm-driven game', 'gradient bang', 'multiplayer']):
            return 'LLM systems are beginning to hold multiplayer coordination inside one active loop.'
        if any(token in lowered for token in ['llm wiki', 'x api', '10000+', '10000', '小脑', 'agent 分身', 'agent memory']):
            return 'X is still one of the richest live graphs for training and refreshing agent memory.'
        if any(token in lowered for token in ['xchat', 'x money', 'cashtag', 'cashtags']):
            return 'X is expanding beyond pure media into a richer graph for interaction, execution, and data capture.'
        if any(token in cleaned for token in [':', '：', '1、', '2、', '3、', '1.', '2.', '3.', '>']):
            clauses = [seg.strip(' ，。；;:>') for seg in cleaned.replace('。', '，').replace(';', '，').split('，') if seg.strip()]
            short = '，'.join(clauses[:2]).strip()
            if short:
                return (short[:88] + '…') if len(short) > 88 else short
        if len(cleaned) > 120:
            clauses = [seg.strip(' ，。；;:') for seg in cleaned.replace('。', '，').replace(';', '，').split('，') if seg.strip()]
            short = '，'.join(clauses[:2]).strip()
            return (short[:110] + '…') if len(short) > 110 else short
        if len(cleaned) > 96:
            return cleaned[:88].rstrip() + '…'
        return cleaned

    if point_type == 'product_signal':
        if any(token in lowered for token in ['custom timeline', 'custom timelines', 'home tab', 'pin a specific topic']):
            return 'Custom Timelines makes attention routing inside X more programmable and builder-friendly.'
        if any(token in lowered for token in ['sub-agents', 'latent space']):
            return 'Sub-agents are moving from isolated calls toward shared coordination space.'
        if any(token in lowered for token in ['x payouts', 'remove my monetization', 'payouts went from']):
            return 'Revenue and reach on centralized platforms still sit behind revocable control surfaces.'
        if 'hacker news' in lowered:
            return 'The system-loop angle is now visible enough to travel outside the core agent niche.'
        if any(token in lowered for token in ['boris said', 'cli usage is allowed', 'still blocked', 'usage is allowed']):
            return 'Execution support is opening up, but orchestration reliability is becoming the real differentiator.'
        if any(token in lowered for token in ['massively multiplayer', 'llm-driven game', 'multiplayer', 'gradient bang']):
            return 'LLM-native systems are starting to coordinate multiple actors inside one live environment.'
        if any(token in lowered for token in ['cashtag', 'cashtags']):
            return 'Cashtags makes financial action part of X-native attention flows.'
        if 'xchat' in lowered:
            return 'XChat pushes X from broadcast into coordination and group action.'
        if 'x money' in lowered:
            return 'X Money points toward a closed loop between social graph, payments, and execution.'
        return cleaned[:140]

    if point_type == 'structural_inference':
        if any(token in lowered for token in ['communities', 'community', 'xchat']) and any(token in lowered for token in ['remove', 'shutting down', 'declining usage', 'delete']):
            return 'If the community layer can be removed by product fiat, builders are still renting social infrastructure.'
        if any(token in lowered for token in ['sub-agents', 'latent space', 'multiplayer', 'llm-driven game', 'orchestration', 'system loop', 'execution loop']):
            return 'The real leverage is not in isolated agent output but in keeping intent, coordination, and execution inside one loop.'
        if any(token in lowered for token in ['x payouts', 'remove my monetization', 'payouts went from', 'targeted me']):
            return 'If monetization and reach can still be altered by platform pressure, the real bottleneck is dependency, not audience growth.'
        if any(token in lowered for token in ['xchat', 'x money', 'cashtag', 'cashtags', 'open social graph', 'llm wiki', 'agent memory', 'wikiwise']):
            return infer_x_superapp_structural_inference(lowered)
        if any(token in lowered for token in ['converging', 'operating path', 'stack', 'settlement', 'protocol']):
            return 'The deeper shift is toward a graph + execution + settlement stack, not isolated feature launches.'
        return cleaned[:150]

    return cleaned


def complete_evidence_triple(groups: list[dict[str, str]], source_text: str, claim_family: str) -> list[dict[str, str]]:
    present = {str(item.get('type') or '').strip() for item in groups}
    lowered = (source_text or '').lower()
    completed = list(groups)

    if 'source_fact' not in present:
        fact = ''
        if claim_family == 'x-superapp-open-graph':
            fact = 'X is still one of the richest live graphs for tracking how social, product, and payment signals evolve in real time.'
        elif any(token in lowered for token in ['agent', 'ai', 'graph', 'social']):
            fact = 'The underlying source is not just commentary; it reflects live behavior on a real social graph.'
        if fact:
            completed.append({'type': 'source_fact', 'text': fact})

    if 'product_signal' not in present:
        signal = ''
        if any(token in lowered for token in ['cashtag', 'cashtags']):
            signal = 'Cashtags makes financial action part of X-native attention flows.'
        elif any(token in lowered for token in ['sub-agents', 'latent space']):
            signal = 'Sub-agents are moving from isolated calls toward shared coordination space.'
        elif any(token in lowered for token in ['massively multiplayer', 'llm-driven game', 'multiplayer', 'gradient bang']):
            signal = 'LLM-native systems are starting to coordinate multiple actors inside one live environment.'
        elif 'xchat' in lowered:
            signal = 'XChat pushes X from broadcast into coordination and group action.'
        elif 'x money' in lowered:
            signal = 'X Money points toward a closed loop between social graph, payments, and execution.'
        if signal:
            completed.append({'type': 'product_signal', 'text': signal})

    if 'structural_inference' not in present:
        inference = ''
        if claim_family == 'x-superapp-open-graph':
            inference = infer_x_superapp_structural_inference(lowered)
        elif claim_family == 'intent-coordination-execution':
            inference = 'The real leverage is not in isolated agent output but in keeping intent, coordination, and execution inside one loop.'
        elif claim_family == 'agent-protocol-vs-platform':
            if any(token in lowered for token in ['communities', 'community', 'xchat', 'groupchat']):
                inference = 'If the community surface can be deleted by platform decree, the real bottleneck is infrastructure sovereignty, not feature velocity.'
            else:
                inference = 'The structural issue is platform dependency, not whether agents can generate more content.'
        elif claim_family == 'community-as-ai-crypto-intersection':
            inference = 'The real compounding layer is community coordination, not just tooling novelty.'
        elif any(token in lowered for token in ['protocol', 'settlement', 'coordination']):
            inference = 'The important layer is the coordination structure emerging underneath the surface feature changes.'
        if inference:
            completed.append({'type': 'structural_inference', 'text': inference})

    order = {'source_fact': 0, 'product_signal': 1, 'structural_inference': 2}
    completed.sort(key=lambda x: order.get(x.get('type') or '', 9))
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in completed:
        key = (str(item.get('type') or ''), str(item.get('text') or ''))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append({'type': key[0], 'text': key[1]})
    return deduped[:4]


def distill_evidence_groups(groups: list[dict[str, str]], source_text: str = '', claim_family: str = '') -> tuple[list[str], list[dict[str, str]]]:
    distilled: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in groups:
        point_type = str(item.get('type') or '').strip() or 'source_fact'
        raw_text = str(item.get('text') or '').strip()
        text = distill_supporting_point(raw_text, point_type)
        key = (point_type, text)
        if not text or key in seen:
            continue
        seen.add(key)
        distilled.append({'type': point_type, 'text': text})
    distilled = complete_evidence_triple(distilled, source_text, claim_family)
    order = {'source_fact': 0, 'product_signal': 1, 'structural_inference': 2}
    distilled.sort(key=lambda x: order.get(x.get('type') or '', 9))
    distilled = distilled[:4]
    return [item['text'] for item in distilled], distilled


def build_supporting_points(claim_delta: dict[str, Any], source_item: dict[str, Any] | None, claim_family_override: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    points: list[str] = []
    source_text = str((source_item or {}).get('text') or claim_delta.get('source_excerpt') or '')
    claim_family = str(claim_family_override or claim_delta.get('claim_family') or claim_delta.get('claim_id') or '').strip()
    for point in claim_delta.get('supporting_points') or []:
        cleaned = clean_supporting_text(str(point))
        if cleaned and not is_announcement_copy(cleaned) and cleaned not in points:
            points.append(cleaned)
    if 'Cashtags' in source_text or '$Cashtags' in source_text:
        points.append('Cashtags adds financial actionability directly onto X-native attention flows.')
    if 'Custom Timelines' in source_text:
        points.append('Custom Timelines increases routing power for niche community attention inside X.')
    if 'XChat' in source_text:
        points.append('XChat shifts the stack from broadcast-only social into group coordination and action.')
    if 'X Money' in source_text:
        points.append('X Money points toward a future closed loop between social graph, payments, and agent action.')
    if 'Sub-agents' in source_text or 'sub-agents' in source_text or 'latent space' in source_text:
        points.append('Sub-agents are moving from isolated calls toward shared coordination space.')
    if 'Gradient Bang' in source_text or 'massively multiplayer' in source_text or 'LLM-driven game' in source_text or 'multiplayer' in source_text:
        points.append('LLM-native systems are starting to coordinate multiple actors inside one live environment.')
    points = list(dict.fromkeys([p for p in points if p]))[:4]
    if source_text:
        source_fact_seed = distill_supporting_point(source_text, 'source_fact')
        if source_fact_seed:
            points.insert(0, source_fact_seed)
    points = list(dict.fromkeys([p for p in points if p]))[:4]
    classified = [{'type': classify_supporting_point(p), 'text': p} for p in points]
    return distill_evidence_groups(classified, source_text=source_text, claim_family=claim_family)


def build_theme_pack_claim(
    daily_theme_pack: dict[str, Any],
    recent_theme_counts: dict[str, Any],
    recent_claim_families: set[str],
) -> dict[str, Any] | None:
    thesis = str(daily_theme_pack.get('thesis') or '').strip()
    signal_summary = str(daily_theme_pack.get('signal_summary') or '').strip()
    open_question = str(daily_theme_pack.get('open_question') or '').strip()
    theme_names = [str(x).strip() for x in (daily_theme_pack.get('theme_names') or []) if str(x).strip()]
    if not thesis:
        return None

    theme = infer_theme_from_theme_pack(theme_names)
    family = 'multi-source-thesis'
    novelty_score = 0.78
    evidence_density = 0.92
    timeliness_score = 0.88
    owner_fit_score = 0.9 if theme != 'general-builder' else 0.78
    publish_fatigue_score = min(1.0, 0.2 * float(recent_theme_counts.get(theme) or 0))
    if family in recent_claim_families:
        publish_fatigue_score = max(publish_fatigue_score, 0.45)

    supporting_groups = [
        {'type': 'source_fact', 'text': signal_summary or thesis},
        {'type': 'product_signal', 'text': 'Wiki brief、topic brief 与 trader runtime 已经被合并进同一条 daily theme synthesis。'},
        {'type': 'structural_inference', 'text': open_question or 'The next useful post should emerge from cross-source synthesis, not single-tweet restatement.'},
    ]
    supporting_points, supporting_point_groups = distill_evidence_groups(
        supporting_groups,
        source_text=signal_summary or thesis,
        claim_family=family,
    )

    base_publication_score = compute_publication_score(
        novelty_score,
        evidence_density,
        timeliness_score,
        owner_fit_score,
        publish_fatigue_score,
    )
    evidence_bonus = evidence_promotion_bonus(family, supporting_point_groups)
    publication_score = round(base_publication_score + evidence_bonus + 0.06, 4)
    claim_text = thesis

    return {
        'claim_id': 'daily-theme-pack-thesis',
        'claim_family': family,
        'theme': theme,
        'claim_text': claim_text,
        'claim_type': 'validated',
        'novelty_score': round(novelty_score, 4),
        'timeliness_score': round(timeliness_score, 4),
        'evidence_density': round(evidence_density, 4),
        'owner_fit_score': round(owner_fit_score, 4),
        'publish_fatigue_score': round(publish_fatigue_score, 4),
        'publication_score': publication_score,
        'recommended_actions': ['post', 'thread'],
        'best_anchor_source': 'multi-source:daily-theme-pack',
        'supporting_sources': [],
        'supporting_points': supporting_points,
        'supporting_point_groups': supporting_point_groups,
        'product_mention_mode': 'minimal',
        'source_excerpt': clean_source_excerpt(signal_summary or thesis)[:240],
        'score_breakdown': {
            'base_publication_score': base_publication_score,
            'family_bonus': 0.06,
            'evidence_promotion_bonus': evidence_bonus,
            'novelty_score': round(novelty_score, 4),
            'evidence_density': round(evidence_density, 4),
            'timeliness_score': round(timeliness_score, 4),
            'owner_fit_score': round(owner_fit_score, 4),
            'publish_fatigue_score': round(publish_fatigue_score, 4),
        },
    }


def compute_publication_score(novelty: float, evidence: float, timeliness: float, owner_fit: float, fatigue: float) -> float:
    return round(0.30 * novelty + 0.20 * evidence + 0.20 * timeliness + 0.20 * owner_fit - 0.10 * fatigue, 4)


def evidence_promotion_bonus(family: str, point_groups: list[dict[str, str]]) -> float:
    source_count = sum(1 for item in point_groups if str(item.get('type') or '').strip() == 'source_fact')
    product_count = sum(1 for item in point_groups if str(item.get('type') or '').strip() == 'product_signal')
    structural_count = sum(1 for item in point_groups if str(item.get('type') or '').strip() == 'structural_inference')
    triple_complete = bool(source_count and product_count and structural_count)
    structural_ready = bool(structural_count and (source_count or product_count))

    bonus = 0.0
    if triple_complete:
        bonus += 0.03
    elif structural_ready:
        bonus += 0.015

    if family == 'intent-coordination-execution':
        if triple_complete:
            bonus += 0.04
        elif structural_ready:
            bonus += 0.025
    elif family == 'agent-protocol-vs-platform':
        if triple_complete:
            bonus += 0.03
        elif structural_ready and source_count >= 2:
            bonus += 0.04
        elif structural_ready:
            bonus += 0.02
    elif family == 'community-as-ai-crypto-intersection':
        if triple_complete:
            bonus += 0.025
        elif structural_ready:
            bonus += 0.01
    elif family == 'tokenized-community-coordination' and structural_count == 0:
        bonus -= 0.04

    return round(bonus, 4)


def claim_family_bonus(family: str, source_text: str, delta_type: str) -> float:
    lowered = (source_text or '').lower()
    if family == 'x-superapp-open-graph':
        bonus = 0.08
        if any(token in lowered for token in ['xchat', 'x money', 'cashtag', 'cashtags', 'custom timelines']):
            bonus += 0.04
        return round(bonus, 4)
    if family == 'agent-protocol-vs-platform':
        return 0.07
    if family == 'community-as-ai-crypto-intersection':
        return 0.06
    if family == 'intent-coordination-execution':
        if any(token in lowered for token in ['sub-agent', 'sub-agents', 'latent space', 'multiplayer', 'llm-driven game', 'gradient bang']):
            return 0.06
        return 0.04
    if family == 'tokenized-community-coordination':
        return 0.04
    if family == 'desoc-agent-general':
        return -0.12
    if family.endswith('-general'):
        return -0.06
    if family.endswith('-observation'):
        return -0.08
    if delta_type == 'validated':
        return 0.03
    return 0.0


def window_competition_profile(window_label: str | None) -> dict[str, Any]:
    label = str(window_label or '').strip()
    if label == 'last_3d':
        return {
            'boost': {'x-superapp-open-graph': 0.025},
            'penalty': {'community-as-ai-crypto-intersection': -0.005},
            'preferred_breakthroughs': {'agent-protocol-vs-platform'},
            'label': 'short-horizon freshness / routing bias',
            'window_role': 'freshness-winner',
        }
    if label == 'last_7d':
        return {
            'boost': {'intent-coordination-execution': 0.025, 'agent-protocol-vs-platform': 0.015},
            'penalty': {'x-superapp-open-graph': -0.01, 'tokenized-community-coordination': -0.02},
            'preferred_breakthroughs': {'intent-coordination-execution', 'agent-protocol-vs-platform'},
            'label': 'mid-horizon breakthrough bias',
            'window_role': 'breakthrough-winner',
        }
    if label == 'last_14d':
        return {
            'boost': {'intent-coordination-execution': 0.015, 'community-as-ai-crypto-intersection': 0.02},
            'penalty': {'x-superapp-open-graph': -0.01},
            'preferred_breakthroughs': {'intent-coordination-execution', 'community-as-ai-crypto-intersection'},
            'label': 'long-horizon compounding bias',
            'window_role': 'compounding-winner',
        }
    if label == 'all':
        return {
            'boost': {'intent-coordination-execution': 0.035, 'community-as-ai-crypto-intersection': 0.04},
            'penalty': {'x-superapp-open-graph': -0.035, 'tokenized-community-coordination': -0.02},
            'preferred_breakthroughs': {'intent-coordination-execution', 'community-as-ai-crypto-intersection'},
            'label': 'archive-horizon compounding bias',
            'window_role': 'archive-winner',
        }
    return {
        'boost': {},
        'penalty': {},
        'preferred_breakthroughs': set(),
        'label': 'neutral-window bias',
        'window_role': 'neutral',
    }


def apply_cross_family_breakthrough_adjustments(claims: list[dict[str, Any]], recent_claim_families: set[str], window_label: str | None = None) -> None:
    if not claims:
        return
    profile = window_competition_profile(window_label)
    for claim in claims:
        family = str(claim.get('claim_family') or '')
        breakdown = claim.setdefault('score_breakdown', {})
        if family in (profile.get('boost') or {}):
            boost = float((profile.get('boost') or {}).get(family) or 0.0)
            claim['publication_score'] = round(float(claim.get('publication_score') or 0.0) + boost, 4)
            breakdown['window_profile_bonus'] = round(float(breakdown.get('window_profile_bonus') or 0.0) + boost, 4)
        if family in (profile.get('penalty') or {}):
            penalty = float((profile.get('penalty') or {}).get(family) or 0.0)
            claim['publication_score'] = round(float(claim.get('publication_score') or 0.0) + penalty, 4)
            breakdown['window_profile_penalty'] = round(float(breakdown.get('window_profile_penalty') or 0.0) + penalty, 4)
        claim['score_breakdown'] = breakdown

    dominant = next((c for c in claims if str(c.get('claim_family') or '') == 'x-superapp-open-graph'), None)
    if dominant and 'x-superapp-open-graph' in recent_claim_families:
        dominant['publication_score'] = round(float(dominant.get('publication_score') or 0.0) - 0.03, 4)
        breakdown = dominant.setdefault('score_breakdown', {})
        breakdown['overcapture_penalty'] = -0.03

    dominant_score = float(dominant.get('publication_score') or 0.0) if dominant else None
    breakthrough_families = {'intent-coordination-execution', 'community-as-ai-crypto-intersection', 'agent-protocol-vs-platform'} | set(profile.get('preferred_breakthroughs') or set())
    strongest_breakthrough = None
    strongest_breakthrough_score = -1.0
    for claim in claims:
        family = str(claim.get('claim_family') or '')
        if family not in breakthrough_families:
            continue
        breakdown = claim.get('score_breakdown') or {}
        evidence_bonus = float(breakdown.get('evidence_promotion_bonus') or 0.0)
        current_score = float(claim.get('publication_score') or 0.0)
        fatigue_score = float(breakdown.get('publish_fatigue_score') or 0.0)
        if window_label == 'last_7d' and family in (profile.get('preferred_breakthroughs') or set()) and fatigue_score >= 0.6 and evidence_bonus >= 0.03:
            claim['publication_score'] = round(current_score + 0.025, 4)
            breakdown['fatigue_relief_bonus'] = round(float(breakdown.get('fatigue_relief_bonus') or 0.0) + 0.025, 4)
            current_score = float(claim.get('publication_score') or 0.0)
        if evidence_bonus < 0.05:
            continue
        if dominant_score is not None and current_score < dominant_score and (dominant_score - current_score) <= 0.06:
            claim['publication_score'] = round(current_score + 0.035, 4)
            breakdown['breakthrough_bonus'] = 0.035
        elif dominant is None and current_score >= 0.82:
            claim['publication_score'] = round(current_score + 0.02, 4)
            breakdown['breakthrough_bonus'] = 0.02
        claim['score_breakdown'] = breakdown
        boosted_score = float(claim.get('publication_score') or 0.0)
        if evidence_bonus >= 0.07 and boosted_score > strongest_breakthrough_score:
            strongest_breakthrough = claim
            strongest_breakthrough_score = boosted_score

    if dominant and strongest_breakthrough is not None:
        dominant_score = float(dominant.get('publication_score') or 0.0)
        challenger_score = float(strongest_breakthrough.get('publication_score') or 0.0)
        if challenger_score < dominant_score and (dominant_score - challenger_score) <= 0.03:
            dominant['publication_score'] = round(dominant_score - 0.025, 4)
            dominant_breakdown = dominant.setdefault('score_breakdown', {})
            dominant_breakdown['overcapture_penalty'] = round(float(dominant_breakdown.get('overcapture_penalty') or 0.0) - 0.025, 4)
            strongest_breakthrough['publication_score'] = round(challenger_score + 0.02, 4)
            challenger_breakdown = strongest_breakthrough.setdefault('score_breakdown', {})
            challenger_breakdown['breakthrough_bonus'] = round(float(challenger_breakdown.get('breakthrough_bonus') or 0.0) + 0.02, 4)


def generate_publishable_claims(
    runtime: Path = RUNTIME,
    bookmaker_root: Path = BOOKMARKER_ROOT,
    wiki_delta: dict[str, Any] | None = None,
    publication_memory: dict[str, Any] | None = None,
    *,
    x_sync_doc: dict[str, Any] | None = None,
    topic_brief_doc: dict[str, Any] | None = None,
    window_label: str | None = None,
) -> dict[str, Any]:
    wiki_delta = wiki_delta or read_json(runtime / 'wiki-delta.json') or {}
    publication_memory = publication_memory or read_json(runtime / 'publication-memory.json') or {}
    if x_sync_doc is not None:
        x_sync = x_sync_doc
    else:
        x_sync, _, _ = load_x_sync_with_fallback(bookmaker_root / 'memory')
    topic_brief = topic_brief_doc or read_json(bookmaker_root / 'memory' / 'topic-brief-payload.json') or {}
    daily_theme_pack = read_json(runtime / 'daily-theme-pack.json') or {}
    source_map = source_map_from_x_sync(x_sync)
    recent_theme_counts = publication_memory.get('recent_theme_counts') or {}
    recent_claim_families = {
        str(item.get('claim_family') or '').strip()
        for item in (publication_memory.get('recent_claims') or [])
        if isinstance(item, dict) and str(item.get('claim_family') or '').strip()
    }

    claims: list[dict[str, Any]] = []
    theme_pack_claim = build_theme_pack_claim(daily_theme_pack, recent_theme_counts, recent_claim_families)
    if theme_pack_claim:
        claims.append(theme_pack_claim)
    for claim_delta in wiki_delta.get('claim_deltas') or []:
        if not isinstance(claim_delta, dict):
            continue
        family = str(claim_delta.get('claim_family') or claim_delta.get('claim_id') or '').strip()
        theme = str(claim_delta.get('theme') or 'general-builder').strip()
        supporting_sources = [str(v) for v in (claim_delta.get('supporting_sources') or []) if str(v)]
        best_anchor_source = supporting_sources[0] if supporting_sources else ''
        source_item = source_map.get(best_anchor_source) if best_anchor_source else None
        source_text = clean_source_excerpt(str((source_item or {}).get('text') or claim_delta.get('source_excerpt') or ''))
        family = infer_claim_family_override(family, source_text)
        delta_type = str(claim_delta.get('delta_type') or 'strengthened')

        novelty_score = float(claim_delta.get('novelty_score') or 0.55)
        evidence_density = float(claim_delta.get('evidence_density') or 0.55)
        timeliness_score = 0.92 if delta_type in {'new_connection', 'validated'} else 0.74
        owner_fit_score = 0.9 if family in {'x-superapp-open-graph', 'agent-protocol-vs-platform', 'community-as-ai-crypto-intersection', 'intent-coordination-execution'} else 0.7
        if family == 'x-superapp-open-graph':
            owner_fit_score = 0.96
        elif family == 'agent-protocol-vs-platform':
            owner_fit_score = 0.94
        elif family == 'community-as-ai-crypto-intersection':
            owner_fit_score = 0.92
        elif family == 'intent-coordination-execution':
            owner_fit_score = 0.9
        publish_fatigue_score = min(1.0, 0.2 * float(recent_theme_counts.get(theme) or 0))
        if family in recent_claim_families:
            publish_fatigue_score = max(publish_fatigue_score, 0.65)

        recommended_actions = ['post', 'quote'] if delta_type == 'validated' else ['post', 'thread', 'quote']
        if theme == 'general-builder':
            recommended_actions = ['post', 'quote']
        claim_text = build_claim_text(theme, family, source_text, delta_type)
        supporting_points, supporting_point_groups = build_supporting_points(claim_delta, source_item, claim_family_override=family)
        if not family:
            family = infer_claim_family(theme, source_text)
        base_publication_score = compute_publication_score(
            novelty_score,
            evidence_density,
            timeliness_score,
            owner_fit_score,
            publish_fatigue_score,
        )
        family_bonus = claim_family_bonus(family, source_text, delta_type)
        evidence_bonus = evidence_promotion_bonus(family, supporting_point_groups)
        publication_score = round(base_publication_score + family_bonus + evidence_bonus, 4)
        if family == 'general-builder-observation':
            publication_score = round(publication_score - 0.12, 4)
            if publication_score < 0.75:
                continue
        claims.append({
            'claim_id': str(claim_delta.get('claim_id') or family),
            'claim_family': family,
            'theme': theme,
            'claim_text': claim_text,
            'claim_type': delta_type,
            'novelty_score': round(novelty_score, 4),
            'timeliness_score': round(timeliness_score, 4),
            'evidence_density': round(evidence_density, 4),
            'owner_fit_score': round(owner_fit_score, 4),
            'publish_fatigue_score': round(publish_fatigue_score, 4),
            'publication_score': publication_score,
            'recommended_actions': recommended_actions,
            'best_anchor_source': best_anchor_source,
            'supporting_sources': supporting_sources,
            'supporting_points': supporting_points,
            'supporting_point_groups': supporting_point_groups,
            'product_mention_mode': 'allowed_if_natural' if family != 'agent-protocol-vs-platform' else 'minimal',
            'source_excerpt': source_text[:240],
            'score_breakdown': {
                'base_publication_score': base_publication_score,
                'family_bonus': family_bonus,
                'evidence_promotion_bonus': evidence_bonus,
                'novelty_score': round(novelty_score, 4),
                'evidence_density': round(evidence_density, 4),
                'timeliness_score': round(timeliness_score, 4),
                'owner_fit_score': round(owner_fit_score, 4),
                'publish_fatigue_score': round(publish_fatigue_score, 4),
            },
        })

    apply_cross_family_breakthrough_adjustments(claims, recent_claim_families, window_label=window_label)
    claims.sort(key=lambda item: item.get('publication_score') or 0.0, reverse=True)
    payload = {
        'version': 'v1',
        'generated_at': now_iso(),
        'claims': claims[:8],
        'meta': {
            'source': 'bookmarker-phase1',
            'claim_count': len(claims),
            'window_label': window_label,
            'window_competition_profile': window_competition_profile(window_label).get('label'),
            'window_role': window_competition_profile(window_label).get('window_role'),
            'topic_summary': topic_brief.get('recommendations') or {},
            'daily_theme_pack_available': bool(daily_theme_pack),
        },
    }
    return payload


def main() -> int:
    payload = generate_publishable_claims()
    atomic_write_json(RUNTIME / 'publishable-claims.json', payload)
    print(json.dumps({'status': 'ok', 'path': str(RUNTIME / 'publishable-claims.json'), 'claim_count': len(payload.get('claims') or [])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
