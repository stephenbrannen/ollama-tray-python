"""Maps a client result to the tray's status and tooltip text, with no Qt dependency."""

import re
from enum import Enum
from html import escape

from ollama_tray.client import LoadedModel, OllamaUnavailable

# The tooltip says "GB", so this is decimal (10^9 bytes), not binary GiB.
BYTES_PER_GB = 1_000_000_000
# A long line would stretch the tooltip and notification across the screen.
MAX_LINE_CHARACTERS = 120
LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class TrayStatus(Enum):
    # Shown only until the first poll returns. It is grey, not red, because the server has not
    # failed yet. A failure and the not-responding watchdog still use red.
    CHECKING = "checking"
    LOADED = "loaded"
    IDLE = "idle"
    UNREACHABLE = "unreachable"


def status_from_result(result: list[LoadedModel] | OllamaUnavailable) -> TrayStatus:
    if isinstance(result, OllamaUnavailable):
        return TrayStatus.UNREACHABLE
    return TrayStatus.LOADED if result else TrayStatus.IDLE


def shorten(text: str) -> str:
    """Cut `text` to one line's worth of characters, marking the cut with `...`."""
    if len(text) <= MAX_LINE_CHARACTERS:
        return text
    return f"{text[: MAX_LINE_CHARACTERS - 3]}..."


def escape_line(text: str) -> str:
    """Make one line of server-supplied text safe to show, capped in length.

    Qt may read a notification as rich text, so a model named `<a href="x">` could otherwise
    render as a link. The cap comes first so it cannot cut an escape sequence in half.
    """
    return escape(shorten(text))


def plain_text(text: str) -> str:
    """Make server-supplied text safe as one value in the tooltip, which is plain text.

    The tooltip is not escaped. Desktops show it as plain text, so `&amp;` would appear literally.
    A control character or a line break inside one value would break the one-value-per-line layout.
    """
    return CONTROL_CHARACTERS.sub("", LINE_BREAKS.sub(" ", text))


def plain_line(text: str) -> str:
    """Make one tooltip value plain text, capped in length."""
    return shorten(plain_text(text))


def menu_label_text(text: str) -> str:
    """Make server-supplied text safe as a menu item label, capped in length.

    Qt reads a single `&` in an action's text as a shortcut marker and hides it. Menu text is
    not rich text, so only `&` needs doubling, and the cap comes first so it cannot split a pair.
    """
    return shorten(text).replace("&", "&&")


def escape_lines(text: str) -> str:
    return "\n".join(escape_line(line) for line in text.splitlines())


def tooltip_text(host: str, result: list[LoadedModel] | OllamaUnavailable | None) -> str:
    """Return the server address, then one line per loaded model with its VRAM in GB."""
    host = plain_text(host)
    if result is None:
        return f"{host}\nChecking..."
    if isinstance(result, OllamaUnavailable):
        reason = str(result).strip().splitlines()
        if not reason:
            return f"{host}\nUnreachable"
        return f"{host}\nUnreachable: {plain_line(reason[0])}"
    if not result:
        return f"{host}\nNo model loaded"
    lines = [
        f"{plain_line(model.name)} ({model.size_vram / BYTES_PER_GB:.1f} GB VRAM)"
        for model in result
    ]
    return "\n".join([host, *lines])
