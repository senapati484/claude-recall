#!/usr/bin/env bash
# claude-recall installer
# Usage: curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/senapati484/claude-recall"
RAW_URL="https://raw.githubusercontent.com/senapati484/claude-recall/main"
INSTALL_DIR="$HOME/.claude/skills/claude-recall"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOME/.claude/claude-recall.json"
DEBUG_LOG="$HOME/.claude/claude-recall-debug.log"
INSTALL_LOG="$HOME/.claude/claude-recall-install.log"

# ── What this installer creates/modifies ──────────────────────────────────────
#
# FILES CREATED:
#   $HOME/.claude/skills/claude-recall/       ← skill repo (scripts, SKILL.md, etc.)
#   $HOME/.claude/claude-recall.json          ← config (vault path, settings)
#   $HOME/.claude/claude-recall-install.log   ← pip install log
#   $HOME/.claude/claude-recall-debug.log     ← runtime debug log
#   $HOME/.claude/.recall_*                   ← session markers (transient)
#   <vault>/claude-recall/                    ← Obsidian vault structure
#
# FILES MODIFIED:
#   $HOME/.claude/settings.json               ← adds hooks + MCP server
#
# PYTHON PACKAGES INSTALLED:
#   anthropic                                 ← Claude API SDK for summarization
#   fastmcp                                   ← MCP server for context tools
#
# To undo ALL of the above, run:
#   bash ~/.claude/skills/claude-recall/uninstall.sh
# ──────────────────────────────────────────────────────────────────────────────

G='\033[0;32m'; C='\033[0;36m'; Y='\033[1;33m'; R='\033[0m'
ok()   { echo -e "  ${G}✓${R} $1"; }
info() { echo -e "  ${C}→${R} $1"; }
warn() { echo -e "  ${Y}!${R} $1"; }

echo ""
echo "  ┌───────────────────────────────────────────┐"
echo "  │   claude-recall — install                 │"
echo "  │   Persistent Obsidian memory for Claude   │"
echo "  └───────────────────────────────────────────┘"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
info "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
  echo "  ✗ Python 3 not found. Install from https://python.org"; exit 1
fi
ok "Python $(python3 --version 2>&1 | awk '{print $2}')"

if command -v claude &>/dev/null; then
  ok "Claude Code $(claude --version 2>/dev/null | head -1 || echo 'found')"
elif [ -d "$HOME/.claude" ]; then
  ok "Claude Code detected (~/.claude exists)"
else
  echo "  ✗ Claude Code not found. Install from https://claude.ai/code"; exit 1
fi

# ── 2. Obsidian vault path ────────────────────────────────────────────────────
echo ""
info "Obsidian vault setup..."
echo ""
echo "  claude-recall stores all context inside your Obsidian vault."
echo "  Make sure Obsidian is installed and a vault exists before continuing."
echo ""

# Check if already configured
EXISTING_VAULT=""
if [ -f "$CONFIG" ]; then
  EXISTING_VAULT=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG'))
    print(c.get('vault_path',''))
except: pass
" 2>/dev/null || true)
fi

if [ -n "$EXISTING_VAULT" ] && [ -d "$EXISTING_VAULT" ]; then
  echo "  Existing vault found: $EXISTING_VAULT"
  printf "  Use this vault? [Y/n]: "
  read -r USE_EXISTING </dev/tty || USE_EXISTING="y"
  if [[ "$USE_EXISTING" =~ ^[Nn] ]]; then
    EXISTING_VAULT=""
  fi
fi

if [ -z "$EXISTING_VAULT" ]; then
  printf "  Enter your Obsidian vault path: "
  read -r VAULT_PATH </dev/tty
  VAULT_PATH="${VAULT_PATH/#\~/$HOME}"   # expand ~
  if [ ! -d "$VAULT_PATH" ]; then
    echo ""
    echo "  ✗ Directory not found: $VAULT_PATH"
    echo "  Create your Obsidian vault first, then re-run this script."
    exit 1
  fi
  EXISTING_VAULT="$VAULT_PATH"
fi

VAULT_PATH="$EXISTING_VAULT"
ok "Vault: $VAULT_PATH"

# ── 3. Write config ───────────────────────────────────────────────────────────
mkdir -p "$HOME/.claude"
cat > "$CONFIG" <<EOF
{
  "vault_path": "$VAULT_PATH",
  "vault_folder": "claude-recall",
  "max_context_tokens": 400,
  "include_recent_sessions": 2,
  "save_sessions": true,
  "load_on_every_prompt": true,
  "use_claude_api": true
}
EOF
ok "Config written → $CONFIG"

# ── 4. Download skill files ───────────────────────────────────────────────────
echo ""
info "Downloading claude-recall..."

# Detect if we're running from the repo (works even with `bash /path/to/install.sh`)
# When piped via `curl | bash`, BASH_SOURCE is empty — default to ""
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -f "$SCRIPT_DIR/SKILL.md" ] && [ -d "$SCRIPT_DIR/scripts" ]; then
  info "Local repository detected at $SCRIPT_DIR"
  info "Installing from local files (not from GitHub)..."
  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  cp -R "$SCRIPT_DIR"/* "$INSTALL_DIR/"
  rm -rf "$INSTALL_DIR/.git" 2>/dev/null || true
  ok "Copied → $INSTALL_DIR (local)"
elif command -v git &>/dev/null; then
  if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing install via git pull..."
    git -C "$INSTALL_DIR" fetch --quiet
    git -C "$INSTALL_DIR" reset --hard --quiet origin/main
  elif [ -d "$INSTALL_DIR" ]; then
    info "Replacing non-git install..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 --quiet "$REPO_URL" "$INSTALL_DIR"
  else
    git clone --depth 1 --quiet "$REPO_URL" "$INSTALL_DIR"
  fi
  ok "Cloned → $INSTALL_DIR (GitHub)"
else
  # curl fallback
  mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/references"
  for FILE in \
    "SKILL.md" \
    "install.sh" \
    "uninstall.sh" \
    "scripts/utils.py" \
    "scripts/load_context.py" \
    "scripts/save_context.py" \
    "scripts/summarize.py" \
    "scripts/recall_update.py" \
    "scripts/scan_project.py" \
    "scripts/context_builder.py" \
    "scripts/session_manager.py" \
    "references/hook-api.md" \
    "references/context-structure.md"
  do
    DEST="$INSTALL_DIR/$FILE"
    mkdir -p "$(dirname "$DEST")"
    curl -fsSL "$RAW_URL/$FILE" -o "$DEST"
  done
  ok "Downloaded → $INSTALL_DIR (curl)"
fi

# Verify all scripts compile
COMPILE_ERRORS=0
for pyfile in "$INSTALL_DIR"/scripts/*.py; do
  if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
    warn "Compile error: $(basename "$pyfile")"
    COMPILE_ERRORS=$((COMPILE_ERRORS + 1))
  fi
done
if [ "$COMPILE_ERRORS" -eq 0 ]; then
  ok "All scripts compile OK"
else
  warn "$COMPILE_ERRORS scripts failed to compile"
fi

chmod +x "$INSTALL_DIR/scripts/"*.py 2>/dev/null || true

# ── 4. Install Python dependencies ─────────────────────────────────────────────
echo ""
info "Installing Python dependencies..."

pip3 install anthropic fastmcp openai --quiet --break-system-packages >> "$INSTALL_LOG" 2>&1

if python3 -c "import anthropic; import fastmcp; import openai" 2>/dev/null; then
    ok "anthropic + fastmcp + openai installed"
else
    warn "Python packages install failed"
    warn "Check $INSTALL_LOG for details"
fi

# Check API key (Anthropic or NVIDIA NIM)
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ok "ANTHROPIC_API_KEY detected"
elif [ -n "${OPENAI_API_KEY:-}" ] && [ -n "${NVIDIA_NIM_BASE_URL:-}" ]; then
  ok "NVIDIA NIM detected (OPENAI_API_KEY + NVIDIA_NIM_BASE_URL)"
else
  warn "No API key detected."
  warn "Set ANTHROPIC_API_KEY or OPENAI_API_KEY+NVIDIA_NIM_BASE_URL"
  warn "Context generation will fall back to regex until set."
fi

# ── 6. Register hooks ─────────────────────────────────────────────────────────
echo ""
info "Registering hooks in $SETTINGS..."

LOAD_CMD="python3 $INSTALL_DIR/scripts/load_context.py"
SAVE_CMD="python3 $INSTALL_DIR/scripts/save_context.py"
START_CMD="python3 $INSTALL_DIR/scripts/session_start.py"
POST_TOOL_CMD="python3 $INSTALL_DIR/scripts/post_tool_use.py"

python3 - <<PYEOF
import json, sys
from pathlib import Path

path = Path("$SETTINGS")
load_cmd = "$LOAD_CMD"
save_cmd = "$SAVE_CMD"
start_cmd = "$START_CMD"
post_tool_cmd = "$POST_TOOL_CMD"

if path.exists():
    try:
        settings = json.loads(path.read_text())
    except json.JSONDecodeError:
        warn_path = str(path) + ".bak"
        path.rename(warn_path)
        print(f"  ! settings.json was malformed — backed up to {warn_path}")
        settings = {}
else:
    settings = {}

hooks = settings.setdefault("hooks", {})

def already(event):
    for e in hooks.get(event, []):
        for h in e.get("hooks", []):
            if "claude-recall" in h.get("command", ""):
                return True
    return False

for event, cmd, timeout in [
    ("SessionStart", start_cmd, 10),
    ("UserPromptSubmit", load_cmd, 60),
    ("Stop", save_cmd, 60),
    ("PostToolUse", post_tool_cmd, 10),
]:
    if already(event):
        print(f"  ✓ {event} — already registered")
    else:
        hooks.setdefault(event, []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": cmd, "timeout": timeout}]
        })
        print(f"  ✓ {event} — registered")

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(settings, indent=2))
print(f"  ✓ Saved {path}")
PYEOF

# ── 7b. Register statusLine wrapper ──────────────────────────────────────────
STATUSLINE_CMD="python3 $INSTALL_DIR/scripts/statusline_wrapper.py"
UPSTREAM_FILE="$HOME/.claude/claude-recall-upstream-statusline.txt"

python3 - <<PYEOF
import json
from pathlib import Path

settings_path = Path("$SETTINGS")
upstream_path = Path("$UPSTREAM_FILE")
wrapper_cmd = "$STATUSLINE_CMD"

settings = json.loads(settings_path.read_text())

current_sl = settings.get("statusLine", {})
current_cmd = ""
if isinstance(current_sl, dict):
    current_cmd = current_sl.get("command", "")
elif isinstance(current_sl, str):
    current_cmd = current_sl

# Save the upstream command (if it's not already our wrapper)
if current_cmd and "claude-recall" not in current_cmd:
    upstream_path.write_text(current_cmd)
    print(f"  ✓ Saved upstream statusLine → {upstream_path}")
elif not upstream_path.exists():
    upstream_path.write_text("")

# Set our wrapper as the statusLine
settings["statusLine"] = {
    "type": "command",
    "command": wrapper_cmd,
}
settings_path.write_text(json.dumps(settings, indent=2))
print(f"  ✓ statusLine → claude-recall wrapper")
PYEOF

# ── 7c. Register MCP server ────────────────────────────────────────────────────
echo ""
info "Registering MCP server..."

MCP_CMD="python3 $INSTALL_DIR/scripts/mcp_server.py"

python3 - <<PYEOF
import json
from pathlib import Path

settings_path = Path("$SETTINGS")
mcp_cmd = "$MCP_CMD"

settings = json.loads(settings_path.read_text())

mcp_servers = settings.setdefault("mcpServers", {})

if "claude-recall" in mcp_servers:
    print("  ✓ claude-recall MCP — already registered")
else:
    mcp_servers["claude-recall"] = {
        "command": "python3",
        "args": [mcp_cmd],
        "env": {
            "CLAUDE_RECALL_SLUG": "\${CLAUDE_RECALL_SLUG:-unknown}"
        }
    }
    settings_path.write_text(json.dumps(settings, indent=2))
    print("  ✓ claude-recall MCP → registered")

settings_path.write_text(json.dumps(settings, indent=2))
print(f"  ✓ Saved {settings_path}")
PYEOF
# ── 8. Create vault folder skeleton ──────────────────────────────────────────
VAULT_CR="$VAULT_PATH/claude-recall"
mkdir -p "$VAULT_CR/projects"

if [ ! -f "$VAULT_CR/_index.md" ]; then
cat > "$VAULT_CR/_index.md" <<'EOF'
---
tags: [claude-recall]
---

# claude-recall — project index

Each entry is auto-appended by claude-recall when a session ends.

## Projects

EOF
  ok "Vault folder ready → $VAULT_CR"
fi

# ── 9. Register /claude-recall slash commands ─────────────────────────────────
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
# Copy the top-level command and the subcommand directory
cp "$INSTALL_DIR/commands/claude-recall.md" "$CMD_DIR/claude-recall.md"
if [ -d "$INSTALL_DIR/commands/claude-recall" ]; then
  mkdir -p "$CMD_DIR/claude-recall"
  cp "$INSTALL_DIR/commands/claude-recall/"*.md "$CMD_DIR/claude-recall/"
fi
echo "  ✓ Slash commands registered: /claude-recall, :update, :status, :reset"

# ── 10. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────────────────────┐"
echo "  │   claude-recall installed  ✓                  │"
echo "  └───────────────────────────────────────────────┘"
echo ""
echo "  INSTALLED:"
echo "    Skill:   $INSTALL_DIR"
echo "    Config:  $CONFIG"
echo "    Vault:   $VAULT_PATH"
echo "    Hooks:   UserPromptSubmit + Stop in $SETTINGS"
echo "    MCP:     claude-recall MCP server registered"
echo "    API:     Claude API (ANTHROPIC_API_KEY required)"
echo ""
echo "  TO UNINSTALL:"
echo "    bash $INSTALL_DIR/uninstall.sh"
echo ""
echo "  NEXT STEP: Restart Claude Code."
echo ""
echo "  After restart, open any project directory and start chatting."
echo "  claude-recall will:"
echo "    • Session start → keyword match → inject 100-200 tokens relevant context"
echo "    • Session end → Claude API → updates mindmap.json nodes"
echo "    • Use /recall query <question> for deeper context during session"
echo ""
