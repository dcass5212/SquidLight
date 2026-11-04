"""M4 GUI tests (pytest-qt, FakeBackend): Paint mode on the keyboard page."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.model.store import SettingsStore  # noqa: E402
from rgbapp.ui.sidebar import device_page_id  # noqa: E402


def make_app(qtbot, qapp, store=None):
    backend = FakeBackend()
    host = EngineHost(Engine(backend), store=store, save_delay=0.05)
    app = App(qapp, host)
    app.tray_available = app.window.tray_available = True
    qtbot.addWidget(app.window)
    app.start()
    assert host.wait_running(3.0)
    app.bridge.poll_status()
    return app, backend


@pytest.fixture
def running(qtbot, qapp):
    app, backend = make_app(qtbot, qapp)
    yield app, backend
    app.window.allow_close = True
    app.host.stop()


def kb_page(app):
    return app.window.pages[device_page_id("keyboard")]


def paint_mode(app):
    page = kb_page(app)
    page.set_mode("paint")
    return page, page.preview, page.paint


def center(canvas, led):
    return canvas.led_rect(led).center().toPoint()


def click(qtbot, canvas, led, mods=Qt.NoModifier):
    qtbot.mouseClick(canvas, Qt.LeftButton, mods, center(canvas, led))


def paint_layers(app):
    return [s for s in app.bridge.layers("keyboard") if s.effect == "static_keys"]


def named(canvas, name):
    return canvas.group.led_names.index(name)


# -- mode switch


def test_only_the_keyboard_has_a_paint_mode(running):
    app, _ = running
    assert kb_page(app).paint is not None and set(kb_page(app).mode_buttons) == {"effects", "paint"}
    assert app.window.pages[device_page_id("memory")].paint is None


def test_paint_mode_swaps_the_right_panel_and_leaving_clears_the_selection(running):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    assert page.mode == "paint" and page.right.currentWidget() is panel and canvas.paint_mode
    canvas.set_selection([1, 2])
    page.set_mode("effects")
    assert page.right.currentWidget() is page.layers and not canvas.paint_mode and not canvas.selection


def test_entering_paint_mode_does_not_add_a_layer(running):
    app, _ = running
    before = len(app.bridge.layers("keyboard"))
    paint_mode(app)
    assert len(app.bridge.layers("keyboard")) == before


# -- selecting keys


def test_click_shift_ctrl_and_empty_space(running, qtbot):
    app, _ = running
    _, canvas, _ = paint_mode(app)
    a, s, d = (named(canvas, f"Key: {k}") for k in "ASD")
    click(qtbot, canvas, a)
    assert canvas.selection == {a}
    click(qtbot, canvas, s, Qt.ShiftModifier)
    assert canvas.selection == {a, s}
    click(qtbot, canvas, s, Qt.ControlModifier)  # toggles it off
    assert canvas.selection == {a}
    click(qtbot, canvas, d)  # plain click replaces
    assert canvas.selection == {d}
    qtbot.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(2, 2))  # empty space
    assert not canvas.selection


def test_drag_box_selects_the_keys_it_touches(running, qtbot):
    app, _ = running
    _, canvas, _ = paint_mode(app)
    a, f = named(canvas, "Key: A"), named(canvas, "Key: F")
    start, end = canvas.led_rect(a).center().toPoint(), canvas.led_rect(f).center().toPoint()
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas, (start + end) / 2)
    QTest.mouseMove(canvas, end)
    assert canvas._band is not None
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)
    picked = {canvas.group.led_names[i] for i in canvas.selection}
    assert {"Key: A", "Key: S", "Key: D", "Key: F"} <= picked
    assert "Key: Q" not in picked and canvas._band is None


def test_select_row_and_select_all(running, qtbot):
    app, _ = running
    _, canvas, panel = paint_mode(app)
    a = named(canvas, "Key: A")
    panel.set_tool("row")
    click(qtbot, canvas, a)
    row = {canvas.group.led_names[i] for i in canvas.selection}
    assert {"Key: A", "Key: S", "Key: L"} <= row and "Key: Q" not in row and "Key: Z" not in row
    panel.set_tool("select")
    click(qtbot, canvas, a, Qt.AltModifier)  # Alt-click is the same shortcut
    assert {canvas.group.led_names[i] for i in canvas.selection} == row
    qtbot.keyClick(canvas, Qt.Key_A, Qt.ControlModifier)
    assert len(canvas.selection) == canvas.group.led_count
    qtbot.keyClick(canvas, Qt.Key_Escape)
    assert not canvas.selection
    panel.select_all_button.click()
    assert len(canvas.selection) == canvas.group.led_count


def test_a_multi_led_keycap_is_selected_whole(running, qtbot):
    app, _ = running
    _, canvas, _ = paint_mode(app)
    multi = next(idx for idx in canvas._caps if len(idx) > 1)  # the spacebar
    click(qtbot, canvas, multi[0])
    assert canvas.selection == set(multi)


# -- painting


def test_color_applies_to_the_selection_creates_one_layer_and_reaches_the_keyboard(running, qtbot):
    app, backend = running
    _, canvas, panel = paint_mode(app)
    a, s = named(canvas, "Key: A"), named(canvas, "Key: S")
    click(qtbot, canvas, a)
    click(qtbot, canvas, s, Qt.ShiftModifier)
    panel.swatch.color_changed.emit("#ff0000")
    panel.swatch.color_changed.emit("#00ff00")  # live picking: still one layer, one undo step
    layers = paint_layers(app)
    assert len(layers) == 1 and app.bridge.layers("keyboard")[-1].effect == "static_keys"  # on top
    assert layers[0].params["colors"] == {str(a): "#00ff00", str(s): "#00ff00"}

    names = backend.devices()[-1].led_names
    qtbot.waitUntil(lambda: _kb_color(backend, names, "Key: A") == [0, 255, 0], timeout=2000)
    assert panel.undo_button.isEnabled()
    panel.undo()
    assert not paint_layers(app)[0].params["colors"]  # one step undone the whole drag


def _kb_color(backend, names, name):
    writes = backend.writes_for("fake-keyboard")
    return writes[-1][1][names.index(name)].tolist() if writes else None


def test_fill_clear_undo_redo_and_the_all_variants(running, qtbot):
    app, _ = running
    _, canvas, panel = paint_mode(app)
    assert panel.fill_button.text() == "Fill all"
    panel._set_color("#123456")
    panel.fill_button.click()  # nothing selected: every key
    assert len(paint_layers(app)[0].params["colors"]) == canvas.group.led_count
    click(qtbot, canvas, 3)
    assert panel.clear_button.text() == "Clear selected"
    panel.clear_button.click()
    assert len(paint_layers(app)[0].params["colors"]) == canvas.group.led_count - 1
    panel.undo()
    assert len(paint_layers(app)[0].params["colors"]) == canvas.group.led_count
    panel.redo()
    assert len(paint_layers(app)[0].params["colors"]) == canvas.group.led_count - 1
    canvas.set_selection(())
    panel.clear_button.click()
    assert paint_layers(app)[0].params["colors"] == {}
    assert not panel.clear_button.isEnabled()


def test_ctrl_z_and_ctrl_y_in_paint_mode(running, qtbot):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    page.show()
    app.window.show()
    qtbot.waitExposed(app.window)
    click(qtbot, canvas, 5)
    panel._set_color("#abcdef")
    panel.fill_button.click()
    assert paint_layers(app)[0].params["colors"] == {"5": "#abcdef"}
    qtbot.keyClick(canvas, Qt.Key_Z, Qt.ControlModifier)
    assert paint_layers(app)[0].params["colors"] == {}
    qtbot.keyClick(canvas, Qt.Key_Y, Qt.ControlModifier)
    assert paint_layers(app)[0].params["colors"] == {"5": "#abcdef"}


def test_eyedropper_copies_the_shown_color_then_returns_to_select(running, qtbot):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    app.bridge.set_layer_effect("keyboard", app.bridge.layers("keyboard")[0].id, "static")
    app.bridge.set_layer_param("keyboard", app.bridge.layers("keyboard")[0].id, "color", "#336699")
    qtbot.waitUntil(lambda: canvas._frame is not None and canvas._frame[0].tolist() == [0x33, 0x66, 0x99], timeout=2000)
    panel.set_tool("eyedropper")
    click(qtbot, canvas, 0)
    assert panel.color == "#336699" and panel.recent[0] == "#336699"
    assert panel.tool_buttons["select"].isChecked() and canvas.tool == "select"
    assert not paint_layers(app)  # sampling alone paints nothing


def test_recent_colors_are_kept_and_reusable(running, qtbot):
    app, _ = running
    _, canvas, panel = paint_mode(app)
    for c in ("#111111", "#222222", "#111111"):
        panel._set_color(c)
        panel.fill_button.click()
    assert panel.recent == ["#111111", "#222222"]
    click(qtbot, canvas, 2)
    panel._use_recent("#222222")
    assert paint_layers(app)[0].params["colors"]["2"] == "#222222"


# -- painted layer in the stack


def test_paint_layer_appears_in_the_layers_panel_and_deleting_it_resets_the_document(running):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    canvas.set_selection([4])
    panel._set_color("#ff00ff")
    panel.fill_button.click()
    layer = paint_layers(app)[0]
    assert layer.id in page.layers.rows
    assert page.layers.props.effect.itemData(page.layers.props.effect.currentIndex()) in ("rainbow_wave", "static_keys")
    app.bridge.remove_layer("keyboard", layer.id)  # removed from the Effects side
    assert panel.doc.is_empty and not panel.undo_button.isEnabled()
    panel.fill_button.click()  # selection is still {4}: a fresh layer, not the old paint
    assert paint_layers(app)[0].params["colors"] == {"4": "#ff00ff"}


def test_editing_the_paint_layers_opacity_and_blend_keeps_the_paint(running):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    canvas.set_selection([1])
    panel.fill_button.click()
    lid = paint_layers(app)[0].id
    app.bridge.set_layer_props("keyboard", lid, opacity=0.4, blend="add")
    spec = paint_layers(app)[0]
    assert spec.opacity == pytest.approx(0.4) and spec.blend == "add"
    assert panel.doc.to_params() == spec.params["colors"]


def test_paint_layer_is_selectable_without_an_editor_or_a_library_entry(running):
    app, _ = running
    page, canvas, panel = paint_mode(app)
    canvas.set_selection([1])
    panel.fill_button.click()
    page.set_mode("effects")
    layers = page.layers
    layers.select(paint_layers(app)[0].id)
    assert layers.props.editors == {}  # nothing to generate: the keys are painted on the canvas
    combo = layers.props.effect
    assert combo.itemData(combo.currentIndex()) == "static_keys"
    layers.select(layers.order[-1])  # a normal layer again: the extra entry is gone
    assert combo.findData("static_keys") < 0


def test_painted_layout_is_saved_and_comes_back_after_a_restart(qtbot, qapp, tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    app, _ = make_app(qtbot, qapp, store)
    _, canvas, panel = paint_mode(app)
    canvas.set_selection([6, 7])
    panel._set_color("#00aaff")
    panel.fill_button.click()
    app.window.allow_close = True
    app.window.close()  # before this App (and its bridge) is garbage collected
    app.host.stop()

    app2, _ = make_app(qtbot, qapp, SettingsStore(tmp_path / "profiles.json"))
    try:
        assert paint_layers(app2)[0].params["colors"] == {"6": "#00aaff", "7": "#00aaff"}
        page, canvas2, panel2 = paint_mode(app2)
        assert panel2.doc.to_params() == {"6": "#00aaff", "7": "#00aaff"}
        assert panel2.clear_button.isEnabled()
    finally:
        app2.window.allow_close = True
        app2.window.close()
        app2.host.stop()
