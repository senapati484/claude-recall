---
description: Re-scan project and update context.md with the local LLM
allowed-tools: Bash
---

Explicitly trigger claude-recall to re-scan the current project and update context.md in the Obsidian vault using the locally installed LLM.

Run this:
```bash
python3 ~/.claude/skills/claude-recall/scripts/recall_update.py update
```

Print the full output to the user.
