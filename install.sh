#!/bin/sh
# Installer for local-meeting-recorder (FLY).
#
#   curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/local-meeting-recorder/main/install.sh | sh
#
# Installs uv (if missing), ffmpeg (via Homebrew), ownscribe and the app, then
# registers a login item so the menubar icon starts with your Mac.
#
# Options:
#   --no-login-item   don't start the app at login
#   --uninstall       remove the app, its login item and ownscribe
#                     (settings, recordings and transcripts are kept)
set -eu

REPO="${LMR_REPO:-https://github.com/tuomasharkonen-ux/local-meeting-recorder}"
LABEL="io.github.local-meeting-recorder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="$HOME/.local/bin"
# ownscribe supports Python 3.12 and 3.13; uv fetches it if it isn't installed.
PYTHON="3.12"

login_item=1
action=install
for arg in "$@"; do
  case "$arg" in
    --no-login-item) login_item=0 ;;
    --uninstall) action=uninstall ;;
    -h|--help) echo "usage: install.sh [--no-login-item | --uninstall]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

unload_login_item() {
  if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  fi
}

if [ "$action" = uninstall ]; then
  say "Removing login item"
  unload_login_item
  pkill -f "$BIN_DIR/meeting-recorder" 2>/dev/null || true
  if command -v uv >/dev/null 2>&1; then
    say "Uninstalling the app and ownscribe"
    uv tool uninstall local-meeting-recorder 2>/dev/null || true
    uv tool uninstall ownscribe 2>/dev/null || true
  fi
  echo
  echo "Done. Settings in ~/.config/local-meeting-recorder and recordings in"
  echo "~/ownscribe were left in place; delete them yourself if you want them gone."
  exit 0
fi

# -- requirements ------------------------------------------------------------

[ "$(uname -s)" = Darwin ] || fail "macOS only."
[ "$(uname -m)" = arm64 ] || fail "Needs an Apple Silicon Mac (M1 or later)."

macos="$(sw_vers -productVersion)"
major="${macos%%.*}"
minor="$(echo "$macos" | cut -d. -f2)"
minor="${minor:-0}"
if [ "$major" -lt 14 ] || { [ "$major" -eq 14 ] && [ "$minor" -lt 2 ]; }; then
  fail "Needs macOS 14.2 or later for system audio capture (you have $macos)."
fi

# -- dependencies ------------------------------------------------------------

if ! command -v ffmpeg >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || fail "ffmpeg is required. Install Homebrew from https://brew.sh and re-run, or install ffmpeg another way."
  say "Installing ffmpeg"
  brew install ffmpeg
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$BIN_DIR:$PATH"
fi

say "Installing ownscribe (recording, transcription and diarization engine)"
uv tool install --python "$PYTHON" --upgrade ownscribe

say "Installing local-meeting-recorder"
uv tool install --python "$PYTHON" --force "git+$REPO"

# -- login item --------------------------------------------------------------

unload_login_item
if [ "$login_item" -eq 1 ]; then
  say "Adding login item"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN_DIR/meeting-recorder</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  say "Starting the app"
  nohup "$BIN_DIR/meeting-recorder" >/dev/null 2>&1 &
fi

cat <<'EOF'

Installed. Look for ○ in the menubar; the dashboard is at http://127.0.0.1:8756/

Next steps:
  1. Speaker labels need a free HuggingFace token. Accept the model terms at
     https://huggingface.co/pyannote/speaker-diarization-community-1, create a
     read token at https://huggingface.co/settings/tokens, then paste it via
     the menubar: Configure → Set HuggingFace Token…
  2. The first recording asks for microphone and system audio permission, and
     downloads the speech models (a few GB, once).

To uninstall:
  curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/local-meeting-recorder/main/install.sh | sh -s -- --uninstall
EOF
