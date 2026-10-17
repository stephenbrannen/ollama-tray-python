import logging
import signal
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from threading import Event
from typing import NamedTuple
from unittest.mock import MagicMock, call, patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from pytest import CaptureFixture, LogCaptureFixture, MonkeyPatch, fixture, mark, raises
from responses import RequestsMock

from ollama_tray.app import (
    MenuActions,
    TrayController,
    draw_icon,
    interval_seconds,
    main,
    populate_menu,
    quit_on_signals,
)
from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable
from ollama_tray.status import TrayStatus
from tests.fixtures import HOST, make_model

GREY = "#8b949e"
RED = "#da3633"
# Ollama reports a keep-alive of -1 as a time about 300 years away.
KEPT_FOREVER = datetime(2319, 1, 1, tzinfo=UTC)
RICH_TEXT_NAME = '<a href="x">click</a>'
WAIT_MILLISECONDS = 2000


def icon_color(tray: QSystemTrayIcon) -> str:
    return tray.icon().pixmap(64, 64).toImage().pixelColor(32, 32).name()


@fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    yield app


def wait_for(condition: Callable[[], bool]) -> bool:
    """Pump the event loop until the worker's queued signals have been delivered."""
    deadline = time.monotonic() + WAIT_MILLISECONDS / 1000
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(10)
    return condition()


def labels(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def keep_labels(menu: QMenu) -> list[str]:
    """Return the Keep loaded submenu's labels, or `[]` when the menu has none."""
    for action in menu.actions():
        submenu = action.menu()
        if action.text() == "Keep loaded" and isinstance(submenu, QMenu):
            return labels(submenu)
    return []


class ActionMocks(NamedTuple):
    on_stop: MagicMock
    on_keep_loaded: MagicMock
    on_stop_all: MagicMock
    on_refresh: MagicMock
    on_quit: MagicMock

    @property
    def actions(self) -> MenuActions:
        return MenuActions(*self)


@fixture
def menu_actions() -> ActionMocks:
    return ActionMocks(*(MagicMock() for _ in range(5)))


class Built(NamedTuple):
    controller: TrayController
    tray: QSystemTrayIcon
    menu: QMenu


MakeController = Callable[[OllamaClient, float], Built]


@fixture
def make_controller(qapp: QApplication) -> Iterator[MakeController]:
    controllers: list[TrayController] = []

    def make(client: OllamaClient, interval_seconds: float) -> Built:
        tray, menu = QSystemTrayIcon(), QMenu()
        controller = TrayController(client, HOST, interval_seconds, tray, menu)
        controllers.append(controller)
        return Built(controller, tray, menu)

    yield make
    for controller in controllers:
        controller.shutdown()
        controller.worker_thread.join(2)


@mark.parametrize(
    ("names", "keepable", "expected", "expected_keep"),
    [
        ([], [], ["Refresh now", "Quit"], []),
        (["llama3:8b"], [], ["Stop llama3:8b", "Refresh now", "Quit"], []),
        (
            ["llama3:8b"],
            ["llama3:8b"],
            ["Stop llama3:8b", "Keep loaded", "Refresh now", "Quit"],
            ["llama3:8b"],
        ),
        (
            ["llama3:8b", "qwen3:4b"],
            ["qwen3:4b"],
            ["Stop llama3:8b", "Stop qwen3:4b", "Stop all", "Keep loaded", "Refresh now", "Quit"],
            ["qwen3:4b"],
        ),
        (
            ["q&a"],
            ["q&a"],
            ["Stop q&&a", "Keep loaded", "Refresh now", "Quit"],
            ["q&&a"],
        ),
        (
            ["m" * 300],
            ["m" * 300],
            [f"Stop {'m' * 63}…", "Keep loaded", "Refresh now", "Quit"],
            [f"{'m' * 63}…"],
        ),
        (
            ["a\nb\x07c", "r&d\u202e:8b"],
            ["a\nb\x07c", "r&d\u202e:8b"],
            ["Stop a b c", "Stop r&&d :8b", "Stop all", "Keep loaded", "Refresh now", "Quit"],
            ["a b c", "r&&d :8b"],
        ),
    ],
    ids=[
        "none-loaded",
        "all-kept-loaded",
        "one-keepable",
        "one-of-two-keepable",
        "ampersand-doubled",
        "long-name-capped",
        "control-and-format-characters",
    ],
)
def test_populate_menu_lists_stop_actions_and_keep_loaded_submenu(
    qapp: QApplication,
    names: list[str],
    keepable: list[str],
    expected: list[str],
    expected_keep: list[str],
    menu_actions: ActionMocks,
) -> None:
    menu = QMenu()

    populate_menu(menu, [make_model(name) for name in names], keepable, menu_actions.actions)

    assert labels(menu) == expected
    assert keep_labels(menu) == expected_keep


def test_populate_menu_rebuild_replaces_the_keep_loaded_submenu(
    qapp: QApplication, menu_actions: ActionMocks
) -> None:
    menu = QMenu()
    models = [make_model("a")]
    populate_menu(menu, models, ["a"], menu_actions.actions)

    populate_menu(menu, models, ["a"], menu_actions.actions)

    def direct_menus() -> int:
        return len(menu.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly))

    assert wait_for(lambda: direct_menus() == 1)
    assert keep_labels(menu) == ["a"]


def test_populate_menu_actions_call_their_callbacks(
    qapp: QApplication, menu_actions: ActionMocks
) -> None:
    menu = QMenu()
    populate_menu(menu, [make_model("llama3:8b"), make_model("q&a")], ["q&a"], menu_actions.actions)

    for action in menu.actions():
        action.trigger()
    (keep_menu,) = [m for a in menu.actions() if isinstance(m := a.menu(), QMenu)]
    keep_menu.actions()[0].trigger()

    menu_actions.on_keep_loaded.assert_called_once_with("q&a")
    menu_actions.on_stop.assert_any_call("llama3:8b")
    menu_actions.on_stop.assert_any_call("q&a")
    menu_actions.on_stop_all.assert_called_once_with()
    menu_actions.on_refresh.assert_called_once_with()
    menu_actions.on_quit.assert_called_once_with()


def test_apply_result_updates_tooltip_and_menu(make_controller: MakeController) -> None:
    controller, tray, menu = make_controller(OllamaClient(HOST), 5)

    assert tray.toolTip() == f"{HOST}\nChecking..."
    assert icon_color(tray) == GREY

    controller.apply_result([make_model("llama3:8b")])

    assert tray.toolTip() == f"{HOST}\nllama3:8b (4.7 GB VRAM)"
    assert labels(menu) == ["Stop llama3:8b", "Keep loaded", "Refresh now", "Quit"]

    controller.apply_result(OllamaUnavailable("down"))

    assert tray.toolTip() == f"{HOST}\nUnreachable: down"
    assert icon_color(tray) == RED
    assert labels(menu) == ["Refresh now", "Quit"]


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


def test_refresh_applies_worker_result_on_gui_thread(
    make_controller: MakeController, responses: RequestsMock
) -> None:
    responses.get(
        f"{HOST}/api/ps",
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
    controller, tray, menu = make_controller(OllamaClient(HOST), 5)

    controller.refresh()
    controller.refresh()
    expected = f"{HOST}\nllama3:8b (4.7 GB VRAM, unloads in <1m)"

    assert wait_for(lambda: tray.toolTip() == expected)
    assert len(responses.calls) == 1
    assert labels(menu) == ["Stop llama3:8b", "Keep loaded", "Refresh now", "Quit"]


@patch.object(OllamaClient, "loaded_models", side_effect=[RuntimeError("bug"), []])
def test_poll_recovers_after_unexpected_error(
    loaded_models: MagicMock, make_controller: MakeController
) -> None:
    controller, tray, _menu = make_controller(OllamaClient(HOST), 5)

    controller.refresh()
    assert wait_for(lambda: tray.toolTip().endswith("Unreachable: bug"))
    controller.refresh()

    assert wait_for(lambda: tray.toolTip().endswith("No model loaded"))
    assert loaded_models.call_count == 2


@patch.object(QSystemTrayIcon, "showMessage")
def test_stop_failure_is_reported_and_status_refreshed(
    show_message: MagicMock, make_controller: MakeController, responses: RequestsMock
) -> None:
    responses.post(f"{HOST}/api/generate", status=500)
    responses.get(f"{HOST}/api/ps", json={"models": []})
    controller, tray, _menu = make_controller(OllamaClient(HOST), 5)

    controller.stop_models(["llama3:8b"])

    assert wait_for(lambda: tray.toolTip().endswith("No model loaded"))
    show_message.assert_called_once()


@mark.parametrize(
    "argv",
    [
        ["--host", "gpu-box:notaport"],
        ["--host", "ftp://gpu-box"],
        ["--interval", "0"],
        ["--interval", "0.99"],
        ["--interval", "-1"],
        ["--interval", "soon"],
        ["--interval", "nan"],
        ["--interval", "inf"],
    ],
    ids=[
        "non-numeric-port",
        "ftp-scheme",
        "zero-interval",
        "below-minimum-interval",
        "negative-interval",
        "non-numeric-interval",
        "nan-interval",
        "infinite-interval",
    ],
)
# If a bad argument were accepted, main would go on to start the app. These patches make that
# fail the assertions below instead of creating a second `QApplication`.
@patch("ollama_tray.app.quit_on_signals")
@patch("ollama_tray.app.TrayController")
@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
@patch("ollama_tray.app.QApplication")
def test_main_reports_bad_arguments_as_cli_error(
    _app: MagicMock,
    _available: MagicMock,
    _controller: MagicMock,
    _quit_on_signals: MagicMock,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    argv: list[str],
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


@patch("ollama_tray.app.quit_on_signals")
@patch("ollama_tray.app.TrayController")
@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
@patch("ollama_tray.app.QApplication")
def test_main_starts_controller_and_exits_with_event_loop_code(
    app: MagicMock,
    _available: MagicMock,
    controller_class: MagicMock,
    quit_on_signals_mock: MagicMock,
    monkeypatch: MonkeyPatch,
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
    quit_on_signals_mock.assert_called_once_with(app.return_value)
    controller.start.assert_called_once_with()


@patch.object(QSystemTrayIcon, "showMessage")
def test_stop_all_continues_past_a_failure_and_reports_each(
    show_message: MagicMock, make_controller: MakeController, responses: RequestsMock
) -> None:
    responses.post(f"{HOST}/api/generate", status=500)
    responses.post(f"{HOST}/api/generate", status=200)
    responses.get(f"{HOST}/api/ps", json={"models": []})
    controller, _tray, _menu = make_controller(OllamaClient(HOST), 5)

    controller.stop_models(["a", "b"])

    assert wait_for(lambda: len(responses.calls) == 3)
    assert wait_for(lambda: show_message.called)
    message = show_message.call_args.args[1]
    assert message.startswith("a: ")
    assert "b:" not in message


class StubClient(OllamaClient):
    """Answers instantly, or blocks on `gate` so a test controls when a call returns."""

    def __init__(self) -> None:
        super().__init__(HOST)
        self.gate = Event()
        self.block_polls = False
        self.block_unloads = False
        self.unload_error: Exception | None = None
        self.keep_error: Exception | None = None
        self.block_keeps = False
        self.keep_started = Event()
        self.kept: list[str] = []
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

    def keep_loaded(self, name: str) -> None:
        self.keep_started.set()
        if self.keep_error:
            raise self.keep_error
        if self.block_keeps:
            self.gate.wait()
        self.kept.append(name)


class Harness(NamedTuple):
    controller: TrayController
    client: StubClient
    tray: QSystemTrayIcon
    menu: QMenu


@fixture
def harness(make_controller: MakeController) -> Iterator[Harness]:
    client = StubClient()
    controller, tray, menu = make_controller(client, 0.01)
    yield Harness(controller, client, tray, menu)
    client.gate.set()


# A test that lists this before `harness` builds its controller with an instant watchdog.
@fixture
def instant_watchdog(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("ollama_tray.app.STATUS_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr("ollama_tray.app.POLL_WATCHDOG_MARGIN_SECONDS", 0)


@patch.object(QTimer, "setInterval")
def test_watchdog_waits_for_the_client_deadline_plus_a_margin(
    set_interval: MagicMock, make_controller: MakeController
) -> None:
    make_controller(StubClient(), 5)

    assert call(3000) in set_interval.call_args_list


@patch.object(QSystemTrayIcon, "show")
def test_start_shows_tray_polls_and_keeps_polling_on_the_timer(
    show: MagicMock, harness: Harness
) -> None:
    controller, client, tray, _menu = harness

    controller.start()

    show.assert_called_once_with()
    assert wait_for(lambda: client.polls >= 3)
    assert tray.toolTip().endswith("No model loaded")


@patch.object(QTimer, "stop")
@patch.object(QSystemTrayIcon, "hide")
def test_shutdown_returns_promptly_while_stop_all_is_hung(
    hide: MagicMock, timer_stop: MagicMock, harness: Harness
) -> None:
    controller, client, _tray, _menu = harness
    client.block_unloads = True
    controller.stop_models(["a", "b"])
    assert client.unload_started.wait(2)
    controller.start()

    started = time.monotonic()
    controller.shutdown()
    elapsed = time.monotonic() - started
    client.gate.set()
    controller.worker_thread.join(2)

    assert elapsed < 1
    assert controller.worker_thread.daemon
    assert client.unloaded == ["a"]
    assert timer_stop.call_count == 2
    hide.assert_called_once_with()


def test_shutdown_drops_queued_polls_and_refuses_new_work(harness: Harness) -> None:
    controller, client, _tray, _menu = harness
    client.block_unloads = True
    controller.stop_models(["a"])
    assert client.unload_started.wait(2)
    controller.refresh()

    controller.shutdown()
    controller.refresh()
    controller.stop_models(["b"])
    client.gate.set()
    controller.worker_thread.join(2)

    assert client.polls == 0
    assert client.unloaded == ["a"]


def test_duplicate_stop_is_ignored_while_unload_is_in_flight(harness: Harness) -> None:
    controller, client, _tray, _menu = harness
    client.block_unloads = True

    controller.stop_models(["a"])
    controller.stop_models(["a", "b"])
    client.gate.set()

    assert wait_for(lambda: client.unloaded == ["a", "b"])
    # The follow-up poll runs only after the controller has released the stopped names.
    assert wait_for(lambda: client.polls >= 1)
    controller.stop_models(["a"])
    assert wait_for(lambda: client.unloaded == ["a", "b", "a"])


def test_stop_finishing_during_a_poll_queues_exactly_one_follow_up_poll(harness: Harness) -> None:
    controller, client, _tray, _menu = harness

    controller.stop_models(["a"])
    controller.stop_models(["b"])
    controller.refresh()

    assert wait_for(lambda: client.polls == 2)
    QTest.qWait(50)
    assert client.unloaded == ["a", "b"]
    assert client.polls == 2


def test_poll_in_flight_past_the_deadline_shows_not_responding(
    instant_watchdog: None, harness: Harness
) -> None:
    controller, client, tray, _menu = harness
    client.block_polls = True

    controller.refresh()

    assert wait_for(lambda: tray.toolTip().endswith("Unreachable: not responding"))
    assert icon_color(tray) == RED
    client.models = [make_model("llama3:8b")]
    client.gate.set()
    assert wait_for(lambda: tray.toolTip().endswith("(4.7 GB VRAM)"))


def test_poll_queued_behind_a_slow_stop_is_not_reported_as_not_responding(
    instant_watchdog: None, harness: Harness
) -> None:
    controller, client, tray, _menu = harness
    client.block_unloads = True
    controller.stop_models(["a"])
    assert client.unload_started.wait(2)

    controller.refresh()
    QTest.qWait(100)

    assert "not responding" not in tray.toolTip()
    client.gate.set()
    assert wait_for(lambda: tray.toolTip().endswith("No model loaded"))


@patch.object(QSystemTrayIcon, "showMessage")
def test_worker_keeps_running_after_an_unexpected_unload_error(
    show_message: MagicMock, harness: Harness
) -> None:
    controller, client, tray, _menu = harness
    client.unload_error = RuntimeError("boom")

    controller.stop_models(["a"])

    assert wait_for(lambda: tray.toolTip().endswith("No model loaded"))
    assert show_message.call_args.args[1] == "a: boom"
    controller.stop_models(["a"])
    assert wait_for(lambda: show_message.call_count == 2)


@mark.parametrize("repeats", [1, 3], ids=["single", "repeated"])
@patch.object(QSystemTrayIcon, "setIcon")
def test_icon_is_set_only_when_the_status_changes(
    set_icon: MagicMock, harness: Harness, repeats: int
) -> None:
    controller = harness.controller
    set_icon.reset_mock()

    for _ in range(repeats):
        controller.apply_result([make_model("llama3:8b")])
    controller.apply_result([])

    assert set_icon.call_count == 2


def test_menu_is_not_rebuilt_while_open_and_catches_up_when_it_closes(harness: Harness) -> None:
    controller, _client, _tray, menu = harness

    menu.aboutToShow.emit()
    controller.apply_result([make_model("llama3:8b")])

    assert labels(menu) == ["Refresh now", "Quit"]
    menu.aboutToHide.emit()
    assert wait_for(
        lambda: labels(menu) == ["Stop llama3:8b", "Keep loaded", "Refresh now", "Quit"]
    )


@patch.object(QSystemTrayIcon, "showMessage")
def test_stop_failure_message_is_escaped_and_capped(
    show_message: MagicMock, harness: Harness
) -> None:
    controller, client, _tray, _menu = harness
    client.unload_error = RuntimeError(f"{RICH_TEXT_NAME}{'x' * 10_000}")

    controller.stop_models(["a"])

    assert wait_for(lambda: show_message.called)
    message = show_message.call_args.args[1]
    assert message.startswith("a: &lt;a href=&quot;x&quot;&gt;click&lt;/a&gt;xxx")
    assert message.endswith("xxx…")
    assert len(message) < 400
    assert "<" not in message


def test_keep_loaded_runs_on_the_worker_and_is_followed_by_a_poll(harness: Harness) -> None:
    controller, client, _tray, _menu = harness

    controller.keep_loaded("a")

    assert wait_for(lambda: client.kept == ["a"])
    assert wait_for(lambda: client.polls >= 1)


def test_duplicate_keep_loaded_is_ignored_while_in_flight(harness: Harness) -> None:
    controller, client, _tray, _menu = harness
    client.block_keeps = True

    controller.keep_loaded("a")
    controller.keep_loaded("a")
    controller.keep_loaded("b")
    client.gate.set()

    assert wait_for(lambda: client.kept == ["a", "b"])
    assert wait_for(lambda: client.polls >= 1)
    controller.keep_loaded("a")
    assert wait_for(lambda: client.kept == ["a", "b", "a"])


@patch.object(QSystemTrayIcon, "showMessage")
def test_keep_loaded_failure_is_reported_escaped_and_logged(
    show_message: MagicMock, harness: Harness, caplog: LogCaptureFixture
) -> None:
    controller, client, tray, _menu = harness
    client.keep_error = RuntimeError(f"{RICH_TEXT_NAME}{'x' * 10_000}")

    controller.keep_loaded("a")

    assert wait_for(lambda: show_message.called)
    title, message = show_message.call_args.args[:2]
    assert title == "Could not keep model loaded"
    assert message.startswith("a: &lt;a href=&quot;x&quot;&gt;click&lt;/a&gt;xxx")
    assert "<" not in message
    assert wait_for(lambda: tray.toolTip().endswith("No model loaded"))
    assert caplog.records[0].getMessage().startswith("Could not keep a loaded: <a href")
    controller.keep_loaded("a")
    assert wait_for(lambda: show_message.call_count == 2)


def test_shutdown_refuses_keep_loaded_and_drops_a_queued_one(harness: Harness) -> None:
    controller, client, _tray, _menu = harness
    client.block_unloads = True
    controller.stop_models(["a"])
    assert client.unload_started.wait(2)
    controller.keep_loaded("b")

    controller.shutdown()
    controller.keep_loaded("c")
    client.gate.set()
    controller.worker_thread.join(2)

    assert client.kept == []


def test_keep_loaded_entry_disappears_once_the_model_is_kept(harness: Harness) -> None:
    controller, _client, _tray, menu = harness
    controller.apply_result([make_model("a"), make_model("b")])
    assert keep_labels(menu) == ["a", "b"]

    controller.apply_result([make_model("a", expires_at=KEPT_FOREVER), make_model("b")])

    assert keep_labels(menu) == ["b"]
    controller.apply_result(
        [make_model("a", expires_at=KEPT_FOREVER), make_model("b", expires_at=KEPT_FOREVER)]
    )
    assert labels(menu) == ["Stop a", "Stop b", "Stop all", "Refresh now", "Quit"]


def test_reachability_changes_are_logged_once(harness: Harness, caplog: LogCaptureFixture) -> None:
    controller = harness.controller
    caplog.set_level(logging.INFO, logger="ollama_tray.app")

    controller.apply_result(OllamaUnavailable("down"))
    controller.apply_result(OllamaUnavailable("still down"))
    controller.apply_result([])
    controller.apply_result([make_model("llama3:8b")])

    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", f"Server {HOST} is unreachable: down"),
        ("INFO", f"Server {HOST} is reachable again"),
    ]


@patch.object(QSystemTrayIcon, "showMessage")
def test_failed_stop_is_logged(
    show_message: MagicMock, harness: Harness, caplog: LogCaptureFixture
) -> None:
    controller, client, _tray, _menu = harness
    client.unload_error = RuntimeError("boom")

    controller.stop_models(["a"])

    assert wait_for(lambda: show_message.called)
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("WARNING", "Could not stop a: boom")
    ]


@mark.parametrize(
    ("argv", "level"),
    [([], logging.WARNING), (["--verbose"], logging.INFO)],
    ids=["default", "verbose"],
)
@patch("ollama_tray.app.logging.basicConfig")
@patch("ollama_tray.app.quit_on_signals")
@patch("ollama_tray.app.TrayController")
@patch("ollama_tray.app.QSystemTrayIcon.isSystemTrayAvailable", return_value=True)
@patch("ollama_tray.app.QApplication")
def test_main_logs_to_stderr_at_the_requested_level(
    _app: MagicMock,
    _available: MagicMock,
    _controller: MagicMock,
    _quit_on_signals: MagicMock,
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


@mark.parametrize(
    ("raw", "expected"),
    [("1", 1.0), ("2.5", 2.5)],
    ids=["minimum", "fractional"],
)
def test_interval_seconds_accepts_values_from_the_minimum(raw: str, expected: float) -> None:
    assert interval_seconds(raw) == expected


@patch("ollama_tray.app.build_version", return_value="abc1234")
def test_main_prints_the_build_version(
    _build_version: MagicMock, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["ollama-tray", "--version"])

    with raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "ollama-tray abc1234\n"


@fixture
def restore_signal_handlers() -> Iterator[None]:
    saved = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    yield
    for number, handler in saved.items():
        signal.signal(number, handler)


@mark.parametrize("signal_number", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
@mark.usefixtures("restore_signal_handlers")
@patch.object(QApplication, "quit")
def test_quit_on_signals_quits_the_app_and_wakes_the_event_loop(
    quit_app: MagicMock, qapp: QApplication, signal_number: signal.Signals
) -> None:
    timers_before = len(qapp.findChildren(QTimer))

    quit_on_signals(qapp)
    signal.raise_signal(signal_number)

    quit_app.assert_called_once_with()
    timer = qapp.findChildren(QTimer)[timers_before]
    assert timer.isActive()
    assert timer.interval() == 200
    timer.deleteLater()
