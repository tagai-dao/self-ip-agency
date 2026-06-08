#!/usr/bin/env python3
"""archive_conversations_to_wiki.py — File substantive conversations into wiki/queries.

Karpathy LLM-Wiki principle #4 — "good answers shouldn't disappear into
chat history; they compound in the knowledge base." Today we have two
huge conversation graveyards on this box:

  1. Claude Code sessions:
       ~/.claude/projects/<project>/<session_id>.jsonl
  2. OpenClaw agent sessions (main / bookmarker / trader):
       ~/.openclaw/agents/<agent>/sessions/<session_id>.jsonl

Almost all of these are cron heartbeat noise (NO_REPLY, HEARTBEAT_OK,
status acks). A small minority are substantive synthesis — those are the
"good answers" we want to file.

The filter (all must pass):
  - mtime is stable > MIN_QUIESCE_HOURS (session no longer being written)
  - ≥ MIN_USER_TURNS user messages in the transcript
  - last assistant message body ≥ MIN_LAST_LENGTH chars
  - the conversation does NOT look like cron / heartbeat / runtime-status
    choreography (``[cron:...]``, ``Run the ... cycle``, ``Current time:``,
    selfip heartbeat commands, ...)
  - last assistant message does NOT match the SKIP_PATTERNS regex
    (NO_REPLY, HEARTBEAT_OK, single-emoji acks, ...)

State: ``runtime/shared/conversation-archive-state.json`` maps each
seen session path to its last-archived mtime; a session is only re-filed
when its mtime advances. ``MAX_NEW_PER_RUN`` caps how much disk we
generate per cron tick so the first run against a 12k-session backlog
doesn't melt anything.

Output: ``wiki/queries/YYYY-MM-DD/conversation-{agent}-{session_short}.md``
via the file_to_wiki_query adapter — with a structured ledger entry in
``runtime/shared/wiki-events.jsonl`` rather than polluting ``wiki/log.md``.

Usage:
  python3 archive_conversations_to_wiki.py
  python3 archive_conversations_to_wiki.py --dry-run
  python3 archive_conversations_to_wiki.py --max-new 100
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import the file_to_wiki_query helper (sibling script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_to_wiki_query import file_brief_to_wiki  # noqa: E402

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1])
STATE_PATH = WORKSPACE / "runtime" / "shared" / "conversation-archive-state.json"

# Source locations.
OPENCLAW_AGENTS_DIR = Path.home() / ".openclaw" / "agents"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Filter knobs — tunable via CLI flags.
MIN_QUIESCE_HOURS = 2
MIN_USER_TURNS = 2
MIN_LAST_LENGTH = 200
MIN_SINGLE_TURN_SUBSTANTIVE_LAST_LENGTH = 400
MAX_NEW_PER_RUN = 50
MAX_BODY_CHARS = 12000  # cap on each section's char count in the archived MD

# Skip if the LAST assistant message matches any of these.
# Lowercased, plain-text match against the first ~80 chars.
SKIP_PATTERNS = re.compile(
    r"^("
    r"no_reply|"
    r"heartbeat_ok|"
    r"NO_REPLY|"
    r"HEARTBEAT_OK|"
    r"ok\b|"
    r"✅|"
    r"❌|"
    r"\.+$|"
    r"done\.?$"
    r")",
    re.IGNORECASE,
)

# Strong operational-noise markers. These are intentionally high-confidence
# patterns so we stop archiving cron/runtime transcripts into wiki/queries/
# while avoiding false positives on genuine analysis sessions.
OPS_NOISE_PATTERNS = [
    re.compile(r"^\s*\[cron:[^\]]+\]", re.IGNORECASE),
    re.compile(r"\bselfip-[a-z0-9-]+\b", re.IGNORECASE),
    re.compile(r"\bheartbeat\b", re.IGNORECASE),
    re.compile(r"\bHEARTBEAT_OK\b", re.IGNORECASE),
    re.compile(r"\bNO_REPLY\b", re.IGNORECASE),
    re.compile(r"\bcurrent time:\b", re.IGNORECASE),
    re.compile(r"\b(run|rerun)\s+the\b.*\b(cycle|heartbeat|runtime|maintenance|cron)\b", re.IGNORECASE),
]

# Counter-signals that usually indicate the user is asking for real synthesis
# rather than dispatching an operational chore.
KNOWLEDGE_SIGNALS = [
    re.compile(r"\breview\b", re.IGNORECASE),
    re.compile(r"\banaly[sz]e\b", re.IGNORECASE),
    re.compile(r"\bevaluate\b", re.IGNORECASE),
    re.compile(r"\bdiagnos(?:e|is)\b", re.IGNORECASE),
    re.compile(r"\bcompare\b", re.IGNORECASE),
    re.compile(r"\bbrief\b", re.IGNORECASE),
    re.compile(r"\bdigest\b", re.IGNORECASE),
    re.compile(r"\breport\b", re.IGNORECASE),
    re.compile(r"分析|评估|复盘|总结|方案|报告|对比|原因|诊断"),
]

CRON_TITLE_PATTERNS = [
    re.compile(r"^\s*(?:\[[^\]]+\]\s*)*\[cron:[^\]]+\]", re.IGNORECASE),
    re.compile(r"^\s*run(?:\s+the)?\b", re.IGNORECASE),
    re.compile(r"\bselfip-[a-z0-9-]+\b", re.IGNORECASE),
]

LOW_QUALITY_ASSISTANT_PATTERNS = [
    re.compile(r"^\[assistant turn failed before producing content\]$", re.IGNORECASE),
    re.compile(r"^announce_skip$", re.IGNORECASE),
]

LOW_SIGNAL_USER_PATTERNS = [
    re.compile(r"^\[queued messages while agent was busy\]", re.IGNORECASE),
]

SUBAGENT_TITLE_SKIP_PATTERNS = [
    re.compile(r"\[subagent context\]", re.IGNORECASE),
    re.compile(r"results auto-announce to your requester", re.IGNORECASE),
    re.compile(r"do not busy-poll for status", re.IGNORECASE),
    re.compile(r"^begin\.$", re.IGNORECASE),
    re.compile(r"^begin\.", re.IGNORECASE),
    re.compile(r"your assigned task is in the system prompt", re.IGNORECASE),
    re.compile(r"^workspace facts:?$", re.IGNORECASE),
    re.compile(r"^user requirement:?$", re.IGNORECASE),
    re.compile(r"^implementation requirements:?$", re.IGNORECASE),
]

ASSISTANT_TITLE_SKIP_PATTERNS = [
    re.compile(r"^(now )?let me\b", re.IGNORECASE),
    re.compile(r"^i(?:'|’)ll\b", re.IGNORECASE),
    re.compile(r"^i will\b", re.IGNORECASE),
    re.compile(r"^first,?\b", re.IGNORECASE),
    re.compile(r"^done\.?$", re.IGNORECASE),
]


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "archived": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "archived": {}}


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=STATE_PATH.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, STATE_PATH)


def _flatten_content(content: Any) -> str:
    """Both Claude and OpenClaw store assistant content as either a string
    or a list of blocks ``[{type, text}, ...]``. Reduce to one string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t in ("text", "Text") and b.get("text"):
                parts.append(b["text"])
            elif t == "tool_use":
                # Skip tool calls — they're machinery, not answers.
                continue
        return "\n".join(parts).strip()
    return ""


def _looks_like_operational_noise(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    """Return (is_noise, reason) for cron / heartbeat / runtime-status threads.

    We intentionally rely on strong command-like signals rather than broad
    words like "status" alone; this keeps real analytical conversations
    fileable while blocking scheduled operational transcripts.
    """
    first_user = next((m for m in messages if m["role"] == "user"), None)
    last_asst = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
    first_user_text = (first_user or {}).get("text", "").strip()
    last_asst_text = (last_asst or {}).get("text", "").strip()
    title_seed = _derive_title(messages, "")

    strong_hits: list[str] = []
    for label, text in (
        ("first-user", first_user_text[:1200]),
        ("title", title_seed[:200]),
        ("last-assistant", last_asst_text[:600]),
    ):
        if not text:
            continue
        for rx in OPS_NOISE_PATTERNS:
            if rx.search(text):
                strong_hits.append(f"{label}:{rx.pattern}")

    if not strong_hits:
        return (False, "no operational-noise markers")

    knowledge_hit = any(
        rx.search(first_user_text) or rx.search(last_asst_text[:1200])
        for rx in KNOWLEDGE_SIGNALS
    )
    if knowledge_hit:
        return (False, "knowledge signals present")

    return (True, ", ".join(strong_hits[:3]))


def _iter_session_messages(path: Path) -> list[dict[str, Any]]:
    """Walk a JSONL file and return ordered list of message dicts:
    [{'role': 'user'|'assistant'|'system', 'text': str, 'ts': str}, ...]"""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") not in ("message", None):
            continue
        msg = rec.get("message") or rec
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        body = _flatten_content(msg.get("content"))
        if not body:
            continue
        out.append({"role": role, "text": body, "ts": rec.get("timestamp", "")})
    return out


def _session_qualifies(messages: list[dict[str, Any]], path: Path,
                        min_user_turns: int, min_last_length: int) -> tuple[bool, str]:
    if _path_is_excluded(path):
        return (False, "excluded artifact path")
    noisy, noisy_reason = _looks_like_operational_noise(messages)
    if noisy:
        return (False, f"operational noise: {noisy_reason}")
    substantive_user, substantive_answer = _select_archive_pair(messages)
    if not substantive_answer:
        return (False, "no substantive answer block")
    user_turns = sum(1 for m in messages if m["role"] == "user" and _is_substantive_user_text(m["text"]))
    if user_turns < min_user_turns:
        if user_turns == 1 and len(substantive_answer["text"]) >= MIN_SINGLE_TURN_SUBSTANTIVE_LAST_LENGTH:
            return (True, "single-turn substantive")
        return (False, f"only {user_turns} user turns")
    body = substantive_answer["text"]
    if len(body) < min_last_length:
        return (False, f"last assistant only {len(body)} chars")
    if _is_low_quality_assistant_text(body):
        return (False, "last message matches skip pattern")
    return (True, "ok")


def _mtime_age_hours(path: Path) -> float:
    try:
        m = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - m).total_seconds() / 3600.0
    except Exception:
        return 0.0


def _path_is_excluded(path: Path) -> bool:
    name = str(path)
    return any(tag in name for tag in (".checkpoint.", ".trajectory.", ".reset.", ".bak."))


def _clean_title_line(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", ">", "-", "*", "`")):
            line = line.lstrip("#>*-` ").strip()
        if not line:
            continue
        if len(line) > 80:
            line = line[:77] + "..."
        return line
    return ""


def _extract_user_title_candidate(text: str) -> str:
    """Extract a human-meaningful title seed from a user message.

    Subagent wrappers often prepend boilerplate like "[Subagent Context]",
    "Begin.", or workspace facts before the real task. Skip those lines and
    prefer the actual task sentence.
    """
    lines = text.splitlines()
    for raw_line in lines:
        line = _clean_title_line(raw_line)
        if not line:
            continue
        lowered = line.lower()
        if any(rx.search(line) for rx in SUBAGENT_TITLE_SKIP_PATTERNS):
            continue
        if line.startswith("[Subagent Task]:"):
            task = line.split(":", 1)[1].strip()
            return _clean_title_line(task) or line
        if lowered.startswith("repo: ") or lowered.startswith("base branch: "):
            continue
        return line
    return ""


def _extract_assistant_title_candidate(text: str) -> str:
    for raw_line in text.splitlines():
        line = _clean_title_line(raw_line)
        if not line:
            continue
        if any(rx.search(line) for rx in ASSISTANT_TITLE_SKIP_PATTERNS):
            continue
        return line
    return _clean_title_line(text)


def _looks_like_cron_title_seed(text: str) -> bool:
    seed = text.strip()
    if not seed:
        return False
    return any(rx.search(seed) for rx in CRON_TITLE_PATTERNS)


def _is_low_quality_assistant_text(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    if SKIP_PATTERNS.match(s[:80]):
        return True
    return any(rx.match(s) for rx in LOW_QUALITY_ASSISTANT_PATTERNS)


def _is_substantive_user_text(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    if any(rx.search(s) for rx in LOW_SIGNAL_USER_PATTERNS):
        return False
    if _looks_like_cron_title_seed(s):
        return False
    if any(rx.search(s[:1200]) for rx in OPS_NOISE_PATTERNS):
        return False
    return True


def _select_archive_pair(messages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Choose the last substantive user turn and the assistant reply block that follows it."""
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg["role"] != "user":
            continue
        if not _is_substantive_user_text(msg["text"]):
            continue
        assistant_parts: list[str] = []
        assistant_ts = ""
        for later in messages[idx + 1:]:
            if later["role"] == "user":
                break
            if later["role"] != "assistant":
                continue
            text = later["text"].strip()
            if not text or _is_low_quality_assistant_text(text):
                continue
            assistant_parts.append(text)
            assistant_ts = later.get("ts", "") or assistant_ts
        if assistant_parts:
            answer = {
                "role": "assistant",
                "text": "\n\n".join(assistant_parts).strip(),
                "ts": assistant_ts,
            }
            return msg, answer
    return None, None


def _derive_title(messages: list[dict[str, Any]], default: str) -> str:
    """Prefer the first user line, but beautify cron-style sessions.

    For operational sessions whose first user line is a cron command or
    runtime dispatch string, fall back to the final assistant reply's first
    meaningful line so archives do not get ugly "[cron:...] Run: bash ..."
    titles.
    """
    selected_user, selected_answer = _select_archive_pair(messages)
    if selected_user:
        selected_user_title = _extract_user_title_candidate(selected_user["text"])
        if selected_user_title and not _looks_like_cron_title_seed(selected_user_title):
            return selected_user_title
    if selected_answer:
        selected_answer_title = _extract_assistant_title_candidate(selected_answer["text"])
        if selected_answer_title:
            return selected_answer_title

    first_user_title = ""
    for m in messages:
        if m["role"] != "user":
            continue
        first_user_title = _extract_user_title_candidate(m["text"])
        if first_user_title:
            break
    if first_user_title and not _looks_like_cron_title_seed(first_user_title):
        return first_user_title

    for m in reversed(messages):
        if m["role"] != "assistant":
            continue
        assistant_title = _extract_assistant_title_candidate(m["text"])
        if assistant_title:
            return assistant_title
    if first_user_title:
        return first_user_title
    return default


def _build_archive_md(messages: list[dict[str, Any]], session_id: str,
                       agent: str, ts_first: str, ts_last: str) -> str:
    selected_user, selected_answer = _select_archive_pair(messages)
    first_user = selected_user or next((m for m in messages if m["role"] == "user"), None)
    last_asst = selected_answer or next((m for m in reversed(messages) if m["role"] == "assistant"), None)
    user_block = (first_user["text"] if first_user else "").strip()
    answer_block = (last_asst["text"] if last_asst else "").strip()
    if len(user_block) > MAX_BODY_CHARS:
        user_block = user_block[:MAX_BODY_CHARS] + "\n\n_…(用户消息已截断)_"
    if len(answer_block) > MAX_BODY_CHARS:
        answer_block = answer_block[:MAX_BODY_CHARS] + "\n\n_…(回答已截断)_"
    user_turns = sum(1 for m in messages if m["role"] == "user")
    asst_turns = sum(1 for m in messages if m["role"] == "assistant")
    body = (
        f"> Conversation archive: agent=`{agent}`, session=`{session_id}`, "
        f"{user_turns} user turns, {asst_turns} assistant turns.\n"
        f"> Started: `{ts_first or '?'}`  ·  Last reply: `{ts_last or '?'}`\n\n"
        f"## User's question\n\n{user_block}\n\n"
        f"## Final assistant reply\n\n{answer_block}\n"
    )
    return body


def archive_one(session_path: Path, agent: str,
                 min_user_turns: int, min_last_length: int,
                 min_quiesce_hours: float, dry_run: bool) -> tuple[str, str | None]:
    """Returns (status, output_path_str_or_None). status ∈
    {'archived','skipped','filtered','too_fresh','error'}."""
    if _mtime_age_hours(session_path) < min_quiesce_hours:
        return ("too_fresh", None)
    messages = _iter_session_messages(session_path)
    if not messages:
        return ("filtered", None)
    ok, reason = _session_qualifies(messages, session_path, min_user_turns, min_last_length)
    if not ok:
        return ("filtered", None)
    session_id = session_path.stem
    title_seed = _derive_title(messages, f"Conversation {session_id[:8]}")
    title = f"[{agent}] {title_seed}"
    # Derive file_stem so multiple sessions don't collide on the same day.
    file_stem = f"conversation-{agent}-{session_id[:8]}"
    ts_first = messages[0].get("ts") or ""
    ts_last = messages[-1].get("ts") or ""
    # Derive the date subdir from the session's first message (or last as
    # fallback) so backfilled history spreads across the actual calendar
    # rather than dumping thousands of files into "today".
    date_override: str | None = None
    for candidate in (ts_first, ts_last):
        if isinstance(candidate, str) and len(candidate) >= 10 and candidate[4] == "-":
            date_override = candidate[:10]
            break
    body_md = _build_archive_md(messages, session_id, agent, ts_first, ts_last)
    # Write to a temp file and feed it through file_to_wiki_query — that
    # way log.md + frontmatter conventions stay consistent across all
    # auto-filed wiki entries (briefs, digests, conversations).
    if dry_run:
        return ("archived", f"(dry-run) wiki/queries/<today>/{file_stem}.md")
    with tempfile.NamedTemporaryFile(
        "w", dir="/tmp", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(body_md)
        tmp = f.name
    try:
        out = file_brief_to_wiki(
            source_md_path=Path(tmp),
            source_agent=agent if agent in ("main", "bookmarker", "trader") else "claude-dispatch",
            title=title,
            tags=["conversation", "archive", agent],
            related_concepts=[],
            file_stem=file_stem,
            date_override=date_override,
        )
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    return ("archived", str(out) if out else None)


def _scan_openclaw() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if not OPENCLAW_AGENTS_DIR.exists():
        return out
    for agent in ("main", "bookmarker", "trader"):
        sessions = OPENCLAW_AGENTS_DIR / agent / "sessions"
        if not sessions.exists():
            continue
        for f in sessions.iterdir():
            if not f.is_file() or f.suffix != ".jsonl":
                continue
            # Skip resets / backups / checkpoints / trajectories — those
            # are recovery artifacts, not active conversations.
            name = f.name
            if any(tag in name for tag in (".bak", ".reset", ".checkpoint", ".trajectory")):
                continue
            out.append((f, agent))
    return out


def _scan_claude() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if not CLAUDE_PROJECTS_DIR.exists():
        return out
    for proj in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            out.append((f, "claude"))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-new", type=int, default=MAX_NEW_PER_RUN)
    p.add_argument("--min-user-turns", type=int, default=MIN_USER_TURNS)
    p.add_argument("--min-last-length", type=int, default=MIN_LAST_LENGTH)
    p.add_argument("--min-quiesce-hours", type=float, default=float(MIN_QUIESCE_HOURS))
    p.add_argument("--sources", default="openclaw,claude",
                   help="Comma list of sources to scan: openclaw,claude")
    args = p.parse_args()

    sources = set(s.strip() for s in args.sources.split(","))
    state = _read_state()
    archived_map: dict[str, float] = state.setdefault("archived", {})

    pool: list[tuple[Path, str]] = []
    if "openclaw" in sources:
        pool.extend(_scan_openclaw())
    if "claude" in sources:
        pool.extend(_scan_claude())

    # Order newest-first so a backlog run grabs the freshest content.
    pool.sort(key=lambda x: x[0].stat().st_mtime if x[0].exists() else 0, reverse=True)

    counts = {"archived": 0, "filtered": 0, "too_fresh": 0, "already_archived": 0, "error": 0}
    new_outputs: list[str] = []

    for session_path, agent in pool:
        if counts["archived"] >= args.max_new:
            break
        key = str(session_path)
        try:
            mtime = session_path.stat().st_mtime
        except Exception:
            counts["error"] += 1
            continue
        if archived_map.get(key) == mtime:
            counts["already_archived"] += 1
            continue
        try:
            status, out = archive_one(
                session_path, agent,
                args.min_user_turns, args.min_last_length,
                args.min_quiesce_hours, args.dry_run,
            )
        except Exception as e:
            print(f"[conv-archive] error on {session_path.name}: {e}", file=sys.stderr)
            counts["error"] += 1
            continue
        counts[status] = counts.get(status, 0) + 1
        if status == "archived":
            if not args.dry_run:
                archived_map[key] = mtime
            if out:
                new_outputs.append(out)

    if not args.dry_run:
        state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_state(state)

    print(
        f"[conv-archive] {'(dry-run) ' if args.dry_run else ''}"
        f"archived={counts['archived']} filtered={counts['filtered']} "
        f"too_fresh={counts.get('too_fresh', 0)} "
        f"already_archived={counts['already_archived']} "
        f"errors={counts['error']} (pool={len(pool)})"
    )
    for o in new_outputs[:10]:
        print(f"  ✓ {o}")
    if len(new_outputs) > 10:
        print(f"  …(+{len(new_outputs) - 10} more)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
