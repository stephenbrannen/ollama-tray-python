from pytest import mark

from ollama_tray.client import LoadedModel, OllamaUnavailable
from ollama_tray.status import TrayStatus, status_from_result, tooltip_text

HOST = "http://localhost:11434"


def make_model(name: str, size_vram: int) -> LoadedModel:
    return LoadedModel(name=name, size_vram=size_vram)


@mark.parametrize(
    ("result", "expected"),
    [
        ([make_model("llama3:8b", 4_700_000_000)], TrayStatus.LOADED),
        (
            [make_model("llama3:8b", 4_700_000_000), make_model("qwen3:4b", 2_500_000_000)],
            TrayStatus.LOADED,
        ),
        ([], TrayStatus.IDLE),
        (OllamaUnavailable("connection refused"), TrayStatus.UNREACHABLE),
    ],
    ids=["one-model", "two-models", "nothing-loaded", "unreachable"],
)
def test_status_from_result(
    result: list[LoadedModel] | OllamaUnavailable, expected: TrayStatus
) -> None:
    assert status_from_result(result) == expected


@mark.parametrize(
    ("result", "expected"),
    [
        (None, "http://localhost:11434\nChecking..."),
        (OllamaUnavailable("down"), "http://localhost:11434\nUnreachable"),
        ([], "http://localhost:11434\nNo model loaded"),
        (
            [make_model("llama3:8b", 4_700_000_000), make_model("qwen3:4b", 2_549_000_000)],
            "http://localhost:11434\nllama3:8b (4.7 GB VRAM)\nqwen3:4b (2.5 GB VRAM)",
        ),
    ],
    ids=["first-poll-pending", "unreachable", "nothing-loaded", "models-with-vram"],
)
def test_tooltip_text(result: list[LoadedModel] | OllamaUnavailable | None, expected: str) -> None:
    assert tooltip_text(HOST, result) == expected
