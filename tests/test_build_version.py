from importlib.metadata import version
from pathlib import Path

from pytest import MonkeyPatch, fixture

from ollama_tray import build_version as build_version_module
from ollama_tray.build_version import STAMP_FILE, build_version


# The Nix build runs the tests against the installed, stamped package. So each test points
# `files` at its own folder instead of the real package.
@fixture
def package_dir(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(build_version_module, "files", lambda _package: tmp_path)
    return tmp_path


def test_build_version_reports_the_package_version_without_a_stamp(package_dir: Path) -> None:
    assert build_version() == version("ollama-tray")


def test_build_version_prefers_the_stamp_file(package_dir: Path) -> None:
    (package_dir / STAMP_FILE).write_text("abc1234\n", encoding="utf-8")

    assert build_version() == "abc1234"
