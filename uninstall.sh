#!/usr/bin/env bash
# claude-recall uninstaller
# Removes everything created by install.sh
# Usage: bash ~/.claude/skills/claude-recall/uninstall.sh
set -euo pipefail

REPO_DIR="$HOME/.claude/skills/claude-recall"
CONFIG="$HOME/.claude/claude-recall.json"
SETTINGS="$HOME/.claude/settings.json"
MODEL_DIR="$HOME/.claude/models"
MODEL_FILE="$MODEL_DIR/qwen2.5-0.5b-instruct-q4_k_m.gguf"
DEBUG_LOG="$HOME/.claude/claude-recall-debug.log"
INSTALL_LOG="$HOME/.claude/claude-recall-install.log"

# ── What this uninstaller removes ─────────────────────────────────────────────
#
# ALWAYS REMOVED:
#   $HOME/.claude/skills/claude-recall/       ← skill repo
#   $HOME/.claude/claude-recall.json          ← config
#   $HOME/.claude/claude-recall-debug.log     ← runtime debug log
#   $HOME/.claude/claude-recall-install.log   ← pip install log
#   $HOME/.claude/.recall_*                   ← session markers
#   UserPromptSubmit + Stop hooks from settings.json
#
# OPTIONAL (asks user):
#   $HOME/.claude/models/qwen2.5-0.5b-*.gguf ← LLM model (~380 MB)
#   llama-cpp-python Python package
#
# NEVER REMOVED (preserved):
#   <vault>/claude-recall/                    ← Obsidian notes (user data)
# ──────────────────────────────────────────────────────────────────────────────

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
import json, sys, os
from pathlib import Path

settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
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
            if not (isinstance(h, dict) and any(
                "claude-recall" in hook.get("command", "")
                for hook in h.get("hooks", [])
            ))
        ]
        if len(hooks[event]) < len(original):
            removed += 1
            print(f"  ✓ Removed {event} hook")

if removed > 0:
    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"  ✓ Updated {settings_path}")
else:
    print("  - No claude-recall hooks found")
PYEOF
    ok "Hooks cleaned"
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

# ── 4. Remove debug and install logs ──────────────────────────────────────────
info "Removing logs..."
rm -f "$DEBUG_LOG" 2>/dev/null || true
rm -f "$INSTALL_LOG" 2>/dev/null || true
ok "Removed logs"

# ── 5. Remove session markers ────────────────────────────────────────────────
info "Removing session markers..."
rm -f "$HOME/.claude/.recall_"* 2>/dev/null || true
ok "Removed session markers"

# ── 6. Remove LLM model (optional — ask user) ────────────────────────────────
echo ""
echo "  The LLM model file is ~380 MB:"
echo "    $MODEL_FILE"
printf "  Remove model file? [y/N]: "
read -r REMOVE_MODEL </dev/tty || REMOVE_MODEL="n"
if [[ "$REMOVE_MODEL" =~ ^[Yy]$ ]]; then
    rm -f "$MODEL_FILE" 2>/dev/null || true
    # Remove models dir only if empty
    rmdir "$MODEL_DIR" 2>/dev/null || true
    ok "Removed model file"
else
    info "Keeping model file at $MODEL_FILE"
fi

# ── 7. Remove llama-cpp-python (optional — ask user) ─────────────────────────
echo ""
echo "  The Python package 'llama-cpp-python' was installed for LLM inference."
printf "  Uninstall llama-cpp-python? [y/N]: "
read -r REMOVE_LLAMA </dev/tty || REMOVE_LLAMA="n"
if [[ "$REMOVE_LLAMA" =~ ^[Yy]$ ]]; then
    if pip3 uninstall -y llama-cpp-python 2>/dev/null; then
        ok "Uninstalled llama-cpp-python"
    elif python3 -m pip uninstall -y llama-cpp-python 2>/dev/null; then
        ok "Uninstalled llama-cpp-python"
    else
        warn "Could not uninstall llama-cpp-python — may need manual removal"
    fi
else
    info "Keeping llama-cpp-python installed"
fi

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────────────────────┐"
echo "  │   claude-recall uninstalled  ✓                │"
echo "  └───────────────────────────────────────────────┘"
echo ""
echo "  REMOVED:"
echo "    ✓ Skill files:      $REPO_DIR"
echo "    ✓ Config:           $CONFIG"
echo "    ✓ Hooks:            UserPromptSubmit + Stop from settings.json"
echo "    ✓ Logs:             debug + install logs"
echo "    ✓ Session markers:  ~/.claude/.recall_*"
if [[ "${REMOVE_MODEL:-n}" =~ ^[Yy]$ ]]; then
    echo "    ✓ Model:            $MODEL_FILE"
fi
if [[ "${REMOVE_LLAMA:-n}" =~ ^[Yy]$ ]]; then
    echo "    ✓ Python package:   llama-cpp-python"
fi
echo ""
echo "  PRESERVED:"
echo "    Your Obsidian notes at <vault>/claude-recall/ are untouched."
echo "    Delete them manually if you no longer need them."
echo ""
echo "  Restart Claude Code to complete the removal."
echo ""
