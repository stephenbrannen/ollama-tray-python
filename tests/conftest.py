import os

# Must be set before any Qt module is imported, so the suite runs without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Imported after the variable above, so a future Qt import in the fixtures cannot load Qt first.
from tests.fixtures import responses  # noqa: E402

# Re-exported so pytest finds the shared fixture without each test file importing it.
__all__ = ["responses"]
