#!/usr/bin/env bash
# claude-recall installer
# Usage: curl -fsSL https://raw.githubusercontent.com/senapati484/claude-recall/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/senapati484/claude-recall"
RAW_URL="https://raw.githubusercontent.com/senapati484/claude-recall/main"
INSTALL_DIR="$HOME/.claude/skills/claude-recall"
SETTINGS="$HOME/.claude/settings.json"
CONFIG="$HOME/.claude/claude-recall.json"

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
    # Edge case: non-git existing install (curl-installed) — replace cleanly
    info "Replacing non-git install..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 --quiet "$REPO_URL" "$INSTALL_DIR"
  else
    git clone --depth 1 --quiet "$REPO_URL" "$INSTALL_DIR"
  fi
  ok "Cloned → $INSTALL_DIR"
else
  # curl fallback — download each file individually
  mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/references"
  for FILE in \
    "SKILL.md" \
    "scripts/utils.py" \
    "scripts/load_context.py" \
    "scripts/save_context.py" \
    "scripts/recall_update.py" \
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

# ── 5a. Install llama-cpp-python ─────────────────────────────────────────────
echo ""
info "Installing llama-cpp-python..."
try:
    if python3 -c "import llama_cpp" 2>/dev/null; then
        ok "llama-cpp-python already installed"
    else
        echo "  Installing llama-cpp-python (CPU build)..."
        pip3 install llama-cpp-python --quiet 2>/dev/null || pip3 install llama-cpp-python --quiet
        if python3 -c "import llama_cpp" 2>/dev/null; then
            ok "llama-cpp-python installed"
        else
            warn "llama-cpp-python install failed — LLM summaries disabled"
        fi
    fi
except Exception as e:
    warn "llama-cpp-python error: $e — LLM summaries disabled"

# ── 5b. Download Qwen2.5 0.5B GGUF model ─────────────────────────────────────
MODEL_DIR="$HOME/.claude/models"
MODEL_FILE="$MODEL_DIR/qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

echo ""
info "Checking local model..."
mkdir -p "$MODEL_DIR"

if [ -f "$MODEL_FILE" ]; then
    ok "Model already present: $MODEL_FILE"
elif [ -s "$MODEL_FILE" ]; then
    ok "Model file exists: $MODEL_FILE"
else
    echo "  Downloading Qwen2.5 0.5B (~380 MB)..."
    try:
        if command -v curl &>/dev/null; then
            curl -L --progress-bar "$MODEL_URL" -o "$MODEL_FILE" 2>/dev/null || {
                warn "Download failed"
                rm -f "$MODEL_FILE"
            }
        elif command -v wget &>/dev/null; then
            wget -q "$MODEL_URL" -O "$MODEL_FILE" 2>/dev/null || {
                warn "Download failed"
                rm -f "$MODEL_FILE"
            }
        else
            warn "No curl/wget — download manually:"
            warn "  $MODEL_URL"
        fi
        [ -f "$MODEL_FILE" ] && ok "Model saved → $MODEL_FILE"
    except Exception as e:
        warn "Model download error: $e"

    # Verify download
    if [ ! -f "$MODEL_FILE" ]; then
        warn "Model not found — LLM features disabled"
    fi
fi

# ── 5. Register hooks ─────────────────────────────────────────────────────────
echo ""
info "Registering hooks in $SETTINGS..."
SCAN_CMD="python3 $INSTALL_DIR/scripts/scan_project.py"

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
        # Edge case: malformed settings.json — back up and start fresh
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

# ── 6. Create vault folder skeleton ──────────────────────────────────────────
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

# ── 7. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ┌───────────────────────────────┐"
echo "  │   claude-recall installed  ✓  │"
echo "  └───────────────────────────────┘"
echo ""
echo "  Vault:    $VAULT_PATH"
echo "  Notes at: $VAULT_CR/projects/<project>/"
echo "  Model:    $MODEL_FILE"
echo ""
echo "  NEXT STEP: Restart Claude Code."
echo ""
echo "  After restart, open any project directory and start chatting."
echo "  claude-recall will:"
echo "    • Load context from Obsidian before your first message"
echo "    • Save a session note to Obsidian when you exit"
echo ""
echo "  To add permanent project memory, open in Obsidian:"
echo "    $VAULT_CR/projects/<project>/context.md"
echo ""
echo "  Update later:"
echo "    curl -fsSL $RAW_URL/install.sh | bash"
echo ""
