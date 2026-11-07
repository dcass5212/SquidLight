# Hardware and Setup

## Machine
- Windows 11 Home
- Motherboard: ASUS B650EM MAX GAMING WIFI (AM5, DDR5). The case lights and fans are integrated.
- Keyboard: Corsair K70 RGB PRO (USB VID 1B1C, PID 1BC4)
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
- As built (M8): the task is `\SquidLight\OpenRGB` (Task Scheduler > SquidLight). If it is deleted, or Settings changes the OpenRGB program or port, the app re-creates it after one UAC prompt. The installer does **not** ship the PawnIO driver that OpenRGB 1.0 uses for SMBus (RAM, board); it must already be installed (it is on this PC: `sc query PawnIO`).

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

## M0 findings (verified on the real machine, SDK protocol 4)
Full dump: `docs/device-dump.txt`. It was captured with an earlier OpenRGB build that turned out to be wrong for this keyboard (see below); re-run `tools/probe.py` against 1.0.0 and compare, since LED order or names may differ.

**OpenRGB version: use 1.0.0** (recorded in `vendor/OPENRGB_VERSION`, unzipped so that `vendor/openrgb/OpenRGB.exe` is at the top level). The previous build mis-drove the K70 RGB PRO: a whole-board Static color came out non-uniform with over half the keys dark, even in OpenRGB's own GUI, so the fault was in OpenRGB and not in our app. Switching builds fixed it. If keyboard lighting looks scrambled or partly dark, check the OpenRGB build first, then iCUE, Armoury Crate and Windows Dynamic Lighting running in the background.
- **RAM**: 2 sticks of Corsair Vengeance RGB DDR5, each 1 linear zone of 10 LEDs (SMBus via PawnIO). Direct mode works.
- **Board**: ASUS Aura USB mainboard with 3 "Addressable RGB Header" zones, all reporting 0 LEDs (no auto-detect), and no onboard LEDs exposed. The 12V RGB header that SignalRGB shows is not exposed by OpenRGB.
- **Keyboard**: Corsair K70 RGB PRO, 115 LEDs, matrix 7x21, modes Direct and Static only. Works without iCUE. LED names are not unique ("Key: Space" appears 3 times), so map by index plus name. Non-key LEDs: Profile, Light, Lock, Logo, media keys.
- **Case lights and fans**: all 7 fans and both strips are daisy-chained on **Addressable RGB Header 1**. Headers 2 and 3 were not observed to drive anything. The total chain length is unknown, so the user must set the LED count for Header 1 in M5 (SignalRGB's "60 per header" is only a placeholder default).
- FakeBackend should mirror this: 115-LED keyboard, 2 RAM sticks of 10 LEDs, board with 3 zones (use a non-zero test length on zone 1).
- `tools/header_test.py` lights each ARGB header in turn to re-check this.
