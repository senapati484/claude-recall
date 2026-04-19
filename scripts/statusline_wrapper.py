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
import shutil
import subprocess
import sys
from pathlib import Path

CACHE_PATH = Path("/tmp/claude-recall-status.json")
UPSTREAM_CMD_PATH = Path.home() / ".claude" / "claude-recall-upstream-statusline.txt"


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 120


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
            return f"\x1b[2m\u2630 claude-recall: {slug} (new)\x1b[0m"
        else:
            return f"\x1b[2m\u2630 claude-recall: {sessions} sessions\x1b[0m"
    except Exception:
        return ""


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 2] + "…"


def main():
    stdin_data = sys.stdin.read()
    upstream = run_upstream(stdin_data)
    recall = get_recall_status()

    parts = [p for p in [upstream, recall] if p]
    if not parts:
        return

    combined = " \u2502 ".join(parts) if len(parts) > 1 else parts[0]

    max_width = _terminal_width()
    combined = _truncate(combined, max_width)

    sys.stdout.write(combined)


if __name__ == "__main__":
    main()
