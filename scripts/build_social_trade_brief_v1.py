#!/usr/bin/env python3
"""build_social_trade_brief_v1.py - build a minimal Phase-2 trader social brief.

Outputs in runtime/trader/:
  - PENDING_BRIEF.json
  - optionally PENDING_BRIEF.claimed.json when claim is recommended
  - social-brief-latest.md

Also mirrors the markdown brief into wiki/queries/ via file_to_wiki_query.py.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agency_paths import MAIN_WS

WORKSPACE = MAIN_WS
RUNTIME_TRADER = WORKSPACE / "runtime" / "trader"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"

import sys
sys.path.insert(0, str(WORKSPACE / "scripts"))

from file_to_wiki_query import file_brief_to_wiki  # type: ignore
from runtime_utils_v2 import read_json


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(text)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _top_heat_ticks(limit: int = 5) -> list[str]:
    heat = read_json(RUNTIME_SHARED / "community-heat.json") or {}
    ticks = heat.get("ticks") if isinstance(heat.get("ticks"), dict) else {}
    ranked = sorted(
        ticks.items(),
        key=lambda kv: (
            -_safe_float((kv[1] or {}).get("trend_score")),
            _safe_float((kv[1] or {}).get("trending_rank"), 9999.0),
            str(kv[0]),
        ),
    )
    return [tick for tick, _meta in ranked[:limit]]


def _recommended_tokens(limit: int = 5) -> list[str]:
    wiki_brief = read_json(RUNTIME_SHARED / "wiki-execution-brief.json") or {}
    credit = wiki_brief.get("credit_strategy") if isinstance(wiki_brief.get("credit_strategy"), dict) else {}
    tokens = [str(x).strip() for x in (credit.get("recommended_tokens") or []) if str(x).strip()]
    for tick in _top_heat_ticks(limit=limit):
        if tick not in tokens:
            tokens.append(tick)
    return tokens[:limit] or ["TagClaw", "BUIDL", "TTAI"]


def _claimable_ticks(limit: int = 5) -> list[str]:
    reward = read_json(RUNTIME_TRADER / "reward-status.json") or {}
    items = reward.get("claimable") if isinstance(reward.get("claimable"), list) else []
    ranked: list[tuple[float, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tick = str(item.get("tick") or "").strip()
        if not tick:
            continue
        ranked.append((_safe_float(item.get("reward_value_usd")), tick))
    ranked.sort(key=lambda kv: (-kv[0], kv[1]))
    out: list[str] = []
    for _usd, tick in ranked:
        if tick not in out:
            out.append(tick)
        if len(out) >= limit:
            break
    return out


def _build_post_candidate(
    *,
    thesis: str,
    cashtags: list[str],
    claimable_total: Any,
    portfolio_usd: Any,
) -> dict[str, Any] | None:
    tick = str((cashtags or ["BUIDL"])[0]).strip() or "BUIDL"
    tick_tags = [f"${str(item).strip()}" for item in cashtags[:2] if str(item).strip()]
    metric_parts: list[str] = []
    claimable_value = _safe_float(claimable_total, -1.0)
    portfolio_value = _safe_float(portfolio_usd, -1.0)
    if claimable_value >= 0:
        metric_parts.append(f"claimable ${claimable_value:.2f}")
    if portfolio_value >= 0:
        metric_parts.append(f"portfolio ${portfolio_value:.2f}")
    metric_text = " | ".join(metric_parts)
    thesis_text = str(thesis or "").strip()
    if len(thesis_text) > 110:
        thesis_text = thesis_text[:107].rstrip(" ,.;:") + "..."
    text_parts = [
        "Social-trade brief live.",
        f"Focus: {' '.join(tick_tags)}." if tick_tags else "",
        thesis_text,
        metric_text,
        "#TagClaw #DeFi",
    ]
    text = " ".join(part for part in text_parts if part).strip()
    return {
        "type": "post",
        "tick": tick,
        "text": text,
        "reason": "trader social-trade brief summary post",
        "source": "social-trade-brief",
        "draft_type": "social_trade_brief",
        "target_key": f"tagclaw:post-brief-{tick}",
    }


def build_brief() -> dict[str, Any]:
    trader_latest = read_json(RUNTIME_TRADER / "latest.json") or {}
    wallet = read_json(RUNTIME_TRADER / "wallet-snapshot.json") or {}
    reward = read_json(RUNTIME_TRADER / "reward-status.json") or {}
    tas_trade = read_json(RUNTIME_TRADER / "tas-trade.json") or {}
    risk_status = read_json(RUNTIME_TRADER / "risk-status.json") or {}
    wiki_brief = read_json(RUNTIME_SHARED / "wiki-execution-brief.json") or {}

    cashtags: list[str] = []
    for tick in _recommended_tokens(limit=5):
        if tick not in cashtags:
            cashtags.append(tick)
    for tick in _claimable_ticks(limit=5):
        if tick not in cashtags:
            cashtags.append(tick)
    cashtags = cashtags[:6]

    top_themes = wiki_brief.get("top_themes") if isinstance(wiki_brief.get("top_themes"), list) else []
    theme_names = [str(t.get("name") or "").strip() for t in top_themes if isinstance(t, dict) and str(t.get("name") or "").strip()]
    theme_names = theme_names[:3]

    claimable_total = reward.get("claimable_usd_total")
    claim_recommended = bool(reward.get("claim_recommended")) or (_safe_float(claimable_total, 0.0) >= 2.0)
    portfolio_usd = wallet.get("portfolio_usd") or wallet.get("portfolio_value_usd")
    tas_value = tas_trade.get("value")
    risk_flags = risk_status.get("risk_flags") if isinstance(risk_status.get("risk_flags"), list) else []

    thesis = (
        f"Social-trade focus stays on {', '.join(cashtags[:3])}: "
        f"community heat and treasury context currently point there."
        if cashtags else
        "Social-trade focus is on the highest-signal treasury/community overlap this cycle."
    )
    if claim_recommended:
        thesis += f" Claim flow is live with about ${_safe_float(claimable_total, 0.0):.2f} claimable rewards."

    bullets = [
        f"TAS_trade={tas_value} status={tas_trade.get('status', trader_latest.get('status', 'unknown'))}",
        f"Portfolio≈${_safe_float(portfolio_usd, 0.0):.2f} | Claimable≈${_safe_float(claimable_total, 0.0):.2f}",
        f"Top cashtags: {', '.join(cashtags[:4])}" if cashtags else "Top cashtags: none",
        f"Top wiki themes: {', '.join(theme_names[:3])}" if theme_names else "Top wiki themes: none",
        f"Risk flags: {', '.join(str(x) for x in risk_flags[:4])}" if risk_flags else "Risk flags: none",
    ]
    post_candidate = _build_post_candidate(
        thesis=thesis,
        cashtags=cashtags,
        claimable_total=claimable_total,
        portfolio_usd=portfolio_usd,
    )

    return {
        "schema": "trader.social-brief.v1",
        "generated_at": now_iso(),
        "status": "ok",
        "brief_kind": "social-trade-brief",
        "thesis": thesis,
        "summary_bullets": bullets,
        "cashtags": cashtags,
        "theme_names": theme_names,
        "claim_recommended": claim_recommended,
        "claimable_usd_total": claimable_total,
        "portfolio_usd": portfolio_usd,
        "tas_trade": {
            "value": tas_value,
            "status": tas_trade.get("status"),
        },
        "post_candidate": post_candidate,
        "risk_flags": risk_flags,
        "source_refs": [
            "runtime/trader/latest.json",
            "runtime/trader/wallet-snapshot.json",
            "runtime/trader/reward-status.json",
            "runtime/trader/tas-trade.json",
            "runtime/shared/community-heat.json",
            "runtime/shared/wiki-execution-brief.json",
        ],
    }


def render_markdown(brief: dict[str, Any]) -> str:
    lines = [
        "# Trader Social Brief",
        "",
        f"- generated_at: `{brief.get('generated_at')}`",
        f"- claim_recommended: `{brief.get('claim_recommended')}`",
        f"- claimable_usd_total: `{brief.get('claimable_usd_total')}`",
        f"- portfolio_usd: `{brief.get('portfolio_usd')}`",
        "",
        "## Thesis",
        "",
        str(brief.get("thesis") or ""),
        "",
        "## Cashtags",
        "",
        ", ".join(str(x) for x in (brief.get("cashtags") or [])) or "_none_",
        "",
        "## Summary Bullets",
        "",
    ]
    for bullet in brief.get("summary_bullets") or []:
        lines.append(f"- {bullet}")
    lines.extend([
        "",
        "## Source Refs",
        "",
    ])
    for ref in brief.get("source_refs") or []:
        lines.append(f"- `{ref}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    brief = build_brief()
    claimed = bool(brief.get("claim_recommended"))
    target = RUNTIME_TRADER / ("PENDING_BRIEF.claimed.json" if claimed else "PENDING_BRIEF.json")
    fallback_target = RUNTIME_TRADER / ("PENDING_BRIEF.json" if claimed else "PENDING_BRIEF.claimed.json")
    md_path = RUNTIME_TRADER / "social-brief-latest.md"

    atomic_write_json(target, brief)
    if fallback_target.exists():
        fallback_target.unlink()
    atomic_write_text(md_path, render_markdown(brief))

    file_brief_to_wiki(
        source_md_path=md_path,
        source_agent="trader",
        title=f"Trader Social Brief - {brief.get('generated_at')}",
        tags=["social-brief", "trader", "on-chain"],
        related_concepts=[f"[[concepts/{name}]]" for name in (brief.get("theme_names") or [])[:3]],
        file_stem="trader-social-brief-latest",
    )

    print(json.dumps({
        "status": "ok",
        "brief_json": str(target.relative_to(WORKSPACE)),
        "brief_md": str(md_path.relative_to(WORKSPACE)),
        "cashtags": brief.get("cashtags"),
        "claim_recommended": claimed,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
