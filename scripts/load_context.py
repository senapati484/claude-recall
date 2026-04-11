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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, truncate_to_tokens, session_marker, cleanup_stale_markers,
)


def load_context() -> None:
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id", "unknown")
    cwd        = get_cwd(hook_input)
    cfg        = load_config()

    cleanup_stale_markers()

    # ── Session-start deduplication ───────────────────────────────────────────
    # By default context is injected once per session (not every prompt).
    # Set load_on_every_prompt: true in ~/.claude/claude-recall.json to override.
    marker = session_marker(session_id)
    if not cfg.get("load_on_every_prompt", False) and marker.exists():
        return
    marker.touch()

    # ── Resolve project in vault ──────────────────────────────────────────────
    slug        = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    parts: list[str] = []

    # ── 1. Persistent context.md (human-edited in Obsidian) ───────────────────
    context_md = project_dir / "context.md"
    if context_md.exists():
        text = context_md.read_text(encoding="utf-8").strip()
        if text:
            # Guard against a single huge context.md blowing the token budget
            # Edge case: large context.md — truncation notice appears within context section
            text = truncate_to_tokens(text, cfg.get("max_context_tokens", 2000) // 2)
            parts.append(f"## Project context\n\n{text}")

    # ── 2. Recent session notes (auto-written by save_context.py) ────────────
    sessions_dir = project_dir / "sessions"
    n = cfg.get("include_recent_sessions", 2)
    if sessions_dir.exists() and n > 0:
        try:
            recent = sorted(sessions_dir.glob("*.md"), reverse=True)[:n]
            for note in recent:
                t = note.read_text(encoding="utf-8").strip()
                if t:
                    parts.append(f"## Previous session — {note.stem}\n\n{t}")
        except Exception as exc:
            print(f"[claude-recall] Could not read sessions: {exc}", file=sys.stderr)

    # ── No context found yet ──────────────────────────────────────────────────
    if not parts:
        print(
            f"[claude-recall] No Obsidian context found for '{slug}'.\n"
            f"  A note will be created after this session at:\n"
            f"  {project_dir / 'context.md'}\n"
            f"  Open it in Obsidian and add your project details.",
            file=sys.stderr,
        )
        return

    # ── Assemble, truncate, print to stdout ───────────────────────────────────
    body = "\n\n---\n\n".join(parts)
    body = truncate_to_tokens(body, cfg.get("max_context_tokens", 2000))

    print(
        "<!-- claude-recall: context loaded from Obsidian -->\n"
        f"Project: `{slug}`  |  Directory: `{cwd}`  "
        f"|  Loaded: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        + body
    )


if __name__ == "__main__":
    try:
        load_context()
    except Exception as exc:
        print(f"[claude-recall] load error: {exc}", file=sys.stderr)
        sys.exit(0)
