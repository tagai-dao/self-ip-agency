#!/usr/bin/env python3
"""verify_arm_causality.py — automated proof of which strategy-experiment "arms" actually
change agent behavior.

The strategy-experiment bandit selects values for several arm fields and writes them to
runtime/shared/strategy-experiment.json. But selecting an arm is worthless if nothing
downstream READS it. This module statically traces, per arm field:

  1. Does any executor read the field at all?            (no read  -> DEAD: no consumer)
  2. Is it read straight off the arm object?             (direct read -> WIRED)
  3. Is it read off an intermediate payload/config,      (payload read + no payload write
     and is that key ever written into the payload?       -> DEAD: arm->payload severed)
  4. Which of the field's possible VALUES have a code     (values with no branch are inert,
     branch in an executor?                                e.g. credit_strategy only acts
                                                            on 'buy_small')

This is the guardrail against the exact regression that silently killed the loop
("Phase 1 simplified — no more experiment arm parameters" removed the arm->payload
injection, so the bandit kept optimizing knobs wired to nothing). Run it in CI.

Exit code 0 if every declared arm field is WIRED, 1 otherwise.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE') or (Path.home() / '.openclaw' / 'workspace'))
SCRIPTS = WORKSPACE / 'scripts'

# Files that turn an arm into real behavior (executors) or build the payloads they read.
SCAN_FILES = [
    'execute_treasury_policy_v2.py',
    'execute_social_intent_v2.py',
    'run_main_runtime.py',
    'run_bookmarker_runtime.py',
    'run_trader_runtime.py',
    'build_wiki_grounded_drafts_v1.py',  # P-C: consumes content_angle arm
]

# Declared arm fields and their full value space (from strategy_experiment.py).
# Active arm space ONLY (2026-06-06 P2). DEAD levers (vp_strategy, target_selection,
# post_timing) were removed from strategy_experiment.py, so they're no longer audited.
# This dict must mirror the live arm space — a DEAD field here fails the gate (exit 1).
ARM_FIELDS: dict[str, list[str]] = {
    'credit_strategy': ['hold', 'buy_small'],
    'engagement_mode': ['none', 'reply_to_top_agents'],
    'content_angle': ['insight', 'open_question'],
}

# A receiver expression naming one of these is reading straight off the arm object.
_ARM_RECEIVER_HINTS = ('arm', 'strategy_exp', 'track_a', 'track_b', 'experiment')
# A receiver expression naming one of these is reading off an intermediate payload.
_PAYLOAD_RECEIVER_HINTS = ('post_config', 'curator_config', 'payload', 'config', 'intent')


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)  # py3.9+
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return _unparse(node.value) + '.' + node.attr
        return node.__class__.__name__


def _classify_receiver(expr: str) -> str:
    low = expr.lower()
    if any(h in low for h in _ARM_RECEIVER_HINTS):
        return 'arm'
    if any(h in low for h in _PAYLOAD_RECEIVER_HINTS):
        return 'payload'
    return 'unknown'


class _Scanner(ast.NodeVisitor):
    """Collect reads/writes of arm-field keys and references to arm-value literals."""

    def __init__(self, fname: str, field_names: set[str], value_literals: set[str]):
        self.fname = fname
        self.fields = field_names
        self.values = value_literals
        self.reads: list[dict] = []   # {field, receiver, recv_class, line}
        self.writes: list[dict] = []  # {field, line, kind}
        self.value_refs: dict[str, list[int]] = {}  # value literal -> [lines]

    def _maybe_value(self, s: str, line: int):
        if s in self.values:
            self.value_refs.setdefault(s, []).append(line)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            self._maybe_value(node.value, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # receiver.get('field')
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in self.fields):
            recv = _unparse(node.func.value)
            self.reads.append({
                'field': node.args[0].value, 'receiver': recv,
                'recv_class': _classify_receiver(recv), 'line': node.lineno,
            })
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and key.value in self.fields:
            recv = _unparse(node.value)
            if isinstance(node.ctx, ast.Store):
                self.writes.append({'field': key.value, 'line': node.lineno, 'kind': 'subscript_set'})
            else:
                self.reads.append({
                    'field': key.value, 'receiver': recv,
                    'recv_class': _classify_receiver(recv), 'line': node.lineno,
                })
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and k.value in self.fields:
                self.writes.append({'field': k.value, 'line': k.lineno, 'kind': 'dict_literal_key'})
        self.generic_visit(node)


def scan() -> dict:
    field_names = set(ARM_FIELDS)
    value_literals = {v for vs in ARM_FIELDS.values() for v in vs}
    agg = {f: {'reads': [], 'writes': [], 'value_refs': {}} for f in ARM_FIELDS}
    missing_files = []

    for fname in SCAN_FILES:
        path = SCRIPTS / fname
        if not path.exists():
            missing_files.append(fname)
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=fname)
        except (OSError, SyntaxError) as e:
            missing_files.append(f'{fname} ({e})')
            continue
        sc = _Scanner(fname, field_names, value_literals)
        sc.visit(tree)
        for r in sc.reads:
            agg[r['field']]['reads'].append({'file': fname, **{k: r[k] for k in ('receiver', 'recv_class', 'line')}})
        for w in sc.writes:
            agg[w['field']]['writes'].append({'file': fname, **{k: w[k] for k in ('line', 'kind')}})
        # value literal references: attribute to the field whose value space contains it
        for val, lines in sc.value_refs.items():
            for f, vs in ARM_FIELDS.items():
                if val in vs:
                    agg[f]['value_refs'].setdefault(val, []).extend(f'{fname}:{ln}' for ln in lines)
    return {'fields': agg, 'missing_files': missing_files}


def verdict(agg_field: dict, field: str) -> dict:
    reads = agg_field['reads']
    writes = agg_field['writes']
    direct = [r for r in reads if r['recv_class'] == 'arm']
    payload = [r for r in reads if r['recv_class'] == 'payload']
    unknown = [r for r in reads if r['recv_class'] == 'unknown']

    if not reads:
        status, reason = 'DEAD', 'no executor reads this field — bandit optimizes a knob wired to nothing'
    elif direct:
        status, reason = 'WIRED', 'read directly off the arm object'
    elif payload and writes:
        status, reason = 'WIRED', 'read off a payload that is also written (confirm value is sourced from arm)'
    elif payload and not writes:
        status, reason = 'DEAD', 'consumer reads payload[field] but NOTHING writes that key into the payload — arm->payload link severed'
    elif unknown:
        status, reason = 'CHECK', 'read off an unclassified receiver — manual confirmation needed'
    else:
        status, reason = 'DEAD', 'no usable read path'

    # value-branch coverage: which possible values actually have a code branch?
    referenced = set(agg_field['value_refs'])
    declared = set(ARM_FIELDS[field])
    inert_values = sorted(declared - referenced)
    active_values = sorted(declared & referenced)
    return {
        'status': status, 'reason': reason,
        'active_values': active_values, 'inert_values': inert_values,
        'reads': reads, 'writes': writes,
    }


def main() -> int:
    result = scan()
    print('=' * 78)
    print('ARM CAUSALITY AUDIT — does selecting an arm actually change behavior?')
    print('=' * 78)
    if result['missing_files']:
        print(f'  (skipped/missing scan files: {result["missing_files"]})')
    all_wired = True
    rows = []
    for field in ARM_FIELDS:
        v = verdict(result['fields'][field], field)
        rows.append((field, v))
        if v['status'] != 'WIRED':
            all_wired = False

    for field, v in rows:
        mark = {'WIRED': '[OK]  ', 'DEAD': '[DEAD]', 'CHECK': '[CHK] '}.get(v['status'], '[?]   ')
        print(f'\n{mark} {field:18} {v["status"]}')
        print(f'        {v["reason"]}')
        # Value-branch coverage only means something when a consumer exists to branch on it.
        if v['reads']:
            if v['active_values']:
                print(f'        values with a code branch : {v["active_values"]}')
            if v['inert_values']:
                print(f'        INERT values (no effect)  : {v["inert_values"]}')
        for r in v['reads']:
            print(f'          read  {r["file"]}:{r["line"]}  <- {r["receiver"]} ({r["recv_class"]})')
        for w in v['writes']:
            print(f'          write {w["file"]}:{w["line"]}  ({w["kind"]})')

    print('\n' + '-' * 78)
    wired = sum(1 for _, v in rows if v['status'] == 'WIRED')
    print(f'SUMMARY: {wired}/{len(rows)} arm fields wired to behavior.')
    if not all_wired:
        dead = [f for f, v in rows if v['status'] != 'WIRED']
        print(f'NOT WIRED: {dead}')
        print('A bandit cannot evolve behavior through knobs that change nothing. '
              'Wire these or remove them from the search space before going live.')
    return 0 if all_wired else 1


if __name__ == '__main__':
    sys.exit(main())
