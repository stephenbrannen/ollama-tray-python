import logging
import sys
import time
from collections.abc import Callable, Iterator
from importlib.metadata import version
from threading import Event
from typing import NamedTuple
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from pytest import CaptureFixture, LogCaptureFixture, MonkeyPatch, fixture, mark, raises
from responses import RequestsMock

from ollama_tray.app import TrayController, draw_icon, main, populate_menu
from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable
from ollama_tray.status import TrayStatus

GREY = "#8b949e"
RED = "#da3633"
RICH_TEXT_NAME = '<a href="x">click</a>'


def make_model(name: str) -> LoadedModel:
    return LoadedModel(name=name, size_vram=4_700_000_000)


def icon_color(controller: TrayController) -> str:
    return controller._tray.icon().pixmap(64, 64).toImage().pixelColor(32, 32).name()


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

    assert controller._tray.toolTip() == "http://ollama.test:11434\nChecking..."
    assert icon_color(controller) == GREY

    controller.apply_result([make_model("llama3:8b")])

    assert controller._tray.toolTip() == "http://ollama.test:11434\nllama3:8b (4.7 GB VRAM)"
    assert labels(controller._menu) == ["Stop llama3:8b", "Refresh now", "Quit"]

    controller.apply_result(OllamaUnavailable("down"))

    assert controller._tray.toolTip() == "http://ollama.test:11434\nUnreachable: down"
    assert icon_color(controller) == RED
    assert labels(controller._menu) == ["Refresh now", "Quit"]


@mark.parametrize(
    ("status", "pixel"),
    [
        (TrayStatus.CHECKING, GREY),
        (TrayStatus.LOADED, "#2ea043"),
        (TrayStatus.IDLE, GREY),
        (TrayStatus.UNREACHABLE, RED),
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
    assert wait_for(lambda: controller._tray.toolTip().endswith("Unreachable: bug"), 2000)
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
    [
        ["--host", "gpu-box:notaport"],
        ["--host", "ftp://gpu-box"],
        ["--interval", "0"],
        ["--interval", "-1"],
        ["--interval", "soon"],
        ["--interval", "nan"],
        ["--interval", "inf"],
    ],
    ids=[
        "non-numeric-port",
        "ftp-scheme",
        "zero-interval",
        "negative-interval",
        "non-numeric-interval",
        "nan-interval",
        "infinite-interval",
    ],
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


@patch("ollama_tray.app.TrayController")
@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
@patch("ollama_tray.app.QApplication")
def test_main_starts_controller_and_exits_with_event_loop_code(
    app: MagicMock, _available: MagicMock, controller_class: MagicMock, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray", "--host", "gpu-box:8080", "--interval", "7"])
    app.return_value.exec.return_value = 3

    with raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 3
    controller_class.assert_called_once()
    assert controller_class.call_args.args[1:] == ("http://gpu-box:8080", 7.0)
    controller = controller_class.return_value
    app.return_value.aboutToQuit.connect.assert_called_once_with(controller.shutdown)
    controller.start.assert_called_once_with()


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


class StubClient(OllamaClient):
    """Answers instantly, or blocks on `gate` so a test controls when a call returns."""

    def __init__(self) -> None:
        super().__init__("http://ollama.test:11434")
        self.gate = Event()
        self.block_polls = False
        self.block_unloads = False
        self.unload_error: Exception | None = None
        self.unload_started = Event()
        self.polls = 0
        self.unloaded: list[str] = []
        self.models: list[LoadedModel] = []

    def loaded_models(self) -> list[LoadedModel]:
        self.polls += 1
        if self.block_polls:
            self.gate.wait()
        return self.models

    def unload(self, name: str) -> None:
        self.unload_started.set()
        if self.unload_error:
            raise self.unload_error
        if self.block_unloads:
            self.gate.wait()
        self.unloaded.append(name)


class Harness(NamedTuple):
    controller: TrayController
    client: StubClient


@fixture
def harness(qapp: QApplication) -> Iterator[Harness]:
    client = StubClient()
    controller = TrayController(client, "http://ollama.test:11434", 0.01)
    yield Harness(controller, client)
    client.gate.set()
    controller.shutdown()
    controller._worker.join(2)


def test_start_shows_tray_polls_and_keeps_polling_on_the_timer(harness: Harness) -> None:
    controller, client = harness
    controller._tray.show = MagicMock()

    controller.start()

    controller._tray.show.assert_called_once_with()
    assert wait_for(lambda: client.polls >= 3, 2000)
    assert controller._tray.toolTip().endswith("No model loaded")


def test_shutdown_returns_promptly_while_stop_all_is_hung(harness: Harness) -> None:
    controller, client = harness
    client.block_unloads = True
    controller.stop_models(["a", "b"])
    assert client.unload_started.wait(2)
    controller.start()
    controller._tray.hide = MagicMock()

    started = time.monotonic()
    controller.shutdown()
    elapsed = time.monotonic() - started
    client.gate.set()
    controller._worker.join(2)

    assert elapsed < 1
    assert controller._worker.daemon
    assert client.unloaded == ["a"]
    assert not controller._timer.isActive()
    controller._tray.hide.assert_called_once_with()


def test_shutdown_drops_queued_polls_and_refuses_new_work(harness: Harness) -> None:
    controller, client = harness
    client.block_unloads = True
    controller.stop_models(["a"])
    assert client.unload_started.wait(2)
    controller.refresh()

    controller.shutdown()
    controller.refresh()
    controller.stop_models(["b"])
    client.gate.set()
    controller._worker.join(2)

    assert client.polls == 0
    assert client.unloaded == ["a"]


def test_duplicate_stop_is_ignored_while_unload_is_in_flight(harness: Harness) -> None:
    controller, client = harness
    client.block_unloads = True

    controller.stop_models(["a"])
    controller.stop_models(["a", "b"])
    client.gate.set()

    assert wait_for(lambda: client.unloaded == ["a", "b"], 2000)
    assert wait_for(lambda: not controller._unloading, 2000)
    controller.stop_models(["a"])
    assert wait_for(lambda: client.unloaded == ["a", "b", "a"], 2000)


def test_stop_finishing_during_a_poll_queues_exactly_one_follow_up_poll(harness: Harness) -> None:
    controller, client = harness

    controller.stop_models(["a"])
    controller.stop_models(["b"])
    controller.refresh()

    assert wait_for(lambda: client.polls == 2, 2000)
    QTest.qWait(50)
    assert client.unloaded == ["a", "b"]
    assert client.polls == 2


def test_poll_in_flight_past_the_deadline_shows_not_responding(harness: Harness) -> None:
    controller, client = harness
    client.block_polls = True
    assert controller._watchdog.interval() == 3000
    controller._watchdog.setInterval(10)

    controller.refresh()

    assert wait_for(
        lambda: controller._tray.toolTip().endswith("Unreachable: not responding"), 2000
    )
    assert icon_color(controller) == RED
    client.models = [make_model("llama3:8b")]
    client.gate.set()
    assert wait_for(lambda: controller._tray.toolTip().endswith("(4.7 GB VRAM)"), 2000)
    assert not controller._watchdog.isActive()


def test_poll_queued_behind_a_slow_stop_is_not_reported_as_not_responding(
    harness: Harness,
) -> None:
    controller, client = harness
    client.block_unloads = True
    controller._watchdog.setInterval(10)
    controller.stop_models(["a"])
    assert client.unload_started.wait(2)

    controller.refresh()
    QTest.qWait(100)

    assert "not responding" not in controller._tray.toolTip()
    client.gate.set()
    assert wait_for(lambda: controller._tray.toolTip().endswith("No model loaded"), 2000)


def test_worker_keeps_running_after_an_unexpected_unload_error(harness: Harness) -> None:
    controller, client = harness
    client.unload_error = RuntimeError("boom")
    controller._tray.showMessage = MagicMock()

    controller.stop_models(["a"])

    assert wait_for(lambda: controller._tray.toolTip().endswith("No model loaded"), 2000)
    assert controller._tray.showMessage.call_args.args[1] == "a: boom"
    assert not controller._unloading


@mark.parametrize("repeats", [1, 3], ids=["single", "repeated"])
@patch.object(QSystemTrayIcon, "setIcon")
def test_icon_is_set_only_when_the_status_changes(
    set_icon: MagicMock, harness: Harness, repeats: int
) -> None:
    controller, _client = harness
    set_icon.reset_mock()

    for _ in range(repeats):
        controller.apply_result([make_model("llama3:8b")])
    controller.apply_result([])

    assert set_icon.call_count == 2


def test_menu_is_not_rebuilt_while_open_and_catches_up_when_it_closes(harness: Harness) -> None:
    controller, _client = harness
    menu = controller._menu

    menu.aboutToShow.emit()
    controller.apply_result([make_model("llama3:8b")])

    assert labels(menu) == ["Refresh now", "Quit"]
    menu.aboutToHide.emit()
    assert wait_for(lambda: labels(menu) == ["Stop llama3:8b", "Refresh now", "Quit"], 2000)


def test_stop_failure_message_is_escaped_and_capped(harness: Harness) -> None:
    controller, client = harness
    client.unload_error = RuntimeError(f"{RICH_TEXT_NAME}{'x' * 10_000}")
    controller._tray.showMessage = MagicMock()

    controller.stop_models(["a"])

    assert wait_for(lambda: controller._tray.showMessage.called, 2000)
    message = controller._tray.showMessage.call_args.args[1]
    assert message.startswith("a: &lt;a href=&quot;x&quot;&gt;click&lt;/a&gt;xxx")
    assert message.endswith("xxx...")
    assert "<" not in message


def test_reachability_changes_are_logged_once(harness: Harness, caplog: LogCaptureFixture) -> None:
    controller, _client = harness
    caplog.set_level(logging.INFO, logger="ollama_tray.app")

    controller.apply_result(OllamaUnavailable("down"))
    controller.apply_result(OllamaUnavailable("still down"))
    controller.apply_result([])
    controller.apply_result([make_model("llama3:8b")])

    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", "Server http://ollama.test:11434 is unreachable: down"),
        ("INFO", "Server http://ollama.test:11434 is reachable again"),
    ]


def test_failed_stop_is_logged(harness: Harness, caplog: LogCaptureFixture) -> None:
    controller, client = harness
    client.unload_error = RuntimeError("boom")
    controller._tray.showMessage = MagicMock()

    controller.stop_models(["a"])

    assert wait_for(lambda: controller._tray.showMessage.called, 2000)
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", "Could not stop a: boom")
    ]


@mark.parametrize(
    ("argv", "level"),
    [([], logging.WARNING), (["--verbose"], logging.INFO)],
    ids=["default", "verbose"],
)
@patch("ollama_tray.app.logging.basicConfig")
@patch("ollama_tray.app.TrayController")
@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
@patch("ollama_tray.app.QApplication")
def test_main_logs_to_stderr_at_the_requested_level(
    _app: MagicMock,
    _available: MagicMock,
    _controller: MagicMock,
    basic_config: MagicMock,
    monkeypatch: MonkeyPatch,
    argv: list[str],
    level: int,
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray", *argv])

    with raises(SystemExit):
        main()

    assert basic_config.call_args.kwargs["level"] == level
    assert basic_config.call_args.kwargs["stream"] is sys.stderr


def test_main_prints_the_package_version(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray", "--version"])

    with raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"ollama-tray {version('ollama-tray')}\n"
