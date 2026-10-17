"""Maps a client result to the tray's status and tooltip text, with no Qt dependency."""

from enum import Enum

from ollama_tray.client import LoadedModel, OllamaUnavailable

BYTES_PER_GB = 1_000_000_000


class TrayStatus(Enum):
    LOADED = "loaded"
    IDLE = "idle"
    UNREACHABLE = "unreachable"


def status_from_result(result: list[LoadedModel] | OllamaUnavailable) -> TrayStatus:
    if isinstance(result, OllamaUnavailable):
        return TrayStatus.UNREACHABLE
    return TrayStatus.LOADED if result else TrayStatus.IDLE


def tooltip_text(host: str, result: list[LoadedModel] | OllamaUnavailable | None) -> str:
    """Return the server address, then one line per loaded model with its VRAM in GB."""
    if result is None:
        return f"{host}\nChecking..."
    if isinstance(result, OllamaUnavailable):
        reason = str(result).strip().splitlines()
        return f"{host}\nUnreachable: {reason[0]}" if reason else f"{host}\nUnreachable"
    if not result:
        return f"{host}\nNo model loaded"
    lines = [f"{model.name} ({model.size_vram / BYTES_PER_GB:.1f} GB VRAM)" for model in result]
    return "\n".join([host, *lines])
