from datetime import UTC, datetime, timedelta

from pytest import mark, param

from ollama_tray.client import LoadedModel, OllamaUnavailable
from ollama_tray.status import TrayStatus, status_from_result, tooltip_text, unload_text
from tests.fixtures import HOST, make_model

RICH_TEXT_NAME = '<a href="x">click</a>'
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


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
            f"{HOST}\n{RICH_TEXT_NAME} (4.7 GB VRAM)",
        ),
        (
            [make_model("r&d\x07:8b\nsecond", 4_700_000_000)],
            f"{HOST}\nr&d :8b second (4.7 GB VRAM)",
        ),
        (
            [make_model("llama\u202e3\u200b:8b", 4_700_000_000)],
            f"{HOST}\nllama 3 :8b (4.7 GB VRAM)",
        ),
        (
            [
                make_model("llama3:8b", 4_700_000_000, NOW + timedelta(hours=3, minutes=12)),
                make_model("qwen3:4b", 2_549_000_000, NOW + timedelta(days=105_000)),
                make_model("phi4:14b", 9_100_000_000),
            ],
            f"{HOST}\nllama3:8b (4.7 GB VRAM, unloads in 3h 12m)"
            "\nqwen3:4b (2.5 GB VRAM, kept loaded)"
            "\nphi4:14b (9.1 GB VRAM)",
        ),
        (
            [make_model("m" * 10_000, 4_700_000_000)],
            f"{HOST}\n{'m' * 117}... (4.7 GB VRAM)",
        ),
        (
            OllamaUnavailable(f"{RICH_TEXT_NAME} & \x1b[31mred"),
            f"{HOST}\nUnreachable: {RICH_TEXT_NAME} & [31mred",
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
        "model-name-with-control-character-and-newline",
        "model-name-with-format-characters",
        "unload-times",
        "long-model-name",
        "rich-text-reason",
        "long-reason",
    ],
)
def test_tooltip_text(result: list[LoadedModel] | OllamaUnavailable | None, expected: str) -> None:
    assert tooltip_text(HOST, result, lambda: NOW) == expected


@mark.parametrize(
    ("expires_at", "expected"),
    [
        param(None, "", id="unknown"),
        param(NOW - timedelta(minutes=5), "unloads in <1m", id="already-past"),
        param(NOW + timedelta(seconds=59), "unloads in <1m", id="under-a-minute"),
        param(NOW + timedelta(minutes=1), "unloads in 1m", id="one-minute"),
        param(NOW + timedelta(minutes=4, seconds=59), "unloads in 4m", id="minutes-round-down"),
        param(NOW + timedelta(minutes=59, seconds=59), "unloads in 59m", id="just-under-an-hour"),
        param(NOW + timedelta(hours=1), "unloads in 1h", id="whole-hour"),
        param(NOW + timedelta(hours=3, minutes=12), "unloads in 3h 12m", id="hours-and-minutes"),
        param(NOW + timedelta(hours=23, minutes=59), "unloads in 23h 59m", id="just-under-a-day"),
        param(NOW + timedelta(days=105_000), "kept loaded", id="kept-forever"),
        param(NOW + timedelta(days=36_500, minutes=1), "kept loaded", id="just-over-the-cutoff"),
        param(NOW + timedelta(days=36_500), "unloads in 876000h", id="at-the-cutoff"),
    ],
)
def test_unload_text(expires_at: datetime | None, expected: str) -> None:
    assert unload_text(expires_at, NOW) == expected
