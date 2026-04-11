<p align="center">
  <img src="data/claude.svg" width="80" alt="Claude">&nbsp;&nbsp;&nbsp;
  <b style="font-size: 28px;">×</b>&nbsp;&nbsp;&nbsp;
  <img src="data/obsidian.png" width="80" alt="Obsidian">
</p>

<h1 align="center">claude-recall</h1>

<p align="center">
  <em>Persistent Obsidian memory for Claude Code</em><br>
  <em>Install once. Works on every session. Zero config.</em>
</p>

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/install-one_command-D97757?style=for-the-badge&logo=gnubash&logoColor=white" alt="Install"></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/hooks-automatic-7C3AED?style=for-the-badge&logo=obsidian&logoColor=white" alt="Automatic Hooks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="MIT License"></a>
</p>

---

## The Problem

Claude Code has no memory between sessions. Every time you start a new conversation, Claude forgets your project's stack, architecture decisions, gotchas, and what you worked on yesterday. You end up repeating the same context over and over.

## The Solution

**claude-recall** hooks into Claude Code and bridges it with your Obsidian vault — automatically.

- 🔵 **Before your first message** → loads your project context from Obsidian
- 🟠 **When you exit** → saves a structured session note back to the vault

No manual invocation. No config beyond your vault path. No files created in your project directory.

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

---

## How it works

<table>
<tr>
<td width="50%">

### 🔵 On Session Start

The `UserPromptSubmit` hook fires before your first message:

1. Reads `context.md` from your vault
2. Loads the last 2 session notes
3. Injects everything into Claude's system context

Claude starts every session **already knowing** your project.

</td>
<td width="50%">

### 🟠 On Session End

The `Stop` hook fires when you exit:

1. Reads the session transcript
2. Extracts files mentioned, first prompt, stats
3. Writes a dated Markdown note to the vault

Your work is **automatically documented** in Obsidian.

</td>
</tr>
</table>

**Project slug** is derived from your working directory —
`~/projects/setu` → `setu` · `~/client/acme/dashboard` → `acme-dashboard`

---

## What gets created in Obsidian

```
your-vault/
└── claude-recall/
    ├── _index.md                         ← auto-updated project log
    └── projects/
        └── setu/
            ├── context.md                ← ✏️  you edit this in Obsidian
            └── sessions/
                ├── 2026-04-10_14-30.md   ← auto-written
                └── 2026-04-11_09-15.md   ← auto-written
```

### `context.md` — your permanent memory

Open it in Obsidian and fill in your stack, architecture decisions, gotchas, current state — anything Claude should always know. It ships with a ready-to-fill template:

```markdown
## What this is
A blood donation platform connecting donors with blood banks.

## Stack
Flutter · Express.js · MongoDB Atlas · Railway

## Architecture decisions
- JWT auth with refresh tokens stored in secure storage
- Image uploads compressed client-side before S3

## Gotchas
- Railway free tier has 500MB memory limit
- MongoDB Atlas M0 caps at 500 connections
```

### Session notes — automatic breadcrumbs

Each session note includes metadata as YAML frontmatter, making them searchable in Obsidian with Dataview:

```markdown
---
date: 2026-04-11
project: setu
turns: 8
tags: [claude-recall, session]
---

# Session 2026-04-11 14:30

## Started with
> Add JWT auth to the Express routes

## Files mentioned
- `server/auth.js`
- `routes/api.js`
- `lib/screens/home_screen.dart`
```

---

## Install

**Requirements:** Python 3.8+ · Claude Code · Obsidian (with a vault created)

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

The installer:
1. Asks for your Obsidian vault path (once)
2. Saves config to `~/.claude/claude-recall.json`
3. Clones this repo to `~/.claude/skills/claude-recall/`
4. Registers both hooks in `~/.claude/settings.json`
5. Creates the vault folder skeleton

**Restart Claude Code after install.**

<details>
<summary><strong>Manual install (no curl)</strong></summary>

```bash
git clone https://github.com/senapati484/claude-recall ~/.claude/skills/claude-recall
bash ~/.claude/skills/claude-recall/install.sh
```

</details>

---

## Config

`~/.claude/claude-recall.json` — written by the installer, edit to override:

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

| Key | Default | What it does |
|:---|:---|:---|
| `vault_path` | _(required)_ | Absolute path to your Obsidian vault |
| `vault_folder` | `claude-recall` | Folder inside the vault for all notes |
| `max_context_tokens` | `2000` | Token budget for injected context (~8K chars) |
| `include_recent_sessions` | `2` | How many past session notes to load |
| `save_sessions` | `true` | Write session notes on exit |
| `load_on_every_prompt` | `false` | Reload context on every prompt (not just first) |

---

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

Re-running the installer detects an existing install and runs `git pull` instead of a fresh clone.

---

## Uninstall

```bash
# 1. Remove hooks from settings
# Edit ~/.claude/settings.json and remove the two "claude-recall" entries

# 2. Remove skill files
rm -rf ~/.claude/skills/claude-recall

# 3. Remove config
rm ~/.claude/claude-recall.json
```

> Your Obsidian notes under `<vault>/claude-recall/` are **never touched** by uninstall.

---

## Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Claude Code    │     │  claude-recall    │     │  Obsidian Vault  │
│                  │     │                   │     │                  │
│  UserPromptSubmit├────►│  load_context.py  ├────►│  context.md      │
│                  │     │                   │◄────┤  sessions/*.md   │
│  Stop            ├────►│  save_context.py  ├────►│                  │
│                  │     │                   │     │                  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
                              │
                              ▼
                    ~/.claude/claude-recall.json
```

---

## File reference

| File | Purpose |
|:---|:---|
| `install.sh` | One-command GitHub installer |
| `SKILL.md` | Claude skill metadata and instructions |
| `scripts/load_context.py` | `UserPromptSubmit` hook — loads context from Obsidian |
| `scripts/save_context.py` | `Stop` hook — saves session note to Obsidian |
| `scripts/utils.py` | Shared helpers (config, slugs, truncation) |
| `references/hook-api.md` | Claude Code hook I/O specification |
| `references/context-structure.md` | Vault note formats and examples |

---

## Troubleshooting

<details>
<summary><strong>Claude isn't loading my context</strong></summary>

Test the load hook manually:

```bash
echo '{"cwd":"'$(pwd)'","session_id":"test"}' | python3 ~/.claude/skills/claude-recall/scripts/load_context.py
```

If output is empty, check:
- Does `context.md` exist in your vault for this project?
- Is your vault path correct in `~/.claude/claude-recall.json`?

</details>

<details>
<summary><strong>Session notes aren't being saved</strong></summary>

Check `save_sessions` is `true` in `~/.claude/claude-recall.json` and that Claude Code is passing a transcript path to the Stop hook.

</details>

<details>
<summary><strong>Wrong project slug</strong></summary>

The slug is derived by stripping noise segments (`projects`, `repos`, `code`, `src`, `workspace`, `dev`, `work`, `home`) from your directory path and keeping the last 2 meaningful segments. Check with:

```bash
python3 -c "
from pathlib import Path
import sys; sys.path.insert(0, '$HOME/.claude/skills/claude-recall/scripts')
from utils import cwd_to_slug
print(cwd_to_slug(Path('$(pwd)')))
"
```

</details>

---

<p align="center">
  <sub>Built with 🧠 by <a href="https://github.com/senapati484">senapati484</a></sub>
</p>
