from importlib.metadata import version
from pathlib import Path

from pytest import MonkeyPatch

from ollama_tray import build_version as build_version_module
from ollama_tray.build_version import STAMP_FILE, build_version


def test_build_version_reports_the_package_version_without_a_stamp() -> None:
    assert build_version() == version("ollama-tray")


def test_build_version_prefers_the_stamp_file(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / STAMP_FILE).write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr(build_version_module, "files", lambda _package: tmp_path)

    assert build_version() == "abc1234"
