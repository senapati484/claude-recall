#!/usr/bin/env bash
# claude-recall uninstaller
# Usage: bash ~/.claude/skills/claude-recall/uninstall.sh
set -euo pipefail

REPO_DIR="$HOME/.claude/skills/claude-recall"
CONFIG="$HOME/.claude/claude-recall.json"
SETTINGS="$HOME/.claude/settings.json"
MODEL_DIR="$HOME/.claude/models"
DEBUG_LOG="$HOME/.claude/claude-recall-debug.log"
MARKERS="$HOME/.claude/.recall_"*

G='\033[0;32m'; C='\033[0;36m'; Y='\033[1;33m'; R='\033[0m'
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${C}→${R} $1"; }
warn() { echo -e "  ${Y}!${R} $1"; }

echo ""
echo "  ┌───────────────────────────────────────────┐"
echo "  │   claude-recall — uninstall               │"
echo "  └───────────────────────────────────────────┘"
echo ""

# ── 1. Remove hooks from settings.json ───────────────────────────────────────
info "Removing hooks from $SETTINGS..."

if [ -f "$SETTINGS" ]; then
    python3 - <<'PYEOF'
import json, sys
from pathlib import Path

settings_path = Path("$SETTINGS")
if not settings_path.exists():
    print("  ! settings.json not found")
    sys.exit(0)

try:
    settings = json.loads(settings_path.read_text())
except json.JSONDecodeError:
    print("  ! settings.json is malformed — skipping hook removal")
    sys.exit(0)

hooks = settings.get("hooks", {})
removed = 0

for event in ["UserPromptSubmit", "Stop"]:
    if event in hooks:
        original = hooks[event]
        hooks[event] = [
            h for h in hooks[event]
            if not (isinstance(h, dict) and "claude-recall" in h.get("hooks", [{}])[0].get("command", ""))
        ]
        if len(hooks[event]) < len(original):
            removed += 1
            print(f"  ! Removed {event} hook")

if removed > 0:
    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"  ✓ Updated {settings_path}")
else:
    print("  - No claude-recall hooks found")
PYEOF
    ok "Hooks removed"
else
    warn "settings.json not found — skipping"
fi

# ── 2. Remove skill files ─────────────────────────────────────────────────────
info "Removing skill files..."
if [ -d "$REPO_DIR" ]; then
    rm -rf "$REPO_DIR"
    ok "Removed → $REPO_DIR"
else
    warn "Skill directory not found — skipping"
fi

# ── 3. Remove config ─────────────────────────────────────────────────────────
info "Removing config..."
if [ -f "$CONFIG" ]; then
    rm -f "$CONFIG"
    ok "Removed → $CONFIG"
else
    warn "Config not found — skipping"
fi

# ── 4. Remove model files (optional — ask user) ───────────────────────────────
echo ""
echo "  Remove the LLM model (~380 MB)? This cannot be undone."
printf "  Remove model files? [y/N]: "
read -r REMOVE_MODEL </dev/tty || REMOVE_MODEL="n"
if [[ "$REMOVE_MODEL" =~ ^[Yy]$ ]]; then
    if [ -d "$MODEL_DIR" ]; then
        rm -rf "$MODEL_DIR"
        ok "Removed → $MODEL_DIR"
    else
        warn "Model directory not found — skipping"
    fi
else
    info "Keeping model files at $MODEL_DIR"
fi

# ── 5. Remove debug log and session markers ──────────────────────────────────
info "Cleaning up temporary files..."
rm -f "$DEBUG_LOG"
rm -f $MARKERS 2>/dev/null || true
ok "Removed debug log and session markers"

# ── 6. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────────────┐"
echo "  │   claude-recall uninstalled  ✓        │"
echo "  └───────────────────────────────────────┘"
echo ""
echo "  Your Obsidian notes at <vault>/claude-recall/ are untouched."
echo "  Restart Claude Code to complete the removal."
echo ""
