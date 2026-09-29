"""App settings, and generation of the ownscribe config they imply.

This app does not reimplement ownscribe's pipeline -- it drives the ownscribe CLI.
One thing it needs (the HuggingFace token for diarization) has no
command-line flag, so it must live in
``~/.config/ownscribe/config.toml``. This module owns writing that file.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import tomli_w

from .diarization import LOCAL_MODEL_TOKEN, has_local_model
from .projects import Project

APP_CONFIG_DIR = Path("~/.config/fly-transcriber").expanduser()
SETTINGS_PATH = APP_CONFIG_DIR / "settings.toml"
TOKEN_PATH = APP_CONFIG_DIR / "hf_token"

OWNSCRIBE_CONFIG_DIR = Path("~/.config/ownscribe").expanduser()
OWNSCRIBE_CONFIG_PATH = OWNSCRIBE_CONFIG_DIR / "config.toml"

#: Env var checked before the on-disk token file.
HF_TOKEN_ENV = "HF_TOKEN"


@dataclass
class Settings:
    """User-facing settings for the recorder.

    ``large-v3`` is the default: roughly 0.8x realtime on Apple Silicon, but the
    smallest model that transcribes languages like Finnish usefully.
    """

    # Transcription
    model: str = "large-v3"
    language: str = ""  # "" = auto-detect; e.g. "fi", "en"
    initial_prompt: str = ""  # domain vocabulary / names to prime Whisper
    hotwords: str = ""

    # Recording
    mic: bool = True
    silence_timeout: int = 300  # seconds; 0 disables auto-stop

    # Diarization
    diarize: bool = True
    min_speakers: int = 0  # 0 = auto
    max_speakers: int = 0

    # Output
    output_dir: str = "~/ownscribe"
    # WAVs are large (~300 MB/hour). Turn this on to re-test diarization
    # settings on the same audio instead of needing a fresh recording.
    keep_recording: bool = False

    # Project destinations offered when a recording stops.
    projects: list[Project] = field(default_factory=list)

    def project(self, name: str) -> Project | None:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    @property
    def resolved_output_dir(self) -> Path:
        return Path(self.output_dir).expanduser()


def load_settings() -> Settings:
    """Load settings, falling back to defaults for anything missing."""
    if not SETTINGS_PATH.exists():
        return Settings()
    with open(SETTINGS_PATH, "rb") as f:
        data = tomllib.load(f)
    known = {f_.name for f_ in Settings.__dataclass_fields__.values()}
    values = {k: v for k, v in data.items() if k in known}
    # Projects are nested tables; rebuild them as dataclasses so unknown or
    # invalid keys from a hand-edited file cannot break startup.
    values["projects"] = [
        Project.from_dict(p) for p in data.get("projects", []) if isinstance(p, dict)
    ]
    return Settings(**values)


def save_settings(settings: Settings) -> None:
    APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    document = asdict(settings)
    document["projects"] = [p.to_dict() for p in settings.projects]
    with open(SETTINGS_PATH, "wb") as f:
        tomli_w.dump(document, f)


def read_hf_token() -> str:
    """Return the HuggingFace token, or an empty string if none is configured.

    Checks ``$HF_TOKEN`` first so a shell-exported token wins, then the token file.
    The token is never written into app settings, which keeps it out of anything
    that might be committed.
    """
    env = os.environ.get(HF_TOKEN_ENV, "").strip()
    if env:
        return env
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return ""


def write_hf_token(token: str) -> Path:
    """Persist the token with owner-only permissions."""
    APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token.strip() + "\n", encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return TOKEN_PATH


def has_diarization_token() -> bool:
    return bool(read_hf_token()) or has_local_model()


def build_ownscribe_config(settings: Settings) -> dict:
    """Translate app settings into an ownscribe config document."""
    # A local model needs no token, but ownscribe will not diarize without one,
    # so it gets a placeholder. A real token is only needed without the model.
    token = read_hf_token() or (LOCAL_MODEL_TOKEN if has_local_model() else "")

    # Diarization silently produces unlabelled output without a token, so only
    # claim it is enabled when one is actually available.
    diarize_enabled = settings.diarize and bool(token)

    transcription: dict = {
        "model": settings.model,
        "language": settings.language,
    }
    if settings.initial_prompt:
        transcription["initial_prompt"] = settings.initial_prompt
    if settings.hotwords:
        transcription["hotwords"] = settings.hotwords

    return {
        "audio": {
            "backend": "coreaudio",
            "mic": settings.mic,
            "capture_mode": "all",
            "silence_timeout": settings.silence_timeout,
        },
        "transcription": transcription,
        "diarization": {
            "enabled": diarize_enabled,
            "hf_token": token,
            "min_speakers": settings.min_speakers,
            "max_speakers": settings.max_speakers,
            "telemetry": False,
            "device": "auto",  # uses MPS on Apple Silicon when available
        },
        # Summarization is off by design. The transcript is the product; notes
        # are written by the agent in whichever project the meeting is filed to,
        # which handles language and nuance far better than a small local model.
        "summarization": {"enabled": False},
        "output": {
            "dir": settings.output_dir,
            # JSON, not markdown: it is the only format that carries word-level
            # timestamps and per-word speakers. The markdown renderer collapses
            # a segment to a single speaker, hiding turn changes inside it.
            "format": "json",
            "keep_recording": settings.keep_recording,
        },
    }


def apply_ownscribe_config(settings: Settings) -> Path:
    """Write the ownscribe config, backing up any existing file first.

    Returns the path written. The backup is a one-time snapshot per apply, named
    with a timestamp, so hand edits are never silently destroyed.
    """
    OWNSCRIBE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if OWNSCRIBE_CONFIG_PATH.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = OWNSCRIBE_CONFIG_PATH.with_suffix(f".toml.bak-{stamp}")
        shutil.copy2(OWNSCRIBE_CONFIG_PATH, backup)

    document = build_ownscribe_config(settings)
    with open(OWNSCRIBE_CONFIG_PATH, "wb") as f:
        tomli_w.dump(document, f)
    # The file carries the HF token, so keep it owner-readable only.
    OWNSCRIBE_CONFIG_PATH.chmod(0o600)
    return OWNSCRIBE_CONFIG_PATH
