"""Fixtures that start the watcher stub and the tray app inside the test container.

`e2e/entrypoint.sh` has already started Xvfb and a session bus. Ollama runs in its own container.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from driver import Ollama, Tray, registered_items, wait_until
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import DBusConnection, open_dbus_connection
from pytest import fixture

APP = "/opt/app/bin/ollama-tray"
WATCHER_SCRIPT = Path(__file__).with_name("watcher.py")
# A short interval keeps each wait in the test to about one poll.
APP_INTERVAL_SECONDS = "0.5"
STARTUP_SECONDS = 30


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@fixture(scope="session")
def model() -> str:
    return os.environ["E2E_MODEL"]


@fixture(scope="session")
def ollama_url() -> str:
    return os.environ["OLLAMA_URL"]


@fixture(scope="session")
def ollama_container() -> str:
    return os.environ["OLLAMA_CONTAINER"]


@fixture(scope="session")
def ollama(ollama_url: str, model: str) -> Ollama:
    client = Ollama(ollama_url)
    client.pull(model)
    # A model left loaded by an earlier run would make the first tooltip check meaningless.
    for name in client.loaded_names():
        client.unload(name)
    return client


@fixture(scope="session")
def bus() -> Iterator[DBusConnection]:
    with open_dbus_connection(bus="SESSION") as connection:
        yield connection


@fixture
def watcher(bus: DBusConnection) -> Iterator[None]:
    process = subprocess.Popen([sys.executable, str(WATCHER_SCRIPT)])

    def watcher_owned() -> bool:
        reply = bus.send_and_get_reply(message_bus.NameHasOwner("org.kde.StatusNotifierWatcher"))
        return bool(reply.body[0])

    try:
        wait_until(watcher_owned, bool, "the watcher stub to own its name", STARTUP_SECONDS)
        yield
    finally:
        _stop(process)


@fixture
def tray(watcher: None, bus: DBusConnection, ollama: Ollama, ollama_url: str) -> Iterator[Tray]:
    """Start the app and return its registered item.

    It requests `ollama` only so the model is pulled and unloaded before the app's first poll.
    """
    environment = {**os.environ, "QT_QPA_PLATFORM": "xcb"}
    command = [APP, "--host", ollama_url, "--interval", APP_INTERVAL_SECONDS, "--verbose"]
    process = subprocess.Popen(command, env=environment)
    try:
        items = wait_until(
            lambda: registered_items(bus),
            lambda found: bool(found) or process.poll() is not None,
            "the tray to register a StatusNotifierItem",
            STARTUP_SECONDS,
        )
        assert process.poll() is None, f"ollama-tray exited with {process.returncode}"
        yield Tray(bus, items[0])
    finally:
        _stop(process)
