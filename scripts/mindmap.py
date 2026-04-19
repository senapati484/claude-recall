"""
mindmap.py — Mindmap storage and retrieval for claude-recall.

Manages mindmap.json stored at <vault>/claude-recall/projects/<slug>/mindmap.json

This module provides structured memory of project context that persists
across Claude Code sessions. The mindmap captures project structure,
tech stack, and accumulated session knowledge.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path


STOPWORDS = {
    "the", "a", "an", "is", "it", "in", "on", "at", "to", "for", "of", "and",
    "or", "but", "with", "this", "that", "what", "how", "can", "i", "my", "me",
}


def load_mindmap(project_dir: Path) -> dict:
    """Load mindmap from project directory.

    Args:
        project_dir: Path to project directory containing mindmap.json

    Returns:
        Parsed mindmap dict, or empty skeleton if file doesn't exist.
        Skeleton: {"_meta": {"version": 2}, "nodes": {}, "file_node_map": {}, "sessions": []}
    """
    mindmap_path = project_dir / "mindmap.json"

    if not mindmap_path.exists():
        return {
            "_meta": {"version": 2},
            "nodes": {},
            "file_node_map": {},
            "sessions": [],
        }

    try:
        with open(mindmap_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {
            "_meta": {"version": 2},
            "nodes": {},
            "file_node_map": {},
            "sessions": [],
        }


def save_mindmap(project_dir: Path, mindmap: dict) -> None:
    """Save mindmap to project directory.

    Args:
        project_dir: Path to project directory
        mindmap: Mindmap dict to save

    Writes to mindmap.json with indent=2. Updates _meta.updated to today's ISO date.
    Uses atomic write (temp file + rename) to prevent corruption on crash.
    """
    import tempfile

    mindmap_path = project_dir / "mindmap.json"
    mindmap_path.parent.mkdir(parents=True, exist_ok=True)

    mindmap["_meta"]["updated"] = date.today().isoformat()

    try:
        # Atomic write: write to temp file in same dir, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(mindmap_path.parent),
            prefix=".mindmap_tmp_",
            suffix=".json",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mindmap, f, indent=2)
        os.replace(tmp_path, mindmap_path)
    except Exception:
        # Fallback if atomic write fails
        with open(mindmap_path, "w", encoding="utf-8") as f:
            json.dump(mindmap, f, indent=2)


def get_relevant_nodes(mindmap: dict, query: str, max_nodes: int = 3) -> list[dict]:
    """Find nodes relevant to a query using keyword matching.

    Args:
        mindmap: Loaded mindmap dict
        query: Search query string
        max_nodes: Maximum number of nodes to return (default 3)

    Returns:
        List of top matching nodes, each as:
        {"node_id": str, "content": str, "keywords": list, "files": list, "score": int}
        Sorted by score descending.
        Always includes "project_overview" node if it exists (score boost of +10).
        Skips nodes where content is empty or None.
    """
    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    nodes = mindmap.get("nodes", {})
    results = []

    for node_id, node in nodes.items():
        content = node.get("content", "")
        if not content:
            continue

        keywords = node.get("keywords", [])
        files = node.get("files", [])

        content_words = set(_tokenize(content))
        keyword_set = set(k.lower() for k in keywords)

        score = 0
        for token in query_tokens:
            if token in keyword_set:
                score += 2
            if token in content_words:
                score += 1

        if node_id == "project_overview":
            score += 10

        if score > 0:
            results.append({
                "node_id": node_id,
                "content": content,
                "keywords": keywords,
                "files": files,
                "score": score,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_nodes]


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase words, removing stopwords.

    Args:
        text: Input text to tokenize

    Returns:
        Set of lowercase tokens with stopwords removed
    """
    if not text:
        return set()

    text = text.lower()
    tokens = re.split(r'[\s\-_/.:;,\[\]{}()]+', text)
    return {t for t in tokens if t and t not in STOPWORDS and len(t) > 1}


def upsert_node(
    mindmap: dict,
    node_id: str,
    content: str,
    keywords: list[str] | None = None,
    parent: str | None = None,
    files: list[str] | None = None,
) -> dict:
    """Create or update a node in the mindmap.

    Args:
        mindmap: Mindmap dict to modify
        node_id: Unique identifier for the node
        content: Node content/summary
        keywords: List of keyword strings (default [])
        parent: Parent node_id (default "project_overview" for new nodes)
        files: List of file paths associated with this node (default [])

    Returns:
        Updated mindmap dict.

    On update: merges keywords (union), updates content, updates last_updated.
    On create: sets all fields, parent defaults to "project_overview".
    Updates file_node_map for each file.
    Marks node stale=False after upsert.
    """
    keywords = keywords or []
    files = files or []
    parent = parent or "project_overview"

    nodes = mindmap.setdefault("nodes", {})
    file_node_map = mindmap.setdefault("file_node_map", {})

    existing = nodes.get(node_id)

    if existing:
        existing_keywords = set(existing.get("keywords", []))
        existing_keywords.update(k.lower() for k in keywords)
        existing["keywords"] = list(existing_keywords)
        existing["content"] = content
        existing["last_updated"] = date.today().isoformat()
        existing["stale"] = False
        if parent:
            existing["parent"] = parent
    else:
        nodes[node_id] = {
            "content": content,
            "keywords": [k.lower() for k in keywords],
            "parent": parent,
            "files": files,
            "created": date.today().isoformat(),
            "last_updated": date.today().isoformat(),
            "stale": False,
        }

    node_files = nodes[node_id].get("files", [])
    for filepath in files:
        if filepath not in file_node_map:
            file_node_map[filepath] = []
        if node_id not in file_node_map[filepath]:
            file_node_map[filepath].append(node_id)

    return mindmap


def mark_files_stale(mindmap: dict, changed_files: list[str]) -> list[str]:
    """Mark nodes stale based on changed files.

    Args:
        mindmap: Mindmap dict to modify
        changed_files: List of file paths that have changed

    Returns:
        List of node_ids that were marked as stale.
    """
    file_node_map = mindmap.get("file_node_map", {})
    nodes = mindmap.get("nodes", {})
    stale_ids = []

    for filepath in changed_files:
        node_ids = file_node_map.get(filepath, [])
        for node_id in node_ids:
            if node_id in nodes:
                nodes[node_id]["stale"] = True
                stale_ids.append(node_id)

    return stale_ids


def build_initial_mindmap_from_stack(cwd: Path, slug: str, stack_info: dict) -> dict:
    """Build initial mindmap from detected stack information.

    Args:
        cwd: Current working directory (project root)
        slug: Project slug (for naming)
        stack_info: Dict with detected stack info containing:
            - stack: list of detected technologies
            - env_vars: dict of environment variables
            - config_files: list of config file paths

    Returns:
        Assembled mindmap dict with initial nodes (caller should save it).

    Creates:
    - "project_overview" node with README first paragraph content
    - "stack" node with detected tech stack
    - "environment" node with env vars and config files
    - "gotchas" node (empty, filled in later by sessions)
    - Proper parent/children relationships
    """
    mindmap = {
        "_meta": {
            "version": 2,
            "project": slug,
            "created": date.today().isoformat(),
            "updated": date.today().isoformat(),
        },
        "nodes": {},
        "file_node_map": {},
        "sessions": [],
    }

    readme_content = _extract_readme_content(cwd)
    if readme_content:
        mindmap["nodes"]["project_overview"] = {
            "content": readme_content,
            "keywords": [slug.lower(), "project", "overview"],
            "parent": None,
            "files": [],
            "created": date.today().isoformat(),
            "last_updated": date.today().isoformat(),
            "stale": False,
        }

    stack = stack_info.get("stack", [])
    stack_keywords = [s.lower() for s in stack] if stack else ["unknown"]
    mindmap["nodes"]["stack"] = {
        "content": f"Tech stack: {', '.join(stack)}",
        "keywords": stack_keywords,
        "parent": "project_overview",
        "files": stack_info.get("config_files", []),
        "created": date.today().isoformat(),
        "last_updated": date.today().isoformat(),
        "stale": False,
    }

    env_vars = stack_info.get("env_vars", {})
    env_info = ", ".join(f"{k}={v}" for k, v in list(env_vars.items())[:5])
    config_files = stack_info.get("config_files", [])
    mindmap["nodes"]["environment"] = {
        "content": f"Environment: {env_info}",
        "keywords": list(env_vars.keys())[:10] if env_vars else ["environment"],
        "parent": "project_overview",
        "files": config_files,
        "created": date.today().isoformat(),
        "last_updated": date.today().isoformat(),
        "stale": False,
    }

    mindmap["nodes"]["gotchas"] = {
        "content": "",
        "keywords": ["gotchas", "warnings", "issues"],
        "parent": "project_overview",
        "files": [],
        "created": date.today().isoformat(),
        "last_updated": date.today().isoformat(),
        "stale": False,
    }

    for node_id, node in mindmap["nodes"].items():
        if node_id != "project_overview" and node.get("files"):
            for filepath in node["files"]:
                if filepath not in mindmap["file_node_map"]:
                    mindmap["file_node_map"][filepath] = []
                if node_id not in mindmap["file_node_map"][filepath]:
                    mindmap["file_node_map"][filepath].append(node_id)

    return mindmap


def _extract_readme_content(cwd: Path) -> str:
    """Extract first paragraph from README in project root.

    Args:
        cwd: Project root directory

    Returns:
        First paragraph of README, or empty string if not found
    """
    readme_names = ["README.md", "README.txt", "README", "readme.md"]
    for name in readme_names:
        readme_path = cwd / name
        if readme_path.exists():
            try:
                content = readme_path.read_text(encoding="utf-8")
                lines = content.split("\n")
                paragraph = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        if paragraph:
                            break
                        continue
                    if line.startswith("#"):
                        continue
                    paragraph.append(line)
                    if len(paragraph) >= 3:
                        break
                return " ".join(paragraph)[:300]
            except (IOError, UnicodeDecodeError):
                pass
    return ""


def mindmap_to_context_md(mindmap: dict, max_tokens: int = 1500) -> str:
    """Serialize mindmap to human-readable markdown.

    Args:
        mindmap: Mindmap dict to serialize
        max_tokens: Approximate token limit (4 chars ≈ 1 token)

    Returns:
        Markdown string suitable for Obsidian viewing.
        Groups by parent node, outputs as sections with ## headers.
        Truncates to max_tokens.
    """
    nodes = mindmap.get("nodes", {})
    if not nodes:
        return "# Project Context\n\nNo context available yet."

    by_parent: dict[str | None, list[dict]] = {}
    for node_id, node in nodes.items():
        parent = node.get("parent")
        if parent not in by_parent:
            by_parent[parent] = []
        by_parent[parent].append({
            "node_id": node_id,
            "content": node.get("content", ""),
            "keywords": node.get("keywords", []),
            "files": node.get("files", []),
        })

    lines = ["# Project Context\n"]
    current_length = len("".join(lines))

    def add_line(text: str) -> bool:
        nonlocal current_length
        if current_length + len(text) > max_tokens * 4:
            return False
        lines.append(text)
        current_length += len(text)
        return True

    def render_nodes(node_list: list[dict], indent: int = 0) -> None:
        for node in node_list:
            node_id = node["node_id"]
            content = node["content"]
            keywords = node["keywords"]
            files = node["files"]

            header = f"{'  ' * indent}## {node_id}\n"
            if not add_line(header):
                return

            if content:
                content_line = f"{'  ' * indent}{content}\n"
                if not add_line(content_line):
                    return

            if keywords:
                kw_line = f"{'  ' * indent}_Keywords: {', '.join(keywords[:8])}_\n"
                if not add_line(kw_line):
                    return

            if files:
                file_line = f"{'  ' * indent}_Files: {', '.join(files[:5])}_\n"
                if not add_line(file_line):
                    return

            add_line("\n")

    root_nodes = by_parent.get(None, [])
    render_nodes(root_nodes)

    for parent_id, child_nodes in by_parent.items():
        if parent_id is None:
            continue
        for node in child_nodes:
            if node["node_id"] in by_parent:
                continue
            header = f"## {node['node_id']}\n"
            if not add_line(header):
                break
            if node["content"]:
                if not add_line(f"{node['content']}\n"):
                    break
            if node["keywords"]:
                add_line(f"_Keywords: {', '.join(node['keywords'][:6])}_\n")
            add_line("\n")

    result = "".join(lines)
    if len(result) > max_tokens * 4:
        result = result[: max_tokens * 4 - 100] + "\n\n_(truncated)_"

    return result