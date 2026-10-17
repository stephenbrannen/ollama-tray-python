"""Maps a client result to the tray's status and tooltip text, with no Qt dependency."""

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from html import escape

from ollama_tray.client import LoadedModel, OllamaUnavailable

# The tooltip says "GB", so this is decimal (10^9 bytes), not binary GiB.
BYTES_PER_GB = 1_000_000_000
# A long line would stretch the tooltip and notification across the screen.
MAX_LINE_CHARACTERS = 120
# `keep_alive: -1` makes Ollama report a time centuries away, not a special value. Anything
# further out than this is a model that stays loaded.
KEPT_LOADED_AFTER = timedelta(days=100 * 365)
UNSAFE_CATEGORIES = frozenset({"Cc", "Cf"})


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
    """Make server-supplied text safe as one tooltip value, without HTML-escaping it.

    Desktops show the tooltip as plain text, so `&` and `<` must stay as they are. Line breaks
    and other control characters would break the one-value-per-line layout. Format characters
    such as U+202E (right-to-left override) are invisible but can make the text read as
    something else. Both become spaces, and runs of whitespace collapse to one space.
    """
    spaced = "".join(" " if unicodedata.category(c) in UNSAFE_CATEGORIES else c for c in text)
    return " ".join(spaced.split())


def plain_line(text: str) -> str:
    """Make one tooltip value plain text, capped in length."""
    return shorten(plain_text(text))


def is_kept_loaded(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at - now > KEPT_LOADED_AFTER


def unload_text(expires_at: datetime | None, now: datetime) -> str:
    """Say when the server unloads a model on its own, or return `""` when that is unknown."""
    if expires_at is None:
        return ""
    if is_kept_loaded(expires_at, now):
        return "kept loaded"
    left = expires_at - now
    minutes = int(left.total_seconds() // 60)
    if minutes < 1:
        return "unloads in <1m"
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"unloads in {minutes}m"
    return f"unloads in {hours}h" + (f" {minutes}m" if minutes else "")


def menu_label_text(text: str) -> str:
    """Make server-supplied text safe as a menu item label, capped in length.

    Qt reads a single `&` in an action's text as a shortcut marker and hides it. Menu text is
    not rich text, so only `&` needs doubling, and the cap comes first so it cannot split a pair.
    """
    return shorten(text).replace("&", "&&")


def escape_lines(text: str) -> str:
    """Escape each line of server-supplied text and keep the line breaks."""
    return "\n".join(escape_line(line) for line in text.splitlines())


def utc_now() -> datetime:
    return datetime.now(UTC)


def tooltip_text(
    host: str,
    result: list[LoadedModel] | OllamaUnavailable | None,
    now: Callable[[], datetime] = utc_now,
) -> str:
    """Return the server address, then one line per loaded model.

    Each line has the model's VRAM in GB and, when known, the time until the server unloads it.
    """
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
    current = now()
    lines = []
    for model in result:
        details = [f"{model.size_vram / BYTES_PER_GB:.1f} GB VRAM"]
        if text := unload_text(model.expires_at, current):
            details.append(text)
        lines.append(f"{plain_line(model.name)} ({', '.join(details)})")
    return "\n".join([host, *lines])
