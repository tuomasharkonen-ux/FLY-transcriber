"""FLY-transcriber: the macOS menubar app."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from . import config as appconfig
from .config import Settings, apply_ownscribe_config, load_settings, save_settings
from .diarization import check_diarization
from .dialogs import ask_text
from .filing import file_meeting
from .meetings import list_meetings, parse_meeting_dir, speaker_samples
from .projects import DEFAULT_INBOX, create_project, install_agent_files, slugify
from .recorder import Phase, RunState, Recorder, resolve_ownscribe
from .server import Api, serve_in_background
from .shell import Shell, alert, call_on_main
from .transcript import render as render_turns
from . import state as meeting_state

RECENT_LIMIT = 8


def _duration(meeting) -> float:
    return meeting.duration


def _fmt_duration(seconds: int) -> str:
    return f"{seconds // 60:d}:{seconds % 60:02d}"


class MeetingRecorderApp:
    def __init__(self) -> None:
        self.settings: Settings = load_settings()
        self.recorder = Recorder(self.settings, on_change=self._on_recorder_change)
        self._dirty = threading.Event()
        self._last_phase: Phase = Phase.IDLE
        self._needs_refresh = False
        #: Recordings with a transcript that are neither filed nor skipped.
        self._awaiting = 0

        self._server, self.dashboard_url = self._start_server()
        self.shell = Shell(self.dashboard_url, menu=self._menu)

        self._refresh_awaiting()
        self._render(self.recorder.state)
        # The recorder and the web server run on background threads; all UI
        # mutation happens here, on the main thread, driven by this timer.
        self.shell.every(1.0, self._tick)

    def run(self) -> None:
        self.shell.run()

    # -- right-click menu ----------------------------------------------------

    def _menu(self) -> list:
        """The secondary menu. Everyday use goes through the popover."""
        recording = self.recorder.state.is_recording
        return [
            ("Stop Recording" if recording else "Start Recording", self.toggle_recording),
            None,
            ("Open FLY", lambda _: self.shell.open_window("#/")),
            ("Settings…", lambda _: self.shell.open_window("#/settings")),
            ("New Project…", self.new_project),
            ("Open Recordings Folder", self.open_recordings),
            None,
            ("Advanced", [
                ("Set HuggingFace Token…", self.set_token),
                ("Apply Configuration", self.apply_config),
                ("Reload Settings", self.reload_settings),
                ("Open Settings File", self.open_settings_file),
            ]),
            None,
            ("Quit FLY", self.quit_app),
        ]

    def _refresh_awaiting(self) -> None:
        meetings = list_meetings(self.settings.resolved_output_dir)
        self._awaiting = len(meeting_state.awaiting_filing(meetings))

    # -- recording -----------------------------------------------------------

    def toggle_recording(self, _sender=None) -> None:
        if self.recorder.state.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def stop_recording(self) -> None:
        # SIGINT ends capture and starts processing; nothing to ask here.
        self.recorder.stop()
        self._dirty.set()

    def start_recording(self) -> None:
        state = self.recorder.state
        if state.is_recording:
            return
        # The popover closes on its own once an alert takes focus; closing it
        # first keeps it from flickering behind one.
        self.shell.close_popover()
        if state.is_active:
            alert(
                "Still processing",
                "Transcription is still running. Wait for it to finish before "
                "starting a new recording.",
            )
            return

        # Regenerate the ownscribe config so the token, template and model in
        # effect always match the current settings.
        try:
            apply_ownscribe_config(self.settings)
        except Exception as exc:
            alert("Configuration failed", str(exc))
            return

        # Diarization failures are silent -- ownscribe exits 0 with an unlabelled
        # transcript -- so verify access before committing to a recording.
        if self.settings.diarize:
            access = check_diarization(appconfig.read_hf_token())
            if not access.ok:
                proceed = alert(
                    "Speaker labels unavailable",
                    f"{access.reason}\n\nThe transcript will have no speaker "
                    "labels. Record anyway?",
                    ok="Record",
                    cancel="Cancel",
                )
                if not proceed:
                    return

        try:
            self.recorder.start()
        except Exception as exc:
            alert("Could not start recording", str(exc))
            return
        self._dirty.set()

    def _on_recorder_change(self, _state: RunState) -> None:
        # Called from the reader thread: only flag, never touch the UI here.
        self._dirty.set()

    def _tick(self) -> None:
        state = self.recorder.state
        if state.is_recording or self._dirty.is_set():
            self._dirty.clear()
            self._render(state)
        if state.phase is not self._last_phase:
            self._on_phase_change(state)
            self._last_phase = state.phase
        if self._needs_refresh:
            # Raised by the web UI thread; the icon may only be touched here.
            self._needs_refresh = False
            self._refresh_awaiting()
            self._render(state)

    def _render(self, state: RunState) -> None:
        """The menubar icon: a timer while recording, a count of unfiled ones."""
        if state.is_recording:
            elapsed = _fmt_duration(state.elapsed)
            self.shell.set_status("recording", elapsed, f"Recording — {elapsed}")
        elif state.is_active:
            self.shell.set_status("busy", "", state.detail or "Processing")
        elif state.phase is Phase.FAILED:
            self.shell.set_status("failed", "", f"Failed: {state.error}")
        elif self._awaiting:
            n = self._awaiting
            self.shell.set_status("idle", str(n), f"{n} recording{'s' if n > 1 else ''} to save")
        else:
            self.shell.set_status("idle", "", "FLY")

    def _on_phase_change(self, state: RunState) -> None:
        # Also catches ownscribe stopping itself on the silence timeout.
        if state.phase in (Phase.DONE, Phase.FAILED):
            self._refresh_awaiting()
            self._render(state)
        if state.phase is Phase.DONE:
            self._warn_if_speakers_missing(state)

    # -- web UI backend ------------------------------------------------------

    def _start_server(self):
        api = Api(
            snapshot=self._snapshot,
            file_meeting=self._api_file,
            save_settings=self._api_settings,
            forget=self._api_forget,
            meeting_detail=self._api_meeting_detail,
            reveal=self._api_reveal,
            dismiss=self._api_dismiss,
            record=self._api_record,
        )
        try:
            return serve_in_background(api)
        except OSError as exc:
            # A stale instance holding the port must not stop the recorder.
            alert("FLY is already running?", f"Could not start the UI server: {exc}")
            return None, ""

    def _snapshot(self) -> dict:
        """Everything the dashboard renders. Called from the server thread."""
        run = self.recorder.state
        ledger = meeting_state.load_all()
        meetings = []
        for meeting in list_meetings(self.settings.resolved_output_dir, RECENT_LIMIT):
            entry = ledger.get(meeting.directory.name, meeting_state.MeetingState())
            processing = (
                run.is_active and run.meeting_dir == meeting.directory
            ) or (run.is_active and run.meeting_dir is None and not meeting.has_transcript)
            meetings.append(
                {
                    "name": meeting.directory.name,
                    "title": entry.title or meeting.title,
                    "when": meeting.started.strftime("%d.%m. %H:%M") if meeting.started else "",
                    "duration": _duration(meeting),
                    "has_transcript": meeting.has_transcript,
                    "has_audio": any(meeting.directory.glob("*.wav")),
                    "processing": processing,
                    "speakers": meeting.speakers,
                    "samples": speaker_samples(meeting),
                    "filed": [
                        {"project": f.project, "path": f.path, "at": f.at}
                        for f in entry.filed
                    ],
                    "state": {
                        "title": entry.title,
                        "participants": entry.participants,
                        "speaker_names": entry.speaker_names,
                        "pending_project": entry.pending_project,
                        "dismissed": entry.dismissed,
                    },
                }
            )
        return {
            "run": self._run_summary(run),
            "meetings": meetings,
            "projects": [
                {
                    "name": p.name,
                    "path": str(p.resolved_path),
                    "naming": p.naming,
                    "frontmatter": p.frontmatter,
                }
                for p in self.settings.projects
            ],
            "settings": {
                "model": self.settings.model,
                "language": self.settings.language,
                "silence_timeout": self.settings.silence_timeout,
                "speaker_count": self.settings.min_speakers,
                "hotwords": self.settings.hotwords,
                "mic": self.settings.mic,
                "diarize": self.settings.diarize,
                "keep_recording": self.settings.keep_recording,
            },
        }

    @staticmethod
    def _run_summary(run: RunState) -> dict:
        elapsed = _fmt_duration(run.elapsed)
        if run.is_recording:
            label = "Recording" if run.phase is Phase.RECORDING else "Starting…"
            return {"css": "recording", "label": f"{label} — {elapsed}", "detail": "", "elapsed": elapsed}
        if run.is_active:
            return {
                "css": "busy",
                "label": run.detail or run.phase.value.title(),
                "detail": f"{elapsed} captured",
                "elapsed": elapsed,
            }
        if run.phase is Phase.FAILED:
            return {"css": "failed", "label": "Failed", "detail": run.error, "elapsed": ""}
        return {"css": "idle", "label": "Idle", "detail": "", "elapsed": ""}

    def _api_file(self, name: str, project_name: str, payload: dict) -> dict:
        project = self.settings.project(project_name)
        if project is None:
            raise ValueError(f"Unknown project {project_name!r}")
        directory = self.settings.resolved_output_dir / name
        if not directory.is_dir():
            raise ValueError(f"Unknown meeting {name!r}")

        title = (payload.get("title") or "").strip()
        participants = [str(p).strip() for p in payload.get("participants") or [] if str(p).strip()]
        names = {
            str(k): str(v).strip()
            for k, v in (payload.get("speaker_names") or {}).items()
            if str(v).strip()
        }
        meeting = parse_meeting_dir(directory)
        result = file_meeting(meeting, project, participants, names, title)

        meeting_state.update(
            name, title=title, participants=participants, speaker_names=names
        )
        meeting_state.record_filed(name, project.name, result.path)
        self._needs_refresh = True
        return {"path": str(result.path), "project": project.name}

    def _api_settings(self, payload: dict) -> dict:
        s = self.settings
        s.model = str(payload.get("model", s.model))
        s.language = str(payload.get("language", s.language)).strip()
        s.hotwords = str(payload.get("hotwords", s.hotwords)).strip()
        s.silence_timeout = max(0, int(payload.get("silence_timeout", s.silence_timeout) or 0))
        count = max(0, int(payload.get("speaker_count", s.min_speakers) or 0))
        s.min_speakers = s.max_speakers = count
        s.mic = bool(payload.get("mic", s.mic))
        s.diarize = bool(payload.get("diarize", s.diarize))
        s.keep_recording = bool(payload.get("keep_recording", s.keep_recording))
        save_settings(s)
        apply_ownscribe_config(s)
        return {"ok": True}

    def _meeting_directory(self, name: str) -> Path:
        # The name comes from the URL: accept only a direct child of the output dir.
        root = self.settings.resolved_output_dir
        directory = (root / name).resolve()
        if not name or directory.parent != root.resolve() or not directory.is_dir():
            raise ValueError(f"Unknown meeting {name!r}")
        return directory

    def _api_meeting_detail(self, name: str) -> dict:
        """The full transcript, for the dashboard's single-recording view."""
        meeting = parse_meeting_dir(self._meeting_directory(name))
        entry = meeting_state.get(name)
        return {
            "name": name,
            "turns": [
                {"speaker": t.speaker, "start": t.start, "timestamp": t.timestamp, "text": t.text}
                for t in meeting.turns
            ],
            "markdown": render_turns(meeting.turns, entry.speaker_names),
        }

    def _api_reveal(self, name: str) -> dict:
        directory = self._meeting_directory(name)
        subprocess.run(["open", str(directory)], check=False)
        return {"ok": True}

    def _api_dismiss(self, name: str) -> dict:
        """Mark a recording as not to be filed, so it stops counting as waiting."""
        self._meeting_directory(name)
        meeting_state.update(name, dismissed=True)
        self._needs_refresh = True
        return {"ok": True}

    def _api_forget(self, name: str) -> dict:
        meeting_state.forget(name)
        self._needs_refresh = True
        return {"ok": True}

    def _api_record(self) -> dict:
        """Start or stop from the popover. Starting may show alerts, so it runs
        on the main thread rather than this server thread."""
        call_on_main(self.toggle_recording)
        return {"ok": True}

    # -- checks --------------------------------------------------------------

    def _warn_if_speakers_missing(self, state: RunState) -> None:
        """Surface a diarization that was asked for but did not happen.

        ownscribe reports this only as a log line and still exits 0, so without
        this check a transcript silently arrives with no speakers.
        """
        if not self.settings.diarize or state.meeting_dir is None:
            return
        meeting = parse_meeting_dir(state.meeting_dir)
        if not meeting.has_transcript or meeting.has_speakers:
            return
        access = check_diarization(appconfig.read_hf_token())
        detail = (
            access.reason
            if not access.ok
            else (
                "Diarization produced no speaker labels. The audio may have had "
                "only one speaker, or the model failed to load."
            )
        )
        alert("No speaker labels in transcript", detail)

    # -- menu actions --------------------------------------------------------

    def new_project(self, _sender) -> None:
        """Create a project folder in the home root and register it."""
        name = ask_text(
            "New Project",
            "Project name. Creates ~/<name>/meetings/_inbox/ plus a CLAUDE.md "
            "and an agent skill telling that project's agent what to do with "
            "what lands there.",
        )
        if not name:
            return
        if self.settings.project(name):
            alert("Already exists", f"A project named {name} is configured.")
            return

        preview_path = Path.home() / slugify(name) / DEFAULT_INBOX
        if not alert(
            "Create project?",
            f"Name: {name}\nMeetings land in: {preview_path}\n\n"
            "A CLAUDE.md and the meeting-inbox-to-note skill will be written "
            "unless they already exist.",
            ok="Create",
            cancel="Cancel",
        ):
            return

        try:
            project, written = create_project(name)
        except (OSError, ValueError) as exc:
            alert("Could not create project", str(exc))
            return

        self.settings.projects.append(project)
        save_settings(self.settings)

        wrote = "".join(f"\nWrote {path}" for path in written)
        alert("Project created", f"{project.name}\n{project.resolved_path}{wrote}")
        subprocess.run(["open", str(project.resolved_path.parent.parent)], check=False)

    def reload_settings(self, _sender) -> None:
        """Re-read settings.toml so project edits apply without a restart."""
        self.settings = load_settings()
        self.recorder._settings = self.settings
        self._refresh_awaiting()
        self._render(self.recorder.state)
        alert(
            "Settings reloaded",
            f"{len(self.settings.projects)} project(s) configured.",
        )

    def open_settings_file(self, _sender) -> None:
        appconfig.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not appconfig.SETTINGS_PATH.exists():
            save_settings(self.settings)
        subprocess.run(["open", str(appconfig.SETTINGS_PATH)], check=False)

    def apply_config(self, _sender) -> None:
        try:
            path = apply_ownscribe_config(self.settings)
        except Exception as exc:
            alert("Configuration failed", str(exc))
            return
        alert("Configuration applied", f"Wrote {path}")

    def set_token(self, _sender) -> None:
        token = ask_text(
            "HuggingFace Token",
            "Only needed if the installer did not set up the speaker model.\n"
            "Paste a HuggingFace read token, after accepting the terms for "
            "pyannote/speaker-diarization-community-1.",
        )
        if not token:
            return
        path = appconfig.write_hf_token(token)
        apply_ownscribe_config(self.settings)
        alert("Token saved", f"Stored in {path} (owner-readable only).")

    def open_recordings(self, _sender) -> None:
        path = self.settings.resolved_output_dir
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(path)], check=False)

    def quit_app(self, _sender) -> None:
        if self.recorder.state.is_active:
            confirm = alert(
                "Recording in progress",
                "Quitting now discards the current recording. Quit anyway?",
                ok="Quit",
                cancel="Cancel",
            )
            if not confirm:
                return
            self.recorder.abort()
        self.shell.quit()


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["install-agent"]:
        sys.exit(_install_agent(args[1:]))
    if args == ["warmup"]:
        _extend_path()
        sys.exit(_warmup())
    if args:
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    _extend_path()
    MeetingRecorderApp().run()


#: Where ``uv tool install`` and Homebrew put executables. Started as a login
#: item, the app inherits launchd's minimal PATH, which has neither -- so
#: ownscribe and the ffmpeg it shells out to would not be found.
_TOOL_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def _extend_path() -> None:
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(Path(d).expanduser()) for d in _TOOL_DIRS]
    missing = [d for d in extra if d not in current]
    if missing:
        os.environ["PATH"] = os.pathsep.join([*current, *missing])


USAGE = """usage: fly-transcriber                        start the menubar app
       fly-transcriber install-agent <folder>  add CLAUDE.md and the agent skill
                                               to an existing project folder
       fly-transcriber warmup                  download the speech models now,
                                               not during the first recording"""


def _warmup() -> int:
    """Prefetch the Whisper and alignment models the next recording will use.

    Applies this app's ownscribe config first: ownscribe's own default enables
    local summarization, and warming up with it would download an LLM this app
    never runs. Diarization is skipped -- the installer places that model.
    """
    settings = load_settings()
    apply_ownscribe_config(settings)
    cmd = [*resolve_ownscribe(), "warmup", "--model", settings.model, "--no-diarization"]
    if settings.language:
        cmd += ["--language", settings.language]
    return subprocess.run(cmd, check=False).returncode


def _install_agent(args: list[str]) -> int:
    """Install the agent files into an existing project root."""
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    base = Path(args[0]).expanduser().resolve()
    if not base.is_dir():
        print(f"Not a folder: {base}", file=sys.stderr)
        return 1
    written = install_agent_files(base, base.name)
    for path in written:
        print(f"Wrote {path}")
    if not written:
        print("Nothing to do: CLAUDE.md and the skill already exist.")
    return 0


if __name__ == "__main__":
    main()
