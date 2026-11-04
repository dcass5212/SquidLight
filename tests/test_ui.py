"""GUI smoke tests (pytest-qt) against FakeBackend: the window builds, and the dashboard's
controls reach the (fake) hardware."""

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.engine.devices import build_groups  # noqa: E402
from rgbapp.ui.sidebar import DASHBOARD, device_page_id  # noqa: E402
from rgbapp.ui.widgets import _PreviewLayout  # noqa: E402


@pytest.fixture
def running(qtbot, qapp):
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    app = App(qapp, host)
    app.tray_available = app.window.tray_available = True  # offscreen has no tray
    qtbot.addWidget(app.window)
    app.start()
    assert host.wait_running(3.0)
    app.bridge.poll_status()
    yield app, backend
    app.window.allow_close = True
    host.stop()


def last_write(backend, dev):
    writes = backend.writes_for(dev)
    return writes[-1][1] if writes else None


def test_window_builds_with_a_card_and_sidebar_entry_per_group(running):
    app, _ = running
    keys = ["keyboard", "memory", "mainboard"]
    assert list(app.window.dashboard.cards) == keys  # docs/02 order, not OpenRGB's
    for key in keys:
        assert app.window.sidebar.button(device_page_id(key)) is not None
    assert app.window.chip.full_text == "Connected to OpenRGB"


def test_changing_effect_and_color_on_a_card_reaches_the_device(running, qtbot):
    app, backend = running
    card = app.window.dashboard.cards["keyboard"]
    combo = card.controls.effect
    combo.setCurrentIndex(combo.findData("static"))
    assert card.controls.swatch.isVisibleTo(card)  # Static has a color
    card.controls.swatch.color_changed.emit("#00ff00")
    qtbot.waitUntil(lambda: last_write(backend, "fake-keyboard") is not None
                    and (last_write(backend, "fake-keyboard") == [0, 255, 0]).all(), timeout=2000)
    combo.setCurrentIndex(combo.findData("rainbow_wave"))
    assert not card.controls.swatch.isVisibleTo(card)


def test_brightness_slider_and_toggle_reach_the_device(running, qtbot):
    app, backend = running
    app.bridge.set_effect("memory", "static")
    app.bridge.set_param("memory", "color", "#ffffff")
    card = app.window.dashboard.cards["memory"]
    card.controls.brightness.slider.setValue(50)
    qtbot.waitUntil(lambda: (last_write(backend, "fake-dram-0") == 128).all(), timeout=2000)
    card.toggle.click()
    qtbot.waitUntil(lambda: (last_write(backend, "fake-dram-1") == 0).all(), timeout=2000)
    assert not card.controls.brightness.isEnabled()


def test_card_and_device_page_controls_stay_in_sync(running):
    app, _ = running
    card = app.window.dashboard.cards["keyboard"]
    page = app.window.pages[device_page_id("keyboard")]
    page.controls.brightness.slider.setValue(30)
    assert card.controls.brightness.slider.value() == 30
    card.toggle.click()
    assert page.controls.toggle.isChecked() is False


def test_master_brightness_syncs_dashboard_and_tray(running):
    app, _ = running
    app.tray.brightness_actions[25].trigger()
    assert app.window.dashboard.master.slider.value() == 25
    assert app.bridge.host.master_brightness == 0.25
    app.window.dashboard.master.slider.setValue(75)
    assert app.tray.brightness_actions[75].isChecked()


def test_tray_pause_restores_devices_and_resume_retakes_them(running, qtbot):
    app, backend = running
    app.tray.pause_action.trigger()
    qtbot.waitUntil(lambda: backend.closed, timeout=2000)
    app.bridge.poll_status()
    assert app.window.chip.full_text == "Lighting paused"
    app.tray.pause_action.trigger()
    qtbot.waitUntil(lambda: not backend.closed, timeout=2000)


def test_clicking_a_preview_opens_the_device_page(running, qtbot):
    app, _ = running
    preview = app.window.dashboard.cards["memory"].preview
    qtbot.mouseClick(preview, Qt.LeftButton)
    assert app.window.current_page() == device_page_id("memory")
    assert app.window.title.text() == "Memory"
    app.window.show_page(DASHBOARD)


def test_previews_receive_live_frames(running, qtbot):
    app, _ = running
    app.window.show()
    preview = app.window.dashboard.cards["keyboard"].preview
    qtbot.waitUntil(lambda: preview._frame is not None and preview._frame.any(), timeout=2000)
    app.window.dashboard.cards["keyboard"].preview.grab()  # paints without errors


def test_closing_the_window_hides_it_to_the_tray_and_keeps_lighting(running, qtbot):
    app, backend = running
    app.window.show()
    app.window.close()
    assert not app.window.isVisible()
    assert not backend.closed  # lighting keeps running


def test_quit_restores_devices_off_the_gui_thread(qtbot, qapp, monkeypatch):
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    app = App(qapp, host)
    qtbot.addWidget(app.window)
    app.start()
    assert host.wait_running(3.0)
    monkeypatch.setattr(qapp, "quit", lambda: None)  # keep the test session's app alive
    with qtbot.waitSignal(app.stopped, timeout=5000):
        app.quit()
    assert backend.closed


def test_preview_layout_matches_device_shapes():
    groups = {g.key: g for g in build_groups(FakeBackend().devices())}
    # a keyboard with no layout file falls back to the grid preview of its matrix
    kb = _PreviewLayout({g.key: g for g in build_groups(FakeBackend(keyboard_leds=114).devices())}["keyboard"])
    assert kb.nx == 21 and not kb.vertical
    mem = _PreviewLayout(groups["memory"])
    assert mem.vertical and mem.nx == 2 and mem.ny == 10  # two upright sticks
    board = _PreviewLayout(groups["mainboard"])
    assert board.ny == 1 and board.nx == 12
    # every LED lands on a distinct cell on the RAM and board
    for lay in (mem, board):
        cells = set(zip(lay.cols.tolist(), lay.rows.tolist()))
        assert len(cells) == len(lay.cols)
    assert np.all(kb.cols >= 0) and np.all(kb.rows >= 0)
