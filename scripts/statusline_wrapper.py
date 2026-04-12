#!/usr/bin/env python3
"""
statusline_wrapper.py — claude-recall statusLine integration.

Claude Code's statusLine runs a command and displays its stdout as the
bottom status bar. This wrapper:
  1. Runs the existing upstream statusLine command (e.g. GSD's)
  2. Reads /tmp/claude-recall-status.json (written by session_start.py)
  3. Appends recall info to the output

stdin: JSON from Claude Code (model, workspace, context_window, etc.)
stdout: Formatted status bar text with ANSI codes
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CACHE_PATH = Path("/tmp/claude-recall-status.json")
# The upstream statusLine command is stored here by install.sh
UPSTREAM_CMD_PATH = Path.home() / ".claude" / "claude-recall-upstream-statusline.txt"


def run_upstream(stdin_data: str) -> str:
    """Run the upstream statusLine command and capture its output."""
    if not UPSTREAM_CMD_PATH.exists():
        return ""
    try:
        cmd = UPSTREAM_CMD_PATH.read_text().strip()
        if not cmd:
            return ""
        result = subprocess.run(
            cmd,
            shell=True,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_recall_status() -> str:
    """Read recall cache and return a short status string."""
    try:
        if not CACHE_PATH.exists():
            return ""
        data = json.loads(CACHE_PATH.read_text())
        slug = data.get("slug", "")
        sessions = data.get("sessions", 0)
        is_new = data.get("is_new", False)

        if not slug:
            return ""

        if is_new:
            return f"\x1b[2m☰ claude-recall: {slug} (new)\x1b[0m"
        else:
            return f"\x1b[2m☰ claude-recall: {sessions} sessions\x1b[0m"
    except Exception:
        return ""


def main():
    # Read stdin (Claude Code passes session data as JSON)
    stdin_data = sys.stdin.read()

    # Run upstream statusLine
    upstream = run_upstream(stdin_data)

    # Get recall status
    recall = get_recall_status()

    # Combine: upstream │ recall
    parts = [p for p in [upstream, recall] if p]
    sys.stdout.write(" │ ".join(parts) if len(parts) > 1 else (parts[0] if parts else ""))


if __name__ == "__main__":
    main()
