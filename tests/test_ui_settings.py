"""M8 UI: Settings page, log viewer, accent, resume handling, empty-state actions."""

import pytest

pytest.importorskip("PySide6")

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.model.app_settings import AppSettings, MemorySettingsStore  # noqa: E402
from rgbapp.ui import settings_page  # noqa: E402
from rgbapp.ui.log_viewer import LogViewer, filter_lines  # noqa: E402
from rgbapp.ui.power import PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND, PowerEventFilter  # noqa: E402
from rgbapp.ui.sidebar import SETTINGS  # noqa: E402
from rgbapp.ui.theme import T  # noqa: E402


@pytest.fixture
def running(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(settings_page, "list_output_devices", lambda: ["Speakers", "Headphones"])
    backend = FakeBackend()
    store = MemorySettingsStore(AppSettings())
    host = EngineHost(Engine(backend))
    accent = T.accent
    app = App(qapp, host, app_store=store)
    app.tray_available = app.window.tray_available = True
    qtbot.addWidget(app.window)
    app.start()
    assert host.wait_running(3.0)
    app.bridge.poll_status()
    yield app, backend, store
    app.window.allow_close = True
    host.stop()
    app._apply_accent(accent)  # T is shared: put it back for other tests


def page(app):
    return app.window.pages[SETTINGS]


def test_settings_page_builds_and_shows_the_connection(running):
    app, _, _ = running
    app.window.show_page(SETTINGS)
    p = page(app)
    assert "Connected" in p.status_text.text()
    assert not p.pending.isVisibleTo(p)


def test_write_cap_change_is_saved_and_reaches_the_loop(running, qtbot):
    app, _, store = running
    p = page(app)
    p.write_cap.setCurrentIndex(p.write_cap.findData(60))
    assert store.load().write_cap_hz == 60
    assert app.host.engine.loop.config.max_write_hz == 60


def test_accent_applies_live_and_persists(running):
    app, _, store = running
    p = page(app)
    chip = next(c for c in p.chips if c.color == "#22c3e6")
    chip.click()
    assert T.accent == "#22c3e6" and store.load().accent == "#22c3e6"
    assert "#22c3e6" in app.qapp.styleSheet()
    assert chip.isChecked() and not p.chips[0].isChecked()


def test_audio_devices_are_listed_off_thread_and_choice_reaches_inputs(running, qtbot):
    app, _, store = running
    app.window.show()
    app.window.show_page(SETTINGS)
    p = page(app)
    qtbot.waitUntil(lambda: p.audio.count() == 3, timeout=3000)
    p.audio.setCurrentIndex(p.audio.findData("Headphones"))
    assert store.load().audio_device == "Headphones"
    assert app.host.engine.audio_device == "Headphones"


def test_port_change_marks_restart_pending_and_restart_applies_it(running, qtbot):
    app, backend, store = running
    p = page(app)
    backend.port = 6742  # the fake has no port; give it one like OpenRGBBackend
    p.port.setValue(6800)
    p.port.editingFinished.emit()
    assert store.load().openrgb_port == 6800
    assert p.pending.isVisibleTo(p)
    p.restart_btn.click()
    qtbot.waitUntil(lambda: backend.connect_calls == 2 and app.host.status().state == "connected", timeout=5000)
    assert backend.port == 6800
    app.bridge.app_settings_changed.emit(app.bridge.app_settings)
    assert not p.pending.isVisibleTo(p)


def test_resume_from_sleep_reapplies_more_than_once(running, qtbot):
    app, _, _ = running
    calls = []
    app.host.reapply = lambda: calls.append(1)
    clock = [100.0]
    f = PowerEventFilter(app.bridge.reapply, clock=lambda: clock[0])
    f.handle(PBT_APMRESUMEAUTOMATIC)
    f.handle(PBT_APMRESUMESUSPEND)  # the twin message: debounced
    f.handle(0x000A)  # not a resume
    qtbot.waitUntil(lambda: len(calls) >= 1, timeout=1000)
    assert app.bridge  # timers for the later passes are scheduled
    clock[0] += 60
    f.handle(PBT_APMRESUMESUSPEND)
    qtbot.waitUntil(lambda: len(calls) >= 2, timeout=1000)


def test_empty_state_offers_restart_and_settings_when_openrgb_fails(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(settings_page, "list_output_devices", lambda: [])
    backend = FakeBackend()
    backend.can_reconnect = False
    host = EngineHost(Engine(backend), retry_first=30.0)
    app = App(qapp, host, app_store=MemorySettingsStore())
    qtbot.addWidget(app.window)
    app.start()
    qtbot.waitUntil(lambda: host.status().state == "failed", timeout=3000)
    app.bridge.poll_status()
    empty = app.window.dashboard.empty
    assert empty.restart_btn.isVisibleTo(empty) and "Can't reach" in empty.title.text()
    empty.settings_btn.click()
    assert app.window.current_page() == SETTINGS
    # Hide before `app` goes out of scope: destroying a visible window sends hide events
    # to children whose Python wrappers are already being torn down.
    app.window.allow_close = True
    app.window.hide()
    host.stop()


# ---------------------------------------------------------------- log viewer


LOG = (
    "2026-11-05 10:00:00,000 DEBUG   rgbapp.x: chatter\n"
    "2026-11-05 10:00:01,000 INFO    rgbapp.x: hello\n"
    "2026-11-05 10:00:02,000 ERROR   rgbapp.x: boom\n"
    "Traceback (most recent call last):\n"
    '  File "x.py", line 1\n'
    "2026-11-05 10:00:03,000 WARNING rgbapp.x: careful\n"
)


def test_filter_keeps_tracebacks_with_their_record():
    lines = LOG.splitlines()
    kept, _ = filter_lines(lines, 40)
    assert kept == lines[2:5]
    kept, _ = filter_lines(lines, 20)
    assert "chatter" not in "".join(kept) and len(kept) == 5


def test_log_viewer_tails_appends_and_survives_rotation(qtbot, tmp_path):
    path = tmp_path / "app.log"
    path.write_text(LOG, encoding="utf-8")
    v = LogViewer(path)
    qtbot.addWidget(v)
    v.show()
    assert "hello" in v.text.toPlainText() and "chatter" not in v.text.toPlainText()
    with open(path, "a", encoding="utf-8") as f:
        f.write("2026-11-05 10:00:04,000 INFO    rgbapp.x: later\n")
    v.refresh()
    assert v.text.toPlainText().rstrip().endswith("later")
    path.write_text("2026-11-05 11:00:00,000 INFO    rgbapp.x: fresh file\n", encoding="utf-8")
    v.refresh()
    assert v.text.toPlainText().strip().endswith("fresh file") and "hello" not in v.text.toPlainText()
    v.level.setCurrentIndex(3)  # errors only
    assert v.text.toPlainText().strip() == ""


def test_log_viewer_without_a_file(qtbot, tmp_path):
    v = LogViewer(tmp_path / "none.log")
    qtbot.addWidget(v)
    v.show()
    assert "No log yet" in v.text.toPlainText()
