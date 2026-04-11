#!/usr/bin/env python3
"""
recall_update.py — On-demand context refresh for claude-recall.

Invoked by Claude when the user types /recall (or variants).
Scans the current project directory to detect stack, structure, and config,
then updates context.md with the findings.

Usage (Claude runs this):
    python3 ~/.claude/skills/claude-recall/scripts/recall_update.py [action] [cwd]

Actions:
    update  — Scan filesystem and update context.md (default)
    status  — Print what claude-recall knows about this project
    reset   — Regenerate context.md from scratch (preserves sessions)
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, detect_project_stack, is_scaffold_only,
    merge_auto_section, parse_index_entries,
)


def format_stack_info(fs_stack: dict) -> str:
    """Format detected stack info for display."""
    lines = []
    
    if fs_stack.get("name"):
        lines.append(f"**Project name**: {fs_stack['name']}")
    
    if fs_stack.get("type") and fs_stack["type"] != "unknown":
        lines.append(f"**Type**: {fs_stack['type']}")
    
    if fs_stack.get("stack"):
        lines.append(f"**Stack**: {' · '.join(fs_stack['stack'])}")
    
    if fs_stack.get("structure"):
        dirs = ", ".join(f"`{d}/`" for d in fs_stack["structure"][:10])
        lines.append(f"**Directories**: {dirs}")
    
    if fs_stack.get("config_files"):
        configs = ", ".join(f"`{f}`" for f in fs_stack["config_files"])
        lines.append(f"**Config files**: {configs}")
    
    if fs_stack.get("scripts"):
        scripts = ", ".join(f"`{k}`" for k in list(fs_stack["scripts"].keys())[:8])
        lines.append(f"**npm scripts**: {scripts}")
    
    if fs_stack.get("env_keys"):
        keys = ", ".join(f"`{k}`" for k in fs_stack["env_keys"][:10])
        lines.append(f"**Env vars**: {keys}")
    
    if fs_stack.get("git_branch"):
        lines.append(f"**Git branch**: `{fs_stack['git_branch']}`")
    
    if fs_stack.get("recent_commits"):
        commits = "\n".join(f"  - {c}" for c in fs_stack["recent_commits"][:5])
        lines.append(f"**Recent commits**:\n{commits}")
    
    return "\n".join(lines)


def action_update(cwd: Path, cfg: dict) -> None:
    """Scan filesystem and update context.md."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"
    
    # Detect project stack from filesystem
    fs_stack = detect_project_stack(cwd)
    
    print(f"[claude-recall] Scanning project: {slug}")
    print(f"  Directory: {cwd}")
    print(f"  Detected: {', '.join(fs_stack.get('stack', ['nothing detected']))}")
    print()
    
    # Build auto-content
    stack_str = " · ".join(fs_stack.get("stack", []))
    
    env_parts = []
    if fs_stack.get("env_keys"):
        env_parts.append("Env vars: " + ", ".join(fs_stack["env_keys"][:10]))
    if fs_stack.get("git_branch"):
        env_parts.append(f"Git branch: {fs_stack['git_branch']}")
    environment_str = "\n".join(env_parts)
    
    # Structure info
    structure_parts = []
    if fs_stack.get("structure"):
        structure_parts.append("Directories: " + ", ".join(f"{d}/" for d in fs_stack["structure"][:10]))
    if fs_stack.get("config_files"):
        structure_parts.append("Config: " + ", ".join(fs_stack["config_files"]))
    
    project_dir.mkdir(parents=True, exist_ok=True)

    # Build structure info
    structure_parts = []
    if fs_stack.get("structure"):
        structure_parts.append("Directories: " + ", ".join(f"{d}/" for d in fs_stack["structure"][:10]))
    if fs_stack.get("config_files"):
        structure_parts.append("Config: " + ", ".join(fs_stack["config_files"]))

    # Build description from type
    project_type = fs_stack.get("type", "unknown")
    project_name = fs_stack.get("name") or slug
    if project_type == "claude-skill":
        what_desc = f"Claude Code skill for {project_name} persistent memory"
    elif project_type == "node":
        what_desc = f"Node.js project {project_name}"
    elif project_type == "python":
        what_desc = f"Python project {project_name}"
    else:
        what_desc = f"Project {project_name}"

    if not context_md.exists():
        # Create new context.md with detected info
        content = f"""\
---
project: {slug}
directory: {cwd}
created: {datetime.now().strftime('%Y-%m-%d')}
tags: [claude-recall, context]
---

# {slug}

## What this is
<!-- auto:what_this_is:start -->
{what_desc}
<!-- auto:what_this_is:end -->

## Stack
<!-- auto:stack:start -->
{stack_str}
<!-- auto:stack:end -->

## Current state
<!-- auto:current_state:start -->
<!-- auto:current_state:end -->

## Architecture decisions
<!-- auto:architecture:start -->
<!-- auto:architecture:end -->

## Gotchas
<!-- auto:gotchas:start -->
<!-- auto:gotchas:end -->

## Environment
<!-- auto:environment:start -->
{environment_str}
<!-- auto:environment:end -->
"""
        context_md.write_text(content, encoding="utf-8")
        print(f"  ✓ Created context.md with detected stack info")
    else:
        # Update existing context.md
        existing = context_md.read_text(encoding="utf-8")
        updated = existing

        if stack_str:
            updated = merge_auto_section(updated, "stack", stack_str)
        if environment_str:
            updated = merge_auto_section(updated, "environment", environment_str)
        if what_desc:
            updated = merge_auto_section(updated, "what_this_is", what_desc)

        if updated != existing:
            context_md.write_text(updated, encoding="utf-8")
            print(f"  ✓ Updated context.md with fresh stack/environment info")
        else:
            print(f"  · context.md already up to date")
    
    # Print detected info for Claude to see
    print()
    print("## Detected Project Info")
    print()
    print(format_stack_info(fs_stack))


def action_status(cwd: Path, cfg: dict) -> None:
    """Print what claude-recall knows about this project."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"
    sessions_dir = project_dir / "sessions"
    vault_root = get_vault_root(cfg)
    index_path = vault_root / "_index.md"
    
    print(f"## claude-recall status: {slug}")
    print(f"**Directory**: `{cwd}`")
    print(f"**Project dir**: `{project_dir}`")
    print()
    
    # Context.md status
    if context_md.exists():
        text = context_md.read_text(encoding="utf-8")
        if is_scaffold_only(text):
            print("⚠ `context.md` exists but is empty scaffold — run `/recall update`")
        else:
            lines = len(text.splitlines())
            print(f"✓ `context.md` — {lines} lines of project context")
    else:
        print("✗ `context.md` — not found (will be created on first session end)")
    
    # Session count
    if sessions_dir.exists():
        sessions = list(sessions_dir.glob("*.md"))
        print(f"✓ **{len(sessions)} sessions** recorded")
        if sessions:
            latest = sorted(sessions, reverse=True)[0]
            print(f"  Latest: `{latest.name}`")
    else:
        print("· No sessions recorded yet")
    
    # Index entry
    entries = parse_index_entries(index_path)
    for e in entries:
        if e["slug"] == slug:
            print(f"✓ **Index**: {e['sessions']} sessions, {e['total_turns']} total turns, last active {e['last_active']}")
            break
    else:
        print("· Not in project index yet")
    
    print()
    
    # Print context.md content if it exists and has content
    if context_md.exists():
        text = context_md.read_text(encoding="utf-8")
        if not is_scaffold_only(text):
            print("---")
            print()
            print("### Current context.md content")
            print()
            print(text)


def action_reset(cwd: Path, cfg: dict) -> None:
    """Regenerate context.md from scratch using filesystem detection."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"
    
    if context_md.exists():
        # Backup old context.md
        backup = project_dir / f"context.md.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        context_md.rename(backup)
        print(f"  Backed up existing context.md → {backup.name}")
    
    # Run update to create fresh
    action_update(cwd, cfg)
    print()
    print("  ✓ context.md regenerated from filesystem analysis")
    print("  Previous context backed up (check project folder)")


def main() -> None:
    # Parse arguments
    action = "update"
    cwd = Path(os.getcwd())
    
    args = sys.argv[1:]
    if args:
        action = args[0].lower().strip("-/")
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
        print("Usage: recall_update.py [update|status|reset] [directory]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[claude-recall] recall error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
