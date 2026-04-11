# claude-recall

> Persistent Obsidian memory for Claude Code — install once, works on every session.

claude-recall hooks into Claude Code and bridges it with your Obsidian vault.
Before your first message in any session it loads your project context from Obsidian.
When you exit it saves a structured session note back to the vault.
No manual invocation. No config beyond your vault path.

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

Then restart Claude Code.

---

## How it works

Two Claude Code hooks are registered automatically:

- **`UserPromptSubmit`** — before your first message, `load_context.py` reads `context.md`
  and recent session notes from `<vault>/claude-recall/projects/<slug>/` and injects them
  into Claude's system context.
- **`Stop`** — when you exit, `save_context.py` reads the session transcript and writes a
  dated Markdown note back to the vault under `sessions/`.

Project slug is derived from the directory you launched `claude` in —
`/home/sayan/projects/setu` becomes `setu`, `/home/sayan/client/acme` becomes `client-acme`.

---

## What gets created in Obsidian

```
<your-vault>/
└── claude-recall/
    ├── _index.md                      ← auto-updated log of all projects
    └── projects/
        └── <project-slug>/
            ├── context.md             ← you edit this in Obsidian
            └── sessions/
                └── YYYY-MM-DD_HH-MM.md   ← auto-written on exit
```

**`context.md`** is your permanent memory file. Open it in Obsidian and fill in your
stack, architecture decisions, gotchas, current state — anything Claude should always know.

---

## Install

**Requirements:** Python 3.8+ · Claude Code · Obsidian (with a vault created)

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

The installer asks for your vault path once, saves it to `~/.claude/claude-recall.json`,
clones this repo to `~/.claude/skills/claude-recall/`, and registers both hooks.

**Restart Claude Code after install.**

### Manual install (no curl)

```bash
git clone https://github.com/senapati484/claude-recall ~/.claude/skills/claude-recall
bash ~/.claude/skills/claude-recall/install.sh
```

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

| Key | Default | Description |
|---|---|---|
| `vault_path` | _(required)_ | Absolute path to your Obsidian vault |
| `vault_folder` | `claude-recall` | Folder created inside the vault |
| `max_context_tokens` | `2000` | Token budget for injected context |
| `include_recent_sessions` | `2` | Past session notes loaded per session |
| `load_on_every_prompt` | `false` | Reload context on every prompt |

---

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
```

Re-running the installer detects an existing install and runs `git pull` instead of a fresh clone.

---

## Uninstall

1. Remove the two `claude-recall` hook entries from `~/.claude/settings.json`
2. `rm -rf ~/.claude/skills/claude-recall`
3. `rm ~/.claude/claude-recall.json`

Your Obsidian notes under `<vault>/claude-recall/` are untouched.

---

## File reference

| File | Purpose |
|---|---|
| `install.sh` | One-command GitHub installer |
| `SKILL.md` | Claude skill metadata and instructions |
| `scripts/load_context.py` | `UserPromptSubmit` hook — loads context from Obsidian |
| `scripts/save_context.py` | `Stop` hook — saves session note to Obsidian |
| `scripts/utils.py` | Shared helpers |
| `references/hook-api.md` | Claude Code hook I/O spec |
| `references/context-structure.md` | Vault note formats and examples |

---

## License

MIT
