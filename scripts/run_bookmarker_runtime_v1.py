#!/usr/bin/env python3
"""run_bookmarker_runtime_v1.py — Native bookmarker cycle runtime.

Replaces the dev-claude.sh / claude CLI dependency with a self-contained
Python runtime that handles the bookmarker social curation cycle:

  1. Read feed from TagClaw API
  2. Score and select posts for curation
  3. Execute curation actions (like/curate)
  4. Write result.json and latest.json

No LLM dependency. Uses TagClaw API directly.

Usage (called by bookmarker-cycle.sh):
    cd $WORKSPACE && python3 scripts/run_bookmarker_runtime_v1.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
RUNTIME_BOOKMARKER = WORKSPACE / "runtime" / "bookmarker"
RUNTIME_SHARED = WORKSPACE / "runtime" / "shared"
CONFIG_DIR = WORKSPACE / "config"
BEHAVIOR_FILE = WORKSPACE / "agents" / "bookmarker.md"


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent),
                                     suffix=".tmp", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = f.name
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# TagClaw API
# ---------------------------------------------------------------------------

def resolve_api_key() -> str:
    """Resolve TagClaw API key from skill env or legacy credentials."""
    skill_env = WORKSPACE / "skills" / "tagclaw" / ".env"
    if skill_env.exists():
        for line in skill_env.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "TAGCLAW_API_KEY" and v:
                return v

    legacy = Path.home() / ".config" / "tagclaw" / "credentials.json"
    if legacy.exists():
        try:
            creds = json.loads(legacy.read_text())
            return creds.get("apiKey") or creds.get("api_key") or creds.get("API_KEY") or ""
        except Exception:
            pass
    return ""


def tagclaw_get(endpoint: str, api_key: str) -> dict | list | None:
    """HTTP GET against TagClaw API. Returns parsed JSON or None."""
    import urllib.request
    import urllib.error

    base_url = "https://bsc-api.tagai.fun/tagclaw"
    url = f"{base_url}{endpoint}"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def tagclaw_post(endpoint: str, api_key: str, data: dict) -> dict | None:
    """HTTP POST against TagClaw API."""
    import urllib.request
    import urllib.error

    base_url = "https://bsc-api.tagai.fun/tagclaw"
    url = f"{base_url}{endpoint}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Curation logic
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load agency config for social settings."""
    config_path = CONFIG_DIR / "agency.config.yaml"
    if not config_path.exists():
        return {}
    try:
        # Parse YAML minimally without PyYAML dependency
        text = config_path.read_text()
        # Extract key social settings via simple parsing
        config: dict[str, Any] = {}
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#") or ":" not in s:
                continue
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:
                try:
                    config[k] = float(v) if "." in v else int(v)
                except ValueError:
                    config[k] = v
        return config
    except Exception:
        return {}


def score_post(post: dict) -> float:
    """Simple heuristic scoring for curation candidate posts."""
    score = 0.0

    # Engagement signals
    likes = post.get("likes", 0) or 0
    replies = post.get("replies", 0) or 0
    curates = post.get("curates", 0) or 0
    score += min(likes * 0.5, 5.0)
    score += min(replies * 1.0, 5.0)
    score += min(curates * 0.3, 3.0)

    # Content quality signals
    content = post.get("content", "") or ""
    word_count = len(content.split())
    if 20 <= word_count <= 300:
        score += 2.0  # reasonable length
    if word_count < 5:
        score -= 2.0  # too short

    # Recency bonus
    created = post.get("created_at") or post.get("createdAt")
    if created:
        try:
            ts = created.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if age_hours < 1:
                score += 3.0
            elif age_hours < 6:
                score += 1.5
            elif age_hours < 24:
                score += 0.5
        except Exception:
            pass

    return max(score, 0.0)


def run_curation_cycle() -> dict:
    """Execute one bookmarker curation cycle. Returns result dict."""
    ts_start = now_iso()
    api_key = resolve_api_key()
    actions_taken: list[dict] = []
    errors: list[str] = []
    feed_size = 0

    # 1. Fetch feed
    feed = tagclaw_get("/feed", api_key)
    if feed is None:
        errors.append("Failed to fetch feed from TagClaw API")
        feed = []
    elif isinstance(feed, dict):
        feed = feed.get("posts") or feed.get("items") or feed.get("data") or []
    feed_size = len(feed)

    # 2. Score and rank posts
    scored: list[tuple[float, dict]] = []
    for post in feed:
        if not isinstance(post, dict):
            continue
        s = score_post(post)
        scored.append((s, post))
    scored.sort(key=lambda x: x[0], reverse=True)

    # 3. Select top candidates for curation (conservative: max 3 per cycle)
    config = load_config()
    max_curations = min(int(config.get("curation_vp_pct", 0.6) * 5), 3)
    candidates = scored[:max_curations]

    # 4. Execute curation actions
    for score_val, post in candidates:
        post_id = post.get("id") or post.get("postId") or post.get("post_id")
        if not post_id:
            continue

        # Try curate action
        result = tagclaw_post("/curate", api_key, {"postId": str(post_id)})
        if result is not None:
            actions_taken.append({
                "action": "curate",
                "post_id": str(post_id),
                "score": round(score_val, 2),
                "status": "ok"
            })
        else:
            # Fallback: try like
            result = tagclaw_post("/like", api_key, {"postId": str(post_id)})
            if result is not None:
                actions_taken.append({
                    "action": "like",
                    "post_id": str(post_id),
                    "score": round(score_val, 2),
                    "status": "ok"
                })
            else:
                actions_taken.append({
                    "action": "curate",
                    "post_id": str(post_id),
                    "score": round(score_val, 2),
                    "status": "failed"
                })

    # 5. Build result
    ok_actions = [a for a in actions_taken if a["status"] == "ok"]
    status = "ok" if not errors else ("partial" if ok_actions else "blocked")

    return {
        "schema": "bookmarker.result.v1",
        "status": status,
        "started_at": ts_start,
        "completed_at": now_iso(),
        "feed_size": feed_size,
        "candidates_scored": len(scored),
        "actions_taken": actions_taken,
        "actions_ok": len(ok_actions),
        "actions_failed": len(actions_taken) - len(ok_actions),
        "errors": errors,
        "execution_backend": "native-python",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[bookmarker-runtime] Starting native curation cycle at {now_iso()}")

    try:
        result = run_curation_cycle()
    except Exception as e:
        result = {
            "schema": "bookmarker.result.v1",
            "status": "blocked",
            "started_at": now_iso(),
            "completed_at": now_iso(),
            "errors": [f"Runtime exception: {e}"],
            "execution_backend": "native-python",
            "traceback": traceback.format_exc(),
        }

    # Write result.json
    atomic_write_json(RUNTIME_BOOKMARKER / "result.json", result)
    print(f"[bookmarker-runtime] Wrote result.json (status={result['status']})")

    # Write latest.json
    latest = {
        "schema": "bookmarker.latest.v1",
        "generated_at": now_iso(),
        "status": result["status"],
        "source": "run_bookmarker_runtime_v1.py",
        "actions_ok": result.get("actions_ok", 0),
        "feed_size": result.get("feed_size", 0),
    }
    atomic_write_json(RUNTIME_BOOKMARKER / "latest.json", latest)
    print(f"[bookmarker-runtime] Wrote latest.json")

    # Update shared runtime-status
    rs_path = RUNTIME_SHARED / "runtime-status.json"
    try:
        rs = json.loads(rs_path.read_text()) if rs_path.exists() else {}
    except Exception:
        rs = {}
    rs.setdefault("schema", "runtime-status.v1")
    rs["bookmarker"] = {"status": result["status"], "updated_at": now_iso()}
    rs.pop("bootstrap", None)
    atomic_write_json(rs_path, rs)

    status_code = 0 if result["status"] in ("ok", "partial") else 1
    print(f"[bookmarker-runtime] Cycle complete (exit={status_code})")
    return status_code


if __name__ == "__main__":
    sys.exit(main())
