"""
context_builder.py — Mindmap builder for claude-recall.

Builds mindmap.json files that store structured project context:
- Project overview, tech stack, environment
- File-to-node mappings for relevance
- Session learnings and accumulated knowledge

Replaces the context.md approach with structured mindmap storage.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils import (
    detect_project_stack, DEBUG_LOG,
    merge_auto_section, llm_available, is_nvidia_nim,
)

from mindmap import (
    build_initial_mindmap_from_stack,
    save_mindmap,
    load_mindmap,
    upsert_node,
    mark_files_stale,
    get_relevant_nodes,
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

    full_text = "\n".join(lines[:50])

    strong_match = re.search(r'<strong>([^<]+)</strong>', full_text)
    if strong_match:
        candidate = strong_match.group(1).strip()
        if len(candidate) > 15 and "<" not in candidate:
            return candidate

    def strip_html(s: str) -> str:
        s = re.sub(r'<[^>]+>', ' ', s)
        s = s.replace('&nbsp;', ' ')
        s = s.replace('&amp;', '&')
        s = s.replace('&lt;', '<')
        s = s.replace('&gt;', '>')
        s = re.sub(r'&\w+;', ' ', s)
        return re.sub(r'\s+', ' ', s).strip()

    content_lines = []
    past_header = False
    in_html_block = False

    for line in lines[:50]:
        stripped = line.strip()

        if re.match(r'^<(p|div|table|details|summary)\b', stripped, re.I):
            in_html_block = True
        if re.match(r'^</(p|div|table|details|summary)>', stripped, re.I):
            in_html_block = False
            continue
        if in_html_block:
            continue

        if any(p in stripped for p in ["![", "<img", "<a ", "[![", "```", "<br"]):
            continue
        if stripped.startswith("---") or stripped.startswith("<"):
            continue
        if not stripped:
            if past_header and content_lines:
                break
            continue

        if stripped.startswith("# ") and not past_header:
            past_header = True
            continue

        if stripped.startswith("## "):
            if not content_lines:
                past_header = True
                continue
            break

        cleaned = strip_html(stripped)
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)
        cleaned = re.sub(r'[*_`]', '', cleaned)
        cleaned = cleaned.strip()

        if len(cleaned) > 10:
            content_lines.append(cleaned)
            past_header = True

    description = " ".join(content_lines).strip()
    if len(description) > 200:
        cut = description[:200].rfind(".")
        if cut > 80:
            description = description[:cut + 1]
        else:
            description = description[:200] + "..."
    return description


# ── Env file detection (keep existing) ────────────────────────────────────────

def detect_env_files(cwd: Path) -> dict[str, list[str]]:
    """Detect environment configuration files."""
    results = {"env_files": [], "config_files": []}

    for f in cwd.iterdir():
        if not f.is_file():
            continue
        name = f.name

        if name in (".env", ".env.local", ".env.development",
                   ".env.production", ".env.example", ".env.template"):
            results["env_files"].append(name)
        elif name in ("docker-compose.yml", "docker-compose.yaml",
                     "docker-compose.override.yml"):
            results["config_files"].append(name)
        elif name in (" Makefile", "pytest.ini", ".editorconfig",
                     "tsconfig.json", "jsconfig.json", "pyrightconfig.json"):
            results["config_files"].append(name)

    return results


# ── Mindmap builder (replaces build_compact_context) ────────────────────────

def build_initial_mindmap(cwd: Path, slug: str, project_dir: Path) -> dict:
    """Build initial mindmap from project filesystem.

    Args:
        cwd: Current working directory (project root)
        slug: Project slug
        project_dir: Path to save mindmap.json

    Returns:
        Assembled mindmap dict (caller should save it).
    """
    stack_info = detect_project_stack(cwd)
    readme_desc = read_readme_description(cwd)

    mindmap = build_initial_mindmap_from_stack(cwd, slug, stack_info)

    if readme_desc and "project_overview" in mindmap["nodes"]:
        existing = mindmap["nodes"]["project_overview"]
        existing["content"] = readme_desc
        existing["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        existing_keywords = set(existing.get("keywords", []))
        existing_keywords.update([slug.lower(), "project", "overview"])
        existing["keywords"] = list(existing_keywords)

    save_mindmap(project_dir, mindmap)
    _debug(f"Created initial mindmap.json for {slug}")

    return mindmap


# ── Mindmap update after session ─────────────────────────────────────────────

def update_mindmap_after_session(
    project_dir: Path,
    session_summary: dict,
    changed_files: list[str],
) -> None:
    """Update mindmap with learnings from a completed session.

    Args:
        project_dir: Path to project directory with mindmap.json
        session_summary: Dict with keys: summary, next_steps, keywords,
                        decisions, files_and_roles
        changed_files: List of file paths that were modified
    """
    mindmap = load_mindmap(project_dir)

    files_and_roles = session_summary.get("files_and_roles", {})
    for filepath, role in files_and_roles.items():
        parent_dir = str(Path(filepath).parent)
        if parent_dir == ".":
            parent_dir = "root"
        node_id = parent_dir.replace("/", "_").replace("\\", "_").replace(".", "_")

        content = f"{filepath}: {role}"
        upsert_node(
            mindmap,
            node_id=node_id,
            content=content,
            keywords=session_summary.get("keywords", []),
            parent="project_overview",
            files=[filepath],
        )

    decisions = session_summary.get("decisions", [])
    if decisions:
        existing = mindmap["nodes"].get("architecture", {})
        existing_content = existing.get("content", "")
        if existing_content:
            new_content = existing_content + " | " + " | ".join(decisions[:3])
        else:
            new_content = " | ".join(decisions[:3])
        upsert_node(
            mindmap,
            node_id="architecture",
            content=new_content,
            keywords=["architecture", "decisions"],
            parent="project_overview",
        )

    keywords = session_summary.get("keywords", [])
    if keywords:
        project_overview = mindmap["nodes"].get("project_overview", {})
        existing_kw = set(project_overview.get("keywords", []))
        existing_kw.update(k.lower() for k in keywords)
        if project_overview:
            project_overview["keywords"] = list(existing_kw)
            project_overview["last_updated"] = datetime.now().strftime("%Y-%m-%d")

    stale_ids = mark_files_stale(mindmap, changed_files)

    if stale_ids:
        project_context = _build_project_context(mindmap)
        for node_id in stale_ids:
            node_data = mindmap["nodes"].get(node_id, {})
            if node_data:
                updated_content = summarize_stale_node(node_id, node_data, project_context)
                if updated_content and updated_content != node_data.get("content"):
                    node_data["content"] = updated_content
                    node_data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
                    node_data["stale"] = False

    save_mindmap(project_dir, mindmap)
    _debug(f"Updated mindmap.json with session learnings")


def _build_project_context(mindmap: dict) -> str:
    """Build a brief project context string for LLM."""
    overview = mindmap["nodes"].get("project_overview", {})
    content = overview.get("content", "")[:200]

    stack = mindmap["nodes"].get("stack", {})
    stack_content = stack.get("content", "")[:100]

    return f"Project: {content} Stack: {stack_content}"


def summarize_stale_node(node_id: str, node_data: dict, project_context: str) -> str:
    """Re-summarize a stale node using Claude API (Anthropic or NVIDIA NIM).

    Args:
        node_id: ID of the node to summarize
        node_data: Current node data dict
        project_context: Brief project context string

    Returns:
        Updated content string, or original on failure.
        If no API available, returns node_data["content"] unchanged.
    """
    if not llm_available():
        return node_data.get("content", "")

    try:
        current_content = node_data.get("content", "")
        files_changed = node_data.get("files", [])

        system_prompt = "You are a developer context updater. Respond with only the updated description, no explanation."
        user_prompt = f"""Given this context about a project: {project_context}
Update this node's description in 1-2 sentences: {current_content}
Recent files changed: {files_changed}

Respond with only the updated description, no explanation."""

        if is_nvidia_nim():
            from openai import OpenAI
            client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=os.environ.get("NVIDIA_NIM_BASE_URL"),
            )
            response = client.chat.completions.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=100,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            updated = response.choices[0].message.content.strip()
        else:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                temperature=0,
                system=[{"type": "text", "text": system_prompt}],
                messages=[{"type": "user", "text": user_prompt}],
            )
            updated = response.content[0].text.strip()

        if len(updated) > 5 and "given this context" not in updated.lower():
            return updated

        return node_data.get("content", "")

    except Exception as e:
        _debug(f"summarize_stale_node error: {e}")
        return node_data.get("content", "")


# ── Context check (updated for mindmap) ─────────────────────────────────────

def is_context_empty_or_missing(project_dir: Path) -> bool:
    """Check if mindmap.json needs to be (re)generated."""
    mindmap_path = project_dir / "mindmap.json"
    if not mindmap_path.exists():
        return True
    try:
        data = json.loads(mindmap_path.read_text())
        return len(data.get("nodes", {})) == 0
    except Exception:
        return True


# ── Legacy context functions (kept for compatibility) ────────────────────────

def build_compact_context(cwd: Path, slug: str) -> str:
    """Legacy function — returns empty string (use build_initial_mindmap instead)."""
    return ""


def update_context_after_session(
    project_dir: Path,
    slug: str,
    cwd: Path,
    current_state: str,
    decisions: list[str] | None = None,
    gotchas: list[str] | None = None,
    key_files_update: list[str] | None = None,
    session_summary: str | None = None,
    all_prompts: list[str] | None = None,
) -> None:
    """Legacy function — no-op (use update_mindmap_after_session instead)."""
    pass