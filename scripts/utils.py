"""
utils.py — Shared helpers for claude-recall.

All scripts import from here. Config lives at ~/.claude/claude-recall.json
and points to the user's Obsidian vault.

KEY FUNCTIONS:
- load_config() — load user config
- get_vault_root() / get_project_dir() — vault path resolution
- cwd_to_slug() — project directory → Obsidian-safe slug
- detect_project_stack() — scan filesystem for tech stack
- get_anthropic_client() — Anthropic SDK client (optional fallback)
- llm_available() — check if claude CLI or API keys are available
- merge_auto_section() — update auto-marker sections in context.md
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "claude-recall.json"
DEBUG_LOG = Path.home() / ".claude" / "claude-recall-debug.log"


def safe_unlink(path: Path) -> None:
    """Remove a file if it exists. Compatible with Python 3.7+."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def debug_log(msg: str) -> None:
    """Write debug message to log file."""
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] UTILS: {msg}\n")
    except Exception:
        pass


def write_status_cache(slug: str, session_count: int, is_new: bool = False) -> None:
    """Write recall status to a cache file that the statusLine wrapper reads.

    The statusLine is Claude Code's built-in status bar. Our wrapper script
    reads this cache and appends recall info to the bar.
    """
    try:
        import json as _json
        cache_path = Path("/tmp/claude-recall-status.json")
        cache_path.write_text(_json.dumps({
            "slug": slug,
            "sessions": session_count,
            "is_new": is_new,
            "ts": datetime.now().isoformat(),
        }))
        debug_log(f"write_status_cache: wrote {cache_path}")
    except Exception as e:
        debug_log(f"write_status_cache: error: {e}")


DEFAULT_CONFIG = {
    "vault_path": "",
    "vault_folder": "claude-recall",
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
    "index.js", "index.ts", "index.py",
    "youtubei.js",
}

NOISE_PATH_FRAGMENTS = {"_next/static", "node_modules", ".next/", "dist/", ".cache/"}


# ── Config & paths ────────────────────────────────────────────────────────────

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
        print(
            f"[claude-recall] Vault not found at {vault}. "
            "Check if the drive is mounted and vault_path in ~/.claude/claude-recall.json.",
            file=sys.stderr,
        )
        sys.exit(0)
    return vault / cfg["vault_folder"]


def cwd_to_slug(cwd: Path) -> str:
    """Convert a project directory path into a short, Obsidian-safe slug.

    /home/sayan/projects/setu          → setu
    /home/sayan/client/acme/dashboard  → acme-dashboard
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


# ── Hook I/O ──────────────────────────────────────────────────────────────────

def read_hook_input() -> dict:
    """Read Claude Code hook JSON from stdin or environment variables."""
    try:
        raw = sys.stdin.read().strip()
        if raw:
            debug_log(f"read_hook_input: got stdin data: {raw[:100]}")
            return json.loads(raw)
    except Exception as e:
        debug_log(f"read_hook_input: stdin error: {e}")

    for key in ["CLAUDE_HOOK_INPUT", "CLAUDE_SESSION_ID", "CLAUDE_CWD", "HOOK_INPUT"]:
        val = os.environ.get(key)
        if val:
            debug_log(f"read_hook_input: found env {key}={val[:50]}")
            try:
                return json.loads(val)
            except Exception:
                pass

    debug_log("read_hook_input: no input found, returning empty dict")
    return {}


def get_cwd(hook_input: dict) -> Path:
    return Path(hook_input.get("cwd") or os.getcwd())


def get_current_prompt(hook_input: dict) -> str:
    """Extract the user's current prompt text from hook input."""
    return hook_input.get("prompt", "").strip()


def now_str(fmt: str = "%Y-%m-%d_%H-%M") -> str:
    return datetime.now().strftime(fmt)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough budget: 4 chars ≈ 1 token. Cuts at a line boundary."""
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    cut = text[:limit].rfind("\n")
    cut = cut if cut > limit // 2 else limit
    return text[:cut] + "\n\n[claude-recall: truncated]"


# ── Claude API ───────────────────────────────────────────────────────────────

def llm_available() -> bool:
    """Check if Claude API is available (Anthropic or NVIDIA NIM)."""
    # Check Anthropic first
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            return True
        except ImportError:
            pass
    # Check NVIDIA NIM (OpenAI-compatible)
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("NVIDIA_NIM_BASE_URL"):
        return True
    return False


def get_anthropic_client():
    """Get Anthropic client or OpenAI client for NVIDIA NIM. Returns None if unavailable."""
    # Try Anthropic first
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            return anthropic.Anthropic()
        except Exception:
            pass
    
    # Try NVIDIA NIM (OpenAI-compatible)
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("NVIDIA_NIM_BASE_URL"):
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=os.environ.get("NVIDIA_NIM_BASE_URL"),
            )
            return client
        except Exception:
            pass
    
    return None


def is_nvidia_nim() -> bool:
    """Check if using NVIDIA NIM instead of Anthropic."""
    return bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("NVIDIA_NIM_BASE_URL"))


# ── File filtering ────────────────────────────────────────────────────────────

def filter_file_paths(files: list[str], cwd: Path | None = None) -> list[str]:
    """Filter out noise from extracted file paths. Normalize to relative."""
    cwd_str = str(cwd) if cwd else ""
    clean = []
    seen = set()

    for f in files:
        # Normalize absolute paths to relative
        if cwd_str and f.startswith(cwd_str):
            f = f[len(cwd_str):].lstrip("/")

        basename = f.rsplit("/", 1)[-1] if "/" in f else f
        if basename in NOISE_FILES:
            continue
        if any(frag in f for frag in NOISE_PATH_FRAGMENTS):
            continue
        if basename.count(".") == 1 and basename.split(".")[0][0].isupper() and basename.endswith(".js"):
            continue

        if f not in seen and f:
            seen.add(f)
            clean.append(f)

    return clean[:15]


# ── Filesystem detection ─────────────────────────────────────────────────────

def detect_project_stack(cwd: Path) -> dict:
    """Detect project stack from filesystem. Returns structured info."""
    info = {
        "type": "unknown",
        "name": "",
        "stack": [],
        "scripts": {},
        "config_files": [],
        "env_keys": [],
    }

    cwd = Path(cwd)
    if not cwd.exists():
        return info

    # Detect config files
    try:
        entries = sorted(cwd.iterdir())
        files = [e.name for e in entries if e.is_file()]
        config_names = {
            "package.json", "tsconfig.json", "next.config.ts", "next.config.js",
            "next.config.mjs", "vite.config.ts", "vite.config.js",
            "tailwind.config.ts", "tailwind.config.js", "postcss.config.js",
            "pubspec.yaml", "requirements.txt", "pyproject.toml", "setup.py",
            "Cargo.toml", "go.mod", "Gemfile", "Makefile", "Dockerfile",
            "docker-compose.yml", "docker-compose.yaml",
            ".env", ".env.local", ".env.example",
        }
        info["config_files"] = [f for f in files if f in config_names]
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

            framework_map = {
                "next": "Next.js", "react": "React", "vue": "Vue.js",
                "svelte": "Svelte", "@angular/core": "Angular",
                "express": "Express.js", "fastify": "Fastify", "hono": "Hono",
                "nuxt": "Nuxt.js", "remix": "Remix", "astro": "Astro", "vite": "Vite",
            }
            for dep, label in framework_map.items():
                if dep in all_deps:
                    version = all_deps[dep].lstrip("^~>=<")
                    major = version.split(".")[0] if version else ""
                    info["stack"].append(f"{label} {major}" if major.isdigit() else label)

            tool_map = {
                "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
                "prisma": "Prisma", "drizzle-orm": "Drizzle ORM",
                "@supabase/supabase-js": "Supabase", "firebase": "Firebase",
                "mongoose": "Mongoose", "stripe": "Stripe", "zod": "Zod",
                "@trpc/server": "tRPC", "playwright": "Playwright",
                "jest": "Jest", "vitest": "Vitest",
            }
            for dep, label in tool_map.items():
                if dep in all_deps:
                    info["stack"].append(label)

            info["type"] = "node"
        except (json.JSONDecodeError, KeyError):
            pass

    # ── Python ──
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
                    "llama-cpp-python": "llama-cpp-python",
                }
                for dep, label in py_deps.items():
                    if dep in text.lower():
                        info["stack"].append(label)
            except Exception:
                pass
            break

    # ── Python by .py files ──
    if info["type"] == "unknown":
        try:
            py_files = list(cwd.glob("*.py")) + list(cwd.glob("scripts/*.py"))
            if py_files:
                info["type"] = "python"
                info["stack"].append("Python")
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
            for dep, label in {"tokio": "Tokio", "actix-web": "Actix", "axum": "Axum",
                               "serde": "Serde", "bevy": "Bevy"}.items():
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

    # ── Flutter ──
    pubspec = cwd / "pubspec.yaml"
    if pubspec.exists():
        info["type"] = "flutter"
        info["stack"].append("Flutter")
        info["stack"].append("Dart")
        try:
            text = pubspec.read_text(encoding="utf-8")
            name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
            if name_match:
                info["name"] = name_match.group(1).strip()
        except Exception:
            pass

    # ── Claude Code Skill ──
    skill_md = cwd / "SKILL.md"
    if skill_md.exists():
        if info["type"] == "unknown":
            info["type"] = "claude-skill"
        info["stack"].append("Claude Code Skill")
        info["name"] = info["name"] or cwd.name
        try:
            text = skill_md.read_text(encoding="utf-8")
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
            break

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

    # Deduplicate stack
    info["stack"] = list(dict.fromkeys(info["stack"]))

    return info


# ── Auto-marker section management ───────────────────────────────────────────

def merge_auto_section(existing_text: str, section_name: str, new_content: str) -> str:
    """Merge auto-generated content into a section of context.md.

    If auto markers exist: replace content between them.
    If section exists but no markers: add markers after header.
    If section doesn't exist: append it.

    User content outside auto markers is NEVER modified.
    """
    auto_start = f"<!-- auto:{section_name}:start -->"
    auto_end = f"<!-- auto:{section_name}:end -->"

    new_block = f"{auto_start}\n{new_content.strip()}\n{auto_end}"

    # Case 1: auto markers already exist
    pattern = re.compile(
        re.escape(auto_start) + r".*?" + re.escape(auto_end),
        re.DOTALL
    )
    if pattern.search(existing_text):
        return pattern.sub(new_block, existing_text)

    # Header map
    header_map = {
        "what_this_is": "## What this is",
        "stack": "## Stack",
        "current_state": "## Current State",
        "key_files": "## Key Files",
        "decisions": "## Decisions",
        "architecture": "## Architecture decisions",
        "gotchas": "## Gotchas",
        "environment": "## Environment",
        "entry_point": "## Run",
    }
    header = header_map.get(section_name, f"## {section_name}")

    # Case 2: section header exists
    header_pattern = re.compile(
        r"(" + re.escape(header) + r"\s*\n)"
        r"(<!-- .+?-->\s*\n)?"
    )
    match = header_pattern.search(existing_text)
    if match:
        insert_pos = match.end()
        return existing_text[:insert_pos] + new_block + "\n" + existing_text[insert_pos:]

    # Case 3: section doesn't exist — append
    return existing_text.rstrip() + f"\n\n{header}\n{new_block}\n"


# ── Index management ─────────────────────────────────────────────────────────

def parse_index_entries(index_path: Path) -> list[dict]:
    """Parse _index.md entries into structured data."""
    entries = []
    if not index_path.exists():
        return entries

    text = index_path.read_text(encoding="utf-8")

    # Table format
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

    return entries


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

    entries.sort(key=lambda e: e.get("last_active", ""), reverse=True)

    rows = []
    for e in entries:
        d = e["directory"]
        display_dir = d if len(d) <= 50 else "..." + d[-47:]
        rows.append(
            f"| [{e['slug']}](projects/{e['slug']}/context) "
            f"| `{display_dir}` "
            f"| {e['sessions']} "
            f"| {e['total_turns']} "
            f"| {e['last_active']} |"
        )

    return header + "\n".join(rows) + "\n"


# ── Compatibility stubs (old Qwen/llama-cpp functions — kept for import safety) ─

def ensure_model() -> bool:
    """Compatibility stub — always returns True (model check removed with Qwen).

    Previously checked if the Qwen GGUF model file was downloaded.
    Now claude -p CLI handles summarization, so no model file is needed.
    """
    return True


def get_model_path() -> Path:
    """Compatibility stub — returns a dummy path (no longer used).

    Previously returned the path to the Qwen GGUF file.
    """
    return Path.home() / ".claude" / "models" / "REMOVED_SEE_CLAUDE_CLI"


def get_llm():
    """Compatibility stub — always returns None (llama-cpp removed).

    Previously returned a cached Llama() instance.
    Now returns None so callers fall back to other methods.
    """
    return None
