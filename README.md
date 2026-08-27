# MegaCode

Launch several **Claude Code** sessions at once and manage them in a single
window — tiled, and **drag-to-swap**.

Two launch modes:

- **Workspace (default)** — one MegaCode window hosting a grid of *embedded*
  terminals. Minimize/restore them all at once, and drag any terminal's header
  onto another to swap their positions. The running sessions survive the swap.
- **Separate windows** — the original mode: opens N Windows Terminal windows and
  tiles them across the monitor (handy if you'd rather use real WT windows).

Each terminal can run **Claude Code**, **PowerShell**, **Command Prompt**, or a
**custom command** — pick it at launch, and mix types later with the Add
button's dropdown. The Add menu also offers an **AI Chat** tile: a dark
web-chatbot-style panel (bubbles, streaming markdown, Enter to send) backed by
one persistent `claude` CLI session over stdio. The chat keeps the whole
conversation, even across a Stop or a crashed process — the next message
resumes the same session.

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

## Terminal interactions (embedded workspace)

Each embedded terminal behaves like the classic Windows console:

- **Scrollback** — the mouse wheel scrolls back through previous output (up to
  ~10 000 lines are retained, so output that scrolled off the top is no longer
  lost). While scrolled up the view stays anchored to the same lines as new
  output arrives; press any key or paste to jump back to the live prompt.
  `Ctrl` + wheel scrolls a page at a time. (When a full-screen app such as Claude
  Code is tracking the mouse, the wheel is forwarded to it instead.)
- **Copy / paste (QuickEdit)** — drag with the left button to select, then
  **right-click copies** the selection; **right-click again pastes**. Middle-click
  also pastes. `Ctrl+Shift+C`/`V` and `Ctrl+Ins`/`Shift+Ins` work too, and plain
  `Ctrl+C` copies when there is a selection (otherwise sends the interrupt).
  Keep dragging past the top or bottom edge and the view **auto-scrolls**,
  extending the selection through the scrollback (the further past the edge,
  the faster it scrolls).
- **Resize panes** — drag the gap between two terminals (it lights up on
  hover) to widen one against its neighbours, tmux-style. Panes never collapse
  below a minimum size, and your arrangement survives adding, closing or
  swapping terminals.
- **Rename a tile** — double-click a tile's title bar to rename it. The name
  travels with the session across drag-swaps; clear it to revert to `cmd #N`.

## AI Chat tiles

The Add button's dropdown includes **AI Chat**. The tile looks like a web chat
bot — dark theme, user bubbles right, assistant markdown left (code blocks in
monospace), streaming with a block cursor, `Enter` to send, `Shift+Enter` for a
new line, `Esc`/■ to stop. Under the hood it talks to the `claude` CLI through
its machine-readable streaming mode (`--input-format/--output-format
stream-json --include-partial-messages`): one long-lived process for the whole
conversation, `--resume <session id>` if the process ever dies. Above the
composer sit two compact pickers: the **model** (`--model`, e.g. glm-5.3 /
glm-5-turbo / glm-4.7 / glm-4.6v) and the **reasoning effort** (`--effort`,
low / high / max); "Default" defers to the CLI's own settings. Changing them
restarts the claude process at the next turn (the conversation is resumed),
and every new chat tile starts where you last left the pickers — edit
`CHAT_MODELS` / `CHAT_EFFORTS` in `chat_widget.py` to expose other values.
**Drag & drop** files into the chat (anywhere — transcript or composer) to
insert their path; a file from outside the working folder also grants claude
read access to its folder (`--add-dir`) before the message is sent, so the
chat can actually open it. Chat tiles are first-class grid citizens: resize,
swap and close them like terminals.


```
src/megacode/
  layouts.py          # pure tiling math (unit-tested) + grid shape helpers
  win32_helpers.py    # ctypes wrappers for the separate-windows mode
  conpty.py           # ConPTY wrapper around pywinpty (spawn/read/write/resize)
  terminal_widget.py  # embeddable terminal: ConPTY + pyte + QPainter + keys
  chat_backend.py     # one persistent claude CLI session over stdio (stream-json)
  chat_widget.py      # the AI Chat tile: bubbles, markdown, streaming UI
  workspace.py        # resizable/draggable grid of terminal/chat tiles, in one window
  terminal.py         # separate-windows mode (wt.exe launch + Win32 tiling)
  app.py              # launcher <-> workspace UI
```

## Test

```bash
.venv/Scripts/python.exe -m pytest                 # layouts + terminal + chat widget
.venv/Scripts/python.exe scripts/term_render_test.py   # claude TUI parses in pyte
.venv/Scripts/python.exe scripts/workspace_test.py     # 2 embedded terminals + swap
.venv/Scripts/python.exe scripts/chat_backend_smoke.py # real claude stream-json chat
.venv/Scripts/python.exe scripts/verify.py 4        # separate-windows pixel tiling
```

The pytest suite runs headless (it sets `QT_QPA_PLATFORM=offscreen`) and covers
the layout math plus the terminal widget's scrollback viewport, selection
resolution, mouse encoding and resize-history behaviour.
