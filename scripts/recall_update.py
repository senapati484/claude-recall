#!/usr/bin/env python3
"""
recall_update.py — /recall command for claude-recall.

Usage:
    python3 ~/.claude/skills/claude-recall/scripts/recall_update.py <action> [cwd]

Actions:
    update   Scan project and update context.md with LLM-generated content
    status   Show current context.md content
    reset    Delete context.md and regenerate from scratch

When action is "update" or "reset", this script:
1. Ensures the LLM model is available (auto-downloads if missing)
2. Scans the project directory (README, package.json, etc.)
3. Asks the LLM to generate a rich project summary
4. Writes context.md with proper auto-markers

The LLM call uses the Qwen2.5 0.5B GGUF model via llama-cpp-python.
If the model is unavailable and auto-download fails, falls back to filesystem-only context.
"""

import json
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, detect_project_stack, generate_file_tree,
    ensure_model, llm_available, get_model_path,
)


# ── LLM context generation prompt ──────────────────────────────────────────────

_CONTEXT_SYSTEM = (
    "You are a senior developer summarising a project. "
    "Respond ONLY with valid JSON. No markdown, no explanation."
)

_CONTEXT_USER_TEMPLATE = """Analyze this project and produce a JSON summary.

Project directory: {cwd}
Top-level entries: {dirs}
README excerpt: {readme}
Config files (CRITICAL — use these to detect the real stack): {config_files}
Git commits: {git_commits}

Output exactly this JSON — fill every field:
{{
  "what_this_is": "one sentence describing what this project does",
  "stack": ["tech1", "tech2", "tech3"],
  "key_directories": ["dir1/", "dir2/", "dir3/"],
  "entry_point": "main entry point file or command",
  "description": "2-3 sentence overview of the project"
}}

Rules:
- Use config_files to determine the REAL stack — do NOT guess from directory names
- For Flutter/Dart projects: stack must include "Flutter" and "Dart", NOT just "Python"
- For Python projects: look for requirements.txt, setup.py, pyproject.toml
- For Node projects: look for package.json dependencies
- stack: list the actual technologies from config files (max 8)
- key_directories: max 5 most important directories
- entry_point: the main file or command to run the project
- description: what this specific project does (not a generic template description)
- If README is a generic template (e.g. "This project is a starting point"), infer from config files
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_readme_content(cwd: Path) -> str:
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


def generate_llm_context(cwd: Path) -> dict | None:
    """
    Ask the LLM to generate rich project context.
    Returns a dict with keys: what_this_is, stack, key_directories,
    entry_point, description — or None on failure.
    """
    # Ensure model is available (auto-download if missing)
    if not ensure_model(silent=True) and not llm_available():
        return None

    try:
        from llama_cpp import Llama

        # Get filesystem info for the prompt
        fs = detect_project_stack(cwd)
        dirs = [p.name for p in cwd.iterdir() if p.is_dir() and not p.name.startswith(".")][:10]
        dirs_str = ", ".join(dirs)
        readme = get_readme_content(cwd)
        config_str = ", ".join(fs.get("config_files", [])) or "none"
        git_commits = ""
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=str(cwd), capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                git_commits = result.stdout.strip()
        except Exception:
            pass

        user_msg = _CONTEXT_USER_TEMPLATE.format(
            cwd=str(cwd),
            dirs=dirs_str,
            readme=readme or "(none)",
            config_files=config_str,
            git_commits=git_commits or "(none)",
        )

        llm = Llama(
            model_path=str(get_model_path()),
            n_ctx=8192,
            n_threads=4,
            n_gpu_layers=0,
            verbose=False,
        )

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
        if required.issubset(result.keys()):
            return result
        return None

    except Exception as e:
        print(f"[claude-recall] LLM context generation failed: {e}", file=sys.stderr)
        return None


def build_context_md(cwd: Path, slug: str, llm_ctx: dict | None, fs: dict) -> str:
    """
    Build a complete context.md with auto-markers.
    Uses LLM context if available, otherwise filesystem-only scaffold.
    """
    # Detect from filesystem as fallback
    stack_str = llm_ctx["stack"] if llm_ctx else fs.get("stack", [])
    if isinstance(stack_str, list):
        stack_str = " · ".join(stack_str)
    if not stack_str:
        stack_str = " · ".join(fs.get("stack", [])) if fs.get("stack") else "Not detected"

    what_this_is = llm_ctx.get("what_this_is", "") if llm_ctx else ""
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

    description = llm_ctx.get("description", "") if llm_ctx else ""

    key_dirs = llm_ctx.get("key_directories", []) if llm_ctx else fs.get("structure", [])[:5]
    structure_str = ", ".join(key_dirs) if key_dirs else ", ".join(fs.get("structure", [])[:10])

    entry_point = llm_ctx.get("entry_point", "") if llm_ctx else ""
    config_str = ", ".join(fs.get("config_files", [])) if fs.get("config_files") else ""

    tree = generate_file_tree(cwd, max_depth=2, max_files=40)

    # Environment
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
Config: {config_str}
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


# ── Actions ──────────────────────────────────────────────────────────────────

def action_update(cwd: Path, cfg: dict) -> None:
    """Scan project and update context.md with LLM-generated content."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    context_md = project_dir / "context.md"

    print(f"[claude-recall] Updating context for: {slug}")
    print(f"  Directory: {cwd}")

    # Scan filesystem
    fs = detect_project_stack(cwd)
    print(f"  Detected stack: {' · '.join(fs.get('stack', [])) or 'none'}")

    # Generate LLM context
    llm_ctx = generate_llm_context(cwd)
    if llm_ctx:
        print(f"  LLM context: {llm_ctx.get('what_this_is', '')[:80]}")
    else:
        print(f"  LLM unavailable — using filesystem-only context")

    # Build and write context.md
    content = build_context_md(cwd, slug, llm_ctx, fs)
    context_md.write_text(content, encoding="utf-8")
    print(f"  ✓ Updated: {context_md}")


def action_status(cwd: Path, cfg: dict) -> None:
    """Print current context.md content."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"

    print(f"## claude-recall: {slug}")
    print(f"Directory: `{cwd}`")
    print(f"Vault:     {get_vault_root(cfg)}")

    sessions_dir = project_dir / "sessions"
    session_count = len(list(sessions_dir.glob("*.md"))) if sessions_dir.exists() else 0
    print(f"Sessions:  {session_count}")

    if context_md.exists():
        print()
        print(context_md.read_text())
    else:
        print("\nNo context.md yet. Run `/recall update` to generate it.")


def action_reset(cwd: Path, cfg: dict) -> None:
    """Delete existing context.md and regenerate from scratch."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"

    if context_md.exists():
        backup = context_md.with_suffix(".md.bak")
        context_md.rename(backup)
        print(f"[claude-recall] Reset: backed up old context.md → {backup}")
    else:
        print(f"[claude-recall] Reset: no existing context.md, generating fresh")

    action_update(cwd, cfg)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    action = "update"
    cwd = Path(os.getcwd())

    args = sys.argv[1:]
    if args:
        action = args[0].strip("-/")
    if len(args) > 1:
        cwd = Path(args[1])

    cfg = load_config()

    if action in ("update", "u"):
        action_update(cwd, cfg)
    elif action in ("status", "s"):
        action_status(cwd, cfg)
    elif action in ("reset", "r"):
        action_reset(cwd, cfg)
    else:
        print(f"Unknown action: {action}")
        print("Usage: recall_update.py [update|status|reset] [cwd]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[claude-recall] Error: {e}", file=sys.stderr)
        sys.exit(1)
