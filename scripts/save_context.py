#!/usr/bin/env python3
"""
save_context.py — claude-recall Stop hook.

Fires when the Claude Code session ends. Reads the session transcript,
extracts key facts, and:
1. Writes a structured session note to Obsidian
2. Updates context.md with session learnings
3. Updates _index.md project index

Never exits non-zero.
"""

import json
import re
import sys
import traceback
import os
import subprocess
from datetime import datetime
from pathlib import Path

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
    """Parse transcript JSONL and extract structured data."""
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

    # Supplement with regex
    all_text = " ".join(m["content"] for m in messages if isinstance(m.get("content"), str))
    file_re = re.compile(
        r'[\w./\-]+\.(?:tsx?|jsx?|py|dart|go|rs|rb|java|kt|swift|'
        r'md|json|yaml|yml|toml|sh|html|css|scss)\b'
    )
    raw_files = list(dict.fromkeys(m.group() for m in file_re.finditer(all_text)))
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

    Uses the FIRST user message (what they asked), not tool outputs.
    """
    user_msgs = [m["content"] for m in transcript["messages"] if m.get("role") == "user"]
    if not user_msgs:
        return ""

    first_prompt = user_msgs[0].strip().split("\n")[0][:150]

    # Clean up HTML/command fragments
    first_prompt = re.sub(r'<[^>]+>', '', first_prompt).strip()
    if len(first_prompt) < 10:
        return ""

    # Skip if it looks like a path or command
    if first_prompt.startswith("/") or first_prompt.startswith("cd "):
        return ""

    return f"Last session: {first_prompt}"


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

    _debug(f"session_id={session_id}, transcript={transcript_path}, cwd={cwd}")

    cleanup_stale_markers()

    if not cfg.get("save_sessions", True):
        _debug("save_sessions disabled")
        return

    # Parse transcript
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

    # Extract facts
    facts = extract_facts(transcript, cwd)

    # LLM summary (if available)
    llm_data = None
    if _HAS_LLM and ensure_model():
        llm_data = _llm_summary(messages, facts=facts)
        if llm_data:
            _debug(f"LLM summary OK: {llm_data.get('summary', '')[:60]}")
        else:
            _debug("LLM summary returned None")

    # Generate context.md if it doesn't exist yet
    if is_context_empty_or_missing(project_dir):
        try:
            content = build_compact_context(cwd, slug)
            (project_dir / "context.md").write_text(content, encoding="utf-8")
            _debug("Created initial context.md")
        except Exception as e:
            _debug(f"Failed to create context.md: {e}")

    # Update context.md with session learnings
    current_state = ""
    if llm_data and llm_data.get("summary"):
        current_state = f"Last session: {llm_data['summary'][:200]}"
    else:
        current_state = extract_current_state(transcript)

    decisions = extract_decisions(transcript)
    gotchas = extract_gotchas(transcript)

    # Normalize file paths for key_files update
    key_files = filter_file_paths(facts.get("files", []), cwd)

    update_context_after_session(
        project_dir=project_dir,
        slug=slug,
        cwd=cwd,
        current_state=current_state,
        decisions=decisions if decisions else None,
        gotchas=gotchas if gotchas else None,
        key_files_update=key_files[:10] if key_files else None,
    )

    # Write session note
    note = build_session_note(slug, cwd, session_id, facts, llm_data)
    note_path = project_dir / "sessions" / f"{now_str()}.md"

    _debug(f"Writing note to: {note_path}")
    try:
        note_path.write_text(note, encoding="utf-8")
        _debug("Note written successfully")
    except PermissionError as e:
        _debug(f"Permission error writing note: {e}")
        print(f"[claude-recall] Cannot write session note: {note_path}", file=sys.stderr)
        return

    # Update vault index
    update_index(vault_root, slug, cwd, facts["turns"])

    # Clean up session marker
    clear_session_marker(session_id, cwd)

    _debug(f"=== SAVED: {note_path} ===")
    print(f"[claude-recall] Saved to Obsidian → {note_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        save_session()
    except Exception as exc:
        _debug(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] save error: {exc}", file=sys.stderr)
        sys.exit(0)
