"""Estimate how far along processing is.

ownscribe reports phases but no percentage, so progress is a prediction: the
time spent processing past runs, per second of audio, scaled to this recording.
Each successful run adds a sample, so the estimate adapts to the machine. The
engines differ about fourfold, so each keeps its own samples.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

#: Samples for faster-whisper; other engines get a file of their own beside it.
TIMINGS_PATH = Path("~/.config/fly-transcriber/timings.json").expanduser()

#: Processing seconds per audio second before any run has been timed. Measured
#: on an M4 Pro with diarization: 0.38 (faster-whisper on CPU), 0.15 (MLX).
DEFAULT_RATES = {"faster-whisper": 0.4, "mlx": 0.16}
#: Recordings shorter than this are dominated by model loading; don't learn from them.
MIN_AUDIO = 30
MAX_SAMPLES = 10
#: Never claim completion before ownscribe says so.
CAP = 0.95


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
        and all(isinstance(x, (int, float)) for x in s) and s[0] > 0
    ]


def predict(audio_seconds: float, engine: str = "faster-whisper") -> float:
    """Expected processing seconds for a recording of this length."""
    rates = [proc / audio for audio, proc in _load(engine)]
    if rates:
        rate = statistics.median(rates)
    else:
        rate = DEFAULT_RATES.get(engine, DEFAULT_RATES["faster-whisper"])
    return max(rate * audio_seconds, 1.0)


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
    """Progress in [0, CAP] after ``elapsed`` seconds of a predicted run."""
    if predicted <= 0:
        return 0.0
    return max(0.0, min(elapsed / predicted, 1.0)) * CAP
