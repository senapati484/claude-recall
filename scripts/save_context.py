#!/usr/bin/env python3
"""
save_context.py — claude-recall Stop hook.

CRITICAL DESIGN NOTE:
The `Stop` hook fires after EVERY assistant response turn, NOT just when
the user closes the terminal. This means for a session with 5 user prompts,
this script runs 5+ times.

Our strategy:
- Use session_id to write to ONE session note (overwrite on each turn)
- The note filename is based on session_id, not timestamp
- Each call reads the FULL growing transcript, so later calls have more data
- LLM summary only runs when the transcript has enough content (>= 3 messages)

Never exits non-zero — a failed hook would block Claude.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import os
import subprocess
from datetime import datetime
from pathlib import Path

# EARLY DIAGNOSTIC — log immediately to confirm hook is being called
try:
    _log = Path.home() / ".claude" / "claude-recall-debug.log"
    with open(_log, "a") as _f:
        _f.write(f"[{datetime.now().isoformat()}] SAVE: >>> SCRIPT STARTED (pid={os.getpid()})\n")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

try:
    from summarize import generate_summary as _llm_summary
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False

from utils import (
    load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, now_str, filter_file_paths, detect_project_stack,
    parse_index_entries, build_index_table, ensure_model, DEBUG_LOG,
    notify_terminal,
)
from session_manager import (
    build_session_note, clear_session_marker, cleanup_stale_markers,
)
from context_builder import (
    update_context_after_session, is_context_empty_or_missing,
    build_compact_context,
)


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SAVE: {msg}\n")
    except Exception:
        pass


# ── Transcript parsing ────────────────────────────────────────────────────────

def parse_transcript(path: str) -> dict:
    """Parse transcript JSONL and extract structured data.

    Handles Claude Code's real transcript format where each line has:
    - type: "user", "assistant", "system", "permission-mode", etc.
    - message: {role, content} (only for user/assistant types)
    """
    result = {
        "messages": [], "tool_calls": [], "errors": [], "file_ops": [],
    }
    if not path:
        return result

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue

                    role = msg.get("role")
                    content = msg.get("content")

                    if not role:
                        continue

                    content_str = ""
                    if isinstance(content, str):
                        content_str = content
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type")
                            if btype == "text":
                                content_str += block.get("text", "") or ""
                            elif btype == "tool_result":
                                tool_content = block.get("content", "")
                                if isinstance(tool_content, str):
                                    content_str += tool_content
                            elif btype == "tool_use":
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                result["tool_calls"].append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                })
                                _track_file_ops(tool_name, tool_input, result["file_ops"])

                    if content_str.strip():
                        result["messages"].append({"role": role, "content": content_str})

                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        _debug(f"Transcript read error: {exc}")
        print(f"[claude-recall] Transcript read error: {exc}", file=sys.stderr)

    return result


def _track_file_ops(tool_name: str, tool_input: dict, file_ops: list) -> None:
    """Track file read/write/edit operations from tool calls."""
    if tool_name in ("Read", "Glob", "Grep"):
        fp = tool_input.get("file_path", "")
        if fp:
            file_ops.append(("read", fp))
    elif tool_name in ("Write", "NotebookEdit"):
        fp = tool_input.get("file_path", "")
        if fp:
            file_ops.append(("write", fp))
    elif tool_name == "Edit":
        fp = tool_input.get("file_path", "")
        if fp:
            file_ops.append(("edit", fp))
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd:
            file_ops.append(("bash", cmd))


# ── Fact extraction ──────────────────────────────────────────────────────────

def extract_facts(transcript: dict, cwd: Path) -> dict:
    """Extract session facts from parsed transcript data."""
    messages = transcript["messages"]
    tool_calls = transcript["tool_calls"]
    file_ops = transcript["file_ops"]

    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]

    # Files from tool operations (most reliable)
    files = []
    for op, path in file_ops:
        if op in ("read", "write", "edit") and path:
            files.append(path)

    # Supplement with regex from assistant messages
    asst_text = " ".join(
        m["content"] for m in messages
        if m.get("role") == "assistant" and isinstance(m.get("content"), str)
    )
    file_re = re.compile(
        r'[\w./\-]+\.(?:tsx?|jsx?|py|dart|go|rs|rb|java|kt|swift|'
        r'md|json|yaml|yml|toml|sh|html|css|scss)\b'
    )
    raw_files = list(dict.fromkeys(m.group() for m in file_re.finditer(asst_text)))
    files = list(dict.fromkeys(files + raw_files))
    files = filter_file_paths(files, cwd)

    return {
        "first_prompt": (user_msgs[0][:300].replace("\n", " ") if user_msgs else "(no messages)"),
        "turns": len(user_msgs),
        "total_messages": len(messages),
        "files": files,
        "tool_count": len(tool_calls),
    }


def extract_current_state(transcript: dict) -> str:
    """Extract what the user was working on (for context.md current_state).

    Uses the LAST user message (most recent work).
    """
    user_msgs = [m["content"] for m in transcript["messages"] if m.get("role") == "user"]
    if not user_msgs:
        return ""

    # Use the last user message for the most recent context
    last_prompt = user_msgs[-1].strip().split("\n")[0][:150]

    # Clean up
    last_prompt = re.sub(r'<[^>]+>', '', last_prompt).strip()
    if len(last_prompt) < 10:
        # Fall back to first
        last_prompt = user_msgs[0].strip().split("\n")[0][:150]
        last_prompt = re.sub(r'<[^>]+>', '', last_prompt).strip()
        if len(last_prompt) < 10:
            return ""

    # Skip if it looks like a path or command
    if last_prompt.startswith("/") or last_prompt.startswith("cd "):
        return ""

    return f"Working on: {last_prompt}"


def extract_decisions(transcript: dict) -> list[str]:
    """Extract architecture/design decisions from transcript."""
    assistant_msgs = [m["content"] for m in transcript["messages"] if m.get("role") == "assistant"]
    decisions = []
    patterns = [
        r"I\s+(?:chose|decided|picked|went\s+with|opted\s+for)\s+([A-Z].{10,100})",
        r"(?:better\s+to\s+use)\s+([A-Z].{10,100})",
    ]
    for msg in assistant_msgs[-10:]:
        for pattern in patterns:
            for m in re.finditer(pattern, msg, re.IGNORECASE):
                decision = m.group(1).strip().rstrip(".")
                if len(decision) > 15 and "{" not in decision and "`" not in decision:
                    decisions.append(decision)

    return list(dict.fromkeys(decisions))[:5]


def extract_gotchas(transcript: dict) -> list[str]:
    """Extract important warnings/gotchas from assistant messages."""
    assistant_msgs = [m["content"] for m in transcript["messages"] if m.get("role") == "assistant"]
    gotchas = []
    patterns = [
        r"(?:watch out|be careful|don't forget|make sure|important)[\s:]+([A-Z][^\n]{15,150})(?:\.|$)",
        r"(?:the (?:issue|problem|bug) (?:was|is))[\s:]+([A-Z][^\n]{15,150})(?:\.|$)",
    ]
    for msg in assistant_msgs[-10:]:
        for pattern in patterns:
            for m in re.finditer(pattern, msg, re.IGNORECASE):
                gotcha = m.group(1).strip().rstrip(".")
                if len(gotcha) > 15 and "{" not in gotcha and "`" not in gotcha:
                    alpha_ratio = sum(c.isalpha() for c in gotcha) / max(len(gotcha), 1)
                    if alpha_ratio > 0.5:
                        gotchas.append(gotcha)

    return list(dict.fromkeys(gotchas))[:5]


# ── Session note path ─────────────────────────────────────────────────────────

def _session_note_path(sessions_dir: Path, session_id: str) -> Path:
    """Get session note path — uses session_id so the same session always
    overwrites the same file (critical because Stop fires per-turn).

    The filename is: <date>_<short-id>.md
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    short_id = session_id[:8] if session_id else "unknown"
    return sessions_dir / f"{date_str}_{short_id}.md"


# ── Index update ──────────────────────────────────────────────────────────────

def update_index(vault_root: Path, slug: str, cwd: Path, turns: int) -> None:
    """Update _index.md with deduplicated project entry."""
    index_path = vault_root / "_index.md"
    entries = parse_index_entries(index_path)

    found = False
    for entry in entries:
        if entry["slug"] == slug:
            entry["sessions"] += 1
            entry["total_turns"] += turns
            entry["last_active"] = now_str("%Y-%m-%d %H:%M")
            entry["directory"] = str(cwd)
            found = True
            break

    if not found:
        entries.append({
            "slug": slug,
            "directory": str(cwd),
            "sessions": 1,
            "total_turns": turns,
            "last_active": now_str("%Y-%m-%d %H:%M"),
        })

    index_path.write_text(build_index_table(entries), encoding="utf-8")
    _debug(f"Index updated: {slug} ({'existing' if found else 'new'})")


# ── Main ──────────────────────────────────────────────────────────────────────

def save_session() -> None:
    _debug("=== SAVE SESSION STARTED ===")

    hook_input      = read_hook_input()
    session_id      = hook_input.get("session_id", now_str())
    transcript_path = hook_input.get("transcript_path", "")
    cwd             = get_cwd(hook_input)
    cfg             = load_config()

    _debug(f"session_id={session_id}, cwd={cwd}")

    cleanup_stale_markers()

    if not cfg.get("save_sessions", True):
        _debug("save_sessions disabled")
        return

    # Parse the FULL transcript (it grows with each turn)
    transcript = parse_transcript(transcript_path) if transcript_path else {
        "messages": [], "tool_calls": [], "errors": [], "file_ops": []
    }
    messages = transcript["messages"]
    _debug(f"Parsed {len(messages)} messages, {len(transcript['tool_calls'])} tool calls")
    if not messages:
        _debug("No messages - returning early")
        return

    slug        = cwd_to_slug(cwd)
    vault_root  = get_vault_root(cfg)
    project_dir = get_project_dir(cfg, slug)

    try:
        (project_dir / "sessions").mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        _debug(f"Permission error: {e}")
        print(f"[claude-recall] Cannot write to vault: {project_dir}", file=sys.stderr)
        return

    # Extract facts from the FULL transcript
    facts = extract_facts(transcript, cwd)

    # LLM summary — only attempt if transcript has enough content (>= 4 messages)
    # For very short sessions (1-2 turns), LLM tends to echo the example prompt
    llm_data = None
    if _HAS_LLM and ensure_model() and facts["total_messages"] >= 4:
        llm_data = _llm_summary(messages, facts=facts)
        if llm_data:
            _debug(f"LLM summary OK: {llm_data.get('summary', '')[:60]}")
        else:
            _debug("LLM summary returned None")
    elif facts["total_messages"] < 4:
        _debug(f"Skipping LLM summary — only {facts['total_messages']} messages (minimum 4)")

    # Generate context.md if it doesn't exist yet
    if is_context_empty_or_missing(project_dir):
        try:
            print(f"[claude-recall] Generating context for '{slug}'...", file=sys.stderr)
            content = build_compact_context(cwd, slug)
            (project_dir / "context.md").write_text(content, encoding="utf-8")
            _debug("Created initial context.md")
        except Exception as e:
            _debug(f"Failed to create context.md: {e}")

    # Build current_state from LLM or regex
    current_state = ""
    if llm_data and llm_data.get("summary"):
        current_state = f"Last session: {llm_data['summary'][:200]}"
    else:
        current_state = extract_current_state(transcript)

    decisions = extract_decisions(transcript)
    gotchas = extract_gotchas(transcript)

    # Normalize file paths for key_files update
    key_files = filter_file_paths(facts.get("files", []), cwd)

    # Update context.md with session learnings
    update_context_after_session(
        project_dir=project_dir,
        slug=slug,
        cwd=cwd,
        current_state=current_state,
        decisions=decisions if decisions else None,
        gotchas=gotchas if gotchas else None,
        key_files_update=key_files[:10] if key_files else None,
    )

    # Write session note — OVERWRITE same file for same session_id
    # This is critical because Stop fires after EVERY turn, not just at exit.
    # Each call gets the FULL growing transcript, so later calls have more data.
    note = build_session_note(slug, cwd, session_id, facts, llm_data)
    note_path = _session_note_path(project_dir / "sessions", session_id)

    _debug(f"Writing note to: {note_path} (overwrite={note_path.exists()})")
    try:
        note_path.write_text(note, encoding="utf-8")
        _debug("Note written successfully")
    except PermissionError as e:
        _debug(f"Permission error writing note: {e}")
        print(f"[claude-recall] Cannot write session note: {note_path}", file=sys.stderr)
        return

    # Update vault index — only on first save for this session
    # (Avoid inflating session count on every turn)
    index_marker = project_dir / "sessions" / f".{session_id[:8]}_indexed"
    if not index_marker.exists():
        update_index(vault_root, slug, cwd, facts["turns"])
        try:
            index_marker.write_text(now_str(), encoding="utf-8")
        except Exception:
            pass
    else:
        _debug("Skipping index update — already indexed for this session")

    # Don't clear the session marker here — the session is still active!
    # The marker will be cleaned up by stale marker cleanup on next load.

    _debug(f"=== SAVED: {note_path} (turns={facts['turns']}, msgs={facts['total_messages']}) ===")
    notify_terminal(f"[claude-recall] ✓ Session saved → {slug} ({facts['turns']} turns)")


if __name__ == "__main__":
    try:
        save_session()
    except Exception as exc:
        _debug(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] save error: {exc}", file=sys.stderr)
        sys.exit(0)
