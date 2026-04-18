#!/usr/bin/env python3
"""
recall_update.py — /recall command for claude-recall.

Usage:
    python3 ~/.claude/skills/claude-recall/scripts/recall_update.py <action> [cwd]

Actions:
    update   Regenerate mindmap.json from filesystem + README
    status   Show current mindmap status as tree
    query    Search mindmap for relevant context
    doctor   Check setup health and LLM backend availability
    reset    Delete mindmap.json and regenerate from scratch
"""

import sys
import os
import shutil
import subprocess
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, detect_project_stack, llm_available, safe_unlink,
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


def action_doctor(cwd: Path, cfg: dict) -> None:
    """Run comprehensive health check of claude-recall installation."""
    import shutil, subprocess, json as _json, time
    from pathlib import Path

    print("## claude-recall: doctor")
    print()
    ok_count = 0
    err_count = 0

    def ok(msg):
        nonlocal ok_count
        print(f"  ✓ {msg}")
        ok_count += 1

    def err(msg, hint=""):
        nonlocal err_count
        print(f"  ✗ {msg}")
        if hint:
            print(f"    → {hint}")
        err_count += 1

    def warn(msg):
        print(f"  ! {msg}")

    # 1. Python version
    import sys
    v = sys.version_info
    if v >= (3, 8):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        err(f"Python {v.major}.{v.minor} — need 3.8+")

    # 2. claude CLI
    claude_path = shutil.which("claude")
    if claude_path:
        try:
            r = subprocess.run(
                ["claude", "-p", "--bare", "--dangerously-skip-permissions",
                 "--output-format", "text", "Say: RECALL_TEST_OK"],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0 and r.stdout.strip():
                ok(f"claude CLI: working ({claude_path})")
            else:
                err(f"claude CLI: found but test failed (exit {r.returncode})",
                    hint=r.stderr.strip()[:80] or "Check Claude Code auth")
        except subprocess.TimeoutExpired:
            err("claude CLI: test timed out", hint="Claude Code may be offline")
        except Exception as e:
            err(f"claude CLI: {e}")
    else:
        err("claude CLI: not found", hint="Install Claude Code and ensure it's in PATH")

    # 3. Dependencies
    print()
    for dep, required in [("anthropic", False), ("fastmcp", True), ("openai", False)]:
        try:
            __import__(dep)
            ok(f"{dep}: installed")
        except ImportError:
            if required:
                err(f"{dep}: missing", hint=f"pip3 install {dep} --break-system-packages")
            else:
                warn(f"{dep}: not installed (optional fallback)")

    # 4. Config + vault
    print()
    vault = cfg.get("vault_path", "")
    if vault and Path(vault).exists():
        ok(f"vault: {vault}")
    else:
        err(f"vault: not found at '{vault}'", hint="Re-run install.sh")

    # 5. Hooks
    print()
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        settings = _json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})
        required_hooks = {
            "SessionStart": "session_start.py",
            "UserPromptSubmit": "load_context.py",
            "Stop": "save_context.py",
            "PostToolUse": "post_tool_use.py",
        }
        for event, script in required_hooks.items():
            found = any(
                script in h.get("command", "")
                for e in hooks.get(event, [])
                for h in e.get("hooks", [])
            )
            if found:
                ok(f"hook: {event}")
            else:
                err(f"hook: {event} missing", hint="Re-run install.sh")
    else:
        err("settings.json not found", hint="Re-run install.sh")

    # 6. Upstream statusLine
    print()
    upstream_path = Path.home() / ".claude" / "claude-recall-upstream-statusline.txt"
    if upstream_path.exists():
        upstream_cmd = upstream_path.read_text().strip()
        if not upstream_cmd:
            ok("statusLine: no upstream (clean)")
        else:
            first_word = upstream_cmd.split()[0]
            is_valid = (first_word in ("python3", "python", "node", "bash", "sh")
                        or shutil.which(first_word) is not None)
            if is_valid:
                ok(f"statusLine upstream: valid ({upstream_cmd[:50]})")
            else:
                err(f"statusLine upstream: stale command '{upstream_cmd[:40]}'",
                    hint="Run: python3 recall_update.py repair")

    # 7. Mindmap for current project
    print()
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    mindmap_path = project_dir / "mindmap.json"

    print(f"  Project slug: {slug}")
    if mindmap_path.exists():
        try:
            data = _json.loads(mindmap_path.read_text())
            nodes = data.get("nodes", {})
            stale = [k for k, v in nodes.items() if v.get("stale")]
            ok(f"mindmap.json: {len(nodes)} nodes ({len(stale)} stale)")
        except Exception as e:
            err(f"mindmap.json: corrupt — {e}", hint="Run: python3 recall_update.py reset")
    else:
        warn(f"mindmap.json: not yet generated (run: python3 recall_update.py update)")

    # Summary
    print()
    print(f"  Result: {ok_count} OK, {err_count} error(s)")
    if err_count == 0:
        print("  ✓ claude-recall is healthy. Restart Claude Code if you just installed.")
    else:
        print("  ✗ Fix the errors above, then restart Claude Code.")


def action_repair(cwd: Path, cfg: dict) -> None:
    """Fix common installation issues without re-running install.sh."""
    import shutil, json as _json
    from pathlib import Path

    print("## claude-recall: repair")
    print()
    fixed = 0

    upstream_path = Path.home() / ".claude" / "claude-recall-upstream-statusline.txt"
    if upstream_path.exists():
        upstream_cmd = upstream_path.read_text().strip()
        if upstream_cmd:
            first_word = upstream_cmd.split()[0]
            valid = (first_word in ("python3", "python", "node", "bash", "sh")
                     or shutil.which(first_word) is not None)
            if not valid:
                upstream_path.write_text("")
                print(f"  ✓ Cleared stale upstream statusLine: '{upstream_cmd[:60]}'")
                fixed += 1
            else:
                print(f"  ✓ Upstream statusLine OK: '{upstream_cmd[:60]}'")
        else:
            print(f"  ✓ Upstream statusLine: empty (no upstream)")
    else:
        print(f"  - Upstream statusLine file not found")

    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        settings = _json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})
        required_hooks = {
            "SessionStart": "session_start.py",
            "UserPromptSubmit": "load_context.py",
            "Stop": "save_context.py",
            "PostToolUse": "post_tool_use.py",
        }
        for event, script in required_hooks.items():
            found = any(
                script in h.get("command", "")
                for e in hooks.get(event, [])
                for h in e.get("hooks", [])
            )
            if found:
                print(f"  ✓ Hook: {event}")
            else:
                print(f"  ✗ Hook: {event} — NOT registered (re-run install.sh)")

    print()
    print("  Checking dependencies...")
    deps = {"anthropic": False, "fastmcp": False, "openai": False}
    for dep in deps:
        try:
            __import__(dep)
            deps[dep] = True
            print(f"  ✓ {dep}: installed")
        except ImportError:
            print(f"  ✗ {dep}: missing — run: pip3 install {dep} --break-system-packages")

    print()
    claude_path = shutil.which("claude")
    if claude_path:
        print(f"  ✓ claude CLI: {claude_path}")
    else:
        print(f"  ✗ claude CLI: not in PATH")

    marker_dir = Path.home() / ".claude"
    import time
    stale_markers = [
        f for f in marker_dir.glob(".recall_*")
        if time.time() - f.stat().st_mtime > 3600
    ]
    if stale_markers:
        for m in stale_markers:
            safe_unlink(m)
        print(f"  ✓ Removed {len(stale_markers)} stale session marker(s)")
        fixed += 1

    print()
    if fixed > 0:
        print(f"  ✓ Repaired {fixed} issue(s). Restart Claude Code to apply.")
    else:
        print(f"  ✓ No issues found. If errors persist, re-run install.sh.")


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
    elif action in ("doctor", "check", "d"):
        action_doctor(cwd, cfg)
    elif action in ("repair", "fix"):
        action_repair(cwd, cfg)
    else:
        print(f"Unknown action: {action}")
        print("Usage: recall_update.py [update|status|query|doctor|repair|reset] [cwd]")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[claude-recall] Error: {e}", file=sys.stderr)
        sys.exit(1)