"""Preflight checks for speaker diarization access.

Diarization fails *quietly*: when the HuggingFace token cannot reach the gated
pyannote model, ownscribe logs the error, carries on, and exits 0 with an
unlabelled transcript. Since a diarized transcript is the point, this module
checks access up front so the problem surfaces before a meeting is recorded
rather than after.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

#: The model whisperx loads by default. ownscribe constructs
#: ``DiarizationPipeline(token=..., device=...)`` without ``model_name``, so this
#: is what gets fetched -- note it is NOT the older ``speaker-diarization-3.1``;
#: whisperx 3.8 switched defaults, and accepting terms for 3.1 alone is not enough.
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"

#: Probed because gating is enforced on file resolution. The metadata API returns
#: 200 for gated repos you have *not* been granted, so it cannot be used here.
PROBE_FILE = "config.yaml"


@dataclass(frozen=True)
class AccessResult:
    ok: bool
    reason: str = ""
    repo: str = DIARIZATION_REPO

    @property
    def gate_url(self) -> str:
        return f"https://huggingface.co/{self.repo}"


def check_access(
    token: str, repo: str = DIARIZATION_REPO, timeout: float = 10.0
) -> AccessResult:
    """Check whether ``token`` can actually fetch the gated diarization model.

    Network problems fail *open* (``ok=True``): a flaky connection should not
    block a recording, and the pipeline will report the real error if it matters.
    """
    if not token:
        return AccessResult(False, "No HuggingFace token configured.", repo)

    url = f"https://huggingface.co/{repo}/resolve/main/{PROBE_FILE}"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return AccessResult(True, "", repo)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return AccessResult(False, _gate_message(exc.code, repo), repo)
        return AccessResult(False, f"HuggingFace returned HTTP {exc.code}.", repo)
    except (urllib.error.URLError, TimeoutError, OSError):
        return AccessResult(True, "", repo)  # fail open


def _gate_message(code: int, repo: str) -> str:
    if code == 401:
        return "The HuggingFace token was rejected. It may be revoked or mistyped."
    return (
        f"Your token cannot access {repo}.\n\n"
        f"Accept the model terms at:\nhttps://huggingface.co/{repo}\n\n"
        "Note this is a different model from speaker-diarization-3.1 -- "
        "whisperx changed its default, so accepting the older one is not enough."
    )
