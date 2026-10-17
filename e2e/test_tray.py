"""End-to-end test of the tray against a real Ollama server and a D-Bus tray protocol.

Run it with `e2e/run.sh`. The default `uv run pytest` does not collect this directory.
"""

import subprocess

from driver import Ollama, ToolTip, Tray, kill_proxy, wait_until
from pytest import mark

from conftest import PROXY_URL

GREY = "#8b949e"
GREEN = "#2ea043"
RED = "#da3633"
# Twenty app polls at the fixture's 0.5 s interval, so a slow runner still passes.
UPDATE_SECONDS = 10
# A correct unload sends `keep_alive: 0`, and Ollama evicted the model in about 25 ms here.
# `keep_alive: 1` keeps it for one second after the request, so the bound sits well between.
UNLOAD_SECONDS = 0.5


def test_tray_follows_a_real_server(
    tray: Tray, ollama: Ollama, model: str, proxy: subprocess.Popen[bytes]
) -> None:
    """Walk one session: idle, a model loads, Stop unloads it, then the server goes away.

    The steps share one app and one server, and each step starts from the state the previous
    one left, so they are one test.
    """
    wait_until(tray.tooltip, lambda tip: tip.title.endswith("No model loaded"), "idle", 10)
    assert tray.icon_color() == GREY

    ollama.load(model)
    tooltip: ToolTip = wait_until(
        tray.tooltip, lambda tip: model in tip.title, "the model in the tooltip", UPDATE_SECONDS
    )
    assert tooltip.title.splitlines()[1].startswith(f"{model} (")
    assert tray.icon_color() == GREEN

    items = wait_until(
        tray.menu_items, lambda labels: f"Stop {model}" in labels, "a Stop item", UPDATE_SECONDS
    )
    tray.click(items[f"Stop {model}"])
    wait_until(ollama.loaded_names, lambda names: names == [], "Ollama to unload", UNLOAD_SECONDS)
    wait_until(
        tray.tooltip,
        lambda tip: tip.title.endswith("No model loaded"),
        "the tooltip to show nothing loaded",
        UPDATE_SECONDS,
    )
    assert tray.icon_color() == GREY

    kill_proxy(proxy)
    wait_until(tray.tooltip, lambda tip: "Unreachable" in tip.title, "unreachable", UPDATE_SECONDS)
    assert tray.icon_color() == RED


# Ollama answers 404 for this path, so the tray shows the server as unreachable with the path
# in the first line.
@mark.parametrize("host_path", ["/a&b<c"])
def test_tooltip_shows_special_characters_as_typed(tray: Tray, host_path: str) -> None:
    """Qt sends the tooltip as plain text, so an HTML-escaped `&amp;` would appear literally."""
    tooltip = wait_until(
        tray.tooltip, lambda tip: "Unreachable" in tip.title, "unreachable", UPDATE_SECONDS
    )

    assert tooltip.title.splitlines()[0] == f"{PROXY_URL}{host_path}"
