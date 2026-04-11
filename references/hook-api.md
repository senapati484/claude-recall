# Claude Code Hook API

## Hooks used by claude-recall

### UserPromptSubmit
Runs before every user message. Text printed to **stdout** is prepended to
Claude's system context for that turn.

**stdin (JSON):**
```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "UserPromptSubmit"
}
```

Exit code 0 = success. Non-zero = hook error (Claude still runs).
Exit code 2 = block the prompt entirely (writes stderr as error message).

### Stop
Runs when the session ends. stdout is ignored — use for side effects only.

**stdin:** Same JSON schema as above.

Exit code 2 = force Claude to continue working.

---

## Registration in `~/.claude/settings.json`

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/skills/claude-recall/scripts/load_context.py"}]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [{"type": "command", "command": "python3 ~/.claude/skills/claude-recall/scripts/save_context.py"}]
      }
    ]
  }
}
```

`matcher` is a regex on the prompt text. Empty string = match all prompts.

---

## Transcript JSONL format

```jsonl
{"role": "user", "content": "Help me refactor the auth module"}
{"role": "assistant", "content": "Sure — let's start with..."}
```

`save_context.py` reads this file (path comes from `transcript_path` in hook input).
