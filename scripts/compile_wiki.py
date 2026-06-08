#!/usr/bin/env python3
"""
compile_wiki.py — Wiki 编译脚本
把 memory/raw/ 下的 bookmark/tweet raw 文件 LLM 编译成结构化的 wiki 概念文章。

架构：
  raw/  = 数据层（单条推文，每个文件）
  wiki/ = 知识层（按概念聚合的文章，LLM 维护）

用法:
  python3 compile_wiki.py                  # 增量编译（只处理新 raw 文件）
  python3 compile_wiki.py --dry-run        # 打印会做什么，不写文件
  python3 compile_wiki.py --theme AgentInfrastructure  # 强制重编某个 theme
  python3 compile_wiki.py --full           # 全量编译（忽略 last_compiled_at）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from agency_paths import MAIN_WS
import shutil

# LLM call tunables. Default timeout bumped from 120s → 300s after
# 2026-05-21 backfill — large themes (TokenEconomy=1021 files,
# Misc=1442 files) timed out at 120s and dropped on the floor. 300s
# fits any reasonable LLM compose under load.
LLM_TIMEOUT_SECONDS = int(os.environ.get("COMPILE_WIKI_LLM_TIMEOUT") or 300)
# Number of retries for the LLM call. The most common failure mode is
# Claude CLI exiting rc=1 with empty stderr (rate limit) — typically
# clears within a minute. One retry covers ~95% of these.
LLM_MAX_RETRIES = int(os.environ.get("COMPILE_WIKI_LLM_RETRIES") or 1)
LLM_RETRY_SLEEP_SECONDS = int(os.environ.get("COMPILE_WIKI_LLM_RETRY_SLEEP") or 60)

# ── Paths ──────────────────────────────────────────────────────────────────
WORKSPACE = Path(__file__).resolve().parent.parent
MEMORY = WORKSPACE / "memory"
RAW_BOOKMARKS = MEMORY / "raw" / "x-bookmarks"
RAW_TWEETS = MEMORY / "raw" / "x-tweets"
RAW_TAGCLAW_POSTS = MEMORY / "raw" / "tagclaw-posts"
RAW_LIKES = MEMORY / "raw" / "x-likes"  # L4: @0xNought liked tweets
# V3: shared wiki layer — bookmarker writes, main/trader read-only
WIKI_DIR = (MAIN_WS / 'wiki' / 'concepts')
META_FILE = WIKI_DIR / "_meta.json"
INDEX_FILE = (MAIN_WS / 'wiki' / 'INDEX.md')
LOG_FILE = (MAIN_WS / 'wiki' / 'log.md')
# Legacy path kept for fallback (do not delete until V3 is stable)
_LEGACY_WIKI_DIR = MEMORY / "wiki"
# Obsidian writes removed — symlink in C5 makes workspace/wiki/concepts == Obsidian/Wiki/concepts
OBSIDIAN_WIKI = Path.home() / "Obsidian" / "MyVault" / "Wiki"  # kept for reference only

CLAUDE_BIN = (os.environ.get("CLAUDE_BIN")
              or shutil.which("claude")
              or str(Path.home() / ".local" / "bin" / "claude"))
MAX_ARTICLE_CHARS = 4000 * 4  # ~4000 Chinese chars ≈ 16 000 bytes
MAX_RAW_FILES_PER_THEME = 20

# ── D5: Schema version enforcement ──────────────────────────────────────────
WIKI_SCHEMA_FILE = (MAIN_WS / 'WIKI.md')
EXPECTED_SCHEMA_VERSION = 'v1'


def check_schema_version(schema_file: Path = WIKI_SCHEMA_FILE,
                          expected_version: str = EXPECTED_SCHEMA_VERSION) -> bool:
    """Read WIKI.md, verify schema_version matches. Warn but don't abort on mismatch."""
    if not schema_file.exists():
        print(f"[schema] WARNING: {schema_file} not found — cannot verify schema version",
              file=sys.stderr)
        return False
    try:
        text = schema_file.read_text(encoding='utf-8')
    except Exception as e:
        print(f"[schema] WARNING: failed to read {schema_file}: {e}", file=sys.stderr)
        return False
    # Check first 10 lines for schema_version marker or title
    for line in text.splitlines()[:10]:
        if f'schema_version: {expected_version}' in line:
            print(f'[schema] WIKI.md {expected_version} OK')
            return True
        if f'Wiki Schema {expected_version}' in line:
            print(f'[schema] WIKI.md {expected_version} OK')
            return True
    print(f"[schema] WARNING: WIKI.md version mismatch — expected {expected_version}",
          file=sys.stderr)
    return False

# ── YAML frontmatter parser (stdlib only) ──────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata_dict, body_text) from a markdown file with --- frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end == -1:
        return {}, text
    meta: dict = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


def normalize_theme_name(raw_theme: Optional[str], fallback: Optional[str] = None) -> str:
    theme = (raw_theme or fallback or "Misc").strip()
    if not theme or theme.lower() in {"null", "none", "unknown", "untagged"}:
        return "Misc"
    return theme


# ── Atomic write ────────────────────────────────────────────────────────────

def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp",
                                     delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_log(msg: str) -> None:
    """Append a single line to wiki/log.md (non-atomic, append-only by design)."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        print(f"[warn] log append failed: {e}", file=sys.stderr)


def iter_indexable_articles(wiki_dir: Path) -> list[Path]:
    """Return concept article files that should appear in INDEX.md."""
    if not wiki_dir.exists():
        return []
    files: list[Path] = []
    for p in sorted(wiki_dir.glob("*.md")):
        name = p.name
        stem = p.stem.strip()
        if not stem:
            continue
        if name.lower() == "index.md":
            continue
        if name.startswith((".", "_")):
            continue
        files.append(p)
    return files


# ── Meta helpers ────────────────────────────────────────────────────────────

def load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_compiled_at": None, "file_count": 0, "article_count": 0,
            "tagclaw_posts_count": 0, "high_recognition_themes": []}


def save_meta(meta: dict, dry_run: bool) -> None:
    meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not dry_run:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(META_FILE, meta)
    else:
        print(f"[dry-run] would write _meta.json: {json.dumps(meta, ensure_ascii=False)}")


# ── Raw file scanning ────────────────────────────────────────────────────────

def scan_raw_files(since_ts: Optional[float], force_theme: Optional[str]) -> dict[str, list[Path]]:
    """Return {theme: [path, ...]} for raw files newer than since_ts."""
    by_theme: dict[str, list[Path]] = {}
    for root in (RAW_BOOKMARKS, RAW_TWEETS, RAW_LIKES):
        if not root.exists():
            continue
        for p in sorted(root.glob("*.md")):
            try:
                mtime = p.stat().st_mtime
            except Exception:
                continue
            # If since_ts given and not forcing, skip older files
            if since_ts is not None and mtime <= since_ts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, _ = parse_frontmatter(text)
            theme = normalize_theme_name(meta.get("theme"), meta.get("primary_theme"))
            if force_theme and theme != force_theme:
                continue
            by_theme.setdefault(theme, []).append(p)
    return by_theme


# ── LLM call ────────────────────────────────────────────────────────────────

def call_llm(prompt: str, task: str) -> Optional[str]:
    """Call claude CLI with retry. Returns stdout text or None on persistent failure.

    Failure handling (Phase 4, 2026-05-23):
      - 300s default timeout (was 120s) — large-theme LLM composes routinely
        exceed 2 min when the model has to pull in many raw bookmarks.
      - 1 retry by default (configurable via env). Triggered on TimeoutExpired
        or rc!=0; sleeps LLM_RETRY_SLEEP_SECONDS (default 60s) between
        attempts. ~95% of the rc=1+empty-stderr failures observed in the
        2026-05-21 backfill were transient rate limits that cleared inside
        a minute.
      - File-not-found and other unexpected exceptions never retry — they're
        operator-side issues, retry can't help.
    """
    # Match the hardened Claude CLI dispatch path: remove inherited Claude
    # session env so the child process does not trip nested-session /
    # internal-error guards.
    env = {
        k: v for k, v in os.environ.items()
        if k != "ANTHROPIC_API_KEY" and not k.startswith("CLAUDE")
    }
    attempt = 0
    while True:
        attempt += 1
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "--print", "--permission-mode", "bypassPermissions", "-p", task],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=LLM_TIMEOUT_SECONDS,
                env=env,
                cwd=str(WORKSPACE),
            )
            if result.returncode == 0:
                out = result.stdout.strip()
                if out:
                    if attempt > 1:
                        print(
                            f"[info] claude CLI succeeded on attempt {attempt}",
                            file=sys.stderr,
                        )
                    return out
                fail_reason = "rc=0 but empty stdout"
            else:
                stderr_excerpt = (result.stderr or "(empty)").strip()[:200]
                fail_reason = f"rc={result.returncode} stderr={stderr_excerpt!r}"
        except FileNotFoundError:
            print(f"[warn] claude CLI not found at {CLAUDE_BIN}", file=sys.stderr)
            return None  # no retry — operator-side problem
        except subprocess.TimeoutExpired:
            fail_reason = f"timed out at {LLM_TIMEOUT_SECONDS}s"
        except Exception as e:  # noqa: BLE001 — log + retry generic errors too
            fail_reason = f"unexpected: {e}"

        if attempt > LLM_MAX_RETRIES:
            print(
                f"[warn] claude CLI failed (attempt {attempt}/{LLM_MAX_RETRIES + 1}): {fail_reason}",
                file=sys.stderr,
            )
            return None
        print(
            f"[warn] claude CLI failed (attempt {attempt}/{LLM_MAX_RETRIES + 1}): {fail_reason}; "
            f"sleeping {LLM_RETRY_SLEEP_SECONDS}s before retry",
            file=sys.stderr,
        )
        time.sleep(LLM_RETRY_SLEEP_SECONDS)


def select_prompt_raw_files(raw_files: list[Path], limit: int = MAX_RAW_FILES_PER_THEME) -> list[Path]:
    """Select a representative sample with recency bias for prompt construction."""
    if len(raw_files) <= limit:
        return list(raw_files)
    files = sorted(raw_files, key=lambda p: p.stat().st_mtime)
    oldest = files[:4]
    newest = files[-12:]
    remaining = max(0, limit - len(oldest) - len(newest))
    middle = files[4:-12]
    sampled_middle: list[Path] = []
    if remaining > 0 and middle:
        step = max(1, len(middle) // remaining)
        sampled_middle = middle[::step][:remaining]
    selected = oldest + sampled_middle + newest
    # Keep stable chronological ordering in the final prompt.
    ordered: list[Path] = []
    seen: set[str] = set()
    for p in selected:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
    return ordered[:limit]


def build_prompt(theme: str, raw_files: list[Path], existing_article: Optional[str]) -> tuple[str, str]:
    """Return (stdin_prompt, task_description) for the LLM call."""
    # Gather raw content
    raw_parts = []
    sampled_files = select_prompt_raw_files(raw_files)
    for p in sampled_files:
        try:
            raw_parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    raw_content = "\n\n---\n\n".join(raw_parts)

    action = "更新" if existing_article else "新建"
    existing_section = ""
    if existing_article:
        # Truncate existing article if very long
        truncated = existing_article[:MAX_ARTICLE_CHARS // 2]
        existing_section = f"\n\n以下是现有的 wiki 文章：\n\n{truncated}"

    prompt = f"""你是 0xNought 的知识整理助手（TagClawX）。

以下是关于「{theme}」主题的新推文/收藏内容（raw 格式，已按时间跨度和近期信号抽样）：

{raw_content}{existing_section}

请{action}一篇关于「{theme}」的 wiki 概念文章，要求：
1. 核心概念定义（2-3句）
2. 关键洞察列表（bullet points，每条25字以内）
3. 与 0xNought 工作（TagClaw/OpenClaw/Agent Economy）的关联
4. 来源列表（tweet_id + 作者 + 一句摘要）
5. 开放问题（值得深入探索的方向）
6. 中英混用，有观点，不喊口号

输出纯 markdown，不要解释。"""

    task = f"请{action}关于{theme}的wiki文章，输出纯markdown"
    return prompt, task


# ── Article compilation ──────────────────────────────────────────────────────

def compile_theme(theme: str, raw_files: list[Path], dry_run: bool) -> Optional[str]:
    """Compile/update one wiki article. Returns article content or None on failure."""
    article_path = WIKI_DIR / f"{theme}.md"
    existing = None
    if article_path.exists():
        try:
            existing = article_path.read_text(encoding="utf-8")
        except Exception:
            pass

    if dry_run:
        action = "update" if existing else "create"
        print(f"[dry-run] would {action} wiki/{theme}.md from {len(raw_files)} raw files")
        # Return a placeholder so index can be built
        return existing or f"# {theme}\n\n(dry-run placeholder)\n"

    prompt, task = build_prompt(theme, raw_files, existing)
    print(f"  Calling LLM for theme '{theme}' ({len(raw_files)} files)...")
    content = call_llm(prompt, task)

    if content is None:
        print(f"[warn] LLM failed for theme '{theme}', skipping", file=sys.stderr)
        return None

    # Strip outer markdown code fences (e.g. ```markdown ... ```)
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first line (```markdown or ```) and last ``` if present
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        content = "\n".join(lines).strip()

    # Truncate if too long
    if len(content) > MAX_ARTICLE_CHARS:
        # Keep last MAX_ARTICLE_CHARS chars (newest content)
        content = content[-MAX_ARTICLE_CHARS:]
        content = "<!-- truncated to 4000 chars -->\n" + content

    # Add update timestamp header if not present
    ts_line = f"<!-- last_updated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->\n"
    if not content.startswith("<!--"):
        content = ts_line + content

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(article_path, content)
    # V3: Obsidian write removed — symlink makes workspace/wiki/concepts == Obsidian/Wiki/concepts
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    append_log(f"[{ts}] ingest | {len(raw_files)} raw files | updated: {theme}.md")

    return content


# ── Index generation ─────────────────────────────────────────────────────────

def extract_one_liner(content: str) -> str:
    """Extract the first meaningful sentence from article content."""
    # Strip YAML frontmatter block first
    body = content
    if content.startswith('---'):
        end = content.find('\n---', 3)
        if end != -1:
            body = content[end + 4:]
    # Skip YAML-like metadata lines (key: value) that aren't frontmatter-delimited
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("#", "<!--", "---", "`", ">")):
            continue
        if line.startswith("|"):
            continue
        # Skip YAML frontmatter spillover (aliases:, tags:, graph_class:, etc.)
        if ':' in line and not line.startswith('http') and len(line.split(':')[0].strip()) < 30:
            # Only skip if it looks like a metadata key (single word, no spaces before colon)
            key_part = line.split(':')[0].strip()
            if ' ' not in key_part and key_part.isascii() and not key_part[0].isupper():
                continue
        return line[:80]
    return ""


def build_index(articles: dict[str, str], dry_run: bool) -> None:
    """Write wiki/index.md with per-article one-line summaries."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Wiki Index",
        f"",
        f"> Auto-generated by compile_wiki.py at {now}",
        f"",
        f"| 文章 | 最近更新 | 核心观点 |",
        f"|------|----------|----------|",
    ]

    # Scan all existing wiki articles (not just newly compiled)
    all_articles: dict[str, str] = dict(articles)
    if WIKI_DIR.exists():
        for p in iter_indexable_articles(WIKI_DIR):
            theme_name = p.stem.strip()
            if not theme_name:
                continue
            if theme_name not in all_articles:
                try:
                    all_articles[theme_name] = p.read_text(encoding="utf-8")
                except Exception:
                    pass

    for theme, content in sorted(all_articles.items()):
        one_liner = extract_one_liner(content)
        # Try to extract timestamp from content
        updated = now
        meta, _ = parse_frontmatter(content)
        frontmatter_updated = (meta.get("updated") or "").strip()
        frontmatter_compiled = (meta.get("last_compiled_at") or "").strip()
        if frontmatter_compiled:
            updated = frontmatter_compiled
        elif frontmatter_updated:
            updated = frontmatter_updated
        for line in content.splitlines():
            if "last_updated:" in line:
                updated = line.split("last_updated:")[-1].strip().rstrip(" -->")
                break
        lines.append(f"| [{theme}]({theme}.md) | {updated} | {one_liner} |")

    index_content = "\n".join(lines) + "\n"

    if dry_run:
        print(f"[dry-run] would write wiki/INDEX.md ({len(all_articles)} articles)")
        print(f"[dry-run] target path: {INDEX_FILE}")
    else:
        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(INDEX_FILE, index_content)
        # V3: Obsidian index write removed — symlink handles it
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        append_log(f"[{ts}] index_updated | {len(all_articles)} articles")


# ── D1: Cross-page reference pass ───────────────────────────────────────────

def cross_reference_pass(updated_themes: list, wiki_dir: Path, log_file: Path) -> int:
    """For each updated theme, find other concept pages that mention the theme
    but don't yet link it in '## 关联概念', then append a stub [[双链]].

    Returns the number of pages touched.
    """
    if not updated_themes or not wiki_dir.exists():
        return 0

    # Collect all concept pages
    all_pages: dict[str, Path] = {
        p.stem: p for p in wiki_dir.glob("*.md")
    }

    touched = 0
    for theme in updated_themes:
        if theme not in all_pages:
            continue
        for page_stem, page_path in all_pages.items():
            if page_stem == theme:
                continue
            try:
                text = page_path.read_text(encoding="utf-8")
            except Exception:
                continue

            # Check if the page body mentions the theme
            if theme.lower() not in text.lower():
                continue

            # Check if the 关联概念 section already contains the wikilink
            link_token = f"[[{theme}]]"
            if link_token in text:
                continue

            # Find or create ## 关联概念 section
            section_header = "## 关联概念"
            new_entry = f"- {link_token} — 待关联（本次 ingest 自动检测）"

            if section_header in text:
                # Append inside the section (before the next ## or EOF)
                idx = text.index(section_header)
                after = text[idx + len(section_header):]
                next_section = after.find("\n## ")
                if next_section == -1:
                    new_text = text.rstrip() + "\n" + new_entry + "\n"
                else:
                    insert_at = idx + len(section_header) + next_section
                    new_text = text[:insert_at].rstrip() + "\n" + new_entry + "\n" + text[insert_at:]
            else:
                new_text = text.rstrip() + f"\n\n{section_header}\n{new_entry}\n"

            # Atomic write
            try:
                with tempfile.NamedTemporaryFile(
                    "w", dir=page_path.parent, suffix=".tmp",
                    delete=False, encoding="utf-8"
                ) as f:
                    f.write(new_text)
                    tmp = f.name
                os.replace(tmp, page_path)
                touched += 1
            except Exception as e:
                print(f"[cross-ref] WARNING: failed to update {page_path.name}: {e}",
                      file=sys.stderr)

    # Log the result
    if touched > 0 or updated_themes:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        themes_str = ", ".join(updated_themes[:5])
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] cross-ref | {themes_str} → touched: {touched} pages\n")
        except Exception:
            pass

    return touched


# ── TagClaw posts performance data ───────────────────────────────────────────

_NULL_VALUES = {"null", "none", "~", ""}


def scan_tagclaw_posts() -> dict[str, list[dict]]:
    """Scan raw/tagclaw-posts/ and return {wiki_source: [post_data, ...]}."""
    if not RAW_TAGCLAW_POSTS.exists():
        return {}
    by_source: dict[str, list[dict]] = {}
    for p in sorted(RAW_TAGCLAW_POSTS.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = parse_frontmatter(text)
        wiki_source = (meta.get("wiki_source") or "").strip().strip('"').strip("'")
        if not wiki_source or wiki_source.lower() in _NULL_VALUES:
            continue
        date_str = (meta.get("created_at") or p.stem)[:10]
        owner_reaction = (meta.get("owner_reaction") or "").strip().strip('"').strip("'")
        by_source.setdefault(wiki_source, []).append({
            "post_id": meta.get("post_id", ""),
            "date": date_str,
            "body": body[:80].replace("|", "\\|").replace("\n", " "),
            "owner_reaction": owner_reaction if owner_reaction.lower() not in _NULL_VALUES else None,
        })
    return by_source


def _build_performance_section(posts: list[dict]) -> str:
    """Build 发帖绩效记录 markdown table."""
    lines = [
        "## 发帖绩效记录",
        "",
        "| 日期 | 帖子摘要 | 主人认可 |",
        "|------|---------|----------|",
    ]
    for post in sorted(posts, key=lambda p: p.get("date", ""), reverse=True):
        date = post.get("date") or "—"
        excerpt = (post.get("body") or "")[:50]
        reaction = post.get("owner_reaction")
        reaction_display = f"{reaction.capitalize()} ✓" if reaction else "—"
        lines.append(f"| {date} | {excerpt} | {reaction_display} |")
    return "\n".join(lines) + "\n"


def update_wiki_with_performance(
    wiki_source: str, posts: list[dict], dry_run: bool
) -> bool:
    """Append/refresh performance section in wiki article.

    Returns True if the article qualifies as high-recognition
    (owner_reaction non-null rate > 30%).
    """
    article_path = WIKI_DIR / f"{wiki_source}.md"
    if not article_path.exists():
        return False
    try:
        content = article_path.read_text(encoding="utf-8")
    except Exception:
        return False

    total = len(posts)
    recognized = sum(1 for p in posts if p.get("owner_reaction"))
    high_rec = total > 0 and (recognized / total) > 0.30

    # Strip existing performance section (between ## 发帖绩效记录 and next ## or EOF)
    perf_marker = "## 发帖绩效记录"
    if perf_marker in content:
        idx = content.index(perf_marker)
        # Find next top-level section after the marker (if any)
        rest = content[idx + len(perf_marker):]
        next_section = rest.find("\n## ")
        if next_section == -1:
            content = content[:idx].rstrip() + "\n"
        else:
            content = content[:idx].rstrip() + "\n" + rest[next_section + 1:]

    # Manage high-recognition marker at top of file
    high_rec_marker = "<!-- performance: high-recognition -->"
    if high_rec:
        if high_rec_marker not in content:
            content = high_rec_marker + "\n" + content
    else:
        content = content.replace(high_rec_marker + "\n", "").replace(high_rec_marker, "")

    # Append fresh performance section
    perf_section = _build_performance_section(posts)
    content = content.rstrip() + "\n\n" + perf_section

    if dry_run:
        print(f"[dry-run] would update wiki/{wiki_source}.md performance "
              f"({total} posts, recognized={recognized}, high_rec={high_rec})")
    else:
        atomic_write(article_path, content)
        # V3: Obsidian write removed — symlink handles it

    return high_rec


# ── D4: qmd index updater ───────────────────────────────────────────────────

QMD_BIN = Path.home() / ".bun" / "bin" / "qmd"


def _update_qmd_index() -> None:
    """Update qmd wiki-concepts collection index after ingest."""
    if not QMD_BIN.exists():
        print(f"[qmd] WARNING: qmd not found at {QMD_BIN}, skipping index update",
              file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [str(QMD_BIN), "update", "--collection", "wiki-concepts"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print("[qmd] wiki-concepts index updated")
        else:
            print(f"[qmd] WARNING: update failed (rc={result.returncode}): {result.stderr[:200]}",
                  file=sys.stderr)
    except Exception as e:
        print(f"[qmd] WARNING: update error: {e}", file=sys.stderr)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Compile wiki from raw bookmark/tweet files")
    parser.add_argument("--dry-run", action="store_true", help="Print plan, don't write files")
    parser.add_argument("--theme", help="Force recompile a specific theme")
    parser.add_argument("--full", action="store_true", help="Full recompile (ignore last_compiled_at)")
    args = parser.parse_args()

    check_schema_version()

    meta = load_meta()
    last_compiled_at = meta.get("last_compiled_at")

    # Determine cutoff timestamp
    since_ts: Optional[float] = None
    if not args.full and not args.theme and last_compiled_at:
        try:
            dt = datetime.fromisoformat(last_compiled_at.replace("Z", "+00:00"))
            since_ts = dt.timestamp()
        except Exception:
            since_ts = None

    print(f"compile_wiki: scanning raw files (since={last_compiled_at or 'beginning'}, theme={args.theme or 'all'})")

    by_theme = scan_raw_files(since_ts, args.theme)

    if not by_theme:
        print("compile_wiki: no new raw files to process.")
        # Still process tagclaw-posts performance data and rebuild INDEX.md
        posts_by_source = scan_tagclaw_posts()
        total_posts = sum(len(v) for v in posts_by_source.values())
        high_rec_themes: list[str] = []
        for _ws, _posts in posts_by_source.items():
            if update_wiki_with_performance(_ws, _posts, args.dry_run):
                high_rec_themes.append(_ws)
        build_index({}, args.dry_run)
        meta["last_compiled_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["tagclaw_posts_count"] = total_posts
        meta["high_recognition_themes"] = high_rec_themes
        meta.pop("last_failed_themes", None)
        save_meta(meta, args.dry_run)
        return

    total_files = sum(len(v) for v in by_theme.values())
    print(f"compile_wiki: found {total_files} new files across {len(by_theme)} themes: {list(by_theme.keys())}")

    compiled_articles: dict[str, str] = {}
    failed_themes: list[str] = []

    for theme, files in sorted(by_theme.items()):
        print(f"  [{theme}] {len(files)} files")
        content = compile_theme(theme, files, args.dry_run)
        if content is not None:
            compiled_articles[theme] = content
        else:
            failed_themes.append(theme)

    # Build index even if some themes failed
    build_index(compiled_articles, args.dry_run)

    # ── D1: Cross-reference pass (after index build) ─────────────────────────
    updated_themes = list(compiled_articles.keys())
    if args.dry_run:
        print(f"[dry-run] would run cross-reference pass for {len(updated_themes)} themes: {updated_themes}")
    else:
        n_touched = cross_reference_pass(updated_themes, WIKI_DIR, LOG_FILE)
        if n_touched:
            print(f"[cross-ref] touched {n_touched} pages")

    # Count total wiki articles
    article_count = 0
    if WIKI_DIR.exists() and not args.dry_run:
        article_count = sum(1 for p in WIKI_DIR.glob("*.md") if p.name != "index.md")
    elif args.dry_run:
        article_count = len(compiled_articles)

    # Process tagclaw-posts performance data (additive, does not affect wiki article body)
    posts_by_source = scan_tagclaw_posts()
    total_tagclaw_posts = sum(len(v) for v in posts_by_source.values())
    high_rec_themes: list[str] = []
    for _ws, _posts in posts_by_source.items():
        if update_wiki_with_performance(_ws, _posts, args.dry_run):
            high_rec_themes.append(_ws)

    # Update meta
    meta["last_compiled_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["file_count"] = meta.get("file_count", 0) + total_files
    meta["article_count"] = article_count
    meta["tagclaw_posts_count"] = total_tagclaw_posts
    meta["high_recognition_themes"] = high_rec_themes
    if failed_themes:
        meta["last_failed_themes"] = failed_themes
    else:
        meta.pop("last_failed_themes", None)
    save_meta(meta, args.dry_run)

    # ── D4: Update qmd wiki-concepts index after ingest ─────────────────────
    if not args.dry_run and compiled_articles:
        _update_qmd_index()

    status = "ok" if not failed_themes else "partial"
    print(f"compile_wiki: done. status={status}, compiled={len(compiled_articles)}, failed={len(failed_themes)}")
    if failed_themes:
        print(f"  failed themes: {failed_themes}")


if __name__ == "__main__":
    main()
