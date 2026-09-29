#!/bin/sh
# Installer for FLY-transcriber.
#
#   curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/FLY-transcriber/main/install.sh | sh
#
# Installs uv (if missing), ffmpeg (via Homebrew), ownscribe and the app, the
# speaker model (no HuggingFace account needed) and the speech models, then
# registers a login item so the menubar icon starts with your Mac.
#
# Options:
#   --no-login-item   don't start the app at login
#   --no-warmup       skip the ~3 GB speech model download; it then happens
#                     during the first recording instead
#   --uninstall       remove the app, its login item, models and ownscribe
#                     (settings, recordings and transcripts are kept)
set -eu

REPO="${FLY_REPO:-https://github.com/tuomasharkonen-ux/FLY-transcriber}"
LABEL="io.github.fly-transcriber"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="$HOME/.local/bin"
# ownscribe supports Python 3.12 and 3.13; uv fetches it if it isn't installed.
PYTHON="3.12"
# Pinned to the minor version this app is tested with: the local speaker model
# relies on how ownscribe and pyannote look the model up.
OWNSCRIBE="ownscribe>=0.15,<0.16"

DATA_DIR="$HOME/.local/share/fly-transcriber"
MODEL_DIR="$DATA_DIR/models/pyannote/speaker-diarization-community-1"
MODEL_URL="${FLY_MODEL_URL:-$REPO/releases/download/speaker-model-v1/speaker-diarization-community-1.tar.gz}"
# pyannote/speaker-diarization-community-1 (CC-BY-4.0), as packaged by
# scripts/package_speaker_model.sh.
MODEL_SHA256="5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be  config.yaml
7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e  segmentation/pytorch_model.bin
6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929  embedding/pytorch_model.bin
9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255  plda/plda.npz
325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f  plda/xvec_transform.npz"

login_item=1
warmup=1
action=install
for arg in "$@"; do
  case "$arg" in
    --no-login-item) login_item=0 ;;
    --no-warmup) warmup=0 ;;
    --uninstall) action=uninstall ;;
    -h|--help) echo "usage: install.sh [--no-login-item] [--no-warmup] | --uninstall"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mWarning:\033[0m %s\n' "$*" >&2; }

# Checks every model file against MODEL_SHA256; $1 is the model directory.
model_ok() {
  [ -d "$1" ] && ( cd "$1" && printf '%s\n' "$MODEL_SHA256" | shasum -a 256 -c -s ) 2>/dev/null
}

unload_login_item() {
  if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  fi
}

if [ "$action" = uninstall ]; then
  say "Removing login item"
  unload_login_item
  pkill -f "$BIN_DIR/fly-transcriber" 2>/dev/null || true
  if command -v uv >/dev/null 2>&1; then
    say "Uninstalling the app and ownscribe"
    uv tool uninstall fly-transcriber 2>/dev/null || true
    uv tool uninstall ownscribe 2>/dev/null || true
  fi
  rm -rf "$DATA_DIR"
  echo
  echo "Done. Settings in ~/.config/fly-transcriber, recordings in ~/ownscribe and"
  echo "the speech models in ~/.cache/huggingface were left in place; delete them"
  echo "yourself if you want them gone."
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
uv tool install --python "$PYTHON" --upgrade "$OWNSCRIBE"

say "Installing FLY-transcriber"
uv tool install --python "$PYTHON" --force "git+$REPO"

# -- models ------------------------------------------------------------------

speaker_model=1
if model_ok "$MODEL_DIR"; then
  say "Speaker model already installed"
else
  say "Downloading the speaker model (30 MB)"
  tmp="$(mktemp -d)"
  extracted="$tmp/pyannote/speaker-diarization-community-1"
  if curl -fLsS "$MODEL_URL" -o "$tmp/model.tar.gz" \
      && tar -xzf "$tmp/model.tar.gz" -C "$tmp" \
      && model_ok "$extracted"; then
    rm -rf "$MODEL_DIR"
    mkdir -p "$(dirname "$MODEL_DIR")"
    mv "$extracted" "$MODEL_DIR"
  else
    speaker_model=0
    warn "Could not install the speaker model. Recording works, but speaker labels need a HuggingFace token (see below)."
  fi
  rm -rf "$tmp"
fi

if [ "$warmup" -eq 1 ]; then
  say "Downloading the speech models (about 3 GB, once; this takes a while)"
  "$BIN_DIR/fly-transcriber" warmup \
    || warn "Model download failed; it will be retried during the first recording."
fi

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
    <string>$BIN_DIR/fly-transcriber</string>
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
  nohup "$BIN_DIR/fly-transcriber" >/dev/null 2>&1 &
fi

echo
echo "Installed. Look for ○ in the menubar; the dashboard is at http://127.0.0.1:8756/"
echo
echo "Your first recording asks for microphone and system audio permission."
if [ "$speaker_model" -eq 0 ]; then
  cat <<'EOF'

To get speaker labels without the bundled model, accept the terms at
https://huggingface.co/pyannote/speaker-diarization-community-1, create a read
token at https://huggingface.co/settings/tokens, and paste it via the menubar:
Configure → Set HuggingFace Token…
EOF
fi
cat <<'EOF'

To uninstall:
  curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/FLY-transcriber/main/install.sh | sh -s -- --uninstall
EOF
