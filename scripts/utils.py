"""
utils.py — Shared helpers for claude-recall.

All scripts import from here. Config lives at ~/.claude/claude-recall.json
and points to the user's Obsidian vault.
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "claude-recall.json"
DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] UTILS: {msg}\n")
    except Exception:
        pass

DEFAULT_CONFIG = {
    "vault_path": "",                  # Required — set by install.sh
    "vault_folder": "claude-recall",   # Folder inside the vault
    "max_context_tokens": 2000,
    "include_recent_sessions": 2,
    "save_sessions": True,
    "load_on_every_prompt": False,
}


def load_config() -> dict:
    """Load config, merging user values over defaults."""
    if not CONFIG_PATH.exists():
        print(
            "[claude-recall] Config not found. Run install.sh first.",
            file=sys.stderr,
        )
        sys.exit(0)
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(user)
        if not cfg["vault_path"]:
            print("[claude-recall] vault_path is empty. Re-run install.sh.", file=sys.stderr)
            sys.exit(0)
        return cfg
    except json.JSONDecodeError as e:
        print(f"[claude-recall] Config malformed: {e}. Re-run install.sh.", file=sys.stderr)
        sys.exit(0)


def get_vault_root(cfg: dict) -> Path:
    """Return the claude-recall folder inside the Obsidian vault."""
    vault = Path(cfg["vault_path"]).expanduser()
    if not vault.exists():
        # Edge case: unmounted drive — tell user to check mount
        print(
            f"[claude-recall] Vault not found at {vault}. "
            "Check if the drive is mounted and vault_path in ~/.claude/claude-recall.json.",
            file=sys.stderr,
        )
        sys.exit(0)
    return vault / cfg["vault_folder"]


def cwd_to_slug(cwd: Path) -> str:
    """
    Convert a project directory path into a short, Obsidian-safe slug.

    /home/sayan/projects/setu          → setu
    /home/sayan/client/acme/dashboard  → acme-dashboard
    /mnt/c/Users/sayan/work/api        → work-api   (WSL paths handled)

    Generic segments stripped: projects repos code src workspace dev work home
    """
    parts = list(cwd.parts)

    # Strip WSL Windows prefix /mnt/X/
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1:
        parts = parts[3:]

    # Strip home dir prefix
    home_parts = list(Path.home().parts)
    while parts and home_parts and parts[0] == home_parts[0]:
        parts.pop(0)
        home_parts.pop(0)

    # Drop generic noise segments
    noise = {"projects", "repos", "code", "src", "workspace", "dev", "work", "home"}
    meaningful = [p for p in parts if p.lower() not in noise]
    if meaningful:
        chosen = meaningful[-2:]
    elif len(parts) >= 2:
        chosen = parts[-2:]
    else:
        chosen = parts

    slug = "-".join(chosen).lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug).strip("-")
    return slug or "unknown-project"


def get_project_dir(cfg: dict, slug: str) -> Path:
    """Return vault_root/projects/<slug>/"""
    return get_vault_root(cfg) / "projects" / slug


def read_hook_input() -> dict:
    """Read Claude Code hook JSON from stdin or environment variables."""
    # First try stdin
    try:
        raw = sys.stdin.read().strip()
        if raw:
            debug_log(f"read_hook_input: got stdin data: {raw[:100]}")
            return json.loads(raw)
    except Exception as e:
        debug_log(f"read_hook_input: stdin error: {e}")
    
    # Try environment variables
    for key in ["CLAUDE_HOOK_INPUT", "CLAUDE_SESSION_ID", "CLAUDE_CWD", "HOOK_INPUT"]:
        val = os.environ.get(key)
        if val:
            debug_log(f"read_hook_input: found env {key}={val[:50]}")
            try:
                return json.loads(val)
            except:
                pass
    
    debug_log("read_hook_input: no input found, returning empty dict")
    return {}


def get_cwd(hook_input: dict) -> Path:
    return Path(hook_input.get("cwd") or os.getcwd())


def now_str(fmt: str = "%Y-%m-%d_%H-%M") -> str:
    return datetime.now().strftime(fmt)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough budget: 4 chars ≈ 1 token. Cuts at a line boundary."""
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    cut = text[:limit].rfind("\n")
    cut = cut if cut > limit // 2 else limit
    return text[:cut] + "\n\n[claude-recall: truncated — edit context.md in Obsidian to trim]"


def session_marker(session_id: str) -> Path:
    """Marker file preventing context re-injection on every prompt."""
    # Edge case: empty/unknown session_id — use timestamp+PID to avoid collisions
    if not session_id or session_id == "unknown":
        session_id = f"{now_str()}_{os.getpid()}"
    return Path.home() / ".claude" / f".recall_{session_id}"


def cleanup_stale_markers():
    """Delete marker files older than 24 h (crash cleanup)."""
    import time
    cutoff = time.time() - 86400
    for f in (Path.home() / ".claude").glob(".recall_*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            pass
