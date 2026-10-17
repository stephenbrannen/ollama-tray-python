import os

# Must be set before any Qt module is imported, so the suite runs without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
