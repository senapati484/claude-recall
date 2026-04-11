#!/usr/bin/env python3
"""
load_context.py — claude-recall UserPromptSubmit hook.

Fires before every user prompt. Reads project context from the Obsidian vault
and prints it to stdout — Claude Code prepends this to Claude's system context.

Storage layout in Obsidian:
  <vault>/claude-recall/projects/<slug>/context.md      ← human-edited
  <vault>/claude-recall/projects/<slug>/sessions/*.md   ← auto-written by save_context.py

Never exits non-zero — a failed hook would block Claude from starting.
"""

import sys
import traceback
import os
from datetime import datetime
from pathlib import Path

DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] LOAD: {msg}\n")
    except Exception:
        pass

def load_context() -> None:
    debug_log(f"=== LOAD SESSION STARTED ===")
    debug_log(f"CWD: {os.getcwd()}")
    debug_log(f"Args: {sys.argv}")
    debug_log(f"stdin isatty: {sys.stdin.isatty()}")
    debug_log(f"stdin closed: {sys.stdin.closed}")
    
    # Check for env vars Claude might set
    debug_log(f"CLAUDE_SESSION_ID: {os.environ.get('CLAUDE_SESSION_ID', 'NOT SET')}")
    debug_log(f"CLAUDE_CWD: {os.environ.get('CLAUDE_CWD', 'NOT SET')}")
    
    try:
        from utils import (
            load_config, get_project_dir, read_hook_input, get_cwd,
            cwd_to_slug, truncate_to_tokens, session_marker, cleanup_stale_markers,
        )
        debug_log("Utils imported successfully")
        
        hook_input = read_hook_input()
        debug_log(f"Raw hook_input: {hook_input}")
        session_id = hook_input.get("session_id", "unknown")
        cwd        = get_cwd(hook_input)
        cfg        = load_config()
        
        debug_log(f"session_id={session_id}, cwd={cwd}")
        debug_log(f"Config vault_path: {cfg.get('vault_path')}")

        cleanup_stale_markers()

        # Session-start deduplication
        marker = session_marker(session_id)
        debug_log(f"Marker path: {marker}, exists: {marker.exists()}")
        if not cfg.get("load_on_every_prompt", False) and marker.exists():
            debug_log("Skipping - marker exists (session already loaded)")
            return
        marker.touch()

        # Resolve project in vault
        slug        = cwd_to_slug(cwd)
        project_dir = get_project_dir(cfg, slug)
        debug_log(f"Project dir: {project_dir}")
        parts: list[str] = []

        # Persistent context.md
        context_md = project_dir / "context.md"
        debug_log(f"Context md exists: {context_md.exists()}")
        if context_md.exists():
            text = context_md.read_text(encoding="utf-8").strip()
            if text:
                text = truncate_to_tokens(text, cfg.get("max_context_tokens", 2000) // 2)
                parts.append(f"## Project context\n\n{text}")

        # Recent session notes
        sessions_dir = project_dir / "sessions"
        n = cfg.get("include_recent_sessions", 2)
        debug_log(f"Sessions dir: {sessions_dir}, n={n}")
        if sessions_dir.exists() and n > 0:
            try:
                recent = sorted(sessions_dir.glob("*.md"), reverse=True)[:n]
                debug_log(f"Found {len(recent)} sessions")
                for note in recent:
                    t = note.read_text(encoding="utf-8").strip()
                    if t:
                        parts.append(f"## Previous session — {note.stem}\n\n{t}")
            except Exception as exc:
                debug_log(f"Session read error: {exc}")
                print(f"[claude-recall] Could not read sessions: {exc}", file=sys.stderr)

        if not parts:
            debug_log("No context found")
            print(
                f"[claude-recall] No Obsidian context found for '{slug}'.\n"
                f"  A note will be created after this session at:\n"
                f"  {project_dir / 'context.md'}\n"
                f"  Open it in Obsidian and add your project details.",
                file=sys.stderr,
            )
            return

        body = "\n\n---\n\n".join(parts)
        body = truncate_to_tokens(body, cfg.get("max_context_tokens", 2000))

        debug_log(f"Returning {len(body)} chars of context")
        print(
            "<!-- claude-recall: context loaded from Obsidian -->\n"
            f"Project: `{slug}`  |  Directory: `{cwd}`  "
            f"|  Loaded: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            + body
        )
        
    except Exception as exc:
        debug_log(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] load error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    load_context()
