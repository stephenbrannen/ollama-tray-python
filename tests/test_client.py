from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

from pytest import MonkeyPatch, fixture, mark, raises
from requests import ConnectionError as RequestsConnectionError
from requests import Timeout
from responses import RequestsMock, matchers

from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable, resolve_host

BASE_URL = "http://ollama.test:11434"


@fixture
def responses() -> Iterator[RequestsMock]:
    with RequestsMock() as mock:
        yield mock


@fixture
def client() -> OllamaClient:
    return OllamaClient(BASE_URL)


@mark.parametrize(
    ("flag", "env", "expected"),
    [
        (None, None, "http://localhost:11434"),
        (None, "", "http://localhost:11434"),
        (None, "gpu-box", "http://gpu-box:11434"),
        (None, "gpu-box:8080", "http://gpu-box:8080"),
        (None, "https://gpu-box", "https://gpu-box:11434"),
        (None, "https://gpu-box:8443", "https://gpu-box:8443"),
        (None, "http://gpu-box:8080/", "http://gpu-box:8080"),
        (None, "0.0.0.0", "http://0.0.0.0:11434"),
        (None, "[::1]:9000", "http://[::1]:9000"),
        ("flag-box:1", "env-box:2", "http://flag-box:1"),
        ("", "env-box:2", "http://env-box:2"),
    ],
    ids=[
        "default",
        "empty-env-is-unset",
        "env-bare-host",
        "env-host-and-port",
        "env-scheme-only",
        "env-scheme-and-port",
        "trailing-slash",
        "wildcard-address",
        "ipv6-with-port",
        "flag-beats-env",
        "empty-flag-falls-to-env",
    ],
)
def test_resolve_host(
    monkeypatch: MonkeyPatch, flag: str | None, env: str | None, expected: str
) -> None:
    if env is None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_HOST", env)

    assert resolve_host(flag) == expected


@mark.parametrize(
    ("payload", "expected"),
    [
        ({"models": []}, []),
        (
            {
                "models": [
                    {
                        "name": "llama3:8b",
                        "size_vram": 5137025024,
                        "expires_at": "2026-10-16T12:30:00Z",
                    }
                ]
            },
            [LoadedModel("llama3:8b", 5137025024, datetime(2026, 10, 16, 12, 30, tzinfo=UTC))],
        ),
        (
            {
                "models": [
                    {
                        "name": "llama3:8b",
                        "size_vram": 5137025024,
                        "expires_at": "2026-10-16T05:30:00.837530123-07:00",
                    },
                    {"name": "qwen3:4b", "size_vram": 0, "expires_at": "2026-10-16T12:35:00Z"},
                ]
            },
            [
                LoadedModel(
                    "llama3:8b",
                    5137025024,
                    datetime(2026, 10, 16, 5, 30, 0, 837530, tzinfo=timezone(timedelta(hours=-7))),
                ),
                LoadedModel("qwen3:4b", 0, datetime(2026, 10, 16, 12, 35, tzinfo=UTC)),
            ],
        ),
    ],
    ids=["none", "one", "two-with-offset-and-nanoseconds"],
)
def test_loaded_models_parses_api_ps(
    client: OllamaClient,
    responses: RequestsMock,
    payload: dict[str, list[dict[str, str | int]]],
    expected: list[LoadedModel],
) -> None:
    responses.get(
        f"{BASE_URL}/api/ps",
        json=payload,
        match=[matchers.request_kwargs_matcher({"timeout": 2})],
    )

    assert client.loaded_models() == expected


@mark.parametrize(
    "failure",
    [RequestsConnectionError("refused"), Timeout("slow"), None],
    ids=["connection-error", "timeout", "http-500"],
)
def test_loaded_models_raises_unavailable(
    client: OllamaClient, responses: RequestsMock, failure: Exception | None
) -> None:
    if failure is None:
        responses.get(f"{BASE_URL}/api/ps", status=500)
    else:
        responses.get(f"{BASE_URL}/api/ps", body=failure)

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
    responses.get(f"{BASE_URL}/api/ps", body=body)

    with raises(OllamaUnavailable):
        client.loaded_models()


def test_unload_posts_keep_alive_zero(client: OllamaClient, responses: RequestsMock) -> None:
    responses.post(
        f"{BASE_URL}/api/generate",
        json={"done": True},
        match=[matchers.json_params_matcher({"model": "llama3:8b", "keep_alive": 0})],
    )

    client.unload("llama3:8b")

    assert len(responses.calls) == 1


def test_unload_raises_unavailable_on_server_error(
    client: OllamaClient, responses: RequestsMock
) -> None:
    responses.post(f"{BASE_URL}/api/generate", status=500)

    with raises(OllamaUnavailable):
        client.unload("llama3:8b")
