from pytest import mark

from ollama_tray.client import LoadedModel, OllamaUnavailable
from ollama_tray.status import TrayStatus, status_from_result, tooltip_text

HOST = "http://localhost:11434"
RICH_TEXT_NAME = '<a href="x">click</a>'
ESCAPED_RICH_TEXT_NAME = "&lt;a href=&quot;x&quot;&gt;click&lt;/a&gt;"


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
        (OllamaUnavailable("down"), "http://localhost:11434\nUnreachable: down"),
        (OllamaUnavailable(""), "http://localhost:11434\nUnreachable"),
        (OllamaUnavailable("a: boom\nb: bang"), "http://localhost:11434\nUnreachable: a: boom"),
        ([], "http://localhost:11434\nNo model loaded"),
        (
            [make_model("llama3:8b", 4_700_000_000), make_model("qwen3:4b", 2_549_000_000)],
            "http://localhost:11434\nllama3:8b (4.7 GB VRAM)\nqwen3:4b (2.5 GB VRAM)",
        ),
        (
            [make_model(RICH_TEXT_NAME, 4_700_000_000)],
            f"http://localhost:11434\n{ESCAPED_RICH_TEXT_NAME} (4.7 GB VRAM)",
        ),
        (
            [make_model("m" * 10_000, 4_700_000_000)],
            f"http://localhost:11434\n{'m' * 117}... (4.7 GB VRAM)",
        ),
        (
            OllamaUnavailable(RICH_TEXT_NAME),
            f"http://localhost:11434\nUnreachable: {ESCAPED_RICH_TEXT_NAME}",
        ),
        (
            OllamaUnavailable("e" * 10_000),
            f"http://localhost:11434\nUnreachable: {'e' * 117}...",
        ),
    ],
    ids=[
        "first-poll-pending",
        "unreachable",
        "no-reason",
        "multi-line-reason",
        "nothing-loaded",
        "models-with-vram",
        "rich-text-model-name",
        "long-model-name",
        "rich-text-reason",
        "long-reason",
    ],
)
def test_tooltip_text(result: list[LoadedModel] | OllamaUnavailable | None, expected: str) -> None:
    assert tooltip_text(HOST, result) == expected
