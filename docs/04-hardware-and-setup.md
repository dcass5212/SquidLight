# Hardware and Setup

## Machine
- Windows 11 Home
- Motherboard: ASUS B650EM MAX GAMING WIFI (AM5, DDR5). The case lights and fans are integrated.
- Keyboard: Corsair K70 Pro
- RAM: Corsair Vengeance RGB (DDR5)

## OpenRGB: bundled, not user-installed
The final app ships its own copy of OpenRGB (see `01-product-and-decisions.md`). Two phases:

**Development (now, before the installer exists)**
1. Download a current OpenRGB release (check the project's releases page) and unzip it to `vendor/openrgb/` in the repo (git-ignored; record the version in `vendor/OPENRGB_VERSION`).
2. Close or disable **Armoury Crate / Aura Sync**, **iCUE** and any other RGB software. They fight OpenRGB for the same devices.
3. Run `vendor/openrgb/OpenRGB.exe` as **Administrator** once and check that all three device types appear. Note the exact device names plus the LED names of the keyboard and the board's zones.
4. Install Python 3.11+ and create a venv in the repo. Until M1's process manager exists, start the server by hand: `OpenRGB.exe --server --startminimized`.

**Shipped app (M1 process manager, M8 installer)**
- `OpenRGBManager` (see `03-architecture.md`) starts and supervises the bundled `OpenRGB.exe`; the user never sees it.
- The installer creates a scheduled task that runs the bundled OpenRGB at logon with highest privileges, so no UAC prompt appears each time. The app triggers that task rather than elevating itself.
- OpenRGB's own settings and plugins live in its config folder; point it at an app-owned config dir (`--config`) so it doesn't touch a separate OpenRGB install the user may have.

## Expected device behavior (verify in milestone 0)
| Device | Expectation | Risk |
|---|---|---|
| Motherboard | Shows Aura controller with onboard LEDs and ARGB headers | ARGB header LED counts are usually not auto-detected. The app must let the user set them per zone. |
| Case lights/fans | Appear as zones on the board only if plugged into its headers | If the case has its own controller or hub, it won't show up; then it needs a separate integration (out of scope for v1) |
| K70 Pro | Per-key control via OpenRGB's Corsair peripheral driver | Needs iCUE closed. Key names must be captured before building the layout JSON. |
| Corsair Vengeance RGB DDR5 | Per-stick LEDs over SMBus | DDR5 Corsair support is newer and kit-dependent. If it doesn't appear, mark RAM as unsupported in v1 and continue. |

## Milestone 0 script (first thing to build)
`tools/probe.py`: connects to OpenRGB and prints, for every device, its name, type, active mode, available modes, zone names plus sizes, and LED names with indices. Save the output to `docs/device-dump.txt`. This dump drives the keyboard layout JSON and the zone UI, so don't skip it.

## Known pitfalls
- OpenRGB must be in **Direct** mode per device for the app to set per-LED colors. Other modes run firmware effects and ignore writes.
- Writing too fast over SMBus can freeze other SMBus users (RAM, sensors). Keep the RAM and motherboard write rate low (about 10 to 20 Hz is plenty).
- Admin rights: if OpenRGB runs elevated and the app doesn't, the TCP client still works, since the client only needs the socket. Don't elevate the app.
- Windows can sleep or wake the USB keyboard and drop its lighting state. Reapply the current frame on resume (handle the power event, or just force-write after a reconnect).
- A non-elevated process can't silently start an elevated one. That's why the shipped app triggers a pre-created scheduled task (highest privileges) instead of calling `subprocess` on `OpenRGB.exe` directly. In development, running OpenRGB by hand as admin is fine.
- If another OpenRGB (the user's own) already holds port 6742, connect to it instead of starting ours, and show a note in Settings.
- Verify the exact CLI flags (`--server`, `--startminimized`, `--server-port`, config-dir option) against the bundled version's `--help` before relying on them; flags have changed between releases.
- Both the SDK server and the bundled OpenRGB must bind to `127.0.0.1` only.
