# UX Design

Model it on Razer Synapse: device-first navigation, one big visual, simple panels. Borrow iCUE's layer-stack idea for effects.

## Window layout (min 1100x700)
```
+-----------+--------------------------------------------+----------------+
| Sidebar   |  Title bar: title | status | Save/Revert     | Right panel    |
|           |--------------------------------------------|                |
| Dashboard |                                            | Layers /       |
| Keyboard  |        Device visual (canvas)              | Effect         |
| Memory    |        live preview of what's lit          | properties     |
| Mainboard |                                            |                |
| Case      |--------------------------------------------|                |
|           |  Bottom bar: brightness, speed, Apply      |                |
| Profiles  |                                            |                |
| Settings  |                                            |                |
+-----------+--------------------------------------------+----------------+
```
- Sidebar shows one entry per detected device group, with a status dot (connected, disconnected, error). A Profiles list sits under Devices: one entry per profile, the active one marked with an accent dot; click to switch (asks first if there are unsaved changes). Manage profiles and Settings are pinned at the bottom.
- The canvas is a live preview and reflects the engine's actual output.
- The right panel changes with context: layer stack plus properties for the selected layer.

## Theme
- "Light in deep water": cool ink chrome that stays quiet, so the only saturated colors are the accent and the LEDs
- Background `#0e0f11`, panels `#15171a`, raised `#1d2024`, hairlines `#262a2f`
- Text `#e8eaed`, muted `#8f949b` (5:1 or better on every surface)
- Single accent color (default green `#44d62c`, user-changeable); "selected" is always the same 3px accent bar
- Device previews float on the page with no frame; their glow lights the surface around them
- 8px grid, 6px corner radius, 32px controls, system font (Segoe UI Variable: Display for titles, Text for body); sentence case, no all-caps labels
- Define colors as tokens in one `theme.py`; build the QSS from it

## Screens

### Dashboard
Card per device group: name, status, current effect, brightness slider, and an on/off toggle. A master brightness slider and a profile switcher sit on top. Clicking a card opens that device.

### Keyboard
- A canvas drawing the K70 Pro ANSI layout, each key as a rounded rectangle colored with its live color.
- Two modes, toggled at the top of the canvas:
  - **Effects**: manage layers (below).
  - **Paint**: select keys (click, shift-click, drag-box, select row, select all), then apply color via a picker. Includes eyedropper, fill, and clear.
- Paint creates or edits a "Static per-key" layer. Painting is non-destructive and sits in the layer stack.
- Undo/redo (Ctrl+Z / Ctrl+Y) in Paint mode.

### Memory, Mainboard, Case
- Canvas is simpler: a row of LEDs or zones. RAM draws each stick as a vertical bar of LEDs. Mainboard and case draw zones as labeled strips.
- Zones can be selected and given a static color or an effect. Fan or strip LED count is user-editable for ARGB zones (OpenRGB often can't know it).
- A "Sync with keyboard" toggle mirrors the keyboard's dominant effect onto these devices.

### Layers panel (shared)
- Ordered list, top layer composited over lower ones. Each row has a visibility eye, name, blend mode, opacity, a drag handle and a delete button.
- Blend modes, in dropdown order with a separator between families: Normal | Add, Subtract | Multiply, Screen, Overlay | Darken, Lighten | Difference, Color dodge. Each item has a one-line tooltip.
- "+ Add effect" opens the effect library (a grid of effect cards with a small animated preview).
- Selecting a layer shows its parameters: colors (1 to N gradient stops), speed, direction, size, and the key/zone mask it applies to.

### Effect library (v1)
Static, Breathing, Color Cycle, Rainbow Wave, Gradient Wave, Reactive Typing (keys flash on press and fade), Ripple (from pressed key), Starlight, Audio Spectrum, Audio Pulse (bass-driven), CPU/GPU Meter, Temperature Gradient. Each effect declares its parameter schema so the UI is generated from it.

### Profiles
- List of profiles with name, optional icon, and a thumbnail. Actions: new, duplicate, rename, delete, import/export JSON.
- Each profile stores the layer stacks and brightness for every device.
- **Auto-switch rules**: a rule is "when process `X.exe` is running (or focused), activate profile P". Priority is by list order; a default profile is used otherwise. The UI includes a process picker listing running apps.

### Settings
Start with Windows, start minimized, OpenRGB path and port, device-write rate cap, accent color, audio device selection, hardware-monitor source, a "Restart OpenRGB" button, and a log viewer.

## Tray
Icon with a menu: Open, current profile submenu, Master brightness (25/50/75/100), Pause lighting (restores device default), Quit. Closing the window hides to tray. A tray balloon appears on first minimize only.

## Interaction principles
- Every change previews live on the hardware immediately; there is no Apply button for effects. The only explicit save is the profile ("unsaved changes" dot, with Save/Revert).
- Never block the GUI on device I/O; show a status chip ("OpenRGB disconnected, retrying...").
- Empty states explain what to do ("No devices found. Is OpenRGB running as admin?").
- Keyboard accessible: tab order, Delete removes the selected layer, Space toggles visibility.
