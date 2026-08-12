# MegaCode

Launch several **Claude Code** sessions at once and manage them in a single
window — tiled, and **drag-to-swap**.

Two launch modes:

- **Workspace (default)** — one MegaCode window hosting a grid of *embedded*
  terminals. Minimize/restore them all at once, and drag any terminal's header
  onto another to swap their positions. The running sessions survive the swap.
- **Separate windows** — the original mode: opens N Windows Terminal windows and
  tiles them across the monitor (handy if you'd rather use real WT windows).

Pick **2, 3, 4 or 6** instances; the grid shape is:

| Instances | Layout |
|-----------|--------|
| 2 | two columns side by side |
| 3 | three columns side by side |
| 4 | 2 × 2 grid |
| 6 | 3 × 2 grid |

(Add/remove terminals dynamically and the grid auto-tiles a near-square shape.)

## Requirements (runtime)

- **Windows 10/11** (uses ConPTY, the Win32 API and Windows Terminal)
- **Claude Code** (`claude`) on your `PATH`
- **Windows Terminal** (`wt.exe`) — only needed for the *separate windows* mode

## Run from source

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m megacode          # dev: PYTHONPATH=src python -m megacode
```

## Build the standalone .exe

```bash
.venv/Scripts/python.exe scripts/build.py
```

Produces a single `dist/MegaCode.exe` (no console window, no Python install
needed), ~53 MB.

## How the workspace works

Each terminal is a real Claude Code process attached to a Windows **ConPTY**
(via `pywinpty`); its VT output is parsed by **pyte** and painted by Qt. Because
the session lives *inside* its widget, moving the widget (drag-swap) moves the
live session — nothing restarts.

```
src/megacode/
  layouts.py          # pure tiling math (unit-tested) + grid shape helpers
  win32_helpers.py    # ctypes wrappers for the separate-windows mode
  conpty.py           # ConPTY wrapper around pywinpty (spawn/read/write/resize)
  terminal_widget.py  # embeddable terminal: ConPTY + pyte + QPainter + keys
  workspace.py        # draggable grid of terminal tiles, in one window
  terminal.py         # separate-windows mode (wt.exe launch + Win32 tiling)
  app.py              # launcher <-> workspace UI
```

## Test

```bash
.venv/Scripts/python.exe -m pytest                 # layout unit tests
.venv/Scripts/python.exe scripts/term_render_test.py   # claude TUI parses in pyte
.venv/Scripts/python.exe scripts/workspace_test.py     # 2 embedded terminals + swap
.venv/Scripts/python.exe scripts/verify.py 4        # separate-windows pixel tiling
```
