#!/usr/bin/env python3
"""
load_context.py — claude-recall UserPromptSubmit hook.

Fires before every user prompt:
1. Loads relevant context nodes from mindmap.json based on current prompt
2. Injects last session summary for continuity
3. Prints to stdout → Claude reads as system context

Never exits non-zero — a failed hook would block Claude from starting.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import os
from datetime import datetime
from pathlib import Path

try:
    _log = Path.home() / ".claude" / "claude-recall-debug.log"
    with open(_log, "a") as _f:
        _f.write(f"[{datetime.now().isoformat()}] LOAD: >>> SCRIPT STARTED (pid={os.getpid()})\n")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    load_config, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, truncate_to_tokens, DEBUG_LOG,
)
from session_manager import (
    should_load_context, mark_session_loaded, cleanup_stale_markers,
    get_last_session_summary,
)
from context_builder import (
    build_initial_mindmap, is_context_empty_or_missing,
)

from mindmap import load_mindmap, get_relevant_nodes, mindmap_to_context_md


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] LOAD: {msg}\n")
    except Exception:
        pass


def start_mcp_if_needed() -> None:
    """Start MCP server process if not already running."""
    import subprocess

    pid_file = Path.home() / ".claude" / "claude-recall-mcp.pid"
    script_path = Path(__file__).parent / "mcp_server.py"

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            try:
                os.kill(existing_pid, 0)
                _debug(f"MCP already running with PID {existing_pid}")
                return
            except (OSError, ProcessLookupError):
                _debug(f"Stale PID file, removing")
                pid_file.unlink()
        except Exception:
            pid_file.unlink()

    try:
        env = os.environ.copy()
        slug_env_path = Path.home() / ".claude" / "claude-recall-slug.env"
        if slug_env_path.exists():
            for line in slug_env_path.read_text().splitlines():
                if "=" in line:
                    key, val = line.split("=", 1)
                    env[key] = val

        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_file.write_text(str(proc.pid))
        _debug(f"Started MCP server with PID {proc.pid}")
    except Exception as e:
        _debug(f"Failed to start MCP server: {e}")


def load_context() -> None:
    _debug("=== LOAD SESSION STARTED ===")
    _debug(f"CWD: {os.getcwd()}")

    try:
        hook_input = read_hook_input()
        session_id = hook_input.get("session_id", "unknown")
        cwd = get_cwd(hook_input)
        cfg = load_config()
        current_prompt = hook_input.get("prompt", "")

        _debug(f"session_id={session_id}, cwd={cwd}, prompt_len={len(current_prompt)}")

        cleanup_stale_markers()

        # Determine if this is the first prompt of the session
        is_first_prompt = should_load_context(session_id, cwd)
        
        if is_first_prompt:
            mark_session_loaded(session_id, cwd)
        
        # Always load if we have a prompt to match against (per-prompt keyword mode)
        # OR if it's the first prompt (full context mode)
        has_prompt = bool(current_prompt.strip())
        should_run = is_first_prompt or (has_prompt and cfg.get("load_on_every_prompt", True))
        
        if not should_run:
            _debug("Skipping - no prompt text and not first prompt")
            return

        slug = cwd_to_slug(cwd)

        # Write slug to env file for MCP server
        slug_env_path = Path.home() / ".claude" / "claude-recall-slug.env"
        slug_env_path.write_text(f"CLAUDE_RECALL_SLUG={slug}\n")
        _debug(f"Wrote slug env file: {slug_env_path}")
        project_dir = get_project_dir(cfg, slug)
        mindmap_path = project_dir / "mindmap.json"

        _debug(f"slug={slug}, project_dir={project_dir}")

        if is_context_empty_or_missing(project_dir):
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                print(f"[claude-recall] ⚡ Generating mindmap for '{slug}'...", file=sys.stderr)
                mindmap = build_initial_mindmap(cwd, slug, project_dir)
                _debug(f"Auto-generated mindmap.json ({len(mindmap.get('nodes', {}))} nodes)")
                print(f"[claude-recall] ✓ Mindmap generated ({len(mindmap.get('nodes', {}))} nodes)", file=sys.stderr)
            except Exception as exc:
                _debug(f"Auto-generate failed: {exc}")
                print(f"[claude-recall] ✗ Auto-generate error: {exc}", file=sys.stderr)

        mindmap = load_mindmap(project_dir)

        if is_first_prompt and not current_prompt:
            # First prompt with no text yet — inject overview + recent session
            all_nodes = mindmap.get("nodes", {})
            relevant = [
                {"node_id": k, **v} 
                for k, v in list(all_nodes.items())[:3]
                if v.get("content")
            ]
            _debug("First prompt: injecting top 3 nodes (no prompt text yet)")
        elif current_prompt:
            # Subsequent prompts — keyword match to inject only relevant nodes
            max_nodes = 2 if not is_first_prompt else 3
            relevant = get_relevant_nodes(mindmap, current_prompt, max_nodes=max_nodes)
            _debug(f"Keyword match on '{current_prompt[:50]}': {[r['node_id'] for r in relevant]}")
        else:
            _debug("No prompt and not first — skipping injection")
            return

        _debug(f"Found {len(relevant)} relevant nodes")

        context_lines = []
        for node in relevant:
            context_lines.append(f"### {node['node_id'].replace('_', ' ').title()}")
            context_lines.append(node['content'])
            if node.get('files'):
                context_lines.append(f"Files: {', '.join(node['files'][:4])}")
        context_text = "\n\n".join(context_lines)

        max_ctx = cfg.get("max_context_tokens", 400)
        context_text = truncate_to_tokens(context_text, max_ctx)

        last_summary = get_last_session_summary(project_dir)
        if last_summary:
            last_summary = truncate_to_tokens(last_summary, int(max_ctx * 0.2))

        _debug(f"Returning {len(context_text)} chars of context")

        print(
            f"<!-- claude-recall: {len(relevant)} relevant context nodes loaded -->\n"
            f"Project: `{slug}` | Dir: `{cwd}` | Nodes: {len(mindmap.get('nodes', {}))}\n\n"
            f"## Relevant Context\n\n{context_text}\n\n"
            + (f"## Previous session\n\n{last_summary}\n\n" if last_summary else "")
            + "> **claude-recall active** — use MCP tool `recall_get` for deeper context.\n"
        )

        start_mcp_if_needed()

    except Exception as exc:
        _debug(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] ✗ load error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    load_context()