#!/usr/bin/env python3
"""
post_tool_use.py — claude-recall PostToolUse hook.

Fires after each tool use to detect file edits and mark mindmap nodes stale.
This keeps context fresh by invalidating nodes when their files are modified.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import load_config, get_project_dir, read_hook_input, cwd_to_slug
from mindmap import load_mindmap, mark_files_stale, save_mindmap

SKIP_TOOLS = {"Edit", "Write", "MultiEdit", "Create"}


def main() -> None:
    hook_input = read_hook_input()
    tool_name = hook_input.get("tool_name", "")

    if tool_name not in SKIP_TOOLS:
        return

    tool_input = hook_input.get("tool_input", {})
    changed_files = []

    if tool_name == "Edit":
        fp = tool_input.get("file_path")
        if fp:
            changed_files.append(fp)
    elif tool_name == "Write":
        fp = tool_input.get("file_path")
        if fp:
            changed_files.append(fp)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        changed_files = [e.get("file_path") for e in edits if e.get("file_path")]
    elif tool_name == "Create":
        fp = tool_input.get("file_path")
        if fp:
            changed_files.append(fp)

    if not changed_files:
        return

    slug_env = Path.home() / ".claude" / "claude-recall-slug.env"
    if not slug_env.exists():
        return

    slug = slug_env.read_text().strip().split("=")[-1]
    if not slug or slug == "unknown":
        return

    cfg = load_config()
    project_dir = get_project_dir(cfg, slug)

    if not (project_dir / "mindmap.json").exists():
        return

    mindmap = load_mindmap(project_dir)
    stale_ids = mark_files_stale(mindmap, changed_files)

    if stale_ids:
        save_mindmap(project_dir, mindmap)
        file_list = ", ".join(changed_files[:3])
        print(f"[claude-recall] ⚠ {len(stale_ids)} context nodes stale after editing {file_list}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)