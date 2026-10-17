from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu
from pytest import CaptureFixture, MonkeyPatch, fixture, mark, raises
from responses import RequestsMock

from ollama_tray.app import TrayController, draw_icon, main, populate_menu
from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable
from ollama_tray.status import TrayStatus


def make_model(name: str) -> LoadedModel:
    return LoadedModel(
        name=name, size_vram=4_700_000_000, expires_at=datetime(2026, 1, 1, tzinfo=UTC)
    )


@fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    yield app


def wait_for(condition: Callable[[], bool], timeout_milliseconds: int) -> bool:
    """Pump the event loop until the worker's queued signals have been delivered."""
    waited = 0
    while not condition() and waited < timeout_milliseconds:
        QTest.qWait(10)
        waited += 10
    return condition()


def labels(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


@mark.parametrize(
    ("names", "expected"),
    [
        ([], ["Refresh now", "Quit"]),
        (["llama3:8b"], ["Stop llama3:8b", "Refresh now", "Quit"]),
        (
            ["llama3:8b", "qwen3:4b"],
            ["Stop llama3:8b", "Stop qwen3:4b", "Stop all", "Refresh now", "Quit"],
        ),
    ],
    ids=["none-loaded", "one-loaded", "two-loaded"],
)
def test_populate_menu_lists_stop_actions(
    qapp: QApplication, names: list[str], expected: list[str]
) -> None:
    menu = QMenu()

    populate_menu(menu, [make_model(name) for name in names], print, print, print, print)

    assert labels(menu) == expected


def test_populate_menu_actions_call_their_callbacks(qapp: QApplication) -> None:
    on_stop, on_stop_all, on_refresh, on_quit = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    menu = QMenu()
    populate_menu(
        menu,
        [make_model("llama3:8b"), make_model("qwen3:4b")],
        on_stop,
        on_stop_all,
        on_refresh,
        on_quit,
    )

    for action in menu.actions():
        action.trigger()

    on_stop.assert_any_call("llama3:8b")
    on_stop.assert_any_call("qwen3:4b")
    on_stop_all.assert_called_once_with()
    on_refresh.assert_called_once_with()
    on_quit.assert_called_once_with()


def test_apply_result_updates_tooltip_and_menu(qapp: QApplication) -> None:
    controller = TrayController(
        OllamaClient("http://ollama.test:11434"), "http://ollama.test:11434", 5
    )

    controller.apply_result([make_model("llama3:8b")])

    assert controller._tray.toolTip() == "http://ollama.test:11434\nllama3:8b (4.7 GB VRAM)"
    assert labels(controller._menu) == ["Stop llama3:8b", "Refresh now", "Quit"]

    controller.apply_result(OllamaUnavailable("down"))

    assert controller._tray.toolTip() == "http://ollama.test:11434\nUnreachable"
    assert labels(controller._menu) == ["Refresh now", "Quit"]


@mark.parametrize(
    ("status", "pixel"),
    [
        (TrayStatus.LOADED, "#2ea043"),
        (TrayStatus.IDLE, "#8b949e"),
        (TrayStatus.UNREACHABLE, "#da3633"),
    ],
)
def test_draw_icon_fills_center_with_status_color(
    qapp: QApplication, status: TrayStatus, pixel: str
) -> None:
    image = draw_icon(status).pixmap(64, 64).toImage()

    assert image.pixelColor(32, 32).name() == pixel


@fixture
def responses() -> Iterator[RequestsMock]:
    with RequestsMock() as mock:
        yield mock


def test_refresh_applies_worker_result_on_gui_thread(
    qapp: QApplication, responses: RequestsMock
) -> None:
    host = "http://ollama.test:11434"
    responses.get(
        f"{host}/api/ps",
        json={
            "models": [
                {
                    "name": "llama3:8b",
                    "size_vram": 4_700_000_000,
                    "expires_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )
    controller = TrayController(OllamaClient(host), host, 5)

    controller.refresh()
    controller.refresh()
    expected = f"{host}\nllama3:8b (4.7 GB VRAM)"

    assert wait_for(lambda: controller._tray.toolTip() == expected, 2000)
    assert len(responses.calls) == 1
    assert labels(controller._menu) == ["Stop llama3:8b", "Refresh now", "Quit"]


@patch.object(OllamaClient, "loaded_models", side_effect=[RuntimeError("bug"), []])
def test_poll_recovers_after_unexpected_error(loaded_models: MagicMock, qapp: QApplication) -> None:
    host = "http://ollama.test:11434"
    controller = TrayController(OllamaClient(host), host, 5)

    controller.refresh()
    assert wait_for(lambda: controller._tray.toolTip().endswith("Unreachable"), 2000)
    controller.refresh()

    assert wait_for(lambda: controller._tray.toolTip().endswith("No model loaded"), 2000)
    assert loaded_models.call_count == 2


def test_stop_failure_is_reported_and_status_refreshed(
    qapp: QApplication, responses: RequestsMock
) -> None:
    host = "http://ollama.test:11434"
    responses.post(f"{host}/api/generate", status=500)
    responses.get(f"{host}/api/ps", json={"models": []})
    controller = TrayController(OllamaClient(host), host, 5)
    controller._tray.showMessage = MagicMock()

    controller.stop_models(["llama3:8b"])

    assert wait_for(lambda: controller._tray.toolTip().endswith("No model loaded"), 2000)
    controller._tray.showMessage.assert_called_once()


@mark.parametrize(
    "argv",
    [["--host", "gpu-box:notaport"], ["--interval", "0"], ["--interval", "soon"]],
    ids=["non-numeric-port", "zero-interval", "non-numeric-interval"],
)
def test_main_reports_bad_arguments_as_cli_error(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str], argv: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray", *argv])

    with raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert "usage: ollama-tray" in capsys.readouterr().err


@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=False)
@patch("ollama_tray.app.QApplication")
def test_main_exits_with_message_when_no_tray(
    _app: MagicMock, _available: MagicMock, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray"])

    with raises(SystemExit) as exit_info:
        main()

    assert "AppIndicator" in str(exit_info.value.code)


def test_stop_all_continues_past_a_failure_and_reports_each(
    qapp: QApplication, responses: RequestsMock
) -> None:
    host = "http://ollama.test:11434"
    responses.post(f"{host}/api/generate", status=500)
    responses.post(f"{host}/api/generate", status=200)
    responses.get(f"{host}/api/ps", json={"models": []})
    controller = TrayController(OllamaClient(host), host, 5)
    controller._tray.showMessage = MagicMock()

    controller.stop_models(["a", "b"])

    assert wait_for(lambda: len(responses.calls) == 3, 2000)
    assert wait_for(lambda: controller._tray.showMessage.called, 2000)
    message = controller._tray.showMessage.call_args.args[1]
    assert message.startswith("a: ") and "b:" not in message
