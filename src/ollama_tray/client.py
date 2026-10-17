"""Client for the two Ollama endpoints the tray uses, with no GUI dependency."""

import json
import os
import time
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError as Urllib3Error

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_PORT = 11434
STATUS_TIMEOUT_SECONDS = 2
# Unloading makes the server free GPU memory first, which can take longer than a status check.
UNLOAD_TIMEOUT_SECONDS = 10
# `/api/ps` is a few hundred bytes per model, so anything near this size is not Ollama.
MAX_BODY_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 8192
ALLOWED_SCHEMES = ("http", "https")


class OllamaUnavailable(Exception):
    """The server could not be reached, timed out, or answered with an error status."""


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_vram: int


class _RunningModel(TypedDict):
    name: str
    size_vram: int


class _PsPayload(TypedDict):
    models: list[_RunningModel]


def resolve_host(flag: str | None) -> str:
    """Return the server base URL from the flag, else `OLLAMA_HOST`, else the default.

    The address is `[http[s]://][host][:port][/path]`:

    * A missing scheme means `http`. Any scheme other than `http` or `https` is rejected.
    * A missing host means `127.0.0.1`, not `localhost`, because `localhost` can resolve to
      `::1` while Ollama listens on IPv4.
    * A missing port means 11434 on every scheme, including `https`. The `ollama` CLI uses
      80 and 443 instead, but Ollama is almost never on those ports. Port 0 is rejected.
    * A path is kept without its trailing slash.
    * A user name, password, query or fragment is rejected.

    Every rejection raises `ValueError`.
    """
    raw = flag or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"scheme must be http or https, not {parsed.scheme!r}")
    # Credentials in the address would be printed in the tooltip and sent on every request.
    if "@" in parsed.netloc:
        raise ValueError("user and password are not supported")
    if parsed.query or parsed.fragment:
        raise ValueError("query and fragment are not supported")
    hostname = parsed.hostname or "127.0.0.1"
    if ":" in hostname:
        hostname = f"[{hostname}]"
    # `parsed.port or DEFAULT_PORT` would turn port 0 into the default instead of rejecting it.
    port = DEFAULT_PORT if parsed.port is None else parsed.port
    if port == 0:
        raise ValueError("port must be between 1 and 65535")
    return f"{parsed.scheme}://{hostname}:{port}{parsed.path.rstrip('/')}"


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        # `requests.Session` is not guaranteed thread-safe. The tray calls this client from one
        # worker thread only, so the session can keep its connection open between polls.
        self._session = requests.Session()

    def loaded_models(self) -> list[LoadedModel]:
        raw = self._request("GET", "/api/ps", STATUS_TIMEOUT_SECONDS)
        try:
            body: _PsPayload = json.loads(raw)
            return [
                LoadedModel(name=str(entry["name"]), size_vram=int(entry["size_vram"]))
                for entry in body["models"]
            ]
        except (ValueError, KeyError, TypeError) as error:
            # A 200 that is not Ollama's payload (a proxy error page, say) is as unusable to
            # the tray as an unreachable server, so callers get the same single failure.
            raise OllamaUnavailable(f"unexpected /api/ps response: {error}") from error

    def unload(self, name: str) -> None:
        # Ollama evicts a model as soon as its keep-alive is zero. `ollama stop` does the same.
        self._request(
            "POST",
            "/api/generate",
            UNLOAD_TIMEOUT_SECONDS,
            payload={"model": name, "keep_alive": 0},
        )

    def _request(
        self,
        method: str,
        path: str,
        timeout_seconds: float,
        payload: dict[str, str | int] | None = None,
    ) -> bytes:
        """Return the response body, or raise `OllamaUnavailable`.

        The `requests` timeout limits each socket wait, not the whole request, so a server
        that sends one byte at a time could hold the call open forever. Once the headers
        arrive, the body is read in chunks against a deadline and capped in size. The deadline
        is checked between chunks, so a stalled read can overrun it by one socket timeout.
        """
        deadline = time.monotonic() + timeout_seconds
        try:
            # Redirects are refused because the tray only talks to the address it was given.
            with self._session.request(
                method,
                f"{self._base_url}{path}",
                json=payload,
                timeout=timeout_seconds,
                allow_redirects=False,
                stream=True,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise OllamaUnavailable(f"unexpected redirect (HTTP {response.status_code})")
                response.raise_for_status()
                body = bytearray()
                # `read1` returns whatever has arrived. `iter_content` waits to fill a whole
                # chunk, which would let a trickling server skip the deadline check.
                while chunk := response.raw.read1(READ_CHUNK_BYTES, decode_content=True):
                    body += chunk
                    if len(body) > MAX_BODY_BYTES:
                        raise OllamaUnavailable("response body is too large")
                    if time.monotonic() > deadline:
                        raise OllamaUnavailable("response took too long")
        # Reading `response.raw` directly bypasses `requests`, so a dropped connection or a read
        # timeout surfaces as a urllib3 or socket error instead of a `RequestException`.
        except (requests.RequestException, Urllib3Error, OSError) as error:
            raise OllamaUnavailable(str(error)) from error
        return bytes(body)
