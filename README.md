<p align="center">
  <a href="https://github.com/senapati484/claude-recall"><img src="data/claude.svg" width="70" alt="Claude" align="absmiddle"></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <img src="data/plus.svg" width="32" alt="+" align="absmiddle">
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://github.com/senapati484/claude-recall"><img src="data/obsidian.png" width="70" alt="Obsidian" align="absmiddle"></a>
</p>

<h1 align="center">claude-recall</h1>

<p align="center">
  <strong>Persistent Obsidian memory for Claude Code</strong><br>
  <sub>Install once · Works every session · Zero config · Zero project pollution</sub>
</p>

<p align="center">
  <a href="#-install"><img src="https://img.shields.io/badge/install-one_command-D97757?style=for-the-badge&logo=gnubash&logoColor=white" alt="Install"></a>&nbsp;
  <a href="#-how-it-works"><img src="https://img.shields.io/badge/hooks-automatic-7C3AED?style=for-the-badge&logo=obsidian&logoColor=white" alt="Automatic"></a>&nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge" alt="License"></a>
</p>

<br>

## 💡 The Problem

Claude Code has **no memory between sessions**. Every time you start a new conversation, Claude forgets your project's stack, architecture decisions, gotchas, and what you worked on yesterday.

You end up repeating the same context over and over.

## ✅ The Solution

**claude-recall** hooks into Claude Code and bridges it with your **Obsidian vault** — completely automatically.

| | Hook | What happens |
|:--|:--|:--|
| 🔵 | **Before your first message** | Loads your project context from Obsidian |
| 🟠 | **When you exit** | Saves a structured session note back to the vault |

No manual invocation. No config beyond your vault path. **No files created in your project directory.**

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

---

## 🔁 How it works

### 🔵 On Session Start — `UserPromptSubmit` hook

1. Reads `context.md` from your vault
2. Loads the last 2 session notes
3. Injects everything into Claude's system context

> Claude starts every session **already knowing** your project.
> Context is loaded **once per terminal session** — not on every prompt.

### 🟠 On Session End — `Stop` hook

1. Reads the session transcript (all messages)
2. LLM analyzes the full transcript → generates summary, decisions, next steps
3. Updates `context.md` auto-sections with session learnings
4. Writes a dated session note to the vault

> Your work is **automatically documented** in Obsidian.
> Uses **claude CLI** under the hood — no API keys required.

**Project slug** is derived from your working directory:

| Path | Slug |
|:--|:--|
| `~/projects/setu` | `setu` |
| `~/client/acme/dashboard` | `acme-dashboard` |
| `~/Desktop/Dev/Innovation/setu` | `innovation-setu` |

---

## 📁 What gets created in Obsidian

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

### `context.md` — auto-generated project memory

`context.md` is **auto-generated from project scan + optionally AI-enhanced via claude CLI** on first load and updated after each session.
You can edit it in Obsidian — your content outside `<!-- auto:* -->` markers is never overwritten:

```markdown
## What this is
<!-- auto:what_this_is:start -->
Project: setu — blood donation platform
<!-- auto:what_this_is:end -->

## Stack
<!-- auto:stack:start -->
Flutter · Express.js · MongoDB Atlas · Railway
<!-- auto:stack:end -->

## Key files
<!-- auto:key_files:start -->
- `lib/auth/jwt_handler.dart`
- `server/routes/auth.ts`
- `lib/screens/donor_screen.dart`
<!-- auto:key_files:end -->

## Architecture decisions
<!-- auto:architecture:start -->
- JWT auth with refresh tokens stored in secure storage
- Image uploads compressed client-side before S3
<!-- auto:architecture:end -->

## Gotchas
<!-- auto:gotchas:start -->
- Railway free tier has 500MB memory limit
- MongoDB Atlas M0 caps at 500 connections
<!-- auto:gotchas:end -->
```

### Session notes — automatic breadcrumbs

Each session note includes YAML frontmatter, making them searchable with Obsidian Dataview:

```yaml
---
date: 2026-04-11
project: setu
turns: 8
tags: [claude-recall, session]
---
```

```markdown
# Session 2026-04-11 14:30

## Started with
> Add JWT auth to the Express routes

## Stats
8 user turns · 12 total messages · 5 tool calls

## Summary
Started with: Add JWT auth... · 3 file(s) modified · Tools used: Readx2, Editx2, Bashx1

## Files touched
- server/auth.js
- routes/api.js
- lib/screens/home_screen.dart

## Tools used
- `Read`: 2x
- `Edit`: 2x
- `Bash`: 1x

## Git changes
```
server/auth.js   |  25 +++++++++
routes/api.js    |  10 ++++
2 files changed, 35 insertions(+)
```

## Next steps
- [ ] _(edit in Obsidian or ask Claude to summarise)_
```

---

## 📦 Install

> **Requirements:** Python 3.8+ · Claude Code · Obsidian

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

**What the installer does:**

1. Asks for your Obsidian vault path (once)
2. Saves config to `~/.claude/claude-recall.json`
3. Clones this repo to `~/.claude/skills/claude-recall/`
4. Installs `anthropic` and `fastmcp` via pip
5. Registers hooks + MCP server in `~/.claude/settings.json`
6. Creates the vault folder skeleton

**⚠️ Restart Claude Code after install.**

<details>
<summary><strong>Manual install (no curl)</strong></summary>

```bash
git clone https://github.com/senapati484/claude-recall ~/.claude/skills/claude-recall
bash ~/.claude/skills/claude-recall/install.sh
```

</details>

---

## ⚙️ Config

Edit `~/.claude/claude-recall.json` to override defaults:

```json
{
  "vault_path": "/path/to/your/vault",
  "vault_folder": "claude-recall",
  "max_context_tokens": 400,
  "include_recent_sessions": 2,
  "save_sessions": true,
  "load_on_every_prompt": true,
  "use_claude_api": true
}
```

| Key | Default | What it does |
|:--|:--|:--|
| `vault_path` | _(required)_ | Absolute path to your Obsidian vault |
| `vault_folder` | `claude-recall` | Folder inside the vault for all notes |
| `max_context_tokens` | `400` | Token budget for injected context (~1.6K chars) |
| `include_recent_sessions` | `2` | How many past session notes to load |
| `save_sessions` | `true` | Write session notes on exit |
| `load_on_every_prompt` | `true` | Reload relevant context on every prompt |
| `use_claude_api` | `true` | Use Claude API for summarization |

---

## 🔄 Update

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

Re-running the installer detects an existing install and runs `git pull`.

---

## 🗑️ Uninstall

```bash
# 1. Remove hooks from settings
#    Edit ~/.claude/settings.json — delete the two "claude-recall" entries

# 2. Remove skill files
rm -rf ~/.claude/skills/claude-recall

# 3. Remove config
rm ~/.claude/claude-recall.json
```

> Your Obsidian notes under `<vault>/claude-recall/` are **never touched** by uninstall.

---

## 🏗️ Architecture

```
┌──────────────┐       ┌──────────────────┐       ┌────────────────┐
│  Claude Code │       │  claude-recall   │       │ Obsidian Vault │
│              │       │                  │       │                │
│  Prompt      ├──────►│ load_context.py  │◄──────┤ mindmap.json   │
│  (every msg) │       │ + get_relevant() │       │ context.md     │
│              │       │                  │       │ sessions/*.md  │
│  Exit        ├──────►│ save_context.py  ├──────►│                │
│  (stop hook) │       │ + update_mindmap │       └────────────────┘
│              │       │                  │
│  Tool use    ├──────►│ post_tool_use.py │       ┌────────────────┐
│  (edit)      │       │ + mark_stale()   │       │  MCP Server    │
└──────────────┘       └────────┬─────────┘       │ recall_get()   │
                                 │                 │ recall_update()│
                    ┌────────────┴────────────┐    │ recall_session_│
                    │   claude CLI (primary)  │    │ recall_mindmap │
                    │   or API key (fallback) │    └────────────────┘
                    └─────────────────────────┘
~/.claude/claude-recall.json
~/.claude/claude-recall-slug.env
```

---

## 🗺️ Mindmap Storage

claude-recall stores project context as a **JSON graph** at `<vault>/claude-recall/projects/<slug>/mindmap.json`:

```json
{
  "_meta": {"version": 2, "updated": "2026-04-18"},
  "nodes": {
    "project_overview": {
      "content": "Blood donation platform with donor/recipient matching",
      "keywords": ["flutter", "express", "mongodb", "setu"],
      "parent": null,
      "files": [],
      "created": "2026-04-10",
      "last_updated": "2026-04-18",
      "stale": false
    },
    "stack": {
      "content": "Tech stack: Flutter, Express.js, MongoDB Atlas, Railway",
      "keywords": ["flutter", "express", "mongodb", "railway"],
      "parent": "project_overview",
      "files": ["package.json", "pubspec.yaml"],
      "stale": false
    },
    "auth_system": {
      "content": "JWT auth with refresh tokens stored in secure storage",
      "keywords": ["jwt", "auth", "security"],
      "parent": "project_overview",
      "files": ["lib/auth/jwt_handler.dart"],
      "stale": true
    }
  },
  "file_node_map": {
    "lib/auth/jwt_handler.dart": ["auth_system"]
  },
  "sessions": [
    {"date": "2026-04-18", "summary": "Added JWT auth...", "nodes_updated": ["auth_system"]}
  ]
}
```

**Why JSON?** Enables fast keyword lookups, parent/child relationships, and file→node mapping. The `context.md` in your vault is auto-generated from this JSON for Obsidian viewing.

---

## 🔌 MCP Tools

claude-recall registers an MCP server that exposes 4 tools Claude can call during a session:

| Tool | When used | What it returns |
|:--|:--|:--|
| `recall_get(query)` | You ask about past decisions/architecture | Relevant context nodes |
| `recall_update_node(node_id, content, keywords)` | You explicitly update context | Confirmation |
| `recall_session_history(count)` | You ask "what did I work on before?" | Last N session summaries |
| `recall_mindmap()` | You ask for full project overview | Full mindmap tree |

> These tools let Claude fetch deeper context mid-session — not just what was injected at prompt time.

---

## 📄 File reference

| File | Purpose |
|:--|:--|
| `install.sh` | One-command GitHub installer |
| `SKILL.md` | Claude skill metadata and instructions |
| `scripts/load_context.py` | `UserPromptSubmit` hook — injects relevant context nodes |
| `scripts/save_context.py` | `Stop` hook — writes session note, updates mindmap |
| `scripts/summarize.py` | LLM summarizer using claude CLI or fallback API |
| `scripts/mindmap.py` | Mindmap storage + keyword search + node management |
| `scripts/mcp_server.py` | FastMCP server exposing recall tools to Claude |
| `scripts/post_tool_use.py` | `PostToolUse` hook — marks nodes stale on file edits |
| `scripts/recall_update.py` | `/recall` command for manual context updates |
| `scripts/utils.py` | Shared helpers (config, slugs, truncation, stack detection) |
| `references/hook-api.md` | Claude Code hook I/O specification |
| `references/context-structure.md` | Vault note formats and examples |

---

## 🔧 Troubleshooting

<details>
<summary><strong>Claude isn't loading my context</strong></summary>

Test the load hook manually:

```bash
echo '{"cwd":"'$(pwd)'","session_id":"test"}' | python3 ~/.claude/skills/claude-recall/scripts/load_context.py
```

If output is empty, check:
- Does `context.md` exist in your vault for this project?
- Is `vault_path` correct in `~/.claude/claude-recall.json`?

</details>

<details>
<summary><strong>Session notes aren't being saved</strong></summary>

Check that `save_sessions` is `true` in `~/.claude/claude-recall.json` and that Claude Code is passing a `transcript_path` to the Stop hook.

</details>

<details>
<summary><strong>Wrong project slug</strong></summary>

The slug strips noise segments (`projects`, `repos`, `code`, `src`, `workspace`, `dev`, `work`, `home`) and keeps the last 2 meaningful parts. Verify with:

```bash
python3 -c "
from pathlib import Path
import sys; sys.path.insert(0, '\$HOME/.claude/skills/claude-recall/scripts')
from utils import cwd_to_slug
print(cwd_to_slug(Path('\$(pwd)')))
"
```

</details>

---

<p align="center">
  <sub>Built with 🧠 by <a href="https://github.com/senapati484">senapati484</a></sub>
</p>
