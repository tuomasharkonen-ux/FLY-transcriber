// Dashboard UI: Preact + htm, vendored so it runs offline with no build step.
import { html, render, useEffect, useMemo, useRef, useState } from "./vendor/htm-preact-3.1.1.js";
import {
  BRAND, BRAND_TEXT, Avatar, Button, Empty, Field, FilingFields, Icon, IconButton, Logo, Pill,
  RunBar, Switch, Toasts, api, awaitsFiling, fmtDuration, speakerHue, speakerName, status,
  useFilingForm, useToasts,
} from "./common.js";

const POLL_MS = 1000;

function useHashRoute() {
  const read = () => {
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "settings") return { view: "settings" };
    if (parts[0] === "r" && parts[1]) return { view: "recording", name: decodeURIComponent(parts[1]) };
    return { view: "recordings" };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => { setRoute(read()); window.scrollTo(0, 0); };
    addEventListener("hashchange", onChange);
    return () => removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

const go = (hash) => { location.hash = hash; };

async function copyTranscript(meeting, toast) {
  try {
    const detail = await api(`/api/meeting?name=${encodeURIComponent(meeting.name)}`);
    const title = meeting.title ? `# ${meeting.title}\n\n` : "";
    await navigator.clipboard.writeText(title + detail.markdown + "\n");
    toast("Transcript copied");
  } catch (err) {
    toast(err.message, "error");
  }
}

// -- chrome -----------------------------------------------------------------

const Footer = () => html`
  <footer class="footer">
    <${Logo} size=${40} />
    <div class="footer-name">${BRAND.name}</div>
    <div class="footer-expansion">
      ${BRAND.expansion.map((w) => Array.isArray(w)
        ? html`<span><strong>${w[0]}</strong>${w[1]}</span>`
        : html`<span class="filler">${w}</span>`)}
    </div>
  </footer>`;

const RunStatus = ({ run }) => html`
  <div class=${`run run-${run.css || "idle"}`} title=${run.detail || ""}>
    <span class="run-dot"></span>
    <span class="run-label">${run.css === "idle" ? "Ready" : run.label}</span>
    ${run.detail && html`<span class="run-detail">${run.detail}</span>`}
    <${RunBar} progress=${run.progress} />
  </div>`;

const TopBar = ({ route, run }) => {
  const tab = route.view === "settings" ? "settings" : "recordings";
  return html`
    <header class="topbar">
      <div class="topbar-inner">
        <a class="brand" href="#/" title=${BRAND_TEXT}>
          <${Logo} />
          <span class="brand-name">${BRAND.name}</span>
        </a>
        <nav class="nav">
          <a class=${`nav-item ${tab === "recordings" ? "active" : ""}`} href="#/">Recordings</a>
          <a class=${`nav-item ${tab === "settings" ? "active" : ""}`} href="#/settings">Settings</a>
        </nav>
        <${RunStatus} run=${run} />
      </div>
    </header>`;
};

// -- recordings list --------------------------------------------------------

const RecordingRow = ({ meeting, onFile, onDelete }) => {
  const st = status(meeting);
  const canAct = meeting.has_transcript && !meeting.processing;
  const meta = [meeting.when, meeting.duration ? fmtDuration(meeting.duration) : null];
  const n = meeting.speakers.length;
  if (n) meta.push(`${n} speaker${n === 1 ? "" : "s"}`);
  const open = () => go(`#/r/${encodeURIComponent(meeting.name)}`);
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };

  return html`
    <li class="row" onClick=${open}>
      <div class="row-main">
        <div class="row-title">${meeting.title || "Untitled recording"}</div>
        <div class="row-meta">
          ${meta.filter(Boolean).join(" · ")}
          ${meeting.filed.length > 0 && html`
            <span class="row-filed">→ ${[...new Set(meeting.filed.map((f) => f.project))].join(", ")}</span>`}
        </div>
      </div>
      <${Pill} tone=${st.tone}>${st.label}<//>
      <div class="row-actions">
        ${canAct && html`
          <${Button} size="sm" variant=${meeting.filed.length ? "secondary" : "primary"}
            onClick=${stop(() => onFile(meeting))}>${meeting.filed.length ? "Edit" : "Save"}<//>`}
        ${!meeting.processing && html`
          <${IconButton} icon="trash" label="Delete recording"
            onClick=${stop(() => onDelete(meeting))} />`}
        <${IconButton} icon="chevron" label="Open" onClick=${stop(open)} />
      </div>
    </li>`;
};

const DeleteDialog = ({ meeting, onClose, toast }) => {
  const ref = useRef();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { ref.current.showModal(); }, []);

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/api/delete", { meeting: meeting.name });
      toast("Moved to the Trash");
      ref.current.close();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return html`
    <dialog class="dialog dialog-narrow" ref=${ref} onClose=${onClose}
      onClick=${(e) => e.target === ref.current && ref.current.close()}>
      <div class="dialog-body">
        <div class="dialog-head">
          <h2>Delete this recording?</h2>
        </div>
        <p>
          <strong>${meeting.title || "Untitled recording"}</strong>${meeting.when ? ` (${meeting.when})` : ""}${" "}
          and its transcript${meeting.has_audio ? " and audio" : ""} move to the Trash.
          ${meeting.filed.length > 0 ? " Notes already saved to your projects are kept." : ""}
        </p>
        ${error && html`<div class="error-text">${error}</div>`}
        <div class="dialog-foot">
          <${Button} onClick=${() => ref.current.close()}>Cancel<//>
          <${Button} variant="danger" disabled=${busy} onClick=${remove}>${busy ? "Deleting…" : "Delete"}<//>
        </div>
      </div>
    </dialog>`;
};

const RecordingsView = ({ meetings, onFile, toast }) => {
  const unfiled = meetings.filter(awaitsFiling).length;
  const [deleting, setDeleting] = useState(null);
  return html`
    <section class="page">
      <div class="page-head">
        <div>
          <h1>Recordings</h1>
          <p class="subtle">
            ${meetings.length
              ? unfiled ? `${unfiled} waiting to be saved` : "Everything is saved"
              : "Start a recording from the FLY icon in the menubar"}
          </p>
        </div>
      </div>
      ${meetings.length
        ? html`<ul class="list">
            ${meetings.map((m) => html`<${RecordingRow} key=${m.name} meeting=${m} onFile=${onFile} onDelete=${setDeleting} />`)}
          </ul>`
        : html`<${Empty} title="No recordings yet">
            Click the FLY icon in the menubar to start one. Finished transcripts show up here.
          <//>`}
      ${deleting && html`<${DeleteDialog} meeting=${deleting} toast=${toast} onClose=${() => setDeleting(null)} />`}
    </section>`;
};

// -- single recording -------------------------------------------------------

function useDetail(meeting) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  // Refetch when anything that changes the rendered transcript changes.
  const key = meeting && JSON.stringify([meeting.name, meeting.has_transcript, meeting.state.speaker_names]);
  useEffect(() => {
    if (!meeting || !meeting.has_transcript) return;
    let live = true;
    api(`/api/meeting?name=${encodeURIComponent(meeting.name)}`)
      .then((d) => live && (setDetail(d), setError("")))
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [key]);
  return [detail, error];
}

/** Consecutive turns by one speaker collapse into a single block. */
function groupTurns(turns) {
  const blocks = [];
  for (const turn of turns) {
    const last = blocks[blocks.length - 1];
    if (last && last.speaker === turn.speaker) last.lines.push(turn);
    else blocks.push({ speaker: turn.speaker, lines: [turn] });
  }
  return blocks;
}

const Transcript = ({ detail, meeting }) => html`
  <div class="transcript">
    ${groupTurns(detail.turns).map((block, i) => html`
      <div class="turn" key=${i}>
        <${Avatar} label=${block.speaker} meeting=${meeting} />
        <div class="turn-body">
          <div class="turn-head">
            <span class="turn-speaker" data-hue=${speakerHue(block.speaker, meeting.speakers)}>
              ${speakerName(block.speaker, meeting)}
            </span>
            <span class="turn-time">${block.lines[0].timestamp}</span>
          </div>
          ${block.lines.map((line) => html`<p key=${line.start} title=${line.timestamp}>${line.text}</p>`)}
        </div>
      </div>`)}
  </div>`;

const RecordingView = ({ meeting, onFile, toast }) => {
  const [detail, error] = useDetail(meeting);
  if (!meeting) {
    return html`<section class="page">
      <a class="back" href="#/"><${Icon} name="back" /> Recordings</a>
      <${Empty} title="Recording not found">It may have been moved or deleted.<//>
    </section>`;
  }
  const st = status(meeting);
  const canAct = meeting.has_transcript && !meeting.processing;
  const reveal = () => api("/api/reveal", { meeting: meeting.name }).catch((e) => toast(e.message, "error"));

  return html`
    <section class="page">
      <a class="back" href="#/"><${Icon} name="back" /> Recordings</a>
      <div class="page-head">
        <div>
          <div class="title-line">
            <h1>${meeting.title || "Untitled recording"}</h1>
            <${Pill} tone=${st.tone}>${st.label}<//>
          </div>
          <p class="subtle">${[meeting.when, meeting.duration && fmtDuration(meeting.duration)].filter(Boolean).join(" · ")}</p>
        </div>
        <div class="head-actions">
          <${IconButton} icon="folder" label="Show in Finder" onClick=${reveal} />
          ${canAct && html`
            <${Button} icon="copy" onClick=${() => copyTranscript(meeting, toast)}>Copy<//>
            <${Button} variant="primary" onClick=${() => onFile(meeting)}>
              ${meeting.filed.length ? "Edit" : "Save"}
            <//>`}
        </div>
      </div>

      <div class="detail">
        <div class="card detail-main">
          ${meeting.processing
            ? html`<${Empty} title="Still processing">The transcript appears here when it is ready.<//>`
            : !meeting.has_transcript
              ? html`<${Empty} title="No transcript">This recording produced no transcript.<//>`
              : error
                ? html`<div class="error-text">${error}</div>`
                : detail
                  ? html`<${Transcript} detail=${detail} meeting=${meeting} />`
                  : html`<div class="loading">Loading transcript…</div>`}
        </div>

        <aside class="detail-side">
          <div class="card">
            <h3>Speakers</h3>
            ${meeting.speakers.length
              ? html`<ul class="speaker-list">
                  ${meeting.speakers.map((label) => html`
                    <li key=${label}>
                      <${Avatar} label=${label} meeting=${meeting} />
                      <span>${speakerName(label, meeting)}</span>
                    </li>`)}
                </ul>`
              : html`<p class="subtle">No speaker labels.</p>`}
            ${meeting.state.participants?.length > 0 && html`
              <h3>Participants</h3>
              <p>${meeting.state.participants.join(", ")}</p>`}
          </div>
          <div class="card">
            <h3>Saved to</h3>
            ${meeting.filed.length
              ? html`<ul class="filed-list">
                  ${meeting.filed.map((f) => html`
                    <li key=${f.path}>
                      <span class="filed-project">${f.project}</span>
                      <code title=${f.path}>${f.path.split("/").pop()}</code>
                    </li>`)}
                </ul>`
              : html`<p class="subtle">Not saved yet.</p>`}
            <h3>Files</h3>
            <p class="subtle">${meeting.has_audio ? "Audio kept" : "Audio discarded"} · <code>${meeting.name}</code></p>
          </div>
        </aside>
      </div>
    </section>`;
};

// -- filing dialog ----------------------------------------------------------

const FileDialog = ({ meeting, projects, onClose, toast }) => {
  const ref = useRef();
  const form = useFilingForm(meeting, projects, {
    onFiled: (project) => { toast(`Saved to ${project}`); onClose(); },
    onSkipped: () => { toast("Skipped — it won't count as waiting"); ref.current.close(); },
  });

  useEffect(() => {
    ref.current.showModal();
    ref.current.querySelector("input")?.focus();
  }, []);

  return html`
    <dialog class="dialog" ref=${ref} onClose=${onClose}
      onClick=${(e) => e.target === ref.current && ref.current.close()}>
      <form method="dialog" class="dialog-body" onSubmit=${form.submit}>
        <div class="dialog-head">
          <div>
            <h2>${meeting.filed.length ? "Edit saved note" : "Save recording"}</h2>
            <p class="subtle">${meeting.when}${meeting.duration ? ` · ${fmtDuration(meeting.duration)}` : ""}</p>
          </div>
          <${IconButton} icon="close" label="Close" onClick=${() => ref.current.close()} />
        </div>

        <${FilingFields} form=${form} meeting=${meeting} projects=${projects} />

        <div class="dialog-foot">
          ${awaitsFiling(meeting) && html`
            <${Button} variant="ghost" class="foot-start" onClick=${form.skip}
              title="Stop counting this one as waiting to be saved">Don't save<//>`}
          <${Button} onClick=${() => ref.current.close()}>Cancel<//>
          <button type="submit" class="btn btn-primary" disabled=${form.busy || !projects.length}>
            ${form.busy ? "Saving…" : `${meeting.filed.length ? "Save changes to" : "Save to"} ${form.project || "…"}`}
          </button>
        </div>
      </form>
    </dialog>`;
};

// -- settings ---------------------------------------------------------------

const MODELS = [
  ["large-v3", "large-v3 — recommended"],
  ["medium", "medium"],
  ["small", "small"],
  ["base", "base"],
  ["tiny", "tiny — fastest, unusable for Finnish"],
];

const SettingsView = ({ settings, projects, toast }) => {
  const [draft, setDraft] = useState(settings);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const serverKey = JSON.stringify(settings);

  // Follow the server until the user starts editing.
  useEffect(() => { if (!dirty) setDraft(settings); }, [serverKey]);

  const set = (key) => (value) => { setDraft((d) => ({ ...d, [key]: value })); setDirty(true); };
  const text = (key) => (e) => set(key)(e.currentTarget.value);
  const num = (key) => (e) => set(key)(Number(e.currentTarget.value));

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api("/api/settings", draft);
      setDirty(false);
      toast("Saved — applies to the next recording");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setSaving(false);
    }
  };

  if (!draft) return null;
  return html`
    <form class="page" onSubmit=${save}>
      <div class="page-head">
        <div>
          <h1>Settings</h1>
          <p class="subtle">Changes apply from the next recording.</p>
        </div>
      </div>

      <div class="card section">
        <h2>Transcription</h2>
        <div class="grid-2">
          <${Field} label="Whisper model">
            <select class="input" value=${draft.model} onChange=${text("model")}>
              ${MODELS.map(([v, l]) => html`<option key=${v} value=${v}>${l}</option>`)}
            </select>
          <//>
          <${Field} label="Language" hint="Leave empty to auto-detect.">
            <input class="input" value=${draft.language} placeholder="fi" onInput=${text("language")} />
          <//>
        </div>
        <${Field} label="Vocabulary hints"
          hint="Names, products and jargon the transcriber gets wrong. Applied at recording start — the only point where it helps.">
          <input class="input" value=${draft.hotwords} placeholder="Acme, Kubernetes, Aino Virtanen" onInput=${text("hotwords")} />
        <//>
      </div>

      <div class="card section">
        <h2>Speakers</h2>
        <${Switch} label="Identify speakers" hint="Diarization with pyannote, using the speaker model the installer set up."
          checked=${draft.diarize} onChange=${set("diarize")} />
        <div class="grid-2">
          <${Field} label="Speaker count"
            hint="0 = auto. Over-stating it makes diarization split one person in two, convincingly.">
            <input class="input" type="number" min="0" max="20" value=${draft.speaker_count} onInput=${num("speaker_count")} />
          <//>
        </div>
      </div>

      <div class="card section">
        <h2>Recording</h2>
        <${Switch} label="Capture microphone" hint="In addition to system audio."
          checked=${draft.mic} onChange=${set("mic")} />
        <${Switch} label="Keep audio files" hint="About 300 MB per hour. Needed to re-run diarization later."
          checked=${draft.keep_recording} onChange=${set("keep_recording")} />
        <div class="grid-2">
          <${Field} label="Silence auto-stop" hint="Seconds of silence before stopping. 0 disables it.">
            <input class="input" type="number" min="0" step="30" value=${draft.silence_timeout} onInput=${num("silence_timeout")} />
          <//>
        </div>
      </div>

      <div class="card section">
        <h2>Projects</h2>
        <p class="subtle">Destinations offered when saving. Edit them in${" "}
          <code>~/.config/fly-transcriber/settings.toml</code>.</p>
        ${projects.length
          ? html`<ul class="project-list">
              ${projects.map((p) => html`
                <li key=${p.name}>
                  <div class="row-title">${p.name}</div>
                  <div class="row-meta"><code>${p.path}</code></div>
                  <div class="row-meta">${p.naming} filenames · ${p.frontmatter} frontmatter</div>
                </li>`)}
            </ul>`
          : html`<p class="subtle">No projects configured.</p>`}
      </div>

      <div class=${`savebar ${dirty ? "visible" : ""}`}>
        <span>Unsaved changes</span>
        <${Button} onClick=${() => { setDraft(settings); setDirty(false); }}>Discard<//>
        <button type="submit" class="btn btn-primary" disabled=${saving}>${saving ? "Saving…" : "Save"}</button>
      </div>
    </form>`;
};

// -- app --------------------------------------------------------------------

const DISCONNECTED = { css: "failed", label: "Disconnected", detail: "Is the app running?" };

function App() {
  const route = useHashRoute();
  const [state, setState] = useState(null);
  const [run, setRun] = useState({ css: "idle", label: "Connecting…" });
  const [filing, setFiling] = useState(null);
  const [toasts, toast] = useToasts();

  useEffect(() => {
    let timer;
    const poll = async () => {
      try {
        const next = await api("/api/state");
        setState(next);
        setRun(next.run);
      } catch {
        setRun(DISCONNECTED);
      }
      timer = setTimeout(poll, POLL_MS);
    };
    poll();
    return () => clearTimeout(timer);
  }, []);

  const meetings = state?.meetings || [];
  const projects = state?.projects || [];
  const byName = useMemo(() => Object.fromEntries(meetings.map((m) => [m.name, m])), [meetings]);
  // Look the meeting up fresh on each poll, so the dialog never files stale data.
  const filingMeeting = filing && byName[filing];

  // Opening the dashboard with a recording waiting goes straight to filing it.
  // Only on the first load: later polls must not reopen a dialog just closed.
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!state || autoOpened.current) return;
    autoOpened.current = true;
    const waiting = meetings.find(awaitsFiling); // newest first
    if (waiting && route.view === "recordings") setFiling(waiting.name);
  }, [state]);

  let page;
  if (!state) page = html`<div class="loading page">Loading…</div>`;
  else if (route.view === "settings") page = html`<${SettingsView} settings=${state.settings} projects=${projects} toast=${toast} />`;
  else if (route.view === "recording") page = html`<${RecordingView} meeting=${byName[route.name]} onFile=${(m) => setFiling(m.name)} toast=${toast} />`;
  else page = html`<${RecordingsView} meetings=${meetings} onFile=${(m) => setFiling(m.name)} toast=${toast} />`;

  return html`
    <${TopBar} route=${route} run=${run} />
    <main>${page}</main>
    <${Footer} />
    ${filingMeeting && html`<${FileDialog} key=${filing} meeting=${filingMeeting} projects=${projects}
      toast=${toast} onClose=${() => setFiling(null)} />`}
    <${Toasts} toasts=${toasts} />`;
}

document.title = `${BRAND.name} — ${BRAND_TEXT}`;
render(html`<${App} />`, document.getElementById("root"));
