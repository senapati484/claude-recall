---
description: Delete and regenerate context from scratch
allowed-tools: Bash
---

Reset claude-recall for this project. This backs up the existing context.md and regenerates it from scratch using the local LLM.

Run this:
```bash
python3 ~/.claude/skills/claude-recall/scripts/recall_update.py reset
```

Print the full output to the user.
