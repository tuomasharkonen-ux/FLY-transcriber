#!/bin/sh
# Package the pyannote speaker model for the GitHub release the installer
# downloads it from, so users need no HuggingFace account.
#
#   sh scripts/package_speaker_model.sh
#
# Reads the model from your HuggingFace cache, so accept its terms and let it
# download once (any diarized recording does that). Writes
# dist/speaker-diarization-community-1.tar.gz, laid out to extract straight into
# ~/.local/share/fly-transcriber/models/. Upload it with:
#
#   gh release create speaker-model-v1 dist/speaker-diarization-community-1.tar.gz \
#     --title "Speaker model v1" --notes-file dist/SPEAKER-MODEL-NOTICE.md
#
# The model is CC-BY-4.0, which allows redistribution with attribution; the
# notice written below travels inside the archive and as the release notes.
set -eu

REPO_ID="pyannote/speaker-diarization-community-1"
CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--pyannote--speaker-diarization-community-1"
FILES="config.yaml segmentation/pytorch_model.bin embedding/pytorch_model.bin plda/plda.npz plda/xvec_transform.npz"

snapshot="$(ls -d "$CACHE"/snapshots/*/ 2>/dev/null | head -1)"
[ -n "$snapshot" ] || { echo "Model not in the HuggingFace cache: $CACHE" >&2; exit 1; }

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
dest="$work/$REPO_ID"
mkdir -p "$dest" "$root/dist"

for f in $FILES; do
  mkdir -p "$dest/$(dirname "$f")"
  cp -L "$snapshot/$f" "$dest/$f"
done

cat > "$dest/NOTICE.md" <<EOF
# pyannote speaker-diarization-community-1

Redistributed unmodified by FLY-transcriber so that it can be installed
without a HuggingFace account.

- Source: https://huggingface.co/$REPO_ID (revision $(basename "$snapshot"))
- Authors: pyannote (https://github.com/pyannote/pyannote-audio)
- License: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/

If you use this model outside FLY-transcriber, please get it from the source
above and credit pyannote.
EOF
cp "$dest/NOTICE.md" "$root/dist/SPEAKER-MODEL-NOTICE.md"

# Deterministic archive: sorted names, fixed owner, no macOS metadata.
out="$root/dist/speaker-diarization-community-1.tar.gz"
( cd "$work" && COPYFILE_DISABLE=1 tar --uid 0 --gid 0 --uname "" --gname "" -czf "$out" pyannote )

echo "Wrote $out"
echo "Per-file SHA-256 (must match MODEL_SHA256 in install.sh):"
( cd "$dest" && for f in $FILES; do shasum -a 256 "$f"; done )
