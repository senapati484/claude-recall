#!/usr/bin/env python3
"""
recall_update.py — /recall command for claude-recall.

Usage:
    python3 ~/.claude/skills/claude-recall/scripts/recall_update.py <action> [cwd]

Actions:
    update   Regenerate context.md from filesystem + README + LLM
    status   Show current context.md content
    reset    Delete context.md and regenerate from scratch
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, detect_project_stack, llm_available,
)
from context_builder import build_compact_context


def action_update(cwd: Path, cfg: dict) -> None:
    """Scan project and regenerate context.md."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    context_md = project_dir / "context.md"

    print(f"[claude-recall] Updating context for: {slug}")
    print(f"  Directory: {cwd}")

    fs = detect_project_stack(cwd)
    print(f"  Detected stack: {' · '.join(fs.get('stack', [])) or 'none'}")
    print(f"  LLM available: {llm_available()}")

    content = build_compact_context(cwd, slug)
    context_md.write_text(content, encoding="utf-8")
    print(f"  ✓ Updated: {context_md}")
    print(f"  Size: {len(content)} chars (~{len(content) // 4} tokens)")


def action_status(cwd: Path, cfg: dict) -> None:
    """Print current context.md content."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"

    print(f"## claude-recall: {slug}")
    print(f"Directory: `{cwd}`")
    print(f"Vault:     {get_vault_root(cfg)}")

    sessions_dir = project_dir / "sessions"
    session_count = len(list(sessions_dir.glob("*.md"))) if sessions_dir.exists() else 0
    print(f"Sessions:  {session_count}")

    if context_md.exists():
        print()
        print(context_md.read_text())
    else:
        print("\nNo context.md yet. Run `/recall update` to generate it.")


def action_reset(cwd: Path, cfg: dict) -> None:
    """Delete existing context.md and regenerate from scratch."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"

    if context_md.exists():
        backup = context_md.with_suffix(".md.bak")
        context_md.rename(backup)
        print(f"[claude-recall] Reset: backed up old → {backup}")
    else:
        print(f"[claude-recall] Reset: no existing context.md")

    action_update(cwd, cfg)


def main() -> None:
    action = "update"
    cwd = Path(os.getcwd())

    args = sys.argv[1:]
    if args:
        action = args[0].strip("-/")
    if len(args) > 1:
        cwd = Path(args[1])

    cfg = load_config()

    if action in ("update", "u"):
        action_update(cwd, cfg)
    elif action in ("status", "s"):
        action_status(cwd, cfg)
    elif action in ("reset", "r"):
        action_reset(cwd, cfg)
    else:
        print(f"Unknown action: {action}")
        print("Usage: recall_update.py [update|status|reset] [cwd]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[claude-recall] Error: {e}", file=sys.stderr)
        sys.exit(1)
