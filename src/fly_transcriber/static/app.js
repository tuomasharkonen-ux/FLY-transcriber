// Dashboard UI: Preact + htm, vendored so it runs offline with no build step.
import {
  html,
  render,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./vendor/htm-preact-3.1.1.js";

export const BRAND = {
  name: "FLY",
  // The acronym letters; "of" is filler and rendered as such.
  expansion: [["F", "aithful"], ["L", "ogger"], "of", ["Y", "apping"]],
};

const BRAND_TEXT = BRAND.expansion.map((w) => (Array.isArray(w) ? w.join("") : w)).join(" ");

const POLL_MS = 1000;
const SPEAKER_HUES = 6;

// -- helpers ----------------------------------------------------------------

function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${s} s`;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (data.error) throw new Error(data.error);
  return data;
}

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

/** Stable colour slot per diarization label, so a speaker keeps one colour everywhere. */
function speakerHue(label, speakers) {
  const index = speakers.indexOf(label);
  return index < 0 ? "none" : String(index % SPEAKER_HUES);
}

/** "SPEAKER_01" reads as "Speaker 2" until the user names them. */
function prettyLabel(label) {
  const match = label?.match(/^SPEAKER_(\d+)$/);
  return match ? `Speaker ${Number(match[1]) + 1}` : label || "Unattributed";
}

function speakerName(label, meeting) {
  return meeting.state.speaker_names?.[label] || prettyLabel(label);
}

function initials(name) {
  const match = name.match(/^(?:SPEAKER_|Speaker )(\d+)$/);
  if (match) return name.startsWith("SPEAKER_") ? String(Number(match[1]) + 1) : match[1];
  return name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "?";
}

function status(meeting) {
  if (meeting.processing) return { tone: "busy", label: "Processing" };
  if (!meeting.has_transcript) return { tone: "muted", label: "No transcript" };
  if (meeting.filed.length) return { tone: "ok", label: "Filed" };
  if (meeting.state.dismissed) return { tone: "muted", label: "Skipped" };
  return { tone: "warn", label: "Not filed" };
}

/** Matches the menubar's "Ready to file (n)" count. */
const awaitsFiling = (m) =>
  m.has_transcript && !m.processing && !m.filed.length && !m.state.dismissed;

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

// -- icons ------------------------------------------------------------------

const ICONS = {
  copy: "M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9A1.5 1.5 0 0 1 5.5 8Z",
  chevron: "m9 6 6 6-6 6",
  back: "M15 6l-6 6 6 6",
  folder: "M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5v-10Z",
  send: "M4 12h13M13 7l5 5-5 5",
  close: "M6 6l12 12M18 6 6 18",
  mic: "M12 4a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3Zm-6 8a6 6 0 0 0 12 0M12 18v2",
};

const Icon = ({ name, size = 16 }) => html`
  <svg class="icon" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d=${ICONS[name]} />
  </svg>`;

// -- primitives -------------------------------------------------------------

const Button = ({ variant = "secondary", size, icon, children, class: extra = "", ...props }) => html`
  <button type="button" class=${`btn btn-${variant} ${size ? `btn-${size}` : ""} ${extra}`} ...${props}>
    ${icon && html`<${Icon} name=${icon} />`}
    ${children && html`<span>${children}</span>`}
  </button>`;

const IconButton = ({ icon, label, ...props }) => html`
  <button type="button" class="btn btn-ghost btn-icon" title=${label} aria-label=${label} ...${props}>
    <${Icon} name=${icon} />
  </button>`;

const Pill = ({ tone, children }) => html`<span class=${`pill pill-${tone}`}>${children}</span>`;

const Field = ({ label, hint, children }) => html`
  <label class="field">
    <span class="field-label">${label}</span>
    ${children}
    ${hint && html`<span class="field-hint">${hint}</span>`}
  </label>`;

const Switch = ({ label, hint, checked, onChange }) => html`
  <label class="switch-row">
    <span class="switch-text">
      <span class="switch-label">${label}</span>
      ${hint && html`<span class="field-hint">${hint}</span>`}
    </span>
    <input type="checkbox" role="switch" class="switch" checked=${checked}
      onChange=${(e) => onChange(e.currentTarget.checked)} />
  </label>`;

const Avatar = ({ label, meeting }) => {
  const name = speakerName(label, meeting);
  return html`<span class="avatar" data-hue=${speakerHue(label, meeting.speakers)} title=${name}>
    ${initials(name)}
  </span>`;
};

const Empty = ({ title, children }) => html`
  <div class="empty">
    <div class="empty-mark"><${Icon} name="mic" size=${22} /></div>
    <div class="empty-title">${title}</div>
    <div class="empty-body">${children}</div>
  </div>`;

// -- toasts -----------------------------------------------------------------

function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((text, tone = "ok") => {
    const id = Math.random();
    setToasts((all) => [...all, { id, text, tone }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), 3200);
  }, []);
  return [toasts, push];
}

const Toasts = ({ toasts }) => html`
  <div class="toasts" role="status">
    ${toasts.map((t) => html`<div key=${t.id} class=${`toast toast-${t.tone}`}>${t.text}</div>`)}
  </div>`;

// -- chrome -----------------------------------------------------------------

/** A microphone with fly wings. Mirrors favicon.svg. */
const Logo = ({ size = 28 }) => html`
  <svg class="logo" width=${size} height=${size} viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="9" fill="var(--brand)" />
    <ellipse cx="9" cy="10" rx="3.4" ry="7" transform="rotate(-56 9 10)" fill="var(--logo-wing)" fill-opacity=".85" />
    <ellipse cx="23" cy="10" rx="3.4" ry="7" transform="rotate(56 23 10)" fill="var(--logo-wing)" fill-opacity=".85" />
    <rect x="12.6" y="5.5" width="6.8" height="12" rx="3.4" fill="var(--brand-ink)" />
    <path d="M14.4 9.6h3.2M14.4 12.2h3.2" stroke="var(--brand)" stroke-width="1.1" stroke-linecap="round" />
    <path d="M9.6 15a6.4 6.4 0 0 0 12.8 0M16 21.4v3.4M12.8 24.8h6.4" stroke="var(--brand-ink)" stroke-width="1.7" stroke-linecap="round" fill="none" />
  </svg>`;

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

const RecordingRow = ({ meeting, onFile, toast }) => {
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
          <${Button} size="sm" variant=${meeting.filed.length ? "secondary" : "primary"} icon="send"
            onClick=${stop(() => onFile(meeting))}>File<//>
          <${IconButton} icon="copy" label="Copy transcript"
            onClick=${stop(() => copyTranscript(meeting, toast))} />`}
        <${IconButton} icon="chevron" label="Open" onClick=${stop(open)} />
      </div>
    </li>`;
};

const RecordingsView = ({ meetings, onFile, toast }) => {
  const unfiled = meetings.filter(awaitsFiling).length;
  return html`
    <section class="page">
      <div class="page-head">
        <div>
          <h1>Recordings</h1>
          <p class="subtle">
            ${meetings.length
              ? unfiled ? `${unfiled} waiting to be filed` : "Everything is filed"
              : "Start a recording from the menubar"}
          </p>
        </div>
      </div>
      ${meetings.length
        ? html`<ul class="list">
            ${meetings.map((m) => html`<${RecordingRow} key=${m.name} meeting=${m} onFile=${onFile} toast=${toast} />`)}
          </ul>`
        : html`<${Empty} title="No recordings yet">
            Click <strong>○</strong> in the menubar to start one. Finished transcripts show up here.
          <//>`}
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
            <${Button} variant="primary" icon="send" onClick=${() => onFile(meeting)}>
              ${meeting.filed.length ? "File again" : "File"}
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
            <h3>Filed to</h3>
            ${meeting.filed.length
              ? html`<ul class="filed-list">
                  ${meeting.filed.map((f) => html`
                    <li key=${f.path}>
                      <span class="filed-project">${f.project}</span>
                      <code title=${f.path}>${f.path.split("/").pop()}</code>
                    </li>`)}
                </ul>`
              : html`<p class="subtle">Not filed yet.</p>`}
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
  const [title, setTitle] = useState(meeting.state.title || meeting.title || "");
  const [participants, setParticipants] = useState((meeting.state.participants || []).join(", "));
  const [names, setNames] = useState({ ...(meeting.state.speaker_names || {}) });
  const [project, setProject] = useState(
    meeting.state.pending_project || meeting.filed[0]?.project || projects[0]?.name || "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    ref.current.showModal();
    ref.current.querySelector("input")?.focus();
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const speaker_names = Object.fromEntries(
      Object.entries(names).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v),
    );
    try {
      await api("/api/file", {
        meeting: meeting.name,
        project,
        title: title.trim(),
        participants: participants.split(",").map((s) => s.trim()).filter(Boolean),
        speaker_names,
      });
      toast(`Filed to ${project}`);
      onClose();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const skip = async () => {
    try {
      await api("/api/dismiss", { meeting: meeting.name });
      toast("Skipped — it won't count as waiting");
      ref.current.close();
    } catch (err) {
      setError(err.message);
    }
  };

  return html`
    <dialog class="dialog" ref=${ref} onClose=${onClose}
      onClick=${(e) => e.target === ref.current && ref.current.close()}>
      <form method="dialog" class="dialog-body" onSubmit=${submit}>
        <div class="dialog-head">
          <div>
            <h2>File recording</h2>
            <p class="subtle">${meeting.when}${meeting.duration ? ` · ${fmtDuration(meeting.duration)}` : ""}</p>
          </div>
          <${IconButton} icon="close" label="Close" onClick=${() => ref.current.close()} />
        </div>

        <div class="stack">
          <${Field} label="Title" hint="Names the filed note.">
            <input class="input" value=${title} placeholder="Weekly sync"
              onInput=${(e) => setTitle(e.currentTarget.value)} />
          <//>
          <${Field} label="Participants" hint="Comma-separated.">
            <input class="input" value=${participants} placeholder="Aino, Mikko, Sara"
              onInput=${(e) => setParticipants(e.currentTarget.value)} />
          <//>

          ${meeting.speakers.length > 0 && html`
            <div class="field">
              <span class="field-label">Who is who</span>
              <div class="speaker-map">
                ${meeting.speakers.map((label) => html`
                  <div class="speaker-map-row" key=${label}>
                    <span class="avatar" data-hue=${speakerHue(label, meeting.speakers)}>${initials(label)}</span>
                    <div class="speaker-map-fields">
                      <input class="input" value=${names[label] || ""} placeholder=${prettyLabel(label)}
                        onInput=${(e) => setNames({ ...names, [label]: e.currentTarget.value })} />
                      ${meeting.samples?.[label] && html`<span class="quote">“${meeting.samples[label]}”</span>`}
                    </div>
                  </div>`)}
              </div>
              <span class="field-hint">Names apply to the filed copy only.</span>
            </div>`}

          <${Field} label="Destination">
            <select class="input" value=${project} onChange=${(e) => setProject(e.currentTarget.value)}>
              ${projects.map((p) => html`<option key=${p.name} value=${p.name}>${p.name}</option>`)}
            </select>
          <//>
        </div>

        ${!projects.length && html`<div class="error-text">No projects configured — add one in settings.toml.</div>`}
        ${error && html`<div class="error-text">${error}</div>`}

        <div class="dialog-foot">
          ${awaitsFiling(meeting) && html`
            <${Button} variant="ghost" class="foot-start" onClick=${skip}
              title="Stop counting this one as waiting to be filed">Don't file<//>`}
          <${Button} onClick=${() => ref.current.close()}>Cancel<//>
          <button type="submit" class="btn btn-primary" disabled=${busy || !projects.length}>
            ${busy ? "Filing…" : `File to ${project || "…"}`}
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
        <p class="subtle">Destinations offered when filing. Edit them in${" "}
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
