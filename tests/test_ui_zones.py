"""M5 GUI tests (pytest-qt, FakeBackend): zone rows, LED counts, per-zone color and
effects, zone selection, and "sync with keyboard"."""

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt  # noqa: E402

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.ui.sidebar import device_page_id  # noqa: E402
from rgbapp.ui.zone_panel import ZonePanel  # noqa: E402

BOARD = "fake-board"
HEADER1 = "Addressable RGB Header 1"


def wait_for(cond, qtbot, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        qtbot.wait(20)
    return cond()


@pytest.fixture
def running(qtbot, qapp):
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
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


def last(backend, dev):
    w = backend.writes_for(dev)
    return w[-1][1] if w else None


def test_memory_and_mainboard_pages_have_zone_panels_and_the_keyboard_does_not(running):
    app, _ = running
    assert isinstance(page(app, "memory").zones, ZonePanel)
    assert isinstance(page(app, "mainboard").zones, ZonePanel)
    assert page(app, "keyboard").zones is None
    assert list(page(app, "memory").zones.rows) == ["Corsair DRAM", "Corsair DRAM (2)"]
    assert len(page(app, "mainboard").zones.rows) == 3


def test_empty_board_explains_itself_and_ram_has_no_size_editor(running):
    app, _ = running
    zones = page(app, "mainboard").zones
    assert zones.empty_note.isVisibleTo(zones)
    assert all(r.size_edit is not None and not r.swatch.isEnabled() for r in zones.rows.values())
    assert all(r.size_edit is None for r in page(app, "memory").zones.rows.values())


def test_setting_an_led_count_lights_the_header_and_rebuilds_the_page(running, qtbot):
    app, backend = running
    old_page = page(app, "mainboard")
    row = old_page.zones.rows[HEADER1]
    assert not row.apply_btn.isEnabled()
    row.size_edit.setValue(24)
    assert row.apply_btn.isEnabled()
    row.apply_btn.click()

    assert wait_for(lambda: backend.writes_for(BOARD), qtbot)
    app.bridge.poll_status()
    new_page = page(app, "mainboard")
    assert new_page is not old_page  # groups changed: pages were rebuilt
    new_row = new_page.zones.rows[HEADER1]
    assert new_row.size_edit.value() == 24 and new_row.swatch.isEnabled()
    assert not new_page.zones.empty_note.isVisibleTo(new_page.zones)
    assert backend.writes_for(BOARD)[-1][1].shape == (24, 3)


def test_zone_color_is_a_masked_layer_and_updates_in_place(running, qtbot):
    app, backend = running
    zones = page(app, "memory").zones
    row = zones.rows["Corsair DRAM (2)"]
    row._color_changed("#ff0000")
    layers = app.bridge.layers("memory")
    assert [l.mask for l in layers[1:]] == [["Corsair DRAM (2)"]]
    assert wait_for(lambda: last(backend, "fake-dram-1") is not None and (last(backend, "fake-dram-1") == [255, 0, 0]).any(), qtbot)

    row._color_changed("#00ff00")  # same layer, not a second one
    assert len(app.bridge.layers("memory")) == 2
    assert app.bridge.layers("memory")[1].params["color"] == "#00ff00"
    assert (last(backend, "fake-dram-0") != [0, 255, 0]).any()  # stick 1 untouched


def test_zone_effect_adds_a_masked_layer_and_selects_it(running):
    app, _ = running
    pg = page(app, "mainboard")
    # give the board a header to work with first
    app.bridge.set_zone_size(BOARD, HEADER1, 12)
    assert wait_for_page_rebuild(app, pg)
    pg = page(app, "mainboard")
    row = pg.zones.rows[HEADER1]
    row._add_effect("breathing")
    top = app.bridge.layers("mainboard")[-1]
    assert top.effect == "breathing" and top.mask == [HEADER1]
    assert pg.layers.selected_spec().id == top.id


def wait_for_page_rebuild(app, old_page, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.bridge.poll_status()
        if page(app, "mainboard") is not old_page:
            return True
        time.sleep(0.02)
    return False


def test_selecting_a_zone_outlines_it_and_clicking_the_preview_selects_it(running):
    app, _ = running
    pg = page(app, "memory")
    zones, preview = pg.zones, pg.preview
    zones.rows["Corsair DRAM"].pick.click()
    assert preview._outline == set(range(10))
    zones.rows["Corsair DRAM (2)"].pick.click()
    assert preview._outline == set(range(10, 20))
    zones.rows["Corsair DRAM (2)"].pick.click()  # click again clears
    assert preview._outline == set()

    preview.resize(600, 300)
    preview._geometry()
    # press on an LED of stick 1
    preview.led_pressed.emit(3)
    assert zones.selected_ref().name == "Corsair DRAM"
    assert preview._outline == set(range(10))


def test_preview_hit_test_finds_the_led_under_the_cursor(running):
    app, _ = running
    preview = page(app, "memory").preview
    preview.resize(600, 300)
    preview._geometry()
    for i in (0, 9, 10, 19):
        assert preview.led_at(preview._rects[i].center()) == i
    assert preview.led_at(QPointF(2, 2)) is None


def test_sync_toggle_mirrors_the_keyboard_and_locks_the_layers_panel(running, qtbot):
    app, backend = running
    app.bridge.set_effect("keyboard", "static")
    app.bridge.set_param("keyboard", "color", "#ff0000")
    app.bridge.set_effect("memory", "static")
    app.bridge.set_param("memory", "color", "#0000ff")
    pg = page(app, "memory")
    assert pg.layers.isEnabled()

    pg.zones.sync.click()
    assert app.bridge.settings("memory").sync_keyboard
    assert not pg.layers.isEnabled()
    assert not pg.zones.rows["Corsair DRAM"].swatch.isEnabled()
    assert wait_for(lambda: (last(backend, "fake-dram-0") == [255, 0, 0]).all(), qtbot)

    pg.zones.sync.click()
    assert pg.layers.isEnabled() and pg.zones.rows["Corsair DRAM"].swatch.isEnabled()
    assert wait_for(lambda: (last(backend, "fake-dram-0") == [0, 0, 255]).all(), qtbot)


def test_keyboard_page_has_no_sync_toggle_and_dashboard_still_builds(running):
    app, _ = running
    assert set(app.window.dashboard.cards) >= {"keyboard", "memory", "mainboard"}
    assert page(app, "keyboard").preview.__class__.__name__ == "KeyboardCanvas"
