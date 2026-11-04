"""M3 GUI tests (pytest-qt, FakeBackend): the keyboard canvas, the layers panel, the
effect library and the generated property editors."""

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.engine.effects import EFFECTS, get_effect_class, listed_effects  # noqa: E402
from rgbapp.ui.effect_library import EffectLibrary  # noqa: E402
from rgbapp.ui.keyboard_canvas import KeyboardCanvas  # noqa: E402
from rgbapp.ui.param_editors import GradientEditor, NumberEditor  # noqa: E402
from rgbapp.ui.sidebar import device_page_id  # noqa: E402
from rgbapp.ui.widgets import LedPreview  # noqa: E402


@pytest.fixture
def running(qtbot, qapp):
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    app = App(qapp, host)
    app.tray_available = app.window.tray_available = True
    qtbot.addWidget(app.window)
    app.start()
    assert host.wait_running(3.0)
    app.bridge.poll_status()
    yield app, backend
    app.window.allow_close = True
    host.stop()


def page(app, key):
    return app.window.pages[device_page_id(key)]


def last_write(backend, dev):
    writes = backend.writes_for(dev)
    return writes[-1][1] if writes else None


def kb_color(backend, name):
    """What the fake keyboard last received for the first LED called ``name``."""
    names = backend.devices()[-1].led_names
    frame = last_write(backend, "fake-keyboard")
    return None if frame is None else frame[names.index(name)]


# ---------------------------------------------------------------- canvas


def test_keyboard_gets_the_layout_canvas_and_other_devices_the_grid(running):
    app, _ = running
    assert isinstance(page(app, "keyboard").preview, KeyboardCanvas)
    assert isinstance(app.window.dashboard.cards["keyboard"].preview, KeyboardCanvas)
    assert isinstance(page(app, "memory").preview, LedPreview)


def test_canvas_draws_keys_where_the_layout_puts_them(running):
    app, _ = running
    canvas = page(app, "keyboard").preview
    canvas.resize(1000, 380)
    names = canvas.group.led_names
    rect = lambda n: canvas.led_rect(names.index(n))
    assert rect("Key: Escape").center().x() < rect("Key: F1").center().x() < rect("Key: F12").center().x()
    assert rect("Key: Escape").center().y() < rect("Key: A").center().y()
    assert rect("Key: Left Shift").width() > 2 * rect("Key: Z").width()
    spaces = [i for i, n in enumerate(names) if n == "Key: Space"]
    xs = [canvas.led_rect(i).center().x() for i in spaces]
    assert xs == sorted(xs)
    # hit testing finds the key under a point
    assert canvas.led_at(rect("Key: Enter").center()) == names.index("Key: Enter")
    assert canvas.led_at(QPointF(1, 1)) is None


def test_canvas_shows_each_leds_color_on_its_own_key(running):
    """The preview matches the hardware: LED i's color is drawn on key i."""
    app, _ = running
    canvas = page(app, "keyboard").preview
    canvas.resize(1000, 380)
    names = canvas.group.led_names
    frame = np.zeros((len(names), 3), dtype=np.uint8)
    frame[names.index("Key: W")] = [255, 0, 0]
    frame[names.index("Key: Number Pad 5")] = [0, 0, 255]
    canvas.set_frame(frame)
    img = canvas.grab().toImage()
    sample = lambda n: img.pixelColor(canvas.led_rect(names.index(n)).topLeft().toPoint() + QPoint(4, 4))
    w, np5, s = sample("Key: W"), sample("Key: Number Pad 5"), sample("Key: S")
    assert w.red() > 200 and w.blue() < 80
    assert np5.blue() > 200 and np5.red() < 80
    assert s.red() < 80 and s.blue() < 80  # unlit keys stay dark


def test_canvas_receives_live_frames_on_the_device_page(running, qtbot):
    app, _ = running
    app.window.show()
    app.window.show_page(device_page_id("keyboard"))
    canvas = page(app, "keyboard").preview
    qtbot.waitUntil(lambda: canvas._frame is not None and canvas._frame.any(), timeout=2000)
    canvas.grab()


# ---------------------------------------------------------------- layers panel


def test_panel_starts_with_the_base_layer_selected(running):
    app, _ = running
    panel = page(app, "keyboard").layers
    assert len(panel.rows) == 1
    assert panel.selected == panel.order[0]
    assert panel.props.effect.currentData() == "rainbow_wave"
    assert set(panel.props.editors) == {p.id for p in get_effect_class("rainbow_wave").params}


def test_three_layer_stack_edits_live_on_the_hardware(running, qtbot):
    app, backend = running
    panel = page(app, "keyboard").layers
    base = panel.order[0]
    panel.select(base)
    panel.props.effect.setCurrentIndex(panel.props.effect.findData("static"))
    panel.props.editors["color"].swatch.color_changed.emit("#ff0000")
    mid = panel.add("static")
    panel.props.editors["color"].swatch.color_changed.emit("#00ff00")
    top = panel.add("static")
    panel.props.editors["color"].swatch.color_changed.emit("#0000ff")
    assert panel.order == [top, mid, base]  # top layer listed first
    assert len(app.bridge.layers("keyboard")) == 3

    wait = lambda rgb: qtbot.waitUntil(lambda: kb_color(backend, "Key: A") is not None and list(kb_color(backend, "Key: A")) == rgb, timeout=2000)
    wait([0, 0, 255])
    panel.rows[top].opacity.slider.setValue(50)  # half blue over green
    wait([0, 128, 128])
    row = panel.rows[top]
    row.blend.setCurrentIndex(row.blend.findData("add"))
    panel.rows[top].opacity.slider.setValue(100)
    wait([0, 255, 255])
    panel.rows[mid].eye.click()  # hide green
    wait([255, 0, 255])
    # the dashboard card follows: base effect shown, with a note about the layers on top
    controls = app.window.dashboard.cards["keyboard"].controls
    assert controls.effect.currentData() == "static"
    assert controls.layers_note.text() == "+ 2 more layers on top"


def test_mask_choice_limits_a_layer_and_is_outlined_on_the_canvas(running, qtbot):
    app, backend = running
    p = page(app, "keyboard")
    panel = p.layers
    panel.props.effect.setCurrentIndex(panel.props.effect.findData("static"))
    panel.props.editors["color"].swatch.color_changed.emit("#000000")
    panel.add("static")
    panel.props.editors["color"].swatch.color_changed.emit("#ffffff")
    combo = panel.props.mask
    combo.setCurrentIndex(combo.findData("Number pad"))
    assert app.bridge.layers("keyboard")[-1].mask == ["Number pad"]
    qtbot.waitUntil(lambda: kb_color(backend, "Key: Number Pad 5") is not None
                    and list(kb_color(backend, "Key: Number Pad 5")) == [255, 255, 255]
                    and list(kb_color(backend, "Key: A")) == [0, 0, 0], timeout=2000)
    assert len(p.preview._outline) == 17
    panel.select(panel.order[-1])  # base layer covers everything: no outline
    assert not p.preview._outline


def test_keyboard_shortcuts_toggle_reorder_and_delete(running, qtbot):
    app, _ = running
    panel = page(app, "keyboard").layers
    base = panel.order[0]
    top = panel.add("starlight")
    panel.list.setFocus()
    qtbot.keyClick(panel.list, Qt.Key_Space)
    assert app.bridge.layers("keyboard")[-1].visible is False
    qtbot.keyClick(panel.list, Qt.Key_Down, Qt.ControlModifier)  # move the top layer down
    assert [s.id for s in app.bridge.layers("keyboard")] == [top, base]
    assert panel.order == [base, top] and panel.selected == top
    qtbot.keyClick(panel.list, Qt.Key_Up)
    assert panel.selected == base
    qtbot.keyClick(panel.list, Qt.Key_Delete)
    assert [s.id for s in app.bridge.layers("keyboard")] == [top]
    assert panel.selected == top
    panel.rows[top].delete.click()
    assert app.bridge.layers("keyboard") == [] and panel.selected is None
    assert panel.empty.isVisibleTo(panel) and not panel.props.isVisibleTo(panel)


def test_dragging_the_handle_reorders_layers(running, qtbot):
    app, _ = running
    panel = page(app, "keyboard").layers
    app.window.show()
    app.window.show_page(device_page_id("keyboard"))
    a = panel.order[0]
    b = panel.add("starlight")
    c = panel.add("color_cycle")
    assert panel.order == [c, b, a]
    qtbot.wait(50)
    handle = panel.rows[c].handle
    bottom_row = panel.rows[a]
    target = panel.list.mapToGlobal(QPoint(10, bottom_row.geometry().bottom() - 2))

    def send(kind, global_pos, buttons):
        local = QPointF(handle.mapFromGlobal(global_pos))
        ev = QMouseEvent(kind, local, QPointF(global_pos), Qt.LeftButton, buttons, Qt.NoModifier)
        QApplication.sendEvent(handle, ev)

    start = handle.mapToGlobal(handle.rect().center())
    send(QEvent.MouseButtonPress, start, Qt.LeftButton)
    send(QEvent.MouseMove, target, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, target, Qt.NoButton)
    assert panel.order == [b, a, c]
    assert [s.id for s in app.bridge.layers("keyboard")] == [c, a, b]


def test_generated_editors_cover_every_effect_and_edit_live(running):
    app, _ = running
    panel = page(app, "keyboard").layers
    lid = panel.order[0]
    for cls in listed_effects():
        effect_id = cls.id
        panel.props.effect.setCurrentIndex(panel.props.effect.findData(effect_id))
        assert set(panel.props.editors) == {p.id for p in cls.params}, effect_id
    # a float slider and the gradient editor write through to the layer
    panel.props.effect.setCurrentIndex(panel.props.effect.findData("gradient_wave"))
    speed = panel.props.editors["speed"]
    assert isinstance(speed, NumberEditor)
    speed.slider.setValue(speed.slider.maximum())
    assert app.bridge.layers("keyboard")[0].params["speed"] == pytest.approx(3.0)
    grad = panel.props.editors["gradient"]
    assert isinstance(grad, GradientEditor) and len(grad.swatches) == 3
    grad.add_btn.click()
    stops = app.bridge.layers("keyboard")[0].params["gradient"]
    assert [p for p, _ in stops] == pytest.approx([0, 1 / 3, 2 / 3, 1], abs=1e-3)
    grad.swatches[0].color_changed.emit("#123456")
    assert app.bridge.layers("keyboard")[0].params["gradient"][0][1] == "#123456"
    assert app.bridge.layers("keyboard")[0].id == lid


def test_effect_library_lists_every_effect_and_returns_the_pick(qtbot):
    dlg = EffectLibrary()
    qtbot.addWidget(dlg)
    assert list(dlg.cards) == [c.id for c in listed_effects()]
    assert "static_keys" not in dlg.cards  # made in Paint mode, not picked from the library
    dlg._tick()
    dlg._tick()
    assert dlg.cards["rainbow_wave"]._colors.any()  # previews animate real effects
    dlg.cards["starlight"].grab()
    dlg.cards["gradient_wave"].click()
    assert dlg.chosen == "gradient_wave" and dlg.result() == dlg.DialogCode.Accepted


def test_memory_page_has_a_layers_panel_with_zone_masks(running):
    app, _ = running
    panel = page(app, "memory").layers
    choices = [panel.props.mask.itemData(i) for i in range(panel.props.mask.count())]
    assert choices == ["all", "Corsair DRAM", "Corsair DRAM (2)"]
