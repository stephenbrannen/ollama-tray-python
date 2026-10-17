"""System tray icon that shows which models an Ollama server has loaded."""

import argparse
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ollama_tray.client import LoadedModel, OllamaClient, OllamaUnavailable, resolve_host
from ollama_tray.status import TrayStatus, status_from_result, tooltip_text

DEFAULT_INTERVAL_SECONDS = 5.0
ICON_SIZE_PIXELS = 64
STATUS_COLORS = {
    TrayStatus.LOADED: "#2ea043",
    TrayStatus.IDLE: "#8b949e",
    TrayStatus.UNREACHABLE: "#da3633",
}
SHUTDOWN_WAIT_MILLISECONDS = 3000


def draw_icon(status: TrayStatus) -> QIcon:
    """Paint a filled circle at runtime, so the repo ships no image files."""
    pixmap = QPixmap(ICON_SIZE_PIXELS, ICON_SIZE_PIXELS)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(STATUS_COLORS[status]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, ICON_SIZE_PIXELS - 8, ICON_SIZE_PIXELS - 8)
    painter.end()
    return QIcon(pixmap)


def populate_menu(
    menu: QMenu,
    models: list[LoadedModel],
    on_stop: Callable[[str], None],
    on_stop_all: Callable[[], None],
    on_refresh: Callable[[], None],
    on_quit: Callable[[], None],
) -> None:
    menu.clear()
    for model in models:
        stop_action = menu.addAction(f"Stop {model.name}")
        stop_action.triggered.connect(lambda _checked=False, name=model.name: on_stop(name))
    if len(models) > 1:
        menu.addAction("Stop all").triggered.connect(lambda _checked=False: on_stop_all())
    if models:
        menu.addSeparator()
    menu.addAction("Refresh now").triggered.connect(lambda _checked=False: on_refresh())
    menu.addAction("Quit").triggered.connect(lambda _checked=False: on_quit())


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    """Runs one client call on a pool thread and reports back through signals."""

    def __init__(self, call: Callable[[], object], signals: _TaskSignals) -> None:
        super().__init__()
        self._call = call
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._call()
        except OllamaUnavailable as error:
            self._signals.failed.emit(str(error))
        else:
            self._signals.succeeded.emit(result)


class TrayController(QObject):
    def __init__(self, client: OllamaClient, host: str, interval_seconds: float) -> None:
        super().__init__()
        self._client = client
        self._host = host
        self._models: list[LoadedModel] = []
        self._menu_names: tuple[str, ...] | None = None
        self._poll_in_flight = False

        # `requests.Session` is not guaranteed thread-safe, so one worker thread makes every
        # client call (poll and stop) run one at a time.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        # Signals are emitted on the worker thread. The receiving slots live on this object,
        # so Qt queues them onto the GUI thread, which is the only place widgets are touched.
        self._poll_signals = _TaskSignals(self)
        self._poll_signals.succeeded.connect(self._on_poll_succeeded)
        self._poll_signals.failed.connect(self._on_poll_failed)
        self._stop_signals = _TaskSignals(self)
        self._stop_signals.succeeded.connect(self._on_stop_finished)
        self._stop_signals.failed.connect(self._on_stop_failed)

        self._menu = QMenu()
        self._tray = QSystemTrayIcon()
        self._tray.setContextMenu(self._menu)
        self._timer = QTimer(self)
        self._timer.setInterval(round(interval_seconds * 1000))
        self._timer.timeout.connect(self.refresh)
        self.apply_result(None)

    def start(self) -> None:
        self._tray.show()
        self.refresh()
        self._timer.start()

    def shutdown(self) -> None:
        self._timer.stop()
        self._pool.waitForDone(SHUTDOWN_WAIT_MILLISECONDS)
        self._tray.hide()

    @Slot()
    def refresh(self) -> None:
        # A server that answers slower than the interval would otherwise pile up queued polls.
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        self._pool.start(_Task(self._client.loaded_models, self._poll_signals))

    def stop_models(self, names: list[str]) -> None:
        def unload_all() -> None:
            failures = []
            for name in names:
                try:
                    self._client.unload(name)
                except OllamaUnavailable as error:
                    failures.append(f"{name}: {error}")
            if failures:
                raise OllamaUnavailable("\n".join(failures))

        self._pool.start(_Task(unload_all, self._stop_signals))

    def apply_result(self, result: list[LoadedModel] | OllamaUnavailable | None) -> None:
        if result is None:
            self._tray.setIcon(draw_icon(TrayStatus.UNREACHABLE))
            self._tray.setToolTip(tooltip_text(self._host, None))
            self._rebuild_menu_if_changed([])
            return
        self._models = [] if isinstance(result, OllamaUnavailable) else result
        self._tray.setIcon(draw_icon(status_from_result(result)))
        self._tray.setToolTip(tooltip_text(self._host, result))
        self._rebuild_menu_if_changed(self._models)

    def _rebuild_menu_if_changed(self, models: list[LoadedModel]) -> None:
        # Rebuilding while the menu is open would make it flicker on every poll.
        names = tuple(model.name for model in models)
        if names == self._menu_names:
            return
        self._menu_names = names
        populate_menu(
            self._menu,
            models,
            on_stop=lambda name: self.stop_models([name]),
            on_stop_all=lambda: self.stop_models([model.name for model in self._models]),
            on_refresh=self.refresh,
            on_quit=QApplication.quit,
        )

    @Slot(object)
    def _on_poll_succeeded(self, models: list[LoadedModel]) -> None:
        self._poll_in_flight = False
        self.apply_result(models)

    @Slot(str)
    def _on_poll_failed(self, message: str) -> None:
        self._poll_in_flight = False
        self.apply_result(OllamaUnavailable(message))

    @Slot(object)
    def _on_stop_finished(self, _result: None) -> None:
        self.refresh()

    @Slot(str)
    def _on_stop_failed(self, message: str) -> None:
        self._tray.showMessage("Could not stop model", message, QSystemTrayIcon.MessageIcon.Warning)
        self.refresh()


def positive_seconds(raw: str) -> float:
    try:
        seconds = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return seconds


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ollama-tray", description="Show which models an Ollama server has loaded."
    )
    parser.add_argument("--host", help="server address; defaults to OLLAMA_HOST, then localhost")
    parser.add_argument(
        "--interval",
        type=positive_seconds,
        default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between status checks (default: %(default)s)",
    )
    args = parser.parse_args()
    try:
        host = resolve_host(args.host)
    except ValueError as error:
        parser.error(f"invalid server address: {error}")

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        raise SystemExit(
            "No system tray found. On GNOME, install and enable the AppIndicator extension."
        )

    controller = TrayController(OllamaClient(host), host, args.interval)
    app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    sys.exit(app.exec())
