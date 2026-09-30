"""Estimate how far along processing is.

ownscribe reports phases but no percentage, so progress is a prediction: the
time spent processing past runs, per second of audio, scaled to this recording.
Each successful run adds a sample, so the estimate adapts to the machine.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

TIMINGS_PATH = Path("~/.config/fly-transcriber/timings.json").expanduser()

#: Processing seconds per audio second before any run has been timed.
DEFAULT_RATE = 0.35
#: Recordings shorter than this are dominated by model loading; don't learn from them.
MIN_AUDIO = 30
MAX_SAMPLES = 10
#: Never claim completion before ownscribe says so.
CAP = 0.95


def _load() -> list[list[float]]:
    try:
        data = json.loads(TIMINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        s for s in data
        if isinstance(s, list) and len(s) == 2
        and all(isinstance(x, (int, float)) for x in s) and s[0] > 0
    ]


def predict(audio_seconds: float) -> float:
    """Expected processing seconds for a recording of this length."""
    rates = [proc / audio for audio, proc in _load()]
    rate = statistics.median(rates) if rates else DEFAULT_RATE
    return max(rate * audio_seconds, 1.0)


def record(audio_seconds: float, processing_seconds: float) -> None:
    """Remember how long a finished run took."""
    if audio_seconds < MIN_AUDIO or processing_seconds <= 0:
        return
    samples = (_load() + [[audio_seconds, processing_seconds]])[-MAX_SAMPLES:]
    try:
        TIMINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = TIMINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(samples), encoding="utf-8")
        tmp.replace(TIMINGS_PATH)
    except OSError:
        pass  # timings are a nicety; never fail a run over them


def fraction(elapsed: float, predicted: float) -> float:
    """Progress in [0, CAP] after ``elapsed`` seconds of a predicted run."""
    if predicted <= 0:
        return 0.0
    return max(0.0, min(elapsed / predicted, 1.0)) * CAP
