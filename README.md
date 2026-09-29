# FLY — Faithful Logger of Yapping

**Private meeting transcripts for macOS.** Record a meeting from the menubar and
get a transcript that says *who said what*, then hand it to the AI agent in your
project folder to write the notes. Recording, transcription and speaker
detection all run locally on your Mac; no audio or text is sent anywhere.

```
🎙️ Record  →  📝 Transcribe  →  👥 Label speakers  →  🏷️ Name them  →  🤝 Hand over to your agent
            Whisper large-v3     pyannote                              meeting-inbox-to-note skill
```

A short animated walkthrough is in [`docs/how-it-works.html`](docs/how-it-works.html)
(download it and open it in a browser).

## What you get

- **A menubar recorder.** Click to start, click to stop. It captures your
  microphone and system audio, so remote participants are included.
- **Local transcription** with Whisper `large-v3`. This is accurate even for
  languages that smaller models get wrong, such as Finnish.
- **Speaker labels** from pyannote. You give each speaker a real name in the
  dashboard before filing.
- **Filing into projects.** Each transcript lands in a folder your agent works
  in, such as an Obsidian vault or a repo, marked `status: raw`.
- **An agent skill** that turns the raw transcript into a proper note. It checks
  who said what from context, writes in the meeting's language, fixes misheard
  terms and leaves an attribution review for you to spot-check.

There is deliberately **no built-in summary**. A small local model makes
things up, while your own agent has the project context and a stronger model.

## Requirements

- A Mac with Apple Silicon (M1 or later) running **macOS 14.2 or later**
  (needed for system audio capture)
- [Homebrew](https://brew.sh), used to install `ffmpeg`
- About **5 GB of disk space** for the speech models, downloaded during install
- An AI coding agent such as [Claude Code](https://claude.com/claude-code) to
  turn transcripts into notes (optional)

## Install

```bash
curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/FLY-transcriber/main/install.sh | sh
```

The script checks your Mac meets the requirements and then:

1. installs [uv](https://docs.astral.sh/uv/) if you don't have it, and `ffmpeg`
   through Homebrew,
2. installs [ownscribe](https://github.com/paberr/ownscribe) (the recording and
   transcription engine) and this app as `uv` tools,
3. downloads the speaker model (30 MB) and the speech models (about 3 GB), so
   your first recording starts straight away,
4. adds a login item so the app starts with your Mac, and starts it now.

No accounts or tokens are needed. Look for **○** in the menubar. The dashboard
is at <http://127.0.0.1:8756/>.

Options go after `sh -s --`, for example `… | sh -s -- --no-login-item`:

- `--no-login-item`: don't start the app at login.
- `--no-warmup`: skip the 3 GB download for now; it then happens during your
  first recording.

If you'd rather read the script before running it, download
[`install.sh`](install.sh) and run `sh install.sh`.

## First-time setup

### Allow audio access

Your first recording asks for **microphone** and **system audio** access.
Allow both. Without system audio, the other people on a call aren't recorded.

### Tell it about your vocabulary

**Configure → Vocabulary Hints…** takes a comma-separated list of names,
products and jargon. Whisper gets exactly these words wrong, and listing them
measurably helps. Hints are applied when a recording starts, so add them before
the meeting. You can also pin the **Transcription Language…** instead of
relying on auto-detection.

## Using it

1. **Start Recording** from the menubar. The icon shows a timer (`● 3:24`).
2. **Stop Recording** when the meeting ends. Processing starts on its own
   (`◌ Processing`), and live progress is shown in the dashboard.
3. When it's done the menubar shows `○ Ready to file (1)`. Open the dashboard:
   it goes straight to the filing form. Add a title and participants, name each
   speaker (each one is shown with their first line so you can tell them
   apart), pick a project, and file.
4. Ask your agent to process the inbox. The skill turns the transcript into a
   note and deletes the raw file.

Processing isn't instant. It runs at about **0.8× realtime**, so a one-hour
meeting takes around 50 minutes, plus speaker detection.

### Projects

A project is a folder where transcripts are filed. **Configure → New Project…**
creates `~/<name>/meetings/_inbox/` and writes two files at the project root:

- `CLAUDE.md`: tells the agent what the inbox is and what to watch out for.
- `.claude/skills/meeting-inbox-to-note/SKILL.md`: the step-by-step skill for
  turning a transcript into a note.

To use an **existing** folder, such as an Obsidian vault you already have, add
the agent files to it:

```bash
fly-transcriber install-agent ~/path/to/your/project
```

Then add it as a destination in `~/.config/fly-transcriber/settings.toml`:

```toml
[[projects]]
name = "Acme"
path = "~/acme/meetings/_inbox"
naming = "vault"          # 28-09-26-title.md   (or "timestamp": 2026-09-28_1420_title.md)
frontmatter = "obsidian"  # slim vault template (or "generic")
tags = []
```

Existing `CLAUDE.md` or skill files are never overwritten, so you can tailor
them to your project. Put your note template, folder layout and task format in
`CLAUDE.md`, and the skill follows them.

### About speaker labels

Labels come from voice alone, so treat them as a strong hint rather than a
fact. Short replies ("Yeah.", "Right.") and two people sharing one laptop mic
are the usual sources of mistakes. That's why the agent skill checks every
decision and action item against the text before naming anyone, and lists
every change it made. Speaker names are applied only to the filed copy; the
original transcript keeps its anonymous labels, so a wrong mapping can always
be redone.

## Privacy and consent

Everything stays on your Mac: audio, transcripts and the models that produce
them. The only network traffic is the model download during install.
The dashboard listens on `127.0.0.1` only.

**Tell people when you record.** In many places, including the EU, you need
participants' consent to record a meeting. It's your responsibility to ask.

## Configuration

| File | What it is |
|---|---|
| `~/.config/fly-transcriber/settings.toml` | App settings, also editable in the dashboard |
| `~/.config/fly-transcriber/hf_token` | Optional HuggingFace token, only needed without the bundled speaker model |
| `~/.config/fly-transcriber/state.json` | What was filed where, plus the titles and names you typed |
| `~/.config/ownscribe/config.toml` | Generated from settings before each recording; don't edit by hand |
| `~/ownscribe/` | Recordings and original transcripts |
| `~/.local/share/fly-transcriber/models/` | The speaker model |

Useful settings:

- `model`: `large-v3` by default. `medium` or `small` are faster but much less
  accurate outside English.
- `keep_recording`: keeps the WAV file (about 300 MB per hour). Off by default.
- `language`: empty to auto-detect, or a code like `"fi"` or `"en"`.

After editing `settings.toml` by hand, use **Configure → Reload Settings**.
`$HF_TOKEN` overrides the token file if set.

## Uninstall

```bash
curl -LsSf https://raw.githubusercontent.com/tuomasharkonen-ux/FLY-transcriber/main/install.sh | sh -s -- --uninstall
```

This removes the app, ownscribe, the speaker model and the login item. Your
settings, recordings and the speech models are kept; delete
`~/.config/fly-transcriber`, `~/ownscribe` and `~/.cache/huggingface` yourself
if you want them gone.

## Known limitations

- **Apple Silicon and macOS 14.2+ only**, because system audio capture relies
  on Core Audio taps.
- **Meetings are named by hand** at filing. An unnamed meeting is filed as
  `28-09-26-meeting-1420.md`.
- **No macOS notifications** unless the app runs as a signed bundle. The
  menubar icon is the status indicator.

## How it works

The app is a thin front-end for the [ownscribe](https://github.com/paberr/ownscribe)
CLI, which it runs as a subprocess. A few details are worth knowing if you work
on it:

- **Stop sends `SIGINT`.** That ends capture and *starts* processing. Quitting
  during a recording asks first, because that really does discard it.
- **Speaker turns come from ownscribe's JSON, not its markdown.** The markdown
  gives a whole segment to one speaker, which hides turn changes inside it,
  while the JSON keeps a speaker for every word. Segments are split where the
  word-level speaker changes, and single-word flips are ignored as noise.
- **The speaker model is installed locally, so no account is needed.**
  `pyannote/speaker-diarization-community-1` is gated on HuggingFace, but it's
  CC-BY-4.0 and its gate is auto-approved. The installer downloads a copy from
  this repo's releases into `~/.local/share/fly-transcriber/models/`. pyannote
  treats a model name that exists as a folder as a local model *before* it
  contacts the hub, and resolves it against the working directory, so the app
  runs ownscribe from that folder. ownscribe won't diarize without a token, so
  it gets a placeholder that is never sent anywhere. The installer pins
  ownscribe to the version this was tested with. The copy is packaged by
  `scripts/package_speaker_model.sh`.
- **Speaker labels are checked before recording.** When pyannote can't load
  its model, ownscribe still exits successfully with an unlabelled transcript.
  So without a local model, the app checks the gated model up front with a
  `HEAD` on a model file (the metadata API reports success even when access
  hasn't been granted).
- **Summaries were tried and removed.** The local model (`phi-4-mini`) invented
  a decision nobody made and drifted from Finnish into English partway through.
- **Text prompts use `osascript`.** As a menubar-only app, the app can't give
  keyboard focus to a native text field. That's also why most input happens
  in the web dashboard.

## Development

```bash
git clone https://github.com/tuomasharkonen-ux/FLY-transcriber
cd FLY-transcriber
uv run fly-transcriber                      # run from source
uv run pytest                               # tests
uv run python scripts/dashboard_preview.py  # dashboard with fake data on :8757
```

The dashboard is Preact + htm, vendored in `static/vendor/`, with no build step
and no npm. The tested logic lives in the non-UI modules; see `CLAUDE.md` for an
architecture overview.

## Credits

Built on [ownscribe](https://github.com/paberr/ownscribe),
[WhisperX](https://github.com/m-bain/whisperX),
[OpenAI Whisper](https://github.com/openai/whisper),
[pyannote.audio](https://github.com/pyannote/pyannote-audio) and
[rumps](https://github.com/jaredks/rumps). The dashboard bundles
[Preact](https://preactjs.com) (MIT) and [htm](https://github.com/developit/htm)
(Apache-2.0); their licences are in `src/fly_transcriber/static/vendor/`.

The installer downloads pyannote's
[speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
model, redistributed unmodified under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Credit for it goes
to [pyannote](https://github.com/pyannote/pyannote-audio). If you use it outside
this app, please get it from the source.

## License

[MIT](LICENSE)
