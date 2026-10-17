from importlib.metadata import version

import ollama_tray


def test_package_imports_and_is_installed() -> None:
    assert ollama_tray.__doc__
    assert version("ollama-tray") == "0.1.0"
