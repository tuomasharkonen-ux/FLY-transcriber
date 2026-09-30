"""Serve the dashboard with fake data, for UI work without the menubar app.

    uv run python scripts/dashboard_preview.py   # http://127.0.0.1:8757/
                                                 # popover: /popover.html
"""

from __future__ import annotations

import os
import time

from fly_transcriber.server import Api, make_server

PORT = int(os.environ.get("PORT", 8757))

#: The fake run: processing by default; the record button toggles a recording.
RUN = {"started": None}

TURNS = [
    ("SPEAKER_00", 3, "Onko teillä jo ne testitunnukset?"),
    ("SPEAKER_01", 6, "Ei vielä, odotetaan että API-avain tulee."),
    ("SPEAKER_01", 9, "Mä voin pingata sitä huomenna."),
    ("SPEAKER_00", 14, "Hyvä. Testitunnukset on sitten kunnossa, eikö?"),
    ("SPEAKER_02", 21, "Joo, ja ne pitää mapata ennen perjantaita."),
]

STATE = {
    "acme-sync": {"title": "Acme weekly sync", "participants": ["Aino", "Mikko"],
                  "speaker_names": {"SPEAKER_00": "Aino"}, "pending_project": "Acme"},
}


def meeting(name, title, when, duration, speakers, filed=(), processing=False, transcript=True):
    entry = STATE.get(name, {})
    return {
        "name": name, "title": entry.get("title", title), "when": when, "duration": duration,
        "has_transcript": transcript, "has_audio": True, "processing": processing,
        "speakers": speakers,
        "samples": {s: next(t for sp, _, t in TURNS if sp == s) for s in speakers},
        "filed": [{"project": p, "path": f"/Users/me/{p}/meetings/_inbox/28-09-26-{name}.md", "at": ""} for p in filed],
        "state": {"title": entry.get("title", ""), "participants": entry.get("participants", []),
                  "speaker_names": entry.get("speaker_names", {}),
                  "pending_project": entry.get("pending_project", ""),
                  "dismissed": entry.get("dismissed", False)},
    }


def run_summary():
    if RUN["started"] is None:
        return {"css": "busy", "label": "Diarizing", "detail": "42:10 captured", "elapsed": "42:10", "progress": 0.6}
    s = int(time.time() - RUN["started"])
    return {"css": "recording", "label": f"Recording — {s // 60}:{s % 60:02d}", "detail": "",
            "elapsed": f"{s // 60}:{s % 60:02d}"}


def record():
    RUN["started"] = None if RUN["started"] else time.time()
    return {"ok": True}


def snapshot():
    three = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    return {
        "run": run_summary(),
        "meetings": [
            meeting("in-progress", "", "29.09. 10:00", 0, [], processing=True, transcript=False),
            meeting("acme-sync", "", "28.09. 14:20", 3420, three),
            meeting("design-review", "Design review", "27.09. 09:30", 1800, three[:2], filed=["Globex"]),
            meeting("one-on-one", "", "26.09. 13:00", 1500, three[:2], filed=["Acme", "Globex"]),
        ],
        "projects": [
            {"name": "Acme", "path": "/Users/me/acme/notes/meetings/_inbox", "naming": "vault", "frontmatter": "obsidian"},
            {"name": "Globex", "path": "/Users/me/globex/meetings/_inbox", "naming": "timestamp", "frontmatter": "generic"},
        ],
        "settings": {"model": "large-v3", "language": "fi", "silence_timeout": 300, "speaker_count": 0,
                     "hotwords": "Acme, Globex, Kubernetes", "mic": True, "diarize": True, "keep_recording": True},
    }


def detail(name):
    return {
        "name": name,
        "turns": [{"speaker": s, "start": t, "timestamp": f"00:{t:02d}", "text": x} for s, t, x in TURNS],
        "markdown": "\n".join(f"**{s}** {x}" for s, _, x in TURNS),
    }


def file_meeting(name, project, payload):
    STATE[name] = {**payload, "pending_project": project}
    return {"path": f"/Users/me/{project}/x.md", "project": project}


def dismiss(name):
    STATE[name] = {**STATE.get(name, {}), "dismissed": True}
    return {"ok": True}


if __name__ == "__main__":
    api = Api(snapshot=snapshot, file_meeting=file_meeting, save_settings=lambda p: {"ok": True},
              forget=lambda n: {"ok": True}, meeting_detail=detail, reveal=lambda n: {"ok": True},
              dismiss=dismiss, record=record)
    print(f"http://127.0.0.1:{PORT}/")
    make_server(api, PORT).serve_forever()
