---
description: Explicitly update project context using the local LLM (Qwen2.5 0.5B)
allowed-tools: Bash
---

Run the claude-recall context updater. This explicitly triggers the local LLM to re-scan the project and update context.md in the Obsidian vault.

Arguments: $ARGUMENTS (default: "update")
- update — Re-scan project and update context.md
- status — Show current stored context
- reset  — Delete and regenerate context from scratch

Execute this command:
```bash
python3 ~/.claude/skills/claude-recall/scripts/recall_update.py $ARGUMENTS
```

Print the output to the user.
