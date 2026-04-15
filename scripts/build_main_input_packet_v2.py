#!/usr/bin/env python3
"""Build a runtime-first main input packet from V2 worker artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE") or str(Path.home() / ".openclaw" / "workspace"))
RUNTIME = ROOT / 'runtime'
MEMORY = ROOT / 'memory'


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def age_gap_seconds(a: Any, b: Any) -> float | None:
    da = parse_dt(a)
    db = parse_dt(b)
    if not da or not db:
        return None
    return abs((da - db).total_seconds())


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
    Path(temp_name).replace(path)


def choose_field(runtime_value: Any, runtime_ref: str, runtime_class: str, fallback_value: Any = None, fallback_ref: str | None = None) -> tuple[Any, dict[str, Any]]:
    if runtime_value is not None:
        return runtime_value, {
            'source_class': runtime_class,
            'source_ref': runtime_ref,
            'used_fallback': False,
        }
    if fallback_value is not None:
        return fallback_value, {
            'source_class': 'legacy-fallback',
            'source_ref': fallback_ref,
            'used_fallback': True,
        }
    return None, {
        'source_class': 'missing',
        'source_ref': None,
        'used_fallback': False,
    }


def extract_source_health(source_health: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(source_health, dict) or not source_health:
        return None
    value = {
        'bird': source_health.get('bird'),
        'browser_relay': source_health.get('browser_relay'),
        'xurl': source_health.get('xurl'),
        'mismatch': source_health.get('mismatch'),
    }
    return value if any(v is not None for v in value.values()) else None


def derive_claim_recommended(reward_status: dict[str, Any] | None) -> bool | None:
    items = reward_status.get('claimable') if isinstance(reward_status, dict) else None
    if not isinstance(items, list):
        return None
    seen = False
    for item in items:
        if not isinstance(item, dict):
            continue
        seen = True
        try:
            usd_value = float(item.get('reward_value_usd'))
        except Exception:
            usd_value = None
        if usd_value is not None and usd_value > 2:
            return True
    return False if seen else None


def derive_wallet_state(wallet_snapshot: dict[str, Any] | None) -> str | None:
    status = wallet_snapshot.get('status') if isinstance(wallet_snapshot, dict) else None
    if status == 'ok':
        return 'healthy'
    if status in {'blocked', 'stale'}:
        return 'blocked'
    return status


def fetch_live_op_vp() -> tuple[float | None, float | None]:
    """Fetch real-time OP/VP from TagClaw API."""
    import subprocess
    credentials = read_json(Path.home() / '.config' / 'tagclaw' / 'credentials.json')
    if not credentials or not credentials.get('api_key'):
        return None, None
    api_key = str(credentials['api_key']).strip()
    try:
        proc = subprocess.run(
            ['curl', '-sS', 'https://bsc-api.tagai.fun/tagclaw/me',
             '-H', f'Authorization: Bearer {api_key}',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None, None
        data = json.loads(proc.stdout.strip())
        agent = data.get('agent') or data
        op = agent.get('op')
        vp = agent.get('vp')
        return (float(op) if op is not None else None), (float(vp) if vp is not None else None)
    except Exception:
        return None, None


def main() -> int:
    bookmarker_latest = read_json(RUNTIME / 'bookmarker' / 'latest.json') or {}
    trader_latest = read_json(RUNTIME / 'trader' / 'latest.json') or {}
    source_health = read_json(RUNTIME / 'bookmarker' / 'source-health.json') or {}
    topic_brief = read_json(RUNTIME / 'bookmarker' / 'topic-brief.json') or {}
    content_candidates = read_json(RUNTIME / 'bookmarker' / 'content-candidates.json') or {}
    # TAS_social: bookmarker is now the sole owner (2026-03-25).
    # Main no longer computes or publishes TAS_social.
    bookmarker_tas_social = read_json(RUNTIME / 'bookmarker' / 'tas-social.json') or {}
    wallet_snapshot = read_json(RUNTIME / 'trader' / 'wallet-snapshot.json') or {}
    reward_status = read_json(RUNTIME / 'trader' / 'reward-status.json') or {}
    tas_trade = read_json(RUNTIME / 'trader' / 'tas-trade.json') or {}
    trader_mq = read_json(RUNTIME / 'trader' / 'measurement-quality.json') or {}
    risk_status = read_json(RUNTIME / 'trader' / 'risk-status.json') or {}
    main_runtime_state = read_json(RUNTIME / 'main' / 'runtime-state.json') or {}
    main_tas_social = {}  # retired: Main no longer owns TAS_social
    heartbeat_state = read_json(MEMORY / 'heartbeat-state.json') or {}
    legacy_tas_trade = read_json(MEMORY / 'tas-trade-latest.json') or {}
    x_trend = read_json(RUNTIME / 'bookmarker' / 'x-trend-latest.json') or {}

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not bookmarker_latest:
        blockers.append({"code": "missing_bookmarker_latest", "message": "runtime/bookmarker/latest.json missing", "severity": "error"})
    if not trader_latest:
        blockers.append({"code": "missing_trader_latest", "message": "runtime/trader/latest.json missing", "severity": "error"})

    if bookmarker_latest.get('status') in {'blocked', 'stale'}:
        warnings.append({"code": "bookmarker_degraded", "message": f"bookmarker latest is {bookmarker_latest.get('status')}", "severity": "warning"})
    if trader_latest.get('status') in {'blocked', 'stale', 'partial'}:
        warnings.append({"code": "trader_degraded", "message": f"trader latest is {trader_latest.get('status')}", "severity": "warning"})

    mixed_epoch_checks = {
        'reward_vs_wallet_gap_seconds': age_gap_seconds(reward_status.get('checked_at') or reward_status.get('updated_at'), wallet_snapshot.get('updated_at')),
        'reward_vs_tas_gap_seconds': age_gap_seconds(reward_status.get('checked_at') or reward_status.get('updated_at'), tas_trade.get('updated_at')),
        'reward_vs_latest_gap_seconds': age_gap_seconds(reward_status.get('checked_at') or reward_status.get('updated_at'), trader_latest.get('generated_at')),
        'wallet_vs_latest_gap_seconds': age_gap_seconds(wallet_snapshot.get('updated_at'), trader_latest.get('generated_at')),
    }
    mixed_epoch_threshold_seconds = 15 * 60
    mixed_epoch_failures = {k: v for k, v in mixed_epoch_checks.items() if v is not None and v > mixed_epoch_threshold_seconds}
    if mixed_epoch_failures:
        warnings.append({
            "code": "trader_mixed_epoch_runtime",
            "message": f"trader runtime timestamps are inconsistent beyond {mixed_epoch_threshold_seconds}s: {mixed_epoch_failures}",
            "severity": "warning"
        })

    # Bundle coherence check (additive — does not replace mixed_epoch_checks)
    trader_bundle_ts = {
        'wallet-snapshot': wallet_snapshot.get('bundle_ts'),
        'reward-status': reward_status.get('bundle_ts'),
        'tas-trade': tas_trade.get('bundle_ts'),
        'risk-status': risk_status.get('bundle_ts'),
        'latest': trader_latest.get('bundle_ts'),
    }
    if any(v is not None for v in trader_bundle_ts.values()):
        unique_ts = set(v for v in trader_bundle_ts.values() if v is not None)
        if len(unique_ts) != 1 or any(v is None for v in trader_bundle_ts.values()):
            expected = next(iter(unique_ts)) if len(unique_ts) == 1 else None
            diverging = {k: v for k, v in trader_bundle_ts.items() if v != expected}
            warnings.append({
                "code": "trader_bundle_incoherent",
                "message": f"trader runtime bundle_ts mismatch — files diverge: {diverging}",
                "severity": "warning"
            })

    summary: dict[str, Any] = {}
    provenance: dict[str, Any] = {}

    summary['content_urgency'], provenance['content_urgency'] = choose_field(
        topic_brief.get('content_urgency'),
        'runtime/bookmarker/topic-brief.json',
        'runtime-canonical',
        (bookmarker_latest.get('outputs') or {}).get('content_urgency'),
        'runtime/bookmarker/latest.json',
    )
    summary['high_signal_count'], provenance['high_signal_count'] = choose_field(
        topic_brief.get('high_signal_count'),
        'runtime/bookmarker/topic-brief.json',
        'runtime-canonical',
        (bookmarker_latest.get('outputs') or {}).get('high_signal_count'),
        'runtime/bookmarker/latest.json',
    )
    summary['source_health'], provenance['source_health'] = choose_field(
        extract_source_health(source_health),
        'runtime/bookmarker/source-health.json',
        'runtime-canonical',
        (bookmarker_latest.get('outputs') or {}).get('source_health'),
        'runtime/bookmarker/latest.json',
    )
    # TAS_social: bookmarker is the sole owner — always read from bookmarker runtime (2026-03-25)
    summary['tas_social'], provenance['tas_social'] = choose_field(
        {'status': bookmarker_tas_social.get('status'), 'value': bookmarker_tas_social.get('value')}
        if bookmarker_tas_social else None,
        'runtime/bookmarker/tas-social.json',
        'runtime-canonical',
    )
    # P2: enforce null semantics — tas_trade value must be null (not claim-only) when
    # price_visibility != ok, measurement_quality != ok, or portfolio_usd is missing.
    _tt_price_vis = trader_mq.get('price_visibility', 'unknown')
    _tt_mq_status = trader_mq.get('overall_status') or (tas_trade.get('measurement_quality') or {}).get('overall_status', 'unknown')
    _tt_portfolio = tas_trade.get('portfolio_usd_raw')
    _tt_measurement_ok = _tt_price_vis == 'ok' and _tt_mq_status == 'ok' and _tt_portfolio is not None
    if tas_trade and not _tt_measurement_ok:
        _tas_trade_primary = {'status': 'degraded', 'value': None}
    elif tas_trade:
        _tas_trade_primary = {'status': tas_trade.get('status'), 'value': tas_trade.get('value')}
    else:
        _tas_trade_primary = None
    summary['tas_trade'], provenance['tas_trade'] = choose_field(
        _tas_trade_primary,
        'runtime/trader/tas-trade.json',
        'runtime-canonical',
        ({'status': legacy_tas_trade.get('status'), 'value': legacy_tas_trade.get('value')} if legacy_tas_trade else None),
        'memory/tas-trade-latest.json',
    )
    summary['claimable_usd'], provenance['claimable_usd'] = choose_field(
        reward_status.get('claimable_usd_total'),
        'runtime/trader/reward-status.json',
        'runtime-canonical',
        ((trader_latest.get('outputs') or {}).get('reward_state') or {}).get('claimable_usd'),
        'runtime/trader/latest.json',
    )
    summary['claim_recommended'], provenance['claim_recommended'] = choose_field(
        derive_claim_recommended(reward_status),
        'runtime/trader/reward-status.json',
        'runtime-canonical',
        ((trader_latest.get('outputs') or {}).get('reward_state') or {}).get('claim_recommended'),
        'runtime/trader/latest.json',
    )
    summary['wallet_state'], provenance['wallet_state'] = choose_field(
        derive_wallet_state(wallet_snapshot),
        'runtime/trader/wallet-snapshot.json',
        'runtime-canonical',
        (trader_latest.get('outputs') or {}).get('wallet_state'),
        'runtime/trader/latest.json',
    )
    summary['recent_operations'], provenance['recent_operations'] = choose_field(
        (trader_latest.get('outputs') or {}).get('recent_operations'),
        ((trader_latest.get('outputs') or {}).get('execution_ledger_ref') or 'runtime/trader/latest.json'),
        'runtime-canonical',
    )
    summary['last_failed_operation'], provenance['last_failed_operation'] = choose_field(
        (trader_latest.get('outputs') or {}).get('last_failed_operation'),
        ((trader_latest.get('outputs') or {}).get('execution_ledger_ref') or 'runtime/trader/latest.json'),
        'runtime-canonical',
    )
    summary['pending_or_unconfirmed_orders'], provenance['pending_or_unconfirmed_orders'] = choose_field(
        (trader_latest.get('outputs') or {}).get('pending_or_unconfirmed_orders'),
        ((trader_latest.get('outputs') or {}).get('execution_ledger_ref') or 'runtime/trader/latest.json'),
        'runtime-canonical',
    )
    summary['execution_count_today'], provenance['execution_count_today'] = choose_field(
        (trader_latest.get('outputs') or {}).get('execution_count_today'),
        ((trader_latest.get('outputs') or {}).get('execution_ledger_ref') or 'runtime/trader/latest.json'),
        'runtime-canonical',
    )
    # OP/VP: prefer live API query over stale runtime-state / heartbeat-state
    live_op, live_vp = fetch_live_op_vp()
    summary['op'], provenance['op'] = choose_field(
        live_op,
        'tagclaw-api:/tagclaw/me',
        'live-api',
        main_runtime_state.get('op'),
        'runtime/main/runtime-state.json',
    )
    summary['vp'], provenance['vp'] = choose_field(
        live_vp,
        'tagclaw-api:/tagclaw/me',
        'live-api',
        main_runtime_state.get('vp'),
        'runtime/main/runtime-state.json',
    )
    x_trend_kw_list = [k['term'] for k in x_trend.get('keywords', [])[:8] if isinstance(k, dict) and 'term' in k]
    summary['x_trend_keywords'], provenance['x_trend_keywords'] = choose_field(
        x_trend_kw_list,
        'runtime/bookmarker/x-trend-latest.json',
        'runtime-canonical',
    )
    summary['x_trend_high_signal'], provenance['x_trend_high_signal'] = choose_field(
        x_trend.get('high_signal_count'),
        'runtime/bookmarker/x-trend-latest.json',
        'runtime-canonical',
    )

    if any(meta.get('used_fallback') for meta in provenance.values() if isinstance(meta, dict)):
        warnings.append({"code": "legacy_fallback_used", "message": "input-packet used one or more legacy fallback fields", "severity": "warning"})

    packet = {
        "version": "v1",
        "updated_at": now_iso(),
        "status": "blocked" if blockers else ("partial" if warnings else "ok"),
        "summary": summary,
        "bookmarker": {
            "latest_ref": "runtime/bookmarker/latest.json",
            "topic_brief_ref": "runtime/bookmarker/topic-brief.json",
            "content_candidates_ref": "runtime/bookmarker/content-candidates.json",
            "source_health_ref": "runtime/bookmarker/source-health.json",
            "status": bookmarker_latest.get('status'),
            "topic_keywords": topic_brief.get('keywords', []),
            "candidate_count": len(content_candidates.get('items', [])) if isinstance(content_candidates.get('items'), list) else None,
            "x_trend_ref": "runtime/bookmarker/x-trend-latest.json",
            "x_trend_keywords": [k['term'] for k in x_trend.get('keywords', [])[:5] if isinstance(k, dict) and 'term' in k],
        },
        "trader": {
            "latest_ref": "runtime/trader/latest.json",
            "wallet_snapshot_ref": "runtime/trader/wallet-snapshot.json",
            "reward_status_ref": "runtime/trader/reward-status.json",
            "tas_trade_ref": "runtime/trader/tas-trade.json",
            "risk_status_ref": "runtime/trader/risk-status.json",
            "execution_ledger_ref": ((trader_latest.get('outputs') or {}).get('execution_ledger_ref')),
            "status": trader_latest.get('status'),
            "wallet_address": wallet_snapshot.get('wallet_address'),
            "risk_flags": risk_status.get('risk_flags', []),
            "freshness": {
                "threshold_seconds": mixed_epoch_threshold_seconds,
                "checks": mixed_epoch_checks,
                "mixed_epoch": bool(mixed_epoch_failures),
            },
        },
        "main": {
            "runtime_state_ref": "runtime/main/runtime-state.json",
            "tas_social_ref": "runtime/bookmarker/tas-social.json",  # bookmarker owns TAS_social
            "social_intent_ref": "runtime/main/social-intent.json",
            "treasury_policy_ref": "runtime/main/treasury-policy.json",
        },
        "provenance": {
            "summary_fields": provenance,
            "fallback_fields": sorted([
                name for name, meta in provenance.items()
                if isinstance(meta, dict) and meta.get('used_fallback')
            ]),
        },
        "blockers": blockers,
        "warnings": warnings,
        "notes": "runtime-first packet for main active-cycle consumption; ownership-sensitive fields prefer canonical runtime outputs and mark runtime-owned / runtime-canonical / runtime-bridge / legacy-fallback provenance explicitly"
    }

    out = RUNTIME / 'main' / 'input-packet.json'
    atomic_write_json(out, packet)
    print(json.dumps({"status": packet['status'], "path": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
