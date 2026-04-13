#!/usr/bin/env python3
"""
load_context.py — claude-recall UserPromptSubmit hook.

Fires before every user prompt. On the FIRST prompt of a session:
1. Loads project context from Obsidian vault
2. Injects last session summary for continuity
3. Prints to stdout → Claude reads as system context

Subsequent prompts in the same session are skipped (marker file).
Never exits non-zero — a failed hook would block Claude from starting.
"""

from __future__ import annotations

import json
import sys
import traceback
import os
from datetime import datetime
from pathlib import Path

# EARLY DIAGNOSTIC — log immediately to confirm hook is being called
try:
    _log = Path.home() / ".claude" / "claude-recall-debug.log"
    with open(_log, "a") as _f:
        _f.write(f"[{datetime.now().isoformat()}] LOAD: >>> SCRIPT STARTED (pid={os.getpid()})\n")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    load_config, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, truncate_to_tokens, DEBUG_LOG,
)
from session_manager import (
    should_load_context, mark_session_loaded, cleanup_stale_markers,
    get_last_session_summary,
)
from context_builder import (
    build_compact_context, is_context_empty_or_missing,
)


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] LOAD: {msg}\n")
    except Exception:
        pass


def load_context() -> None:
    _debug("=== LOAD SESSION STARTED ===")
    _debug(f"CWD: {os.getcwd()}")

    try:
        hook_input = read_hook_input()
        session_id = hook_input.get("session_id", "unknown")
        cwd        = get_cwd(hook_input)
        cfg        = load_config()

        _debug(f"session_id={session_id}, cwd={cwd}")

        cleanup_stale_markers()

        # Session dedup — only load on first prompt
        if not cfg.get("load_on_every_prompt", False):
            if not should_load_context(session_id, cwd):
                _debug("Skipping - marker exists (session already loaded)")
                return

        mark_session_loaded(session_id, cwd)

        # Resolve project in vault
        slug        = cwd_to_slug(cwd)
        project_dir = get_project_dir(cfg, slug)
        context_md  = project_dir / "context.md"

        _debug(f"slug={slug}, project_dir={project_dir}, context exists={context_md.exists()}")

        # Auto-generate context.md if missing or empty
        if is_context_empty_or_missing(project_dir):
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                print(f"[claude-recall] ⚡ Generating context for '{slug}'...", file=sys.stderr)
                content = build_compact_context(cwd, slug)
                context_md.write_text(content, encoding="utf-8")
                _debug(f"Auto-generated context.md ({len(content)} chars)")
                print(f"[claude-recall] ✓ Context generated ({len(content)} chars)", file=sys.stderr)
            except Exception as exc:
                _debug(f"Auto-generate failed: {exc}")
                print(f"[claude-recall] ✗ Auto-generate error: {exc}", file=sys.stderr)

        # Build output for Claude
        parts: list[str] = []
        max_ctx = cfg.get("max_context_tokens", 2000)

        # 1. Load context.md (60% of token budget)
        if context_md.exists():
            text = context_md.read_text(encoding="utf-8").strip()
            if text:
                text = truncate_to_tokens(text, int(max_ctx * 0.6))
                parts.append(f"## Project context\n\n{text}")

        # 2. Last session summary for continuity (20% of budget)
        last_summary = get_last_session_summary(project_dir)
        if last_summary:
            last_summary = truncate_to_tokens(last_summary, int(max_ctx * 0.2))
            parts.append(f"## Previous session\n\n{last_summary}")

        # 3. file-index.json (20% of budget, if exists)
        file_index_path = project_dir / "file-index.json"
        if file_index_path.exists():
            try:
                raw_index = json.loads(file_index_path.read_text(encoding="utf-8"))
                raw_index.pop("_cache_mtimes", None)
                if raw_index:
                    lines = []
                    for rel_path, info in list(raw_index.items())[:10]:
                        if isinstance(info, dict) and info.get("purpose"):
                            lines.append(f"- `{rel_path}` — {info['purpose']}")
                    if lines:
                        parts.append(
                            "## Key files\n\n" + "\n".join(lines)
                        )
            except Exception:
                pass

        if not parts:
            _debug("No context found")
            return

        # Extract active task from most recent session's Next Steps
        active_task = ""
        try:
            sessions_dir = project_dir / "sessions"
            if sessions_dir.exists():
                notes = sorted(sessions_dir.glob("*.md"), reverse=True)
                if notes:
                    latest = notes[0].read_text(encoding="utf-8")
                    task_match = re.search(r"## Next Steps\s*\n- \[ \] (.+)", latest)
                    if task_match:
                        active_task = task_match.group(1).strip()
                        if len(active_task) > 100:
                            active_task = active_task[:100] + "..."
        except Exception:
            pass

        # Label each section with semantic priority (case-insensitive prefix match)
        labeled_parts = []
        for part in parts:
            plower = part.lower()
            if plower.startswith("## project context"):
                labeled_parts.append("[MUST KNOW]\n" + part)
            elif plower.startswith("## previous session"):
                labeled_parts.append("[RECENT WORK]\n" + part)
            elif plower.startswith("## key files"):
                labeled_parts.append("[KEY FILES]\n" + part)
            else:
                labeled_parts.append(part)

        # Prepend active task if found
        if active_task:
            labeled_parts.insert(0, f"LAST SESSION ENDED WITH: {active_task}")

        body = "\n\n---\n\n".join(labeled_parts)
        body = truncate_to_tokens(body, max_ctx)

        # Header
        sessions_dir = project_dir / "sessions"
        session_count = len([
            f for f in sessions_dir.glob("*.md")
            if not f.name.startswith(".")
        ]) if sessions_dir.exists() else 0
        header_parts = [f"Project: `{slug}`", f"Dir: `{cwd}`"]
        if session_count > 0:
            header_parts.append(f"Sessions: {session_count}")
        header_parts.append(f"Loaded: {datetime.now().strftime('%H:%M')}")

        _debug(f"Returning {len(body)} chars of context")

        # Print to stdout — Claude reads this as system context
        print(
            f"<!-- claude-recall: project memory loaded -->\n"
            f"{' | '.join(header_parts)}\n\n"
            + body
            + "\n\n> **claude-recall active** — context auto-saves when you stop.\n"
        )


    except Exception as exc:
        _debug(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] ✗ load error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    load_context()