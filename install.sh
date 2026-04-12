#!/usr/bin/env bash
# claude-recall installer
# Usage: curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/senapati484/claude-recall"
RAW_URL="https://raw.githubusercontent.com/senapati484/claude-recall/main"
INSTALL_DIR="$HOME/.claude/skills/claude-recall"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOME/.claude/claude-recall.json"
MODEL_DIR="$HOME/.claude/models"
MODEL_FILE="$MODEL_DIR/qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"
DEBUG_LOG="$HOME/.claude/claude-recall-debug.log"
INSTALL_LOG="$HOME/.claude/claude-recall-install.log"

# ── What this installer creates/modifies ──────────────────────────────────────
#
# FILES CREATED:
#   $HOME/.claude/skills/claude-recall/       ← skill repo (scripts, SKILL.md, etc.)
#   $HOME/.claude/claude-recall.json          ← config (vault path, settings)
#   $HOME/.claude/models/qwen2.5-0.5b-*.gguf ← LLM model (~380 MB)
#   $HOME/.claude/claude-recall-install.log   ← pip install log
#   $HOME/.claude/claude-recall-debug.log     ← runtime debug log
#   $HOME/.claude/.recall_*                   ← session markers (transient)
#   <vault>/claude-recall/                    ← Obsidian vault structure
#
# FILES MODIFIED:
#   $HOME/.claude/settings.json               ← adds UserPromptSubmit + Stop hooks
#
# PYTHON PACKAGES INSTALLED:
#   llama-cpp-python                          ← local LLM inference engine
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

if ! command -v claude &>/dev/null; then
  echo "  ✗ Claude Code not found. Install from https://claude.ai/code"; exit 1
fi
ok "Claude Code $(claude --version 2>/dev/null | head -1 || echo 'found')"

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
  "max_context_tokens": 2000,
  "include_recent_sessions": 2,
  "save_sessions": true,
  "load_on_every_prompt": false
}
EOF
ok "Config written → $CONFIG"

# ── 4. Download skill files ───────────────────────────────────────────────────
echo ""
info "Downloading claude-recall..."

if [ -f "./SKILL.md" ] && [ -d "./scripts" ]; then
  info "Local repository detected. Installing directly from local files..."
  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  cp -R ./* "$INSTALL_DIR/"
  rm -rf "$INSTALL_DIR/.git" 2>/dev/null || true
  ok "Copied → $INSTALL_DIR"
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
  ok "Cloned → $INSTALL_DIR"
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
  ok "Downloaded → $INSTALL_DIR"
fi

chmod +x "$INSTALL_DIR/scripts/"*.py 2>/dev/null || true

# ── 5. Install Python dependency: llama-cpp-python ────────────────────────────
echo ""
info "Installing Python dependency: llama-cpp-python..."

LLAMA_INSTALLED=0

# Check if already importable
if python3 -c "import llama_cpp" 2>/dev/null; then
    ok "llama-cpp-python already installed"
    LLAMA_INSTALLED=1
else
    echo "  Installing llama-cpp-python (this may take 2-5 minutes)..."
    echo "  Log: $INSTALL_LOG"

    # Attempt 1: pip3 install
    if command -v pip3 &>/dev/null; then
        if pip3 install llama-cpp-python >> "$INSTALL_LOG" 2>&1; then
            LLAMA_INSTALLED=1
        fi
    fi

    # Attempt 2: python3 -m pip
    if [ "$LLAMA_INSTALLED" -eq 0 ]; then
        if python3 -m pip install llama-cpp-python >> "$INSTALL_LOG" 2>&1; then
            LLAMA_INSTALLED=1
        fi
    fi

    # Attempt 3: pip3 with --no-cache-dir (clears stale wheel cache)
    if [ "$LLAMA_INSTALLED" -eq 0 ]; then
        if pip3 install --no-cache-dir llama-cpp-python >> "$INSTALL_LOG" 2>&1; then
            LLAMA_INSTALLED=1
        fi
    fi

    # Final verification — python3 can actually import it
    if [ "$LLAMA_INSTALLED" -eq 1 ]; then
        if python3 -c "import llama_cpp; print('import OK')" >> "$INSTALL_LOG" 2>&1; then
            ok "llama-cpp-python installed and verified"
        else
            LLAMA_INSTALLED=0
            warn "llama-cpp-python installed but import failed"
            warn "Check $INSTALL_LOG for details"
        fi
    else
        warn "llama-cpp-python install failed — LLM summaries will be disabled"
        warn "To install manually:"
        warn "  pip3 install llama-cpp-python"
        warn "  OR: CMAKE_ARGS='-DGGML_METAL=off' pip3 install llama-cpp-python"
    fi
fi

# ── 6. Download Qwen2.5 0.5B GGUF model ──────────────────────────────────────
echo ""
info "Checking local LLM model..."
mkdir -p "$MODEL_DIR"

if [ -f "$MODEL_FILE" ] && [ -s "$MODEL_FILE" ]; then
    ok "Model already present: $MODEL_FILE"
else
    echo "  Downloading Qwen2.5 0.5B (~380 MB, this may take a few minutes)..."
    DOWNLOADED=0
    if command -v curl &>/dev/null; then
        if curl -fL "$MODEL_URL" -o "$MODEL_FILE" 2>&1; then
            DOWNLOADED=1
        fi
    elif command -v wget &>/dev/null; then
        if wget -q "$MODEL_URL" -O "$MODEL_FILE" 2>&1; then
            DOWNLOADED=1
        fi
    else
        warn "No curl or wget found — install one to download the LLM model"
    fi

    if [ "$DOWNLOADED" -eq 1 ] && [ -f "$MODEL_FILE" ] && [ -s "$MODEL_FILE" ]; then
        ok "Model saved → $MODEL_FILE"
    else
        rm -f "$MODEL_FILE"
        warn "Model download failed — LLM features disabled"
    fi
fi

# ── 7. Register hooks ─────────────────────────────────────────────────────────
echo ""
info "Registering hooks in $SETTINGS..."

LOAD_CMD="python3 $INSTALL_DIR/scripts/load_context.py"
SAVE_CMD="python3 $INSTALL_DIR/scripts/save_context.py"

python3 - <<PYEOF
import json, sys
from pathlib import Path

path = Path("$SETTINGS")
load_cmd = "$LOAD_CMD"
save_cmd = "$SAVE_CMD"

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

for event, cmd in [("UserPromptSubmit", load_cmd), ("Stop", save_cmd)]:
    if already(event):
        print(f"  ✓ {event} — already registered")
    else:
        hooks.setdefault(event, []).append({
            "matcher": "",
            "hooks": [{"type": "command", "command": cmd}]
        })
        print(f"  ✓ {event} — registered")

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(settings, indent=2))
print(f"  ✓ Saved {path}")
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

# ── 9. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────────────────────┐"
echo "  │   claude-recall installed  ✓                  │"
echo "  └───────────────────────────────────────────────┘"
echo ""
echo "  INSTALLED:"
echo "    Skill:   $INSTALL_DIR"
echo "    Config:  $CONFIG"
echo "    Model:   $MODEL_FILE"
echo "    Vault:   $VAULT_PATH"
echo "    Hooks:   UserPromptSubmit + Stop in $SETTINGS"
echo "    Python:  llama-cpp-python ($([ $LLAMA_INSTALLED -eq 1 ] && echo 'OK' || echo 'FAILED'))"
echo ""
echo "  TO UNINSTALL:"
echo "    bash $INSTALL_DIR/uninstall.sh"
echo ""
echo "  NEXT STEP: Restart Claude Code."
echo ""
echo "  After restart, open any project directory and start chatting."
echo "  claude-recall will:"
echo "    • Load context from Obsidian before your first message"
echo "    • Save a session note to Obsidian when you exit"
echo ""
