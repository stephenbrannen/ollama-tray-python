"""System tray icon that shows which models an Ollama server has loaded."""

import argparse
import logging
import math
import queue
import sys
import threading
from collections.abc import Callable
from importlib.metadata import version

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ollama_tray.client import (
    STATUS_TIMEOUT_SECONDS,
    LoadedModel,
    OllamaClient,
    OllamaUnavailable,
    resolve_host,
)
from ollama_tray.status import (
    TrayStatus,
    escape_lines,
    shorten,
    status_from_result,
    tooltip_text,
)

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 5.0
ICON_SIZE_PIXELS = 64
# Space between the circle and the pixmap edge, so the antialiased rim is not clipped.
ICON_MARGIN_PIXELS = 4
GREY = "#8b949e"
STATUS_COLORS = {
    TrayStatus.CHECKING: GREY,
    TrayStatus.LOADED: "#2ea043",
    TrayStatus.IDLE: GREY,
    TrayStatus.UNREACHABLE: "#da3633",
}
# The client deadline is checked between reads, so a poll can overrun it by one socket wait.
# Past this point the GUI stops waiting and shows the server as not responding.
POLL_WATCHDOG_MARGIN_SECONDS = 1
NOT_RESPONDING = "not responding"


def draw_icon(status: TrayStatus) -> QIcon:
    """Paint a filled circle at runtime, so the repo ships no image files."""
    pixmap = QPixmap(ICON_SIZE_PIXELS, ICON_SIZE_PIXELS)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(STATUS_COLORS[status]))
    painter.setPen(Qt.PenStyle.NoPen)
    diameter = ICON_SIZE_PIXELS - 2 * ICON_MARGIN_PIXELS
    painter.drawEllipse(ICON_MARGIN_PIXELS, ICON_MARGIN_PIXELS, diameter, diameter)
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
        # `name=model.name` binds this model now. A plain closure would read the loop variable
        # later and stop the last model every time. `_checked` takes the bool that Qt sends.
        stop_action.triggered.connect(lambda _checked=False, name=model.name: on_stop(name))
    if len(models) > 1:
        menu.addAction("Stop all").triggered.connect(lambda _checked=False: on_stop_all())
    if models:
        menu.addSeparator()
    menu.addAction("Refresh now").triggered.connect(lambda _checked=False: on_refresh())
    menu.addAction("Quit").triggered.connect(lambda _checked=False: on_quit())


class _PollSignals(QObject):
    started = Signal()
    succeeded = Signal(list)
    failed = Signal(str)


class _StopSignals(QObject):
    failed = Signal(str)
    finished = Signal(list)


class TrayController(QObject):
    def __init__(self, client: OllamaClient, host: str, interval_seconds: float) -> None:
        super().__init__()
        self._client = client
        self._host = host
        self._models: list[LoadedModel] = []
        self._menu_names: tuple[str, ...] | None = None
        self._menu_open = False
        self._menu_rebuild_pending = False
        self._poll_in_flight = False
        self._poll_again = False
        self._unloading: set[str] = set()
        # The worker reads this between models, so it is a thread-safe event, not a plain bool.
        self._shutting_down = threading.Event()
        self._status: TrayStatus | None = None
        # Only changes of this flag are logged, so a server that stays down logs once, not
        # on every poll.
        self._unreachable = False
        # A `QPixmap` needs the application to exist, so the icons are drawn here, not at import.
        self._icons = {status: draw_icon(status) for status in TrayStatus}

        # The client's `requests.Session` is not guaranteed thread-safe, so one worker thread
        # makes every client call (poll and stop) run one at a time.
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        # A daemon thread cannot hold the process open while it waits on a hung server. Qt's
        # `QThreadPool` joins its threads on exit, which delayed quitting by 10 s.
        self._worker = threading.Thread(target=self._work, name="ollama-client", daemon=True)
        self._worker.start()

        # Signals are emitted on the worker thread. The receiving slots live on this object,
        # so Qt queues them onto the GUI thread, which is the only place widgets are touched.
        self._poll_signals = _PollSignals(self)
        self._poll_signals.started.connect(self._on_poll_started)
        self._poll_signals.succeeded.connect(self._on_poll_succeeded)
        self._poll_signals.failed.connect(self._on_poll_failed)
        self._stop_signals = _StopSignals(self)
        self._stop_signals.failed.connect(self._on_stop_failed)
        self._stop_signals.finished.connect(self._on_stop_finished)

        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._on_menu_shown)
        self._menu.aboutToHide.connect(self._on_menu_hidden)
        self._tray = QSystemTrayIcon()
        self._tray.setContextMenu(self._menu)
        self._timer = QTimer(self)
        self._timer.setInterval(round(interval_seconds * 1000))
        self._timer.timeout.connect(self.refresh)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval((STATUS_TIMEOUT_SECONDS + POLL_WATCHDOG_MARGIN_SECONDS) * 1000)
        self._watchdog.timeout.connect(self._on_poll_stalled)
        self.apply_result(None)

    def start(self) -> None:
        self._tray.show()
        self.refresh()
        self._timer.start()

    def shutdown(self) -> None:
        """Stop all work without waiting on the server.

        A call that is already in flight ends on its own deadline, and the worker thread is
        not joined here, so a hung server cannot delay quitting.
        """
        self._shutting_down.set()
        self._timer.stop()
        self._watchdog.stop()
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        self._jobs.put(None)
        self._tray.hide()

    def _work(self) -> None:
        while (job := self._jobs.get()) is not None:
            # A job that raises must not end this thread, or every later poll and stop would
            # wait in the queue forever and the icon would freeze.
            try:
                job()
            except Exception:
                logger.exception("Worker job failed")

    @Slot()
    def refresh(self) -> None:
        # A server that answers slower than the interval would otherwise pile up queued polls.
        if self._poll_in_flight or self._shutting_down.is_set():
            return
        self._poll_in_flight = True
        self._jobs.put(self._poll)

    def _poll(self) -> None:
        # The watchdog times the call, not the queue wait. A poll can queue behind a stop that
        # takes several unload timeouts, and a healthy server must not show as not responding.
        self._poll_signals.started.emit()
        try:
            models = self._client.loaded_models()
        except Exception as error:
            # Catch everything, not only `OllamaUnavailable`. A failure that emits no signal
            # leaves the controller's in-flight flag set, and every later poll is then skipped.
            self._poll_signals.failed.emit(str(error))
        else:
            self._poll_signals.succeeded.emit(models)

    def stop_models(self, names: list[str]) -> None:
        # A second click on the same model would queue another unload behind the first.
        new_names = [name for name in names if name not in self._unloading]
        if not new_names or self._shutting_down.is_set():
            return
        self._unloading.update(new_names)
        self._jobs.put(lambda: self._unload_all(new_names))

    def _unload_all(self, names: list[str]) -> None:
        failures = []
        try:
            for name in names:
                # Each unload can wait out its timeout, so quitting must be noticed between models.
                if self._shutting_down.is_set():
                    return
                try:
                    self._client.unload(name)
                # Catch everything, not only `OllamaUnavailable`, so one model's failure does
                # not skip the models after it or end the worker thread.
                except Exception as error:
                    logger.warning("Could not stop %s: %s", shorten(name), shorten(str(error)))
                    failures.append(f"{name}: {error}")
        finally:
            # The names must always be released, or those models could never be stopped again.
            if failures:
                self._stop_signals.failed.emit("\n".join(failures))
            self._stop_signals.finished.emit(names)

    def apply_result(self, result: list[LoadedModel] | OllamaUnavailable | None) -> None:
        """Show a poll result. `None` means the first poll has not returned yet."""
        if result is None:
            self._show_status(TrayStatus.CHECKING)
            self._tray.setToolTip(tooltip_text(self._host, None))
            self._rebuild_menu_if_changed([])
            return
        self._log_reachability_change(result)
        self._models = [] if isinstance(result, OllamaUnavailable) else result
        self._show_status(status_from_result(result))
        self._tray.setToolTip(tooltip_text(self._host, result))
        self._rebuild_menu_if_changed(self._models)

    def _log_reachability_change(self, result: list[LoadedModel] | OllamaUnavailable) -> None:
        unreachable = isinstance(result, OllamaUnavailable)
        if unreachable == self._unreachable:
            return
        self._unreachable = unreachable
        if isinstance(result, OllamaUnavailable):
            logger.warning("Server %s is unreachable: %s", self._host, shorten(str(result)))
        else:
            logger.info("Server %s is reachable again", self._host)

    def _show_status(self, status: TrayStatus) -> None:
        if status == self._status:
            return
        self._status = status
        self._tray.setIcon(self._icons[status])

    def _rebuild_menu_if_changed(self, models: list[LoadedModel]) -> None:
        names = tuple(model.name for model in models)
        if names == self._menu_names:
            self._menu_rebuild_pending = False
            return
        # Rebuilding while the menu is open would make it flicker, and would delete the action
        # under the user's pointer.
        if self._menu_open:
            self._menu_rebuild_pending = True
            return
        self._menu_rebuild_pending = False
        self._menu_names = names
        populate_menu(
            self._menu,
            models,
            on_stop=lambda name: self.stop_models([name]),
            on_stop_all=lambda: self.stop_models([model.name for model in self._models]),
            on_refresh=self.refresh,
            on_quit=QApplication.quit,
        )

    @Slot()
    def _on_menu_shown(self) -> None:
        self._menu_open = True

    @Slot()
    def _on_menu_hidden(self) -> None:
        self._menu_open = False
        if self._menu_rebuild_pending:
            # `aboutToHide` fires before the chosen action runs, and a rebuild now would delete
            # that action first. A zero-delay timer runs the rebuild after the action.
            QTimer.singleShot(0, lambda: self._rebuild_menu_if_changed(self._models))

    @Slot()
    def _on_poll_started(self) -> None:
        self._watchdog.start()

    @Slot()
    def _on_poll_stalled(self) -> None:
        self.apply_result(OllamaUnavailable(NOT_RESPONDING))

    def _finish_poll(self) -> None:
        self._poll_in_flight = False
        self._watchdog.stop()

    @Slot(list)
    def _on_poll_succeeded(self, models: list[LoadedModel]) -> None:
        self._finish_poll()
        self.apply_result(models)
        self._run_queued_poll()

    @Slot(str)
    def _on_poll_failed(self, message: str) -> None:
        self._finish_poll()
        self.apply_result(OllamaUnavailable(message))
        self._run_queued_poll()

    def _run_queued_poll(self) -> None:
        if self._poll_again:
            self._poll_again = False
            self.refresh()

    @Slot(str)
    def _on_stop_failed(self, message: str) -> None:
        self._tray.showMessage(
            "Could not stop model", escape_lines(message), QSystemTrayIcon.MessageIcon.Warning
        )

    @Slot(list)
    def _on_stop_finished(self, names: list[str]) -> None:
        self._unloading.difference_update(names)
        # A poll that started before the unload finished can still report the model as loaded,
        # so a poll in flight is followed by one more instead of being trusted.
        if self._poll_in_flight:
            self._poll_again = True
        else:
            self.refresh()


HOST_HELP = (
    "server address as [http[s]://][host][:port][/path]. Defaults to OLLAMA_HOST, then "
    "http://127.0.0.1:11434. A missing port means 11434 on every scheme."
)
INTERVAL_HELP = "seconds between status checks, a finite number above 0 (default: %(default)s)"


def positive_seconds(raw: str) -> float:
    try:
        seconds = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    # `nan` and `inf` pass a plain `<= 0` check, and `QTimer` cannot take either.
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return seconds


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ollama-tray", description="Show which models an Ollama server has loaded."
    )
    parser.add_argument("--host", help=HOST_HELP)
    parser.add_argument(
        "--interval",
        type=positive_seconds,
        default=DEFAULT_INTERVAL_SECONDS,
        help=INTERVAL_HELP,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log at INFO level, which adds a line when the server recovers",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('ollama-tray')}")
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
