"""Reading completed meetings off disk.

ownscribe writes each meeting to ``<output>/YYYY-MM-DD_HHMM[_title-slug]/``.
With summarization disabled that directory holds ``transcript.md`` and, unless
recordings are kept, nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from .transcript import (
    UNATTRIBUTED,
    duration as _duration,
    Turn,
    load_turns,
    samples,
    speakers,
    transcript_path,
)

_DIR_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{4})(?:_(?P<slug>.+))?$")



@dataclass
class Meeting:
    """A completed (or partial) meeting on disk."""

    directory: Path
    started: datetime | None = None
    title: str = ""

    @property
    def transcript_path(self) -> Path | None:
        """The transcript file, preferring JSON for its word-level speakers."""
        return transcript_path(self.directory)

    @property
    def has_transcript(self) -> bool:
        return self.transcript_path is not None

    @property
    def duration(self) -> float:
        """Length in seconds. Falls back to the last turn when unknown."""
        path = self.transcript_path
        if path is None:
            return 0.0
        seconds = _duration(path)
        if seconds:
            return seconds
        return self.turns[-1].start if self.turns else 0.0

    @cached_property
    def turns(self) -> list[Turn]:
        path = self.transcript_path
        return load_turns(path) if path else []

    @property
    def is_complete(self) -> bool:
        """A transcript is all that is required -- summarization is disabled."""
        return self.has_transcript

    @property
    def has_speakers(self) -> bool:
        """True when the transcript carries real speaker labels.

        A transcript containing only "Unknown" is not diarized in any useful
        sense, so it reports False and triggers the missing-speakers warning.
        """
        return bool(self.speakers)

    @property
    def speakers(self) -> list[str]:
        return speakers(self.turns)

    @property
    def display_name(self) -> str:
        when = self.started.strftime("%d.%m. %H:%M") if self.started else "?"
        return f"{when}  {self.title}" if self.title else when


def speaker_samples(meeting: "Meeting", max_chars: int = 90) -> dict[str, str]:
    """First thing each speaker says, as a hint for naming them.

    Speaker identity cannot be known until after diarization, so this is what
    makes a post-processing "who is SPEAKER_00?" prompt answerable.
    """
    return samples(meeting.turns, max_chars)


def parse_meeting_dir(directory: Path) -> Meeting:
    """Build a :class:`Meeting` from a directory, tolerating partial runs."""
    meeting = Meeting(directory=directory)

    match = _DIR_RE.match(directory.name)
    if match:
        try:
            meeting.started = datetime.strptime(
                f"{match.group('date')} {match.group('time')}", "%Y-%m-%d %H%M"
            )
        except ValueError:
            meeting.started = None
        slug = match.group("slug")
        if slug:
            # Re-running summarization appends a second title slug, so underscores
            # can appear mid-name; treat both separators as spaces.
            meeting.title = slug.replace("-", " ").replace("_", " ").strip()

    return meeting


def list_meetings(output_dir: Path, limit: int | None = None) -> list[Meeting]:
    """Return meetings newest-first."""
    if not output_dir.exists():
        return []
    # Skip dotfolders: opening the output directory in Obsidian or Finder leaves
    # .obsidian / .DS_Store behind, and those are not meetings.
    meetings = [
        parse_meeting_dir(p)
        for p in output_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]
    meetings.sort(
        key=lambda m: (m.started or datetime.min, m.directory.name), reverse=True
    )
    return meetings[:limit] if limit else meetings
