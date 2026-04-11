#!/usr/bin/env python3
"""
load_context.py — claude-recall UserPromptSubmit hook.

Fires before every user prompt. Reads project context from the Obsidian vault
and prints it to stdout — Claude Code prepends this to Claude's system context.

KEY BEHAVIOR: If no context.md exists (first session in this project), this
script AUTO-GENERATES it by scanning the project directory — detecting stack,
file tree, config, git info. Claude gets full context from the very first message.

Storage layout in Obsidian:
  <vault>/claude-recall/projects/<slug>/context.md      ← auto-generated + user can edit
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
    debug_log("=== LOAD SESSION STARTED ===")
    debug_log(f"CWD: {os.getcwd()}")
    
    try:
        from utils import (
            load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
            cwd_to_slug, truncate_to_tokens, session_marker, cleanup_stale_markers,
            is_scaffold_only, auto_generate_context_md,
        )
        debug_log("Utils imported successfully")
        
        hook_input = read_hook_input()
        session_id = hook_input.get("session_id", "unknown")
        cwd        = get_cwd(hook_input)
        cfg        = load_config()
        
        debug_log(f"session_id={session_id}, cwd={cwd}")

        cleanup_stale_markers()

        # Session-start deduplication — only load context on first prompt
        marker = session_marker(session_id)
        if not cfg.get("load_on_every_prompt", False) and marker.exists():
            debug_log("Skipping - marker exists (session already loaded)")
            return
        marker.touch()

        # Resolve project in vault
        slug        = cwd_to_slug(cwd)
        project_dir = get_project_dir(cfg, slug)
        context_md  = project_dir / "context.md"
        
        debug_log(f"slug={slug}, project_dir={project_dir}, context exists={context_md.exists()}")

        # ──────────────────────────────────────────────────────────────────
        # AUTO-GENERATE context.md if it doesn't exist or is empty scaffold
        # This is the KEY feature — Claude has full context from first msg
        # ──────────────────────────────────────────────────────────────────
        needs_generation = False
        if not context_md.exists():
            needs_generation = True
            debug_log("context.md does not exist — will auto-generate")
        elif is_scaffold_only(context_md.read_text(encoding="utf-8")):
            needs_generation = True
            debug_log("context.md is empty scaffold — will auto-generate")
        
        if needs_generation:
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                content = auto_generate_context_md(cwd, slug)
                context_md.write_text(content, encoding="utf-8")
                debug_log(f"Auto-generated context.md ({len(content)} chars)")
                print(
                    f"[claude-recall] Auto-generated context for '{slug}' from project files.",
                    file=sys.stderr,
                )
            except Exception as exc:
                debug_log(f"Auto-generate failed: {exc}")
                print(f"[claude-recall] Auto-generate error: {exc}", file=sys.stderr)

        # ──────────────────────────────────────────────────────
        # Build context output for Claude
        # Token budget: 2000 total
        # - context.md: 800 tokens
        # - each session: 400 tokens (2 sessions = 800 tokens)
        # - header/footer: ~200 tokens
        # - gaps/separators: ~200 tokens
        # ──────────────────────────────────────────────────────
        parts: list[str] = []
        max_ctx = cfg.get("max_context_tokens", 2000)

        # Load context.md (800 tokens)
        if context_md.exists():
            text = context_md.read_text(encoding="utf-8").strip()
            if text and not is_scaffold_only(text):
                text = truncate_to_tokens(text, int(max_ctx * 0.4))
                parts.append(f"## Project context\n\n{text}")

        # Load recent session notes (400 tokens each)
        sessions_dir = project_dir / "sessions"
        n = cfg.get("include_recent_sessions", 2)
        session_count = 0
        session_summaries = []  # For header: tool counts and changes summary
        if sessions_dir.exists() and n > 0:
            try:
                all_sessions = sorted(sessions_dir.glob("*.md"), reverse=True)
                session_count = len(all_sessions)
                for note in all_sessions[:n]:
                    t = note.read_text(encoding="utf-8").strip()
                    if t:
                        # Truncate individual session notes
                        t = truncate_to_tokens(t, int(max_ctx * 0.2))
                        parts.append(f"## Previous session — {note.stem}\n\n{t}")
            except Exception as exc:
                debug_log(f"Session read error: {exc}")

        if not parts:
            debug_log("No context found even after auto-generation")
            return

        body = "\n\n---\n\n".join(parts)
        body = truncate_to_tokens(body, max_ctx)

        # Build header
        header_parts = [f"Project: `{slug}`", f"Dir: `{cwd}`"]
        if session_count > 0:
            header_parts.append(f"Sessions: {session_count}")
        header_parts.append(f"Loaded: {datetime.now().strftime('%H:%M')}")

        debug_log(f"Returning {len(body)} chars of context")
        
        print(
            "<!-- claude-recall: context loaded from Obsidian -->\n"
            f"{' | '.join(header_parts)}\n\n"
            + body
            + "\n\n> **claude-recall**: `/recall update` (refresh) · `/recall status` · `/recall reset`\n"
        )
        
    except Exception as exc:
        debug_log(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] load error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    load_context()
