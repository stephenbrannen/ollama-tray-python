import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

from pytest import MonkeyPatch, fixture, mark, param, raises
from requests import ConnectionError as RequestsConnectionError
from requests import Timeout
from responses import RequestsMock, matchers

from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable, resolve_host
from tests.fixtures import HOST

OVERSIZE_TOTAL_BYTES = 64 * 1024 * 1024


class LoopbackServer(ThreadingHTTPServer):
    """A real HTTP server for the behaviors `responses` cannot fake: slow and endless bodies."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = "trickle"
        self.paths: list[str] = []
        self.bytes_sent = 0
        self.stopped = Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, LoopbackServer)
        server.paths.append(self.path)
        if server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        if server.mode == "truncated":
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.end_headers()
        try:
            if server.mode == "trickle":
                # Bounded so a client without a deadline fails the test instead of hanging it.
                for _ in range(60):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    if server.stopped.wait(0.05):
                        return
            else:
                chunk = b" " * 65536
                while server.bytes_sent < OVERSIZE_TOTAL_BYTES:
                    self.wfile.write(chunk)
                    server.bytes_sent += len(chunk)
        except OSError:
            return

    def log_message(self, format: str, *args: object) -> None:
        return


@fixture
def loopback() -> Iterator[LoopbackServer]:
    server = LoopbackServer()
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.stopped.set()
    server.shutdown()
    server.server_close()
    thread.join()


@fixture
def client() -> OllamaClient:
    return OllamaClient(HOST)


@mark.parametrize(
    ("flag", "env", "expected"),
    [
        param(None, {}, "http://127.0.0.1:11434", id="default"),
        param(None, {"OLLAMA_HOST": ""}, "http://127.0.0.1:11434", id="empty-env-is-unset"),
        param(None, {"OLLAMA_HOST": "gpu-box"}, "http://gpu-box:11434", id="env-bare-host"),
        param(None, {"OLLAMA_HOST": "gpu-box:8080"}, "http://gpu-box:8080", id="env-host-and-port"),
        param(
            None, {"OLLAMA_HOST": "https://gpu-box"}, "https://gpu-box:11434", id="env-scheme-only"
        ),
        param(
            None, {"OLLAMA_HOST": "HTTPS://Gpu-Box"}, "https://gpu-box:11434", id="uppercase-scheme"
        ),
        param(
            None,
            {"OLLAMA_HOST": "https://gpu-box:8443"},
            "https://gpu-box:8443",
            id="env-scheme-and-port",
        ),
        param(
            None,
            {"OLLAMA_HOST": "http://gpu-box:8080/"},
            "http://gpu-box:8080",
            id="trailing-slash",
        ),
        param(None, {"OLLAMA_HOST": "0.0.0.0"}, "http://0.0.0.0:11434", id="wildcard-address"),
        param(None, {"OLLAMA_HOST": "[::1]:9000"}, "http://[::1]:9000", id="ipv6-with-port"),
        param(
            None,
            {"OLLAMA_HOST": "http://box:8080/ollama/"},
            "http://box:8080/ollama",
            id="path-kept-trailing-slash-dropped",
        ),
        param(
            None,
            {"OLLAMA_HOST": "box/ollama"},
            "http://box:11434/ollama",
            id="path-with-default-port",
        ),
        param(None, {"OLLAMA_HOST": ":8080"}, "http://127.0.0.1:8080", id="port-only"),
        param("flag-box:1", {"OLLAMA_HOST": "env-box:2"}, "http://flag-box:1", id="flag-beats-env"),
        param("", {"OLLAMA_HOST": "env-box:2"}, "http://env-box:2", id="empty-flag-falls-to-env"),
        param("  flag-box:1\t", {}, "http://flag-box:1", id="flag-whitespace-trimmed"),
        param(
            None, {"OLLAMA_HOST": " env-box:2 "}, "http://env-box:2", id="env-whitespace-trimmed"
        ),
        param("  ", {"OLLAMA_HOST": "env-box:2"}, "http://env-box:2", id="blank-flag-falls-to-env"),
        param(None, {"OLLAMA_HOST": "  "}, "http://127.0.0.1:11434", id="blank-env-is-unset"),
    ],
)
def test_resolve_host(
    monkeypatch: MonkeyPatch, flag: str | None, env: dict[str, str], expected: str
) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert resolve_host(flag) == expected


@mark.parametrize(
    "host",
    [
        param("box:abc", id="non-numeric-port"),
        param("box:70000", id="out-of-range-port"),
        param("box:0", id="port-zero"),
        param("ftp://box", id="ftp-scheme"),
        param("FILE:///etc/passwd", id="file-scheme-uppercase"),
        param("user@box", id="userinfo-user"),
        param("http://user:secret@box:8080", id="userinfo-user-and-password"),
        param("box?x=1", id="query"),
        param("box#frag", id="fragment"),
        param("box?", id="empty-query"),
        param("box#", id="empty-fragment"),
        param("  box?  ", id="empty-query-with-whitespace"),
    ],
)
def test_resolve_host_rejects_invalid_address(host: str) -> None:
    with raises(ValueError):
        resolve_host(host)


@mark.parametrize(
    ("payload", "expected"),
    [
        param({"models": []}, [], id="none"),
        param(
            {"models": [{"name": "llama3:8b", "size_vram": 5137025024}]},
            [LoadedModel("llama3:8b", 5137025024)],
            id="one-without-expires-at",
        ),
        param(
            {
                "models": [
                    {
                        "name": "llama3:8b",
                        "size_vram": 1,
                        "expires_at": "2026-10-16T12:30:00.123456789-04:00",
                    },
                    {"name": "qwen3:4b", "size_vram": 0, "expires_at": "2026-10-16T12:30:00Z"},
                ]
            },
            [
                LoadedModel("llama3:8b", 1, datetime(2026, 10, 16, 16, 30, 0, 123456, tzinfo=UTC)),
                LoadedModel("qwen3:4b", 0, datetime(2026, 10, 16, 12, 30, tzinfo=UTC)),
            ],
            id="expires-at-with-fraction-and-offset",
        ),
        *[
            param(
                {"models": [{"name": "llama3:8b", "size_vram": 1, "expires_at": value}]},
                [LoadedModel("llama3:8b", 1, None)],
                id=f"unreadable-expires-at-{label}",
            )
            for label, value in [
                ("not-a-date", "not a date"),
                ("empty", ""),
                ("null", None),
                ("number", 1_760_000_000),
                ("no-zone", "2026-10-16T12:30:00"),
                ("overflow", "9999-12-31T23:59:59-23:00"),
            ]
        ],
    ],
)
def test_loaded_models_parses_api_ps(
    client: OllamaClient,
    responses: RequestsMock,
    payload: dict[str, list[dict[str, str | int | None]]],
    expected: list[LoadedModel],
) -> None:
    responses.get(
        f"{HOST}/api/ps",
        json=payload,
        match=[matchers.request_kwargs_matcher({"timeout": 2})],
    )

    assert client.loaded_models() == expected


@mark.parametrize(
    "reply",
    [
        {"body": RequestsConnectionError("refused")},
        {"body": Timeout("slow")},
        {"status": 500},
    ],
    ids=["connection-error", "timeout", "http-500"],
)
def test_loaded_models_raises_unavailable(
    client: OllamaClient, responses: RequestsMock, reply: dict[str, Exception | int]
) -> None:
    responses.get(f"{HOST}/api/ps", **reply)

    with raises(OllamaUnavailable):
        client.loaded_models()


@mark.parametrize(
    "body",
    ["<html>bad gateway</html>", '{"error": "nope"}', '{"models": [{"name": "llama3:8b"}]}'],
    ids=["not-json", "missing-models-key", "missing-model-field"],
)
def test_loaded_models_raises_unavailable_on_unexpected_payload(
    client: OllamaClient, responses: RequestsMock, body: str
) -> None:
    responses.get(f"{HOST}/api/ps", body=body)

    with raises(OllamaUnavailable):
        client.loaded_models()


def test_unload_posts_keep_alive_zero_with_its_own_timeout(
    client: OllamaClient, responses: RequestsMock
) -> None:
    responses.post(
        f"{HOST}/api/generate",
        json={"done": True},
        match=[
            matchers.json_params_matcher({"model": "llama3:8b", "keep_alive": 0}),
            matchers.request_kwargs_matcher({"timeout": 10}),
        ],
    )

    client.unload("llama3:8b")

    assert len(responses.calls) == 1


def test_keep_loaded_posts_keep_alive_minus_one_with_the_unload_timeout(
    client: OllamaClient, responses: RequestsMock
) -> None:
    responses.post(
        f"{HOST}/api/generate",
        json={"done": True},
        match=[
            matchers.json_params_matcher({"model": "llama3:8b", "keep_alive": -1}),
            matchers.request_kwargs_matcher({"timeout": 10}),
        ],
    )

    client.keep_loaded("llama3:8b")

    assert len(responses.calls) == 1


def test_keep_loaded_raises_unavailable_on_server_error(
    client: OllamaClient, responses: RequestsMock
) -> None:
    responses.post(f"{HOST}/api/generate", status=500)

    with raises(OllamaUnavailable):
        client.keep_loaded("llama3:8b")


def test_unload_raises_unavailable_on_server_error(
    client: OllamaClient, responses: RequestsMock
) -> None:
    responses.post(f"{HOST}/api/generate", status=500)

    with raises(OllamaUnavailable):
        client.unload("llama3:8b")


def test_loaded_models_fails_within_deadline_on_trickling_server(
    monkeypatch: MonkeyPatch, loopback: LoopbackServer
) -> None:
    monkeypatch.setattr("ollama_tray.client.STATUS_TIMEOUT_SECONDS", 0.5)
    loopback.mode = "trickle"
    started = time.monotonic()

    with raises(OllamaUnavailable, match="took too long"):
        OllamaClient(loopback.url).loaded_models()

    # The server stops trickling after 3s, so a client without a deadline takes at least that long.
    # The bound sits well above the 0.5s deadline to absorb a slow runner and well below 3s.
    assert time.monotonic() - started < 2.5


def test_loaded_models_fails_on_oversized_body_without_reading_it_all(
    loopback: LoopbackServer,
) -> None:
    loopback.mode = "oversize"

    with raises(OllamaUnavailable, match="too large"):
        OllamaClient(loopback.url).loaded_models()

    assert loopback.bytes_sent < OVERSIZE_TOTAL_BYTES


def test_loaded_models_does_not_follow_redirects(loopback: LoopbackServer) -> None:
    loopback.mode = "redirect"

    with raises(OllamaUnavailable, match="redirect"):
        OllamaClient(loopback.url).loaded_models()

    assert loopback.paths == ["/api/ps"]


def test_loaded_models_raises_unavailable_when_server_closes_mid_body(
    loopback: LoopbackServer,
) -> None:
    loopback.mode = "truncated"

    with raises(OllamaUnavailable):
        OllamaClient(loopback.url).loaded_models()
