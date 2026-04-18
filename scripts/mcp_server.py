#!/usr/bin/env python3
"""
mcp_server.py — FastMCP server exposing claude-recall context as MCP tools.

This server exposes project memory to Claude during a session via MCP tools:
- recall_get: Search relevant context nodes
- recall_update_node: Update a specific node
- recall_session_history: Get recent session summaries
- recall_mindmap: Get full project mindmap tree

Run standalone: python mcp_server.py
Or use with FastMCP's transport (stdio, SSE, etc.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent))

from utils import load_config, get_project_dir
from mindmap import (
    load_mindmap,
    get_relevant_nodes,
    upsert_node,
    save_mindmap,
)


mcp = FastMCP("claude-recall")


def _get_project_dir() -> Path:
    """Get the project directory for the current project."""
    cfg = load_config()
    slug = os.environ.get("CLAUDE_RECALL_SLUG", "unknown")
    return get_project_dir(cfg, slug)


@mcp.tool()
def recall_get(query: str) -> str:
    """Search project memory for context relevant to a topic or question.

    Args:
        query: The topic or question to search for

    Returns:
        Formatted context string or "No stored context found" message
    """
    try:
        project_dir = _get_project_dir()
        mindmap = load_mindmap(project_dir)

        if not mindmap.get("nodes"):
            return f"No stored context found for: {query}"

        nodes = get_relevant_nodes(mindmap, query, max_nodes=5)

        if not nodes:
            return f"No stored context found for: {query}"

        lines = [f"## Context: {query}\n"]
        for node in nodes:
            node_id = node.get("node_id", "unknown")
            content = node.get("content", "")
            files = node.get("files", [])

            lines.append(f"\n### {node_id.replace('_', ' ').title()}")
            if content:
                lines.append(content)
            if files:
                lines.append(f"\n_Files: {', '.join(files[:4])}_")

        return "\n".join(lines)

    except Exception as e:
        return f"Error searching context: {str(e)}"


@mcp.tool()
def recall_update_node(
    node_id: str,
    content: str,
    keywords: str = "",
    files: str = "",
) -> str:
    """Update a specific context node with new information.

    Args:
        node_id: snake_case node name (e.g. "auth_system", "api_routes")
        content: new content for this node (1-3 sentences)
        keywords: comma-separated keywords to add (optional)
        files: comma-separated file paths related to this node (optional)

    Returns:
        Confirmation message with node_id
    """
    try:
        project_dir = _get_project_dir()
        mindmap = load_mindmap(project_dir)

        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []

        upsert_node(
            mindmap=mindmap,
            node_id=node_id,
            content=content,
            keywords=kw_list,
            files=file_list,
        )

        save_mindmap(project_dir, mindmap)

        return f"✓ Updated node: {node_id}"

    except Exception as e:
        return f"Error updating node: {str(e)}"


@mcp.tool()
def recall_session_history(count: int = 3) -> str:
    """Get summaries of recent work sessions on this project.

    Args:
        count: number of recent sessions to return (default 3)

    Returns:
        Formatted session history or "No sessions recorded" message
    """
    try:
        project_dir = _get_project_dir()
        mindmap = load_mindmap(project_dir)

        sessions = mindmap.get("sessions", [])

        if not sessions:
            return "No sessions recorded yet."

        recent = sessions[-count:] if len(sessions) >= count else sessions

        lines = ["## Recent Sessions\n"]
        for i, session in enumerate(recent, 1):
            date = session.get("date", "unknown date")
            summary = session.get("summary", "No summary")
            nodes = session.get("nodes_updated", [])

            lines.append(f"\n### Session {i}: {date}")
            lines.append(summary)
            if nodes:
                lines.append(f"_Nodes updated: {', '.join(nodes)}_")

        return "\n".join(lines)

    except Exception as e:
        return f"Error loading session history: {str(e)}"


@mcp.tool()
def recall_mindmap() -> str:
    """Get the full project mindmap — all stored context nodes.

    Returns:
        Formatted markdown tree showing parent/children relationships
    """
    try:
        project_dir = _get_project_dir()
        mindmap = load_mindmap(project_dir)

        nodes = mindmap.get("nodes", {})

        if not nodes:
            return "## Project Mindmap\n\nNo context nodes yet."

        by_parent: dict[str | None, list[dict]] = {None: []}
        for node_id, node in nodes.items():
            parent = node.get("parent")
            if parent not in by_parent:
                by_parent[parent] = []
            by_parent[parent].append({
                "node_id": node_id,
                "content": node.get("content", ""),
                "keywords": node.get("keywords", []),
                "files": node.get("files", []),
                "stale": node.get("stale", False),
            })

        lines = ["## Project Mindmap\n"]

        def render_node(node: dict, indent: int = 0) -> None:
            node_id = node["node_id"]
            content = node["content"]
            keywords = node["keywords"]
            stale = node.get("stale", False)

            prefix = "  " * indent
            stale_marker = " [stale]" if stale else ""
            lines.append(f"{prefix}- **{node_id}**{stale_marker}")

            if content:
                lines.append(f"{prefix}  {content[:100]}{'...' if len(content) > 100 else ''}")

            if keywords:
                lines.append(f"{prefix}  _Keywords: {', '.join(keywords[:5])}_")

            lines.append("")

            children = by_parent.get(node_id, [])
            for child in children:
                render_node(child, indent + 1)

        root_nodes = by_parent.get(None, [])
        for node in root_nodes:
            render_node(node)

        return "\n".join(lines)

    except Exception as e:
        return f"Error loading mindmap: {str(e)}"


if __name__ == "__main__":
    mcp.run()