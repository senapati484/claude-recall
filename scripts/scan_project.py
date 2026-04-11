"""
scan_project.py — Per-file project intelligence for claude-recall.

Scans the current working directory for source files, runs each through
the local Qwen model, and writes structured summaries to:
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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    load_config, get_project_dir, read_hook_input,
    get_cwd, cwd_to_slug, llm_available, get_model_path,
)

# Source file extensions to scan
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".go",
    ".rs", ".java", ".kt", ".swift", ".rb", ".php",
    ".vue", ".svelte", ".html", ".css", ".scss",
    ".sh", ".bash", ".yaml", ".yml", ".toml", ".json",
}

# Directories to always skip
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".dart_tool", "build", "dist", ".next", ".nuxt",
    "coverage", ".pytest_cache", ".mypy_cache", "target",
    "vendor", "pods", ".gradle", "android/build",
}

# Max file size to read (bytes) — skip huge generated files
MAX_FILE_BYTES = 40_000

_FILE_PROMPT = """Analyse this source file and respond ONLY with valid JSON:

File: {filename}
Content:
{content}

Output exactly this JSON:
{{
  "purpose": "one sentence: what this file does",
  "exports": ["function or class name 1", "function or class name 2"],
  "depends_on": ["imported module or file 1", "imported module or file 2"],
  "keywords": ["tag1", "tag2", "tag3"]
}}

Be concise. exports and depends_on: list only the most important 3-5 items each.
"""


def collect_files(root: Path) -> list[Path]:
    """Walk root, return source files respecting SKIP_DIRS and MAX_FILE_BYTES."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place so os.walk doesn't descend into them
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


def summarise_file(llm, filepath: Path, root: Path) -> dict | None:
    """Ask the LLM to summarise a single source file. Returns dict or None."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        # Trim to ~100 lines to keep context usage sane
        lines = content.splitlines()[:120]
        content_trimmed = "\n".join(lines)
        rel = str(filepath.relative_to(root))

        prompt = _FILE_PROMPT.format(filename=rel, content=content_trimmed)
        response = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.1,
        )
        raw = response["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        result["file"] = rel
        return result
    except Exception as exc:
        print(f"[scan] skipped {filepath.name}: {exc}", file=sys.stderr)
        return None


def scan_project() -> None:
    if not llm_available():
        print(
            "[claude-recall] scan_project: model not found at "
            f"{get_model_path()}\n"
            "Run install.sh to download it.",
            file=sys.stderr,
        )
        sys.exit(1)

    cwd = Path(os.getcwd())
    cfg = load_config()
    slug = cwd_to_slug(cwd)
    project_dir = get_project_dir(cfg, slug)
    project_dir.mkdir(parents=True, exist_ok=True)

    index_path = project_dir / "file-index.json"

    # Load existing index + mtime cache
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

    from llama_cpp import Llama
    llm = Llama(
        model_path=str(get_model_path()),
        n_ctx=8192,
        n_threads=4,
        n_gpu_layers=0,
        verbose=False,
    )

    updated = dict(existing)
    new_mtimes = dict(mtimes)
    processed = 0

    for fp in files:
        rel = str(fp.relative_to(cwd))
        try:
            mtime = str(fp.stat().st_mtime)
        except OSError:
            continue

        # Skip if file hasn't changed since last scan
        if mtimes.get(rel) == mtime and rel in existing:
            continue

        summary = summarise_file(llm, fp, cwd)
        if summary:
            updated[rel] = summary
            new_mtimes[rel] = mtime
            processed += 1
            print(f"  + {rel}")

    # Write back with mtime cache
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