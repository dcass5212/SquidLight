import os

# UI tests render offscreen: no windows flash up and no display is needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_inputs(monkeypatch):
    """Engines built in tests never open the sound card, the keyboard hook or the network."""
    from rgbapp.engine import core, inputs

    monkeypatch.setattr(core, "InputHub", inputs.NullHub)
