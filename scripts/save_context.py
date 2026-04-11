#!/usr/bin/env python3
"""
save_context.py — claude-recall Stop hook.

Fires when the Claude Code session ends. Reads the session transcript,
extracts key facts, and writes a structured Markdown note to Obsidian:

  <vault>/claude-recall/projects/<slug>/sessions/YYYY-MM-DD_HH-MM.md

On first session for a project it also scaffolds:
  <vault>/claude-recall/projects/<slug>/context.md  ← user edits this in Obsidian
  <vault>/claude-recall/_index.md                   ← running log of all projects

Never exits non-zero.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, now_str, session_marker, cleanup_stale_markers,
)


# ── Transcript ────────────────────────────────────────────────────────────────

def parse_transcript(path: str) -> list[dict]:
    messages = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj.get("content"), str) and obj.get("role"):
                        messages.append({"role": obj["role"], "content": obj["content"]})
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        print(f"[claude-recall] Transcript read error: {exc}", file=sys.stderr)
    return messages


def extract_facts(messages: list[dict]) -> dict:
    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    all_text  = " ".join(m["content"] for m in messages if isinstance(m.get("content"), str))

    # Source files mentioned in the conversation
    file_re = re.compile(
        r'[\w./\-]+\.(?:tsx?|jsx?|py|dart|go|rs|rb|java|kt|swift|'
        r'md|json|yaml|yml|toml|sh|env|sql|html|css|scss)\b'
    )
    files = list(dict.fromkeys(m.group() for m in file_re.finditer(all_text)))[:15]

    return {
        "first_prompt":    (user_msgs[0][:300].replace("\n", " ") if user_msgs else "(no messages)"),
        "turns":           len(user_msgs),
        "total_messages":  len(messages),
        "files":           files,
    }


# ── Note builders ─────────────────────────────────────────────────────────────

def build_session_note(slug: str, cwd: Path, session_id: str, facts: dict) -> str:
    ts = datetime.now()
    files_section = ""
    if facts["files"]:
        items = "\n".join(f"- `{f}`" for f in facts["files"])
        files_section = f"\n## Files mentioned\n\n{items}\n"

    return (
        f"---\n"
        f"date: {ts.strftime('%Y-%m-%d')}\n"
        f"time: {ts.strftime('%H:%M')}\n"
        f"project: {slug}\n"
        f"directory: {cwd}\n"
        f"session_id: {session_id}\n"
        f"turns: {facts['turns']}\n"
        f"tags: [claude-recall, session]\n"
        f"---\n\n"
        f"# Session {ts.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Directory\n\n`{cwd}`\n\n"
        f"## Started with\n\n> {facts['first_prompt']}\n\n"
        f"## Stats\n\n"
        f"{facts['turns']} user turns · {facts['total_messages']} total messages\n"
        f"{files_section}\n"
        f"## Next steps\n\n"
        f"- [ ] _(edit in Obsidian or ask Claude to summarise)_\n"
    )


CONTEXT_SCAFFOLD = """\
---
project: {slug}
directory: {cwd}
created: {date}
tags: [claude-recall, context]
---

# {slug}

## What this is

<!-- Describe the project in 1–3 sentences -->

## Stack

<!-- e.g. Flutter · Express.js · MongoDB Atlas · Railway -->

## Current state

<!-- What's done, in progress, blocked -->

## Architecture decisions

<!-- Key decisions and the reasoning behind them -->

## Gotchas

<!-- Tricky things to remember — env vars, ordering constraints, quirks -->

## Environment

<!-- Deployment targets, service names, env var keys (never values) -->
"""


def ensure_context_scaffold(project_dir: Path, slug: str, cwd: Path) -> None:
    """Write context.md scaffold only on first encounter of this project."""
    context_md = project_dir / "context.md"
    if context_md.exists():
        return
    context_md.write_text(
        CONTEXT_SCAFFOLD.format(slug=slug, cwd=cwd, date=datetime.now().strftime("%Y-%m-%d")),
        encoding="utf-8",
    )
    print(
        f"[claude-recall] Created Obsidian note: {context_md}\n"
        f"  Open it in Obsidian and add your project details.",
        file=sys.stderr,
    )


def update_index(vault_root: Path, slug: str, cwd: Path, turns: int) -> None:
    """Append an entry to _index.md in the vault root folder."""
    index = vault_root / "_index.md"
    if not index.exists():
        index.write_text(
            "---\ntags: [claude-recall]\n---\n\n# claude-recall — project index\n\n",
            encoding="utf-8",
        )
    line = f"- [{slug}](projects/{slug}/context) · `{cwd}` · {turns} turns · {now_str('%Y-%m-%d %H:%M')}\n"
    with open(index, "a", encoding="utf-8") as f:
        f.write(line)


# ── Main ──────────────────────────────────────────────────────────────────────

def save_session() -> None:
    hook_input      = read_hook_input()
    session_id      = hook_input.get("session_id", now_str())
    transcript_path = hook_input.get("transcript_path", "")
    cwd             = get_cwd(hook_input)
    cfg             = load_config()

    cleanup_stale_markers()

    if not cfg.get("save_sessions", True):
        return

    messages = parse_transcript(transcript_path) if transcript_path else []
    if not messages:
        return   # Nothing happened this session — skip

    slug        = cwd_to_slug(cwd)
    vault_root  = get_vault_root(cfg)
    project_dir = get_project_dir(cfg, slug)

    # Edge case: read-only vault — wrap in try/except to avoid hook failure
    try:
        (project_dir / "sessions").mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(
            f"[claude-recall] Cannot write to vault — check permissions: {project_dir}",
            file=sys.stderr,
        )
        return

    # Scaffold context.md for new projects
    ensure_context_scaffold(project_dir, slug, cwd)

    # Write session note
    facts     = extract_facts(messages)
    note      = build_session_note(slug, cwd, session_id, facts)
    note_path = project_dir / "sessions" / f"{now_str()}.md"

    try:
        note_path.write_text(note, encoding="utf-8")
    except PermissionError:
        # Edge case: read-only sessions directory
        print(
            f"[claude-recall] Cannot write session note — check permissions: {note_path}",
            file=sys.stderr,
        )
        return

    # Update vault index
    update_index(vault_root, slug, cwd, facts["turns"])

    # Clean up session marker
    marker = session_marker(session_id)
    if marker.exists():
        marker.unlink(missing_ok=True)

    print(f"[claude-recall] Saved to Obsidian → {note_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        save_session()
    except Exception as exc:
        print(f"[claude-recall] save error: {exc}", file=sys.stderr)
        sys.exit(0)
