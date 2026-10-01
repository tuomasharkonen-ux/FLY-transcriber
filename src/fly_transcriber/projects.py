"""Project destinations a finished meeting can be filed into.

A project is a folder some agent watches -- typically an Obsidian vault with its
own naming and frontmatter conventions. The recorder drops raw material there;
curating it into a proper note is the receiving agent's job.
"""

from __future__ import annotations

import re
import unicodedata
from importlib import resources
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Filename conventions.
#:  ``vault``     -> ``28-09-26-meeting-title.md`` (dd-mm-yy)
#:  ``timestamp`` -> ``2026-09-28_1420_meeting-title.md`` (sorts chronologically)
NAMING_STYLES = ("vault", "timestamp")

#: Frontmatter shapes. ``obsidian`` is a slim vault-note template (dd-mm-yy
#: date, tags, a banner for the agent); ``generic`` is the tool's own format.
FRONTMATTER_STYLES = ("obsidian", "generic")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class Project:
    """A destination folder for finished meetings."""

    name: str
    path: str
    naming: str = "vault"
    frontmatter: str = "obsidian"
    #: Seeded into frontmatter. Left empty by default -- guessing tags is the
    #: kind of plausible-looking fabrication this pipeline is prone to.
    tags: list[str] = field(default_factory=list)

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        project = cls(**filtered)
        if project.naming not in NAMING_STYLES:
            project.naming = "vault"
        if project.frontmatter not in FRONTMATTER_STYLES:
            project.frontmatter = "generic"
        return project


#: Finnish and Swedish vowels must transliterate rather than be stripped --
#: naive ASCII folding turns "Ääkköset" into "kk-set".
_TRANSLITERATE = str.maketrans({"ä": "a", "ö": "o", "å": "a", "ü": "u", "é": "e"})


def slugify(text: str, max_length: int = 60) -> str:
    """Lowercase kebab-case slug, as both naming styles expect."""
    folded = text.casefold().translate(_TRANSLITERATE)
    # Decompose anything remaining and drop the combining marks.
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(c)
    )
    slug = _SLUG_RE.sub("-", folded).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0]
    return slug or "meeting"


#: Where meetings land inside a newly created project.
DEFAULT_INBOX = "meetings/_inbox"

AGENT_INSTRUCTIONS = """\
# {name} — Agent Instructions

## Meeting inbox

Meeting transcripts land in `{inbox}/`. Each file is one meeting, produced by
FLY-transcriber: recorded locally, transcribed with WhisperX (large-v3)
and diarized with pyannote.

**These are raw transcripts, not notes.** They carry `status: raw` in
frontmatter. Use that to find your queue.

### What to do with a file

The `{skill}` skill in `.claude/skills/` walks through this in
detail; use it when asked to process the inbox.

1. Read the transcript.
2. Write the meeting notes yourself, following this project's conventions --
   summary, decisions, open questions, to-dos, or whatever fits here.
3. Delete the inbox file once notes are written, so the queue stays accurate.

Writing the notes is deliberately left to you. The recorder does no
summarization: the small local model available for it invented decisions that
were never made and drifted out of the meeting's language partway through. You
have the better model and the project context.

### What to watch for

- **Speech recognition errors** are common with names, products and jargon.
  A garbled phrase is sometimes rendered as plausible-sounding nonsense rather
  than as obvious noise. When something reads oddly, treat it as suspect.
- **`speakers:`** are anonymous labels (`SPEAKER_00`) unless they were mapped
  to real names at saving time.
- **`participants:`** is a roster typed by hand when the recording stopped. It
  is not linked to the speaker labels and may be incomplete.
- **Speaker counts are auto-detected** and can be wrong -- both merging two
  quiet people into one label and splitting one person across two.

### Speaker attribution

Diarization tells speakers apart by voice only, and some lines are attributed
to the wrong person -- between people sharing a microphone in the room, but
also between them and remote participants. Speaker names mapped at saving are
applied to every line the label covers, so a swapped line carries a real name
and looks trustworthy.

Before attributing decisions, action items or opinions to anyone, check the
attribution against the text:

- Being addressed by name ("Sam, what do you think?") usually means the next
  turn is that person.
- First-person claims ("I set that up last week") should fit what that person
  does or owns.
- A question and its answer are rarely the same speaker; one argument split
  across two labels mid-sentence is usually one speaker.

Correct an attribution only on clear evidence like that. Where it stays
ambiguous, write both names ("Alex / Sam") rather than picking one. The
transcript file is deleted after notes are written, so the notes are where any
correction has to be right.

When the transcript does not support a claim, do not make it.
"""


#: Where the bundled agent skill is installed, relative to a project root.
SKILL_NAME = "meeting-inbox-to-note"
SKILL_RELPATH = Path(".claude") / "skills" / SKILL_NAME / "SKILL.md"


def _bundled_skill() -> str:
    return (
        resources.files("fly_transcriber")
        .joinpath("skills", SKILL_NAME, "SKILL.md")
        .read_text(encoding="utf-8")
    )


def install_agent_files(
    base: Path, name: str, inbox: str = DEFAULT_INBOX
) -> list[Path]:
    """Write ``CLAUDE.md`` and the meeting skill into project root ``base``.

    Returns the files written. Existing files are never overwritten: a project
    that already has agent instructions or its own version of the skill keeps
    them.
    """
    written: list[Path] = []
    targets = [
        (base / "CLAUDE.md", AGENT_INSTRUCTIONS.format(name=name, inbox=inbox, skill=SKILL_NAME)),
        (base / SKILL_RELPATH, _bundled_skill()),
    ]
    for path, content in targets:
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def create_project(
    name: str,
    root: Path | None = None,
    inbox: str = DEFAULT_INBOX,
    scaffold: bool = True,
) -> tuple["Project", list[Path]]:
    """Create a new project folder and return it with the agent files written.

    Creates ``<root>/<name>/<inbox>/`` and, when ``scaffold`` is set, a
    ``CLAUDE.md`` and the meeting skill at the project root -- without them, a
    fresh folder has no agent to act on what lands there and the inbox simply
    accumulates.
    """
    if not name.strip():
        raise ValueError("Project name cannot be empty")
    root = root or Path.home()
    folder = slugify(name)
    base = root.expanduser() / folder
    (base / inbox).mkdir(parents=True, exist_ok=True)

    written = install_agent_files(base, name.strip(), inbox) if scaffold else []

    project = Project(
        name=name.strip(),
        path=str(base / inbox),
        naming="vault",
        frontmatter="generic",
    )
    return project, written


@dataclass
class PlanStep:
    """One folder or file that adding a project touches."""

    path: Path
    kind: str  # "folder" | "file"
    exists: bool
    purpose: str


@dataclass
class ProjectPlan:
    """What adding a project will do, shown to the user before it is done."""

    project: Project
    root: Path
    steps: list[PlanStep]
    #: Things worth knowing that are not files, e.g. an existing CLAUDE.md
    #: that will not mention a custom inbox.
    notes: list[str] = field(default_factory=list)
    agent_files: bool = True
    inbox: str = DEFAULT_INBOX


def _clean_inbox(inbox: str) -> str:
    """A relative subfolder of the project root; "" means the root itself."""
    parts = [p for p in inbox.strip().replace("\\", "/").split("/") if p not in ("", ".")]
    if inbox.strip().startswith(("/", "~")) or ".." in parts:
        raise ValueError("The transcripts folder must be inside the project folder")
    return "/".join(parts)


def plan_project(
    name: str,
    folder: str | Path,
    *,
    new: bool,
    inbox: str = DEFAULT_INBOX,
    agent_files: bool = True,
    naming: str = "vault",
    frontmatter: str = "generic",
) -> ProjectPlan:
    """Work out what adding a project would create, without touching disk.

    ``new`` creates a folder named after the project inside ``folder``;
    otherwise ``folder`` is an existing project root (a vault, a repository).
    Raises ``ValueError`` with a message meant for the user.
    """
    name = name.strip()
    if not name:
        raise ValueError("Give the project a name")
    if not str(folder).strip():
        raise ValueError("Choose a folder")
    location = Path(str(folder).strip()).expanduser()
    if not location.is_absolute():
        raise ValueError("Use a full path, like ~/Documents")
    inbox = _clean_inbox(inbox)
    if naming not in NAMING_STYLES:
        raise ValueError(f"Unknown filename style {naming!r}")
    if frontmatter not in FRONTMATTER_STYLES:
        raise ValueError(f"Unknown frontmatter style {frontmatter!r}")

    if new:
        if not location.is_dir():
            raise ValueError(f"{location} is not a folder")
        root = location / slugify(name)
        if root.exists():
            raise ValueError(
                f"{root} already exists. Pick another name or location, "
                "or add it as an existing folder."
            )
    else:
        root = location
        if not root.is_dir():
            raise ValueError(f"{root} is not a folder")
        if root in (Path.home(), Path("/")):
            # CLAUDE.md in the home folder would apply to every project under it.
            raise ValueError("Choose a project folder, not your whole home folder")

    target = root / inbox if inbox else root
    steps: list[PlanStep] = []
    if new:
        steps.append(PlanStep(root, "folder", False, "The project folder"))
    if inbox:
        steps.append(PlanStep(
            target, "folder", target.is_dir(),
            "Saved transcripts land here, one markdown file per meeting",
        ))

    notes: list[str] = []
    if agent_files:
        instructions, skill = root / "CLAUDE.md", root / SKILL_RELPATH
        steps.append(PlanStep(
            instructions, "file", instructions.exists(),
            "Tells an AI agent working in this folder where transcripts land "
            "and how to turn them into notes",
        ))
        steps.append(PlanStep(
            skill, "file", skill.exists(),
            "The step-by-step skill the agent follows to process a transcript",
        ))
        if instructions.exists() and inbox != DEFAULT_INBOX:
            notes.append(
                f"Your CLAUDE.md is kept as it is. Add a line telling the agent "
                f"that transcripts land in {inbox or 'the project root'}/, since "
                f"the skill otherwise looks in {DEFAULT_INBOX}/."
            )

    project = Project(name=name, path=str(target), naming=naming, frontmatter=frontmatter)
    return ProjectPlan(project, root, steps, notes, agent_files, inbox)


def plan_from_request(payload: dict, existing: list[Project]) -> ProjectPlan:
    """``plan_project`` for a dashboard request, refusing duplicates of ``existing``."""
    plan = plan_project(
        str(payload.get("name") or ""),
        str(payload.get("folder") or ""),
        new=payload.get("kind") == "new",
        inbox=str(payload.get("inbox", DEFAULT_INBOX) or ""),
        agent_files=bool(payload.get("agent_files", True)),
        naming=str(payload.get("naming") or "vault"),
        frontmatter=str(payload.get("frontmatter") or "generic"),
    )
    for other in existing:
        if other.name.casefold() == plan.project.name.casefold():
            raise ValueError(f"There is already a project named {other.name}")
        if other.resolved_path == plan.project.resolved_path:
            raise ValueError(f"{other.name} already saves to that folder")
    return plan


def display_path(path: Path) -> str:
    """``~/acme`` rather than ``/Users/me/acme``: shorter, and what people type."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def plan_to_dict(plan: ProjectPlan) -> dict:
    return {
        "name": plan.project.name,
        "root": display_path(plan.root),
        "inbox": display_path(plan.project.resolved_path),
        "steps": [
            {"path": display_path(s.path), "kind": s.kind, "exists": s.exists, "purpose": s.purpose}
            for s in plan.steps
        ],
        "notes": plan.notes,
    }


def apply_plan(plan: ProjectPlan) -> list[Path]:
    """Create what ``plan`` describes. Returns the files and folders created."""
    created = [s.path for s in plan.steps if not s.exists and s.kind == "folder"]
    plan.project.resolved_path.mkdir(parents=True, exist_ok=True)
    if plan.agent_files:
        created += install_agent_files(plan.root, plan.project.name, plan.inbox or ".")
    return created


def filename_for(meeting, project: Project, title: str | None = None) -> str:
    """Build the destination filename for ``meeting`` under ``project``.

    ``title`` is the name entered when recording stopped. Without summarization
    there is no generated title to fall back on, so an unnamed meeting is
    identified by its time instead of colliding on a generic slug.
    """
    started = meeting.started
    if project.naming == "timestamp" or started is None:
        return f"{meeting.directory.name}.md"

    name = title or meeting.title
    slug = slugify(name) if name else f"meeting-{started.strftime('%H%M')}"
    return f"{started.strftime('%d-%m-%y')}-{slug}.md"
