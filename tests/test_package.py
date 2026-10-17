from importlib.metadata import version
from runpy import run_module
from unittest.mock import MagicMock, patch

import ollama_tray


def test_package_imports_and_is_installed() -> None:
    assert ollama_tray.__doc__
    assert version("ollama-tray")


@patch("ollama_tray.app.main")
def test_python_dash_m_runs_app_main(main: MagicMock) -> None:
    run_module("ollama_tray", run_name="__main__")

    main.assert_called_once_with()
