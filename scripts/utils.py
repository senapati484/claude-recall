"""
utils.py — Shared helpers for claude-recall.

All scripts import from here. Config lives at ~/.claude/claude-recall.json
and points to the user's Obsidian vault.
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "claude-recall.json"
DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"

def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] UTILS: {msg}\n")
    except Exception:
        pass

DEFAULT_CONFIG = {
    "vault_path": "",                  # Required — set by install.sh
    "vault_folder": "claude-recall",   # Folder inside the vault
    "max_context_tokens": 2000,
    "include_recent_sessions": 2,
    "save_sessions": True,
    "load_on_every_prompt": False,
}

# Files that regex matches but aren't real project files
NOISE_FILES = {
    "Next.js", "next.js", "Node.js", "node.js", "React.js", "react.js",
    "Vue.js", "vue.js", "Express.js", "express.js", "Nuxt.js", "nuxt.js",
    "Svelte.js", "svelte.js", "Remix.js", "remix.js",
    "response.json", "request.json", "NextResponse.json",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "index.js", "index.ts", "index.py",  # too generic
    "youtubei.js",
}

# Noise path fragments — skip files with these in the path
NOISE_PATH_FRAGMENTS = {"_next/static", "node_modules", ".next/", "dist/", ".cache/"}


def load_config() -> dict:
    """Load config, merging user values over defaults."""
    if not CONFIG_PATH.exists():
        print(
            "[claude-recall] Config not found. Run install.sh first.",
            file=sys.stderr,
        )
        sys.exit(0)
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(user)
        if not cfg["vault_path"]:
            print("[claude-recall] vault_path is empty. Re-run install.sh.", file=sys.stderr)
            sys.exit(0)
        return cfg
    except json.JSONDecodeError as e:
        print(f"[claude-recall] Config malformed: {e}. Re-run install.sh.", file=sys.stderr)
        sys.exit(0)


def get_vault_root(cfg: dict) -> Path:
    """Return the claude-recall folder inside the Obsidian vault."""
    vault = Path(cfg["vault_path"]).expanduser()
    if not vault.exists():
        # Edge case: unmounted drive — tell user to check mount
        print(
            f"[claude-recall] Vault not found at {vault}. "
            "Check if the drive is mounted and vault_path in ~/.claude/claude-recall.json.",
            file=sys.stderr,
        )
        sys.exit(0)
    return vault / cfg["vault_folder"]


def cwd_to_slug(cwd: Path) -> str:
    """
    Convert a project directory path into a short, Obsidian-safe slug.

    /home/sayan/projects/setu          → setu
    /home/sayan/client/acme/dashboard  → acme-dashboard
    /mnt/c/Users/sayan/work/api        → work-api   (WSL paths handled)

    Generic segments stripped: projects repos code src workspace dev work home
    """
    parts = list(cwd.parts)

    # Strip WSL Windows prefix /mnt/X/
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1:
        parts = parts[3:]

    # Strip home dir prefix
    home_parts = list(Path.home().parts)
    while parts and home_parts and parts[0] == home_parts[0]:
        parts.pop(0)
        home_parts.pop(0)

    # Drop generic noise segments
    noise = {"projects", "repos", "code", "src", "workspace", "dev", "work", "home"}
    meaningful = [p for p in parts if p.lower() not in noise]
    if meaningful:
        chosen = meaningful[-2:]
    elif len(parts) >= 2:
        chosen = parts[-2:]
    else:
        chosen = parts

    slug = "-".join(chosen).lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug).strip("-")
    return slug or "unknown-project"


def get_project_dir(cfg: dict, slug: str) -> Path:
    """Return vault_root/projects/<slug>/"""
    return get_vault_root(cfg) / "projects" / slug


def read_hook_input() -> dict:
    """Read Claude Code hook JSON from stdin or environment variables."""
    # First try stdin
    try:
        raw = sys.stdin.read().strip()
        if raw:
            debug_log(f"read_hook_input: got stdin data: {raw[:100]}")
            return json.loads(raw)
    except Exception as e:
        debug_log(f"read_hook_input: stdin error: {e}")
    
    # Try environment variables
    for key in ["CLAUDE_HOOK_INPUT", "CLAUDE_SESSION_ID", "CLAUDE_CWD", "HOOK_INPUT"]:
        val = os.environ.get(key)
        if val:
            debug_log(f"read_hook_input: found env {key}={val[:50]}")
            try:
                return json.loads(val)
            except:
                pass
    
    debug_log("read_hook_input: no input found, returning empty dict")
    return {}


def get_cwd(hook_input: dict) -> Path:
    return Path(hook_input.get("cwd") or os.getcwd())


def now_str(fmt: str = "%Y-%m-%d_%H-%M") -> str:
    return datetime.now().strftime(fmt)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough budget: 4 chars ≈ 1 token. Cuts at a line boundary."""
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    cut = text[:limit].rfind("\n")
    cut = cut if cut > limit // 2 else limit
    return text[:cut] + "\n\n[claude-recall: truncated — edit context.md in Obsidian to trim]"


def session_marker(session_id: str) -> Path:
    """Marker file preventing context re-injection on every prompt."""
    # Edge case: empty/unknown session_id — use timestamp+PID to avoid collisions
    if not session_id or session_id == "unknown":
        session_id = f"{now_str()}_{os.getpid()}"
    return Path.home() / ".claude" / f".recall_{session_id}"


def cleanup_stale_markers():
    """Delete marker files older than 24 h (crash cleanup)."""
    import time
    cutoff = time.time() - 86400
    for f in (Path.home() / ".claude").glob(".recall_*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            pass


# ── New v2 helpers ────────────────────────────────────────────────────────────

def is_scaffold_only(text: str) -> bool:
    """Check if context.md is just the empty scaffold with no real content.
    
    Returns True if every section body is either empty or just an HTML comment.
    """
    # Strip frontmatter
    body = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL).strip()
    # Remove the title line
    body = re.sub(r"^#\s+\S+.*$", "", body, flags=re.MULTILINE).strip()
    # Remove section headers
    body = re.sub(r"^##\s+.*$", "", body, flags=re.MULTILINE)
    # Remove HTML comments
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    # Remove auto markers
    body = re.sub(r"<!--\s*auto:(start|end)\s*-->", "", body)
    # If nothing meaningful remains, it's just the scaffold
    return len(body.strip()) < 10


def filter_file_paths(files: list[str]) -> list[str]:
    """Filter out noise from extracted file paths."""
    clean = []
    for f in files:
        basename = f.rsplit("/", 1)[-1] if "/" in f else f
        # Skip known noise files
        if basename in NOISE_FILES:
            continue
        # Skip paths with noise fragments
        if any(frag in f for frag in NOISE_PATH_FRAGMENTS):
            continue
        # Skip if it's just a library name (no extension actually in the project)
        if basename.count(".") == 1 and basename.split(".")[0][0].isupper() and basename.endswith(".js"):
            continue  # e.g. "React.js", "Express.js"
        clean.append(f)
    return clean[:15]


def detect_project_stack(cwd: Path) -> dict:
    """Detect project stack from filesystem. Returns structured info.
    
    Scans package.json, pubspec.yaml, requirements.txt, Cargo.toml, go.mod, etc.
    """
    info = {
        "type": "unknown",
        "name": "",
        "stack": [],
        "scripts": {},
        "structure": [],
        "config_files": [],
        "env_keys": [],
    }
    
    cwd = Path(cwd)
    if not cwd.exists():
        return info
    
    # Detect top-level structure
    try:
        entries = sorted(cwd.iterdir())
        dirs = [e.name for e in entries if e.is_dir() and not e.name.startswith(".")]
        files = [e.name for e in entries if e.is_file()]
        info["structure"] = dirs[:20]
        info["config_files"] = [f for f in files if f in {
            "package.json", "tsconfig.json", "next.config.ts", "next.config.js",
            "next.config.mjs", "vite.config.ts", "vite.config.js",
            "tailwind.config.ts", "tailwind.config.js", "postcss.config.js",
            "pubspec.yaml", "requirements.txt", "pyproject.toml", "setup.py",
            "Cargo.toml", "go.mod", "Gemfile", "Makefile", "Dockerfile",
            "docker-compose.yml", "docker-compose.yaml",
            ".env", ".env.local", ".env.example",
        }]
    except PermissionError:
        pass
    
    # ── Node.js / JavaScript / TypeScript ──
    pkg_json = cwd / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            info["name"] = pkg.get("name", "")
            info["scripts"] = pkg.get("scripts", {})
            
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            
            # Detect frameworks
            framework_map = {
                "next": "Next.js",
                "react": "React",
                "vue": "Vue.js",
                "svelte": "Svelte",
                "@angular/core": "Angular",
                "express": "Express.js",
                "fastify": "Fastify",
                "hono": "Hono",
                "nuxt": "Nuxt.js",
                "remix": "Remix",
                "astro": "Astro",
                "vite": "Vite",
            }
            for dep, label in framework_map.items():
                if dep in all_deps:
                    version = all_deps[dep].lstrip("^~>=<")
                    major = version.split(".")[0] if version else ""
                    info["stack"].append(f"{label} {major}" if major.isdigit() else label)
            
            # Detect tools
            tool_map = {
                "tailwindcss": "Tailwind CSS",
                "typescript": "TypeScript",
                "prisma": "Prisma",
                "drizzle-orm": "Drizzle ORM",
                "@supabase/supabase-js": "Supabase",
                "firebase": "Firebase",
                "mongoose": "Mongoose",
                "sequelize": "Sequelize",
                "socket.io": "Socket.io",
                "stripe": "Stripe",
                "zod": "Zod",
                "@trpc/server": "tRPC",
                "playwright": "Playwright",
                "jest": "Jest",
                "vitest": "Vitest",
                "python-dotenv": "Python-dotenv",
                "anthropic": "Anthropic SDK",
            }
            for dep, label in tool_map.items():
                if dep in all_deps:
                    info["stack"].append(label)

            info["type"] = "node"
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Python (root-level files) ──
    for pyfile in ["requirements.txt", "pyproject.toml", "setup.py"]:
        if (cwd / pyfile).exists():
            info["type"] = "python"
            info["stack"].append("Python")
            try:
                text = (cwd / pyfile).read_text(encoding="utf-8")
                py_deps = {
                    "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
                    "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic",
                    "celery": "Celery", "redis": "Redis",
                    "torch": "PyTorch", "tensorflow": "TensorFlow",
                    "pandas": "Pandas", "numpy": "NumPy",
                }
                for dep, label in py_deps.items():
                    if dep in text.lower():
                        info["stack"].append(label)
            except Exception:
                pass
            break

    # ── Python (by .py files if no root requirements.txt) ──
    if info["type"] == "unknown":
        try:
            py_files = list(cwd.glob("**/*.py"))
            if py_files:
                info["type"] = "python"
                info["stack"].append("Python")
                # Check for common Python frameworks in imports
                import_re = re.compile(r"^import\s+(\w+)|^from\s+(\w+)", re.MULTILINE)
                found_modules = set()
                for pf in py_files[:10]:  # Check first 10 files
                    try:
                        text = pf.read_text(encoding="utf-8", errors="ignore")
                        for m in import_re.finditer(text):
                            found_modules.add(m.group(1) or m.group(2))
                    except Exception:
                        pass
                py_frameworks = {
                    "flask": "Flask", "fastapi": "FastAPI", "django": "Django",
                    "anthropic": "Anthropic SDK", "openai": "OpenAI",
                    "requests": "Requests", "httpx": "httpx",
                    "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic",
                }
                for mod, label in py_frameworks.items():
                    if mod in found_modules:
                        info["stack"].append(label)
        except PermissionError:
            pass
    
    # ── Rust ──
    cargo = cwd / "Cargo.toml"
    if cargo.exists():
        info["type"] = "rust"
        info["stack"].append("Rust")
        try:
            text = cargo.read_text(encoding="utf-8")
            name_match = re.search(r'^name\s*=\s*"(.+)"', text, re.MULTILINE)
            if name_match:
                info["name"] = name_match.group(1)
            rust_deps = {
                "tokio": "Tokio", "actix-web": "Actix", "axum": "Axum",
                "serde": "Serde", "diesel": "Diesel", "sqlx": "SQLx",
                "bevy": "Bevy",
            }
            for dep, label in rust_deps.items():
                if dep in text:
                    info["stack"].append(label)
        except Exception:
            pass
    
    # ── Go ──
    gomod = cwd / "go.mod"
    if gomod.exists():
        info["type"] = "go"
        info["stack"].append("Go")
        try:
            text = gomod.read_text(encoding="utf-8")
            mod_match = re.search(r"^module\s+(.+)$", text, re.MULTILINE)
            if mod_match:
                info["name"] = mod_match.group(1).strip()
        except Exception:
            pass

    # ── Claude Code Skill (SKILL.md at root) ──
    skill_md = cwd / "SKILL.md"
    if skill_md.exists():
        info["type"] = "claude-skill"
        info["stack"].append("Claude Code Skill")
        info["name"] = cwd.name
        try:
            text = skill_md.read_text(encoding="utf-8")
            # Try to extract name from skill metadata
            name_match = re.search(r"^name:\s*([^\s\n]+)", text, re.MULTILINE)
            if name_match:
                info["name"] = name_match.group(1)
        except Exception:
            pass

    # ── .env keys (never values) ──
    for env_file in [".env.example", ".env.local", ".env"]:
        env_path = cwd / env_file
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=", 1)[0].strip()
                        if key:
                            info["env_keys"].append(key)
            except Exception:
                pass
            break  # Only read first found env file
    
    # ── Git info ──
    try:
        import subprocess
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(cwd), capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["git_branch"] = result.stdout.strip()
        
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(cwd), capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["recent_commits"] = result.stdout.strip().splitlines()
    except Exception:
        pass
    
    return info


def parse_index_entries(index_path: Path) -> list[dict]:
    """Parse _index.md entries into structured data for deduplication.
    
    Handles both old format (simple list) and new format (with session count).
    Returns list of dicts with keys: slug, directory, sessions, total_turns, last_active
    """
    entries = []
    if not index_path.exists():
        return entries
    
    text = index_path.read_text(encoding="utf-8")
    
    # New table format
    table_re = re.compile(
        r"\|\s*\[([^\]]+)\]\([^)]+\)\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+)\s*\|"
    )
    for m in table_re.finditer(text):
        entries.append({
            "slug": m.group(1),
            "directory": m.group(2),
            "sessions": int(m.group(3)),
            "total_turns": int(m.group(4)),
            "last_active": m.group(5).strip(),
        })
    
    if entries:
        # Merge old entries not already in table
        old_re = re.compile(
            r"- \[([^\]]+)\]\([^)]+\)\s*·\s*`([^`]+)`\s*·\s*(\d+)\s+turns?\s*·\s*(.+)"
        )
        slug_map: dict[str, dict] = {}
        for m in old_re.finditer(text):
            slug = m.group(1)
            if any(e["slug"] == slug for e in entries):
                continue
            directory = m.group(2)
            turns = int(m.group(3))
            timestamp = m.group(4).strip()
            slug_map[slug] = {
                "slug": slug,
                "directory": directory,
                "sessions": 1,
                "total_turns": turns,
                "last_active": timestamp,
            }
        for entry in slug_map.values():
            entries.append(entry)
        return entries

    # Old list format only
    old_re = re.compile(
        r"- \[([^\]]+)\]\([^)]+\)\s*·\s*`([^`]+)`\s*·\s*(\d+)\s+turns?\s*·\s*(.+)"
    )
    slug_map: dict[str, dict] = {}
    for m in old_re.finditer(text):
        slug = m.group(1)
        directory = m.group(2)
        turns = int(m.group(3))
        timestamp = m.group(4).strip()
        
        if slug in slug_map:
            slug_map[slug]["sessions"] += 1
            slug_map[slug]["total_turns"] += turns
            # Keep the latest timestamp
            if timestamp > slug_map[slug]["last_active"]:
                slug_map[slug]["last_active"] = timestamp
        else:
            slug_map[slug] = {
                "slug": slug,
                "directory": directory,
                "sessions": 1,
                "total_turns": turns,
                "last_active": timestamp,
            }
    
    return list(slug_map.values())


def merge_auto_section(existing_text: str, section_name: str, new_content: str) -> str:
    """Merge auto-generated content into a section of context.md.
    
    If auto markers exist for that section, replace content between them.
    If section exists but no auto markers, add markers and content after the header.
    If section doesn't exist, append it.
    
    User-written content outside auto markers is NEVER modified.
    """
    auto_start = f"<!-- auto:{section_name}:start -->"
    auto_end = f"<!-- auto:{section_name}:end -->"
    
    new_block = f"{auto_start}\n{new_content.strip()}\n{auto_end}"
    
    # Case 1: auto markers already exist — replace between them
    pattern = re.compile(
        re.escape(auto_start) + r".*?" + re.escape(auto_end),
        re.DOTALL
    )
    if pattern.search(existing_text):
        return pattern.sub(new_block, existing_text)
    
    # Map section names to headers
    header_map = {
        "what_this_is": "## What this is",
        "stack": "## Stack",
        "current_state": "## Current state",
        "key_files": "## Key files",
        "architecture": "## Architecture decisions",
        "gotchas": "## Gotchas",
        "environment": "## Environment",
    }
    header = header_map.get(section_name, f"## {section_name}")
    
    # Case 2: section header exists — insert auto block after it
    header_pattern = re.compile(
        r"(" + re.escape(header) + r"\s*\n)"
        r"(<!-- .+?-->\s*\n)?"  # optional existing HTML comment placeholder
    )
    match = header_pattern.search(existing_text)
    if match:
        insert_pos = match.end()
        return existing_text[:insert_pos] + new_block + "\n" + existing_text[insert_pos:]
    
    # Case 3: section doesn't exist — append at end
    return existing_text.rstrip() + f"\n\n{header}\n{new_block}\n"


def generate_file_tree(cwd: Path, max_depth: int = 2, max_files: int = 40) -> str:
    """Generate a compact file tree of the project directory.
    
    Respects .gitignore patterns, skips common noise directories.
    Returns a tree string suitable for context.md.
    """
    SKIP_DIRS = {
        "node_modules", ".git", ".next", "__pycache__", ".cache", "dist",
        "build", ".dart_tool", ".idea", ".vscode", ".flutter-plugins",
        "venv", ".venv", "env", ".env", ".tox", "coverage",
        "target",  # Rust
        ".gradle", ".kotlin",  # Kotlin/Android
    }
    SKIP_FILES = {".DS_Store", "Thumbs.db", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
    
    lines = []
    count = 0
    
    def walk(path: Path, prefix: str, depth: int):
        nonlocal count
        if depth > max_depth or count >= max_files:
            return
        
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        
        # Filter entries
        filtered = []
        for e in entries:
            if e.name.startswith(".") and e.name not in {".env.example", ".env.local", ".env"}:
                continue
            if e.is_dir() and e.name in SKIP_DIRS:
                continue
            if e.is_file() and e.name in SKIP_FILES:
                continue
            filtered.append(e)
        
        for i, entry in enumerate(filtered):
            if count >= max_files:
                lines.append(f"{prefix}... (truncated)")
                return
            
            is_last = (i == len(filtered) - 1)
            connector = "└── " if is_last else "├── "
            
            if entry.is_dir():
                # Count children to show inline
                try:
                    child_count = sum(1 for _ in entry.iterdir() 
                                     if not _.name.startswith(".") 
                                     and _.name not in SKIP_DIRS)
                except PermissionError:
                    child_count = 0
                
                lines.append(f"{prefix}{connector}{entry.name}/")
                count += 1
                
                next_prefix = prefix + ("    " if is_last else "│   ")
                walk(entry, next_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")
                count += 1
    
    walk(cwd, "", 0)
    return "\n".join(lines) if lines else "(empty project)"


def auto_generate_context_md(cwd: Path, slug: str) -> str:
    """Generate a fully populated context.md from filesystem scanning.
    
    This is the KEY function — called on first session load to give Claude
    instant context without requiring manual editing.
    """
    fs = detect_project_stack(cwd)
    tree = generate_file_tree(cwd, max_depth=2, max_files=40)
    
    # Build stack string
    stack_str = " · ".join(fs.get("stack", [])) if fs.get("stack") else "Not detected"
    
    # Build what this is
    what_this_is = ""
    if fs.get("name"):
        what_this_is = f"Project: {fs['name']}"
    if fs.get("type") and fs["type"] != "unknown":
        type_labels = {
            "node": "Node.js/JavaScript project",
            "python": "Python project",
            "rust": "Rust project",
            "go": "Go project",
            "flutter": "Flutter/Dart project",
            "claude-skill": "Claude Code Skill",
        }
        label = type_labels.get(fs["type"], fs["type"])
        what_this_is = f"{what_this_is} — {label}" if what_this_is else label
    
    # Build environment
    env_parts = []
    if fs.get("env_keys"):
        env_parts.append("Env vars: " + ", ".join(fs["env_keys"][:15]))
    if fs.get("git_branch"):
        env_parts.append(f"Git branch: {fs['git_branch']}")
    if fs.get("recent_commits"):
        env_parts.append("Recent commits:")
        for c in fs["recent_commits"][:5]:
            env_parts.append(f"  - {c}")
    environment_str = "\n".join(env_parts) if env_parts else "Not detected"
    
    # Build scripts section for Node projects
    scripts_str = ""
    if fs.get("scripts"):
        scripts_str = "\n## Scripts\n<!-- auto:scripts:start -->\n"
        for name, cmd in list(fs["scripts"].items())[:10]:
            scripts_str += f"- `{name}`: {cmd}\n"
        scripts_str += "<!-- auto:scripts:end -->\n"
    
    # Build config files
    config_str = ""
    if fs.get("config_files"):
        config_str = ", ".join(fs["config_files"])
    
    # Build structure section
    structure_str = ""
    if fs.get("structure"):
        structure_str = ", ".join(f"{d}/" for d in fs["structure"][:15])
    
    content = f"""\
---
project: {slug}
directory: {cwd}
created: {datetime.now().strftime('%Y-%m-%d')}
tags: [claude-recall, context]
---

# {slug}

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

```
{tree}
```
<!-- auto:structure:end -->
{scripts_str}
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
    return content


def build_index_table(entries: list[dict]) -> str:
    """Build the _index.md content from structured entries."""
    header = (
        "---\ntags: [claude-recall]\n---\n\n"
        "# claude-recall — project index\n\n"
        "Auto-updated by claude-recall on each session end.\n\n"
        "## Projects\n\n"
        "| Project | Directory | Sessions | Total Turns | Last Active |\n"
        "|---------|-----------|----------|-------------|-------------|\n"
    )
    
    # Sort by last_active descending
    entries.sort(key=lambda e: e.get("last_active", ""), reverse=True)
    
    rows = []
    for e in entries:
        # Shorten directory for readability if it's long
        d = e["directory"]
        display_dir = d
        if len(d) > 50:
            display_dir = "..." + d[-47:]
        rows.append(
            f"| [{e['slug']}](projects/{e['slug']}/context) "
            f"| `{display_dir}` "
            f"| {e['sessions']} "
            f"| {e['total_turns']} "
            f"| {e['last_active']} |"
        )
    
    return header + "\n".join(rows) + "\n"


def get_model_path() -> Path:
    """
    Return the path to the local Qwen GGUF model file.
    Model lives at ~/.claude/models/ — shared across all projects.
    Does NOT raise if the file is absent; callers check .exists() themselves.
    """
    return Path.home() / ".claude" / "models" / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def ensure_model(silent: bool = False) -> bool:
    """
    Ensure the Qwen GGUF model is available at ~/.claude/models/.
    If the model file is missing, attempt to download from HuggingFace.
    Returns True if the model is available, False otherwise.
    """
    model_path = get_model_path()
    if model_path.exists():
        return True

    MODEL_URL = (
        "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF"
        "/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
    )
    model_path.parent.mkdir(parents=True, exist_ok=True)

    if not silent:
        print(
            "[claude-recall] LLM model not found — downloading ~380 MB from HuggingFace...",
            file=sys.stderr,
        )

    try:
        import urllib.request
        urllib.request.urlretrieve(MODEL_URL, str(model_path))
        if model_path.exists():
            if not silent:
                print(f"[claude-recall] Model saved → {model_path}", file=sys.stderr)
            return True
    except Exception as e:
        if not silent:
            print(f"[claude-recall] Model download failed: {e}", file=sys.stderr)

    return False


def llm_available() -> bool:
    """
    True if both llama-cpp-python is importable AND the model file exists.
    Used by summarize.py and scan_project.py as a guard before loading.
    """
    if not get_model_path().exists():
        return False
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False
