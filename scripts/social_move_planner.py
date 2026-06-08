#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from agency_paths import BOOKMARKER_WS, MAIN_WS

BOOKMARKER_ROOT = (BOOKMARKER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'bookmarker'

PHRASE_BLACKLIST = [
    'My read:',
    'The value here is',
    'This suggests',
    'This highlights',
    'What this means is',
    'This extends the broader line of thought',
]


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


def _group_texts(claim: dict[str, Any], kind: str) -> list[str]:
    groups = claim.get('supporting_point_groups') or []
    return [
        str(item.get('text') or '').strip()
        for item in groups
        if str(item.get('type') or '').strip() == kind and str(item.get('text') or '').strip()
    ]


def is_generic_claim_family(claim_family: str) -> bool:
    token = str(claim_family or '').strip().lower()
    return token.endswith('-observation') or token.endswith('-general') or token in {'general-builder-observation', 'desoc-agent-general'}


def assess_evidence_sufficiency(claim: dict[str, Any], claim_family: str) -> dict[str, Any]:
    source_facts = _group_texts(claim, 'source_fact')
    product_points = _group_texts(claim, 'product_signal')
    structural_points = _group_texts(claim, 'structural_inference')
    publication_score = float(claim.get('publication_score') or 0.0)
    evidence_density = float(claim.get('evidence_density') or 0.0)
    novelty = float(claim.get('novelty_score') or 0.0)
    source_excerpt = str(claim.get('source_excerpt') or '').lower()

    triple_complete = bool(source_facts and product_points and structural_points)
    structural_ready = bool(structural_points and (source_facts or product_points))
    has_reply_surface = any(
        token in source_excerpt
        for token in ['community', 'social graph', 'attention', 'builders', 'open graph', 'x money', 'xchat', 'cashtag', 'cashtags', 'sub-agents', 'multiplayer', 'agent']
    ) or claim_family in {'community-as-ai-crypto-intersection', 'x-superapp-open-graph', 'intent-coordination-execution'}
    has_opposition_surface = any(
        token in source_excerpt
        for token in ['policy', 'pressure', 'protocol', 'settlement', 'execution', 'wrong layer', 'not just', 'not on', 'versus', 'vs']
    ) or claim_family == 'agent-protocol-vs-platform'

    challenge_ready = bool(
        structural_ready
        and has_opposition_surface
        and publication_score >= 0.68
        and evidence_density >= 0.45
    )
    debate_ready = bool(
        triple_complete
        and has_reply_surface
        and novelty >= 0.5
        and publication_score >= 0.72
    )

    return {
        'source_fact_count': len(source_facts),
        'product_signal_count': len(product_points),
        'structural_inference_count': len(structural_points),
        'triple_complete': triple_complete,
        'structural_ready': structural_ready,
        'has_reply_surface': has_reply_surface,
        'has_opposition_surface': has_opposition_surface,
        'challenge_ready': challenge_ready,
        'debate_ready': debate_ready,
    }


def pick_argument_move(claim: dict[str, Any], claim_family: str, theme: str) -> tuple[str, str, dict[str, Any]]:
    source_excerpt = str(claim.get('source_excerpt') or '')
    structural_points = _group_texts(claim, 'structural_inference')
    product_points = _group_texts(claim, 'product_signal')
    publication_score = float(claim.get('publication_score') or 0.0)
    owner_fit = float(claim.get('owner_fit_score') or 0.0)
    gate = assess_evidence_sufficiency(claim, claim_family)

    if gate['challenge_ready'] and gate['has_opposition_surface'] and (publication_score >= 0.82 or owner_fit >= 0.9):
        return 'challenge', 'structural_opposition_signal', gate

    if gate['debate_ready']:
        return 'debate-hook', 'evidence_triple_with_reply_surface', gate

    if claim_family == 'x-superapp-open-graph':
        if gate['challenge_ready'] and structural_points and product_points and publication_score >= 0.84:
            return 'challenge', 'x_superapp_high_conviction', gate
        if gate['debate_ready'] or (gate['triple_complete'] and publication_score >= 0.72):
            return 'debate-hook', 'x_superapp_argument_surface', gate
        return 'builder-signal', 'gate_fallback_insufficient_for_stronger_move', gate

    if claim_family == 'agent-protocol-vs-platform':
        if gate['challenge_ready']:
            return 'challenge', 'family_default_protocol_conflict', gate
        return 'builder-signal', 'gate_fallback_insufficient_for_challenge', gate
    if claim_family == 'community-as-ai-crypto-intersection':
        if gate['debate_ready']:
            return 'debate-hook', 'family_default_discussion_surface', gate
        return 'builder-signal', 'gate_fallback_insufficient_for_debate', gate
    if claim_family == 'intent-coordination-execution':
        if gate['debate_ready'] and publication_score >= 0.74:
            return 'debate-hook', 'intent_loop_argument_surface', gate
        if gate['structural_ready'] and publication_score >= 0.62:
            return 'builder-signal', 'intent_loop_structural_signal', gate
        return 'builder-signal', 'intent_loop_family_fallback', gate
    if claim_family == 'tokenized-community-coordination':
        return 'builder-signal', 'family_default_builder_signal', gate
    if theme == 'token-coordination':
        if gate['debate_ready']:
            return 'debate-hook', 'theme_promoted_by_evidence_triple', gate
        if gate['challenge_ready']:
            return 'challenge', 'theme_promoted_by_structural_opposition', gate
        return 'field-report', 'theme_default_field_report', gate
    if is_generic_claim_family(claim_family):
        publication_score = float(claim.get('publication_score') or 0.0)
        if publication_score >= 0.58:
            return 'builder-signal', 'generic_family_builder_fallback', gate
        return 'field-report', 'generic_family_field_report_fallback', gate
    return 'take', 'fallback_take', gate


def infer_move_type(claim: dict[str, Any], claim_family: str, theme: str) -> tuple[str, str, dict[str, Any]]:
    return pick_argument_move(claim, claim_family, theme)


def infer_voice_mode(move_type: str) -> str:
    mapping = {
        'builder-signal': 'agent-native-take',
        'challenge': 'builder-signal',
        'debate-hook': 'field-report',
        'coordination-call': 'coordination-invite',
        'field-report': 'field-report',
        'take': 'agent-native-take',
    }
    return mapping.get(move_type, 'agent-native-take')


def infer_sub_angle(claim_family: str, source_excerpt: str) -> str:
    lowered = (source_excerpt or '').lower()
    if claim_family == 'x-superapp-open-graph':
        if 'x money' in lowered or 'cashtag' in lowered or 'payment' in lowered:
            return 'payment-execution'
        if 'xchat' in lowered:
            return 'os-convergence'
        if 'open social graph' in lowered or 'builder' in lowered:
            return 'open-builder-window'
        return 'agent-data-rail'
    if claim_family == 'agent-protocol-vs-platform':
        return 'platform-pressure'
    if claim_family == 'community-as-ai-crypto-intersection':
        return 'coordination-surface'
    if claim_family == 'intent-coordination-execution':
        if 'multiplayer' in lowered or 'llm-driven game' in lowered:
            return 'shared-loop'
        if 'sub-agent' in lowered or 'latent space' in lowered:
            return 'orchestration-loop'
        return 'execution-loop'
    return 'default'


def move_constraints(move_type: str, voice_mode: str) -> tuple[list[str], list[str]]:
    must_include = ['explicit position', 'one structurally new signal']
    if move_type in {'debate-hook', 'challenge'}:
        must_include.append('one reply surface')
    if move_type == 'coordination-call':
        must_include.append('one action invitation')

    must_avoid = list(PHRASE_BLACKLIST)
    if voice_mode == 'agent-native-take':
        must_avoid += ['broad explanation', 'commentator tone', 'summary framing']
    if move_type == 'builder-signal':
        must_include.append('single narrative thread from opener to closing')
        must_avoid += ['generic motivation speech', 'bullet-briefing evidence', 'announcement-copy phrasing']
    return must_include, must_avoid


def social_tension_score(move_type: str, claim_family: str) -> float:
    base = {
        'challenge': 0.92,
        'debate-hook': 0.86,
        'builder-signal': 0.78,
        'coordination-call': 0.82,
        'field-report': 0.68,
        'take': 0.74,
    }.get(move_type, 0.7)
    if claim_family == 'x-superapp-open-graph':
        base += 0.04
    return min(1.0, round(base, 4))


def interaction_potential_score(move_type: str, claim_family: str) -> float:
    base = {
        'challenge': 0.86,
        'debate-hook': 0.88,
        'builder-signal': 0.72,
        'coordination-call': 0.8,
        'field-report': 0.6,
        'take': 0.7,
    }.get(move_type, 0.65)
    if claim_family == 'x-superapp-open-graph':
        base += 0.05
    return min(1.0, round(base, 4))


def _clean_supporting_points(points: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for raw in points or []:
        text = str(raw or '').strip()
        lowered = text.lower()
        if not text:
            continue
        if text.startswith('→ http') or lowered.startswith('my read:'):
            continue
        if any(phrase.lower() in lowered for phrase in PHRASE_BLACKLIST):
            continue
        cleaned.append(text)
    return cleaned[:4]


def role_output_contract(window_role: str) -> tuple[list[str], list[str], str, str]:
    role = str(window_role or '').strip()
    if role == 'freshness-winner':
        return (
            ['immediacy', 'one sharp current signal'],
            ['archive-summary tone', 'slow historical framing'],
            'react to a live shift before it settles into consensus',
            'short-window-reactive',
        )
    if role == 'breakthrough-winner':
        return (
            ['one reframe that breaks a stale reading', 'one concrete breakthrough signal'],
            ['generic status update', 'passive recap'],
            'show why a secondary narrative now deserves to break into the front row',
            'breakthrough-reframe',
        )
    if role == 'compounding-winner':
        return (
            ['one compounding thesis', 'evidence of persistence across multiple signals'],
            ['announcement tone', 'single-feature framing'],
            'show why this narrative keeps strengthening as signals accumulate',
            'compounding-thesis',
        )
    if role == 'archive-winner':
        return (
            ['one durable structural takeaway', 'evidence that survives beyond the latest cycle'],
            ['hot-take freshness framing', 'single-cycle overreaction'],
            'surface the strongest durable takeaway from the recent archive horizon',
            'archive-structural',
        )
    return ([], [], 'align the output with the winning claim role', 'default')


def generate_social_move_plan(
    runtime: Path = RUNTIME,
    publishable_claims_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    publishable_claims_doc = publishable_claims_doc or read_json(runtime / 'publishable-claims.json') or {}
    claims = [c for c in (publishable_claims_doc.get('claims') or []) if isinstance(c, dict)]
    window_role = str(((publishable_claims_doc.get('meta') or {}).get('window_role') or '')).strip()
    window_profile = str(((publishable_claims_doc.get('meta') or {}).get('window_competition_profile') or '')).strip()
    role_must_include, role_must_avoid, role_interaction_goal, role_hook_style = role_output_contract(window_role)
    moves: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim.get('claim_id') or claim.get('claim_family') or '').strip()
        claim_family = str(claim.get('claim_family') or claim_id).strip()
        theme = str(claim.get('theme') or 'general-builder').strip()
        move_type, move_reason, evidence_gate = infer_move_type(claim, claim_family, theme)
        if window_role == 'freshness-winner' and move_type == 'challenge':
            if bool((evidence_gate or {}).get('debate_ready')):
                move_type = 'debate-hook'
                move_reason = 'freshness_role_prefers_live_argument'
            else:
                move_type = 'builder-signal'
                move_reason = 'freshness_role_prefers_reactive_signal'
        voice_mode = infer_voice_mode(move_type)
        sub_angle = infer_sub_angle(claim_family, str(claim.get('source_excerpt') or ''))
        must_include, must_avoid = move_constraints(move_type, voice_mode)
        novelty = float(claim.get('novelty_score') or 0.5)
        evidence = float(claim.get('evidence_density') or 0.5)
        timeliness = float(claim.get('timeliness_score') or 0.5)
        owner_fit = float(claim.get('owner_fit_score') or 0.5)
        fatigue = float(claim.get('publish_fatigue_score') or 0.0)
        publication = float(claim.get('publication_score') or 0.0)
        tension = social_tension_score(move_type, claim_family)
        interaction = interaction_potential_score(move_type, claim_family)
        generic_penalty = 0.08 if is_generic_claim_family(claim_family) else 0.0
        move_score = round(
            0.22 * publication +
            0.18 * novelty +
            0.14 * evidence +
            0.12 * timeliness +
            0.12 * owner_fit +
            0.11 * tension +
            0.11 * interaction -
            0.10 * fatigue -
            generic_penalty,
            4,
        )
        target_object = 'AI builders watching X' if claim_family == 'x-superapp-open-graph' else 'builders and protocol participants'
        hook_style = 'half-step-thesis' if move_type in {'builder-signal', 'debate-hook'} else 'direct-claim'
        must_include = list(dict.fromkeys(must_include + role_must_include))
        must_avoid = list(dict.fromkeys(must_avoid + role_must_avoid))
        interaction_goal = role_interaction_goal or ('attract replies and alignment' if move_type != 'field-report' else 'signal a fresh field observation')
        if move_type == 'field-report':
            interaction_goal = 'signal a fresh field observation'
        effective_hook_style = role_hook_style if role_hook_style != 'default' and move_type in {'builder-signal', 'debate-hook', 'challenge'} else hook_style
        moves.append({
            'move_id': f'move-{claim_id}-{move_type}',
            'claim_id': claim_id,
            'claim_family': claim_family,
            'theme': theme,
            'window_role': window_role,
            'window_competition_profile': window_profile,
            'move_type': move_type,
            'move_reason': move_reason,
            'evidence_gate': evidence_gate,
            'voice_mode': voice_mode,
            'sub_angle': sub_angle,
            'target_object': target_object,
            'tension_mode': 'sharp-positioning' if move_type in {'challenge', 'debate-hook', 'builder-signal'} else 'measured-positioning',
            'interaction_goal': interaction_goal,
            'hook_style': effective_hook_style,
            'novelty_score': novelty,
            'social_tension_score': tension,
            'interaction_potential_score': interaction,
            'owner_fit_score': owner_fit,
            'publish_fatigue_score': fatigue,
            'move_score': move_score,
            'best_anchor_source': claim.get('best_anchor_source'),
            'supporting_points': _clean_supporting_points(claim.get('supporting_points') or []),
            'supporting_point_groups': claim.get('supporting_point_groups') or [],
            'must_include': must_include,
            'must_avoid': must_avoid,
        })
    moves.sort(key=lambda m: m.get('move_score') or 0.0, reverse=True)
    payload = {
        'version': 'v1',
        'generated_at': now_iso(),
        'moves': moves,
        'meta': {
            'move_count': len(moves),
            'source_claim_count': len(claims),
        },
    }
    return payload


def main() -> int:
    payload = generate_social_move_plan()
    atomic_write_json(RUNTIME / 'social-move-plan.json', payload)
    print(json.dumps({'status': 'ok', 'path': str(RUNTIME / 'social-move-plan.json'), 'move_count': len(payload.get('moves') or [])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
