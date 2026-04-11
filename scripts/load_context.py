#!/usr/bin/env python3
"""
load_context.py — claude-recall UserPromptSubmit hook.

Fires before every user prompt. Reads project context from the Obsidian vault
and prints it to stdout — Claude Code prepends this to Claude's system context.

KEY BEHAVIOR:
- Session deduplication: only loads context on the FIRST prompt of a session
  (subsequent prompts in the same session are skipped via marker file)
- If context.md doesn't exist or is an empty scaffold: auto-generates it
  using the LLM (Qwen GGUF) for rich, accurate context
- Claude gets full project context from the very first message

Storage layout in Obsidian:
  <vault>/claude-recall/projects/<slug>/context.md      ← auto-generated + user can edit
  <vault>/claude-recall/projects/<slug>/sessions/*.md   ← auto-written by save_context.py

Never exits non-zero — a failed hook would block Claude from starting.
"""

import json
import sys
import traceback
import os
from datetime import datetime
from pathlib import Path

# CRITICAL: must set path before importing from utils
sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    load_config, get_vault_root, get_project_dir, read_hook_input, get_cwd,
    cwd_to_slug, truncate_to_tokens, session_marker, cleanup_stale_markers,
    is_scaffold_only, auto_generate_context_md, detect_project_stack,
    ensure_model, llm_available, get_model_path, generate_file_tree,
)

DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] LOAD: {msg}\n")
    except Exception:
        pass

# ── LLM context generation (used when no context.md exists) ───────────────────

_CONTEXT_SYSTEM = (
    "You are a senior developer summarising a project. "
    "Respond ONLY with valid JSON. No markdown, no explanation."
)

_CONTEXT_USER_TEMPLATE = """Analyze this project and produce a JSON summary.

Project directory: {cwd}
Top-level directories: {dirs}
Config files detected: {config_files}
README excerpt: {readme}

Output exactly this JSON — fill every field:
{{
  "what_this_is": "one sentence describing what this project does",
  "stack": ["tech1", "tech2", "tech3"],
  "key_directories": ["dir1/", "dir2/", "dir3/"],
  "entry_point": "main entry point file or command",
  "description": "2-3 sentence overview of the project"
}}

Rules:
- be accurate and concise
- stack: list the main technologies/frameworks/libraries (max 8)
- key_directories: max 5 most important directories
- entry_point: the main file or command to run the project
- If README is empty, infer from directory structure and file names
"""


def _get_readme_content(cwd: Path) -> str:
    """Extract plain text from README.md (first meaningful lines)."""
    for name in ("README.md", "readme.md", "README", "readme"):
        readme = cwd / name
        if readme.exists():
            try:
                lines = readme.read_text(encoding="utf-8").splitlines()[:40]
                out = []
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("```"):
                        break
                    if stripped and not stripped.startswith("#"):
                        out.append(stripped)
                    elif stripped.startswith("#"):
                        out.append(stripped)
                content = " ".join(out)
                return content[:600]
            except Exception:
                pass
    return ""


def _generate_context_with_llm(cwd: Path, slug: str) -> str | None:
    """
    Generate rich context.md content using the LLM.
    Returns the full context.md text, or None if LLM fails.
    """
    if not ensure_model(silent=True) and not llm_available():
        debug_log("LLM not available for context generation")
        return None

    try:
        from llama_cpp import Llama

        fs = detect_project_stack(cwd)
        dirs = [p.name for p in cwd.iterdir()
                if p.is_dir() and not p.name.startswith(".")][:10]
        dirs_str = ", ".join(dirs)
        readme = _get_readme_content(cwd)
        config_str = ", ".join(fs.get("config_files", [])) or "none"

        user_msg = _CONTEXT_USER_TEMPLATE.format(
            cwd=str(cwd),
            dirs=dirs_str,
            config_files=config_str,
            readme=readme or "(none)",
        )

        debug_log("Loading LLM for context generation...")
        llm = Llama(
            model_path=str(get_model_path()),
            n_ctx=8192,
            n_threads=4,
            n_gpu_layers=0,
            verbose=False,
        )

        debug_log("Calling LLM...")
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _CONTEXT_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=512,
            temperature=0.1,
        )

        raw = response["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw.strip())
        required = {"what_this_is", "stack", "key_directories", "entry_point", "description"}
        if not required.issubset(result.keys()):
            debug_log("LLM response missing required keys")
            return None

        stack_str = " · ".join(result.get("stack", []))
        if not stack_str:
            stack_str = " · ".join(fs.get("stack", [])) or "Not detected"

        what_this_is = result.get("what_this_is", "")
        if not what_this_is:
            what_this_is = f"Project: {fs.get('name', slug)}"
            if fs.get("type") and fs["type"] != "unknown":
                labels = {
                    "node": "Node.js/JavaScript project",
                    "python": "Python project",
                    "rust": "Rust project",
                    "go": "Go project",
                    "flutter": "Flutter/Dart project",
                    "claude-skill": "Claude Code Skill",
                }
                what_this_is = f"{what_this_is} — {labels.get(fs['type'], fs['type'])}"

        description = result.get("description", "")
        key_dirs = result.get("key_directories", []) or fs.get("structure", [])[:5]
        structure_str = ", ".join(key_dirs)
        entry_point = result.get("entry_point", "")
        config_files_str = config_str

        env_parts = []
        if fs.get("env_keys"):
            env_parts.append("Env vars: " + ", ".join(fs["env_keys"][:10]))
        if fs.get("git_branch"):
            env_parts.append(f"Git branch: {fs['git_branch']}")
        if fs.get("recent_commits"):
            env_parts.append("Recent commits:")
            for c in fs["recent_commits"][:5]:
                env_parts.append(f"  - {c}")
        environment_str = "\n".join(env_parts) if env_parts else "Not detected"

        tree = generate_file_tree(cwd, max_depth=2, max_files=40)

        return f"""\
---
project: {slug}
directory: {cwd}
created: {datetime.now().strftime('%Y-%m-%d')}
updated: {datetime.now().strftime('%Y-%m-%d')}
tags: [claude-recall, context]
---

# {slug}

{description}

## What this is
<!-- auto:what_this_is:start -->
{what_this_is}
<!-- auto:what_this_is:end -->

## Stack
<!-- auto:stack:start -->
{stack_str}
<!-- auto:stack:end -->

## Project Structure
<!-- auto:structure:start -->
Top-level: {structure_str}
Config: {config_files_str}
Entry point: {entry_point}

```
{tree}
```
<!-- auto:structure:end -->

## Current state
<!-- auto:current_state:start -->
First session — no history yet
<!-- auto:current_state:end -->

## Architecture decisions
<!-- auto:architecture:start -->
<!-- auto:architecture:end -->

## Gotchas
<!-- auto:gotchas:start -->
<!-- auto:gotchas:end -->

## Environment
<!-- auto:environment:start -->
{environment_str}
<!-- auto:environment:end -->
"""

    except Exception as exc:
        debug_log(f"_generate_context_with_llm failed: {exc}")
        print(f"[claude-recall] LLM context generation failed: {exc}", file=sys.stderr)
        return None


def load_context() -> None:
    debug_log("=== LOAD SESSION STARTED ===")
    debug_log(f"CWD: {os.getcwd()}")

    try:
        hook_input = read_hook_input()
        session_id = hook_input.get("session_id", "unknown")
        cwd        = get_cwd(hook_input)
        cfg        = load_config()
        
        debug_log(f"session_id={session_id}, cwd={cwd}")

        cleanup_stale_markers()

        # Session-start deduplication — only load context on first prompt
        marker = session_marker(session_id, cwd)
        if not cfg.get("load_on_every_prompt", False) and marker.exists():
            debug_log("Skipping - marker exists (session already loaded)")
            return
        marker.touch()

        # Resolve project in vault
        slug        = cwd_to_slug(cwd)
        project_dir = get_project_dir(cfg, slug)
        context_md  = project_dir / "context.md"
        
        debug_log(f"slug={slug}, project_dir={project_dir}, context exists={context_md.exists()}")

        # AUTO-GENERATE context.md if it doesn't exist or is empty scaffold
        needs_generation = False
        if not context_md.exists():
            needs_generation = True
            debug_log("context.md does not exist — will auto-generate")
        elif is_scaffold_only(context_md.read_text(encoding="utf-8")):
            needs_generation = True
            debug_log("context.md is empty scaffold — will auto-generate")
        
        if needs_generation:
            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                print(
                    f"[claude-recall] Generating context for '{slug}' with LLM...",
                    file=sys.stderr,
                )
                # Try LLM-powered generation first
                content = _generate_context_with_llm(cwd, slug)
                if content:
                    context_md.write_text(content, encoding="utf-8")
                    debug_log(f"Auto-generated context.md via LLM ({len(content)} chars)")
                    print(
                        f"[claude-recall] Context generated and saved.",
                        file=sys.stderr,
                    )
                else:
                    # Fallback to filesystem-only
                    content = auto_generate_context_md(cwd, slug)
                    context_md.write_text(content, encoding="utf-8")
                    debug_log(f"Auto-generated context.md via filesystem ({len(content)} chars)")
                    print(
                        f"[claude-recall] Context generated from project files.",
                        file=sys.stderr,
                    )
            except Exception as exc:
                debug_log(f"Auto-generate failed: {exc}")
                # Fallback: try filesystem generation
                try:
                    content = auto_generate_context_md(cwd, slug)
                    context_md.write_text(content, encoding="utf-8")
                    print(
                        f"[claude-recall] Context generated (fallback).",
                        file=sys.stderr,
                    )
                except Exception as exc2:
                    debug_log(f"Fallback also failed: {exc2}")
                    print(f"[claude-recall] Auto-generate error: {exc}", file=sys.stderr)

        # Build context output for Claude
        parts: list[str] = []
        max_ctx = cfg.get("max_context_tokens", 2000)

        # Load context.md (800 tokens)
        if context_md.exists():
            text = context_md.read_text(encoding="utf-8").strip()
            if text and not is_scaffold_only(text):
                text = truncate_to_tokens(text, int(max_ctx * 0.4))
                parts.append(f"## Project context\n\n{text}")

        # Load recent session notes (400 tokens each)
        sessions_dir = project_dir / "sessions"
        n = cfg.get("include_recent_sessions", 2)
        session_count = 0
        if sessions_dir.exists() and n > 0:
            try:
                all_sessions = sorted(sessions_dir.glob("*.md"), reverse=True)
                session_count = len(all_sessions)
                for note in all_sessions[:n]:
                    t = note.read_text(encoding="utf-8").strip()
                    if t:
                        t = truncate_to_tokens(t, int(max_ctx * 0.2))
                        parts.append(f"## Previous session — {note.stem}\n\n{t}")
            except Exception as exc:
                debug_log(f"Session read error: {exc}")

        # file-index.json — per-file summaries (written by scan_project.py)
        file_index_path = project_dir / "file-index.json"
        if file_index_path.exists():
            try:
                raw_index = json.loads(
                    file_index_path.read_text(encoding="utf-8")
                )
                raw_index.pop("_cache_mtimes", None)

                if raw_index:
                    lines = []
                    for rel_path, info in list(raw_index.items())[:15]:
                        if isinstance(info, dict) and info.get("purpose"):
                            lines.append(f"- `{rel_path}` — {info['purpose']}")
                    if lines:
                        parts.append(
                            "## Key files in this project\n\n" + "\n".join(lines)
                        )
            except Exception as exc:
                print(f"[claude-recall] file-index read error: {exc}", file=sys.stderr)

        if not parts:
            debug_log("No context found even after auto-generation")
            return

        body = "\n\n---\n\n".join(parts)
        body = truncate_to_tokens(body, max_ctx)

        # Build header
        header_parts = [f"Project: `{slug}`", f"Dir: `{cwd}`"]
        if session_count > 0:
            header_parts.append(f"Sessions: {session_count}")
        header_parts.append(f"Loaded: {datetime.now().strftime('%H:%M')}")

        debug_log(f"Returning {len(body)} chars of context")
        
        print(
            "<!-- claude-recall: context loaded from Obsidian -->\n"
            f"{' | '.join(header_parts)}\n\n"
            + body
            + "\n\n> **claude-recall**: Run `python3 ~/.claude/skills/claude-recall/scripts/recall_update.py update` to refresh context\n"
        )
        
    except Exception as exc:
        debug_log(f"ERROR: {exc}\n{traceback.format_exc()}")
        print(f"[claude-recall] load error: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    load_context()