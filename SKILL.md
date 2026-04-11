---
name: claude-recall
description: >
  Always-on Claude Code skill that bridges Claude with an Obsidian vault for persistent
  project memory. Automatically loads project context from Obsidian before every prompt
  (UserPromptSubmit hook) and saves a structured session note back to the vault on exit
  (Stop hook). Zero manual invocation — hooks fire on every session automatically.

  Storage: <vault>/claude-recall/projects/<project-slug>/context.md (human-edited in
  Obsidian) and sessions/YYYY-MM-DD_HH-MM.md (auto-written). Project slug is derived
  from the directory Claude Code was launched in.

  Install from GitHub (one command):
    curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash

  Consult this skill for: install help, Obsidian vault path issues, hooks not firing,
  context not loading, sessions not saving, editing context.md, vault folder structure,
  slug mapping questions, config options, uninstalling.
---

# claude-recall

Obsidian-backed persistent memory for Claude Code. Install once, works on every session.

## How It Works

```
Session start  →  load_context.py  →  reads Obsidian vault  →  stdout → Claude context
Session exit   →  save_context.py  →  writes session note   →  Obsidian vault
```

Both scripts run via Claude Code hooks in `~/.claude/settings.json`. No invocation needed.

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
    ├── _index.md                    ← running log of all projects (auto-updated)
    └── projects/
        └── <project-slug>/
            ├── context.md           ← YOU edit this in Obsidian
            └── sessions/
                └── YYYY-MM-DD_HH-MM.md   ← auto-written on exit
```

**`context.md`** is the permanent memory file — open it in Obsidian and fill in your
stack, architecture decisions, gotchas, current state. Claude reads it before your
first message every session.

**Project slug** is derived from the directory you launched `claude` in:
`/home/sayan/projects/setu` → `setu`, `/home/sayan/client/acme` → `client-acme`

---

## Scripts

- `scripts/load_context.py` — `UserPromptSubmit` hook. Reads `context.md` + last N session
  notes from the vault, prints them to stdout. See `references/hook-api.md`.
- `scripts/save_context.py` — `Stop` hook. Parses session transcript, writes session note
  and scaffolds `context.md` for new projects. See `references/context-structure.md`.
- `scripts/utils.py` — shared helpers: config loading, vault path resolution, slug generation,
  token truncation, hook I/O.

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
| Hook errors | Run `echo '{"cwd":"'$(pwd)'","session_id":"t"}' \| python3 ~/.claude/skills/claude-recall/scripts/load_context.py` |
| Re-install / update | Re-run the `curl` install command — detects existing install |

---

## When Helping the User

- **First-time setup**: walk through install → vault path → restart → test with a prompt
- **Context not appearing**: check hook registration → run load script manually → open vault
- **Editing context**: guide to `<vault>/claude-recall/projects/<slug>/context.md` in Obsidian
- **Team usage**: commit `context.md` to git, ignore `sessions/`; team shares Claude memory
