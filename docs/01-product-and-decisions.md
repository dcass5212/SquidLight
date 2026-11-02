# Product and Decisions

## Goal
One app to control all RGB on the user's PC: motherboard and case lights, keyboard, RAM. The reference is Razer Synapse (primary) and Corsair iCUE (secondary): a clean device-centric UI with a visual per-key editor, an effect library, and profiles that switch automatically.

Single user, single machine, Windows 11. No accounts, cloud or telemetry.

## Decided (do not re-open)
| Area | Decision |
|---|---|
| Language/UI | Python + PySide6 |
| Hardware access | OpenRGB SDK server (TCP 6742) via `openrgb-python`. No vendor SDKs in v1. |
| Distribution | **Bundled OpenRGB.** The installer ships an unmodified OpenRGB build in the app folder. The app starts it as a hidden background process (`--server --startminimized`) and stops it on quit. The user never opens OpenRGB. |
| Elevation | OpenRGB runs elevated (needed for SMBus on RAM and board); our app stays non-elevated. A scheduled task ("run at logon, highest privileges") started by the installer launches the bundled OpenRGB. Fall back to a one-time UAC prompt if the task is missing. |
| Licensing | OpenRGB is GPL-2.0. Ship it as a separate, unmodified process, include its license text and a link to its source in the install folder and in Settings > About. Never link or import its code into ours. |
| Run model | Tray app. A background engine keeps lighting running when the window is closed; optional start with Windows. |
| Look | Dark, Synapse-like: left device sidebar, large central device visual, effects/layers panel on the right |
| v1 features | Per-key keyboard editor, effect library with layers, profiles with per-app auto-switching, audio and system-reactive lighting |
| Platform | Windows only |

## Devices in scope
- ASUS B650EM MAX GAMING WIFI: onboard RGB plus ARGB/RGB headers feeding the case lights and fans
- Corsair K70 Pro keyboard (per-key)
- Corsair Vengeance RGB DDR5 RAM (2 or more sticks)

See `04-hardware-and-setup.md` for caveats. Devices are discovered at runtime; never hard-code LED counts.

## Defaults chosen (change only if the user asks)
- Single process: Qt GUI thread plus an engine thread. No separate service in v1; the tray keeps the process alive.
- 30 FPS render loop, with a device write cap of about 30 Hz (60 for the keyboard if stable).
- Config is JSON in `%APPDATA%\RGBApp\`. No database.
- The app supervises the bundled OpenRGB process: if the server port is already open (e.g. the user runs their own OpenRGB), connect to it and don't spawn another; otherwise start ours, and restart it if it dies.
- A global brightness master plus a per-device brightness setting.

## Non-goals (v1)
- Cross-platform support, cloud sync, mobile app, plugins or marketplace
- Game integrations (Chroma-style per-game SDKs)
- Firmware updates, macro/key-remap features, fan curves
- Modifying or embedding OpenRGB's source; writing our own device drivers; vendor SDKs (Aura, iCUE)
- Ambilight/screen capture (possible v2)

## Open questions to resolve with real hardware (milestone 0)
- Does OpenRGB detect all three device types on this machine, including DDR5 RAM over SMBus?
- Do the case fans and lights appear as ARGB zones on the board, or do they sit behind a separate hub?
- Does the K70 Pro need iCUE closed (software mode) to be controlled?
