import os

# UI tests render offscreen: no windows flash up and no display is needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
