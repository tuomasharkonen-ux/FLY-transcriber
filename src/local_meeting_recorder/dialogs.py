"""Text prompts that actually accept typing.

rumps builds its input windows as an ``NSAlert`` with an accessory
``NSTextField``, and never makes that field the first responder. In a normal
app the alert would take keyboard focus anyway; this one runs as an *accessory*
app (no Dock icon), so it cannot become the active application and the field
renders but ignores every keystroke.

Rather than fight NSAlert focus from inside a background app, prompts are handed
to ``osascript``. Its dialog belongs to a separate, ordinary process that can
activate and take focus on its own.
"""

from __future__ import annotations

import subprocess

#: Long enough for any real answer, short enough that a forgotten dialog does
#: not block filing forever.
TIMEOUT_SECONDS = 600


def _literal(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def ask_text(
    title: str,
    message: str,
    default: str = "",
    timeout: int = TIMEOUT_SECONDS,
) -> str | None:
    """Prompt for a line of text.

    Returns the entered text, or ``None`` if cancelled, dismissed or timed out --
    callers treat that as "skip", never as an empty answer.
    """
    # The reply is bound to a variable rather than read from `result`: AppleScript
    # reassigns `result` after *every* statement, so the first `if` that inspects
    # it destroys it and the following lines fail with "variable result is not
    # defined" -- which looks exactly like the user pressing Skip.
    script = "\n".join(
        [
            # "me" is osascript itself; activating brings the dialog to the front
            # and gives it keyboard focus, which the menubar app cannot take.
            "tell me to activate",
            (
                f"set reply to display dialog {_literal(message)} "
                f"with title {_literal(title)} "
                f"default answer {_literal(default)} "
                'buttons {"Skip", "OK"} default button "OK" '
                f"giving up after {int(timeout)}"
            ),
            "if gave up of reply then return",
            # "Skip" is an ordinary button, not a Cancel button, so AppleScript
            # returns normally and would hand back whatever was typed.
            'if button returned of reply is "Skip" then return',
            "return text returned of reply",
        ]
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    # A cancelled dialog exits non-zero (-128); a timeout returns empty output.
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def ask_list(
    title: str,
    message: str,
    default: str = "",
    timeout: int = TIMEOUT_SECONDS,
) -> list[str]:
    """Prompt for a comma-separated list. Empty on skip."""
    text = ask_text(title, message, default, timeout)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]
