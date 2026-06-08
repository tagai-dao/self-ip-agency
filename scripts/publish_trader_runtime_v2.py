#!/usr/bin/env python3
"""Trader V2 self-publisher: writes directly to main workspace runtime/trader/*.

This replaces the V1 projection bridge (publish_runtime_v1.py --agent trader)
and the shadow-native publisher. Trader now owns its runtime outputs directly.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from agency_paths import MAIN_WS, TRADER_WS

TRADER_ROOT = (TRADER_WS)
MAIN_ROOT = (MAIN_WS)
RUNTIME = MAIN_ROOT / 'runtime' / 'trader'

# ── Risk-management stop-loss (P1, 2026-05-28) ───────────────────────────
# A held position that is BOTH heat-declining AND down beyond this drawdown
# over the trailing window triggers a sell — INDEPENDENT of the concentration
# gate. Ticks under an active priority_override (owner strategic hold, e.g.
# BUIDL) are EXEMPT and never stop-loss-sold. Per owner decision 2026-05-28:
# "豁免 BUIDL，止损只管其他仓".
PRICE_HISTORY_PATH = RUNTIME / 'price-history.json'
STOP_LOSS_DRAWDOWN_PCT = -0.25   # -25% over the window
STOP_LOSS_WINDOW_HOURS = 72      # 3 days

WIKI_ROOT = (MAIN_WS / 'wiki')

# P2 2026-04-03: Credit rank baseline — holding 500k+ TagClaw = full score (1.0)
_CREDIT_RANK_BASELINE_FALLBACK = 500_000

# P0 2026-04-09: TAS_trade v3 — normalized USD formula
PORTFOLIO_TARGET = 50.0   # portfolio_usd / 50 → full score (1.0)
CLAIM_TARGET = 5.0        # claimable_usd / 5 → full score (1.0)


def load_wiki_credit_baseline(fallback: float) -> float:
    """Read Credit rank baseline from wiki/concepts/TagClaw.md, fallback if not found."""
    tagclaw_md = WIKI_ROOT / 'concepts' / 'TagClaw.md'
    try:
        text = tagclaw_md.read_text(encoding='utf-8')
    except Exception:
        return fallback
    # Search for CREDIT_RANK_BASELINE or Credit 排名 patterns with a number
    for pattern in (
        re.compile(r'CREDIT_RANK_BASELINE\s*[=:]\s*([\d_,.]+)'),
        re.compile(r'Credit\s*排名[^0-9]*([\d_,.]+)'),
        re.compile(r'credit_i\s*='),  # formula marker — use fallback
    ):
        m = pattern.search(text)
        if m and m.lastindex and m.lastindex >= 1:
            try:
                return float(m.group(1).replace(',', '').replace('_', ''))
            except ValueError:
                continue
    return fallback


CREDIT_RANK_BASELINE = load_wiki_credit_baseline(fallback=_CREDIT_RANK_BASELINE_FALLBACK)


def load_wiki_trending_ticks(n: int = 5) -> list[str]:
    """Read top-n trending ticks from wiki ticks_trending.json, with fallback."""
    path = WIKI_ROOT / 'tagclaw-platform' / 'raw' / 'ticks_trending.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        ticks = data.get('data', {}).get('ticks', [])
        return [t['tick'] for t in ticks[:n] if isinstance(t, dict) and t.get('tick')]
    except Exception:
        return ['TagClaw', 'BUIDL', 'TTAI']


def load_community_heat() -> dict[str, Any]:
    """Read community-heat.json from main workspace shared runtime.

    Returns the full heat data if fresh (< 6h), else empty dict with heat_available=False.
    """
    heat_path = MAIN_ROOT / 'runtime' / 'shared' / 'community-heat.json'
    try:
        if not heat_path.exists():
            return {'heat_available': False, 'reason': 'file_missing'}
        data = json.loads(heat_path.read_text(encoding='utf-8'))
        # Freshness check: computed_at < 6h ago
        computed_at = data.get('computed_at', '')
        if computed_at:
            from datetime import timedelta
            ct = datetime.fromisoformat(computed_at.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) - ct.astimezone(timezone.utc) > timedelta(hours=6):
                return {'heat_available': False, 'reason': 'stale', 'stale_since': computed_at}
        data['heat_available'] = True
        return data
    except Exception:
        return {'heat_available': False, 'reason': 'parse_error'}


def load_wiki_execution_brief_credit() -> dict[str, Any]:
    """Read credit_strategy from wiki-execution-brief.json."""
    path = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-execution-brief.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        cs = data.get('credit_strategy', {})
        if not isinstance(cs, dict):
            return {}
        return {
            'vp_flush_threshold': cs.get('vp_flush_threshold'),
            'daily_vp_target': cs.get('daily_vp_target'),
            'recommended_tokens': cs.get('recommended_tokens', []),
            'source': 'wiki-execution-brief',
        }
    except Exception:
        return {}


def is_wiki_platform_fresh(max_age_days: int = 7) -> bool:
    """Check if ticks_trending.json exists and is less than max_age_days old."""
    path = WIKI_ROOT / 'tagclaw-platform' / 'raw' / 'ticks_trending.json'
    try:
        if not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age = datetime.now(timezone.utc) - mtime
        return age.total_seconds() < max_age_days * 86400
    except Exception:
        return False


def load_wiki_narrative_context(max_items: int = 8) -> dict[str, Any]:
    """READ-ONLY narrative context from the shared wiki retrieval pack.

    Surfaces each concept's stance + discussion hook + category so the trader
    carries a narrative rationale alongside numeric signals. This is REFERENCE
    CONTEXT ONLY — it MUST NOT feed position sizing, stop-loss, claim, or order
    logic. Purely informational; guarded so a missing pack degrades to empty.
    """
    pack_path = MAIN_ROOT / 'runtime' / 'shared' / 'wiki-retrieval-pack.json'
    out: dict[str, Any] = {'available': False, 'pack_generated_at': None, 'concepts': []}
    try:
        pack = json.loads(pack_path.read_text(encoding='utf-8'))
    except Exception:
        return out
    out['available'] = True
    out['pack_generated_at'] = pack.get('generated_at')
    docs = pack.get('docs') if isinstance(pack.get('docs'), list) else []

    def _field(text: str, label: str) -> str | None:
        for line in (text or '').splitlines():
            if line.startswith(label):
                return (line[len(label):].strip()[:240]) or None
        return None

    items: list[dict[str, Any]] = []
    for d in docs:
        if not isinstance(d, dict) or d.get('doc_type') != 'entity':
            continue
        text = d.get('text', '')
        items.append({
            'concept': d.get('canonical_name'),
            'category': d.get('category'),
            'stance': _field(text, 'Stance:'),
            'discussion_hook': _field(text, 'Discussion hook:'),
        })
        if len(items) >= max_items:
            break
    out['concepts'] = items
    return out


def load_recent_trade_decisions(max_items: int = 8) -> dict[str, Any]:
    """READ-ONLY: the trader's own recent trade decisions + outcomes from the
    decision-memory ledger (decision-index.json). Lets the trader recall what it
    recently decided and how it turned out. REFERENCE CONTEXT ONLY — MUST NOT
    feed position sizing, stop-loss, claim, or order logic. Guarded.
    """
    idx_path = MAIN_ROOT / 'runtime' / 'shared' / 'decision-index.json'
    out: dict[str, Any] = {'available': False, 'generated_at': None, 'decisions': []}
    try:
        idx = json.loads(idx_path.read_text(encoding='utf-8'))
    except Exception:
        return out
    out['available'] = True
    out['generated_at'] = idx.get('generated_at')
    decisions = idx.get('decisions') if isinstance(idx.get('decisions'), list) else []
    picks = []
    for d in decisions:
        if not isinstance(d, dict) or d.get('agent') != 'trader':
            continue
        picks.append({
            'decided_at': d.get('decided_at'),
            'kind': d.get('kind'),
            'action': d.get('action'),
            'outcome': d.get('outcome'),
            'outcome_detail': d.get('outcome_detail'),
        })
        if len(picks) >= max_items:
            break
    out['decisions'] = picks
    return out


REWARD_LINE_RE = re.compile(
    r"^\s*-\s+(?P<tick>[A-Za-z0-9_]+):\s+claimable\s+`(?P<amount>[^`]+)`\s+\|\s+price_usd\s+`(?P<price>[^`]+)`\s+\|\s+reward_value_usd\s+`(?P<usd>[^`]+)`\s+\|\s+(?P<action>[^\n]+)$",
    re.MULTILINE,
)
REWARD_LINE_ALT_RE = re.compile(
    r"^\s*-\s+(?P<tick>[A-Za-z0-9_]+):\s+claimable\s+amount=(?P<amount>[^,\n]+),\s+price_usd=(?P<price>[^,\n]+),\s+reward_value_usd=(?P<usd>[^,\n]+),\s+(?P<action>[^\n]+)$",
    re.MULTILINE,
)
REWARDS_CYCLE_TS_RE = re.compile(r"\((?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+CST\)")
WALLET_RE = re.compile(r"^-\s+Wallet(?: address)?:\s+`?([^`\n]+)`?\s*$", re.MULTILINE)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding='utf-8')


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, path)


def _snap_ts(snap: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(snap.get('ts')).replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def append_price_history(positions: list[dict[str, Any]], ts_iso: str, max_days: int = 10) -> dict[str, Any]:
    """Append a per-tick price snapshot to price-history.json (rolling window).

    The on-chain monitor does not currently populate per-tick drawdown
    (price_change_pct=0), so we accumulate our own price series to derive a
    trailing drawdown for the stop-loss trigger. Returns the post-append doc.
    """
    hist = read_json(PRICE_HISTORY_PATH) or {'version': 'v1', 'snapshots': []}
    snaps = hist.get('snapshots') if isinstance(hist.get('snapshots'), list) else []
    prices: dict[str, float] = {}
    for p in (positions or []):
        if isinstance(p, dict) and p.get('tick'):
            pu = safe_float(p.get('price_usd'))
            if pu and pu > 0:
                prices[str(p['tick'])] = pu
    if prices:
        snaps.append({'ts': ts_iso, 'prices': prices})
    cutoff = datetime.now(timezone.utc).timestamp() - max_days * 86400
    snaps = [s for s in snaps if (_snap_ts(s) is None or _snap_ts(s) >= cutoff)]
    hist['snapshots'] = snaps
    hist['updated_at'] = ts_iso
    atomic_write_json(PRICE_HISTORY_PATH, hist)
    return hist


def compute_drawdowns(history: dict[str, Any], window_hours: int = STOP_LOSS_WINDOW_HOURS) -> dict[str, float]:
    """Per-tick fractional price change (latest vs oldest in window). Negative = drawdown."""
    snaps = (history or {}).get('snapshots') or []
    if len(snaps) < 2:
        return {}
    cutoff = datetime.now(timezone.utc).timestamp() - window_hours * 3600
    in_window = [s for s in snaps if (_snap_ts(s) is not None and _snap_ts(s) >= cutoff)]
    if len(in_window) < 2:
        in_window = list(snaps)  # not enough depth yet → use whatever we have
    in_window.sort(key=lambda s: _snap_ts(s) or 0.0)
    oldest = in_window[0].get('prices') or {}
    latest = in_window[-1].get('prices') or {}
    out: dict[str, float] = {}
    for tick, new_p in latest.items():
        old_p = oldest.get(tick)
        if old_p and old_p > 0 and new_p and new_p > 0:
            out[tick] = (new_p - old_p) / old_p
    return out


def active_override_ticks(heat_data: dict[str, Any]) -> set[str]:
    """Ticks under an active owner priority_override — EXEMPT from stop-loss sells."""
    out: set[str] = set()
    po = (heat_data or {}).get('priority_override')
    candidates = po if isinstance(po, list) else ([po] if isinstance(po, dict) else [])
    for o in candidates:
        if isinstance(o, dict) and o.get('active') and o.get('tick'):
            out.add(str(o['tick']))
    return out


def execution_ledger_path(executed_at: str | None = None) -> Path:
    if executed_at:
        try:
            dt = datetime.fromisoformat(executed_at.replace('Z', '+00:00'))
            return RUNTIME / f"executions-{dt.astimezone().strftime('%Y-%m-%d')}.json"
        except Exception:
            pass
    return RUNTIME / f"executions-{datetime.now().strftime('%Y-%m-%d')}.json"


def latest_execution_ledger() -> tuple[Path | None, dict[str, Any] | None]:
    candidates = sorted(RUNTIME.glob('executions-*.json'))
    if not candidates:
        return None, None
    path = candidates[-1]
    return path, read_json(path)


def parse_reward_action(action: str | None) -> dict[str, Any]:
    text = (action or '').strip()
    status = None
    if text.startswith('failed'):
        status = 'failed'
    elif text.startswith('skipped'):
        status = 'skipped'
    elif text.startswith('claimed'):
        status = 'claimed'
    elif text.startswith('completed'):
        status = 'completed'
    elif text.startswith('ok'):
        status = 'ok'
    details: dict[str, Any] = {'status': status}
    for key in ['orderId', 'blocker', 'failure_reason']:
        m = re.search(rf"{key}=([^;|]+)", text)
        if m:
            details[key] = m.group(1).strip()
    return details


def extract_rewards_checked_at(text: str | None) -> str | None:
    if not text:
        return None
    m = REWARDS_CYCLE_TS_RE.search(text)
    if not m:
        return None
    try:
        local_tz = datetime.now().astimezone().tzinfo
        dt = datetime.strptime(m.group('ts'), '%Y-%m-%d %H:%M:%S').replace(tzinfo=local_tz)
        return dt.isoformat(timespec='seconds')
    except Exception:
        return None


def upsert_claim_entries_from_rewards(reward_items: list[dict[str, Any]], checked_at: str | None) -> None:
    entries = []
    executed_at = checked_at or now_iso()
    path = execution_ledger_path(executed_at)
    ledger = read_json(path) or {'version': 'v1', 'date': path.stem.replace('executions-', ''), 'updated_at': executed_at, 'items': []}
    items = ledger.get('items') if isinstance(ledger.get('items'), list) else []
    existing_ids = {str(item.get('id')) for item in items if isinstance(item, dict) and item.get('id')}

    for reward in reward_items:
        if not isinstance(reward, dict):
            continue
        action = reward.get('action') or ''
        details = parse_reward_action(action)
        status = reward.get('status') or details.get('status')
        order_id = reward.get('order_id') or details.get('orderId')
        blocker = reward.get('blocker') or details.get('blocker')
        failure_reason = reward.get('failure_reason') or details.get('failure_reason')
        if status not in {'failed', 'claimed', 'completed', 'skipped'}:
            continue
        tick = reward.get('tick')
        entry_id = f"order:{order_id}" if order_id else f"claim:{executed_at}:{tick}:{status}"
        if entry_id in existing_ids:
            continue
        items.append({
            'id': entry_id,
            'ts': executed_at,
            'run_id': 'backfill:rewards-claim-latest',
            'source_agent': 'trader',
            'action': 'claim',
            'tick': tick,
            'amount': reward.get('claimable_amount'),
            'amount_unit': tick,
            'usd': reward.get('reward_value_usd'),
            'raw_amount': None,
            'tx_hash': None,
            'order_id': order_id,
            'status': 'ok' if status in {'claimed', 'completed'} else status,
            'trigger_reason': blocker or failure_reason,
            'balance_before': None,
            'balance_after': None,
            'remote_route': 'tagclaw-claim',
            'approve_hash': None,
            'expected_amount': None,
            'expected_receive': None,
            'remote': {
                'ok': status in {'claimed', 'completed'},
                'response': {
                    'orderId': order_id,
                    'blocker': blocker,
                    'failure_reason': failure_reason,
                    'source': 'memory/rewards-claim-latest.json',
                },
            },
        })
        existing_ids.add(entry_id)

    ledger['version'] = 'v1'
    ledger['date'] = path.stem.replace('executions-', '')
    ledger['updated_at'] = executed_at
    ledger['items'] = items[-500:]
    atomic_write_json(path, ledger)


def summarize_execution_ledger(ledger: dict[str, Any] | None) -> dict[str, Any]:
    items = ledger.get('items') if isinstance(ledger, dict) and isinstance(ledger.get('items'), list) else []

    def is_meaningful(item: dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        return any([
            item.get('tick'),
            item.get('tx_hash'),
            item.get('order_id'),
            item.get('remote'),
            item.get('expected_amount'),
            item.get('expected_receive'),
        ])

    meaningful_items = [item for item in items if is_meaningful(item)]
    recent_operations = meaningful_items[-10:]
    last_operation = recent_operations[-1] if recent_operations else None
    severe_statuses = {'failed', 'blocked', 'partial', 'pending', 'unconfirmed'}
    soft_statuses = {'skipped'}
    last_failed_operation = next(
        (
            item for item in reversed(meaningful_items)
            if isinstance(item, dict) and item.get('status') in severe_statuses
        ),
        None,
    )
    if last_failed_operation is None:
        last_failed_operation = next(
            (
                item for item in reversed(meaningful_items)
                if isinstance(item, dict) and item.get('status') in soft_statuses
            ),
            None,
        )
    pending_or_unconfirmed = [
        item for item in meaningful_items
        if isinstance(item, dict) and (
            item.get('status') in {'partial', 'pending', 'unconfirmed'}
            or (item.get('action') == 'claim' and item.get('order_id') and item.get('status') != 'ok')
        )
    ][-10:]
    action_counts: dict[str, int] = {}
    for item in meaningful_items:
        if not isinstance(item, dict):
            continue
        action = str(item.get('action') or 'unknown')
        action_counts[action] = action_counts.get(action, 0) + 1
    return {
        'count': len(meaningful_items),
        'recent_operations': recent_operations,
        'last_operation': last_operation,
        'last_failed_operation': last_failed_operation,
        'pending_or_unconfirmed_orders': pending_or_unconfirmed,
        'action_counts': action_counts,
    }


def normalize_status(value: str | None, default: str = 'stale') -> str:
    if value in {'ok', 'partial', 'blocked', 'stale'}:
        return value
    if value in {'error', 'failed', 'fail'}:
        return 'blocked'
    return default


def safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def compute_cost_basis(runtime_dir: Path) -> dict[str, Any]:
    """Compute per-tick cost basis from all execution ledgers.

    Scans buy and claim entries across all daily ledger files.
    Computes weighted-average cost per token for each tick.
    """
    ledgers = sorted(runtime_dir.glob('executions-*.json'))
    # Accumulate: tick -> { total_usd_spent, total_tokens_acquired, entries }
    basis: dict[str, dict[str, Any]] = {}

    for ledger_path in ledgers:
        data = read_json(ledger_path)
        if not isinstance(data, dict):
            continue
        for item in data.get('items') or []:
            if not isinstance(item, dict):
                continue
            status = item.get('status')
            if status not in ('ok', 'claimed', 'completed'):
                continue
            action = item.get('action') or item.get('type')
            tick = item.get('tick')
            if not tick:
                continue

            usd = safe_float(item.get('usd'))
            amount = safe_float(item.get('amount'))

            if action == 'buy' and usd is not None and usd > 0:
                entry = basis.setdefault(tick, {'total_usd': 0.0, 'total_tokens': 0.0, 'buy_count': 0, 'claim_count': 0})
                entry['total_usd'] += usd
                if amount and amount > 0:
                    entry['total_tokens'] += amount
                entry['buy_count'] += 1

            elif action == 'claim' and usd is not None and usd > 0:
                entry = basis.setdefault(tick, {'total_usd': 0.0, 'total_tokens': 0.0, 'buy_count': 0, 'claim_count': 0})
                # Claims are "free" tokens — cost basis = 0, but we track the USD value at claim time
                if amount and amount > 0:
                    entry['total_tokens'] += amount
                entry['claim_count'] += 1

    # Also scan treasury-history for sells (to track realized P&L later)
    treasury_history = read_json(MAIN_ROOT / 'runtime' / 'shared' / 'treasury-history.json') or {}
    sell_usd_total: dict[str, float] = {}
    for item in treasury_history.get('items') or []:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'buy' and item.get('result_status') == 'ok':
            tick = item.get('tick')
            usd = safe_float(item.get('buy_usd'))
            if tick and usd and usd > 0:
                entry = basis.setdefault(tick, {'total_usd': 0.0, 'total_tokens': 0.0, 'buy_count': 0, 'claim_count': 0})
                # Avoid double-counting: only add if ledger didn't already capture it
                # Use a simple heuristic: if buy_count from ledgers is 0 for this tick, add from history
                # This is conservative — better to slightly undercount than double-count
        if item.get('type') == 'sell' and item.get('result_status') == 'ok':
            tick = item.get('tick')
            usd = safe_float(item.get('sell_usd'))
            if tick and usd:
                sell_usd_total[tick] = sell_usd_total.get(tick, 0.0) + usd

    ticks_data: dict[str, Any] = {}
    for tick, entry in basis.items():
        avg_cost = (entry['total_usd'] / entry['total_tokens']) if entry['total_tokens'] > 0 and entry['total_usd'] > 0 else 0.0
        ticks_data[tick] = {
            'total_usd_spent': round(entry['total_usd'], 8),
            'total_tokens_acquired': round(entry['total_tokens'], 8),
            'avg_cost_per_token': round(avg_cost, 12),
            'buy_count': entry['buy_count'],
            'claim_count': entry['claim_count'],
            'total_sold_usd': round(sell_usd_total.get(tick, 0.0), 8),
        }

    has_any_buy = any(t.get('buy_count', 0) > 0 for t in ticks_data.values())
    has_any_tokens = any(t.get('total_tokens_acquired', 0) > 0 for t in ticks_data.values())
    quality = 'ok' if has_any_buy else ('partial' if has_any_tokens else 'missing')

    return {
        'version': 'v1',
        'source_class': 'trader-native',
        'generated_at': now_iso(),
        'ticks': ticks_data,
        'quality': quality,
        'notes': 'Cost basis computed from execution ledgers. Claims counted as zero-cost acquisition.',
    }


def compute_portfolio_baseline(
    *,
    bundle_ts: str,
    run_id: str,
    wallet_address: str | None,
    balances: dict[str, Any],
    onchain: dict[str, Any],
    claimable_total: float,
) -> dict[str, Any]:
    positions = onchain.get('positions') if isinstance(onchain.get('positions'), list) else []
    by_tick = {str(p.get('tick')): p for p in positions if isinstance(p, dict) and p.get('tick')}
    assets: dict[str, Any] = {}
    for tick, raw_balance in balances.items():
        bal = safe_float(raw_balance)
        pos = by_tick.get(str(tick), {})
        price_usd = safe_float(pos.get('price_usd')) if pos else None
        value_usd = safe_float(pos.get('value_usd')) if pos else None
        assets[str(tick)] = {
            'balance': bal,
            'balance_raw': raw_balance,
            'price_usd': price_usd,
            'value_usd': value_usd,
            'price_trend': pos.get('price_trend') if pos else None,
        }

    known_value = round(sum(v.get('value_usd') or 0.0 for v in assets.values() if isinstance(v, dict)), 6)
    return {
        'version': 'v1',
        'source_class': 'trader-native',
        'captured_at': bundle_ts,
        'run_id': run_id,
        'wallet_address': wallet_address,
        'assets': assets,
        'portfolio_value_usd_known': known_value,
        'reward_claimable_usd_total': round(float(claimable_total or 0.0), 8),
        'notes': 'Canonical trader portfolio baseline built from wallet snapshot + onchain marks + reward snapshot.',
    }


def compute_portfolio_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    prev_assets = (previous or {}).get('assets') or {}
    curr_assets = current.get('assets') or {}
    ticks = sorted(set(curr_assets.keys()) | set(prev_assets.keys()))
    asset_deltas: dict[str, Any] = {}
    for tick in ticks:
        cur = curr_assets.get(tick) or {}
        prev = prev_assets.get(tick) or {}
        asset_deltas[tick] = {
            'balance_delta': round((safe_float(cur.get('balance')) or 0.0) - (safe_float(prev.get('balance')) or 0.0), 12),
            'value_usd_delta': round((safe_float(cur.get('value_usd')) or 0.0) - (safe_float(prev.get('value_usd')) or 0.0), 6),
        }
    return {
        'version': 'v1',
        'source_class': 'trader-native',
        'generated_at': current.get('captured_at'),
        'run_id': current.get('run_id'),
        'previous_baseline_ref': 'runtime/trader/portfolio-baseline.json' if previous else None,
        'status': 'ok' if previous else 'partial',
        'portfolio_value_usd_delta': round((safe_float(current.get('portfolio_value_usd_known')) or 0.0) - (safe_float((previous or {}).get('portfolio_value_usd_known')) or 0.0), 6),
        'reward_claimable_usd_delta': round((safe_float(current.get('reward_claimable_usd_total')) or 0.0) - (safe_float((previous or {}).get('reward_claimable_usd_total')) or 0.0), 8),
        'asset_deltas': asset_deltas,
        'notes': 'Delta versus the previous canonical trader baseline.',
    }


def compute_measurement_quality(
    *,
    bundle_ts: str,
    run_id: str,
    balances: dict[str, Any],
    reward_items: list[dict[str, Any]],
    onchain: dict[str, Any],
    execution_summary: dict[str, Any],
    previous_baseline: dict[str, Any] | None,
    cost_basis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    positions = onchain.get('positions') if isinstance(onchain.get('positions'), list) else []
    priced_positions = sum(1 for p in positions if isinstance(p, dict) and safe_float(p.get('price_usd')) is not None)
    tracked_positions = len(positions)
    wallet_visibility = 'ok' if balances else 'blocked'
    price_visibility = 'ok' if tracked_positions > 0 and priced_positions == tracked_positions else ('partial' if priced_positions > 0 else 'blocked')
    reward_visibility = 'ok' if reward_items else 'partial'
    # Cost basis quality: derived from cost-basis.json
    cost_basis_quality = (cost_basis or {}).get('quality', 'missing')
    if cost_basis_quality not in ('ok', 'partial', 'missing'):
        cost_basis_quality = 'missing'
    baseline_continuity = 'ok' if previous_baseline else 'partial'
    execution_evidence_quality = 'ok' if (execution_summary.get('count') or 0) > 0 else 'partial'

    score = 0.0
    score += 0.25 if wallet_visibility == 'ok' else 0.0
    score += 0.25 if price_visibility == 'ok' else (0.15 if price_visibility == 'partial' else 0.0)
    score += 0.15 if reward_visibility == 'ok' else 0.05
    score += 0.15 if execution_evidence_quality == 'ok' else 0.05
    score += 0.1 if baseline_continuity == 'ok' else 0.03
    score += 0.1 if cost_basis_quality == 'ok' else (0.05 if cost_basis_quality == 'partial' else 0.0)
    overall_confidence = round(min(1.0, score), 4)

    if wallet_visibility == 'blocked' or price_visibility == 'blocked':
        overall_status = 'blocked'
    elif cost_basis_quality == 'missing' or baseline_continuity != 'ok' or price_visibility != 'ok':
        overall_status = 'partial'
    else:
        overall_status = 'ok'

    actionability = 'observe_only' if overall_status != 'ok' else 'full'
    return {
        'version': 'v1',
        'source_class': 'trader-native',
        'generated_at': bundle_ts,
        'run_id': run_id,
        'wallet_visibility': wallet_visibility,
        'price_visibility': price_visibility,
        'reward_visibility': reward_visibility,
        'cost_basis_quality': cost_basis_quality,
        'baseline_continuity': baseline_continuity,
        'execution_evidence_quality': execution_evidence_quality,
        'overall_status': overall_status,
        'overall_confidence': overall_confidence,
        'actionability': actionability,
        'cost_basis_ref': 'runtime/trader/cost-basis.json',
        'notes': 'Measurement quality gate for canonical trader baseline / portfolio delta / TAS_trade interpretation.',
    }


def build_metric_strategy_loop(
    metric_name: str,
    current_value: float | None,
    previous_value: float | None,
    current_status: str,
    previous_status: str | None = None,
    previous_strategy: str | None = None,
    previous_reason: str | None = None,
) -> dict[str, Any]:
    previous_status = previous_status or 'unknown'
    delta = round(current_value - previous_value, 6) if current_value is not None and previous_value is not None else None
    if current_value is None or previous_value is None:
        trend = 'blocked' if ('blocked' in {current_status, previous_status}) else 'partial'
    elif abs(delta or 0.0) < 1e-9:
        trend = 'flat'
    elif (delta or 0.0) > 0:
        trend = 'improved'
    else:
        trend = 'declined'

    if trend == 'improved':
        strategy_action = 'reinforce_previous_strategy'
        planning_focus = f'{metric_name} improved; reinforce the previous execution policy.'
    elif trend == 'declined':
        strategy_action = 'discard_previous_strategy'
        planning_focus = f'{metric_name} declined; discard the previous execution policy and change approach.'
    else:
        strategy_action = 'conservative_explore'
        planning_focus = f'{metric_name} is {trend}; stay conservative and repair measurement / evidence quality before repeating the old policy.'

    return {
        'metric': metric_name,
        'current_value': current_value,
        'previous_value': previous_value,
        'delta': delta,
        'current_status': current_status,
        'previous_status': previous_status,
        'trend': trend,
        'strategy_action': strategy_action,
        'planning_focus': planning_focus,
        'rule': {
            'improved': 'reinforce_previous_strategy',
            'declined': 'discard_previous_strategy',
            'flat_or_partial_or_blocked': 'conservative_explore',
        },
        'previous_strategy': previous_strategy,
        'previous_reason': previous_reason,
    }


def parse_markdown_balances(text: str | None) -> tuple[str | None, dict[str, str]]:
    wallet = None
    balances: dict[str, str] = {}
    if not text:
        return wallet, balances
    wallet_match = WALLET_RE.search(text)
    if wallet_match:
        wallet = wallet_match.group(1)
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('## Balances'):
            section = 'balances'
            continue
        if stripped.startswith('## '):
            section = None
            continue
        m = re.match(r"^-\s+([A-Za-z0-9_]+):\s+`?([^`]+?)`?\s*$", stripped)
        if m and section == 'balances':
            balances[m.group(1)] = m.group(2)
    return wallet, balances


def parse_reward_items(text: str | None) -> tuple[list[dict[str, Any]], float | None, bool]:
    if not text:
        return [], None, False
    reward_items: list[dict[str, Any]] = []
    seen_ticks: set[str] = set()
    claimable_total = 0.0
    claim_recommended = False

    for regex in (REWARD_LINE_RE, REWARD_LINE_ALT_RE):
        for m in regex.finditer(text):
            tick = m.group('tick')
            if tick in seen_ticks:
                continue
            try:
                usd_num = float(m.group('usd'))
                claimable_total += usd_num
                if usd_num > 2:
                    claim_recommended = True
            except ValueError:
                usd_num = None
            reward_items.append({
                'tick': tick,
                'claimable_amount': m.group('amount').strip(),
                'price_usd': m.group('price').strip(),
                'reward_value_usd': usd_num,
                'action': m.group('action').strip(),
            })
            seen_ticks.add(tick)

    return reward_items, (round(claimable_total, 8) if reward_items else None), claim_recommended


def normalize_reward_items_from_json(data: dict[str, Any] | None) -> tuple[list[dict[str, Any]], float | None, bool, str | None, str | None]:
    if not isinstance(data, dict):
        return [], None, False, None, None
    items = data.get('claimable') if isinstance(data.get('claimable'), list) else data.get('results')
    if not isinstance(items, list):
        return [], None, False, None, None
    reward_items: list[dict[str, Any]] = []
    claimable_total = 0.0
    claim_recommended = False
    for item in items:
        if not isinstance(item, dict):
            continue
        usd_num = safe_float(item.get('reward_value_usd'))
        if usd_num is not None:
            claimable_total += usd_num
            if usd_num > 2:
                claim_recommended = True
        status = item.get('status')
        action_parts = [str(status or 'unknown')]
        if item.get('order_id') or item.get('orderId'):
            action_parts.append(f"orderId={item.get('order_id') or item.get('orderId')}")
        if item.get('blocker'):
            action_parts.append(f"blocker={item.get('blocker')}")
        if item.get('failure_reason'):
            action_parts.append(f"failure_reason={item.get('failure_reason')}")
        reward_items.append({
            'tick': item.get('tick'),
            'claimable_amount': item.get('claimable_amount'),
            'price_usd': item.get('price_usd'),
            'price_source': item.get('price_source'),
            'reward_value_usd': usd_num,
            'status': status,
            'blocker': item.get('blocker'),
            'order_id': item.get('order_id') or item.get('orderId'),
            'failure_reason': item.get('failure_reason'),
            'claim_response': item.get('claim_response'),
            'final_status': item.get('final_status'),
            'final_status_response': item.get('final_status_response'),
            'action': ' | '.join(action_parts),
        })
    checked_at = data.get('checked_at') or data.get('at')
    checked_at_iso = data.get('checked_at_iso')
    if checked_at_iso is None and isinstance(checked_at, str) and checked_at.endswith(' CST'):
        try:
            local_tz = datetime.now().astimezone().tzinfo
            checked_at_iso = datetime.strptime(checked_at.replace(' CST', ''), '%Y-%m-%d %H:%M:%S').replace(tzinfo=local_tz).isoformat(timespec='seconds')
        except Exception:
            checked_at_iso = None
    return reward_items, (round(claimable_total, 8) if reward_items else None), claim_recommended, checked_at, checked_at_iso


def build_rewards_latest_json(reward_items: list[dict[str, Any]], checked_at_iso: str | None, checked_at_label: str | None, source_kind: str) -> dict[str, Any]:
    claimable_total = 0.0
    claim_recommended = False
    normalized_items = []
    for item in reward_items:
        if not isinstance(item, dict):
            continue
        usd_num = safe_float(item.get('reward_value_usd'))
        if usd_num is not None:
            claimable_total += usd_num
            if usd_num > 2:
                claim_recommended = True
        normalized_items.append({
            'tick': item.get('tick'),
            'claimable_amount': item.get('claimable_amount'),
            'price_usd': item.get('price_usd'),
            'price_source': item.get('price_source'),
            'reward_value_usd': usd_num,
            'status': item.get('status') or parse_reward_action(item.get('action')).get('status'),
            'blocker': item.get('blocker') or parse_reward_action(item.get('action')).get('blocker'),
            'order_id': item.get('order_id') or parse_reward_action(item.get('action')).get('orderId'),
            'failure_reason': item.get('failure_reason') or parse_reward_action(item.get('action')).get('failure_reason'),
            'claim_response': item.get('claim_response'),
            'final_status': item.get('final_status'),
            'final_status_response': item.get('final_status_response'),
            'action': item.get('action'),
        })
    return {
        'version': 'v1',
        'checked_at_iso': checked_at_iso,
        'checked_at': checked_at_label,
        'source_kind': source_kind,
        'claimable': normalized_items,
        'claimable_usd_total': round(claimable_total, 8) if normalized_items else None,
        'claim_recommended': claim_recommended if normalized_items else None,
    }


def _is_authoritative_rewards_json(data: dict[str, Any] | None) -> bool:
    """A rewards JSON is authoritative if it has a valid checked_at_iso, even when claimable is empty.

    This prevents stale tmp data from overriding a fresh check that found nothing claimable.
    """
    if not isinstance(data, dict):
        return False
    checked = data.get('checked_at_iso') or data.get('checked_at') or data.get('at')
    return bool(checked)


def _parse_checked_at_iso(ts: str | None) -> datetime | None:
    """Best-effort parse of a checked_at_iso string to datetime for freshness comparison."""
    if not ts:
        return None
    normalized = ts.replace(' CST', ' +0800')
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S %z', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(normalized, fmt)
        except Exception:
            continue
    return None


def load_rewards_claim_data(memory: Path) -> tuple[list[dict[str, Any]], float | None, bool, str | None, str | None, str]:
    rewards_json_path = memory / 'rewards-claim-latest.json'
    rewards_json = read_json(rewards_json_path)
    reward_items, claimable_total, claim_recommended, checked_at_label, checked_at_iso = normalize_reward_items_from_json(rewards_json)
    # P0 2026-04-18: A fresh memory JSON with valid checked_at_iso is authoritative
    # even when claimable is empty — do NOT fall back to stale tmp.
    if reward_items or _is_authoritative_rewards_json(rewards_json):
        return reward_items, claimable_total, claim_recommended, checked_at_label, checked_at_iso, 'memory-json'

    # Tier 2: tmp fallback — only use if fresher than memory JSON (or memory missing)
    tmp_json = read_json(TRADER_ROOT / 'tmp' / 'last_rewards_claim_cycle.json')
    tmp_items, tmp_total, tmp_recommended, tmp_label, tmp_iso = normalize_reward_items_from_json(tmp_json)
    if tmp_items:
        # Freshness gate: skip stale tmp if memory JSON had a newer checked_at
        mem_ts = _parse_checked_at_iso(checked_at_iso)
        tmp_ts = _parse_checked_at_iso(tmp_iso)
        if mem_ts and tmp_ts and tmp_ts < mem_ts:
            # Memory is fresher — return empty authoritative result, not stale tmp
            return reward_items, claimable_total, claim_recommended, checked_at_label, checked_at_iso, 'memory-json'
        return tmp_items, tmp_total, tmp_recommended, tmp_label, tmp_iso, 'tmp-json'

    rewards_md = read_text(memory / 'rewards-claim-latest.md')
    reward_items, claimable_total, claim_recommended = parse_reward_items(rewards_md)
    checked_at_iso = extract_rewards_checked_at(rewards_md)
    return reward_items, claimable_total, claim_recommended, None, checked_at_iso, 'markdown-fallback'


# P2 2026-04-03: Portfolio value score based on USD delta vs baseline.
def compute_portfolio_value_score(delta_doc: dict[str, Any], baseline_doc: dict[str, Any]) -> float:
    """Score 0.0–0.7 based on portfolio USD change relative to total_value_usd.

    Returns:
        0.7  if delta > 0          (portfolio grew)
        0.5  if delta == 0         (flat)
        0.4  if -10% < delta < 0   (mild loss)
        0.2  if delta <= -10%      (significant loss)
    """
    delta_usd = safe_float(delta_doc.get('portfolio_value_usd_delta')) or 0.0
    total_usd = safe_float(baseline_doc.get('portfolio_value_usd_known')) or 0.0
    if total_usd == 0.0:
        return 0.5
    delta_pct = delta_usd / total_usd
    if delta_pct > 0:
        return 0.7
    elif delta_pct == 0.0:
        return 0.5
    elif delta_pct > -0.10:
        return 0.4
    else:
        return 0.2


# P2 2026-04-03: Credit rank score based on TagClaw balance vs CREDIT_RANK_BASELINE.
def compute_credit_rank_score(wallet_snapshot: dict[str, Any]) -> float:
    """Score 0.0–1.0: holding CREDIT_RANK_BASELINE TagClaw tokens = full score."""
    balances = wallet_snapshot.get('balances') or {}
    tagclaw_balance = safe_float(balances.get('TagClaw')) or 0.0
    return round(min(1.0, tagclaw_balance / CREDIT_RANK_BASELINE), 4)


# P0 2026-04-09: TAS_trade v3 — linear normalized USD formula
def compute_tas_trade_v3(portfolio_usd: float, claimable_usd: float) -> float:
    """TAS_trade = 0.9 × min(portfolio_usd/PORTFOLIO_TARGET, 1.0) + 0.1 × min(claimable_usd/CLAIM_TARGET, 1.0)"""
    portfolio_norm = min(portfolio_usd / PORTFOLIO_TARGET, 1.0) if PORTFOLIO_TARGET > 0 else 0.0
    claimable_norm = min(claimable_usd / CLAIM_TARGET, 1.0) if CLAIM_TARGET > 0 else 0.0
    return round(0.9 * portfolio_norm + 0.1 * claimable_norm, 4)


def main() -> int:
    bundle_ts = now_iso()
    run_id = f"trader-v2-{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    memory = TRADER_ROOT / 'memory'
    tas_trade_src = read_json(memory / 'tas-trade-latest.json') or {}
    wallet_md = read_text(memory / 'wallet-balance-latest.md')
    execution_record = read_json(RUNTIME / 'execution-record.json') or {}
    previous_wallet_snapshot = read_json(RUNTIME / 'wallet-snapshot.json') or {}
    previous_portfolio_baseline = read_json(RUNTIME / 'portfolio-baseline.json') or {}
    previous_tas_trade_doc = read_json(RUNTIME / 'tas-trade.json') or {}
    previous_latest = read_json(RUNTIME / 'latest.json') or {}

    # Wiki-first data loading
    wiki_trending_ticks = load_wiki_trending_ticks(n=5)
    wiki_credit_context = load_wiki_execution_brief_credit()
    wiki_platform_available = is_wiki_platform_fresh()
    wiki_brief_available = bool(wiki_credit_context)

    # P1-2: Community heat signal
    heat_data = load_community_heat()
    heat_available = heat_data.get('heat_available', False)

    computed_at = tas_trade_src.get('computed_at') or bundle_ts
    generated_at = computed_at if computed_at else bundle_ts
    status = normalize_status(tas_trade_src.get('status'), default='stale')
    wallet_address, balances = parse_markdown_balances(wallet_md)

    reward_items, claimable_total, claim_recommended, rewards_checked_at_label, rewards_checked_at_iso, rewards_source_kind = load_rewards_claim_data(memory)
    rewards_checked_at = rewards_checked_at_iso or generated_at
    atomic_write_json(memory / 'rewards-claim-latest.json', build_rewards_latest_json(
        reward_items,
        rewards_checked_at,
        rewards_checked_at_label,
        rewards_source_kind,
    ))
    upsert_claim_entries_from_rewards(reward_items, rewards_checked_at)
    ledger_path, execution_ledger = latest_execution_ledger()
    execution_summary = summarize_execution_ledger(execution_ledger)

    risk_flags = []
    if status == 'partial':
        risk_flags.append('tas_trade_partial')
    for blocker in (tas_trade_src.get('blockers') or [])[:9]:
        risk_flags.append(str(blocker))

    # Write all outputs
    # Compute net_value_usdt from onchain positions (fallback to 'unavailable')
    _onchain_pre = read_json(RUNTIME / 'onchain-positions.json') or {}
    _net_value_usdt = _onchain_pre.get('total_portfolio_usd')
    if _net_value_usdt is None:
        _net_value_usdt = 'unavailable'
    atomic_write_json(RUNTIME / 'wallet-snapshot.json', {
        'version': 'v2', 'updated_at': bundle_ts, 'source_class': 'trader-native',
        'bundle_ts': bundle_ts, 'run_id': run_id,
        'status': 'ok' if balances else 'blocked',
        'wallet_address': wallet_address, 'balances': balances,
        'net_value_usdt': _net_value_usdt,
        'net_value_source': 'onchain-positions' if isinstance(_net_value_usdt, (int, float)) else 'unavailable',
        'notes': 'trader self-published V2',
    })
    atomic_write_json(RUNTIME / 'reward-status.json', {
        'version': 'v2', 'updated_at': bundle_ts, 'source_class': 'trader-native',
        'bundle_ts': bundle_ts, 'run_id': run_id,
        'status': normalize_status(status, default='stale'),
        'checked_at': rewards_checked_at,
        'source_kind': rewards_source_kind,
        'claimable': reward_items,
        'claimable_usd_total': claimable_total,
        'notes': 'trader self-published V2 (JSON-first rewards source)',
    })
    # P5+ 2026-03-25: Run on-chain position monitor before computing TAS_trade.
    # This fetches real-time BSC prices via onchainos CLI and writes onchain-positions.json.
    try:
        import subprocess as _sp
        _monitor = TRADER_ROOT / 'scripts' / 'monitor_onchain_positions.py'
        _proc = _sp.run(['python3', str(_monitor)], capture_output=True, text=True, timeout=45)
        if _proc.returncode == 0:
            _monitor_result = json.loads(_proc.stdout.strip())
        else:
            _monitor_result = {}
    except Exception:
        _monitor_result = {}

    # Use onchain monitor holding_trend if available (more accurate than balance delta)
    _onchain = read_json(RUNTIME / 'onchain-positions.json') or {}
    _onchain_trend = _onchain.get('holding_trend')
    _onchain_trend_score = _onchain.get('holding_trend_score')

    # Refresh wallet snapshot with post-monitor balances / USD totals when available.
    _onchain_positions = _onchain.get('positions') if isinstance(_onchain.get('positions'), list) else []
    _onchain_balances = {
        str(p.get('tick')): str(p.get('balance'))
        for p in _onchain_positions if isinstance(p, dict) and p.get('tick')
    }
    if _onchain_balances:
        balances = _onchain_balances
    _net_value_post = _onchain.get('total_portfolio_usd')
    atomic_write_json(RUNTIME / 'wallet-snapshot.json', {
        'version': 'v2', 'updated_at': bundle_ts, 'source_class': 'trader-native',
        'bundle_ts': bundle_ts, 'run_id': run_id,
        'status': 'ok' if balances else 'blocked',
        'wallet_address': wallet_address, 'balances': balances,
        'net_value_usdt': _net_value_post if _net_value_post is not None else _net_value_usdt,
        'net_value_source': 'onchain-positions' if (_net_value_post is not None or isinstance(_net_value_usdt, (int, float))) else 'unavailable',
        'notes': 'trader self-published V2 (post-monitor refresh)',
    })

    portfolio_baseline = compute_portfolio_baseline(
        bundle_ts=bundle_ts,
        run_id=run_id,
        wallet_address=wallet_address,
        balances=balances,
        onchain=_onchain,
        claimable_total=claimable_total,
    )
    portfolio_delta = compute_portfolio_delta(portfolio_baseline, previous_portfolio_baseline)
    cost_basis = compute_cost_basis(RUNTIME)
    atomic_write_json(RUNTIME / 'cost-basis.json', cost_basis)
    measurement_quality = compute_measurement_quality(
        bundle_ts=bundle_ts,
        run_id=run_id,
        balances=balances,
        reward_items=reward_items,
        onchain=_onchain,
        execution_summary=execution_summary,
        previous_baseline=previous_portfolio_baseline,
        cost_basis=cost_basis,
    )
    atomic_write_json(RUNTIME / 'portfolio-baseline.json', portfolio_baseline)
    atomic_write_json(RUNTIME / 'portfolio-delta.json', portfolio_delta)
    atomic_write_json(RUNTIME / 'measurement-quality.json', measurement_quality)
    if measurement_quality.get('overall_status') != 'ok':
        risk_flags.append(f"measurement_quality_{measurement_quality.get('overall_status')}")
    if measurement_quality.get('cost_basis_quality') != 'ok':
        risk_flags.append('cost_basis_missing')
    if measurement_quality.get('actionability') == 'observe_only':
        risk_flags.append('observe_only_due_to_measurement')

    # claim_history_score: based on successful claims from execution ledgers (not reward_items).
    # reward_items only shows currently-claimable rewards; already-claimed ticks disappear from it.
    # The execution ledger is the canonical record of what actually happened.
    # Only count real failures (status='failed'), NOT 'blocked' or 'skipped' —
    # those are below-minimum-threshold rejections, not execution failures.
    claimed_count = 0
    failed_count = 0
    for ledger_path in sorted(RUNTIME.glob('executions-*.json')):
        ledger_data = read_json(ledger_path)
        if not isinstance(ledger_data, dict):
            continue
        for item in ledger_data.get('items') or []:
            if not isinstance(item, dict) or item.get('action') != 'claim':
                continue
            s = item.get('status', '')
            if s in ('ok', 'claimed', 'completed'):
                claimed_count += 1
            elif s == 'failed':
                # Only count genuine execution failures, not skipped/blocked (below threshold)
                failed_count += 1
    claim_history_score = min(1.0, claimed_count * 0.3 - failed_count * 0.1)
    claim_history_score = max(0.0, claim_history_score)

    # holding_trend_score: compare current balances to previous snapshot
    prev_snapshot = previous_wallet_snapshot or {}
    prev_balances = prev_snapshot.get('balances') or {}
    tracked_ticks = ['TagClaw', 'BUIDL', 'TTAI']
    growing, stable, declining = 0, 0, 0
    for tick in tracked_ticks:
        cur = safe_float(balances.get(tick)) or 0.0
        prev = safe_float(prev_balances.get(tick)) or 0.0
        if cur > prev * 1.01:
            growing += 1
        elif cur < prev * 0.99:
            declining += 1
        else:
            stable += 1
    # Prefer onchain monitor result (real price data) over balance delta
    if _onchain_trend and _onchain_trend != 'unknown':
        holding_trend = _onchain_trend
        holding_trend_score = _onchain_trend_score or 0.1
    elif growing > declining:
        holding_trend = 'growing'
        holding_trend_score = 0.3
    elif declining > growing:
        holding_trend = 'declining'
        holding_trend_score = 0.0
    else:
        holding_trend = 'stable'
        holding_trend_score = 0.1

    # Extended TAS_trade: P2 formula — portfolio_value×0.7 + credit_rank×0.3
    # (claim_history / holding_trend retained as dashboard fields but excluded from formula)
    base_tas = safe_float(tas_trade_src.get('value')) or 0.0
    portfolio_value_score = compute_portfolio_value_score(portfolio_delta, portfolio_baseline)
    credit_rank_score = compute_credit_rank_score({'balances': balances})
    tas_trade_extended = round(min(1.0,
        portfolio_value_score * 0.7 +
        credit_rank_score * 0.3
    ), 4)

    # P0 2026-04-09: TAS_trade v3 — normalized USD formula
    _onchain_total_usd = safe_float(_onchain.get('total_portfolio_usd'))
    if _onchain_total_usd is None:
        _onchain_total_usd = safe_float(portfolio_baseline.get('portfolio_value_usd_known')) or 0.0
    _claimable_usd_for_v3 = safe_float(claimable_total) or 0.0
    tas_trade_v3 = compute_tas_trade_v3(_onchain_total_usd, _claimable_usd_for_v3)
    _portfolio_usd_norm = round(min(_onchain_total_usd / PORTFOLIO_TARGET, 1.0), 4) if PORTFOLIO_TARGET > 0 else 0.0
    _claimable_usd_norm = round(min(_claimable_usd_for_v3 / CLAIM_TARGET, 1.0), 4) if CLAIM_TARGET > 0 else 0.0

    # Detect degraded onchain snapshot: if the live total_portfolio_usd drops >60%
    # from the known baseline, the onchain monitor likely returned incomplete data
    # (e.g. only BNB priced, other tokens at 0).  Mark status as 'degraded' so
    # downstream consumers (Main TAS history) can filter it out.
    _baseline_usd = safe_float(portfolio_baseline.get('portfolio_value_usd_known'))
    _onchain_degraded = (
        _baseline_usd is not None
        and _baseline_usd > 0
        and _onchain_total_usd < _baseline_usd * 0.4
    )
    if _onchain_degraded:
        tas_trade_status = 'degraded'
    else:
        tas_trade_status = measurement_quality.get('overall_status') or ('ok' if tas_trade_v3 is not None else normalize_status(tas_trade_src.get('status'), default='stale'))
    # P2 2026-04-10: explicit history eligibility — only 'ok' TAS_trade measurements
    # are canonical.  Downstream main runtime uses this to guard TAS history.
    tas_trade_history_eligible = tas_trade_status == 'ok'
    trader_strategy_loop = build_metric_strategy_loop(
        'TAS_trade',
        tas_trade_v3,
        safe_float(previous_tas_trade_doc.get('value')),
        tas_trade_status,
        normalize_status(previous_tas_trade_doc.get('status'), default='stale'),
        previous_tas_trade_doc.get('strategy_action') or previous_tas_trade_doc.get('autonomy_mode'),
        previous_tas_trade_doc.get('autonomy_reason') or previous_tas_trade_doc.get('notes'),
    )

    # Trader autonomy intent based on TAS_trade
    # Read Main guidance (if available) — overrides defaults when present
    main_guidance_doc = read_json(MAIN_ROOT / 'runtime' / 'main' / 'trader-guidance.json') or {}
    main_guidance = main_guidance_doc.get('guidance') or {}
    guidance_mode = main_guidance_doc.get('experiment_mode', 'baseline')

    # Apply guidance overrides to trading parameters
    g_claim_threshold = min(2.0, safe_float(main_guidance.get('claim_threshold_usd')) or 2.0)  # P0: hard cap at $2
    g_claim_patience = str(main_guidance.get('claim_patience') or 'standard')
    g_claim_freq_mode = str(main_guidance.get('claim_frequency_mode') or 'standard')
    g_portfolio_tick = str(main_guidance.get('portfolio_target_tick') or 'auto')
    g_focus_action = str(main_guidance.get('focus_action') or 'claim_priority')
    g_risk_mode = str(main_guidance.get('risk_mode') or 'standard')

    # P6: Read Main dispatch-config treasury guardrails for Trader autonomy decisions
    dispatch_config = read_json(MAIN_ROOT / 'runtime' / 'shared' / 'dispatch-config.json') or {}
    treasury_gate = dispatch_config.get('treasury') or {}
    trading_cfg = treasury_gate.get('trading') or {}
    sell_triggers = trading_cfg.get('sell_triggers') or {}

    # Read TAS_social from Bookmarker (sell trigger uses social signal)
    bookmarker_tas_social = read_json(MAIN_ROOT / 'runtime' / 'bookmarker' / 'tas-social.json') or {}
    tas_social_val = safe_float(bookmarker_tas_social.get('value')) or 0.0

    # Read onchain concentration for sell trigger
    onchain = read_json(RUNTIME / 'onchain-positions.json') or {}
    concentration_val = safe_float((onchain.get('concentration') or {}).get('value')) or 0.0

    # Sell trigger evaluation (mirrors execute_treasury_policy_v2.py logic)
    sell_trigger_social = tas_social_val < (safe_float(sell_triggers.get('tas_social_below')) or 0.3)
    sell_trigger_concentration = concentration_val > (safe_float(sell_triggers.get('reward_concentration_above')) or 0.6)
    sell_triggered = sell_trigger_social or sell_trigger_concentration

    # P1 risk-management stop-loss inputs: accumulate price history → trailing
    # drawdown, and read the owner priority_override exemption set (BUIDL).
    price_history = append_price_history((onchain.get('positions') or []), bundle_ts)
    drawdowns_3d = compute_drawdowns(price_history, STOP_LOSS_WINDOW_HOURS)
    override_ticks = active_override_ticks(heat_data)
    stop_loss_ticks: list[dict[str, Any]] = []  # filled after heat_sell_candidates is known

    # Guardrails from dispatch-config
    max_budget_usd = safe_float(trading_cfg.get('max_budget_usd')) or 2.0
    max_sell_usd = safe_float(trading_cfg.get('max_sell_usd')) or 2.0
    max_trades_per_day = int(trading_cfg.get('max_trades_per_day') or 2)
    min_claimable_usd = safe_float(treasury_gate.get('min_claimable_usd')) or 1.0
    allow_trading = bool(treasury_gate.get('allow_trading', True))
    allow_claims = bool(treasury_gate.get('allow_claims', True))

    # P1-4C: Read treasury-policy coupling from main (align_event from bookmarker)
    treasury_policy_doc = read_json(MAIN_ROOT / 'runtime' / 'main' / 'treasury-policy.json') or {}
    tp_payload = treasury_policy_doc.get('payload') or {}
    tp_coupling = tp_payload.get('coupling') or {}
    coupling_active = bool(tp_coupling.get('align_event_active', False))
    coupling_source = tp_coupling.get('coupling_source', 'none')
    rebalance_allowed = bool(tp_payload.get('rebalance_allowed', False))

    # If coupling active, override claim threshold to accelerate claims
    if coupling_active:
        coupling_claim_threshold = safe_float(tp_coupling.get('recommended_claim_threshold_usd')) or 2.0
        g_claim_threshold_override = min(g_claim_threshold, coupling_claim_threshold)
    else:
        g_claim_threshold_override = None

    # Autonomy decision: TAS_trade gate + dispatch-config gate
    if not allow_trading:
        trader_autonomy_mode = 'conservative'
        trader_recommended_actions = ['claim'] if allow_claims else []
        trader_autonomy_reason = 'dispatch-config: allow_trading=false → claim only'
    elif tas_trade_v3 >= 0.5:
        trader_autonomy_mode = 'active'
        trader_recommended_actions = ['claim', 'sell', 'buy'] if sell_triggered else ['claim', 'buy']
        trader_autonomy_reason = (
            f'TAS_trade={tas_trade_v3:.2f}≥0.5 → active'
            + (f'; sell triggered (TAS_social={tas_social_val:.2f}<{sell_triggers.get("tas_social_below",0.3)} '
               f'or concentration={concentration_val:.2f}>{sell_triggers.get("reward_concentration_above",0.6)})' if sell_triggered else '')
        )
    elif tas_trade_v3 >= 0.3:
        trader_autonomy_mode = 'standard'
        trader_recommended_actions = ['claim', 'sell'] if sell_triggered else ['claim']
        trader_autonomy_reason = f'TAS_trade={tas_trade_v3:.2f}≥0.3 → standard (claim{"/sell" if sell_triggered else ""} only)'
    else:
        # P1-4: Claim vs trade policy split — claims are zero-risk and allowed even in conservative mode.
        # Only buy/sell are gated by TAS_trade >= 0.3.
        trader_autonomy_mode = 'conservative'
        trader_recommended_actions = ['claim'] if allow_claims else []
        trader_autonomy_reason = f'TAS_trade={tas_trade_v3:.2f}<0.3 → conservative (claim-only; buy/sell locked until TAS_trade≥0.3)'

    # P1-2: Community heat signal → preferred_ticks and sell_candidates
    heat_preferred_ticks: list[str] = []
    heat_sell_candidates: list[str] = []
    if heat_available:
        heat_ticks = heat_data.get('ticks', {})
        for tick_name, tick_info in heat_ticks.items():
            trend = tick_info.get('trend', 'stable')
            if trend == 'rising':
                heat_preferred_ticks.append(tick_name)
            elif trend == 'declining':
                # Check if we hold > 1 USD of this tick
                tick_balance = safe_float(balances.get(tick_name)) or 0.0
                if tick_balance > 0:
                    heat_sell_candidates.append(tick_name)
        if heat_preferred_ticks:
            trader_autonomy_reason += f'; heat_signal: {"↑".join(heat_preferred_ticks)}↑ → buy_preferred'
        if heat_sell_candidates:
            trader_autonomy_reason += f'; heat_declining: {"↓".join(heat_sell_candidates)}↓ → sell_candidate'

    # P1 stop-loss: a HELD, heat-declining, NON-override tick that is down
    # beyond STOP_LOSS_DRAWDOWN_PCT over the window triggers a risk-management
    # sell — independent of the concentration gate and the TAS_trade buy/sell
    # lock. priority_override ticks (BUIDL strategic hold) are exempt.
    for _tick in heat_sell_candidates:
        if _tick in override_ticks:
            continue
        _dd = drawdowns_3d.get(_tick)
        if _dd is not None and _dd <= STOP_LOSS_DRAWDOWN_PCT:
            stop_loss_ticks.append({'tick': _tick, 'drawdown_3d': round(_dd, 4)})
    if stop_loss_ticks and allow_trading:
        sell_triggered = True
        if 'sell' not in trader_recommended_actions:
            trader_recommended_actions = list(trader_recommended_actions) + ['sell']
        _sl_names = ','.join(s['tick'] for s in stop_loss_ticks)
        trader_autonomy_reason += (
            f'; STOP-LOSS: {_sl_names} 3d-drawdown≤{STOP_LOSS_DRAWDOWN_PCT:.0%} '
            f'(override-exempt: {",".join(sorted(override_ticks)) or "none"}) → risk sell'
        )

    # P1-3: Stake and LP eligibility
    _portfolio_usd = safe_float(_onchain.get('total_portfolio_usd')) or 0.0
    _concentration_risk = (onchain.get('concentration') or {}).get('risk', 'unknown')
    stake_eligible = (
        holding_trend == 'stable'
        and _portfolio_usd > 10.0
        and tas_trade_v3 > 0.4
        and rebalance_allowed
    )
    lp_eligible = (
        _concentration_risk == 'low'
        and tas_trade_v3 > 0.5
        and _portfolio_usd > 15.0
        and rebalance_allowed
    )

    # Insert stake/LP into recommended_actions by priority: claim > stake > sell > buy > provide_liquidity
    if stake_eligible and trader_autonomy_mode in ('standard', 'active'):
        # Insert after claim but before buy
        if 'stake' not in trader_recommended_actions:
            claim_idx = trader_recommended_actions.index('claim') if 'claim' in trader_recommended_actions else -1
            trader_recommended_actions.insert(claim_idx + 1, 'stake')
            trader_autonomy_reason += f'; stake_eligible (portfolio={_portfolio_usd:.1f}>10, trend=stable, TAS={tas_trade_v3:.2f}>0.4)'

    if lp_eligible and trader_autonomy_mode == 'active':
        if 'provide_liquidity' not in trader_recommended_actions:
            trader_recommended_actions.append('provide_liquidity')
            trader_autonomy_reason += f'; lp_eligible (concentration_risk=low, portfolio={_portfolio_usd:.1f}>15, TAS={tas_trade_v3:.2f}>0.5)'

    # P1-4C: Coupling annotation
    if coupling_active:
        if g_claim_threshold_override is not None:
            trader_autonomy_reason += f'; align_event_active → claim_threshold={g_claim_threshold_override:.1f} (coupled)'

    # P3: Credit Strategy from strategy-experiment (Track A)
    strategy_exp = read_json(MAIN_ROOT / 'runtime' / 'shared' / 'strategy-experiment.json') or {}
    track_a = strategy_exp.get('track_a') or {}
    credit_strategy = (track_a.get('current_arm') or {}).get('credit_strategy', 'hold')

    # Only apply credit_strategy override when:
    # 1. credit_strategy is not 'hold'
    # 2. TAS_trade is in standard or active mode (not conservative)
    # 3. allow_trading is True
    # 4. portfolio concentration guardrail not breached
    if credit_strategy in ('buy_small', 'add_lp') and trader_autonomy_mode in ('standard', 'active') and allow_trading:
        concentration_ok = concentration_val <= 0.4  # don't buy if TagClaw already > 40% of portfolio
        if concentration_ok:
            if 'buy' not in trader_recommended_actions:
                trader_recommended_actions = list(trader_recommended_actions) + ['buy']
            trader_autonomy_reason += f'; credit_strategy={credit_strategy} → buy added'
        else:
            trader_autonomy_reason += f'; credit_strategy={credit_strategy} but concentration={concentration_val:.2f}>0.4 → buy suppressed'

    atomic_write_json(RUNTIME / 'tas-trade.json', {
        'version': 'v2', 'updated_at': bundle_ts, 'source_class': 'trader-native',
        'bundle_ts': bundle_ts, 'run_id': run_id,
        'status': tas_trade_status,
        'history_eligible': tas_trade_history_eligible,
        'value': tas_trade_v3,
        # P2 2026-05-28: the native v3 compute (`value`/`tas_trade_v3`) is the
        # AUTHORITATIVE live TAS_trade. `base_value` is the legacy external
        # value from memory/tas-trade-latest.json (frozen 2026-05-17) kept only
        # as a historical reference — do NOT report it as the current value.
        'authoritative_source': 'trader-native v3 (compute_tas_trade_v3)',
        'base_value': base_tas,
        'base_value_note': 'legacy/frozen reference (memory/tas-trade-latest.json @2026-05-17); superseded by native v3 — not the current value',
        # P0 2026-04-09: v3 normalized USD formula (primary)
        'tas_trade_v3': tas_trade_v3,
        'portfolio_usd_raw': round(_onchain_total_usd, 4),
        'portfolio_usd_norm': _portfolio_usd_norm,
        'claimable_usd_raw': round(_claimable_usd_for_v3, 4),
        'claimable_usd_norm': _claimable_usd_norm,
        # P2: deprecated — retained for backward compat
        'tas_trade_extended': tas_trade_extended,
        'portfolio_value_score': portfolio_value_score,
        'credit_rank_score': credit_rank_score,
        # Retained for dashboard display (not in formula since P2)
        'claim_history_score': claim_history_score,
        'holding_trend': holding_trend,
        'holding_trend_score': holding_trend_score,
        'measurement_quality_ref': 'runtime/trader/measurement-quality.json',
        'portfolio_baseline_ref': 'runtime/trader/portfolio-baseline.json',
        'portfolio_delta_ref': 'runtime/trader/portfolio-delta.json',
        'measurement_quality': {
            'overall_status': measurement_quality.get('overall_status'),
            'overall_confidence': measurement_quality.get('overall_confidence'),
            'actionability': measurement_quality.get('actionability'),
        },
        'comparison': trader_strategy_loop,
        'strategy_action': trader_strategy_loop['strategy_action'],
        'planning_focus': trader_strategy_loop['planning_focus'],
        'formula': 'TAS_trade = 0.9 × min(portfolio_usd/50, 1.0) + 0.1 × min(claimable_usd/5, 1.0)',
        'autonomy_mode': trader_autonomy_mode,
        'recommended_actions': trader_recommended_actions,
        'preferred_ticks': heat_preferred_ticks if heat_preferred_ticks else wiki_trending_ticks[:3],
        'autonomy_reason': trader_autonomy_reason,
        # P1-2: Community heat signal fields
        'heat_signal_available': heat_available,
        'heat_preferred_ticks': heat_preferred_ticks,
        'heat_sell_candidates': heat_sell_candidates,
        'heat_source': str(MAIN_ROOT / 'runtime' / 'shared' / 'community-heat.json') if heat_available else 'unavailable',
        # P1-3: Stake/LP eligibility
        'stake_eligible': stake_eligible,
        'lp_eligible': lp_eligible,
        # P1-4C: Coupling fields
        'coupling_active': coupling_active,
        'coupling_source': coupling_source,
        'credit_strategy': credit_strategy,  # from strategy-experiment track_a
        'credit_strategy_source': 'strategy-experiment.json',
        'sell_triggered': sell_triggered,
        'sell_trigger_detail': {
            'tas_social': tas_social_val,
            'tas_social_threshold': sell_triggers.get('tas_social_below', 0.3),
            'concentration': concentration_val,
            'concentration_threshold': sell_triggers.get('reward_concentration_above', 0.6),
            # P1 stop-loss (risk management): declining + drawdown, override-exempt
            'stop_loss_triggered': bool(stop_loss_ticks),
            'stop_loss_ticks': stop_loss_ticks,
            'stop_loss_drawdown_pct': STOP_LOSS_DRAWDOWN_PCT,
            'stop_loss_window_hours': STOP_LOSS_WINDOW_HOURS,
            'priority_override_exempt': sorted(override_ticks),
            'drawdowns_observed': {k: round(v, 4) for k, v in drawdowns_3d.items()},
        },
        'guardrails': {
            'max_budget_usd': max_budget_usd,
            'max_sell_usd': max_sell_usd,
            'max_trades_per_day': max_trades_per_day,
            'min_claimable_usd': g_claim_threshold,  # guidance overrides dispatch-config default
            'allow_trading': allow_trading,
            'allow_claims': allow_claims,
        },
        'main_guidance': {
            'experiment_mode': guidance_mode,
            'claim_patience': g_claim_patience,
            'claim_threshold_usd': g_claim_threshold,
            'claim_frequency_mode': g_claim_freq_mode,
            'portfolio_target_tick': g_portfolio_tick,
            'focus_action': g_focus_action,
            'risk_mode': g_risk_mode,
        },
        'dimensions': {
            'claim_pnl_3d': round(safe_float(portfolio_delta.get('reward_claimable_usd_delta')) or 0.0, 4),
            'holding_delta_3d_usd': round(safe_float(portfolio_delta.get('portfolio_value_usd_delta')) or 0.0, 4),
            'net_pnl_3d_usd': round(
                (safe_float(portfolio_delta.get('reward_claimable_usd_delta')) or 0.0) +
                (safe_float(portfolio_delta.get('portfolio_value_usd_delta')) or 0.0), 4),
            # P3 2026-05-28: always report a live trailing window anchored on
            # the current cycle — never the frozen data_window from the legacy
            # source (which was stuck at 2026-05-17 and made the metric look
            # "11 days stale" even though the native value is fresh).
            'observation_window': {
                'start': (datetime.now(timezone.utc) - timedelta(hours=STOP_LOSS_WINDOW_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'end': bundle_ts,
                'hours': STOP_LOSS_WINDOW_HOURS,
                'note': 'live trailing window (native cycle); legacy data_window retired',
            },
        },
        'onchain_ref': 'runtime/trader/onchain-positions.json',
        'onchain_total_usd': _onchain.get('total_portfolio_usd'),
        'onchain_concentration_risk': _onchain.get('concentration', {}).get('risk'),
        'summary': tas_trade_src.get('summary'),
        'notes': 'trader V2 — onchain monitoring + TAS_trade + Main dispatch-config guardrails',
    })
    atomic_write_json(RUNTIME / 'risk-status.json', {
        'version': 'v2', 'updated_at': bundle_ts, 'source_class': 'trader-native',
        'bundle_ts': bundle_ts, 'run_id': run_id,
        'status': normalize_status(status, default='stale'),
        'risk_flags': risk_flags,
        'wiki_credit_context': wiki_credit_context if wiki_credit_context else None,
        'notes': 'trader self-published V2',
    })

    # PR1: explicit execution-plane artifacts for trader-owned treasury execution
    treasury_plan = {
        'version': 'v1',
        'plan_kind': 'treasury-execution-plan',
        'agent': 'trader',
        'executor': 'trader',
        'execution_owner': 'trader',
        'control_plane': 'main',
        'run_id': run_id,
        'generated_at': generated_at,
        'source_class': 'trader-execution-plane',
        'control_ref': 'runtime/main/treasury-policy.json',
        'guidance_ref': 'runtime/main/trader-guidance.json',
        'autonomy_ref': 'runtime/trader/tas-trade.json',
        'reward_status_ref': 'runtime/trader/reward-status.json',
        'status': 'ready' if trader_recommended_actions else 'hold',
        'autonomy_mode': trader_autonomy_mode,
        'strategy_action': trader_strategy_loop['strategy_action'],
        'planning_focus': trader_strategy_loop['planning_focus'],
        'payload': {
            'recommended_actions': trader_recommended_actions,
            'claimable_count': len(reward_items),
            'guardrails': {
                'max_budget_usd': max_budget_usd,
                'max_sell_usd': max_sell_usd,
                'max_trades_per_day': max_trades_per_day,
                'min_claimable_usd': min_claimable_usd,
                'allow_trading': allow_trading,
                'allow_claims': allow_claims,
            },
        },
        'notes': 'Trader execution plane consumes Main policy/guidance but owns treasury writes.',
    }
    atomic_write_json(RUNTIME / 'treasury-execution-plan.json', treasury_plan)

    legacy_treasury_execution = read_json(RUNTIME / 'execution-record.json') or {
        'version': 'v2', 'agent': 'trader', 'status': 'idle', 'generated_at': generated_at,
        'run_id': None, 'selected_action': None, 'results': [],
        'summary': {'attempted': 0, 'succeeded': 0, 'failed': 0},
        'notes': 'awaiting trader treasury execution worker'
    }
    treasury_result = dict(legacy_treasury_execution)
    treasury_result['result_kind'] = 'treasury-execution-result'
    treasury_result['executor'] = 'trader'
    treasury_result['execution_owner'] = 'trader'
    treasury_result['control_plane'] = 'main'
    treasury_result['source_class'] = 'trader-execution-plane'
    treasury_result['control_ref'] = 'runtime/main/treasury-policy.json'
    treasury_result['guidance_ref'] = 'runtime/main/trader-guidance.json'
    treasury_result['legacy_result_ref'] = 'runtime/trader/execution-record.json'
    treasury_result['plan_ref'] = 'runtime/trader/treasury-execution-plan.json'
    atomic_write_json(RUNTIME / 'treasury-execution-result.json', treasury_result)

    # latest.json envelope
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not (memory / 'tas-trade-latest.json').exists():
        blockers.append({'code': 'tas_trade_missing', 'severity': 'error', 'message': 'tas-trade-latest.json missing'})

    latest = {
        'version': 'v2', 'agent': 'trader',
        'run_id': run_id,
        'bundle_ts': bundle_ts,
        'status': 'blocked' if blockers else status,
        'generated_at': generated_at,
        'data_window': {
            'start': (tas_trade_src.get('window') or {}).get('start') or generated_at,
            'end': (tas_trade_src.get('window') or {}).get('end') or generated_at,
        },
        'ttl_seconds': 14400, 'freshness_seconds': 0,
        'source_class': 'trader-native',
        'strategy_loop': trader_strategy_loop,
        'strategy_action': trader_strategy_loop['strategy_action'],
        'planning_focus': trader_strategy_loop['planning_focus'],
        'inputs': {
            'tas_trade': 'memory/tas-trade-latest.json',
            'wallet_balance_md': 'memory/wallet-balance-latest.md',
            'rewards_claim_json': 'memory/rewards-claim-latest.json',
            'rewards_claim_md_fallback': 'memory/rewards-claim-latest.md',
        },
        'outputs': {
            'wallet_state': 'healthy' if balances else 'blocked',
            'reward_state': {
                'claimable_usd': claimable_total,
                'claim_recommended': claim_recommended if reward_items else None,
            },
            'tas_trade': {'status': tas_trade_status, 'value': tas_trade_v3},
            'portfolio_baseline_ref': 'runtime/trader/portfolio-baseline.json',
            'portfolio_delta_ref': 'runtime/trader/portfolio-delta.json',
            'measurement_quality_ref': 'runtime/trader/measurement-quality.json',
            'measurement_quality': {
                'overall_status': measurement_quality.get('overall_status'),
                'overall_confidence': measurement_quality.get('overall_confidence'),
                'actionability': measurement_quality.get('actionability'),
            },
            'strategy_loop': trader_strategy_loop,
            'strategy_action': trader_strategy_loop['strategy_action'],
            'planning_focus': trader_strategy_loop['planning_focus'],
            'risk_flags': risk_flags,
            'treasury_execution_plan_ref': 'runtime/trader/treasury-execution-plan.json',
            'treasury_execution_result_ref': 'runtime/trader/treasury-execution-result.json',
            'execution_ref': 'runtime/trader/execution-record.json',
            'execution_ledger_ref': (f"runtime/trader/{ledger_path.name}" if ledger_path else None),
            'last_execution_status': execution_record.get('status'),
            'execution_count_today': execution_summary.get('count'),
            'recent_operations': execution_summary.get('recent_operations'),
            'last_operation': execution_summary.get('last_operation'),
            'last_failed_operation': execution_summary.get('last_failed_operation'),
            'pending_or_unconfirmed_orders': execution_summary.get('pending_or_unconfirmed_orders'),
            'action_counts_today': execution_summary.get('action_counts'),
        },
        'wiki': {
            'wiki_platform_available': wiki_platform_available,
            'wiki_trending_ticks': wiki_trending_ticks,
            'wiki_credit_vp_threshold': wiki_credit_context.get('vp_flush_threshold') if wiki_credit_context else None,
            'wiki_brief_available': wiki_brief_available,
            # READ-ONLY narrative context (stance/discussion hooks). Reference only —
            # never an input to position/stop/claim/order logic. See load_wiki_narrative_context.
            'wiki_narrative': load_wiki_narrative_context(),
            # READ-ONLY decision memory: trader's own recent decisions + outcomes.
            # Reference only — never an input to trade logic. See load_recent_trade_decisions.
            'recent_trade_decisions': load_recent_trade_decisions(),
        },
        'blockers': blockers, 'warnings': warnings,
        'next_recommended_action': 'wait for treasury-policy from main; evaluate claim/trade inside allowed policy envelope',
        'meta': {
            'wallet_address': wallet_address,
            'reward_items_count': len(reward_items),
            'rewards_checked_at': rewards_checked_at,
            'rewards_source_kind': rewards_source_kind,
            'execution_ledger_ref': (f"runtime/trader/{ledger_path.name}" if ledger_path else None),
            'execution_ledger_count': execution_summary.get('count'),
            'previous_run_id': previous_latest.get('run_id'),
            'previous_portfolio_baseline_ref': 'runtime/trader/portfolio-baseline.json' if previous_portfolio_baseline else None,
        },
    }
    atomic_write_json(RUNTIME / 'latest.json', latest)

    print(json.dumps({'status': latest['status'], 'source_class': 'trader-native', 'outputs_written': 8}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
