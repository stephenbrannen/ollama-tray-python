from runpy import run_module
from unittest.mock import MagicMock, patch


@patch("ollama_tray.app.main")
def test_python_dash_m_runs_app_main(main: MagicMock) -> None:
    run_module("ollama_tray", run_name="__main__")

    main.assert_called_once_with()
