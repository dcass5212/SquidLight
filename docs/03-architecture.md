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

## Error handling
- A device write failure disables that device for a few seconds and retries; never crash the loop.
- Bad or missing JSON loads defaults and keeps the `.bak`. Log to `%APPDATA%\RGBApp\logs\app.log` with rotation.
- On quit or pause: restore each device's original mode.
