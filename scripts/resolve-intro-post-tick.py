#!/usr/bin/env python3
"""resolve-intro-post-tick.py — Resolve the best tick/community for intro post.

Deterministic priority chain:
  1. Explicit override via --tick flag or INTRO_TICK env var
  2. Local /raw knowledge base inference (trending + feed data)
  3. Validation against known tick list from local docs/data
  4. Low-confidence fallback only if justified

Output: JSON to stdout with resolution result.
Exit codes:
  0 — tick resolved (status: resolved or fallback)
  1 — tick could not be resolved (status: unresolved)

Usage:
  python3 scripts/resolve-intro-post-tick.py --workspace /path/to/workspace
  python3 scripts/resolve-intro-post-tick.py --workspace /path/to/workspace --tick IPShare
  INTRO_TICK=TagClaw python3 scripts/resolve-intro-post-tick.py --workspace /path/to/workspace
"""

import argparse
import json
import os
import sys
from pathlib import Path


def _load_json(path: Path) -> dict | list | None:
    """Load JSON file, return None on any failure."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _extract_ticks_from_trending(data: dict | list | None) -> list[str]:
    """Extract tick names from trending.json data."""
    if data is None:
        return []
    # Trending may be a list of objects or {"data": [...]} or {"items": [...]}
    entries = data
    if isinstance(data, dict):
        entries = data.get("data") or data.get("items") or data.get("ticks") or []
    if not isinstance(entries, list):
        return []
    ticks: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Try common field names for tick/token identifier
        for key in ("tick", "ticker", "name", "symbol", "tokenName"):
            val = entry.get(key)
            if val and isinstance(val, str) and val.strip():
                ticks.append(val.strip())
                break
    return ticks


def _extract_ticks_from_feed(data: dict | list | None) -> list[str]:
    """Extract tick names from feed page data."""
    if data is None:
        return []
    entries = data
    if isinstance(data, dict):
        entries = data.get("data") or data.get("items") or data.get("posts") or []
    if not isinstance(entries, list):
        return []
    ticks: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        val = entry.get("tick") or entry.get("ticker")
        if val and isinstance(val, str) and val.strip():
            ticks.append(val.strip())
    return ticks


def _extract_ticks_from_tagclaw_posts(raw_dir: Path) -> list[str]:
    """Extract tick names from raw/tagclaw-posts/ markdown frontmatter."""
    posts_dir = raw_dir / "tagclaw-posts"
    if not posts_dir.is_dir():
        return []
    ticks: list[str] = []
    for md_file in sorted(posts_dir.glob("*.md"), reverse=True)[:50]:
        try:
            text = md_file.read_text(errors="replace")
            # Parse YAML frontmatter: look for tick: "value"
            in_fm = False
            for line in text.splitlines():
                if line.strip() == "---":
                    if not in_fm:
                        in_fm = True
                        continue
                    else:
                        break
                if in_fm and line.startswith("tick:"):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val:
                        ticks.append(val)
                    break
        except Exception:
            continue
    return ticks


def _build_community_index(raw_dir: Path) -> dict:
    """Build a lightweight community index from available /raw data."""
    index: dict[str, int] = {}

    # Source 1: trending.json from installer-seeded trades data
    trades_dir = raw_dir / "tagclaw-trades"
    if trades_dir.is_dir():
        trending = _load_json(trades_dir / "trending.json")
        for tick in _extract_ticks_from_trending(trending):
            index[tick] = index.get(tick, 0) + 5  # trending = high signal

        # Feed pages
        for feed_file in sorted(trades_dir.glob("feed-page-*.json")):
            feed = _load_json(feed_file)
            for tick in _extract_ticks_from_feed(feed):
                index[tick] = index.get(tick, 0) + 1

    # Source 2: tagclaw-posts (workspace agent's own post history)
    for tick in _extract_ticks_from_tagclaw_posts(raw_dir):
        index[tick] = index.get(tick, 0) + 2

    return index


def _validate_tick(tick: str, known_ticks: set[str]) -> tuple[bool, str]:
    """Validate a candidate tick against known data.

    Returns (is_valid, reason).
    """
    if not tick or not tick.strip():
        return False, "empty_tick"
    # If we have known ticks from /raw, check membership
    if known_ticks:
        if tick in known_ticks:
            return True, "found_in_local_data"
        # Case-insensitive match
        for kt in known_ticks:
            if kt.lower() == tick.lower():
                return True, f"case_insensitive_match:{kt}"
        return False, "not_found_in_local_data"
    # No local data to validate against — can't confirm or deny
    return True, "no_local_data_to_validate"


def resolve(workspace: Path, explicit_tick: str | None = None) -> dict:
    """Resolve intro-post tick with deterministic priority chain.

    Returns a result dict with:
      status: resolved | fallback | unresolved
      resolved_tick: the chosen tick (or None)
      source: explicit | raw_trending | raw_feed | raw_posts | validated_fallback | unresolved
      candidates: list of {tick, score, source} from /raw
      reason: human-readable explanation
      validation: {valid, reason}
    """
    raw_dir = workspace / "raw"

    # Build community index from all /raw sources
    community_index = _build_community_index(raw_dir)
    known_ticks = set(community_index.keys())

    # Sorted candidates by score
    candidates = sorted(
        [{"tick": t, "score": s} for t, s in community_index.items()],
        key=lambda x: x["score"],
        reverse=True,
    )

    result: dict = {
        "status": "unresolved",
        "resolved_tick": None,
        "source": "unresolved",
        "candidates": candidates[:10],  # top 10
        "community_index_size": len(community_index),
        "reason": "",
        "validation": {"valid": False, "reason": ""},
    }

    # ── Priority 1: Explicit override ────────────────────────────────────────
    if explicit_tick:
        valid, vreason = _validate_tick(explicit_tick, known_ticks)
        result["resolved_tick"] = explicit_tick
        result["source"] = "explicit"
        result["validation"] = {"valid": valid, "reason": vreason}
        if valid:
            result["status"] = "resolved"
            result["reason"] = f"Explicit tick '{explicit_tick}' provided and validated"
        else:
            # Explicit override is honored even if unvalidated, but flag it
            result["status"] = "resolved"
            result["reason"] = (
                f"Explicit tick '{explicit_tick}' provided but not found in local data "
                f"({vreason}). Honoring explicit override."
            )
        return result

    # ── Priority 2: /raw knowledge base inference ────────────────────────────
    if candidates:
        top = candidates[0]
        tick = top["tick"]
        valid, vreason = _validate_tick(tick, known_ticks)

        # Determine source based on what contributed
        trades_dir = raw_dir / "tagclaw-trades"
        trending_ticks = set()
        if trades_dir.is_dir():
            trending_ticks = set(_extract_ticks_from_trending(
                _load_json(trades_dir / "trending.json")
            ))

        if tick in trending_ticks:
            source = "raw_trending"
        else:
            source = "raw_inference"

        result["resolved_tick"] = tick
        result["source"] = source
        result["validation"] = {"valid": valid, "reason": vreason}
        result["status"] = "resolved"
        result["reason"] = (
            f"Tick '{tick}' inferred from local /raw data "
            f"(score: {top['score']}, source: {source}, "
            f"{len(candidates)} candidates found)"
        )
        return result

    # ── Priority 3: Validated fallback ───────────────────────────────────────
    # IPShare is the canonical self-IP community on TagClaw.
    # Only use as fallback when no /raw data is available at all.
    fallback = "IPShare"
    result["resolved_tick"] = fallback
    result["source"] = "validated_fallback"
    result["validation"] = {"valid": True, "reason": "canonical_self_ip_community"}
    result["status"] = "fallback"
    result["reason"] = (
        f"No /raw data available for inference. Using '{fallback}' as validated "
        f"fallback (canonical self-IP community on TagClaw)."
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve intro-post tick/community")
    parser.add_argument("--workspace", required=True, help="OpenClaw workspace path")
    parser.add_argument("--tick", default=None, help="Explicit tick override")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    explicit_tick = args.tick or os.environ.get("INTRO_TICK") or None

    result = resolve(workspace, explicit_tick)

    # Output JSON to stdout
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()  # trailing newline

    # Exit code: 0 if resolved/fallback, 1 if unresolved
    sys.exit(0 if result["status"] in ("resolved", "fallback") else 1)


if __name__ == "__main__":
    main()
