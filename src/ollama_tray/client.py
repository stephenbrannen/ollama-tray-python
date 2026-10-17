"""Client for the two Ollama endpoints the tray uses, with no GUI dependency."""

import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import requests

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_PORT = 11434
STATUS_TIMEOUT_SECONDS = 2


class OllamaUnavailable(Exception):
    """The server could not be reached, timed out, or answered with an error status."""


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_vram: int
    expires_at: datetime


def resolve_host(flag: str | None) -> str:
    """Return the server base URL from the flag, else `OLLAMA_HOST`, else the default.

    Mirrors the `ollama` CLI so the tray and `ollama ps` talk to the same server: a
    missing scheme means `http://` and a missing port means 11434.
    """
    raw = flag or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    hostname = parsed.hostname or "localhost"
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{parsed.scheme}://{hostname}:{parsed.port or DEFAULT_PORT}"


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def loaded_models(self) -> list[LoadedModel]:
        body: dict[str, list[dict[str, str | int]]] = self._request("GET", "/api/ps").json()
        return [
            LoadedModel(
                name=str(entry["name"]),
                size_vram=int(entry["size_vram"]),
                expires_at=datetime.fromisoformat(str(entry["expires_at"])),
            )
            for entry in body.get("models", [])
        ]

    def unload(self, name: str) -> None:
        # Ollama evicts a model as soon as its keep-alive is zero. `ollama stop` does the same.
        self._request("POST", "/api/generate", json={"model": name, "keep_alive": 0})

    def _request(
        self, method: str, path: str, json: dict[str, str | int] | None = None
    ) -> requests.Response:
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                timeout=STATUS_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise OllamaUnavailable(str(error)) from error
        return response
