"""Turning ownscribe output into speaker turns.

ownscribe's markdown renderer emits a speaker label only when the *segment*
speaker changes, and a segment carries a single speaker even when the words
inside it were attributed to different people. Rapid exchanges therefore
collapse into one confident-looking monologue:

    **SPEAKER_00** [00:11] En mä tiedä, mitä sulla tulee mieleen.
                   [00:12] Mitä mulle tulee mieleen?

whisperx assigns a speaker to every *word* (``assign_word_speakers``), and
ownscribe preserves that in its JSON output. Reading the JSON and splitting
wherever the word-level speaker changes recovers turns the markdown discards.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: ownscribe's label for speech diarization could not attribute. A gap marker,
#: not a person.
UNATTRIBUTED = "Unknown"

_MD_SPEAKER_RE = re.compile(r"^\*\*(?P<speaker>[^*]+)\*\*\s+\[(?P<time>[\d:]+)\]\s*$")
_MD_LINE_RE = re.compile(r"^\[(?P<time>[\d:]+)\]\s*(?P<text>.*)$")

#: Word runs shorter than this are not treated as a speaker change on their own.
#: A single word attributed to the other speaker mid-sentence is nearly always a
#: boundary artefact, not a real interjection.
MIN_RUN_WORDS = 2

#: A turn this short that doesn't end a sentence may be the opening words of the
#: next speaker's sentence; see ``_repair_sentence_openings``.
MAX_OPENING_WORDS = 2
_SENTENCE_END = (".", "?", "!", "…")


@dataclass
class Turn:
    """A stretch of speech attributed to one speaker."""

    speaker: str | None
    start: float
    text: str

    @property
    def timestamp(self) -> str:
        minutes, seconds = divmod(int(self.start), 60)
        return f"{minutes:02d}:{seconds:02d}"


def _parse_time(text: str) -> float:
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts[-3:]
    return hours * 3600 + minutes * 60 + seconds


def transcript_path(directory: Path) -> Path | None:
    """Return the transcript file, preferring JSON for its word-level detail."""
    for name in ("transcript.json", "transcript.md"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def duration(path: Path) -> float:
    """Recording length in seconds, 0 when unknown."""
    if path.suffix.lower() != ".json":
        return 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0.0
    return float(data.get("duration") or 0.0)


def load_turns(path: Path) -> list[Turn]:
    """Read a transcript into speaker turns.

    Turns are kept at segment granularity rather than merged, so every line
    keeps its own timestamp. Grouping into speaker blocks happens at render
    time.
    """
    if path.suffix.lower() == ".json":
        return _turns_from_json(path)
    return _turns_from_markdown(path)


def _turns_from_json(path: Path) -> list[Turn]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    turns: list[Turn] = []
    for segment in data.get("segments", []):
        if not isinstance(segment, dict):
            continue
        turns.extend(_split_segment(segment))
    return _repair_sentence_openings(turns)


def _repair_sentence_openings(turns: list[Turn]) -> list[Turn]:
    """Give a sentence's first words back to the person who says the rest of it.

    At a speaker change, word timings are least precise, and the first word or
    two of the new speaker's sentence ("Mutta", "Se") often land on the previous
    speaker: a short turn that ends no sentence, followed by another speaker
    carrying on in lowercase. On a 25-minute meeting, this halved the speaker
    changes that cut a sentence in two.
    """
    repaired: list[Turn] = []
    for turn in turns:
        last = repaired[-1] if repaired else None
        if (
            last is not None
            and last.speaker != turn.speaker
            and len(last.text.split()) <= MAX_OPENING_WORDS
            and not last.text.rstrip().endswith(_SENTENCE_END)
            and turn.text[:1].islower()
        ):
            repaired[-1] = Turn(turn.speaker, last.start, f"{last.text} {turn.text}")
            continue
        repaired.append(turn)
    return repaired


def _split_segment(segment: dict) -> list[Turn]:
    """Split one segment wherever the word-level speaker changes."""
    words = [w for w in segment.get("words", []) if isinstance(w, dict)]
    fallback_speaker = segment.get("speaker")
    text = (segment.get("text") or "").strip()

    if not words:
        # No word detail (markdown-era data, or a segment whisperx could not
        # align): keep it whole rather than inventing boundaries.
        if not text:
            return []
        return [Turn(fallback_speaker, float(segment.get("start") or 0.0), text)]

    runs: list[list[dict]] = []
    for word in words:
        speaker = word.get("speaker") or fallback_speaker
        if runs and (runs[-1][0].get("speaker") or fallback_speaker) == speaker:
            runs[-1].append(word)
        else:
            runs.append([word])

    runs = _absorb_short_runs(runs, fallback_speaker)

    turns: list[Turn] = []
    for run in runs:
        run_text = " ".join(
            (w.get("word") or w.get("text") or "").strip() for w in run
        ).strip()
        run_text = re.sub(r"\s+([,.!?:;])", r"\1", run_text)
        if not run_text:
            continue
        turns.append(
            Turn(
                speaker=run[0].get("speaker") or fallback_speaker,
                start=float(run[0].get("start") or segment.get("start") or 0.0),
                text=run_text,
            )
        )
    return turns


def _absorb_short_runs(runs: list[list[dict]], fallback: str | None) -> list[list[dict]]:
    """Fold very short runs into their neighbour.

    A one-word flip mid-sentence is a diarization boundary artefact far more
    often than a real interjection, and splitting on it produces a transcript
    that looks shredded.
    """
    if len(runs) < 2:
        return runs
    merged: list[list[dict]] = []
    for run in runs:
        if merged and len(run) < MIN_RUN_WORDS:
            merged[-1].extend(run)
            continue
        if (
            merged
            and (merged[-1][0].get("speaker") or fallback)
            == (run[0].get("speaker") or fallback)
        ):
            merged[-1].extend(run)
            continue
        merged.append(run)
    return merged


def _turns_from_markdown(path: Path) -> list[Turn]:
    """Parse ownscribe's markdown. Speaker detail is coarser -- no word data."""
    turns: list[Turn] = []
    speaker: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("**Language:") :
            continue
        header = _MD_SPEAKER_RE.match(line)
        if header:
            speaker = header.group("speaker").strip()
            continue
        body = _MD_LINE_RE.match(line)
        if body and body.group("text"):
            turns.append(Turn(speaker, _parse_time(body.group("time")), body.group("text")))
        elif not line.startswith("**"):
            turns.append(Turn(speaker, turns[-1].start if turns else 0.0, line))
    return turns


def speakers(turns: list[Turn]) -> list[str]:
    """Distinct real speakers, in order of first appearance."""
    seen: list[str] = []
    for turn in turns:
        name = turn.speaker
        if name and name != UNATTRIBUTED and name not in seen:
            seen.append(name)
    return seen


def resolve_merges(merges: dict[str, str] | None) -> dict[str, str]:
    """Follow merge chains to their end, ignoring self-merges and cycles."""
    resolved: dict[str, str] = {}
    for label in merges or {}:
        seen = {label}
        target = merges[label]
        while target in merges and target not in seen:
            seen.add(target)
            target = merges[target]
        if target not in seen:
            resolved[label] = target
    return resolved


def merge_speakers(turns: list[Turn], merges: dict[str, str] | None) -> list[Turn]:
    """Reassign turns of merged-away speakers to the speaker they were merged into.

    Diarization can split one voice in two; merging fixes that after the fact.
    """
    mapping = resolve_merges(merges)
    if not mapping:
        return turns
    return [
        Turn(mapping.get(t.speaker or "", t.speaker), t.start, t.text) for t in turns
    ]


def samples(turns: list[Turn], max_chars: int = 90) -> dict[str, str]:
    """First thing each speaker says, as a hint when naming them."""
    found: dict[str, str] = {}
    for turn in turns:
        name = turn.speaker
        if name and name != UNATTRIBUTED and name not in found and turn.text:
            found[name] = turn.text[:max_chars]
    return found


def render(turns: list[Turn], names: dict[str, str] | None = None) -> str:
    """Render turns as markdown.

    Consecutive turns by one speaker form a block under a single label, with
    each line keeping its own timestamp -- the shape ownscribe produces, but
    with the speaker changes it hides.
    """
    mapping = names or {}
    lines: list[str] = []
    previous: str | None = None
    for i, turn in enumerate(turns):
        speaker = turn.speaker or UNATTRIBUTED
        label = mapping.get(turn.speaker or "", speaker) or UNATTRIBUTED
        # Compare what is shown, so two labels given one name read as one speaker.
        if label != previous:
            if i:
                lines.append("")
            lines.append(f"**{label}** [{turn.timestamp}]")
            lines.append(turn.text)
            previous = label
        else:
            lines.append(f"[{turn.timestamp}] {turn.text}")
    return "\n".join(lines).strip()
