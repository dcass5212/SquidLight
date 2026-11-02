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
- Build this file after seeing the real LED names in milestone 0; don't guess them.
- For non-key devices, derive a simple layout from zones (linear, or matrix if OpenRGB provides one).

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
  "default_profile_id": "uuid"
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
