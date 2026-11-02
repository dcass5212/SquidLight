# RGB Lighting App

A personal, Synapse-style RGB lighting controller for one Windows 11 PC. It talks to hardware only through the OpenRGB SDK server.

Read these before building, in order:
1. `docs/01-product-and-decisions.md`: what we're building and what's already decided
2. `docs/02-ux-design.md`: screens, layout, interactions
3. `docs/03-architecture.md`: modules, threading, data formats
4. `docs/04-hardware-and-setup.md`: the user's devices, OpenRGB setup, known pitfalls
5. `docs/05-roadmap.md`: milestones and acceptance criteria; build in this order

## Stack
Python 3.11+, PySide6 (Qt 6), `openrgb-python`, numpy, psutil, sounddevice (WASAPI loopback). Package with PyInstaller later, not now.

OpenRGB is **bundled** and managed by our app (`OpenRGBManager`); the user never installs or opens it. It stays a separate, unmodified process (GPL): never import or link its code. In dev, put it in `vendor/openrgb/` and run it elevated.

## Rules
- Do not re-open decisions in `01-product-and-decisions.md`. If one blocks you, say why and ask.
- Work milestone by milestone (`05-roadmap.md`). Each milestone must run end to end before starting the next.
- The engine must run and be testable without a GUI, and must work with a fake OpenRGB backend (`FakeBackend`) so tests need no hardware.
- Never write to a device from the GUI thread.
- Never call `set_colors` faster than the rate cap in `03-architecture.md`.
- Keep user data in `%APPDATA%\RGBApp\`; never in the repo.
- Keep dependencies minimal; ask before adding one.

## Commands (fill in as they are created)
- Run app: `python -m rgbapp`
- Run engine headless: `python -m rgbapp.engine --headless`
- Tests: `pytest`
