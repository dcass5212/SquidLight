# Roadmap

Build in order. Each milestone has acceptance criteria; don't start the next until they pass.

## M0: Probe and project skeleton
- Repo, venv, `pyproject.toml`, `pytest`, logging.
- `tools/probe.py` dumps all devices, zones and LED names to `docs/device-dump.txt`.
- **Done when**: the dump exists and the user has confirmed which devices were found.

## M1: Engine core (headless)
- `Backend` protocol, `OpenRGBBackend`, `FakeBackend`, reconnect logic.
- `OpenRGBManager` (dev mode: spawn `vendor/openrgb/OpenRGB.exe`, detect an already-running server, supervise, stop on exit).
- Render loop, compositor, effects: Static, Breathing, Rainbow Wave.
- CLI: `python -m rgbapp.engine --headless --effect rainbow_wave` lights real devices.
- **Done when**: the rainbow runs on the real keyboard and RAM without frame drops, devices are restored on exit, and the unit tests pass against `FakeBackend`.

## M2: Window, sidebar, dashboard, tray
- Main window with the theme, sidebar, dashboard cards (status, brightness, on/off), tray, close to tray.
- Engine runs inside the app; the status chip shows OpenRGB connection state.
- **Done when**: you can change a device's effect and brightness from the dashboard and see it on hardware.

## M3: Keyboard canvas and layers
- `k70_pro_ansi.json` from the real LED names; `keyboard_canvas` with a live preview.
- Layers panel (add, remove, reorder, visibility, opacity, blend), effect library, generated property editors.
- Effects: Color Cycle, Gradient Wave, Starlight.
- **Done when**: a 3-layer stack edits live and the preview matches the physical keyboard.

## M4: Paint mode
- Key selection (click, shift, drag-box, row, all), color picker, eyedropper, fill, clear, undo/redo.
- Saves into a "Static per-key" layer.
- **Done when**: a painted layout survives restart and layers correctly with an effect on top.

## M5: Other devices
- Memory, Mainboard and Case pages with zone selection, editable ARGB LED counts, and the "sync with keyboard" toggle.
- **Done when**: zones can be colored or given effects independently, and counts persist.

## M6: Profiles and auto-switching
- Profile CRUD, import/export, unsaved-changes state, rules editor with a process picker, tray profile submenu.
- **Done when**: launching a configured app switches the profile within about 2 seconds and returning to the default works when it closes.

## M7: Reactive and audio/system effects
- Input workers on demand. Reactive Typing, Ripple, Audio Spectrum, Audio Pulse, CPU/GPU Meter, Temperature Gradient (with the `StatsProvider` interface).
- **Done when**: effects respond with less than about 100 ms of perceived lag, inputs stop when no layer needs them, and CPU use stays reasonable at idle (target under 5% on one core).

## M8: Polish
- Settings page, start with Windows, resume-from-sleep reapply, error and empty states, log viewer, app icon.
- PyInstaller build (one-folder mode) with the OpenRGB build copied into `openrgb/` beside the exe.
- Installer (Inno Setup or similar): installs both, creates the elevated "run at logon" scheduled task for the bundled OpenRGB, adds the optional app autostart, and includes `LICENSES/OpenRGB-GPL-2.0.txt` plus a source link (also shown in Settings > About). The uninstaller removes the task.
- Wire `OpenRGBManager` installed-mode (trigger the scheduled task, no direct spawn).
- **Done when**: on a clean reboot, with no separate OpenRGB installed and iCUE/Armoury Crate off, the app comes up in the tray, OpenRGB runs hidden, the right profile is applied, and quitting the app stops the bundled OpenRGB.

## Testing guidance
- Unit-test the compositor (blend math), effects (shape and range of output), profile serialization (round trip), rules matching, and layout mapping against `FakeBackend`.
- UI: smoke tests with `pytest-qt` that the windows construct and the key actions work. Manual checks on hardware for each milestone's "Done when".
