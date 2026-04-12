#!/usr/bin/env python3
"""
session_start.py — claude-recall SessionStart hook.

Fires when Claude Code starts a session.
stdout → injected into Claude's system context (Claude sees it)
stderr → may or may not be visible to user (version-dependent)
"""
import sys
import os
from datetime import datetime
from pathlib import Path

# Early log
try:
    _log = Path.home() / ".claude" / "claude-recall-debug.log"
    with open(_log, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] SESSION_START: >>> Hook fired (pid={os.getpid()})\n")
except Exception:
    pass

try:
    sys.path.insert(0, str(Path(__file__).parent))
    from utils import load_config, get_cwd, cwd_to_slug, read_hook_input, get_project_dir

    hook_input = read_hook_input()
    cwd = get_cwd(hook_input)
    slug = cwd_to_slug(cwd)
    cfg = load_config()
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"
    sessions_dir = project_dir / "sessions"
    session_count = len(list(sessions_dir.glob("*.md"))) if sessions_dir.exists() else 0

    # Print to STDOUT — Claude sees this as system context
    if context_md.exists():
        print(
            f"[claude-recall] 🧠 Project memory active for '{slug}' "
            f"({session_count} past sessions). "
            f"Context will load with your first prompt."
        )
    else:
        print(
            f"[claude-recall] 🆕 New project '{slug}' detected. "
            f"Context will be auto-generated on first prompt."
        )

    # Also stderr for terminal (may or may not be visible)
    print(f"[claude-recall] ✓ Memory ready for '{slug}'", file=sys.stderr)

    try:
        with open(_log, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SESSION_START: OK slug={slug} sessions={session_count}\n")
    except Exception:
        pass

except Exception as exc:
    try:
        with open(_log, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SESSION_START: ERROR: {exc}\n")
    except Exception:
        pass

sys.exit(0)
