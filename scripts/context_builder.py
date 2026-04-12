"""
context_builder.py — Compact, high-signal context generation for claude-recall.

Builds context.md files that are:
- Small (< 60 lines, < 1500 tokens)
- High-signal (every line helps Claude)
- Accurate (uses README + filesystem detection, not LLM hallucination)

Replaces the bloated auto_generate_context_md() and build_context_md()
functions that produced generic, token-wasting context.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    detect_project_stack, get_model_path, DEBUG_LOG,
    merge_auto_section,
)


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] BUILDER: {msg}\n")
    except Exception:
        pass


# ── README parsing ────────────────────────────────────────────────────────────

def read_readme_description(cwd: Path) -> str:
    """Extract project description from README.md.

    Strategy: Read first heading + first paragraph. This is almost always
    the best description of what a project does.
    """
    for name in ["README.md", "readme.md", "README.rst", "README"]:
        readme = cwd / name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8", errors="ignore")
                return _extract_description(text)
            except Exception:
                pass
    return ""


def _extract_description(text: str) -> str:
    """Extract meaningful description from README text."""
    lines = text.strip().splitlines()
    if not lines:
        return ""

    # First pass: try to extract from HTML <strong> or <h1> tags
    full_text = "\n".join(lines[:50])

    # Look for <strong>text</strong> in opening HTML (common in centered READMEs)
    strong_match = re.search(r'<strong>([^<]+)</strong>', full_text)
    if strong_match:
        candidate = strong_match.group(1).strip()
        if len(candidate) > 15 and "<" not in candidate:
            return candidate

    # Strip ALL HTML tags and entities for plain text extraction
    def strip_html(s: str) -> str:
        s = re.sub(r'<[^>]+>', ' ', s)  # tags → space
        s = s.replace('&nbsp;', ' ')
        s = s.replace('&amp;', '&')
        s = s.replace('&lt;', '<')
        s = s.replace('&gt;', '>')
        s = re.sub(r'&\w+;', ' ', s)  # other entities
        return re.sub(r'\s+', ' ', s).strip()

    # Second pass: find first meaningful paragraph after heading
    content_lines = []
    past_header = False
    in_html_block = False

    for line in lines[:50]:
        stripped = line.strip()

        # Track HTML blocks (skip entire <p>...</p>, <div>...</div> blocks)
        if re.match(r'^<(p|div|table|details|summary)\b', stripped, re.I):
            in_html_block = True
        if re.match(r'^</(p|div|table|details|summary)>', stripped, re.I):
            in_html_block = False
            continue
        if in_html_block:
            continue

        # Skip badges, images, HTML lines
        if any(p in stripped for p in ["![", "<img", "<a ", "[![", "```", "<br"]):
            continue
        if stripped.startswith("---") or stripped.startswith("<"):
            continue
        if not stripped:
            if past_header and content_lines:
                break
            continue

        # Skip the main title (# heading)
        if stripped.startswith("# ") and not past_header:
            past_header = True
            continue

        # Skip ## sub-headings used as section markers
        if stripped.startswith("## "):
            if not content_lines:
                past_header = True
                continue
            break

        # This is actual content
        cleaned = strip_html(stripped)
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)  # links
        cleaned = re.sub(r'[*_`]', '', cleaned)  # emphasis
        cleaned = cleaned.strip()

        if len(cleaned) > 10:
            content_lines.append(cleaned)
            past_header = True

    description = " ".join(content_lines).strip()
    # Truncate to ~200 chars at sentence boundary
    if len(description) > 200:
        cut = description[:200].rfind(".")
        if cut > 80:
            description = description[:cut + 1]
        else:
            description = description[:200] + "..."
    return description


# ── LLM context generation ───────────────────────────────────────────────────

_LLM_SYSTEM = "You are a developer. Respond ONLY with valid JSON."

_LLM_PROMPT = """Project: {slug}
README says: {readme}
Stack detected: {stack}
Top dirs: {dirs}

Respond with this JSON only:
{{"description": "one sentence: what this project does", "entry_point": "command to run this"}}"""


def _llm_describe_project(cwd: Path, slug: str, readme: str, stack: list, dirs: list) -> dict | None:
    """Ask the Qwen 0.5B model for a project description.

    Ultra-simple 2-field prompt — this model can't handle complex prompts.
    The filesystem detection + README do 90% of the work.
    """
    if not get_model_path().exists():
        return None

    try:
        from llama_cpp import Llama
    except ImportError:
        return None

    try:
        from utils import get_llm
        llm = get_llm()
        if llm is None:
            return None

        prompt = _LLM_PROMPT.format(
            slug=slug,
            readme=readme[:300] if readme else "(no README)",
            stack=", ".join(stack) if stack else "unknown",
            dirs=", ".join(dirs[:8]),
        )

        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=128,
            temperature=0.05,
        )

        # Handle different response formats from llama-cpp-python
        choice = response["choices"][0]
        msg = choice.get("message", choice)
        if isinstance(msg, str):
            raw = msg.strip()
        elif isinstance(msg, dict):
            raw = (msg.get("content") or "").strip()
        else:
            return None

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw.strip())
        # Validate
        if not isinstance(result.get("description"), str):
            return None
        # Reject if it echoes the prompt
        if "one sentence" in result["description"].lower():
            return None

        return result

    except Exception as e:
        _debug(f"LLM describe failed: {e}")
        return None


# ── Compact context builder ──────────────────────────────────────────────────

def build_compact_context(cwd: Path, slug: str) -> str:
    """Build a compact, high-signal context.md for a project.

    Target: < 60 lines, < 1500 tokens. Every line must be useful to Claude.

    Priority order for project description:
    1. LLM analysis of README + filesystem
    2. First paragraph of README.md
    3. Filesystem detection fallback
    """
    fs = detect_project_stack(cwd)
    readme_desc = read_readme_description(cwd)
    stack = fs.get("stack", [])

    # Top-level directories
    try:
        dirs = [e.name for e in sorted(cwd.iterdir())
                if e.is_dir() and not e.name.startswith(".")][:10]
    except Exception:
        dirs = []

    # Try LLM for description
    llm_ctx = _llm_describe_project(cwd, slug, readme_desc, stack, dirs)

    # Build description — priority: README > LLM > filesystem
    if readme_desc:
        description = readme_desc
    elif llm_ctx and llm_ctx.get("description"):
        description = llm_ctx["description"]
    elif fs.get("name"):
        type_labels = {
            "node": "Node.js project", "python": "Python project",
            "rust": "Rust project", "go": "Go project",
            "flutter": "Flutter project", "claude-skill": "Claude Code Skill",
        }
        label = type_labels.get(fs.get("type", ""), "project")
        description = f"{fs['name']} — {label}"
    else:
        description = slug

    # Stack string
    stack_str = " · ".join(stack) if stack else "Not detected"

    # Entry point
    entry_point = ""
    if llm_ctx and llm_ctx.get("entry_point"):
        entry_point = llm_ctx["entry_point"]
    elif fs.get("scripts"):
        if "dev" in fs["scripts"]:
            entry_point = "npm run dev"
        elif "start" in fs["scripts"]:
            entry_point = "npm start"
    elif fs.get("type") == "python":
        entry_point = "python3 main.py"
    elif fs.get("type") == "flutter":
        entry_point = "flutter run"
    elif fs.get("type") == "rust":
        entry_point = "cargo run"
    elif fs.get("type") == "go":
        entry_point = "go run ."

    # Key files — compact list with purpose
    key_files = _build_key_files(cwd, fs)

    # Environment
    env_parts = []
    if fs.get("git_branch"):
        env_parts.append(f"Git: {fs['git_branch']}")
    if fs.get("recent_commits"):
        env_parts.append(f"Last commit: {fs['recent_commits'][0]}")
    if fs.get("env_keys"):
        env_parts.append(f"Env vars: {', '.join(fs['env_keys'][:8])}")
    env_str = " | ".join(env_parts) if env_parts else "Not detected"

    # Build compact context
    parts = [
        f"---",
        f"project: {slug}",
        f"directory: {cwd}",
        f"created: {datetime.now().strftime('%Y-%m-%d')}",
        f"tags: [claude-recall, context]",
        f"---",
        f"",
        f"# {slug}",
        f"",
        f"<!-- auto:what_this_is:start -->",
        f"{description}",
        f"<!-- auto:what_this_is:end -->",
        f"",
        f"## Stack",
        f"<!-- auto:stack:start -->",
        f"{stack_str}",
        f"<!-- auto:stack:end -->",
    ]

    # Entry point (only if detected)
    if entry_point:
        parts.extend([
            f"",
            f"## Run",
            f"<!-- auto:entry_point:start -->",
            f"`{entry_point}`",
            f"<!-- auto:entry_point:end -->",
        ])

    # Key files
    if key_files:
        parts.extend([
            f"",
            f"## Key Files",
            f"<!-- auto:key_files:start -->",
            key_files,
            f"<!-- auto:key_files:end -->",
        ])

    # Current state + Architecture + Gotchas (empty on first create)
    parts.extend([
        f"",
        f"## Current State",
        f"<!-- auto:current_state:start -->",
        f"First session — no history yet",
        f"<!-- auto:current_state:end -->",
        f"",
        f"## Decisions",
        f"<!-- auto:decisions:start -->",
        f"<!-- auto:decisions:end -->",
        f"",
        f"## Gotchas",
        f"<!-- auto:gotchas:start -->",
        f"<!-- auto:gotchas:end -->",
        f"",
        f"## Environment",
        f"<!-- auto:environment:start -->",
        f"{env_str}",
        f"<!-- auto:environment:end -->",
        f"",
    ])

    return "\n".join(parts)


def _build_key_files(cwd: Path, fs: dict) -> str:
    """Build a compact key files section based on filesystem detection."""
    files = []

    # Common important files and their purposes
    file_purposes = {
        "package.json": "Node.js dependencies and scripts",
        "tsconfig.json": "TypeScript configuration",
        "pubspec.yaml": "Flutter/Dart dependencies",
        "Cargo.toml": "Rust dependencies",
        "go.mod": "Go module definition",
        "requirements.txt": "Python dependencies",
        "pyproject.toml": "Python project configuration",
        "Dockerfile": "Container build definition",
        "docker-compose.yml": "Multi-container orchestration",
        "docker-compose.yaml": "Multi-container orchestration",
        "Makefile": "Build automation",
        "SKILL.md": "Claude Code skill definition",
    }

    for config_file in fs.get("config_files", []):
        purpose = file_purposes.get(config_file, "configuration")
        files.append(f"- `{config_file}` — {purpose}")

    # Add entry points based on project type
    if fs.get("type") == "node":
        for f in ["src/index.ts", "src/app.ts", "pages/index.tsx",
                   "app/page.tsx", "src/main.ts", "index.js"]:
            if (cwd / f).exists():
                files.append(f"- `{f}` — entry point")
                break
    elif fs.get("type") == "python":
        for f in ["main.py", "app.py", "manage.py", "src/main.py"]:
            if (cwd / f).exists():
                files.append(f"- `{f}` — entry point")
                break

    return "\n".join(files[:10]) if files else ""


# ── Context update (post-session) ────────────────────────────────────────────

def update_context_after_session(
    project_dir: Path,
    slug: str,
    cwd: Path,
    current_state: str,
    decisions: list[str] | None = None,
    gotchas: list[str] | None = None,
    key_files_update: list[str] | None = None,
) -> None:
    """Update context.md with learnings from a completed session.

    Only updates auto-marker sections. User content outside markers is preserved.
    """
    context_md = project_dir / "context.md"
    if not context_md.exists():
        # Generate fresh if missing
        content = build_compact_context(cwd, slug)
        context_md.write_text(content, encoding="utf-8")
        _debug(f"Created fresh context.md for {slug}")
        return

    existing = context_md.read_text(encoding="utf-8")
    updated = existing

    # Update current state
    if current_state:
        updated = merge_auto_section(updated, "current_state", current_state)

    # Update stack from filesystem (always fresh)
    fs = detect_project_stack(cwd)
    stack = fs.get("stack", [])
    if stack:
        updated = merge_auto_section(updated, "stack", " · ".join(stack))

    # Merge decisions (accumulate, don't replace)
    if decisions:
        existing_decisions = _extract_auto_section(updated, "decisions")
        all_decisions = _merge_list_items(existing_decisions, decisions)
        if all_decisions:
            updated = merge_auto_section(updated, "decisions", all_decisions)

    # Merge gotchas (accumulate, don't replace)
    if gotchas:
        existing_gotchas = _extract_auto_section(updated, "gotchas")
        all_gotchas = _merge_list_items(existing_gotchas, gotchas)
        if all_gotchas:
            updated = merge_auto_section(updated, "gotchas", all_gotchas)

    # Update key files if provided
    if key_files_update:
        files_str = "\n".join(f"- `{f}`" for f in key_files_update[:10])
        updated = merge_auto_section(updated, "key_files", files_str)

    # Update environment
    env_parts = []
    if fs.get("git_branch"):
        env_parts.append(f"Git: {fs['git_branch']}")
    if fs.get("recent_commits"):
        env_parts.append(f"Last commit: {fs['recent_commits'][0]}")
    if env_parts:
        updated = merge_auto_section(updated, "environment", " | ".join(env_parts))

    if updated != existing:
        context_md.write_text(updated, encoding="utf-8")
        _debug("context.md updated with session learnings")


def _extract_auto_section(text: str, section_name: str) -> str:
    """Extract content between auto markers for a section."""
    pattern = re.compile(
        r"<!-- auto:" + re.escape(section_name) + r":start -->\s*\n"
        r"(.*?)\n\s*<!-- auto:" + re.escape(section_name) + r":end -->",
        re.DOTALL
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _merge_list_items(existing: str, new_items: list[str]) -> str:
    """Merge new bullet items into existing list, avoiding duplicates."""
    # Parse existing items
    existing_items = []
    for line in existing.splitlines():
        line = line.strip()
        if line.startswith("- "):
            existing_items.append(line[2:].strip())

    # Add new items (deduplicate by lowercased text)
    seen = {item.lower() for item in existing_items}
    for item in new_items:
        if item.lower() not in seen:
            existing_items.append(item)
            seen.add(item.lower())

    # Keep max 8 items (most recent win)
    items = existing_items[-8:]
    return "\n".join(f"- {item}" for item in items) if items else ""


def is_context_empty_or_missing(project_dir: Path) -> bool:
    """Check if context.md needs to be (re)generated."""
    context_md = project_dir / "context.md"
    if not context_md.exists():
        return True

    text = context_md.read_text(encoding="utf-8")

    # Strip frontmatter + headers + auto markers + HTML comments
    body = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL).strip()
    body = re.sub(r"^#.*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"##\s+.*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = body.strip()

    # If less than 20 chars of actual content, it's empty
    return len(body) < 20
