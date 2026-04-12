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
    DEBUG_LOG,
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
                marker.unlink()
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
                f.unlink()
        except Exception:
            pass


def clear_session_marker(session_id: str, cwd: Path) -> None:
    """Remove marker when session ends normally."""
    slug = cwd_to_slug(cwd)
    marker = _marker_path(session_id, slug)
    if marker.exists():
        marker.unlink(missing_ok=True)


# ── Last session summary ──────────────────────────────────────────────────────

def get_last_session_summary(project_dir: Path) -> str | None:
    """Get compact summary of the most recent session.

    Used by load_context.py to inject continuity into the context.
    Returns a short string or None if no sessions exist.
    """
    sessions_dir = project_dir / "sessions"
    if not sessions_dir.exists():
        return None

    try:
        notes = sorted(sessions_dir.glob("*.md"), reverse=True)
        if not notes:
            return None

        latest = notes[0]
        text = latest.read_text(encoding="utf-8")

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
) -> str:
    """Build a session note for saving to Obsidian.

    Args:
        slug: project slug
        cwd: project directory
        session_id: Claude session ID
        facts: dict from extract_facts() with first_prompt, turns, etc.
        llm_summary: optional LLM-generated summary dict
    """
    ts = datetime.now()

    # --- Summary ---
    if llm_summary and llm_summary.get("summary"):
        summary = llm_summary["summary"]
    else:
        summary = f"Started with: {facts.get('first_prompt', '?')}"

    # --- Files ---
    files_section = ""
    if llm_summary and llm_summary.get("files_and_roles"):
        items = "\n".join(
            f"- `{f}` — {role}"
            for f, role in llm_summary["files_and_roles"].items()
        )
        files_section = f"\n## Files touched\n\n{items}\n"
    elif facts.get("files"):
        # Normalize to relative paths
        files = _normalize_file_paths(facts["files"], cwd)
        items = "\n".join(f"- `{f}`" for f in files[:15])
        files_section = f"\n## Files touched\n\n{items}\n"

    # --- Next steps ---
    if llm_summary and llm_summary.get("next_steps"):
        steps = "\n".join(f"- [ ] {s}" for s in llm_summary["next_steps"])
        next_section = f"\n## Next steps\n\n{steps}\n"
    else:
        next_section = "\n## Next steps\n\n- [ ] _(continue from where you left off)_\n"

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
        f"## Started with\n\n> {facts.get('first_prompt', '?')}\n\n"
        f"## Stats\n\n{facts.get('turns', 0)} user turns · "
        f"{facts.get('total_messages', 0)} total messages\n"
        f"\n## Summary\n\n{summary}\n"
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
