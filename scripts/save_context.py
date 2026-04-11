#!/usr/bin/env python3
"""
save_context.py — claude-recall Stop hook.

Fires when the Claude Code session ends. Reads the session transcript,
extracts key facts, and writes a structured Markdown note to Obsidian:

  <vault>/claude-recall/projects/<slug>/sessions/YYYY-MM-DD_HH-MM.md

On first session for a project it also generates:
  <vault>/claude-recall/projects/<slug>/context.md  ← auto-populated, user can edit
  <vault>/claude-recall/_index.md                   ← deduplicated project index

On subsequent sessions it MERGES new learnings into existing context.md,
preserving any user-written content outside auto-markers.

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

DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SAVE: {msg}\n")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, now_str, session_marker, cleanup_stale_markers,
    filter_file_paths, detect_project_stack, is_scaffold_only,
    parse_index_entries, merge_auto_section, build_index_table,
)


def parse_transcript_full(path: str) -> dict:
    """Parse transcript JSONL and extract structured data.

    Returns dict with:
      - messages: list of {role, content} for text content
      - tool_calls: list of {tool, input, output} for tool use blocks
      - errors: list of error messages found in tool results
      - file_ops: list of (operation, path) from Read/Write/Edit/Bash
    """
    result = {
        "messages": [],
        "tool_calls": [],
        "errors": [],
        "file_ops": [],
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

                    # Handle different content formats
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
                                    # Check for errors
                                    _check_error(tool_content, result["errors"])

                            elif btype == "tool_use":
                                # Extract structured tool call info
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                result["tool_calls"].append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                })
                                # Track file operations
                                _track_file_ops(tool_name, tool_input, result["file_ops"])

                    if content_str.strip():
                        result["messages"].append({"role": role, "content": content_str})

                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        print(f"[claude-recall] Transcript read error: {exc}", file=sys.stderr)

    return result


def _check_error(text: str, errors: list) -> None:
    """Check if text contains an error and extract it."""
    error_indicators = [
        r"(?:Error|Exception|Failed|FAILED)(?:\s+:|\s+–)?\s*(.{10,200}?)(?:\n|$)",
        r"^\s*(?:Error|Exception):\s*(.{10,200}?)$",
        r"Traceback\s+\(most\s+recent\s+call\s+last\)",
    ]
    text_lower = text.lower()
    if any(indicator in text_lower for indicator in ["error:", "exception:", "failed:", "traceback"]):
        for pattern in error_indicators:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                err_text = m.group(0).strip()[:200]
                if err_text not in errors:
                    errors.append(err_text)
                break


def _track_file_ops(tool_name: str, tool_input: dict, file_ops: list) -> None:
    """Track file read/write/edit operations from tool calls."""
    if tool_name in ("Read", "Glob", "Grep"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            file_ops.append(("read", file_path))
    elif tool_name in ("Write", "NotebookEdit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            file_ops.append(("write", file_path))
    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        if file_path:
            file_ops.append(("edit", file_path))
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        if command:
            file_ops.append(("bash", command))


def extract_facts(transcript: dict) -> dict:
    """Extract session facts from parsed transcript data."""
    messages = transcript["messages"]
    tool_calls = transcript["tool_calls"]
    errors = transcript["errors"]
    file_ops = transcript["file_ops"]

    debug_log(f"Extracting facts: {len(messages)} msgs, {len(tool_calls)} tool calls, {len(errors)} errors")

    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]

    # Get files from tool operations (most reliable)
    files_from_ops = []
    for op, path in file_ops:
        if op in ("read", "write", "edit") and path:
            files_from_ops.append(path)

    # Also get from regex on text (supplementary)
    all_text = " ".join(m["content"] for m in messages if isinstance(m.get("content"), str))
    file_re = re.compile(
        r'[\w./\-]+\.(?:tsx?|jsx?|py|dart|go|rs|rb|java|kt|swift|'
        r'md|json|yaml|yml|toml|sh|env|sql|html|css|scss)\b'
    )
    raw_files = list(dict.fromkeys(m.group() for m in file_re.finditer(all_text)))
    files_from_text = filter_file_paths(raw_files)

    # Merge, prioritize tool op files
    all_files = list(dict.fromkeys(files_from_ops + files_from_text))
    files = filter_file_paths(all_files)

    return {
        "first_prompt":    (user_msgs[0][:300].replace("\n", " ") if user_msgs else "(no messages)"),
        "turns":           len(user_msgs),
        "total_messages":  len(messages),
        "files":           files,
        "tool_count":      len(tool_calls),
        "errors":          errors,
    }


def clean_html_comments(text: str) -> str:
    """Remove HTML comments and control markers from text."""
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

def extract_context_from_transcript(transcript: dict, cwd: Path) -> dict:
    """Analyze transcript to extract rich project context.

    Uses structured tool call data for reliable extraction, supplemented
    by text analysis for semantic understanding.
    """
    messages = transcript["messages"]
    tool_calls = transcript["tool_calls"]
    errors = transcript["errors"]
    file_ops = transcript["file_ops"]

    context = {
        "what_this_is": "",
        "stack_hints": [],
        "current_state": "",
        "architecture": [],
        "gotchas": [],
        "tools_used": [],
        "tasks_completed": [],
        "files_changed": set(),
        "errors_fixed": [],
    }

    if not messages:
        return context

    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    assistant_msgs = [m["content"] for m in messages if m.get("role") == "assistant"]
    all_text = " ".join(m["content"] for m in messages)

    # ── Track tools used ──
    tool_names = list(dict.fromkeys(t["tool"] for t in tool_calls if t["tool"]))
    context["tools_used"] = tool_names[:15]

    # ── Track files changed (write/edit operations) ──
    for op, path in file_ops:
        if op in ("write", "edit") and path:
            context["files_changed"].add(path)

    # ── What this is ──
    # Try to extract from early assistant messages (Claude usually describes the project)
    for msg in assistant_msgs[:5]:
        # Look for project description patterns
        desc_patterns = [
            r"(?:this (?:is|appears to be|looks like) (?:a|an) )(.{20,200}?)(?:\.|$)",
            r"(?:The project is )(.{20,200}?)(?:\.|$)",
            r"(?:This (?:app|application|project|codebase) )(.{20,200}?)(?:\.|$)",
        ]
        for pattern in desc_patterns:
            m = re.search(pattern, msg, re.IGNORECASE)
            if m:
                context["what_this_is"] = m.group(0).strip().rstrip(".")
                break
        if context["what_this_is"]:
            break

    # ── Stack hints from conversation ──
    stack_keywords = {
        "next.js": "Next.js", "react": "React", "vue": "Vue.js",
        "angular": "Angular", "svelte": "Svelte", "express": "Express.js",
        "flask": "Flask", "django": "Django", "fastapi": "FastAPI",
        "flutter": "Flutter", "swift": "Swift", "kotlin": "Kotlin",
        "tailwind": "Tailwind CSS", "typescript": "TypeScript",
        "mongodb": "MongoDB", "postgresql": "PostgreSQL", "mysql": "MySQL",
        "redis": "Redis", "firebase": "Firebase", "supabase": "Supabase",
        "docker": "Docker", "kubernetes": "Kubernetes",
        "graphql": "GraphQL", "trpc": "tRPC", "prisma": "Prisma",
        "socket.io": "Socket.io", "websocket": "WebSocket",
        "stripe": "Stripe", "aws": "AWS", "vercel": "Vercel",
        "railway": "Railway", "cloudflare": "Cloudflare",
    }
    text_lower = all_text.lower()
    for keyword, label in stack_keywords.items():
        if keyword in text_lower:
            context["stack_hints"].append(label)
    context["stack_hints"] = list(dict.fromkeys(context["stack_hints"]))[:10]

    # ── Architecture decisions ──
    decision_patterns = [
        r"I\s+(?:chose|decided|picked|went\s+with|opted\s+for)\s+([A-Z].{10,100})",
        r"(?:better\s+to\s+use)\s+([A-Z].{10,100})",
        r"(?:we\s+should\s+use)\s+([A-Z].{10,100})",
    ]
    for msg in messages:
        content = msg["content"]
        for pattern in decision_patterns:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                decision = m.group(0).strip().rstrip(".")
                if len(decision) < 20:
                    continue
                if "?(" in decision or "?)" in decision or ".{" in decision:
                    continue
                if decision not in context["architecture"]:
                    context["architecture"].append(decision)
    seen = set()
    clean_arch = []
    for d in context["architecture"]:
        if d not in seen:
            seen.add(d)
            clean_arch.append(d)
    context["architecture"] = clean_arch[:5]

    # ── Gotchas (from errors + warnings) ──
    gotcha_patterns = [
        r"(?:(?:watch out|be careful|don't forget|remember to|make sure|important:?) )(.{10,200}?)(?:\.|$)",
        r"(?:(?:the (?:issue|problem|bug|error) (?:was|is)) )(.{10,200}?)(?:\.|$)",
        r"(?:(?:this (?:won't work|doesn't work|breaks|fails) (?:because|if|when)) )(.{10,200}?)(?:\.|$)",
        r"(?:MUST )(.{10,150}?)(?:\.|$)",
    ]
    for msg in assistant_msgs:
        for pattern in gotcha_patterns:
            for m in re.finditer(pattern, msg, re.IGNORECASE):
                gotcha = m.group(0).strip().rstrip(".")
                if len(gotcha) > 15 and gotcha not in context["gotchas"]:
                    context["gotchas"].append(gotcha)
    context["gotchas"] = context["gotchas"][:5]

    # Add captured errors as gotchas
    for err in errors[:3]:
        if err not in context["gotchas"]:
            context["gotchas"].append(err)

    # ── Current state ──
    # Build from what was worked on
    if user_msgs:
        topics = []
        for msg in user_msgs[-5:]:
            first_line = msg.strip().split("\n")[0][:150]
            clean_line = clean_html_comments(first_line).strip()
            if len(clean_line) > 10:
                topics.append(clean_line)
        if topics:
            context["current_state"] = f"Last session worked on: {topics[0]}"

    # ── Tasks completed (from assistant summary patterns) ──
    summary_patterns = [
        r"(?:(?:Done|Finished|Completed|Fixed|Updated|Created|Added|Implemented)[\.:!] )(.{10,200}?)(?:\.|!|$)",
    ]
    for msg in assistant_msgs:
        for pattern in summary_patterns:
            for m in re.finditer(pattern, msg, re.IGNORECASE):
                task = m.group(0).strip().rstrip(".!")[:150]
                if len(task) > 10 and task not in context["tasks_completed"]:
                    context["tasks_completed"].append(task)
    context["tasks_completed"] = context["tasks_completed"][:5]

    return context


def extract_git_changes(cwd: Path) -> dict:
    """Extract git diff stats from the session."""
    result = {
        "changed_files": [],
        "diff_summary": "",
        "new_files": [],
    }

    try:
        # Get diff --stat
        diff_proc = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=str(cwd), capture_output=True, text=True, timeout=10
        )
        if diff_proc.returncode == 0 and diff_proc.stdout.strip():
            result["diff_summary"] = diff_proc.stdout.strip()

            # Parse changed files
            for line in diff_proc.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] not in ("files", "insertions", "deletions", "file"):
                    if not parts[0].startswith("-"):
                        result["changed_files"].append(parts[-1])

        # Get new untracked files
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd), capture_output=True, text=True, timeout=10
        )
        if status_proc.returncode == 0:
            for line in status_proc.stdout.strip().splitlines():
                if line.startswith("?? "):
                    result["new_files"].append(line[3:])
    except Exception as e:
        debug_log(f"Git diff error: {e}")

    return result


def extract_session_summary(transcript: dict, git_changes: dict) -> str:
    """Extract a meaningful summary of what happened in the session."""
    messages = transcript["messages"]
    if not messages:
        return ""

    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    assistant_msgs = [m["content"] for m in messages if m.get("role") == "assistant"]
    tool_calls = transcript["tool_calls"]
    file_ops = transcript["file_ops"]

    parts = []

    # What did the user want?
    if user_msgs:
        first = user_msgs[0][:200].replace("\n", " ").strip()
        parts.append(f"Started with: {first}")

    # What was done? Look for patterns in assistant messages
    if assistant_msgs:
        last = assistant_msgs[-1][:300].replace("\n", " ").strip()
        summary_patterns = [
            r"(?:I've |I have |We've |We have )(.{20,200}?)(?:\.|!|$)",
            r"(?:The .{5,30} (?:is|are) now )(.{10,100}?)(?:\.|!|$)",
            r"(?:(?:Done|Finished|Completed|Fixed|Updated|Created|Added)[\.:!] )(.{10,200}?)(?:\.|!|$)",
        ]
        for pattern in summary_patterns:
            m = re.search(pattern, last, re.IGNORECASE)
            if m:
                parts.append(m.group(0).strip())
                break

    # Add git changes summary if meaningful
    changed = len(git_changes.get("changed_files", []))
    new_files = len(git_changes.get("new_files", []))
    if changed > 0:
        parts.append(f"{changed} file(s) modified")
    if new_files > 0:
        parts.append(f"{new_files} new file(s) created")

    # Add tool usage summary
    if tool_calls:
        tool_counts = {}
        for t in tool_calls:
            name = t.get("tool", "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1
        tools_str = ", ".join(f"{n}x{v}" for n, v in tool_counts.items() if v >= 2)
        if tools_str:
            parts.append(f"Tools used: {tools_str}")

    return " · ".join(parts) if parts else ""


# ── Note builders ─────────────────────────────────────────────────────────────

def build_session_note(slug: str, cwd: Path, session_id: str, facts: dict,
                       summary: str = "", transcript: dict = None,
                       git_changes: dict = None) -> str:
    ts = datetime.now()
    transcript = transcript or {}
    git_changes = git_changes or {}

    # Files section - combine from multiple sources
    all_files = list(facts.get("files", []))
    for op, path in transcript.get("file_ops", []):
        if op in ("write", "edit") and path and path not in all_files:
            all_files.append(path)

    files_section = ""
    if all_files:
        items = "\n".join(f"- `{f}`" for f in all_files[:20])
        files_section = f"\n## Files touched\n\n{items}\n"

    # Tools section
    tools_section = ""
    if transcript.get("tool_calls"):
        tool_counts = {}
        for t in transcript["tool_calls"]:
            name = t.get("tool", "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1
        tools_list = [f"- `{name}`: {count}x" for name, count in sorted(tool_counts.items())]
        tools_section = f"\n## Tools used\n\n" + "\n".join(tools_list[:10]) + "\n"

    # Errors section
    errors_section = ""
    if facts.get("errors"):
        errors_list = "\n".join(f"- `{e}`" for e in facts["errors"][:5])
        errors_section = f"\n## Errors encountered\n\n{errors_list}\n"

    # Git changes section
    git_section = ""
    if git_changes.get("diff_summary"):
        git_section = f"\n## Git changes\n\n```\n{git_changes['diff_summary']}\n```\n"
    if git_changes.get("new_files"):
        new_files_list = "\n".join(f"- `{f}`" for f in git_changes["new_files"][:10])
        git_section += f"\n### New files\n\n{new_files_list}\n"

    summary_section = ""
    if summary:
        summary_section = f"\n## Summary\n\n{summary}\n"

    return (
        f"---\n"
        f"date: {ts.strftime('%Y-%m-%d')}\n"
        f"time: {ts.strftime('%H:%M')}\n"
        f"project: {slug}\n"
        f"directory: {cwd}\n"
        f"session_id: {session_id}\n"
        f"turns: {facts['turns']}\n"
        f"tags: [claude-recall, session]\n"
        f"---\n\n"
        f"# Session {ts.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Directory\n\n`{cwd}`\n\n"
        f"## Started with\n\n> {facts['first_prompt']}\n\n"
        f"## Stats\n\n"
        f"{facts['turns']} user turns · {facts['total_messages']} total messages"
        f" · {facts.get('tool_count', 0)} tool calls\n"
        f"{summary_section}"
        f"{files_section}"
        f"{tools_section}"
        f"{errors_section}"
        f"{git_section}"
        f"## Next steps\n\n"
        f"- [ ] _(edit in Obsidian or ask Claude to summarise)_\n"
    )


CONTEXT_TEMPLATE = """\
---
project: {slug}
directory: {cwd}
created: {date}
tags: [claude-recall, context]
---

# {slug}

## What this is
{what_this_is}

## Stack
{stack}

## Current state
{current_state}

## Key files
{key_files}

## Architecture decisions
{architecture}

## Gotchas
{gotchas}

## Environment
{environment}
"""


def build_key_files_section(files_changed: set, all_files: list) -> str:
    """Build the key files section from changed files and mentioned files."""
    if not files_changed and not all_files:
        return ""
    # Prioritize changed files
    files = list(files_changed)
    for f in all_files:
        if f not in files:
            files.append(f)
    files = files[:15]
    return "\n".join(f"- `{f}`" for f in files)


def update_context_md(project_dir: Path, slug: str, cwd: Path,
                      transcript_context: dict, fs_stack: dict,
                      git_changes: dict = None) -> None:
    """Create or update context.md with auto-generated content.

    On first creation: populate from transcript analysis + filesystem detection.
    On update: merge new learnings, preserving user-written content.
    """
    context_md = project_dir / "context.md"
    git_changes = git_changes or {}

    # Build auto-content for each section
    # Stack: combine filesystem detection (reliable) + transcript hints
    stack_items = list(dict.fromkeys(
        fs_stack.get("stack", []) + transcript_context.get("stack_hints", [])
    ))
    stack_str = " · ".join(stack_items) if stack_items else ""

    what_this_is = transcript_context.get("what_this_is", "")
    current_state = transcript_context.get("current_state", "")

    # Key files: from git changes + tool operations
    files_changed = transcript_context.get("files_changed", set())
    all_files = transcript_context.get("files", [])
    if git_changes.get("changed_files"):
        files_changed.update(git_changes["changed_files"])
    if git_changes.get("new_files"):
        files_changed.update(git_changes["new_files"])
    key_files_str = build_key_files_section(files_changed, all_files)

    architecture_items = transcript_context.get("architecture", [])
    architecture_str = "\n".join(f"- {d}" for d in architecture_items) if architecture_items else ""

    gotcha_items = transcript_context.get("gotchas", [])
    gotchas_str = "\n".join(f"- {g}" for g in gotcha_items) if gotcha_items else ""

    # Environment from filesystem
    env_parts = []
    if fs_stack.get("env_keys"):
        env_parts.append("Env vars: " + ", ".join(fs_stack["env_keys"][:10]))
    if fs_stack.get("git_branch"):
        env_parts.append(f"Git branch: {fs_stack['git_branch']}")
    if fs_stack.get("recent_commits"):
        env_parts.append("Recent commits:")
        for c in fs_stack["recent_commits"][:5]:
            env_parts.append(f"  - {c}")
    environment_str = "\n".join(env_parts) if env_parts else ""

    if not context_md.exists():
        # ── First creation: build full template with auto-markers ──
        def wrap_auto(section_name: str, content: str) -> str:
            if not content.strip():
                return f"<!-- auto:{section_name}:start -->\n<!-- auto:{section_name}:end -->"
            return f"<!-- auto:{section_name}:start -->\n{content}\n<!-- auto:{section_name}:end -->"

        content = CONTEXT_TEMPLATE.format(
            slug=slug,
            cwd=cwd,
            date=datetime.now().strftime("%Y-%m-%d"),
            what_this_is=wrap_auto("what_this_is", what_this_is),
            stack=wrap_auto("stack", stack_str),
            current_state=wrap_auto("current_state", current_state),
            key_files=wrap_auto("key_files", key_files_str),
            architecture=wrap_auto("architecture", architecture_str),
            gotchas=wrap_auto("gotchas", gotchas_str),
            environment=wrap_auto("environment", environment_str),
        )

        context_md.write_text(content, encoding="utf-8")
        print(
            f"[claude-recall] Created context note: {context_md}\n"
            f"  Auto-populated with detected project info.",
            file=sys.stderr,
        )
    else:
        # ── Update: merge new content into existing ──
        existing = context_md.read_text(encoding="utf-8")
        updated = existing

        if stack_str:
            updated = merge_auto_section(updated, "stack", stack_str)
        if current_state:
            updated = merge_auto_section(updated, "current_state", current_state)
        if key_files_str:
            updated = merge_auto_section(updated, "key_files", key_files_str)
        if architecture_str:
            updated = merge_auto_section(updated, "architecture", architecture_str)
        if gotchas_str:
            updated = merge_auto_section(updated, "gotchas", gotchas_str)
        if what_this_is:
            updated = merge_auto_section(updated, "what_this_is", what_this_is)

        if updated != existing:
            context_md.write_text(updated, encoding="utf-8")
            debug_log("context.md updated with new auto-content")


def update_index(vault_root: Path, slug: str, cwd: Path, turns: int) -> None:
    """Update _index.md with deduplicated project entry.
    
    If the project already exists, updates its row in-place.
    If new, appends a new row.
    """
    index_path = vault_root / "_index.md"
    
    # Parse existing entries (handles both old and new format)
    entries = parse_index_entries(index_path)
    
    # Find or create entry for this slug
    found = False
    for entry in entries:
        if entry["slug"] == slug:
            entry["sessions"] += 1
            entry["total_turns"] += turns
            entry["last_active"] = now_str("%Y-%m-%d %H:%M")
            entry["directory"] = str(cwd)  # Update in case it changed
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
    
    # Rebuild the index file
    index_path.write_text(build_index_table(entries), encoding="utf-8")
    debug_log(f"Index updated: {slug} ({'existing' if found else 'new'})")


# ── Main ──────────────────────────────────────────────────────────────────────

def save_session() -> None:
    debug_log("=== SAVE SESSION STARTED ===")
    debug_log(f"CWD: {os.getcwd()}, Args: {sys.argv}")

    hook_input      = read_hook_input()
    debug_log(f"Hook input: {hook_input}")

    session_id      = hook_input.get("session_id", now_str())
    transcript_path = hook_input.get("transcript_path", "")
    cwd             = get_cwd(hook_input)
    cfg             = load_config()

    debug_log(f"session_id={session_id}, transcript={transcript_path}, cwd={cwd}")
    debug_log(f"Config vault: {cfg.get('vault_path')}")

    cleanup_stale_markers()

    if not cfg.get("save_sessions", True):
        debug_log("save_sessions disabled, returning")
        return

    # Parse transcript with full structured data
    transcript = parse_transcript_full(transcript_path) if transcript_path else {
        "messages": [], "tool_calls": [], "errors": [], "file_ops": []
    }
    messages = transcript["messages"]
    debug_log(f"Parsed {len(messages)} messages, {len(transcript['tool_calls'])} tool calls")
    if not messages:
        debug_log("No messages - returning early")
        return   # Nothing happened this session — skip

    slug        = cwd_to_slug(cwd)
    vault_root  = get_vault_root(cfg)
    project_dir = get_project_dir(cfg, slug)

    debug_log(f"slug={slug}, vault_root={vault_root}, project_dir={project_dir}")

    # Edge case: read-only vault — wrap in try/except to avoid hook failure
    try:
        (project_dir / "sessions").mkdir(parents=True, exist_ok=True)
        debug_log(f"Created sessions dir: {project_dir / 'sessions'}")
    except PermissionError as e:
        debug_log(f"Permission error: {e}")
        print(
            f"[claude-recall] Cannot write to vault — check permissions: {project_dir}",
            file=sys.stderr,
        )
        return

    # Extract facts and context from transcript
    facts = extract_facts(transcript)
    transcript_context = extract_context_from_transcript(transcript, cwd)

    # Get git changes
    git_changes = extract_git_changes(cwd)

    summary = extract_session_summary(transcript, git_changes)

    # Detect project stack from filesystem
    fs_stack = detect_project_stack(cwd)

    # Auto-generate/update context.md (replaces old empty scaffold)
    try:
        update_context_md(project_dir, slug, cwd, transcript_context, fs_stack, git_changes)
    except Exception as e:
        debug_log(f"Error updating context.md: {e}")
        # Non-fatal — continue with session note

    # Write session note
    note      = build_session_note(slug, cwd, session_id, facts, summary, transcript, git_changes)
    note_path = project_dir / "sessions" / f"{now_str()}.md"

    debug_log(f"Writing note to: {note_path}")
    try:
        note_path.write_text(note, encoding="utf-8")
        debug_log("Note written successfully")
    except PermissionError as e:
        debug_log(f"Permission error writing note: {e}")
        print(
            f"[claude-recall] Cannot write session note — check permissions: {note_path}",
            file=sys.stderr,
        )
        return

    # Update vault index (deduplicated)
    update_index(vault_root, slug, cwd, facts["turns"])

    # Clean up session marker
    marker = session_marker(session_id)
    if marker.exists():
        marker.unlink(missing_ok=True)

    debug_log(f"=== SAVED: {note_path} ===")
    print(f"[claude-recall] Saved to Obsidian → {note_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        save_session()
    except Exception as exc:
        debug_log(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] save error: {exc}", file=sys.stderr)
        sys.exit(0)
