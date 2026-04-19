"""
scan_project.py — Per-file project intelligence for claude-recall.

Scans the current working directory for source files, runs each through
claude -p CLI, and writes structured summaries to:
  <vault>/claude-recall/projects/<slug>/file-index.json

Incremental: re-processes only files whose mtime changed since last run.
Cache stored in file-index.json itself under a "_cache_mtimes" key.

Usage:
  python3 ~/.claude/skills/claude-recall/scripts/scan_project.py
  (run from inside your project directory)
"""

import json
import os
import sys
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_project_dir,
    cwd_to_slug, llm_available,
)

SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".go",
    ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".vue", ".svelte", ".html", ".css", ".scss",
    ".sh", ".bash", ".yaml", ".yml", ".toml", ".json",
}

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".dart_tool", "build", "dist", ".next", ".nuxt",
    "coverage", ".pytest_cache", ".mypy_cache", "target",
    "vendor", "pods", ".gradle", "android/build",
}

MAX_FILE_BYTES = 40_000


def collect_files(root: Path) -> list[Path]:
    """Walk root, return source files respecting SKIP_DIRS and MAX_FILE_BYTES."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.suffix in SOURCE_EXTENSIONS:
                try:
                    if fp.stat().st_size <= MAX_FILE_BYTES:
                        result.append(fp)
                except OSError:
                    pass
    return result


def summarise_file_with_cli(filepath: Path, root: Path) -> dict | None:
    """Summarize a source file using claude -p CLI."""
    if not shutil.which("claude"):
        return None

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()[:80]
        content_trimmed = "\n".join(lines)
        rel = str(filepath.relative_to(root))

        prompt = f"""Analyze this source file and output ONLY valid JSON, no markdown:

File: {rel}
Content:
{content_trimmed}

Output this exact JSON structure:
{{"purpose": "one sentence what this file does", "exports": ["name1", "name2"], "depends_on": ["module1", "module2"], "keywords": ["tag1", "tag2", "tag3"]}}"""

        result = subprocess.run(
            ["claude", "-p", "--bare", "--dangerously-skip-permissions",
             "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=20,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        raw = result.stdout.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        data["file"] = rel
        return data
    except Exception as exc:
        print(f"[scan] skipped {filepath.name}: {exc}", file=sys.stderr)
        return None


def scan_project() -> None:
    if not llm_available():
        print(
            "[claude-recall] scan_project: claude CLI not found. Install Claude Code.",
            file=sys.stderr,
        )
        sys.exit(1)

    cwd = Path(os.getcwd())
    cfg = load_config()
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)

    index_path = project_dir / "file-index.json"

    existing: dict = {}
    mtimes: dict = {}
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text())
            mtimes = data.pop("_cache_mtimes", {})
            existing = data
        except Exception:
            pass

    files = collect_files(cwd)
    print(f"[claude-recall] scan_project: found {len(files)} source files in {cwd}")

    updated = dict(existing)
    new_mtimes = dict(mtimes)
    processed = 0

    for fp in files:
        rel = str(fp.relative_to(cwd))
        try:
            mtime = str(fp.stat().st_mtime)
        except OSError:
            continue

        if mtimes.get(rel) == mtime and rel in existing:
            continue

        summary = summarise_file_with_cli(fp, cwd)
        if summary:
            updated[rel] = summary
            new_mtimes[rel] = mtime
            processed += 1
            print(f"  + {rel}")

    output = dict(updated)
    output["_cache_mtimes"] = new_mtimes
    index_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(
        f"[claude-recall] scan complete: {processed} files updated, "
        f"{len(updated)} total in index -> {index_path}"
    )


if __name__ == "__main__":
    try:
        scan_project()
    except KeyboardInterrupt:
        print("\n[claude-recall] scan cancelled.")
    except Exception as exc:
        print(f"[claude-recall] scan_project error: {exc}", file=sys.stderr)
        sys.exit(1)