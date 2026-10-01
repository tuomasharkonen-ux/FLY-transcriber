"""Run the ownscribe CLI with MLX Whisper in place of faster-whisper.

ownscribe transcribes with faster-whisper on the CPU, which is most of the wait
after a recording. MLX runs the same Whisper model on the Mac's GPU, about four
times faster. Everything else -- capture, alignment, diarization, the output
files and the log lines ``recorder.py`` reads -- stays ownscribe's: only the
object it calls ``.transcribe(audio, ...)`` on is replaced.

This file runs on ownscribe's Python, not FLY's: ``recorder.ownscribe_command``
starts it with ``-P`` so FLY's own modules beside it are not importable, and it
imports nothing from FLY. It relies on two ownscribe 0.15 internals, which the
weekly upstream workflow checks: ``WhisperXTranscriber._load_model`` sets
``self._model``, and ``_transcribe_inner`` calls ``self._model.transcribe(audio,
...)`` and reads ``"segments"`` and ``"language"`` from the result, as from
whisperx.
"""

from __future__ import annotations

import sys


def repo_for(model: str) -> str:
    """The MLX conversion of a Whisper model size, e.g. ``large-v3``."""
    return f"mlx-community/whisper-{model}-mlx"


def resolve_model(repo: str, snapshot_download=None) -> str:
    """Local path of the weights, downloading them only if they aren't cached.

    The cache is tried first so that a recording never depends on the network.
    """
    if snapshot_download is None:
        from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        return snapshot_download(repo)


def to_whisperx(result: dict) -> dict:
    """mlx-whisper's result in the shape ``whisperx.align`` takes."""
    return {
        "segments": [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result["segments"]
            if s["text"].strip()  # MLX emits empty segments over silence
        ],
        "language": result["language"],
    }


class MlxModel:
    """Stands in for whisperx's pipeline object: same call, same result."""

    def __init__(self, path: str, language: str | None = None) -> None:
        self.path = path
        self.language = language

    def transcribe(self, audio, **_whisperx_options) -> dict:
        import mlx_whisper

        try:
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=self.path,
                verbose=None,
                language=self.language,
                # Priming each 30 s window with the previous one's text made
                # large-v3 repeat phrases and drop sentences; whisperx decodes
                # windows independently too.
                condition_on_previous_text=False,
            )
        finally:
            _release()
        return to_whisperx(result)


def _release() -> None:
    """Free the weights (~3 GB) before alignment and diarization run."""
    try:
        import importlib

        import mlx.core as mx

        holder = importlib.import_module("mlx_whisper.transcribe").ModelHolder
        holder.model = holder.model_path = None
        mx.clear_cache()
    except Exception:
        pass  # only memory is at stake


def install() -> None:
    """Make ownscribe load an ``MlxModel`` wherever it would load faster-whisper."""
    from ownscribe.transcription.whisperx_transcriber import WhisperXTranscriber

    def _load_model(self) -> None:
        cfg = self._tx_config
        self._model = MlxModel(resolve_model(repo_for(cfg.model)), cfg.language or None)

    WhisperXTranscriber._load_model = _load_model


def main() -> None:
    install()
    from ownscribe.cli import cli

    sys.argv[0] = "ownscribe"
    sys.exit(cli())


if __name__ == "__main__":
    main()
