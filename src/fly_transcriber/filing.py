"""Filing a finished transcript into a project folder.

What lands is a transcript, not a summary. Summarization is deliberately absent
from this pipeline: the local model available for it fabricated decisions that
were never made and drifted out of the meeting's language partway through. The
agent in the destination project writes the notes, from a transcript it can
trust.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .meetings import Meeting
from .projects import Project, filename_for
from .transcript import merge_speakers, speakers as turn_speakers
from .transcript import render as render_turns

#: Marks a file as an unprocessed transcript. The receiving agent keys off this
#: to find its queue, and should drop it once notes have been written.
RAW_MARKER = "status: raw"

#: Diarization separates speakers by voice alone, and any pair can be swapped --
#: remote participants included. Since the transcript is deleted once notes are
#: written, the notes-writer is the last chance to catch a misattribution.
ATTRIBUTION_NOTE = (
    "> Speaker labels come from voice alone, and some lines are attributed to "
    "the wrong person. Check attributions against the text (being addressed "
    "by name, first-person claims that fit one person, a question and its "
    "answer) before crediting decisions, action items or opinions. Where it "
    "stays ambiguous, name both people rather than picking one."
)


#: Text that YAML reads back unchanged without quotes: starts with a letter and
#: holds nothing YAML gives meaning to (": " starts a mapping, " #" a comment,
#: "," and brackets end a list item). Anything else is quoted.
_PLAIN_YAML_RE = re.compile(r"[^\W\d_][\w .'()&/+-]*")
_YAML_KEYWORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


def yaml_str(text: str) -> str:
    """``text`` as a YAML scalar: bare when that is safe, quoted otherwise.

    Titles and names are typed by people, and "Acme: kickoff" or "Sprint #3"
    left bare would break the frontmatter (or silently lose half the title).
    A JSON string is also a valid YAML double-quoted string.
    """
    text = " ".join(str(text).split())  # one line: a newline would end the field
    if (
        _PLAIN_YAML_RE.fullmatch(text)
        and not text.endswith(" ")
        and text.casefold() not in _YAML_KEYWORDS
    ):
        return text
    return json.dumps(text, ensure_ascii=False)


def _yaml_list(items) -> str:
    return "[" + ", ".join(yaml_str(item) for item in items) + "]"


class FilingError(RuntimeError):
    """Raised when a meeting cannot be filed."""


@dataclass(frozen=True)
class FilingResult:
    path: Path
    project: str

    def __str__(self) -> str:
        return f"{self.project}: {self.path}"


def file_meeting(
    meeting: Meeting,
    project: Project,
    speaker_names: dict[str, str] | None = None,
    title: str | None = None,
    speaker_merges: dict[str, str] | None = None,
    replace: Path | None = None,
) -> FilingResult:
    """Write ``meeting``'s transcript into ``project``'s folder.

    ``speaker_names`` maps diarization labels (``SPEAKER_00``) to real names and
    can only be supplied after transcription; the named speakers are the note's
    ``participants``. ``title`` names the meeting;
    without summarization there is nothing to derive one from.

    ``speaker_merges`` maps a label to the label it is the same person as.

    Never overwrites by accident: a colliding name gets a ``-2``, ``-3`` suffix,
    because losing a transcript to a filename clash is far worse than a
    duplicate. The one exception is ``replace`` -- a note this app wrote earlier,
    rewritten in place -- and only while it still carries ``RAW_MARKER``. Once
    the receiving agent has turned it into notes, it is not ours to touch.
    """
    if not meeting.has_transcript:
        raise FilingError(f"{meeting.directory.name} has no transcript yet")

    target_dir = project.resolved_path
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FilingError(f"Cannot create {target_dir}: {exc}") from exc

    if replace is not None and _still_raw(replace):
        path = replace
    else:
        path = _unique_path(target_dir / filename_for(meeting, project, title))
    path.write_text(
        render(meeting, project, speaker_names, title, speaker_merges),
        encoding="utf-8",
    )
    return FilingResult(path=path, project=project.name)


def _still_raw(path: Path) -> bool:
    try:
        return RAW_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for n in range(2, 100):
        candidate = path.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise FilingError(f"Too many files named like {path.name}")


def render(
    meeting: Meeting,
    project: Project,
    speaker_names: dict[str, str] | None = None,
    title: str | None = None,
    speaker_merges: dict[str, str] | None = None,
) -> str:
    """Render the transcript note for a project."""
    started = meeting.started or datetime.fromtimestamp(
        meeting.directory.stat().st_mtime
    )
    heading = " ".join((title or meeting.title or "Meeting").split())

    lines = _frontmatter(
        meeting, project, started, heading, speaker_names, speaker_merges
    )
    lines += ["", f"# {heading}", ""]

    if project.frontmatter == "obsidian":
        lines += [
            "> Transcript from FLY-transcriber: WhisperX (large-v3) with "
            "pyannote diarization, not reviewed by a human. Speech recognition "
            "errors are likely in names and jargon. Write the notes from this, "
            "then remove this file.",
            "",
        ]

    if meeting.has_speakers:
        lines += [ATTRIBUTION_NOTE, ""]

    lines += ["## Transcript", "", _transcript_body(meeting, speaker_names, speaker_merges), ""]
    return "\n".join(lines)


def _frontmatter(
    meeting: Meeting,
    project: Project,
    started: datetime,
    title: str,
    speaker_names: dict[str, str] | None,
    speaker_merges: dict[str, str] | None = None,
) -> list[str]:
    # Show the mapped names where known, anonymous labels otherwise.
    mapping = speaker_names or {}
    merged = turn_speakers(merge_speakers(meeting.turns, speaker_merges))
    speakers = _yaml_list(dict.fromkeys(mapping.get(s, s) for s in merged))
    # Only speakers given a real name count: anonymous labels are not people.
    people = _yaml_list(dict.fromkeys(mapping[s].strip() for s in merged if mapping.get(s, "").strip()))
    title = yaml_str(title)
    source = yaml_str(str(meeting.directory))

    if project.frontmatter == "obsidian":
        return [
            "---",
            f"title: {title}",
            f"date: {started.strftime('%d-%m-%y')}",
            f"tags: {_yaml_list(project.tags)}",
            "type: meeting",
            f"participants: {people}",
            f"speakers: {speakers}",
            f"diarized: {str(meeting.has_speakers).lower()}",
            RAW_MARKER,
            f"source: {source}",
            "---",
        ]
    return [
        "---",
        f"title: {title}",
        f"date: {started.date().isoformat()}",
        f"time: {started.strftime('%H:%M')}",
        "type: meeting",
        f"participants: {people}",
        f"source: {source}",
        f"speakers: {speakers}",
        f"diarized: {str(meeting.has_speakers).lower()}",
        RAW_MARKER,
        "---",
    ]


def _transcript_body(
    meeting: Meeting,
    speaker_names: dict[str, str] | None = None,
    speaker_merges: dict[str, str] | None = None,
) -> str:
    """Render the transcript, one labelled block per speaker turn.

    Rendered from turns rather than copying ownscribe's markdown: the JSON
    carries word-level speakers, so turns that ownscribe collapses into a single
    block are recovered here.
    """
    if not meeting.has_transcript:
        return "_No transcript._"
    names = {k: v.strip() for k, v in (speaker_names or {}).items() if v.strip()}
    return render_turns(merge_speakers(meeting.turns, speaker_merges), names)
