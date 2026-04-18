#!/usr/bin/env python3
"""
recall_update.py — /recall command for claude-recall.

Usage:
    python3 ~/.claude/skills/claude-recall/scripts/recall_update.py <action> [cwd]

Actions:
    update   Regenerate mindmap.json from filesystem + README
    status   Show current mindmap status as tree
    query    Search mindmap for relevant context
    reset    Delete mindmap.json and regenerate from scratch
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, detect_project_stack, llm_available,
)
from context_builder import build_initial_mindmap
from mindmap import (
    load_mindmap,
    save_mindmap,
    get_relevant_nodes,
    mindmap_to_context_md,
)


def action_update(cwd: Path, cfg: dict) -> None:
    """Scan project and regenerate mindmap.json."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)

    print(f"[claude-recall] Updating mindmap for: {slug}")
    print(f"  Directory: {cwd}")

    fs = detect_project_stack(cwd)
    print(f"  Detected stack: {' · '.join(fs.get('stack', [])) or 'none'}")
    print(f"  LLM available: {llm_available()}")

    mindmap = build_initial_mindmap(cwd, slug, project_dir)

    context_md_content = mindmap_to_context_md(mindmap)
    (project_dir / "context.md").write_text(context_md_content, encoding="utf-8")

    node_count = len(mindmap.get("nodes", {}))
    print(f"  ✓ Mindmap rebuilt: {node_count} nodes")
    print(f"  ✓ Context.md written for Obsidian viewing")


def action_status(cwd: Path, cfg: dict) -> None:
    """Print current mindmap as a tree."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    mindmap_path = project_dir / "mindmap.json"

    print(f"## claude-recall: {slug}")
    print(f"Directory: `{cwd}`")
    print(f"Vault:     {get_vault_root(cfg)}")

    sessions_dir = project_dir / "sessions"
    session_count = len(list(sessions_dir.glob("*.md"))) if sessions_dir.exists() else 0
    print(f"Sessions:  {session_count}")

    if not mindmap_path.exists():
        print("\nNo mindmap.json yet. Run `/recall update` to generate it.")
        return

    mindmap = load_mindmap(project_dir)
    nodes = mindmap.get("nodes", {})

    if not nodes:
        print("\nMindmap is empty. Run `/recall update` to generate it.")
        return

    total_chars = sum(len(n.get("content", "")) for n in nodes.values())
    print(f"\nMindmap: {len(nodes)} nodes ({total_chars} chars)")

    by_parent: dict[str | None, list[tuple[str, dict]]] = {None: []}
    for node_id, node in nodes.items():
        parent = node.get("parent")
        if parent not in by_parent:
            by_parent[parent] = []
        by_parent[parent].append((node_id, node))

    def render_tree(parent: str | None, indent: int = 0) -> None:
        children = sorted(by_parent.get(parent, []), key=lambda x: x[0])
        for node_id, node in children:
            stale = node.get("stale", False)
            updated = node.get("last_updated", "")
            stale_marker = " ⚠️ STALE" if stale else ""
            print(f"{'  ' * indent}├── {node_id} (updated: {updated}){stale_marker}")
            render_tree(node_id, indent + 1)

    render_tree(None)


def action_query(query: str, cwd: Path, cfg: dict) -> None:
    """Search mindmap for relevant context."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    mindmap_path = project_dir / "mindmap.json"

    if not mindmap_path.exists():
        print(f"No mindmap.json for this project. Run `/recall update` first.")
        return

    mindmap = load_mindmap(project_dir)
    nodes = get_relevant_nodes(mindmap, query, max_nodes=5)

    if not nodes:
        print(f"No relevant context found for: {query}")
        return

    print(f"## Query: {query}\n")
    for node in nodes:
        node_id = node.get("node_id", "unknown")
        content = node.get("content", "")
        keywords = node.get("keywords", [])
        files = node.get("files", [])
        score = node.get("score", 0)

        print(f"### {node_id} (score: {score})")
        if content:
            print(content)
        if keywords:
            print(f"\n_Keywords: {', '.join(keywords[:6])}_")
        if files:
            print(f"\n_Files: {', '.join(files[:4])}_")
        print()


def action_reset(cwd: Path, cfg: dict) -> None:
    """Delete existing mindmap.json and regenerate from scratch."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    mindmap_path = project_dir / "mindmap.json"

    if mindmap_path.exists():
        backup = mindmap_path.with_suffix(".json.bak")
        mindmap_path.rename(backup)
        print(f"[claude-recall] Reset: backed up old → {backup}")
    else:
        print(f"[claude-recall] Reset: no existing mindmap.json")

    action_update(cwd, cfg)


def main() -> None:
    action = "status"
    cwd = Path(os.getcwd())
    query = None

    args = sys.argv[1:]
    if args:
        if args[0] == "query" and len(args) > 1:
            action = "query"
            query = " ".join(args[1:])
        elif args[0] == "query" and len(args) == 1:
            print("Usage: recall_update.py query \"your question here\"")
            sys.exit(1)
        else:
            action = args[0].strip("-/")
            if len(args) > 1:
                cwd = Path(args[1])

    cfg = load_config()

    if action in ("update", "u"):
        action_update(cwd, cfg)
    elif action in ("status", "s"):
        action_status(cwd, cfg)
    elif action in ("query", "q"):
        if query:
            action_query(query, cwd, cfg)
        else:
            print("Usage: recall_update.py query \"your question here\"")
            sys.exit(1)
    elif action in ("reset", "r"):
        action_reset(cwd, cfg)
    else:
        print(f"Unknown action: {action}")
        print("Usage: recall_update.py [update|status|query|reset] [cwd]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[claude-recall] Error: {e}", file=sys.stderr)
        sys.exit(1)