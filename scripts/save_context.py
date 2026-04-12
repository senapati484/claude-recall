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

# CRITICAL: must set path BEFORE importing summarize
sys.path.insert(0, str(Path(__file__).parent))

try:
    from summarize import generate_summary as _llm_summary
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False

DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] SAVE: {msg}\n")
    except Exception:
        pass

from utils import (
    load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, now_str, session_marker, cleanup_stale_markers,
    filter_file_paths, detect_project_stack, is_scaffold_only,
    parse_index_entries, merge_auto_section, build_index_table,
    ensure_model,
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
    """Check if text contains an error and extract it.

    Only captures clean, readable error messages. Skips:
    - Python format strings like "{exc}", "{e}"
    - Traceback fragments without actual error messages
    - Very short or malformed strings
    """
    # Skip if text looks like Python code or contains format strings
    if "{" in text and "}" in text and ("exc" in text or "e}" in text):
        return  # Likely a format string like "{exc}", "{e}"

    error_indicators = [
        r"(?:Error|Exception|Failed|FAILED)(?:\s+:|\s+–)?\s*(.{10,200}?)",
    ]
    text_lower = text.lower()
    # Skip very short texts or tool result content that looks like exception dumps
    if len(text) < 15:
        return
    # Skip Python traceback dumps — they contain many newlines and file paths
    if text.count("\n") > 3 and ("traceback" in text_lower or "file " in text_lower):
        return
    if any(indicator in text_lower for indicator in ["error:", "exception:", "failed:", "traceback"]):
        for pattern in error_indicators:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                err_text = m.group(0).strip()[:200]
                # Clean up and validate
                err_text = re.sub(r'\s+', ' ', err_text)  # collapse whitespace
                # Skip if it looks like code/format string
                if "{" in err_text or "}" in err_text:
                    continue
                if err_text.count(":") > 5:
                    continue  # Probably a traceback dump
                if len(err_text) < 15:
                    continue
                # Skip if it contains file paths or line numbers (traceback fragment)
                if re.search(r'[/\\]\w+\.\w+:\d+', err_text):
                    continue
                if re.search(r'\d+\s+\|', err_text):
                    continue  # diff line like "10 | code"
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
    """Remove HTML comments, Claude Code internal tags, and control markers."""
    text = re.sub(r'<command-\w+>.*?</command-\w+>', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    return text

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
    # SKIP session summary patterns that come from save_context's own session notes
    SESSION_SUMMARY_PATTERNS = [
        r"^The session starts? (?:with|by) ",
        r"^Started with: ",
        r"^Check(ing)? the (?:codebase|project)",
        r"^Fix(ing)? (?:the )?(?:context|llm|skill)",
    ]
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
                candidate = m.group(0).strip().rstrip(".")
                # Skip if it looks like a session summary, not a project description
                if any(re.match(p, candidate, re.IGNORECASE) for p in SESSION_SUMMARY_PATTERNS):
                    continue
                context["what_this_is"] = candidate
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
    # Only accept stack hints from transcript if they appear in proper context
    # (not in paths, package names, or as substrings of other words)
    # For ambiguous keywords like "angular" (could be "angular momentum"),
    # require the keyword to be followed by tech-related terms
    tech_suffixes = [
        " framework", " library", "js", "ts", "python", "java", "node",
        " app", " project", " code", " api", " server", " client",
        ".js", ".ts", ".py", "()", " component", " module",
    ]
    for keyword, label in stack_keywords.items():
        if keyword not in text_lower:
            continue
        # Skip if it's clearly a JSON key (package.json style: "keyword":)
        if re.search(r'''["']''' + re.escape(keyword) + r'''["']?\s*:''', text_lower):
            continue
        # Skip if it's in a URL/path (e.g., /node_modules/next/)
        if f"/{keyword}/" in text_lower or f"/{keyword}" in text_lower:
            continue
        # For short keywords (< 6 chars), verify it's not a substring of another word
        if len(keyword) < 6:
            # Check word boundaries - keyword should be surrounded by space/punct
            pattern = r'(?<![a-z])' + re.escape(keyword) + r'(?![a-z])'
            if not re.search(pattern, text_lower):
                continue
            # Extra check for short tech words that appear in common English
            # e.g., "java" in "javascript" - only accept if followed by tech suffix
            if len(keyword) <= 4:
                found_proper = False
                for suffix in tech_suffixes:
                    if keyword + suffix in text_lower:
                        found_proper = True
                        break
                if not found_proper:
                    continue
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
                # Use group(1) to get the captured decision text, not the full match
                decision = m.group(1).strip().rstrip(".")
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

    # ── Gotchas (from assistant advice — NOT from errors) ──
    gotcha_patterns = [
        # Only capture clear advisory patterns with proper word boundaries
        r"(?:watch out|be careful|don't forget|remember to|make sure|important)[:\s]+([A-Z][^\n]{10,150})(?:\.|$)",
        r"(?:the (?:issue|problem|bug) (?:was|is|was caused by))[:\s]+([A-Z][^\n]{10,150})(?:\.|$)",
        r"(?:this (?:won't work|doesn't work|breaks|fails) (?:because|when|if))[:\s]+([A-Z][^\n]{10,150})(?:\.|$)",
        r"(?:MUST[:\s]+)([A-Z][^\n]{10,150})(?:\.|$)",
    ]
    for msg in assistant_msgs:
        for pattern in gotcha_patterns:
            for m in re.finditer(pattern, msg, re.IGNORECASE):
                gotcha = m.group(1).strip().rstrip(".")
                # Strict filters — gotchas must be human-readable advice, not code/fragments
                if len(gotcha) < 15:
                    continue
                if any(c in gotcha for c in ['{', '}', 'sys.', 'file=', '", ', '"', '->', '::']):
                    continue
                # Must have reasonable alpha ratio (not mostly symbols/numbers)
                alpha_ratio = sum(c.isalpha() for c in gotcha) / max(len(gotcha), 1)
                if alpha_ratio < 0.5:
                    continue
                # Skip debugging explanation fragments (these look like "X is caused by Y" not real gotchas)
                skip_prefixes = [
                    "be in how", "the source", "the reason", "this happens because",
                    "the issue is that", "the problem is", "in response parsing",
                    "how `recall", "the `recall", "be to check", "the installed",
                ]
                if any(gotcha.lower().startswith(p) for p in skip_prefixes):
                    continue
                # Skip fragments containing inline code (backtick strings)
                if '`' in gotcha:
                    continue
                if gotcha not in context["gotchas"]:
                    context["gotchas"].append(gotcha)
    context["gotchas"] = context["gotchas"][:5]

    # Skip adding captured errors to gotchas — they are too noisy and contain
    # traceback/code fragments. The gotchas section relies solely on pattern-matched
    # advice from assistant messages.

    # ── Current state ──
    # Build from what was worked on - skip lines that look like code/shell errors
    CODE_INDICATORS = [
        '# ', '## ', 'import ', 'from ', 'export ', 'const ', 'let ', 'var ',
        'function ', 'class ', 'def ', 'fn ', 'pub ', 'struct ', 'interface ',
        'print(', 'console.', 'return ', 'if ', 'for ', 'while ', 'switch(',
        '```', '<!-- ', '===', '---', '--- ', '/*', '*/', '//',
        'npm ', 'git ', 'python', 'pip ', 'cargo ', 'go ', 'rustc ',
        # Shell stderr / eval error patterns
        '(eval):', 'bash:', 'zsh:', 'sh:', '/bin/', '/usr/bin/',
        'no matches found:', 'not found:', 'command not found:',
        '/Users/', '/home/', 'C:\\', '~\'', '"', "']",
    ]
    # Regex to detect lines that look like "123 # comment" (file content with line numbers)
    LINE_NUM_RE = re.compile(r'^\s*\d+\s+#')
    # Detect shell error fragments
    SHELL_ERROR_RE = re.compile(r'^(?:\[.*?\]\s*)?(?:error|warning|failed|no matches|not found)[:\s]', re.IGNORECASE)

    if user_msgs:
        topics = []
        for msg in user_msgs[-5:]:
            first_line = msg.strip().split("\n")[0][:150]
            clean_line = clean_html_comments(first_line).strip()
            # Skip lines that look like code/paths/commands
            if len(clean_line) < 15:
                continue
            if any(clean_line.startswith(ci) for ci in CODE_INDICATORS):
                continue
            # Skip lines that look like file content with line numbers
            if LINE_NUM_RE.match(clean_line):
                continue
            # Skip shell error lines like "(eval):1: no matches found:"
            if SHELL_ERROR_RE.match(clean_line):
                continue
            # Skip lines that are mostly code-like characters
            alpha_ratio = sum(c.isalpha() for c in clean_line) / max(len(clean_line), 1)
            if alpha_ratio < 0.4:  # Less than 40% letters = likely code
                continue
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
                task = m.group(1).strip().rstrip(".!")[:150]  # group(1) = captured text
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

            # Parse changed files - git diff --stat format:
            # filename | XX ++++ ----
            # First field is always the filename
            for line in diff_proc.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 1:
                    file_path = parts[0]
                    # Skip summary lines
                    if file_path in ("files", "insertions", "deletions", "file", "files", "total"):
                        continue
                    # Skip if it contains path separators that don't look like files
                    # (e.g., "++++++++++++++++++++++++++++++" would fail this)
                    if "/" not in file_path and not file_path.endswith(".py") and not file_path.endswith(".md"):
                        continue
                    result["changed_files"].append(file_path)

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

def build_session_note(slug: str, cwd: Path, session_id: str,
                       facts: dict, llm_data: dict | None = None) -> str:
    ts = datetime.now()

    # --- Files section ---
    if llm_data and llm_data.get("files_and_roles"):
        files_items = "\n".join(
            f"- `{f}` — {role}"
            for f, role in llm_data["files_and_roles"].items()
        )
        files_section = f"\n## Files touched\n\n{files_items}\n"
    elif facts.get("files"):
        files_section = "\n## Files touched\n\n" + "\n".join(
            f"- `{f}`" for f in facts["files"]
        ) + "\n"
    else:
        files_section = ""

    # --- Summary line ---
    if llm_data and llm_data.get("summary"):
        summary_section = f"\n## Summary\n\n{llm_data['summary']}\n"
    else:
        summary_section = (
            f"\n## Summary\n\n"
            f"Started with: {facts.get('first_prompt','?')} · "
            f"{len(facts.get('files',[]))} file(s) modified\n"
        )

    # --- Decisions ---
    decisions_section = ""
    if llm_data and llm_data.get("decisions"):
        items = "\n".join(f"- {d}" for d in llm_data["decisions"])
        decisions_section = f"\n## Key decisions\n\n{items}\n"

    # --- Next steps ---
    if llm_data and llm_data.get("next_steps"):
        steps = "\n".join(f"- [ ] {s}" for s in llm_data["next_steps"])
        next_section = f"\n## Next steps\n\n{steps}\n"
    else:
        next_section = "\n## Next steps\n\n- [ ] _(edit in Obsidian or ask Claude to summarise)_\n"

    # --- Keywords ---
    keywords_section = ""
    if llm_data and llm_data.get("keywords"):
        kw = ", ".join(f"`{k}`" for k in llm_data["keywords"])
        keywords_section = f"\n**Keywords:** {kw}\n"

    frontmatter = (
        f"---\n"
        f"date: {ts.strftime('%Y-%m-%d')}\n"
        f"time: {ts.strftime('%H:%M')}\n"
        f"project: {slug}\n"
        f"directory: {cwd}\n"
        f"session_id: {session_id}\n"
        f"turns: {facts.get('turns', 0)}\n"
        f"llm_summary: {'true' if llm_data else 'false'}\n"
        f"tags: [claude-recall, session]\n"
        f"---\n\n"
        f"# Session {ts.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Directory\n\n`{cwd}`\n\n"
        f"## Started with\n\n> {facts.get('first_prompt','?')}\n\n"
        f"## Stats\n\n{facts.get('turns',0)} user turns · "
        f"{facts.get('total_messages',0)} total messages\n"
    )

    return frontmatter + summary_section + decisions_section + files_section + next_section + keywords_section


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
    """Build the key files section from changed files and mentioned files.

    Filters out git diff artifacts, paths with only +-/ characters, etc.
    """
    # Filter files to exclude garbage
    FILE_GARBAGE_RE = re.compile(r'^[\+\-\s|]+$|^\d+\s+\d+$')

    def is_valid_file(f: str) -> bool:
        if not f or len(f) < 3:
            return False
        if FILE_GARBAGE_RE.match(f):
            return False
        if all(c in '+- ' for c in f):
            return False  # Git diff artifact
        if f.count('/') == 0 and '.' not in f and len(f) > 20:
            return False  # Probably a diff line, not a file
        return True

    # Prioritize changed files
    valid_files = []
    seen = set()
    for f in list(files_changed):
        if is_valid_file(f) and f not in seen:
            seen.add(f)
            valid_files.append(f)
    for f in all_files:
        if is_valid_file(f) and f not in seen:
            seen.add(f)
            valid_files.append(f)

    if not valid_files:
        return ""
    return "\n".join(f"- `{f}`" for f in valid_files[:15])


def update_context_md(project_dir: Path, slug: str, cwd: Path,
                      transcript_context: dict, fs_stack: dict,
                      git_changes: dict = None,
                      llm_data: dict | None = None) -> None:
    """Create or update context.md with auto-generated content.

    On first creation: populate from transcript analysis + filesystem detection.
    On update: merge new learnings, preserving user-written content.
    Uses llm_data (if available) for richer context extraction.
    """
    context_md = project_dir / "context.md"
    git_changes = git_changes or {}

    # Build auto-content for each section
    # Stack: use filesystem detection only (transcript hints are too noisy)
    stack_items = fs_stack.get("stack", [])
    stack_str = " · ".join(stack_items) if stack_items else ""

    # what_this_is: ONLY update from transcript if it's a genuine project description.
    # Never overwrite with session summaries (e.g., "The session starts with...").
    # Priority: LLM session summary → existing context.md value → transcript pattern match.
    raw_what = transcript_context.get("what_this_is", "")
    llm_summary = llm_data.get("summary", "") if llm_data else ""
    # Only use transcript what_this_is if it looks like a real project description
    if raw_what and len(raw_what) > 20 and not any(
        raw_what.lower().startswith(p) for p in [
            "the session", "started with", "check", "fix", "update",
            "the context", "this project", "this skill"
        ]
    ):
        what_this_is = raw_what
    elif llm_summary and len(llm_summary) > 20 and not any(
        llm_summary.lower().startswith(p) for p in [
            "the session", "started with", "check", "fix", "update",
            "the context", "this project", "this skill"
        ]
    ):
        what_this_is = llm_summary.replace("\n", " ")[:200]
    else:
        # Keep existing value from context.md — don't overwrite with noise
        what_this_is = ""

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

    # Write session note
    facts    = extract_facts(transcript)
    llm_data = None
    if _HAS_LLM:
        # Ensure model is available (auto-download if missing) before calling LLM
        if ensure_model():
            llm_data = _llm_summary(messages)

    # Extract context from transcript for updating context.md
    transcript_context = extract_context_from_transcript(transcript, cwd)
    fs_stack = detect_project_stack(cwd)
    git_changes = extract_git_changes(cwd) if cwd.exists() else {}

    # Update context.md with session learnings (auto-sections only)
    update_context_md(project_dir, slug, cwd, transcript_context, fs_stack, git_changes, llm_data)

    note = build_session_note(slug, cwd, session_id, facts, llm_data)
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
    marker = session_marker(session_id, cwd)
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
