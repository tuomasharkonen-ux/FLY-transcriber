"""Tests for the non-UI logic: config generation, meeting parsing, export."""

from __future__ import annotations

import json
import subprocess
import textwrap
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fly_transcriber import config as appconfig
from fly_transcriber import diarization
from fly_transcriber import state as meeting_state
from fly_transcriber.server import Api, make_server
from fly_transcriber.config import Settings, build_ownscribe_config
from fly_transcriber.diarization import (
    DIARIZATION_REPO,
    LOCAL_MODEL_TOKEN,
    MODEL_FILES,
    check_access,
    check_diarization,
    has_local_model,
)
from fly_transcriber.dialogs import ask_list, ask_text
from fly_transcriber.filing import ATTRIBUTION_NOTE, FilingError, file_meeting
from fly_transcriber.transcript import Turn, load_turns, render
from fly_transcriber.projects import (
    Project,
    SKILL_NAME,
    SKILL_RELPATH,
    create_project,
    install_agent_files,
    filename_for,
    slugify,
)
from fly_transcriber.meetings import (
    list_meetings,
    parse_meeting_dir,
    speaker_samples,
)
from fly_transcriber.recorder import Phase, Recorder, RunState

SUMMARY = textwrap.dedent(
    """\
    # Meeting Summary

    ## Decisions
    - Ship the integration on Friday.

    ## Open Questions
    - Who owns the API key rotation?

    ## To-Dos
    - Create the test accounts (SPEAKER_01).

    ## Summary
    Short overview of the meeting.
    """
)

TRANSCRIPT_DIARIZED = textwrap.dedent(
    """\
    # Transcript

    **SPEAKER_00** [00:03]
    Onko teillä jo ne testitunnukset?

    **SPEAKER_01** [00:12]
    Joo, ne pitää luoda erikseen.

    **SPEAKER_00** [00:20]
    Selvä.
    """
)

TRANSCRIPT_PLAIN = "# Transcript\n\n[00:03] Plain line with no speaker.\n"

TRANSCRIPT_WITH_UNKNOWN = textwrap.dedent(
    """\
    # Transcript

    **SPEAKER_00** [00:03]
    Onko teillä jo?

    **Unknown** [01:19]
    Eiko oikein?

    **SPEAKER_01** [01:30]
    Joo.
    """
)


def make_meeting(tmp_path, name="2026-09-28_1420_test-accounts", *, transcript, summary):
    d = tmp_path / name
    d.mkdir(parents=True)
    if transcript is not None:
        (d / "transcript.md").write_text(transcript, encoding="utf-8")
    if summary is not None:
        (d / "summary.md").write_text(summary, encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def no_local_model(tmp_path_factory, monkeypatch):
    """Keep a speaker model installed on this machine from leaking into tests."""
    monkeypatch.setattr(diarization, "MODELS_DIR", tmp_path_factory.mktemp("models"))


def install_fake_model(models_dir: Path) -> None:
    base = models_dir / DIARIZATION_REPO
    for name in MODEL_FILES:
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_bytes(b"x")


# -- ownscribe config generation ---------------------------------------------


def test_diarization_off_without_token(monkeypatch):
    """Claiming diarization without a token would silently produce no speakers."""
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    doc = build_ownscribe_config(Settings(diarize=True))
    assert doc["diarization"]["enabled"] is False


def test_diarization_on_with_token(monkeypatch):
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "hf_dummy")
    doc = build_ownscribe_config(Settings(diarize=True))
    assert doc["diarization"]["enabled"] is True
    assert doc["diarization"]["hf_token"] == "hf_dummy"


def test_local_model_enables_diarization_without_token(monkeypatch):
    """ownscribe skips diarization without a token, so a placeholder stands in."""
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    install_fake_model(diarization.MODELS_DIR)
    doc = build_ownscribe_config(Settings(diarize=True))
    assert doc["diarization"]["enabled"] is True
    assert doc["diarization"]["hf_token"] == LOCAL_MODEL_TOKEN


def test_real_token_wins_over_placeholder(monkeypatch):
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "hf_dummy")
    install_fake_model(diarization.MODELS_DIR)
    assert build_ownscribe_config(Settings(diarize=True))["diarization"]["hf_token"] == "hf_dummy"


def test_local_model_needs_every_file(tmp_path):
    assert not has_local_model(tmp_path)
    install_fake_model(tmp_path)
    assert has_local_model(tmp_path)
    (tmp_path / DIARIZATION_REPO / "plda" / "plda.npz").unlink()
    assert not has_local_model(tmp_path)


def test_check_diarization_accepts_local_model_without_network(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    install_fake_model(tmp_path)
    assert check_diarization("", tmp_path).ok


def test_check_diarization_without_model_or_token(tmp_path):
    result = check_diarization("", tmp_path)
    assert not result.ok
    assert "installer" in result.reason


def test_config_wires_model_and_disables_summarization(monkeypatch):
    """Summarization is off by design; the transcript is the product."""
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    doc = build_ownscribe_config(Settings(model="large-v3", language="fi"))
    assert doc["summarization"]["enabled"] is False
    assert doc["transcription"]["model"] == "large-v3"
    assert doc["transcription"]["language"] == "fi"


def test_no_summarize_flag_passed():
    assert "--no-summarize" in Recorder(Settings())._cli_args()


def test_optional_transcription_fields_omitted_when_empty(monkeypatch):
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    doc = build_ownscribe_config(Settings())
    assert "hotwords" not in doc["transcription"]
    assert "initial_prompt" not in doc["transcription"]


def test_apply_backs_up_existing_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[audio]\nmic = false\n", encoding="utf-8")
    monkeypatch.setattr(appconfig, "OWNSCRIBE_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(appconfig, "OWNSCRIBE_CONFIG_PATH", cfg)
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")

    appconfig.apply_ownscribe_config(Settings())

    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1
    assert "mic = false" in backups[0].read_text(encoding="utf-8")


def test_token_file_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(appconfig, "APP_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(appconfig, "TOKEN_PATH", tmp_path / "hf_token")
    path = appconfig.write_hf_token("hf_secret")
    assert path.stat().st_mode & 0o077 == 0
    monkeypatch.delenv(appconfig.HF_TOKEN_ENV, raising=False)
    assert appconfig.read_hf_token() == "hf_secret"


def test_env_token_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setattr(appconfig, "TOKEN_PATH", tmp_path / "hf_token")
    (tmp_path / "hf_token").write_text("from_file\n", encoding="utf-8")
    monkeypatch.setenv(appconfig.HF_TOKEN_ENV, "from_env")
    assert appconfig.read_hf_token() == "from_env"


# -- meeting parsing ---------------------------------------------------------


def test_parses_date_and_title(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_PLAIN, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    assert meeting.started is not None
    assert (meeting.started.year, meeting.started.hour) == (2026, 14)
    assert meeting.title == "test accounts"
    assert meeting.is_complete


def test_doubled_title_slug_is_readable(tmp_path):
    """Re-summarizing appends a second slug; the display name must stay legible."""
    d = make_meeting(
        tmp_path,
        name="2026-09-28_1420_login-flow_test-accounts-plan",
        transcript=None,
        summary=None,
    )
    assert parse_meeting_dir(d).title == "login flow test accounts plan"


def test_detects_speakers(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    assert meeting.has_speakers
    assert meeting.speakers == ["SPEAKER_00", "SPEAKER_01"]


def test_unknown_is_not_a_speaker(tmp_path):
    """ownscribe marks unattributed segments "Unknown"; it is not a person."""
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_WITH_UNKNOWN, summary=None)
    meeting = parse_meeting_dir(d)
    assert meeting.speakers == ["SPEAKER_00", "SPEAKER_01"]
    assert "Unknown" not in speaker_samples(meeting)
    # The marker stays in the transcript body, where it flags a real gap.
    assert "**Unknown**" in meeting.transcript_path.read_text(encoding="utf-8")


def test_only_unknown_counts_as_undiarized(tmp_path):
    d = make_meeting(
        tmp_path,
        transcript="# Transcript\n\n**Unknown** [00:01]\nHello.\n",
        summary=None,
    )
    assert parse_meeting_dir(d).has_speakers is False


def test_plain_transcript_has_no_speakers(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_PLAIN, summary=SUMMARY)
    assert parse_meeting_dir(d).speakers == []


def test_incomplete_meeting(tmp_path):
    d = make_meeting(tmp_path, name="2026-09-28_1409", transcript=None, summary=None)
    meeting = parse_meeting_dir(d)
    assert not meeting.is_complete
    assert meeting.title == ""


def test_transcript_alone_is_complete(tmp_path):
    """Summarization is disabled, so no summary.md is ever produced."""
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_DIARIZED, summary=None)
    assert parse_meeting_dir(d).is_complete


def test_unparseable_dir_name_is_tolerated(tmp_path):
    d = make_meeting(tmp_path, name="scratch", transcript=None, summary=None)
    meeting = parse_meeting_dir(d)
    assert meeting.started is None


def test_list_meetings_is_newest_first(tmp_path):
    make_meeting(tmp_path, "2026-09-27_0900_older", transcript=None, summary=None)
    make_meeting(tmp_path, "2026-09-28_1420_newer", transcript=None, summary=None)
    names = [m.directory.name for m in list_meetings(tmp_path)]
    assert names == ["2026-09-28_1420_newer", "2026-09-27_0900_older"]


def test_list_meetings_missing_dir(tmp_path):
    assert list_meetings(tmp_path / "nope") == []


# -- projects and filing -----------------------------------------------------


def test_vault_naming_is_dd_mm_yy(tmp_path):
    d = make_meeting(
        tmp_path,
        name="2026-09-28_1420_acme-design-review",
        transcript=TRANSCRIPT_DIARIZED,
        summary=SUMMARY,
    )
    meeting = parse_meeting_dir(d)
    project = Project(name="Acme", path=str(tmp_path / "inbox"), naming="vault")
    assert filename_for(meeting, project) == "28-09-26-acme-design-review.md"


def test_timestamp_naming_preserves_directory_name(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    project = Project(name="X", path=str(tmp_path), naming="timestamp")
    assert filename_for(meeting, project) == f"{d.name}.md"


def test_slugify():
    assert slugify("Acme UX Review!") == "acme-ux-review"
    assert slugify("") == "meeting"


def test_slugify_transliterates_finnish():
    """Naive ASCII folding would turn 'Ääkköset' into 'kk-set'."""
    assert slugify("Ääkköset ja työvuorot") == "aakkoset-ja-tyovuorot"
    assert slugify("Käyttäjähaastattelut") == "kayttajahaastattelut"


def test_file_meeting_writes_obsidian_frontmatter(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    project = Project(name="Acme", path=str(tmp_path / "inbox"), frontmatter="obsidian")

    result = file_meeting(meeting, project)
    note = result.path.read_text(encoding="utf-8")

    assert result.path.parent == (tmp_path / "inbox")
    assert "type: meeting" in note
    assert "date: 28-09-26" in note  # DD-MM-YY, not ISO
    assert "status: raw" in note
    # Anonymous diarization labels are not participants.
    assert "participants: []" in note
    assert "speakers: [SPEAKER_00, SPEAKER_01]" in note
    assert "## Transcript" in note
    assert "**SPEAKER_01**" in note


def test_filed_note_warns_it_is_unreviewed(tmp_path):
    """The receiving agent must be able to tell machine output from a real note."""
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="Acme", path=str(tmp_path / "inbox"), frontmatter="obsidian")
    note = file_meeting(parse_meeting_dir(d), project).path.read_text(encoding="utf-8")
    assert "not reviewed by a human" in note


def test_filing_never_overwrites(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    project = Project(name="Acme", path=str(tmp_path / "inbox"))

    first = file_meeting(meeting, project).path
    second = file_meeting(meeting, project).path

    assert first != second
    assert second.name.endswith("-2.md")
    assert first.exists() and second.exists()


def test_filing_creates_missing_directory(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="Acme", path=str(tmp_path / "a" / "b" / "inbox"))
    assert file_meeting(parse_meeting_dir(d), project).path.exists()


def test_filing_refuses_meeting_without_transcript(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=None, summary=None)
    project = Project(name="Acme", path=str(tmp_path / "inbox"))
    with pytest.raises(FilingError):
        file_meeting(parse_meeting_dir(d), project)


def test_title_drives_filename(tmp_path):
    """Without summarization nothing else can name the file."""
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=None)
    project = Project(name="Acme", path=str(tmp_path / "inbox"))
    result = file_meeting(parse_meeting_dir(d), project, title="Acme UX Review")
    assert result.path.name == "28-09-26-acme-ux-review.md"


def test_untitled_meeting_falls_back_to_time(tmp_path):
    """A generic slug would collide across meetings on the same day."""
    d = tmp_path / "src" / "2026-09-28_1420"
    d.mkdir(parents=True)
    (d / "transcript.md").write_text(TRANSCRIPT_DIARIZED, encoding="utf-8")
    project = Project(name="Acme", path=str(tmp_path / "inbox"))
    assert file_meeting(parse_meeting_dir(d), project).path.name == (
        "28-09-26-meeting-1420.md"
    )


def test_generic_frontmatter_uses_iso_date(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="X", path=str(tmp_path / "out"), frontmatter="generic")
    note = file_meeting(parse_meeting_dir(d), project).path.read_text(encoding="utf-8")
    assert "date: 2026-09-28" in note


def test_project_roundtrip_through_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(appconfig, "APP_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(appconfig, "SETTINGS_PATH", tmp_path / "settings.toml")

    original = Settings(
        projects=[
            Project(name="Acme", path="~/acme/notes/meetings/_inbox", tags=["acme"]),
            Project(name="Globex", path="~/globex", naming="timestamp"),
        ]
    )
    appconfig.save_settings(original)
    loaded = appconfig.load_settings()

    assert [p.name for p in loaded.projects] == ["Acme", "Globex"]
    assert loaded.project("Acme").tags == ["acme"]
    assert loaded.project("Globex").naming == "timestamp"
    assert loaded.project("missing") is None


def test_invalid_project_values_fall_back():
    """A hand-edited settings file must not break startup."""
    p = Project.from_dict(
        {"name": "X", "path": "/tmp", "naming": "bogus", "frontmatter": "bogus", "x": 1}
    )
    assert p.naming == "vault"
    assert p.frontmatter == "generic"


# -- participants and speaker naming -----------------------------------------


def test_speaker_samples_gives_first_line_each(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    samples = speaker_samples(parse_meeting_dir(d))
    assert list(samples) == ["SPEAKER_00", "SPEAKER_01"]
    assert samples["SPEAKER_00"].startswith("Onko")
    assert samples["SPEAKER_01"].startswith("Joo")


def test_speaker_samples_empty_without_diarization(tmp_path):
    d = make_meeting(tmp_path, transcript=TRANSCRIPT_PLAIN, summary=SUMMARY)
    assert speaker_samples(parse_meeting_dir(d)) == {}


def test_participants_written_to_frontmatter(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="Acme", path=str(tmp_path / "inbox"), frontmatter="obsidian")
    note = file_meeting(
        parse_meeting_dir(d), project, participants=["Sam Virtanen", "Alex"]
    ).path.read_text(encoding="utf-8")
    assert "participants: [Sam Virtanen, Alex]" in note


def test_speaker_names_rewrite_transcript(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="Acme", path=str(tmp_path / "inbox"))
    note = file_meeting(
        parse_meeting_dir(d),
        project,
        speaker_names={"SPEAKER_00": "Sam", "SPEAKER_01": "Alex"},
    ).path.read_text(encoding="utf-8")
    assert "**Sam**" in note and "**Alex**" in note
    assert "**SPEAKER_00**" not in note
    assert "speakers: [Sam, Alex]" in note


def test_partial_speaker_mapping_keeps_unmapped_labels(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="Acme", path=str(tmp_path / "inbox"))
    note = file_meeting(
        parse_meeting_dir(d), project, speaker_names={"SPEAKER_00": "Sam"}
    ).path.read_text(encoding="utf-8")
    assert "**Sam**" in note
    assert "**SPEAKER_01**" in note  # unmapped stays anonymous


def test_speaker_rename_does_not_touch_source_transcript(tmp_path):
    """A wrong mapping must never corrupt the original transcript."""
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    meeting = parse_meeting_dir(d)
    original = meeting.transcript_path.read_text(encoding="utf-8")
    file_meeting(
        meeting,
        Project(name="X", path=str(tmp_path / "inbox")),
        speaker_names={"SPEAKER_00": "Sam"},
    )
    assert meeting.transcript_path.read_text(encoding="utf-8") == original


def test_diarized_note_cautions_about_attribution(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_DIARIZED, summary=SUMMARY)
    project = Project(name="X", path=str(tmp_path / "inbox"), frontmatter="generic")
    note = file_meeting(parse_meeting_dir(d), project).path.read_text(encoding="utf-8")
    assert ATTRIBUTION_NOTE in note


def test_undiarized_note_has_no_attribution_caution(tmp_path):
    d = make_meeting(tmp_path / "src", transcript=TRANSCRIPT_PLAIN, summary=SUMMARY)
    project = Project(name="X", path=str(tmp_path / "inbox"))
    note = file_meeting(parse_meeting_dir(d), project).path.read_text(encoding="utf-8")
    assert ATTRIBUTION_NOTE not in note


def test_render_ignores_blank_names():
    turns = [Turn("SPEAKER_00", 3.0, "hello")]
    assert "**SPEAKER_00**" in render(turns, {})


# -- project creation --------------------------------------------------------


def test_create_project_makes_inbox_and_instructions(tmp_path):
    project, written = create_project("Acme Corp", root=tmp_path)
    assert project.name == "Acme Corp"
    assert (tmp_path / "acme-corp" / "meetings" / "_inbox").is_dir()
    instructions = tmp_path / "acme-corp" / "CLAUDE.md"
    assert instructions in written
    text = instructions.read_text(encoding="utf-8")
    assert "status: raw" in text
    # The scaffold must tell the agent that writing notes is its job.
    assert "does no\nsummarization" in text
    # ...and warn about the transcription errors it will encounter.
    assert "Speech recognition errors" in text
    assert SKILL_NAME in text


def test_create_project_installs_skill(tmp_path):
    _, written = create_project("Acme", root=tmp_path)
    skill = tmp_path / "acme" / SKILL_RELPATH
    assert skill in written
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: meeting-inbox-to-note\n")
    assert "Attribution review" in text


def test_scaffold_explains_speaker_attribution(tmp_path):
    create_project("Acme", root=tmp_path)
    text = (tmp_path / "acme" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Correct an attribution only on clear evidence" in text


def test_create_project_never_clobbers_existing_agent_files(tmp_path):
    base = tmp_path / "acme"
    (base / SKILL_RELPATH).parent.mkdir(parents=True)
    (base / "CLAUDE.md").write_text("my own instructions", encoding="utf-8")
    (base / SKILL_RELPATH).write_text("my own skill", encoding="utf-8")
    _, written = create_project("Acme", root=tmp_path)
    assert written == []
    assert (base / "CLAUDE.md").read_text(encoding="utf-8") == "my own instructions"
    assert (base / SKILL_RELPATH).read_text(encoding="utf-8") == "my own skill"


def test_install_agent_files_into_existing_folder(tmp_path):
    written = install_agent_files(tmp_path, "Vault")
    assert written == [tmp_path / "CLAUDE.md", tmp_path / SKILL_RELPATH]
    assert install_agent_files(tmp_path, "Vault") == []


def test_create_project_without_scaffold(tmp_path):
    _, written = create_project("Bare", root=tmp_path, scaffold=False)
    assert written == []
    assert not (tmp_path / "bare" / "CLAUDE.md").exists()
    assert not (tmp_path / "bare" / SKILL_RELPATH).exists()


def test_create_project_rejects_empty_name(tmp_path):
    with pytest.raises(ValueError):
        create_project("   ", root=tmp_path)


def test_create_project_is_idempotent(tmp_path):
    create_project("Acme", root=tmp_path)
    project, _ = create_project("Acme", root=tmp_path)
    assert Path(project.path).is_dir()


# -- recorder ----------------------------------------------------------------


def test_cli_args_reflect_settings():
    rec = Recorder(Settings(model="large-v3", language="fi", diarize=True, mic=False))
    args = rec._cli_args()
    assert args[:3] == ["--no-summarize", "--model", "large-v3"]
    assert "--diarize" in args
    assert "--no-mic" in args
    assert "fi" in args


def test_recordings_not_kept_by_default():
    """WAVs run ~300 MB/hour, so keeping them is opt-in."""
    assert Settings().keep_recording is False
    assert "--no-keep-recording" in Recorder(Settings())._cli_args()


def test_keep_recording_can_be_turned_on():
    args = Recorder(Settings(keep_recording=True))._cli_args()
    assert "--keep-recording" in args


def test_keep_recording_reaches_ownscribe_config(monkeypatch):
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    assert build_ownscribe_config(Settings())["output"]["keep_recording"] is False
    assert (
        build_ownscribe_config(Settings(keep_recording=True))["output"][
            "keep_recording"
        ]
        is True
    )


def test_json_output_requested(monkeypatch):
    """Word-level speakers exist only in the JSON output."""
    monkeypatch.setattr(appconfig, "read_hf_token", lambda: "")
    assert build_ownscribe_config(Settings())["output"]["format"] == "json"
    args = Recorder(Settings())._cli_args()
    assert args[args.index("--format") + 1] == "json"


def test_cli_args_omit_diarize_when_disabled():
    assert "--diarize" not in Recorder(Settings(diarize=False))._cli_args()


def test_progress_parsing_advances_phases():
    rec = Recorder(Settings())
    rec.state = RunState(phase=Phase.STARTING)

    rec._interpret("  Recording: 01:05")
    assert rec.state.phase is Phase.RECORDING
    assert rec.state.elapsed == 65

    rec._interpret("Transcribing")
    assert rec.state.phase is Phase.TRANSCRIBING

    rec._interpret("Summarizing")
    assert rec.state.phase is Phase.SUMMARIZING


def test_phases_never_go_backwards():
    """The output log is cumulative, so an earlier marker must not rewind state."""
    rec = Recorder(Settings())
    rec.state = RunState(phase=Phase.SUMMARIZING)
    rec._interpret("Transcribing")
    assert rec.state.phase is Phase.SUMMARIZING


def test_run_state_flags():
    assert RunState(phase=Phase.RECORDING).is_recording
    assert RunState(phase=Phase.TRANSCRIBING).is_active
    assert not RunState(phase=Phase.TRANSCRIBING).is_recording
    assert not RunState(phase=Phase.DONE).is_active
    assert not RunState(phase=Phase.IDLE).is_active


# -- dialogs -----------------------------------------------------------------


def _fake_run(stdout="", returncode=0):
    def run(cmd, **kwargs):
        run.script = cmd[-1]
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    return run


def test_ask_text_returns_entered_value(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run("Acme UX Review\n"))
    assert ask_text("t", "m") == "Acme UX Review"


def test_ask_text_none_on_cancel(monkeypatch):
    """A cancelled osascript dialog exits non-zero."""
    monkeypatch.setattr(subprocess, "run", _fake_run("", returncode=1))
    assert ask_text("t", "m") is None


def test_ask_text_none_on_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run("  \n"))
    assert ask_text("t", "m") is None


def test_ask_text_handles_skip_button(monkeypatch):
    """Skip is an ordinary button, so the script must check which was pressed."""
    run = _fake_run("")
    monkeypatch.setattr(subprocess, "run", run)
    ask_text("t", "m")
    assert 'if button returned of reply is "Skip" then return' in run.script
    assert "if gave up of reply then return" in run.script


def test_reply_is_bound_to_a_variable(monkeypatch):
    """AppleScript reassigns `result` after every statement.

    Reading the dialog reply from `result` means the first `if` that inspects it
    destroys it, and the next line dies with "variable result is not defined" --
    which the caller cannot distinguish from the user pressing Skip. The typed
    answer is silently discarded.
    """
    run = _fake_run("x")
    monkeypatch.setattr(subprocess, "run", run)
    ask_text("t", "m")
    assert "set reply to display dialog" in run.script
    assert "of result" not in run.script


def test_enter_selects_ok_not_skip(monkeypatch):
    """Enter activates the default button; it must be OK."""
    run = _fake_run("x")
    monkeypatch.setattr(subprocess, "run", run)
    ask_text("t", "m")
    assert 'default button "OK"' in run.script


def test_dialog_activates_itself(monkeypatch):
    """Without activation the dialog cannot take keyboard focus."""
    run = _fake_run("x")
    monkeypatch.setattr(subprocess, "run", run)
    ask_text("t", "m")
    assert "tell me to activate" in run.script


def test_quotes_are_escaped(monkeypatch):
    """An unescaped quote would break the AppleScript, not just the text."""
    run = _fake_run("x")
    monkeypatch.setattr(subprocess, "run", run)
    ask_text('He said "hi"', "back\\slash")
    assert '\\"hi\\"' in run.script
    assert "back\\\\slash" in run.script


def test_ask_list_splits_and_strips(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(" Sam , Alex ,,\n"))
    assert ask_list("t", "m") == ["Sam", "Alex"]


def test_ask_list_empty_on_skip(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run("", returncode=1))
    assert ask_list("t", "m") == []


def test_ask_text_survives_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert ask_text("t", "m") is None


# -- transcript turns --------------------------------------------------------


def _json_transcript(tmp_path, segments):
    import json

    d = tmp_path / "2026-09-28_1420"
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(
        json.dumps({"segments": segments, "language": "fi"}), encoding="utf-8"
    )
    return d


def _w(text, start, speaker):
    return {"word": text, "start": start, "end": start + 0.3, "speaker": speaker}


def test_segment_splits_where_word_speaker_changes(tmp_path):
    """The whole point: ownscribe collapses this into one speaker block."""
    d = _json_transcript(
        tmp_path,
        [
            {
                "text": "En ma tieda. Mita mulle tulee mieleen?",
                "start": 11.0,
                "speaker": "SPEAKER_00",
                "words": [
                    _w("En", 11.0, "SPEAKER_00"),
                    _w("ma", 11.3, "SPEAKER_00"),
                    _w("tieda.", 11.6, "SPEAKER_00"),
                    _w("Mita", 12.0, "SPEAKER_01"),
                    _w("mulle", 12.3, "SPEAKER_01"),
                    _w("tulee", 12.6, "SPEAKER_01"),
                ],
            }
        ],
    )
    turns = load_turns(d / "transcript.json")
    assert [t.speaker for t in turns] == ["SPEAKER_00", "SPEAKER_01"]
    assert turns[0].text == "En ma tieda."
    assert turns[1].text.startswith("Mita mulle")


def test_single_word_flip_does_not_split(tmp_path):
    """A one-word flip mid-sentence is a boundary artefact, not a turn."""
    d = _json_transcript(
        tmp_path,
        [
            {
                "text": "a b c d",
                "start": 0.0,
                "speaker": "SPEAKER_00",
                "words": [
                    _w("a", 0.0, "SPEAKER_00"),
                    _w("b", 0.3, "SPEAKER_00"),
                    _w("c", 0.6, "SPEAKER_01"),
                    _w("d", 0.9, "SPEAKER_00"),
                ],
            }
        ],
    )
    turns = load_turns(d / "transcript.json")
    assert [t.speaker for t in turns] == ["SPEAKER_00"]


def test_same_speaker_shares_one_block_but_keeps_timestamps(tmp_path):
    """One label, but every line keeps its own time -- as ownscribe rendered."""
    d = _json_transcript(
        tmp_path,
        [
            {"text": "one", "start": 0.0, "speaker": "S0", "words": [_w("one", 0.0, "S0")]},
            {"text": "two", "start": 61.0, "speaker": "S0", "words": [_w("two", 61.0, "S0")]},
        ],
    )
    out = render(load_turns(d / "transcript.json"))
    assert out.count("**S0**") == 1
    assert "[01:01] two" in out


def test_segment_without_words_kept_whole(tmp_path):
    d = _json_transcript(
        tmp_path, [{"text": "unaligned text", "start": 5.0, "speaker": "S0"}]
    )
    turns = load_turns(d / "transcript.json")
    assert [t.text for t in turns] == ["unaligned text"]


def test_json_preferred_over_markdown(tmp_path):
    d = _json_transcript(
        tmp_path, [{"text": "from json", "start": 0.0, "speaker": "S0"}]
    )
    (d / "transcript.md").write_text("# Transcript\n\n[00:00] from markdown\n", "utf-8")
    meeting = parse_meeting_dir(d)
    assert meeting.transcript_path.name == "transcript.json"
    assert "from json" in render(meeting.turns)


def test_markdown_still_readable(tmp_path):
    """Recordings made before the JSON switch must still file."""
    d = make_meeting(tmp_path / "legacy", transcript=TRANSCRIPT_DIARIZED, summary=None)
    meeting = parse_meeting_dir(d)
    assert meeting.speakers == ["SPEAKER_00", "SPEAKER_01"]
    assert meeting.has_transcript


def test_render_applies_names(tmp_path):
    turns = [Turn("SPEAKER_00", 3.0, "moi"), Turn("SPEAKER_01", 9.0, "hei")]
    out = render(turns, {"SPEAKER_00": "Alex"})
    assert "**Alex** [00:03]" in out
    assert "**SPEAKER_01** [00:09]" in out


def test_timestamps_formatted_from_seconds():
    assert Turn("S", 125.0, "x").timestamp == "02:05"


# -- filing ledger -----------------------------------------------------------


def test_ledger_records_filing(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_state, "STATE_PATH", tmp_path / "state.json")
    meeting_state.update("m1", title="Design review", participants=["Sam"])
    meeting_state.record_filed("m1", "Acme", Path("/x/28-09-26-design-review.md"))

    entry = meeting_state.get("m1")
    assert entry.is_filed
    assert entry.title == "Design review"
    assert entry.filed[0].project == "Acme"
    assert entry.filed[0].at  # timestamped


def test_ledger_clears_pending_on_file(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_state, "STATE_PATH", tmp_path / "state.json")
    meeting_state.update("m1", pending_project="Acme")
    assert meeting_state.get("m1").pending_project == "Acme"
    meeting_state.record_filed("m1", "Acme", Path("/x/a.md"))
    assert meeting_state.get("m1").pending_project == ""


def test_awaiting_filing_skips_filed_dismissed_and_unfinished(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_state, "STATE_PATH", tmp_path / "state.json")
    src = tmp_path / "src"
    for name in ("2026-09-28_0900", "2026-09-28_1000", "2026-09-28_1100"):
        make_meeting(src, name, transcript=TRANSCRIPT_DIARIZED, summary=None)
    make_meeting(src, "2026-09-28_1200", transcript=None, summary=None)  # processing
    meeting_state.record_filed("2026-09-28_0900", "Acme", Path("/x/a.md"))
    meeting_state.update("2026-09-28_1000", dismissed=True)

    waiting = meeting_state.awaiting_filing(list_meetings(src))
    assert [m.directory.name for m in waiting] == ["2026-09-28_1100"]


def test_filing_a_skipped_meeting_unskips_it(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_state, "STATE_PATH", tmp_path / "state.json")
    meeting_state.update("m1", dismissed=True)
    meeting_state.record_filed("m1", "Acme", Path("/x/a.md"))
    assert meeting_state.get("m1").dismissed is False


def test_ledger_unknown_meeting_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_state, "STATE_PATH", tmp_path / "state.json")
    assert meeting_state.get("nope").is_filed is False


def test_corrupt_ledger_does_not_raise(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(meeting_state, "STATE_PATH", path)
    # A broken ledger must never block recording or filing.
    assert meeting_state.load_all() == {}
    meeting_state.update("m1", title="x")
    assert meeting_state.get("m1").title == "x"


def test_ledger_write_is_atomic(tmp_path, monkeypatch):
    """A half-written ledger would lose every meeting's filing history."""
    path = tmp_path / "state.json"
    monkeypatch.setattr(meeting_state, "STATE_PATH", path)
    meeting_state.update("m1", title="x")
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["m1"]["title"] == "x"


# -- web server --------------------------------------------------------------


def _client(api):
    server = make_server(api, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}", server


def _stub_api(**overrides):
    calls = {}

    def snapshot():
        return {"run": {"label": "Idle"}, "meetings": [], "projects": [], "settings": {}}

    def file_meeting(meeting, project, payload):
        calls["file"] = (meeting, project, payload)
        return {"path": "/x/a.md", "project": project}

    def save_settings(payload):
        calls["settings"] = payload
        return {"ok": True}

    def forget(meeting):
        calls["forget"] = meeting
        return {"ok": True}

    api = Api(
        snapshot=overrides.get("snapshot", snapshot),
        file_meeting=overrides.get("file_meeting", file_meeting),
        save_settings=overrides.get("save_settings", save_settings),
        forget=overrides.get("forget", forget),
    )
    return api, calls


def test_server_serves_state_and_static():
    api, _ = _stub_api()
    base, server = _client(api)
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/state").read())["run"]
        page = urllib.request.urlopen(base + "/").read().decode()
        assert 'id="root"' in page
        vendor = urllib.request.urlopen(base + "/vendor/htm-preact-3.1.1.js")
        assert vendor.headers["Content-Type"].startswith("text/javascript")
    finally:
        server.shutdown()


def test_server_serves_meeting_detail():
    api, _ = _stub_api()
    api.meeting_detail = lambda name: {"name": name, "turns": [], "markdown": ""}
    base, server = _client(api)
    try:
        data = json.loads(urllib.request.urlopen(base + "/api/meeting?name=2026-09-28_1420").read())
        assert data["name"] == "2026-09-28_1420"
    finally:
        server.shutdown()


def test_server_meeting_detail_errors_are_404():
    api, _ = _stub_api()
    base, server = _client(api)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/api/meeting?name=x")
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_server_rejects_path_traversal():
    """The requested path comes from the URL and must stay inside static/."""
    api, _ = _stub_api()
    base, server = _client(api)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/../config.py")
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_server_files_meeting():
    api, calls = _stub_api()
    base, server = _client(api)
    try:
        body = json.dumps(
            {"meeting": "m1", "project": "Acme", "title": "T", "participants": ["Sam"]}
        ).encode()
        req = urllib.request.Request(
            base + "/api/file", data=body, headers={"Content-Type": "application/json"}
        )
        assert json.loads(urllib.request.urlopen(req).read())["path"] == "/x/a.md"
        assert calls["file"][0] == "m1"
        assert calls["file"][2]["participants"] == ["Sam"]
    finally:
        server.shutdown()


def test_server_dismisses_meeting():
    api, calls = _stub_api()
    api.dismiss = lambda meeting: calls.setdefault("dismiss", meeting) and {"ok": True}
    base, server = _client(api)
    try:
        req = urllib.request.Request(
            base + "/api/dismiss",
            data=json.dumps({"meeting": "m1"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read()) == {"ok": True}
        assert calls["dismiss"] == "m1"
    finally:
        server.shutdown()


def test_server_reports_errors_as_json():
    """A blank 500 in the browser would give no clue what went wrong."""

    def boom(*_a):
        raise ValueError("Unknown project 'Nope'")

    api, _ = _stub_api(file_meeting=boom)
    base, server = _client(api)
    try:
        req = urllib.request.Request(
            base + "/api/file", data=b"{}", headers={"Content-Type": "application/json"}
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert "Unknown project" in json.loads(exc.value.read())["error"]
    finally:
        server.shutdown()


def test_server_binds_loopback_only():
    api, _ = _stub_api()
    server = make_server(api, port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
