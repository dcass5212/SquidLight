# Architecture

## Package layout
```
rgbapp/
  __main__.py            # entry; builds QApplication, engine, tray
  engine/
    backend.py           # Backend protocol + OpenRGBBackend + FakeBackend
    openrgb_manager.py   # finds, starts, supervises, stops the bundled OpenRGB.exe
    devices.py           # DeviceGroup model: LED list, positions, zones
    compositor.py        # layer stack -> frame buffer (numpy)
    loop.py              # render thread, fps/write caps
    effects/             # one file per effect, registry in __init__.py
    inputs/              # keyboard hook, audio analyzer, system stats
    rules.py             # per-app profile switching (psutil)
  model/
    profile.py           # dataclasses + JSON (de)serialization
    store.py             # load/save in %APPDATA%\RGBApp
  ui/
    theme.py main_window.py sidebar.py dashboard.py
    device_page.py       # canvas + layers panel
    keyboard_canvas.py   # custom QGraphicsView/QWidget
    layers_panel.py effect_library.py profiles_page.py settings_page.py tray.py
    log_viewer.py power.py single_instance.py
  system/                # Windows integration, no Qt
    openrgb_task.py      # the elevated "run OpenRGB" scheduled task (installed mode)
    autostart.py         # HKCU Run entry
packaging/               # PyInstaller spec, build script, Inno Setup script, icon
  layouts/
    k70_pro_ansi.json    # key name -> x, y, w, h (in key units)
tests/
```

## Threads
- **GUI thread (Qt)**: UI only. It talks to the engine via thread-safe calls (`engine.set_profile`, `engine.update_layer`, ...) and receives frames for the preview through a Qt signal (throttled to about 30 Hz).
- **Engine thread**: owns the render loop, the compositor, and all backend I/O.
- **Input workers**: audio capture (sounddevice WASAPI loopback), keyboard hook (global key events), and a system stats poller. Each writes the latest value to a lock-protected snapshot the engine reads each frame.
- **Rules thread**: polls processes about every 1 second and emits "activate profile" events.

## Backend abstraction
```python
class Backend(Protocol):
    def connect(self) -> None: ...
    def devices(self) -> list[DeviceInfo]: ...      # id, name, type, led names, zones
    def write(self, device_id: str, colors: np.ndarray) -> None: ...   # shape (n, 3) uint8
    def close(self) -> None: ...
```
- `OpenRGBBackend` uses `openrgb.OpenRGBClient`. Put each device in **Direct** mode on connect and restore its previous mode on exit or pause.
- `FakeBackend` returns synthetic devices (a 104-key keyboard, 2 RAM sticks of 8 LEDs, a mainboard with 3 zones) and records writes. All engine tests use it.
- Reconnect with exponential backoff (1s up to 15s). Surface the state to the UI.

## OpenRGBManager
Responsibilities, in order at app startup:
1. Probe `127.0.0.1:6742`. If something answers, use it (don't spawn); record `external=True`.
2. Otherwise locate the bundled exe (`<install dir>\openrgb\OpenRGB.exe`, or `vendor/openrgb/` in dev).
3. Start it: in the installed app, trigger the scheduled task (elevated); in dev, spawn directly and expect the user to have it elevated. Use a hidden window and the app-owned config dir.
4. Wait for the port with a timeout (about 15 s), then hand off to `OpenRGBBackend.connect()`.
5. Supervise: if the process dies, restart with backoff and tell the UI via the status chip.
6. On quit: if we started it, stop it (terminate; on timeout, kill); if `external=True`, leave it running.
States exposed to the UI: `starting`, `running`, `external`, `failed(reason)`. Everything is testable with a fake process launcher.

## Rendering model
- Each device group has a frame buffer `float32 (n_leds, 3)` in 0..1.
- A profile has, per device group, an ordered list of layers. Each layer = `effect_id`, `params`, `mask` (LED indices or zone names, or all), `blend`, `opacity`, `visible`.
- Each frame: for each visible layer from bottom to top, `effect.render(ctx) -> (n_leds, 3)`, then composite using the blend mode and opacity inside the mask. Then apply the device brightness and master brightness, convert to uint8, and hand to the writer.
- `ctx` contains: `t` (seconds), `dt`, `led_positions` (normalized x,y per LED), `inputs` (audio bands, key states, cpu/gpu/temps).
- Effects are pure functions of `ctx` plus their own small state, so they are easy to test.
- Skip a device write when the frame is unchanged (an important optimization for static layers).

## Effect plugin contract
```python
class Effect:
    id: str; name: str
    params: list[Param]          # type (color, gradient, float, int, enum, bool), range, default
    needs: set[str]              # {"audio"}, {"keys"}, {"cpu"} so inputs only run when used
    def render(self, ctx, params) -> np.ndarray: ...
```
The UI builds its property editors from `params`. Inputs start only while a visible layer needs them.

## Keyboard layout mapping
- `layouts/k70_pro_ansi.json` maps **OpenRGB LED names** (e.g. "Key: A") to x, y, w, h in key units.
- At startup, match the device's LED names to the layout. Unmatched LEDs go into a fallback strip so nothing is silently dropped; log a warning with the unmatched names.
- As built (M3): the file lists LEDs in device order, each with `name`, `label`, `x, y, w, h`, a `region` (used as a mask name, e.g. "Number pad") and an optional `cap` (LEDs sharing a cap draw as one keycap: the 3-LED spacebar). Names repeat, so an LED matches by `(name, occurrence)`. The best-fitting file is used if it places at least half of a device's LEDs (`engine/layout.py`).
- Build this file after seeing the real LED names in milestone 0; don't guess them.
- For non-key devices, derive a simple layout from zones (linear, or matrix if OpenRGB provides one).

## Paint mode (as built, M4)
- Painting edits one "Static per-key" layer (effect id `static_keys`, hidden from the effect library). Its only param is `colors`, a hidden `ledmap` param: `{"<led index>": "#rrggbb"}`. It has no generated editor.
- An `Effect` may override `coverage(ctx, params)` (bool per LED, or `None` for all). The compositor multiplies the layer's weight by it, so unpainted keys are transparent and the layers below show through. Mask and opacity still apply on top.
- `model/paint.py` (`PaintDocument`, no Qt) holds the painted map and the undo/redo history; edits with the same merge key (dragging in the color picker) are one undo step. `ui/paint_panel.py` drives it and writes each change to the layer through the bridge.
- The canvas owns key selection (click, Shift-add, Ctrl-toggle, drag box, row via the Row tool or Alt-click, Ctrl+A, Esc) and the eyedropper; a multi-LED keycap is always selected whole.
- Persistence: `model/store.py` writes `profiles.json` in the format below with a single "Default" profile (layer stacks, brightness and on/off per group, plus master brightness). `EngineHost` loads it at construction and saves debounced (about 0.5 s) off the caller's thread, and flushes on stop. Other profiles and rules in the file are preserved. M6 builds profile CRUD on this file. `--backend fake` does not read or write it.

## Other devices (as built, M5)
- **ARGB LED counts.** `Backend.resize_zone(device_id, zone_name, size)`; `ZoneInfo` gained `min_size`/`max_size` (`resizable` = max > min). openrgb-python drops a zone's `leds_min/leds_max`, so `OpenRGBBackend` taps the packet callback and re-requests device data to read them. A device with no LEDs but a resizable zone (the board) still gets an empty `DeviceGroup`, so its page exists to enter the count; the loop skips writing empty frames.
- **Applying counts.** Counts are saved in `hardware.json` (same folder and atomic-write rules as `profiles.json`; `{"zone_sizes": {"<device id>|<zone name>": n}}`), not in a profile, because they describe wiring. `Engine.start` applies them after connecting (OpenRGB forgets them on restart). Changing one while running goes `EngineHost.set_zone_size` -> worker thread `Engine.resize_zone`: stop the loop, resize, rebuild groups, re-apply settings, restart the loop. The host's `generation` bumps, so the bridge emits `groups_changed` and the pages rebuild.
- **Zones UI.** `ui/zone_panel.py` sits under the preview of any non-keyboard group with zones: select (outlines it in the preview; clicking an LED selects its zone), LED count + Set for resizable zones, a color swatch and an Effect menu. A zone's color or effect is a normal layer with that zone as its mask, so it also appears in the layers panel. RAM sticks are labeled "Stick N".
- **Sync with keyboard.** `GroupSettings.sync_keyboard` (saved per group in the profile). While on, the group renders one layer: a copy of the keyboard's topmost visible non-paint layer (mask all, opacity 1, normal blend), re-pushed on every keyboard edit. The group's own layers are kept and come back when it is turned off. If the keyboard has only a painted layer, nothing is mirrored and the group keeps its own layers. The layers panel is disabled while synced.
- **Case.** The case lights and fans are Addressable RGB Header 1 on the board (docs/04), so they are a zone on the Mainboard page and not a separate group.

## Profiles and rules (as built, M6)
- **Working copy vs profile.** Edits stay live and autosave (the M4 debounce) into `working` in `profiles.json`, so a restart restores unsaved work. A profile changes only on an explicit **Save**; **Revert** reloads it. `active_profile_id` records which profile the working copy came from. `ProfileManager.dirty` compares the live state to the saved one, ignoring layer ids and device groups still at their untouched default (so a device appearing later is not an "edit"). Files from before M6 (no `working`) load the active profile.
- **`model/profiles.py` (`ProfileManager`, no Qt)**: create (from the current lighting, becomes active), duplicate (saved state), rename (unique names), delete (the last one is kept; rules for it are dropped; default/active move on), set default, import/export (`{"version":1,"profile":{name, master_brightness, devices}}`; imports get a fresh id and a unique name), activate/save/revert, and the rules list. `EngineHost.replace_settings` swaps in a whole profile and applies it to the hardware; `host.revision` drives the dirty check.
- **`SettingsStore`** gained `read_doc`/`update` (read-modify-write under a lock, since the host's save timer and the GUI both write) and `MemoryStore` for `--backend fake`.
- **Rules (`engine/rules.py`).** `{"match": "game.exe", "profile_id": "...", "focused": false}`; first match in list order wins; process names are case-insensitive and `.exe` is optional. `focused: true` means only while its window is the foreground app (Win32 via ctypes). `RulesWatcher` polls psutil about once a second (about 15 ms per poll; it does nothing when there are no rules) and acts only when the *matched rule* changes: a match activates its profile, losing the match activates the default profile, and no change leaves the profile alone. So a manual choice sticks, and changing the default doesn't switch anything. At startup a running matching app switches the profile; no match keeps the restored one. The thread emits a queued signal; the switch itself runs on the GUI thread.
- **Unsaved edits.** A manual switch (header, Profiles page, tray) asks Save / Discard / Cancel. A rule-driven switch does **not** ask and discards unsaved edits to the previous profile.
- **UI.** Header: profile dropdown, plus an "Unsaved changes" dot with Save and Revert while dirty. `ui/profiles_page.py`: profile list and actions; rules editor with a running-program picker, profile choice, "only when in front", reorder and remove. Tray: Profile submenu. After any switch the bridge re-emits `groups_changed`, so the dashboard and device pages rebuild from the new state.
- Not built: profile icons and thumbnails.

## Data formats (JSON, versioned with `"version": 1`)
`profiles.json`:
```json
{
  "version": 1,
  "profiles": [
    {"id": "uuid", "name": "Default", "master_brightness": 1.0,
     "devices": {
       "keyboard": {"brightness": 1.0, "layers": [
         {"effect": "rainbow_wave", "params": {"speed": 0.4}, "mask": "all",
          "blend": "normal", "opacity": 1.0, "visible": true}]}
     }}
  ],
  "rules": [{"match": "valorant.exe", "profile_id": "uuid"}],
  "default_profile_id": "uuid",
  "active_profile_id": "uuid",
  "working": {"master_brightness": 1.0, "devices": {}}
}
```
`settings.json`: OpenRGB path and port, accent color, autostart, rate cap, audio device, and similar. Write atomically (write a temp file, then rename) and keep one `.bak`.

## Inputs
- **Audio**: loopback capture of the default output device, 1024-sample blocks, Hann window plus rFFT, grouped into about 16 log-spaced bands. Normalize with a slow-decaying peak follower. Expose `bands[16]`, `bass`, `level`.
- **Keys**: a global low-level hook (try `pynput` first, ask before adding). Expose the set of pressed keys and press events with timestamps, mapped to LED names.
- **System stats**: CPU/RAM via psutil. Temps and GPU need a source: default to reading LibreHardwareMonitor's local web server JSON if running; otherwise the effect shows a "no sensor source" notice. Keep this behind an `StatsProvider` interface.

### As built (M7)
- **Library choices.** `sounddevice` cannot do WASAPI loopback, so audio uses `soundcard` (user-approved; `include_loopback=True` on the default speaker, re-resolved if the Windows default output changes). The key hook is plain `ctypes` (`WH_KEYBOARD_LL` on its own message-loop thread), so `pynput` was not needed. Only the pressed LED names are kept, for about 3 s, in memory.
- **`engine/inputs/`.** `InputHub` owns the workers. Each frame the loop passes it the union of what the layers need (`Compositor.needs()`: visible, opacity above 0, on a group that is on and above 0 brightness); it starts workers that appear and stops those that vanish (all stop on pause/stop). Needs are `audio`, `keys`, `cpu` (psutil load) and `sensors` (LibreHardwareMonitor). An effect whose source is a param (the meter) overrides `Effect.required_inputs(params)`. `ctx.inputs` holds `audio` (`AudioSnapshot`: `bands[16]`, `bass`, `level`, `error`), `keys` (`KeySnapshot`: `pressed`, `events` with a `seq` and age, `error`) and `stats` (`StatsSnapshot`: `values`, `errors` by need). `ctx` also gained `led_names` and `aspect` (layout width/height) so key effects find their LEDs and ripple rings are round.
- **Analysis.** `AudioAnalyzer` (pure): 40 Hz to 16 kHz in 16 log bands, a 3 dB/octave tilt, dB scaling against a peak follower with a 3 s half-life (40 dB range, so quiet music still moves), instant attack and 0.8 per block release. Silence reads exactly 0. Bass is 20-150 Hz with its own follower.
- **Stats.** `StatsProvider` (`need`, `interval`, `read()`); `PsutilProvider` 0.5 s, `LibreHardwareMonitorProvider` 1 s from `http://127.0.0.1:8085/data.json` (retried every 5 s while unreachable). `parse_lhm` takes the CPU package/Tctl and GPU core temperatures and the CPU total / GPU core loads.
- **Notices.** An effect sets `self.notice` when it can't work (no sensor source, audio capture failed, key hook failed); `Compositor.notices()` -> `Engine/EngineHost.notices()` -> `EngineBridge.notices_changed` -> a caption under the Effect picker in the layer properties.
- **Effects.** Reactive Typing, Ripple, Audio Spectrum, Audio Pulse, CPU/GPU Meter (CPU, RAM, GPU; four directions), Temperature Gradient. On devices with no key LEDs (RAM, case) a key press maps by name to a spot on the device.
- **Cost.** Nothing runs with no input layer. Measured idle cost per input on the dev PC: keys, cpu, sensors about 0%; audio about 5% of one core (soundcard polls WASAPI itself).
- The audio device choice and the LibreHardwareMonitor URL came in M8 (Settings). `InputHub.configure` marks a running worker stale and the render thread rebuilds it on its next `set_needs`, so workers only ever start and stop on that thread.

## Polish and packaging (as built, M8)
- **`settings.json`** (`model/app_settings.py`): `start_minimized`, `openrgb_path` ("" = bundled), `openrgb_port`, `write_cap_hz` (15/20/30/60), `accent`, `audio_device` ("" = Windows default), `lhm_url`. Same atomic write and `.bak` as `profiles.json`; bad values fall back to defaults; unknown keys are kept. "Start with Windows" is **not** stored: the HKCU `Run` value `SquidLight` is the truth (`system/autostart.py`), so the installer and the app can't disagree. `EngineBridge.update_app_settings` saves and applies live; only the OpenRGB path and port wait for "Restart OpenRGB".
- **Write cap.** `LoopConfig.max_write_hz`: the keyboard (USB) writes at the cap; SMBus types keep their default or the cap, whichever is lower. `RenderLoop.set_write_cap` applies it on the next frame.
- **Restart OpenRGB.** `EngineHost.restart(reconfigure)` on a worker: stop (restores devices, stops our OpenRGB), run `reconfigure` (`Engine.configure_server`), start again with the usual retries. The generation bumps on the way down and up, so pages show the "Starting" state and rebuild. A quit during a restart wins.
- **Resume from sleep.** Two triggers, both ending in `RenderLoop.request_resend(reassert=True)` (the backend's optional `reassert()` puts every device back in Direct mode, then every device is written in full): `ui/power.py` catches `WM_POWERBROADCAST` resume (the main window gets a native handle at start even when hidden) and the bridge reapplies at 0, 3 and 8 s while USB devices come back; and the loop itself treats a frame more than 2 s late as a resume.
- **Settings page** (`ui/settings_page.py`): OpenRGB status, mode, program and port, Restart; Startup; Accent (presets + custom, applied live by updating the shared `T` tokens in place and re-applying the stylesheet); Device updates; Audio device (listed on a worker thread) and hardware-monitor URL with a Test button; the log viewer; About (versions, GPL notice, source and license links). Slow work runs in a thread pool and is collected by a timer the page owns, so a result never reaches a deleted widget.
- **Log viewer** (`ui/log_viewer.py`): the last 256 KB of `app.log`, a level filter that keeps tracebacks with their record, and a 1 s size check that only runs while it is visible (appends, or reloads after rotation).
- **Error and empty states.** The dashboard's empty state offers "Restart OpenRGB" and "Settings and log" when OpenRGB failed or reported no devices.
- **One instance.** `ui/single_instance.py`: a named mutex (`SquidLightAppMutex`, also Inno Setup's `AppMutex`) decides; a second launch tells the first over a `QLocalServer` to show its window, then exits. Not used with `--backend fake`.
- **App icon.** The LED-grid icon from `ui/tray.make_icon` (accent-colored, redrawn on accent change); `tools/make_icon.py` writes `packaging/squidlight.ico` for the exe and installer. The process sets an AppUserModelID so Windows groups it as SquidLight.
- **OpenRGBManager installed mode** (the frozen app, or `RGBAPP_OPENRGB_MODE=installed`): never spawns. The task `\SquidLight\OpenRGB` (`system/openrgb_task.py`; logon trigger, "highest privileges", no time limit, runs `OpenRGB.exe` with the same flags as dev mode) is started with `schtasks /Run` and stopped with `schtasks /End`. If the task is missing or its exe or arguments differ (another port), the app re-registers it through one UAC prompt (`ShellExecuteEx runas schtasks /Create /XML`); if the user declines, it fails without asking again until "Restart OpenRGB". A server already on the port is ours (`RUNNING`, ended on quit) when the process is our bundled exe (psutil), otherwise `EXTERNAL`. Supervision watches the port (two misses restart the task).
- **Packaging.** `python packaging/build.py`: PyInstaller one-folder (`packaging/squidlight.spec`, windowed, `dist/SquidLight/`), then copies `vendor/openrgb` to `openrgb/` (plus `OPENRGB_VERSION`) and `LICENSES/` beside the exe, then compiles `packaging/squidlight.iss` with Inno Setup 6 into `dist/SquidLight-Setup-<version>.exe`. The installer (admin) runs `SquidLight.exe --register-openrgb-task`, optionally writes the `Run` value, and ends the task before replacing files; the uninstaller runs `SquidLight.exe --uninstall-cleanup` (ends and deletes the task, removes the `Run` value). `%APPDATA%\RGBApp` is kept.

## Error handling
- A device write failure disables that device for a few seconds and retries; never crash the loop.
- Bad or missing JSON loads defaults and keeps the `.bak`. Log to `%APPDATA%\RGBApp\logs\app.log` with rotation.
- On quit or pause: restore each device's original mode.
