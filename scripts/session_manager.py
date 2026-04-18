"""
session_manager.py — Session lifecycle management for claude-recall.

Handles:
- Session start/end tracking
- Marker file lifecycle (skip duplicate loads per session)
- Last-session summary retrieval for continuity
- Session note building
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

from utils import (
    cwd_to_slug, get_project_dir, now_str,
    DEBUG_LOG, safe_unlink,
)


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SESSION: {msg}\n")
    except Exception:
        pass


# ── Marker lifecycle ──────────────────────────────────────────────────────────

def _marker_path(session_id: str, slug: str) -> Path:
    """Unique marker file per session + project."""
    if not session_id or session_id == "unknown":
        session_id = f"unknown_{now_str()}_{os.getpid()}"
    return Path.home() / ".claude" / f".recall_{slug}_{session_id}"


def should_load_context(session_id: str, cwd: Path) -> bool:
    """Return True if context should be loaded for this session.

    Checks marker file. If marker exists and is < 4 hours old, skip.
    If marker is stale (>4h, crash recovery), remove it and load.
    """
    slug = cwd_to_slug(cwd)
    marker = _marker_path(session_id, slug)

    if marker.exists():
        try:
            age = time.time() - marker.stat().st_mtime
            if age > 4 * 3600:  # 4 hours = stale (crash recovery)
                safe_unlink(marker)
                _debug(f"Stale marker removed: {marker.name} (age={age:.0f}s)")
                return True
        except Exception:
            pass
        return False
    return True


def mark_session_loaded(session_id: str, cwd: Path) -> None:
    """Create marker file to prevent re-loading context this session."""
    slug = cwd_to_slug(cwd)
    marker = _marker_path(session_id, slug)
    marker.touch()


def cleanup_stale_markers() -> None:
    """Delete marker files older than 4 hours (crash cleanup)."""
    cutoff = time.time() - 4 * 3600
    for f in (Path.home() / ".claude").glob(".recall_*"):
        try:
            if f.stat().st_mtime < cutoff:
                safe_unlink(f)
        except Exception:
            pass


def clear_session_marker(session_id: str, cwd: Path) -> None:
    """Remove marker when session ends normally."""
    slug = cwd_to_slug(cwd)
    marker = _marker_path(session_id, slug)
    if marker.exists():
        safe_unlink(marker)


# ── Last session summary ──────────────────────────────────────────────────────

def _session_is_useful_for_summary(note_text: str) -> bool:
    """Check if a session note has enough signal to inject as prior context.

    Uses a lenient check: has turns >= 3 OR (has LLM summary AND files).
    Does NOT strip section headers — that would destroy the content.
    """
    has_turns = re.search(r"^turns:\s*(\d+)", note_text, re.MULTILINE)
    turns = int(has_turns.group(1)) if has_turns else 0
    has_llm = re.search(r"^llm_summary:\s*true", note_text, re.MULTILINE) is not None
    has_files = re.search(r"## Files? Touched", note_text) is not None

    # Quick length check — real sessions have meaningful content
    # Strip only frontmatter + the ## Conversation/## Stats boilerplate headers
    body = re.sub(r"^---.*?---\s*", "", note_text, flags=re.DOTALL).strip()
    body = re.sub(r"^#.*$", "", body, flags=re.MULTILINE).strip()
    body = re.sub(r"## (?:Conversation|Stats)\b.*$", "", body, flags=re.MULTILINE).strip()
    is_meaningful = len(body) > 50

    return (turns >= 3 or (has_llm and has_files)) and is_meaningful


def get_last_session_summary(project_dir: Path) -> str | None:
    """Get compact summary of the most recent USEFUL session.

    Used by load_context.py to inject continuity into the context.
    Skips low-quality sessions (1-turn, no LLM, no files).
    Returns a short string or None if no useful sessions exist.
    """
    sessions_dir = project_dir / "sessions"
    if not sessions_dir.exists():
        return None

    try:
        notes = sorted(sessions_dir.glob("*.md"), reverse=True)
        useful_note = None
        for latest in notes:
            text = latest.read_text(encoding="utf-8")
            if not _session_is_useful_for_summary(text):
                continue
            useful_note = (latest, text)
            break  # found the most recent useful session

        if useful_note is None:
            return None

        latest, text = useful_note

        # Extract key sections from the session note
        parts = []

        # Date from filename
        stem = latest.stem  # e.g. "2026-04-12_06-39"
        parts.append(f"**Last session ({stem})**")

        # Summary section
        summary_match = re.search(
            r"## Summary\s*\n\s*(.+?)(?:\n##|\Z)",
            text, re.DOTALL
        )
        if summary_match:
            summary = summary_match.group(1).strip()
            # Skip if it's prompt template leakage
            if summary and "sentence description" not in summary.lower():
                parts.append(summary[:300])

        # Next steps
        steps_match = re.search(
            r"## Next steps\s*\n(.+?)(?:\n##|\Z)",
            text, re.DOTALL
        )
        if steps_match:
            steps_raw = steps_match.group(1).strip()
            steps = [
                l.strip().lstrip("- [] ").strip()
                for l in steps_raw.splitlines()
                if l.strip().startswith("- [")
                and "edit in Obsidian" not in l
            ]
            if steps:
                parts.append("Next: " + "; ".join(steps[:3]))

        return "\n".join(parts) if len(parts) > 1 else None

    except Exception as e:
        _debug(f"get_last_session_summary error: {e}")
        return None


# ── Session note building ─────────────────────────────────────────────────────

def build_session_note(
    slug: str,
    cwd: Path,
    session_id: str,
    facts: dict,
    llm_summary: dict | None = None,
    git_changes: dict | None = None,
) -> str:
    """Build a session note for saving to Obsidian.

    Includes the full conversation log (all user prompts + assistant summaries),
    file operations, LLM-generated summary, and next steps.
    """
    ts = datetime.now()

    # --- Conversation log ---
    all_prompts = facts.get("all_prompts", [])
    all_responses = facts.get("all_responses", [])
    file_ops = facts.get("file_ops_summary", [])

    conversation_lines = []
    for i, prompt in enumerate(all_prompts):
        prompt_display = prompt[:200]
        conversation_lines.append(f"{i+1}. **User:** {prompt_display}")
        if i < len(all_responses) and all_responses[i]:
            resp_display = all_responses[i][:150]
            conversation_lines.append(f"   → *{resp_display}*")

    conversation_section = ""
    if conversation_lines:
        conversation_section = (
            "\n## Conversation\n\n"
            + "\n".join(conversation_lines)
            + "\n"
        )

    # --- Summary ---
    if llm_summary and llm_summary.get("summary"):
        summary = llm_summary["summary"]
    elif all_prompts:
        # Fallback: join first 3 prompts as summary
        summary = "Topics: " + " → ".join(p[:80] for p in all_prompts[:3])
    else:
        summary = f"Started with: {facts.get('first_prompt', '?')}"

    # --- Git activity ---
    git_section = ""
    if git_changes:
        branch = git_changes.get("branch", "")
        commits = git_changes.get("recent_commits", [])
        changed = git_changes.get("changed_files", [])

        if branch or commits or changed:
            git_lines = []
            if branch:
                git_lines.append(f"- Branch: `{branch}`")
            if commits:
                git_lines.append(f"- Recent: {' | '.join(commits[:3])}")
            if changed:
                git_lines.append(f"- Changed: {', '.join(changed[:10])}")
            if git_lines:
                git_section = "\n## Git Activity\n\n" + "\n".join(git_lines) + "\n"

    # --- Files ---
    files_section = ""
    if llm_summary and llm_summary.get("files_and_roles"):
        items = "\n".join(
            f"- `{f}` — {role}"
            for f, role in llm_summary["files_and_roles"].items()
        )
        files_section = f"\n## Files Touched\n\n{items}\n"
    elif file_ops:
        items = "\n".join(f"- `{op}`" for op in file_ops[:15])
        files_section = f"\n## Files Touched\n\n{items}\n"
    elif facts.get("files"):
        files = _normalize_file_paths(facts["files"], cwd)
        items = "\n".join(f"- `{f}`" for f in files[:15])
        files_section = f"\n## Files Touched\n\n{items}\n"

    # --- Next steps ---
    if llm_summary and llm_summary.get("next_steps"):
        raw_steps = llm_summary["next_steps"]
        if isinstance(raw_steps, str):
            raw_steps = [s.strip() for s in raw_steps.split(",") if s.strip()]
        steps = "\n".join(f"- [ ] {s}" for s in raw_steps[:5])
        next_section = f"\n## Next Steps\n\n{steps}\n"
    else:
        next_section = "\n## Next Steps\n\n- [ ] _(continue from where you left off)_\n"

    # --- Keywords ---
    keywords_section = ""
    if llm_summary and llm_summary.get("keywords"):
        kw = ", ".join(f"`{k}`" for k in llm_summary["keywords"])
        keywords_section = f"\n**Keywords:** {kw}\n"

    return (
        f"---\n"
        f"date: {ts.strftime('%Y-%m-%d')}\n"
        f"time: {ts.strftime('%H:%M')}\n"
        f"project: {slug}\n"
        f"directory: {cwd}\n"
        f"session_id: {session_id}\n"
        f"turns: {facts.get('turns', 0)}\n"
        f"llm_summary: {'true' if llm_summary else 'false'}\n"
        f"tags: [claude-recall, session]\n"
        f"---\n\n"
        f"# Session {ts.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Directory\n\n`{cwd}`\n\n"
        f"## Stats\n\n{facts.get('turns', 0)} user turns · "
        f"{facts.get('total_messages', 0)} total messages · "
        f"{facts.get('tool_count', 0)} tool calls\n"
        f"{conversation_section}"
        f"\n## Summary\n\n{summary}\n"
        f"{git_section}"
        f"{files_section}"
        f"{next_section}"
        f"{keywords_section}"
    )


def _normalize_file_paths(files: list[str], cwd: Path) -> list[str]:
    """Normalize file paths to relative, remove duplicates."""
    cwd_str = str(cwd)
    seen = set()
    result = []
    for f in files:
        # Convert absolute to relative
        if f.startswith(cwd_str):
            f = f[len(cwd_str):].lstrip("/")
        # Remove leading /Users/... paths that don't match cwd
        elif f.startswith("/"):
            parts = f.rsplit("/", 1)
            f = parts[-1] if len(parts) > 1 else f
        # Deduplicate
        if f not in seen and f:
            seen.add(f)
            result.append(f)
    return result
