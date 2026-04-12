#!/usr/bin/env python3
"""
session_start.py — claude-recall SessionStart hook.

Fires when Claude Code starts. Shows a quick status message.
stdout → system context (visible to Claude, not user)
stderr → visible to user in terminal
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

    if context_md.exists():
        # Show a quick status to the user
        print(f"[claude-recall] 🧠 Memory loaded for '{slug}'", file=sys.stderr)
    else:
        print(f"[claude-recall] 🆕 New project detected: '{slug}'", file=sys.stderr)

except Exception as exc:
    # Never fail
    try:
        with open(_log, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SESSION_START: ERROR: {exc}\n")
    except Exception:
        pass

sys.exit(0)
