---
name: claude-recall
description: >
  Always-on Claude Code skill that bridges Claude with an Obsidian vault for persistent
  project memory. Automatically loads project context from Obsidian before every prompt
  (UserPromptSubmit hook) and saves a structured session note back to the vault on exit
  (Stop hook). Zero manual invocation — hooks fire on every session automatically.

  Context is AUTO-GENERATED — Claude analyzes transcripts and project files to populate
  context.md with stack, architecture decisions, gotchas, and current state. Users never
  need to manually edit files. Auto-generated sections are marked with `<!-- auto:*:start/end -->`
  markers. User-written content outside these markers is never overwritten.

  The /recall command lets users trigger on-demand context updates from the terminal.

  Storage: <vault>/claude-recall/projects/<project-slug>/context.md (auto-populated,
  user can edit) and sessions/YYYY-MM-DD_HH-MM.md (auto-written). Project slug is derived
  from the directory Claude Code was launched in.

  Install from GitHub (one command):
    curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash

  Consult this skill for: install help, Obsidian vault path issues, hooks not firing,
  context not loading, sessions not saving, editing context.md, vault folder structure,
  slug mapping questions, config options, uninstalling, /recall commands.
---

# claude-recall

Obsidian-backed persistent memory for Claude Code. Install once, works on every session.
**All context is auto-generated** — no manual Obsidian editing required.

## How It Works

```
Session start  →  load_context.py  →  reads Obsidian vault  →  stdout → Claude context
Session exit   →  save_context.py  →  analyzes transcript   →  updates context.md + writes session note
On /recall     →  recall_update.py →  scans project files   →  updates context.md
```

Both hooks run via Claude Code hooks in `~/.claude/settings.json`. No invocation needed.
The `/recall` command is invoked directly by the user during a Claude session.

---

## /recall Commands

These commands can be used during any Claude Code session:

| Command | What it does |
|---------|-------------|
| `/recall` or `/recall update` | Scan current project directory and update context.md with detected stack, structure, git info |
| `/recall status` | Show what claude-recall knows about this project (context.md content, session count, index entry) |
| `/recall reset` | Regenerate context.md from scratch (backs up old file, preserves sessions) |

**When Claude sees these commands, run:**
```bash
python3 ~/.claude/skills/claude-recall/scripts/recall_update.py <action> <cwd>
```

Where `<action>` is `update`, `status`, or `reset`, and `<cwd>` is the current working directory.

---

## Auto-Context Generation

Claude automatically generates and updates `context.md` content — **users never need to manually edit in Obsidian** (but they can):

- **On first session**: Analyzes transcript + scans project files (package.json, etc.) to auto-populate context
- **On every session end**: Merges new learnings — stack changes, architecture decisions, gotchas
- **On `/recall update`**: Scans filesystem for package.json/pubspec.yaml/etc., git info, env vars

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

### Token savings
Without claude-recall, every new session requires Claude to re-discover the project:
- What framework? What's the file structure? What was I working on?
- This burns 500–2000+ tokens on repeated exploration

With claude-recall, this context is pre-loaded in ~200 tokens, saving significant token usage on every session.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

The installer:
1. Checks Python 3 + Claude Code are present
2. Asks for your Obsidian vault path (once — saved to `~/.claude/claude-recall.json`)
3. Clones the repo to `~/.claude/skills/claude-recall/`
4. Registers `UserPromptSubmit` and `Stop` hooks in `~/.claude/settings.json`
5. Creates `<vault>/claude-recall/` folder structure

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

- `scripts/load_context.py` — `UserPromptSubmit` hook. Reads `context.md` + last N session
  notes from the vault, prints them to stdout. Shows `/recall` command hints.
- `scripts/save_context.py` — `Stop` hook. Parses session transcript, extracts context
  (stack, decisions, gotchas), auto-updates `context.md`, writes session note, updates `_index.md`.
- `scripts/recall_update.py` — `/recall` command. Scans project filesystem to detect stack,
  structure, and config. Updates context.md with findings.
- `scripts/utils.py` — shared helpers: config loading, vault path resolution, slug generation,
  token truncation, hook I/O, stack detection, auto-marker merging, index dedup.

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
| Context.md is empty | Run `/recall update` to populate from project files |
| Hook errors | Run `echo '{"cwd":"'$(pwd)'","session_id":"t"}' \| python3 ~/.claude/skills/claude-recall/scripts/load_context.py` |
| Re-install / update | Re-run the `curl` install command — detects existing install |

---

## When Helping the User

- **First-time setup**: walk through install → vault path → restart → test with a prompt
- **Context not appearing**: check hook registration → run load script manually → open vault
- **Editing context**: guide to `<vault>/claude-recall/projects/<slug>/context.md` in Obsidian
  - Auto-generated sections are inside `<!-- auto:*:start/end -->` markers
  - User can add their own content anywhere outside markers
- **Team usage**: commit `context.md` to git, ignore `sessions/`; team shares Claude memory
- **Token savings**: explain that pre-loaded context avoids repeated codebase discovery
- **/recall commands**: run `recall_update.py` when user asks for context refresh
