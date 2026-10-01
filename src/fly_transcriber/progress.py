"""Estimate how far along processing is.

ownscribe reports phases but no percentage, so progress is a prediction: a fixed
start-up time (the stop handoff, loading three models) plus a rate per second of
audio, learnt from past runs on this machine. The fixed part matters: it was half
of a 5-minute recording's processing, and without it short recordings sat at the
end of the bar. The engines differ about fourfold, so each keeps its own samples.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

#: Samples for faster-whisper; other engines get a file of their own beside it.
TIMINGS_PATH = Path("~/.config/fly-transcriber/timings.json").expanduser()

#: Seconds of processing that don't depend on the recording's length, and
#: processing seconds per audio second before any run has been timed. Measured on
#: an M4 Pro with diarization: a 25-minute and a 5-minute recording.
OVERHEAD = {"faster-whisper": 30.0, "mlx": 60.0}
DEFAULT_RATES = {"faster-whisper": 0.37, "mlx": 0.14}
MIN_RATE = 0.02
#: Recordings shorter than this are nearly all start-up; don't learn a rate from them.
MIN_AUDIO = 60
MAX_SAMPLES = 10
#: Where the bar is when the predicted time is up. Past it, the bar keeps creeping
#: towards CAP rather than stopping, and never claims completion before ownscribe.
DUE = 0.9
CAP = 0.99


def _path(engine: str) -> Path:
    if engine == "faster-whisper":
        return TIMINGS_PATH
    return TIMINGS_PATH.with_name(f"timings-{engine}.json")


def _load(engine: str) -> list[list[float]]:
    try:
        data = json.loads(_path(engine).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        s for s in data
        if isinstance(s, list) and len(s) == 2
        and all(isinstance(x, (int, float)) for x in s) and s[0] >= MIN_AUDIO
    ]


def predict(audio_seconds: float, engine: str = "faster-whisper") -> float:
    """Expected processing seconds for a recording of this length."""
    known = engine if engine in OVERHEAD else "faster-whisper"
    overhead = OVERHEAD[known]
    rates = [max((proc - overhead) / audio, MIN_RATE) for audio, proc in _load(engine)]
    rate = statistics.median(rates) if rates else DEFAULT_RATES[known]
    return overhead + rate * audio_seconds


def record(audio_seconds: float, processing_seconds: float, engine: str = "faster-whisper") -> None:
    """Remember how long a finished run took."""
    if audio_seconds < MIN_AUDIO or processing_seconds <= 0:
        return
    samples = (_load(engine) + [[audio_seconds, processing_seconds]])[-MAX_SAMPLES:]
    path = _path(engine)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(samples), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # timings are a nicety; never fail a run over them


def fraction(elapsed: float, predicted: float) -> float:
    """Progress in [0, CAP) after ``elapsed`` seconds of a predicted run.

    Linear up to DUE at the predicted time, then slower and slower: halfway from
    DUE to CAP at twice the predicted time.
    """
    if predicted <= 0 or elapsed <= 0:
        return 0.0
    if elapsed <= predicted:
        return DUE * elapsed / predicted
    return DUE + (CAP - DUE) * (1 - predicted / elapsed)
