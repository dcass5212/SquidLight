"""The K70 layout file against the real LED list, and the matching rules."""

import logging
import re
from pathlib import Path

import numpy as np
import pytest

from rgbapp.engine.backend import FakeBackend
from rgbapp.engine.compositor import Compositor, LayerSpec
from rgbapp.engine.devices import build_groups
from rgbapp.engine.effects import Ctx
from rgbapp.engine.layout import LayoutEntry, LayoutFile, load_layouts, match_layout

DUMP = Path(__file__).resolve().parent.parent / "docs" / "device-dump.txt"


def real_keyboard_names():
    """LED names of device [3] (the keyboard) from the M0 dump."""
    text = DUMP.read_text(encoding="utf-8")
    block = text.split("Corsair K70 RGB PRO", 1)[1]
    return [m.group(1) for m in re.finditer(r"^\s+\d+: (.+)$", block, re.M)]


@pytest.fixture
def kb():
    return {g.key: g for g in build_groups(FakeBackend().devices())}["keyboard"]


def test_layout_file_places_every_real_keyboard_led():
    names = real_keyboard_names()
    assert len(names) == 115
    lay = match_layout(names)
    assert lay is not None and lay.layout_id == "k70_pro_ansi"
    assert lay.unmatched == ()
    # every LED has a real rect, and no two LEDs share one
    assert (lay.rects[:, 2:] > 0).all()
    assert len({tuple(r) for r in lay.rects.round(3)}) == 115


def test_fake_keyboard_uses_the_real_led_names():
    names = FakeBackend().devices()[-1].led_names
    assert list(names) == real_keyboard_names()


def test_keys_sit_where_they_are_on_the_physical_board(kb):
    lay = kb.layout
    idx = {}
    for i, n in enumerate(kb.led_names):
        idx.setdefault(n, i)
    center = lambda name: lay.centers()[idx[name]]
    esc, f12, w, a, s, d = (center(f"Key: {k}") for k in ("Escape", "F12", "W", "A", "S", "D"))
    assert esc[0] < f12[0] and esc[1] == f12[1]  # same row, left to right
    assert w[1] < a[1] == s[1] == d[1]  # W above the ASD row
    assert a[0] < s[0] < d[0]
    assert center("Key: Number Pad 0")[0] > center("Key: Right Arrow")[0]  # numpad on the right
    assert center("Logo")[1] < esc[1]  # top bar above the function row
    # the three spacebar LEDs sit side by side and form one keycap
    spaces = [i for i, n in enumerate(kb.led_names) if n == "Key: Space"]
    xs = lay.rects[spaces, 0]
    assert len(spaces) == 3 and list(xs) == sorted(xs)
    assert {lay.caps[i] for i in spaces} == {"space"}


def test_positions_come_from_the_layout(kb):
    i_esc = kb.led_names.index("Key: Escape")
    i_np_minus = kb.led_names.index("Key: Number Pad -")
    assert kb.positions[i_esc, 0] == pytest.approx(0.0, abs=0.03)
    assert kb.positions[i_np_minus, 0] == pytest.approx(1.0, abs=0.03)


def test_regions_work_as_masks(kb):
    assert "Number pad" in kb.mask_choices() and "Keyboard" not in kb.mask_choices()
    c = Compositor(kb)
    c.set_layers([LayerSpec("static", {"color": "#ffffff"}, mask=["Number pad"])])
    out = c.render(Ctx(0.0, 1 / 30, kb.positions))
    lit = {kb.led_names[i] for i in np.flatnonzero(out[:, 0] > 0)}
    assert len(lit) == 17 and "Key: Number Pad Enter" in lit and "Key: Enter" not in lit


def test_unknown_leds_go_on_a_fallback_strip_and_are_logged(caplog):
    names = real_keyboard_names() + ["Key: Mystery", "Key: G1"]
    with caplog.at_level(logging.WARNING):
        lay = match_layout(names)
    assert lay.unmatched == (115, 116)
    bottom = (lay.rects[:115, 1] + lay.rects[:115, 3]).max()
    assert (lay.rects[115:, 1] > bottom).all()
    assert "Key: Mystery" in caplog.text


def test_duplicate_names_match_by_occurrence():
    layout = LayoutFile("t", "t", {}, (
        LayoutEntry("A", "A", 0, 0, 1, 1), LayoutEntry("B", "B", 1, 0, 1, 1), LayoutEntry("B", "B", 2, 0, 1, 1),
    ))
    lay = match_layout(["B", "A", "B"], [layout])
    assert list(lay.rects[:, 0]) == [1, 0, 2]


def test_a_layout_that_fits_poorly_is_not_used():
    assert match_layout(["Corsair DRAM LED 0", "Corsair DRAM LED 1"]) is None
    assert {g.key: g.layout for g in build_groups(FakeBackend().devices())}["memory"] is None
    assert load_layouts()  # the shipped file loads
