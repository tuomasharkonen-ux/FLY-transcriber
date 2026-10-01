"""Per-meeting state: what was filed where, and what the user typed.

Filing used to leave no trace -- a note appeared in a project folder and nothing
recorded that it had happened, so "is this filed?" was unanswerable. This keeps
a small JSON ledger alongside the app's settings.

Keyed by meeting directory *name* rather than full path, so moving the output
directory does not orphan the history.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ensure_private_dir

STATE_PATH = Path("~/.config/fly-transcriber/state.json").expanduser()

_lock = threading.Lock()


@dataclass
class FiledCopy:
    """One note written into one project."""

    project: str
    path: str
    at: str


@dataclass
class MeetingState:
    """What the user has told us about a meeting, and where it has gone."""

    #: Project chosen when recording stopped, before processing finished.
    pending_project: str = ""
    title: str = ""
    #: Diarization label -> real name.
    speaker_names: dict[str, str] = field(default_factory=dict)
    #: Diarization label -> the label it is really the same person as.
    speaker_merges: dict[str, str] = field(default_factory=dict)
    #: The user chose not to file this one; it no longer counts as waiting.
    dismissed: bool = False
    filed: list[FiledCopy] = field(default_factory=list)

    @property
    def is_filed(self) -> bool:
        return bool(self.filed)

    @classmethod
    def from_dict(cls, data: dict) -> "MeetingState":
        return cls(
            pending_project=data.get("pending_project", "") or "",
            title=data.get("title", "") or "",
            speaker_names=dict(data.get("speaker_names") or {}),
            speaker_merges=dict(data.get("speaker_merges") or {}),
            dismissed=bool(data.get("dismissed", False)),
            filed=[
                FiledCopy(
                    project=f.get("project", ""),
                    path=f.get("path", ""),
                    at=f.get("at", ""),
                )
                for f in data.get("filed") or []
                if isinstance(f, dict)
            ],
        )


def _load_raw() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger must never stop a recording; worst case the UI shows
        # meetings as unfiled, which is recoverable by re-filing.
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict) -> None:
    ensure_private_dir(STATE_PATH.parent)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)  # atomic: never leave a half-written ledger


def load_all() -> dict[str, MeetingState]:
    with _lock:
        raw = _load_raw()
    return {
        key: MeetingState.from_dict(value)
        for key, value in raw.items()
        if isinstance(value, dict)
    }


def get(meeting_key: str) -> MeetingState:
    return load_all().get(meeting_key, MeetingState())


def update(meeting_key: str, **changes) -> MeetingState:
    """Merge ``changes`` into a meeting's state and persist."""
    with _lock:
        raw = _load_raw()
        current = MeetingState.from_dict(raw.get(meeting_key) or {})
        for key, value in changes.items():
            if hasattr(current, key):
                setattr(current, key, value)
        raw[meeting_key] = asdict(current)
        _save_raw(raw)
    return current


def record_filed(meeting_key: str, project: str, path: Path) -> MeetingState:
    """Record a filed copy. Rewriting a file already recorded updates its time."""
    with _lock:
        raw = _load_raw()
        current = MeetingState.from_dict(raw.get(meeting_key) or {})
        current.filed = [f for f in current.filed if f.path != str(path)]
        current.filed.append(
            FiledCopy(
                project=project,
                path=str(path),
                at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        current.pending_project = ""
        current.dismissed = False
        raw[meeting_key] = asdict(current)
        _save_raw(raw)
    return current


def awaiting_filing(meetings, ledger: dict[str, MeetingState] | None = None) -> list:
    """Meetings with a finished transcript that are neither filed nor skipped."""
    ledger = load_all() if ledger is None else ledger
    waiting = []
    for meeting in meetings:
        entry = ledger.get(meeting.directory.name, MeetingState())
        if meeting.has_transcript and not entry.is_filed and not entry.dismissed:
            waiting.append(meeting)
    return waiting


def forget(meeting_key: str) -> None:
    with _lock:
        raw = _load_raw()
        raw.pop(meeting_key, None)
        _save_raw(raw)
