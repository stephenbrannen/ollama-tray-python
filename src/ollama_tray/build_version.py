"""The version that `--version` prints."""

from importlib.metadata import version
from importlib.resources import files

STAMP_FILE = "_build_version"


def build_version() -> str:
    """Return the stamped build version if the build wrote one, else the package version.

    The Nix build has no release tag. It writes the short commit hash, or `dirty`, into
    `STAMP_FILE` inside the installed package. A plain `uv` or `pip` install has no stamp file
    and reports the version from `pyproject.toml`.
    """
    stamp = files("ollama_tray").joinpath(STAMP_FILE)
    if stamp.is_file():
        return stamp.read_text(encoding="utf-8").strip()
    return version("ollama-tray")
