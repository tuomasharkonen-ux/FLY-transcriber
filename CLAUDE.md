# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run meeting-recorder                      # run the menubar app (dashboard at http://127.0.0.1:8756/)
uv run pytest                                # all tests
uv run pytest tests/test_core.py -k slugify  # single test / subset
uv run python scripts/dashboard_preview.py   # dashboard on :8757 with fake data, no menubar/ownscribe needed
uv run meeting-recorder install-agent <dir>  # write CLAUDE.md + the agent skill into an existing project folder
sh -n install.sh                             # syntax-check the installer (it installs for real; don't run it casually)
```

No linter or type-checker is configured. Runtime needs macOS 14.2+ on Apple Silicon, `ffmpeg`, and `ownscribe` (`uv tool install ownscribe`).

## What this is

A macOS menubar front-end (rumps, accessory app, no Dock icon) that drives the **ownscribe** CLI to record → transcribe (`large-v3`) → diarize (pyannote via whisperx), then files a speaker-labelled transcript into a project folder for another agent to write notes from. **There is no summarization, by design** — the local model fabricated decisions and drifted languages. Don't reintroduce it; effort goes into transcript quality. The README explains the reasoning behind most design choices; read it before changing behaviour.

## Architecture

- **`recorder.py`** — runs ownscribe as a subprocess (never imported; the CLI is the stable contract). Stop = `SIGINT`, which ends capture and *starts* processing (a handoff, not a kill). A background thread pumps stdout and infers `Phase` from log markers ("Transcribing", "Diarizing", …), only ever advancing forward. Prefers `ownscribe` on PATH over `uvx` because the uvx launcher complicates signalling.
- **`config.py`** — `settings.toml` in `~/.config/local-meeting-recorder/` is the source of truth. `~/.config/ownscribe/config.toml` is *generated* from it before every recording (with a `.bak-*` snapshot), because the HF token has no CLI flag. Written `0600`. The token itself lives in a separate `hf_token` file (or `$HF_TOKEN`), never in settings.
- **`diarization.py`** — preflight access check. ownscribe exits 0 with an unlabelled transcript when the gated model is unreachable, so the app checks up front. Gating is enforced on file download, so it probes `HEAD /resolve/main/config.yaml` — the metadata API returns 200 even when not granted. The gate that matters is `speaker-diarization-community-1`, not `3.1`.
- **`transcript.py`** — builds `Turn`s from ownscribe's **JSON**, not its markdown: markdown labels whole segments, JSON keeps whisperx's per-word speaker, so segments are split where the word speaker changes (single-word flips absorbed). Markdown parsing is a fallback. `"Unknown"` is ownscribe's gap marker, not a speaker.
- **`meetings.py`** — reads ownscribe output dirs `<output>/YYYY-MM-DD_HHMM[_slug]/` into `Meeting` objects.
- **`projects.py` / `filing.py`** — a project is a watched folder with `naming` (`vault`|`timestamp`) and `frontmatter` (`obsidian`|`generic`) styles. Filing writes one markdown file with `status: raw` + a banner (plus a speaker-attribution caution when diarized); speaker renaming applies only to the filed copy (the original under `~/ownscribe` keeps `SPEAKER_NN`). `create_project` (and the `install-agent` subcommand, via `install_agent_files`) writes a `CLAUDE.md` plus the bundled `meeting-inbox-to-note` skill (`skills/…/SKILL.md`, shipped as package data, installed to `.claude/skills/`) and never overwrites existing ones.
- **`state.py`** — JSON ledger (`state.json`) of what was filed where plus user-entered titles/names/skipped, keyed by meeting dir *name*. `awaiting_filing` drives the menubar's "Ready to file (n)" and the dashboard's auto-opened filing form. Atomic writes; a corrupt file degrades to "nothing filed".
- **`server.py`** — stdlib `ThreadingHTTPServer` on 127.0.0.1:8756, no auth, no framework. The `Api` object of callbacks is its only coupling to the app, so tests (and `scripts/dashboard_preview.py`) use a stub.
- **`static/`** — the dashboard, branded **FLY** — Faithful Logger of Yapping (name in `BRAND` in `app.js` and `<title>`; the logo (a microphone with fly wings) is drawn twice, in `Logo` in `app.js` and in `favicon.svg`, keep them in sync). Preact + htm, vendored as one ES module in `static/vendor/` — no build step, no npm, works offline; keep it that way. Hash routes: `#/` list, `#/r/<meeting-dir>` full view, `#/settings`. The app polls `/api/state` every second; form components keep their own local state so polling never clobbers typing (settings follow the server only until edited). Colour tokens at the top of `style.css` are two-tier: palette scales (baltic-blue, tropical-teal, emerald, plus amber/coral/slate) and semantic tokens (`--brand`, `--text-2`, `--ok-soft`, …). Components use only semantic tokens; dark mode overrides only the semantic tier. Keep text pairs at WCAG AA.
- **`app.py`** — thin UI layer wiring it together. The recorder and HTTP server run on background threads; **all rumps/AppKit UI mutation happens on the main thread** via the 1-second `rumps.Timer` `_tick`, driven by flags/events set from other threads.
- **`install.sh`** — the one-line installer: checks macOS 14.2+/arm64, installs uv, ffmpeg (brew), ownscribe and the app as `uv tool`s, and a LaunchAgent login item (`io.github.local-meeting-recorder`); `--uninstall` reverses it. Login-item launches get launchd's minimal PATH, so `app.main` appends `~/.local/bin` and Homebrew dirs.
- **`dialogs.py`** — text prompts go through `osascript`, out-of-process. rumps' `NSAlert` text field can't take focus in an accessory app and blocks the main thread; don't switch back to `rumps.Window`. Most text entry has moved to the web dashboard for the same reason.

Tested logic lives in the non-UI modules; `app.py` has no tests. Tests are all in `tests/test_core.py`.

## Constraints to keep in mind

- ownscribe's CLI args are fixed at launch, so anything that primes Whisper (vocabulary hints, names) must be set before recording; speaker names can only be mapped after diarization.
- `notes/` and `hf_token` are gitignored — they contain meeting content / secrets. Never commit transcripts.
- The repo is meant to be public: keep real client, project and colleague names out of code, tests, fixtures and docs (use Acme/Globex, Aino/Mikko/Sara, Alex/Sam).
