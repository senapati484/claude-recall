#!/usr/bin/env bash
# claude-recall uninstaller
# Removes everything created by install.sh
# Usage: bash ~/.claude/skills/claude-recall/uninstall.sh
set -euo pipefail

REPO_DIR="$HOME/.claude/skills/claude-recall"
CONFIG="$HOME/.claude/claude-recall.json"
SETTINGS="$HOME/.claude/settings.json"
DEBUG_LOG="$HOME/.claude/claude-recall-debug.log"
INSTALL_LOG="$HOME/.claude/claude-recall-install.log"
SLUG_ENV="$HOME/.claude/claude-recall-slug.env"
MCP_PID="$HOME/.claude/claude-recall-mcp.pid"
COMMAND_DIR="$HOME/.claude/commands"

# ── What this uninstaller removes ─────────────────────────────────────────────
#
# ALWAYS REMOVED:
#   $HOME/.claude/skills/claude-recall/       ← skill repo
#   $HOME/.claude/claude-recall.json          ← config
#   $HOME/.claude/claude-recall-debug.log     ← runtime debug log
#   $HOME/.claude/claude-recall-install.log   ← pip install log
#   $HOME/.claude/claude-recall-slug.env       ← slug env file for MCP
#   $HOME/.claude/claude-recall-mcp.pid       ← MCP server PID file
#   Session markers and all hooks from settings.json
#   MCP server registration from settings.json
#   Slash commands
#
# OPTIONAL (asks user):
#   anthropic + fastmcp + openai Python packages
#
# NEVER REMOVED (preserved):
#   <vault>/claude-recall/                    ← Obsidian notes (user data)
# ─────────────────────────────────────────────────────────────────────────────

G='\033[0;32m'; C='\033[0;36m'; Y='\033[1;33m'; R='\033[0m'
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${C}→${R} $1"; }
warn() { echo -e "  ${Y}!${R} $1"; }

echo ""
echo "  ┌───────────────────────────────────────────┐"
echo "  │   claude-recall — uninstall               │"
echo "  └───────────────────────────────────────────┘"
echo ""

# ── 1. Remove hooks, MCP, and statusLine from settings.json ───────────────────
info "Cleaning settings.json..."

python3 - <<'PYEOF'
import json
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
if not settings_path.exists():
    print("  ! settings.json not found")
    exit(0)

try:
    settings = json.loads(settings_path.read_text())
except json.JSONDecodeError:
    print("  ! settings.json is malformed")
    exit(0)

removed_items = []

# Remove hooks
hooks = settings.get("hooks", {})
for event in ["SessionStart", "UserPromptSubmit", "Stop", "PostToolUse"]:
    if event in hooks:
        original_count = len(hooks[event])
        hooks[event] = [
            h for h in hooks[event]
            if not (isinstance(h, dict) and any(
                "claude-recall" in hook.get("command", "")
                for hook in h.get("hooks", [])
            ))
        ]
        if len(hooks[event]) < original_count:
            removed_items.append(f"Hook: {event}")

# Remove MCP servers
mcp_servers = settings.get("mcpServers", {})
if "claude-recall" in mcp_servers:
    del mcp_servers["claude-recall"]
    removed_items.append("MCP server: claude-recall")

# Remove statusLine wrapper (restore upstream if exists)
upstream_path = Path.home() / ".claude" / "claude-recall-upstream-statusline.txt"
if "statusLine" in settings:
    if upstream_path.exists():
        upstream_cmd = upstream_path.read_text().strip()
        if upstream_cmd:
            settings["statusLine"] = upstream_cmd
        else:
            del settings["statusLine"]
    else:
        del settings["statusLine"]
    removed_items.append("statusLine wrapper")

# Save updated settings
settings_path.write_text(json.dumps(settings, indent=2))

if removed_items:
    for item in removed_items:
        print(f"  ✓ Removed {item}")
else:
    print("  - No claude-recall entries found")
PYEOF

ok "settings.json cleaned"

# ── 2. Remove slash commands ───────────────────────────────────────────────
info "Removing slash commands..."
if [ -d "$COMMAND_DIR" ]; then
    rm -f "$COMMAND_DIR/claude-recall.md" 2>/dev/null || true
    rm -rf "$COMMAND_DIR/claude-recall" 2>/dev/null || true
    # Remove command dir if empty
    rmdir "$COMMAND_DIR" 2>/dev/null || true
    ok "Removed slash commands"
else
    info "No commands directory found"
fi

# ── 3. Remove skill files ─────────────────────────────────────────────────────
info "Removing skill files..."
if [ -d "$REPO_DIR" ]; then
    rm -rf "$REPO_DIR"
    ok "Removed → $REPO_DIR"
else
    warn "Skill directory not found"
fi

# ── 4. Remove config and env files ─────────────────────────────────────────────
info "Removing config..."
rm -f "$CONFIG" 2>/dev/null || true
rm -f "$SLUG_ENV" 2>/dev/null || true
rm -f "$MCP_PID" 2>/dev/null || true
ok "Removed config and env files"

# ── 5. Remove logs ───────────────────────────────────────────────────────────
info "Removing logs..."
rm -f "$DEBUG_LOG" 2>/dev/null || true
rm -f "$INSTALL_LOG" 2>/dev/null || true
ok "Removed logs"

# ── 6. Remove Python packages (optional) ─────────────────────────────────────
echo ""
echo "  Python packages (anthropic, fastmcp, openai) were installed."
printf "  Uninstall these packages? [y/N]: "
read -r REMOVE_PKGS </dev/tty || REMOVE_PKGS="n"
if [[ "$REMOVE_PKGS" =~ ^[Yy]$ ]]; then
    pip3 uninstall -y anthropic fastmcp openai 2>/dev/null || true
    ok "Removed Python packages"
else
    info "Keeping Python packages installed"
fi

# ── 7. Done ───────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────────────────────┐"
echo "  │   claude-recall uninstalled  ✓                │"
echo "  └───────────────────────────────────────────────┘"
echo ""
echo "  REMOVED:"
echo "    ✓ Skill files:      $REPO_DIR"
echo "    ✓ Config:           $CONFIG"
echo "    ✓ Hooks:            SessionStart, UserPromptSubmit, Stop, PostToolUse"
echo "    ✓ MCP server:      claude-recall"
echo "    ✓ Slash commands:  /claude-recall"
echo "    ✓ StatusLine:      wrapper removed"
echo "    ✓ Logs:             debug + install logs"
echo "    ✓ Env files:       claude-recall-slug.env, claude-recall-mcp.pid"
if [[ "${REMOVE_PKGS:-n}" =~ ^[Yy]$ ]]; then
    echo "    ✓ Python packages: anthropic, fastmcp, openai"
fi
echo ""
echo "  PRESERVED:"
echo "    Your Obsidian notes at <vault>/claude-recall/ are untouched."
echo "    Delete them manually if you no longer need them."
echo ""
echo "  Restart Claude Code to complete the removal."
echo ""