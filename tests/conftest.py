import os

from tests.fixtures import responses

# Must be set before any Qt module is imported, so the suite runs without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Re-exported so pytest finds the shared fixture without each test file importing it.
__all__ = ["responses"]
