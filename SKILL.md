---
name: claude-recall
description: >
  Always-on Claude Code skill that bridges Claude with an Obsidian vault for persistent
  project memory. Automatically loads project context from Obsidian before every session
  (UserPromptSubmit hook) and saves a structured session note back to the vault on exit
  (Stop hook). Zero manual invocation — hooks fire automatically on each session.

  Context is AUTO-GENERATED using a local LLM (Qwen2.5 0.5B GGUF via llama-cpp-python).
  Claude analyzes transcripts and project files to populate context.md with stack,
  architecture decisions, gotchas, and current state. Users never need to manually edit.
  Auto-generated sections are marked with `<!-- auto:*:start/end -->` markers.
  User-written content outside these markers is never overwritten.

  The /recall command (full path required — not a native slash command) lets users trigger on-demand context updates from the terminal.

  Storage: <vault>/claude-recall/projects/<project-slug>/context.md (auto-populated)
  and sessions/YYYY-MM-DD_HH-MM.md (auto-written). Project slug is derived from
  the directory Claude Code was launched in.

  Install from GitHub (one command):
    curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash

  Consult this skill for: install help, Obsidian vault path issues, hooks not firing,
  context not loading, sessions not saving, editing context.md, vault folder structure,
  slug mapping questions, config options, uninstalling, /recall commands.
---

# claude-recall

Obsidian-backed persistent memory for Claude Code. Install once, works on every session.
**All context is auto-generated** using a local LLM — no manual Obsidian editing required.

## Session Model

A **session = one terminal session** (open terminal → close terminal or `/exit`).
This is NOT per-prompt. The session marker at `~/.claude/.recall_<session_id>` prevents
context re-injection after the first prompt of a session.

```
Terminal session opens → UserPromptSubmit fires → context loaded ONCE
Terminal session continues → UserPromptSubmit fires → SKIPPED (marker exists)
Terminal session closes → Stop hook fires → session note written, context updated
```

## How It Works

```
Session start  →  load_context.py  →  reads vault  →  stdout → Claude context
Session exit   →  save_context.py  →  LLM analyzes transcript → updates context.md + session note
/recall        →  recall_update.py →  LLM scans project → updates context.md
```

Both hooks run via Claude Code hooks in `~/.claude/settings.json`. No invocation needed.
The `/recall` command is invoked directly by the user during a Claude session.

## LLM Model

claude-recall uses **Qwen2.5 0.5B Instruct GGUF** (~380 MB) run locally via llama-cpp-python.
The model is auto-downloaded on first use if missing, or during install.sh.

- Model location: `~/.claude/models/qwen2.5-0.5b-instruct-q4_k_m.gguf`
- If model is missing at runtime: auto-downloaded from HuggingFace (~380 MB)
- If download fails: gracefully falls back to filesystem-only context
- LLM is used for:
  - Session summarization (what was done, decisions, next steps)
  - Project context generation (via /recall update)
  - Enhanced context.md updates after each session

---

## /recall Command

To manually refresh project context during a session, **type the full command**:
```
/Users/sayansenapati/.claude/skills/claude-recall/scripts/recall_update.py update
```

Or from any project directory, run:
```
python3 ~/.claude/skills/claude-recall/scripts/recall_update.py update
```

Available actions:
- `update` — Scan project with LLM and regenerate context.md
- `status` — Show current context.md content
- `reset` — Delete context.md and regenerate from scratch

**Note**: `/recall` is not a native Claude Code command. You must use the full path above.
The hint shown in Claude's context will remind you of this each session.

## Quick Reference

| What you type | What happens |
|---|---|
| Full path above | Runs /recall update — LLM regenerates context.md |
| (context loads automatically) | Happens on first prompt of every session |

---

## Auto-Context Generation

Claude automatically generates and updates `context.md` content:

- **On first session load**: Analyzes transcript + scans project files to auto-populate context
- **On every session end**: LLM analyzes full transcript → merges new learnings into context.md
- **On `/recall update`**: LLM scans filesystem for stack, structure, and README → updates context.md

### Auto-marker system
Auto-generated content is wrapped in markers:
```markdown
## Stack
<!-- auto:stack:start -->
Next.js 16 · Tailwind CSS · TypeScript
<!-- auto:stack:end -->
```

- **Inside markers**: Managed by claude-recall, updated automatically
- **Outside markers**: User-owned, NEVER modified by auto-updates

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

The installer:
1. Checks Python 3 + Claude Code are present
2. Asks for your Obsidian vault path (once — saved to `~/.claude/claude-recall.json`)
3. Clones the repo to `~/.claude/skills/claude-recall/`
4. Installs `llama-cpp-python` via pip (if not present)
5. Downloads Qwen2.5 0.5B GGUF model to `~/.claude/models/` (~380 MB)
6. Registers `UserPromptSubmit` and `Stop` hooks in `~/.claude/settings.json`
7. Creates `<vault>/claude-recall/` folder structure

**Then restart Claude Code.**

---

## Obsidian Vault Structure

```
<your-vault>/
└── claude-recall/
    ├── _index.md                    ← deduplicated project table (auto-updated)
    └── projects/
        └── <project-slug>/
            ├── context.md           ← auto-populated + user can edit
            └── sessions/
                └── YYYY-MM-DD_HH-MM.md   ← auto-written on exit
```

**`context.md`** is the permanent memory file — auto-populated with project stack,
architecture decisions, gotchas, and current state. Users can add their own notes
outside the auto-markers. Claude reads it before your first message every session.

**`_index.md`** shows each project ONCE with accumulated stats:
```
| Project | Directory | Sessions | Total Turns | Last Active |
|---------|-----------|----------|-------------|-------------|
| setu    | `/home/.../setu` | 5 | 127 | 2025-01-16 14:07 |
```

**Project slug** is derived from the directory you launched `claude` in:
`/home/sayan/projects/setu` → `setu`, `/home/sayan/client/acme` → `client-acme`

---

## Scripts

- `scripts/load_context.py` — `UserPromptSubmit` hook. Fires once per session.
  - Session deduplication via `~/.claude/.recall_<session_id>` marker
  - Auto-generates context.md on first load if missing
  - Loads context.md + last N session notes → stdout for Claude
- `scripts/save_context.py` — `Stop` hook. Fires once per terminal session.
  - Parses full transcript (all messages, tool calls, errors, file ops)
  - Calls LLM to generate structured summary (summary, decisions, files, next_steps)
  - Updates context.md auto-sections with session learnings
  - Writes session note to sessions/YYYY-MM-DD_HH-MM.md
  - Updates _index.md
- `scripts/summarize.py` — LLM session summariser.
  - Loads Qwen GGUF via llama-cpp-python
  - Auto-downloads model if missing
  - Returns structured JSON: summary, decisions, files_and_roles, next_steps, keywords
- `scripts/recall_update.py` — `/recall` command.
  - `update`: LLM generates rich project context from README + filesystem
  - `status`: prints current context.md
  - `reset`: regenerates context.md from scratch
- `scripts/utils.py` — shared helpers:
  - `ensure_model()` — auto-downloads model from HuggingFace if missing
  - `llm_available()` — checks model + llama_cpp import
  - `auto_generate_context_md()` — filesystem-only context scaffold
  - `merge_auto_section()` — preserves user content outside auto-markers
  - `detect_project_stack()` — scans package.json, requirements.txt, etc.
  - `generate_file_tree()` — project tree respecting .gitignore patterns

---

## Config (`~/.claude/claude-recall.json`)

```json
{
  "vault_path": "/path/to/your/vault",
  "vault_folder": "claude-recall",
  "max_context_tokens": 2000,
  "include_recent_sessions": 2,
  "save_sessions": true,
  "load_on_every_prompt": false
}
```

| Key | Default | Description |
|---|---|---|
| `vault_path` | required | Absolute path to your Obsidian vault |
| `vault_folder` | `claude-recall` | Folder name created inside the vault |
| `max_context_tokens` | `2000` | Token budget for injected context |
| `include_recent_sessions` | `2` | How many past session notes to load |
| `load_on_every_prompt` | `false` | Reload context on every prompt (expensive) |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Context not loading | Check hooks in `~/.claude/settings.json`; restart Claude Code |
| "Vault not found" error | Check vault_path in config; verify drive is mounted |
| Sessions not saving | Verify vault is writable; check `save_sessions: true` in config |
| Wrong project loaded | Launch `claude` from the correct project directory |
| Context.md is empty | Run `/recall update` to populate with LLM |
| LLM not generating | Check `~/.claude/models/qwen2.5-0.5b-instruct-q4_k_m.gguf` exists |
| Hook errors | Run `echo '{"cwd":"'$(pwd)'","session_id":"t"}' \| python3 ~/.claude/skills/claude-recall/scripts/load_context.py` |
| Re-install / update | Re-run the `curl` install command — detects existing install |

---

## When Helping the User

- **First-time setup**: walk through install → vault path → restart → test with a prompt
- **Context not appearing**: check hook registration → run load script manually → open vault
- **Editing context**: guide to `<vault>/claude-recall/projects/<slug>/context.md` in Obsidian
  - Auto-generated sections are inside `<!-- auto:*:start/end -->` markers
  - User can add their own content anywhere outside markers
- **LLM model missing**: explain auto-download or manual download from HuggingFace
- **Token savings**: explain that pre-loaded context avoids repeated codebase discovery
- **/recall commands**: run `recall_update.py` when user asks for context refresh
