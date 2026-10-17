from pytest import mark

from ollama_tray.client import LoadedModel, OllamaUnavailable
from ollama_tray.status import TrayStatus, status_from_result, tooltip_text
from tests.fixtures import HOST, make_model

RICH_TEXT_NAME = '<a href="x">click</a>'
ESCAPED_RICH_TEXT_NAME = "&lt;a href=&quot;x&quot;&gt;click&lt;/a&gt;"


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
        (None, f"{HOST}\nChecking..."),
        (OllamaUnavailable("down"), f"{HOST}\nUnreachable: down"),
        (OllamaUnavailable(""), f"{HOST}\nUnreachable"),
        (OllamaUnavailable("a: boom\nb: bang"), f"{HOST}\nUnreachable: a: boom"),
        ([], f"{HOST}\nNo model loaded"),
        (
            [make_model("llama3:8b", 4_700_000_000), make_model("qwen3:4b", 2_549_000_000)],
            f"{HOST}\nllama3:8b (4.7 GB VRAM)\nqwen3:4b (2.5 GB VRAM)",
        ),
        (
            [make_model(RICH_TEXT_NAME, 4_700_000_000)],
            f"{HOST}\n{ESCAPED_RICH_TEXT_NAME} (4.7 GB VRAM)",
        ),
        (
            [make_model("m" * 10_000, 4_700_000_000)],
            f"{HOST}\n{'m' * 117}... (4.7 GB VRAM)",
        ),
        (
            OllamaUnavailable(RICH_TEXT_NAME),
            f"{HOST}\nUnreachable: {ESCAPED_RICH_TEXT_NAME}",
        ),
        (
            OllamaUnavailable("e" * 10_000),
            f"{HOST}\nUnreachable: {'e' * 117}...",
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
