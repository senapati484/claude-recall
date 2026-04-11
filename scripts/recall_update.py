#!/usr/bin/env python3
"""
recall_update.py — /recall command for claude-recall.

Uses LLM to generate accurate context from README and project files.
"""

import json
import sys
import os
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_vault_root, get_project_dir,
    cwd_to_slug, parse_index_entries, llm_available, get_model_path,
)

LLM_PROMPT = """Generate a brief project summary (4 lines max).
Output format:
- Line 1: What this project does (1 sentence)
- Line 2: Tech stack (comma separated)
- Line 3: Key directories (comma separated, max 5)
- Line 4: Main entry point

Be accurate and concise."""


def generate_context_with_llm(cwd: Path) -> str:
    """Use LLM to generate context."""
    # First get README
    readme = cwd / "README.md"
    readme_content = ""
    if readme.exists():
        try:
            lines = readme.read_text(encoding="utf-8").split('\n')[:30]
            for line in lines:
                if line.strip() and not line.startswith('```'):
                    readme_content += line + "\n"
        except:
            pass
    
    # Get directory structure
    try:
        dirs = [p.name for p in cwd.iterdir() if p.is_dir() and not p.name.startswith('.')][:8]
        dirs_str = ", ".join(dirs)
    except:
        dirs_str = ""
    
    prompt = f"{LLM_PROMPT}\n\nDirectory structure: {dirs_str}"
    if readme_content:
        prompt += f"\n\nREADME:\n{readme_content[:500]}"
    
    # Try LLM
    if llm_available():
        try:
            from llama_cpp import Llama
            llm = Llama(
                model_path=str(get_model_path()),
                n_ctx=2048,
                n_threads=4,
                n_gpu_layers=0,
                verbose=False,
            )
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=0.1,
            )
            result = response["choices"][0]["message"]["content"].strip()
            if result and len(result) > 20:
                return result
        except Exception as e:
            print(f"LLM error: {e}", file=sys.stderr)
    
    # Fallback: use directory structure directly
    if readme_content:
        lines = readme_content.split('\n')[:10]
        for line in lines:
            if line.strip() and not line.startswith('#'):
                return line.strip()
    return f"Project with directories: {dirs_str}"


def action_update(cwd: Path, cfg: dict) -> None:
    """Update context.md with LLM-generated content."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)
    context_md = project_dir / "context.md"
    
    print(f"[claude-recall] Updating context for: {slug}")
    print(f"  Directory: {cwd}")
    
    # Generate context using LLM
    context = generate_context_with_llm(cwd)
    print(f"  Generated: {context[:100]}...")
    
    content = f"""---
project: {slug}
directory: {cwd}
updated: {datetime.now().strftime('%Y-%m-%d')}
tags: [claude-recall]
---

# {slug}

{context}
"""
    
    context_md.write_text(content, encoding="utf-8")
    print(f"  ✓ Updated: {context_md}")


def action_status(cwd: Path, cfg: dict) -> None:
    """Show status."""
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    context_md = project_dir / "context.md"
    
    print(f"## claude-recall: {slug}")
    print(f"Directory: `{cwd}`")
    
    if context_md.exists():
        print(context_md.read_text())
    else:
        print("No context. Run `/recall update`")


def action_reset(cwd: Path, cfg: dict) -> None:
    """Reset context."""
    action_update(cwd, cfg)


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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)