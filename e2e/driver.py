"""Clients the end-to-end test uses: the tray over D-Bus and Ollama over HTTP."""

import contextlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable
from typing import NamedTuple, TypedDict

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import DBusConnection
from jeepney.wrappers import unwrap_msg

WATCHER = DBusAddress(
    "/StatusNotifierWatcher",
    bus_name="org.kde.StatusNotifierWatcher",
    interface="org.kde.StatusNotifierWatcher",
)
# Qt exports the item and its menu at these fixed paths on the app's own bus name.
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
POLL_SECONDS = 0.1

type DBusValue = (
    str | int | bool | bytes | list[DBusValue] | tuple[DBusValue, ...] | dict[str, DBusValue]
)


class ToolTip(NamedTuple):
    title: str
    text: str


def wait_until[T](
    read: Callable[[], T], accept: Callable[[T], bool], what: str, seconds: float
) -> T:
    """Call `read` until `accept` passes, and fail with the last value seen after `seconds`."""
    deadline = time.monotonic() + seconds
    while True:
        # The clock is read before `read` runs, so a value read after the deadline never passes.
        # The test's unload bound relies on this to reject a model that leaves one second late.
        expired = time.monotonic() > deadline
        value = read()
        if accept(value):
            return value
        if expired:
            raise AssertionError(f"waited {seconds} s for {what}; last value: {value!r}")
        time.sleep(POLL_SECONDS)


def get_property(connection: DBusConnection, address: DBusAddress, name: str) -> DBusValue:
    properties = DBusAddress(
        address.object_path, bus_name=address.bus_name, interface=PROPERTIES_INTERFACE
    )
    call = new_method_call(properties, "Get", "ss", (address.interface, name))
    ((_signature, value),) = unwrap_msg(connection.send_and_get_reply(call))
    return value


def registered_items(connection: DBusConnection) -> list[str]:
    items = get_property(connection, WATCHER, "RegisteredStatusNotifierItems")
    assert isinstance(items, list)
    return [str(item) for item in items]


class Pixmap(NamedTuple):
    width: int
    height: int
    argb: bytes


def _pixmap(value: DBusValue) -> Pixmap:
    assert isinstance(value, tuple), value
    width, height, argb = value
    assert isinstance(width, int) and isinstance(height, int) and isinstance(argb, bytes)
    return Pixmap(width, height, argb)


def _layout_labels(node: DBusValue) -> dict[str, int]:
    """Map each label in a dbusmenu layout node `(id, properties, children)` to its item id."""
    assert isinstance(node, tuple), node
    item_id, properties, children = node
    assert isinstance(item_id, int) and isinstance(properties, dict)
    assert isinstance(children, list)
    labels: dict[str, int] = {}
    label = properties.get("label")
    if isinstance(label, tuple):
        labels[str(label[1])] = item_id
    for child in children:
        assert isinstance(child, tuple), child
        labels.update(_layout_labels(child[1]))
    return labels


class Tray:
    """The app's StatusNotifierItem and its dbusmenu, read the way a desktop panel reads them."""

    def __init__(self, connection: DBusConnection, service: str) -> None:
        self._connection = connection
        self._item = DBusAddress(ITEM_PATH, bus_name=service, interface=ITEM_INTERFACE)
        self._menu = DBusAddress(MENU_PATH, bus_name=service, interface=MENU_INTERFACE)

    def tooltip(self) -> ToolTip:
        """Return the tooltip. Qt sends the whole tooltip as the title and leaves the text empty."""
        value = get_property(self._connection, self._item, "ToolTip")
        assert isinstance(value, tuple)
        _icon_name, _pixmaps, title, text = value
        return ToolTip(title=str(title), text=str(text))

    def icon_color(self) -> str:
        """Return the center pixel of the largest icon pixmap as `#rrggbb`."""
        pixmaps = get_property(self._connection, self._item, "IconPixmap")
        assert isinstance(pixmaps, list) and pixmaps, f"no IconPixmap: {pixmaps!r}"
        largest = max((_pixmap(value) for value in pixmaps), key=lambda pixmap: pixmap.width)
        # Each pixel is ARGB32 in network byte order, so the color bytes are at offsets 1 to 3.
        offset = ((largest.height // 2) * largest.width + largest.width // 2) * 4
        return "#" + largest.argb[offset + 1 : offset + 4].hex()

    def menu_items(self) -> dict[str, int]:
        """Return each menu label with its dbusmenu item id."""
        call = new_method_call(self._menu, "GetLayout", "iias", (0, -1, []))
        _revision, layout = unwrap_msg(self._connection.send_and_get_reply(call))
        return _layout_labels(layout)

    def click(self, item_id: int) -> None:
        call = new_method_call(self._menu, "Event", "isvu", (item_id, "clicked", ("i", 0), 0))
        unwrap_msg(self._connection.send_and_get_reply(call))


class _RunningModel(TypedDict):
    name: str


class _PsPayload(TypedDict):
    models: list[_RunningModel]


class Ollama:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def _request(
        self, path: str, payload: dict[str, str | int | bool] | None, seconds: float
    ) -> bytes:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(f"{self._base_url}{path}", data=data)
        with urllib.request.urlopen(request, timeout=seconds) as response:
            return response.read()

    def pull(self, model: str) -> None:
        # The first run downloads the model, so this waits much longer than the other calls.
        self._request("/api/pull", {"model": model, "stream": False}, seconds=900)

    def load(self, model: str) -> None:
        # A generate call with no prompt only loads the model.
        self._request("/api/generate", {"model": model, "keep_alive": "5m"}, seconds=300)

    def unload(self, model: str) -> None:
        self._request("/api/generate", {"model": model, "keep_alive": 0}, seconds=60)

    def loaded_names(self) -> list[str]:
        body: _PsPayload = json.loads(self._request("/api/ps", None, seconds=10))
        return [model["name"] for model in body["models"]]


def port_accepts(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def kill_proxy(process: subprocess.Popen[bytes]) -> None:
    """Kill `socat` and the child it forks for each open connection.

    The tray keeps its HTTP connection open between polls. Killing only the listener would leave
    that child forwarding, and the server would still look reachable.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
