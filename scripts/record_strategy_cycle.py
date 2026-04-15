#!/usr/bin/env python3
"""Record one autoresearch-style strategy cycle to strategy-log.jsonl.

Usage:
  python3 scripts/record_strategy_cycle.py --snapshot-before
  python3 scripts/record_strategy_cycle.py --snapshot-after
  python3 scripts/record_strategy_cycle.py --finalize
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
MEMORY  = ROOT / 'memory'

TAS_SNAPSHOT_BEFORE = RUNTIME / 'main' / 'tas-snapshot-before.json'
TAS_LATEST          = RUNTIME / 'main' / 'tas-latest.json'

MAIN_STRATEGY_LOG   = MEMORY / 'main-strategy-log.jsonl'
BK_STRATEGY_LOG     = MEMORY / 'bookmarker-strategy-log.jsonl'
TR_STRATEGY_LOG     = MEMORY / 'trader-strategy-log.jsonl'

BOOKMARKER_GUIDANCE = RUNTIME / 'main' / 'bookmarker-guidance.json'
TRADER_GUIDANCE     = RUNTIME / 'main' / 'trader-guidance.json'
BK_TAS_SOCIAL       = RUNTIME / 'bookmarker' / 'tas-social.json'
TR_TAS_TRADE        = RUNTIME / 'trader' / 'tas-trade.json'
BK_EXECUTION        = RUNTIME / 'bookmarker' / 'social-execution.json'
TR_EXECUTION        = RUNTIME / 'trader' / 'execution-record.json'


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def atomic_write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        temp_name = tmp.name
    os.replace(temp_name, str(path))


def atomic_append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + '\n'
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
        tmp.write(existing + line)
        temp_name = tmp.name
    os.replace(temp_name, path)


def read_tas_values(tas_doc):
    if not tas_doc:
        return {'social': None, 'trade': None, 'total': None, 'status': 'missing'}
    return {'social': tas_doc.get('tas_social'), 'trade': tas_doc.get('tas_trade'), 'total': tas_doc.get('tas_total'), 'status': tas_doc.get('status', 'unknown')}


def compute_delta(before, after):
    def safe_delta(a, b):
        return round(b - a, 6) if a is not None and b is not None else None
    return {'social': safe_delta(before.get('social'), after.get('social')), 'trade': safe_delta(before.get('trade'), after.get('trade')), 'total': safe_delta(before.get('total'), after.get('total'))}


def snapshot_before():
    tas_doc = read_json(TAS_LATEST) or {}
    tas = read_tas_values(tas_doc)
    snapshot = {'captured_at': now_iso(), 'tas': tas, 'source': 'runtime/main/tas-latest.json'}
    atomic_write_json(TAS_SNAPSHOT_BEFORE, snapshot)
    print(json.dumps({'status': 'snapshot_before_captured', 'tas_total': tas.get('total'), 'tas_social': tas.get('social'), 'tas_trade': tas.get('trade')}, ensure_ascii=False))
    return snapshot


def snapshot_after_and_log():
    before_doc = read_json(TAS_SNAPSHOT_BEFORE) or {}
    tas_before = before_doc.get('tas') or read_tas_values(None)
    after_doc = read_json(TAS_LATEST) or {}
    tas_after = read_tas_values(after_doc)
    delta = compute_delta(tas_before, tas_after)
    total_delta = delta.get('total')
    kept = bool(total_delta is not None and total_delta > 0)
    outcome = 'improved' if kept else ('unchanged' if total_delta == 0 else 'declined')
    cycle_id = now_iso()

    bk_guidance_doc = read_json(BOOKMARKER_GUIDANCE) or {}
    tr_guidance_doc = read_json(TRADER_GUIDANCE) or {}
    bk_guidance = bk_guidance_doc.get('guidance') or {}
    tr_guidance = tr_guidance_doc.get('guidance') or {}
    exp_mode = bk_guidance_doc.get('experiment_mode', 'unknown')

    main_entry = {
        'cycle_id': cycle_id, 'experiment_mode': exp_mode,
        'guidance_given': {'bookmarker': bk_guidance, 'trader': tr_guidance},
        'tas_before': tas_before, 'tas_after': tas_after, 'delta': delta,
        'outcome': outcome, 'kept': kept,
    }
    atomic_append_jsonl(MAIN_STRATEGY_LOG, main_entry)

    bk_entry = {
        'cycle_id': cycle_id, 'experiment_mode': exp_mode, 'guidance': bk_guidance,
        'delta_social': delta.get('social'),
        'kept': bool(delta.get('social') is not None and (delta.get('social') or 0) > 0),
        'topic_directive': bk_guidance.get('topic_directive'),
    }
    atomic_append_jsonl(BK_STRATEGY_LOG, bk_entry)

    tr_entry = {
        'cycle_id': cycle_id, 'experiment_mode': exp_mode, 'guidance': tr_guidance,
        'delta_trade': delta.get('trade'),
        'kept': bool(delta.get('trade') is not None and (delta.get('trade') or 0) > 0),
        'claim_patience': tr_guidance.get('claim_patience'),
    }
    atomic_append_jsonl(TR_STRATEGY_LOG, tr_entry)

    result = {'status': 'logged', 'cycle_id': cycle_id, 'outcome': outcome, 'kept': kept, 'delta': delta}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    args = sys.argv[1:]
    if '--snapshot-before' in args:
        snapshot_before()
        return 0
    if '--snapshot-after' in args or '--finalize' in args:
        snapshot_after_and_log()
        return 0
    # Default: show stats
    for label, path in [('Main', MAIN_STRATEGY_LOG), ('Bookmarker', BK_STRATEGY_LOG), ('Trader', TR_STRATEGY_LOG)]:
        if not path.exists():
            print(f'{label}: no log yet')
            continue
        lines = [l for l in path.read_text().strip().split('\n') if l.strip()]
        entries = []
        for l in lines:
            try:
                entries.append(json.loads(l))
            except Exception:
                pass
        kept = sum(1 for e in entries if e.get('kept'))
        total = len(entries)
        print(f'{label}: {total} cycles, {kept} kept ({int(kept/total*100) if total else 0}% win rate)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
