"""Settings: startup, appearance, OpenRGB, performance, audio and sensors, log, about.

Everything saves as soon as it changes and applies live, except the OpenRGB path and
port, which take effect on "Restart OpenRGB" (the button says so when they're pending).
Slow work (listing audio devices, testing the sensor URL) runs on a Python thread and
reports back through a queued signal.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QTime, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..engine.inputs.audio import list_output_devices
from ..engine.inputs.stats import LHM_URL, LibreHardwareMonitorProvider
from ..engine.loop import WRITE_CAP_CHOICES
from ..engine.openrgb_manager import openrgb_version
from ..paths import licenses_dir, log_dir
from ..system import autostart
from ..system.openrgb_task import TASK_NAME
from .log_viewer import LogViewer
from .theme import ACCENT_PRESETS, T, qcolor
from .widgets import StatusDot, Toggle, guard_wheel, keyboard_focused, track_focus_reason

log = logging.getLogger(__name__)

PAGE_MAX_WIDTH = 860
OPENRGB_SOURCE_URL = "https://gitlab.com/CalcProgrammer1/OpenRGB"
SYSTEM_DEFAULT = "Windows default output"


# --------------------------------------------------------------------------- building blocks

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="settings")


def run_off_thread(owner: QWidget, fn: Callable, done: Callable) -> Future:
    """Run ``fn`` on a worker thread and call ``done(result)`` on the GUI thread. The
    worker never touches Qt: a timer owned by ``owner`` polls the future, so if the page
    is gone by then nothing is called."""
    future = _POOL.submit(fn)
    timer = QTimer(owner)
    timer.setInterval(50)

    def check() -> None:
        if future.done():
            timer.stop()
            timer.deleteLater()
            done(future.result())

    timer.timeout.connect(check)
    timer.start()
    return future



def _card(title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    """A settings section: its title (and a line about it) above a grouped surface.
    Returns the section and the surface's layout to fill."""
    section = QWidget()
    outer = QVBoxLayout(section)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(T.unit)
    t = QLabel(title)
    t.setObjectName("SectionTitle")
    outer.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        outer.addWidget(s)
    outer.addSpacing(T.unit / 2)
    box = QFrame()
    box.setObjectName("Group")
    outer.addWidget(box)
    col = QVBoxLayout(box)
    col.setContentsMargins(T.unit * 2.5, T.unit * 2, T.unit * 2.5, T.unit * 2)
    col.setSpacing(T.unit * 2)
    return section, col


def _row(label: str, hint: str, control: QWidget | QHBoxLayout) -> QHBoxLayout:
    """A setting: name and a one-line explanation on the left, the control on the right."""
    row = QHBoxLayout()
    row.setSpacing(T.unit * 2)
    text = QVBoxLayout()
    text.setSpacing(2)
    name = QLabel(label)
    name.setObjectName("SettingName")
    text.addWidget(name)
    if hint:
        h = QLabel(hint)
        h.setObjectName("Caption")
        h.setWordWrap(True)
        text.addWidget(h)
    row.addLayout(text, 1)
    if isinstance(control, QWidget):
        if hasattr(control, "setAccessibleName") and not control.accessibleName():
            control.setAccessibleName(label)
        row.addWidget(control, 0, Qt.AlignVCenter)
    else:
        row.addLayout(control)
    return row


def _rule() -> QFrame:
    line = QFrame()
    line.setObjectName("Rule")
    line.setFixedHeight(1)
    return line


class AccentChip(QAbstractButton):
    """A round color button for the accent presets."""

    def __init__(self, name: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(f"Accent: {name}")
        self.setToolTip(name)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(5, 5, -5, -5)
        if self.isChecked() or keyboard_focused(self):
            p.setPen(QPen(qcolor(T.text), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)


# --------------------------------------------------------------------------- page


class SettingsPage(QWidget):
    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._loading_devices = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        holder = QWidget()
        scroll.setWidget(holder)
        hrow = QHBoxLayout(holder)
        hrow.setContentsMargins(T.unit * 4, T.unit * 3, T.unit * 3, T.unit * 5)
        body = QWidget()
        body.setMaximumWidth(PAGE_MAX_WIDTH)
        hrow.addWidget(body, 1)
        hrow.addStretch(0)
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.unit * 4)

        col.addWidget(self._build_openrgb())
        col.addWidget(self._build_startup())
        col.addWidget(self._build_away())
        col.addWidget(self._build_appearance())
        col.addWidget(self._build_performance())
        col.addWidget(self._build_inputs())
        col.addWidget(self._build_log())
        col.addWidget(self._build_about())
        col.addStretch(1)

        bridge.status_changed.connect(self._on_status)
        bridge.app_settings_changed.connect(lambda _s: self._sync_pending())
        self._on_status(bridge.status)
        self._sync_pending()

    # -- OpenRGB

    def _build_openrgb(self) -> QFrame:
        box, col = _card("OpenRGB", "SquidLight controls your devices through OpenRGB, which runs in the background.")
        status = QHBoxLayout()
        status.setSpacing(T.unit)
        self.status_dot = StatusDot(diameter=8)
        dot_box = QVBoxLayout()
        dot_box.setContentsMargins(0, 6, 0, 0)  # centred on the first line of text
        dot_box.addWidget(self.status_dot)
        dot_box.addStretch(1)
        status.addLayout(dot_box)
        self.status_text = QLabel()
        self.status_text.setWordWrap(True)
        self.status_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status.addWidget(self.status_text, 1)
        col.addLayout(status)
        self.mode_text = QLabel(self._mode_description())
        self.mode_text.setObjectName("Caption")
        self.mode_text.setWordWrap(True)
        col.addWidget(self.mode_text)
        col.addWidget(_rule())

        s = self.bridge.app_settings
        path_row = QHBoxLayout()
        path_row.setSpacing(T.unit)
        self.path_edit = QLineEdit(s.openrgb_path)
        self.path_edit.setPlaceholderText("Bundled copy")
        self.path_edit.setAccessibleName("OpenRGB program")
        self.path_edit.setMinimumWidth(260)
        self.path_edit.editingFinished.connect(lambda: self._set(openrgb_path=self.path_edit.text().strip()))
        path_row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        reset = QPushButton("Use bundled")
        reset.setToolTip("Go back to the copy of OpenRGB that comes with SquidLight")
        reset.clicked.connect(self._use_bundled)
        path_row.addWidget(reset)
        col.addLayout(_row("Program", "Leave empty to use the bundled OpenRGB.", path_row))
        col.addWidget(_rule())

        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(s.openrgb_port)
        self.port.setAccessibleName("OpenRGB SDK port")
        self.port.setMinimumWidth(110)
        self.port.editingFinished.connect(lambda: self._set(openrgb_port=self.port.value()))
        guard_wheel(self.port)
        col.addLayout(_row("SDK port", "The local port SquidLight talks to OpenRGB on (normally 6742).", self.port))

        col.addWidget(_rule())
        actions = QHBoxLayout()
        actions.setSpacing(T.unit * 1.5)
        self.pending = QLabel("Restart OpenRGB to use the new program or port.")
        self.pending.setObjectName("Unsaved")
        actions.addWidget(self.pending)
        actions.addStretch(1)
        self.restart_btn = QPushButton("Restart OpenRGB")
        self.restart_btn.setToolTip("Hands your devices back, restarts OpenRGB, and applies your lighting again")
        self.restart_btn.clicked.connect(self._restart)
        actions.addWidget(self.restart_btn)
        col.addLayout(actions)
        return box

    def _mode_description(self) -> str:
        manager = getattr(self.bridge.host.engine, "manager", None)
        if manager is None:
            return "SquidLight isn't managing OpenRGB in this session (fake backend or --no-manager)."
        version = openrgb_version()
        name = f"OpenRGB {version}" if version else "OpenRGB"
        if manager.mode == "installed":
            return (
                f"{name} runs with administrator rights through the scheduled task “{TASK_NAME.lstrip(chr(92))}”, "
                "so you never see a UAC prompt. It starts at sign-in and stops when you quit SquidLight."
            )
        return (
            f"Development mode: {name} from vendor/openrgb is started directly, so run SquidLight from an "
            "administrator terminal (or start OpenRGB yourself as administrator)."
        )

    def _on_status(self, status) -> None:
        from .theme import status_color

        self.status_dot.set_color(status_color(status.state))
        text = status.text
        if status.external:
            text += (
                "\nThis is an OpenRGB you started yourself (port already in use), so SquidLight uses it "
                "and leaves it running when you quit."
            )
        self.status_text.setText(text)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose OpenRGB.exe", self.path_edit.text(), "OpenRGB (OpenRGB.exe);;Programs (*.exe)")
        if path:
            self.path_edit.setText(path)
            self._set(openrgb_path=path)

    def _use_bundled(self) -> None:
        self.path_edit.clear()
        self._set(openrgb_path="")

    def _sync_pending(self) -> None:
        pending = self.bridge.server_settings_pending()
        self.pending.setVisible(pending)
        self.restart_btn.setObjectName("AccentButton" if pending else "")
        self.restart_btn.setCursor(Qt.PointingHandCursor)
        self.restart_btn.style().unpolish(self.restart_btn)
        self.restart_btn.style().polish(self.restart_btn)

    def _restart(self) -> None:
        self.bridge.restart_openrgb()
        self._sync_pending()

    # -- startup

    def _build_startup(self) -> QFrame:
        box, col = _card("Startup")
        self.autostart = Toggle()
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)
        col.addLayout(_row("Start with Windows", "Your lighting comes on when you sign in.", self.autostart))
        col.addWidget(_rule())
        self.minimized = Toggle()
        self.minimized.setChecked(self.bridge.app_settings.start_minimized)
        self.minimized.toggled.connect(self._toggle_minimized)
        col.addLayout(
            _row("Start in the tray", "When Windows starts SquidLight, don't open the window.", self.minimized)
        )
        self.minimized.setEnabled(self.autostart.isChecked())
        return box

    def _toggle_autostart(self, on: bool) -> None:
        try:
            autostart.enable(self.bridge.app_settings.start_minimized) if on else autostart.disable()
        except OSError as exc:
            QMessageBox.warning(self, "Start with Windows", f"Windows didn't accept the change: {exc}")
            self.autostart.blockSignals(True)
            self.autostart.setChecked(autostart.is_enabled())
            self.autostart.blockSignals(False)
        self.minimized.setEnabled(self.autostart.isChecked())

    def _toggle_minimized(self, on: bool) -> None:
        self._set(start_minimized=on)
        if autostart.is_enabled():
            try:
                autostart.enable(on)  # the Run entry carries --minimized
            except OSError as exc:
                log.warning("could not update the autostart entry: %s", exc)

    # -- lights off: lock, idle, schedule

    def _build_away(self) -> QFrame:
        a = self.bridge.app_settings
        box, col = _card(
            "Turn lights off",
            "The lights fade out when any of these apply and fade back in when none do. "
            "Pausing from the tray still works on top of this.",
        )
        self.off_on_lock = Toggle()
        self.off_on_lock.setChecked(a.off_on_lock)
        self.off_on_lock.toggled.connect(lambda on: self._set(off_on_lock=on))
        col.addLayout(_row("When the screen is locked", "Back on as soon as you unlock.", self.off_on_lock))
        col.addWidget(_rule())

        self.idle_on = Toggle()
        self.idle_on.setChecked(a.idle_enabled)
        self.idle_on.toggled.connect(self._toggle_idle)
        self.idle_minutes = QSpinBox()
        self.idle_minutes.setRange(1, 240)
        self.idle_minutes.setSuffix(" min")
        self.idle_minutes.setValue(a.idle_minutes)
        self.idle_minutes.setEnabled(a.idle_enabled)
        self.idle_minutes.setAccessibleName("Minutes without input before the lights turn off")
        self.idle_minutes.valueChanged.connect(lambda v: self._set(idle_minutes=v))
        guard_wheel(self.idle_minutes)
        idle_ctl = QHBoxLayout()
        idle_ctl.setSpacing(T.unit * 1.5)
        idle_ctl.addWidget(self.idle_minutes)
        idle_ctl.addWidget(self.idle_on)
        col.addLayout(
            _row("When I'm away from the keyboard", "No keyboard or mouse input for this long. Any input brings them back.", idle_ctl)
        )
        col.addWidget(_rule())

        self.sched_on = Toggle()
        self.sched_on.setChecked(a.schedule_enabled)
        self.sched_on.toggled.connect(self._toggle_schedule)
        self.sched_off_time = self._time_edit(a.schedule_off, "Lights turn off at", "schedule_off")
        self.sched_on_time = self._time_edit(a.schedule_on, "Lights turn on at", "schedule_on")
        for w in (self.sched_off_time, self.sched_on_time):
            w.setEnabled(a.schedule_enabled)
        sched_ctl = QHBoxLayout()
        sched_ctl.setSpacing(T.unit * 1.5)
        sched_ctl.addWidget(self.sched_off_time)
        sched_ctl.addWidget(QLabel("to"))
        sched_ctl.addWidget(self.sched_on_time)
        sched_ctl.addWidget(self.sched_on)
        col.addLayout(_row("On a schedule", "Off from the first time until the second, every day.", sched_ctl))
        col.addWidget(_rule())

        self.fade = QComboBox()
        for ms, label in ((0, "Instant"), (300, "Quick (0.3 s)"), (600, "Smooth (0.6 s)"), (1500, "Slow (1.5 s)"), (3000, "Very slow (3 s)")):
            self.fade.addItem(label, ms)
        idx = self.fade.findData(a.fade_ms)
        if idx < 0:
            self.fade.addItem(f"{a.fade_ms / 1000:g} s", a.fade_ms)
            idx = self.fade.count() - 1
        self.fade.setCurrentIndex(idx)
        self.fade.currentIndexChanged.connect(lambda _i: self._set(fade_ms=self.fade.currentData()))
        guard_wheel(self.fade)
        col.addLayout(_row("Fade length", "Also used to blend between profiles instead of cutting.", self.fade))
        return box

    def _time_edit(self, value: str, name: str, key: str) -> QTimeEdit:
        edit = QTimeEdit(QTime.fromString(value, "HH:mm"))
        edit.setDisplayFormat("HH:mm")
        edit.setAccessibleName(name)
        edit.timeChanged.connect(lambda t: self._set(**{key: t.toString("HH:mm")}))
        return edit

    def _toggle_idle(self, on: bool) -> None:
        self.idle_minutes.setEnabled(on)
        self._set(idle_enabled=on)

    def _toggle_schedule(self, on: bool) -> None:
        self.sched_off_time.setEnabled(on)
        self.sched_on_time.setEnabled(on)
        self._set(schedule_enabled=on)

    # -- appearance

    def _build_appearance(self) -> QFrame:
        box, col = _card("Appearance")
        chips = QHBoxLayout()
        chips.setSpacing(4)
        self.chips: list[AccentChip] = []
        for name, color in ACCENT_PRESETS:
            chip = AccentChip(name, color)
            chip.clicked.connect(lambda _=False, c=color: self._set_accent(c))
            chips.addWidget(chip)
            self.chips.append(chip)
        custom = QPushButton("Custom…")
        custom.clicked.connect(self._custom_accent)
        chips.addSpacing(T.unit)
        chips.addWidget(custom)
        col.addLayout(_row("Accent color", "Used for what's selected or switched on.", chips))
        self._sync_chips()
        return box

    def _sync_chips(self) -> None:
        for chip in self.chips:
            chip.setChecked(chip.color.lower() == self.bridge.app_settings.accent.lower())

    def _set_accent(self, color: str) -> None:
        self._set(accent=color)
        self._sync_chips()

    def _custom_accent(self) -> None:
        color = QColorDialog.getColor(QColor(self.bridge.app_settings.accent), self.window(), "Accent color")
        if color.isValid():
            self._set_accent(color.name())

    # -- performance

    def _build_performance(self) -> QFrame:
        box, col = _card("Performance")
        self.write_cap = QComboBox()
        for hz in WRITE_CAP_CHOICES:
            self.write_cap.addItem(f"{hz} per second" + (" (default)" if hz == 30 else ""), hz)
        self.write_cap.setCurrentIndex(max(0, self.write_cap.findData(self.bridge.app_settings.write_cap_hz)))
        self.write_cap.currentIndexChanged.connect(lambda _i: self._set(write_cap_hz=self.write_cap.currentData()))
        guard_wheel(self.write_cap)
        col.addLayout(
            _row(
                "Device updates",
                "How often the keyboard is updated. RAM and motherboard stay at 15 per second or less, "
                "since faster updates can stall other devices on the same bus.",
                self.write_cap,
            )
        )
        return box

    # -- audio and sensors

    def _build_inputs(self) -> QFrame:
        box, col = _card("Audio and sensors", "Used by the audio and system effects, only while one is showing.")
        audio = QHBoxLayout()
        audio.setSpacing(T.unit)
        self.audio = QComboBox()
        self.audio.setMinimumWidth(260)
        self.audio.setAccessibleName("Audio device")
        self.audio.addItem(SYSTEM_DEFAULT, "")
        saved = self.bridge.app_settings.audio_device
        if saved:
            self.audio.addItem(saved, saved)
            self.audio.setCurrentIndex(1)
        self.audio.currentIndexChanged.connect(self._audio_picked)
        guard_wheel(self.audio)
        audio.addWidget(self.audio)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("List the playback devices again")
        refresh.clicked.connect(self._list_devices)
        audio.addWidget(refresh)
        col.addLayout(_row("Audio device", "What the audio effects listen to.", audio))
        col.addWidget(_rule())

        sensors = QHBoxLayout()
        sensors.setSpacing(T.unit)
        self.lhm = QLineEdit(self.bridge.app_settings.lhm_url)
        self.lhm.setPlaceholderText(LHM_URL)
        self.lhm.setAccessibleName("Hardware monitor address")
        self.lhm.setMinimumWidth(260)
        self.lhm.editingFinished.connect(lambda: self._set(lhm_url=self.lhm.text().strip()))
        sensors.addWidget(self.lhm)
        test = QPushButton("Test")
        test.clicked.connect(self._test_sensors)
        sensors.addWidget(test)
        col.addLayout(
            _row(
                "Hardware monitor",
                "GPU load and temperatures come from LibreHardwareMonitor's web server "
                "(Options > Remote Web Server).",
                sensors,
            )
        )
        self.sensor_result = QLabel()
        self.sensor_result.setObjectName("Caption")
        self.sensor_result.setWordWrap(True)
        self.sensor_result.hide()
        col.addWidget(self.sensor_result)
        return box

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self.audio.count() <= 2 and not self._loading_devices:
            self._list_devices()

    def _list_devices(self) -> None:
        if self._loading_devices:
            return
        self._loading_devices = True
        run_off_thread(self, lambda: list_output_devices(), self._fill_devices)

    def _fill_devices(self, names: list) -> None:
        self._loading_devices = False
        saved = self.bridge.app_settings.audio_device
        self.audio.blockSignals(True)
        self.audio.clear()
        self.audio.addItem(SYSTEM_DEFAULT, "")
        for name in names:
            self.audio.addItem(name, name)
        if saved and saved not in names:
            self.audio.addItem(f"{saved} (not connected)", saved)
        self.audio.setCurrentIndex(max(0, self.audio.findData(saved)))
        self.audio.blockSignals(False)

    def _audio_picked(self, _index: int) -> None:
        self._set(audio_device=self.audio.currentData() or "")

    def _test_sensors(self) -> None:
        url = self.lhm.text().strip() or LHM_URL
        self._set(lhm_url=url)
        self.sensor_result.setText("Checking…")
        self.sensor_result.setStyleSheet("")
        self.sensor_result.show()

        def work() -> str:
            try:
                values = LibreHardwareMonitorProvider(url, timeout=2.0).read()
            except Exception as exc:
                return f"!{exc}"
            found = ", ".join(sorted(values)) or "no CPU or GPU sensors"
            return f"Connected. Found: {found}."

        run_off_thread(self, work, self._show_sensor_result)

    def _show_sensor_result(self, text: str) -> None:
        failed = text.startswith("!")
        self.sensor_result.setText(text.lstrip("!"))
        self.sensor_result.setStyleSheet(f"color: {T.warn};" if failed else "")

    # -- log and about

    def _build_log(self) -> QFrame:
        box, col = _card("Log", "What SquidLight and its OpenRGB connection have been doing. Check here when something isn't lighting up.")
        self.log_viewer = LogViewer(log_dir() / "app.log")
        col.addWidget(self.log_viewer)
        return box

    def _build_about(self) -> QFrame:
        version = openrgb_version()
        box, col = _card("About")
        lines = QLabel(
            f"<b>SquidLight {__version__}</b><br>"
            f"Lighting through OpenRGB{' ' + version if version else ''}. OpenRGB is free software under the "
            "GNU General Public License v2. SquidLight runs it as a separate, unmodified program."
        )
        lines.setWordWrap(True)
        lines.setTextFormat(Qt.RichText)
        col.addWidget(lines)
        license_file = licenses_dir() / "OpenRGB-GPL-2.0.txt"
        links = QLabel(
            f'<a href="{OPENRGB_SOURCE_URL}" style="color:{T.accent}">OpenRGB source code</a>'
            f'&nbsp;&nbsp;&nbsp;&nbsp;<a href="{QUrl.fromLocalFile(str(license_file)).toString()}" '
            f'style="color:{T.accent}">OpenRGB license (GPL-2.0)</a>'
        )
        links.setTextFormat(Qt.RichText)
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextBrowserInteraction)
        col.addWidget(links)
        return box

    # -- saving

    def _set(self, **changes) -> None:
        self.bridge.update_app_settings(**changes)
