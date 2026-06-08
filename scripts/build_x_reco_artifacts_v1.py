#!/usr/bin/env python3
"""build_x_reco_artifacts_v1.py - build Phase-2 X-Reco runtime artifacts.

Produces:
  - runtime/bookmarker/x-reco-ranked.json
  - runtime/bookmarker/x-reco-author-affinity-180d.json
  - runtime/bookmarker/tas-xreco.json
  - runtime/bookmarker/x-reco-eval-latest.json

The goal is not to perfectly recreate every historical local script, but to
materialize the contract the current repo already consumes. Inputs are tolerant:
we read bookmarker memory/x-sync-latest.json first, then fall back to local
archives via fallback_items.py.
"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agency_paths import BOOKMARKER_WS, MAIN_WS
from fallback_items import load_x_sync_with_fallback

sys.path.insert(0, str((MAIN_WS / "scripts")))

try:
    from wiki_registry import get_all_concepts, get_all_ticks, resolve_concept  # type: ignore
except Exception:
    def get_all_concepts() -> list[str]:  # type: ignore[misc]
        return []
    def get_all_ticks() -> list[str]:  # type: ignore[misc]
        return []
    def resolve_concept(name: str) -> str:  # type: ignore[misc]
        return name


BOOKMARKER_ROOT = BOOKMARKER_WS
MAIN_ROOT = MAIN_WS
RUNTIME = MAIN_ROOT / "runtime" / "bookmarker"
MEMORY = BOOKMARKER_ROOT / "memory"

RANKED_PATH = RUNTIME / "x-reco-ranked.json"
AFFINITY_PATH = RUNTIME / "x-reco-author-affinity-180d.json"
TAS_PATH = RUNTIME / "tas-xreco.json"
EVAL_PATH = RUNTIME / "x-reco-eval-latest.json"

OWN_HANDLES = {"0xnought", "@0xnought"}
SOURCE_WEIGHTS = {
    "bookmark": 1.0,
    "like": 0.4,
    "reply": 1.5,
    "comment": 1.5,
    "tweet": 0.2,
    "post": 0.2,
    "unknown": 0.25,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for candidate in (text,):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    for fmt in ("%a %b %d %H:%M:%S %z %Y",):
        try:
            return datetime.strptime(text, fmt).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _normalize_author(item: dict[str, Any]) -> str:
    author = item.get("author")
    if isinstance(author, dict):
        value = author.get("username") or author.get("screen_name") or author.get("handle")
    else:
        value = item.get("username") or item.get("author")
    return str(value or "unknown").strip().lstrip("@").lower() or "unknown"


def _public_metrics(item: dict[str, Any]) -> dict[str, float]:
    raw = item.get("public_metrics") or item.get("publicMetrics") or {}
    if not isinstance(raw, dict):
        raw = {}
    likes = _safe_float(raw.get("like_count") or item.get("like_count"))
    rts = _safe_float(raw.get("retweet_count") or item.get("retweet_count"))
    replies = _safe_float(raw.get("reply_count") or item.get("reply_count"))
    bookmarks = _safe_float(raw.get("bookmark_count") or item.get("bookmark_count"))
    impressions = _safe_float(raw.get("impression_count") or item.get("impression_count"))
    return {
        "likes": likes,
        "retweets": rts,
        "replies": replies,
        "bookmarks": bookmarks,
        "impressions": impressions,
    }


def _source_type(item: dict[str, Any]) -> str:
    src = str(item.get("source_type") or "unknown").strip().lower()
    return src or "unknown"


def _weight_for_source(source_type: str) -> float:
    return SOURCE_WEIGHTS.get(source_type, SOURCE_WEIGHTS["unknown"])


def _token_hits(text: str) -> tuple[list[str], list[str]]:
    concepts = []
    ticks = []
    lower = text.lower()
    for concept in get_all_concepts():
        if concept.lower() in lower:
            canon = resolve_concept(concept)
            if canon not in concepts:
                concepts.append(canon)
    for tick in get_all_ticks():
        if tick.lower() in lower:
            if tick not in ticks:
                ticks.append(tick)
            canon = resolve_concept(tick)
            if canon not in concepts:
                concepts.append(canon)
    # Also catch cashtags/@handles as weak signals when the registry knows them.
    for token in re.findall(r"\$[A-Za-z][A-Za-z0-9_]{1,15}|@[A-Za-z0-9_]{2,20}", text):
        norm = token.lstrip("$@")
        canon = resolve_concept(norm)
        if canon != norm and canon not in concepts:
            concepts.append(canon)
    return concepts, ticks


def _engagement_score(metrics: dict[str, float]) -> float:
    raw = (
        metrics["likes"] * 1.0
        + metrics["retweets"] * 1.8
        + metrics["replies"] * 1.6
        + metrics["bookmarks"] * 1.2
        + metrics["impressions"] * 0.02
    )
    return math.log1p(max(raw, 0.0))


def _recency_score(created_at: datetime | None) -> float:
    if not created_at:
        return 0.2
    age_hours = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds() / 3600.0)
    return max(0.0, 1.5 - min(1.5, age_hours / 72.0))


def load_items() -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    xsync, items, used_fallback = load_x_sync_with_fallback(MEMORY, max_items=120)
    norm: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            norm.append(item)
    return xsync, norm, used_fallback


def build_author_affinity(items: list[dict[str, Any]]) -> dict[str, Any]:
    authors: dict[str, dict[str, Any]] = {}
    for item in items:
        handle = _normalize_author(item)
        if handle in OWN_HANDLES or handle == "unknown":
            continue
        metrics = _public_metrics(item)
        source_type = _source_type(item)
        created_at = _parse_dt(item.get("createdAt") or item.get("created_at") or item.get("tweetTime"))
        rec = authors.setdefault(handle, {
            "weighted_affinity_180d": 0.0,
            "combined_affinity": 0.0,
            "interaction_counts": {},
            "engagement_score_sum": 0.0,
            "item_count": 0,
            "last_interaction_at": None,
            "strong_signal_creator": False,
        })
        base = _weight_for_source(source_type)
        rec["weighted_affinity_180d"] += base
        rec["engagement_score_sum"] += _engagement_score(metrics)
        rec["item_count"] += 1
        counts = rec["interaction_counts"]
        counts[source_type] = int(counts.get(source_type, 0)) + 1
        if created_at:
            iso = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            if not rec["last_interaction_at"] or iso > str(rec["last_interaction_at"]):
                rec["last_interaction_at"] = iso

    for rec in authors.values():
        weighted = _safe_float(rec.get("weighted_affinity_180d"))
        engagement = _safe_float(rec.get("engagement_score_sum"))
        combined = weighted + 0.35 * engagement
        rec["weighted_affinity_180d"] = round(weighted, 4)
        rec["combined_affinity"] = round(combined, 4)
        rec["strong_signal_creator"] = bool(combined >= 2.5 or rec.get("item_count", 0) >= 3)
        rec.pop("engagement_score_sum", None)

    ranked_authors = sorted(authors.items(), key=lambda kv: -_safe_float((kv[1] or {}).get("combined_affinity")))
    return {
        "schema": "x-reco-author-affinity-180d.v1",
        "generated_at": now_iso(),
        "window_days": 180,
        "author_count": len(authors),
        "authors": {handle: meta for handle, meta in ranked_authors},
    }


def build_ranked(items: list[dict[str, Any]], author_affinity: dict[str, Any]) -> dict[str, Any]:
    author_scores = author_affinity.get("authors") if isinstance(author_affinity, dict) else {}
    ranked_items: list[dict[str, Any]] = []
    pushes = 0
    hits = 0

    for item in items:
        handle = _normalize_author(item)
        if handle in OWN_HANDLES:
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        pushes += 1
        metrics = _public_metrics(item)
        source_type = _source_type(item)
        created_at = _parse_dt(item.get("createdAt") or item.get("created_at") or item.get("tweetTime"))
        matched_concepts, matched_ticks = _token_hits(text)
        author_meta = author_scores.get(handle) if isinstance(author_scores, dict) else {}
        affinity = _safe_float((author_meta or {}).get("combined_affinity"))
        item_score = (
            1.3 * _weight_for_source(source_type)
            + 1.1 * _engagement_score(metrics)
            + 0.15 * affinity
            + _recency_score(created_at)
            + min(1.2, 0.3 * len(matched_concepts))
        )
        if matched_concepts:
            hits += 1
        ranked_items.append({
            "id": str(item.get("id") or item.get("tweet_id") or ""),
            "author": {"username": handle},
            "text": text,
            "url": item.get("url") or "",
            "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if created_at else None,
            "source_type": source_type,
            "score": round(item_score, 4),
            "matched_concepts": matched_concepts,
            "matched_ticks": matched_ticks,
            "engagement": metrics,
            "author_affinity": round(affinity, 4),
        })

    ranked_items.sort(key=lambda row: (-_safe_float(row.get("score")), str(row.get("id") or "")))
    return {
        "schema": "x-reco-ranked.v1",
        "generated_at": now_iso(),
        "ranked_count": len(ranked_items),
        "pushes": pushes,
        "hits": hits,
        "hit_rate": round(hits / pushes, 4) if pushes else 0.0,
        "ranked_items": ranked_items[:100],
    }


def build_tas_xreco(ranked: dict[str, Any], used_fallback: bool) -> dict[str, Any]:
    pushes = int(ranked.get("pushes") or 0)
    hits = int(ranked.get("hits") or 0)
    hit_rate = _safe_float(ranked.get("hit_rate"))
    top_items = ranked.get("ranked_items") if isinstance(ranked.get("ranked_items"), list) else []
    mean_top_score = 0.0
    if top_items:
        mean_top_score = sum(_safe_float(item.get("score")) for item in top_items[:10]) / max(1, min(len(top_items), 10))
    if pushes == 0:
        status = "missing"
        value = None
    elif pushes < 3:
        status = "insufficient_data"
        value = round(min(5.0, 1.5 + mean_top_score * 0.4), 4)
    else:
        status = "ok"
        quality = min(5.0, 5.0 * hit_rate + min(1.5, mean_top_score / 4.0))
        value = round(min(5.0, quality), 4)
    return {
        "schema": "tas.metric.xreco.v1",
        "generated_at": now_iso(),
        "status": status,
        "value": value,
        "hits": hits,
        "pushes": pushes,
        "hit_rate": round(hit_rate, 4) if pushes else 0.0,
        "mean_top_score": round(mean_top_score, 4),
        "used_fallback_items": bool(used_fallback),
        "summary": f"x-reco pushes={pushes} hits={hits} hit_rate={round(hit_rate, 4) if pushes else 0.0}",
    }


def build_eval(tas_xreco: dict[str, Any]) -> dict[str, Any]:
    overlap_rate = _safe_float(tas_xreco.get("hit_rate"))
    return {
        "schema": "x-reco-eval.v1",
        "generated_at": now_iso(),
        "report_date": now_iso(),
        "status": tas_xreco.get("status"),
        "gap_analysis": {
            "overlap_rate": round(overlap_rate, 4),
            "hit_count": int(tas_xreco.get("hits") or 0),
            "push_count": int(tas_xreco.get("pushes") or 0),
            "quality_value": tas_xreco.get("value"),
        },
        "summary": tas_xreco.get("summary"),
    }


def main() -> int:
    _xsync_doc, items, used_fallback = load_items()
    author_affinity = build_author_affinity(items)
    ranked = build_ranked(items, author_affinity)
    tas_xreco = build_tas_xreco(ranked, used_fallback)
    eval_doc = build_eval(tas_xreco)

    atomic_write_json(AFFINITY_PATH, author_affinity)
    atomic_write_json(RANKED_PATH, ranked)
    atomic_write_json(TAS_PATH, tas_xreco)
    atomic_write_json(EVAL_PATH, eval_doc)

    print(json.dumps({
        "status": "ok",
        "authors": author_affinity.get("author_count"),
        "ranked": ranked.get("ranked_count"),
        "hits": tas_xreco.get("hits"),
        "pushes": tas_xreco.get("pushes"),
        "tas_xreco": tas_xreco.get("value"),
        "used_fallback_items": used_fallback,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
