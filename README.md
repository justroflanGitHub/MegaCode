# MegaCode

Launch several **Claude Code** sessions at once and manage them in a single
window — tiled, and **drag-to-swap**.

Runs on **Windows 10/11** and, from this port, natively on **Astra Linux SE
1.7.6** (Debian-10 base, system python3 3.7) — see **README-ASTRA.md** for
the Astra install (including air-gapped). One source tree serves both:
the PTY backend (`conpty.py` / `unixpty.py`), the process/security helpers
(`win32_helpers.py` / `posix_helpers.py` via the `plat_helpers.py` facade)
and the external-terminal mode (`terminal.py` / `terminal_posix.py`) are
selected by `sys.platform`.

Two launch modes:

- **Workspace (default)** — one MegaCode window hosting a grid of *embedded*
  terminals. Minimize/restore them all at once, and drag any terminal's header
  onto another to swap their positions. The running sessions survive the swap.
- **Separate windows** — the original mode: opens N Windows Terminal windows
  and tiles them across the monitor (on Linux: xterm with geometry-at-spawn,
  or qterminal/konsole tiled via wmctrl — see README-ASTRA.md).

Each terminal can run **Claude Code**, a shell (**PowerShell** /
**Command Prompt** on Windows, **Bash** on Linux), or a
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

- **Windows 10/11** (uses ConPTY, the Win32 API and Windows Terminal), or
  **Astra Linux SE 1.7.6** / a Debian-10-era Linux with python3 >= 3.7
- **Claude Code** (`claude`) on your `PATH`
- **Windows Terminal** (`wt.exe`) — only needed for the *separate windows*
  mode on Windows (on Linux that mode wants `xterm`)

## Run from source

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m megacode          # dev: PYTHONPATH=src python -m megacode
```

On Linux/Astra from a checkout:

```bash
./megacode.sh          # or: PYTHONPATH=src python3 -m megacode
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
- **Run a command in every pane** — type it in the toolbar's command box and
  press **▶ Run all** (or just `Enter` in the box): the command is executed in
  each open terminal at once. Chat tiles, exited panes and panes holding
  pasted input (those belong to **↵ Run pasted**, see below — appending would
  concatenate the two lines) are skipped, and the toolbar title briefly shows
  how many panes received the command.
- **Run every pasted command at once** — paste a different command into each
  pane, then press **↵ Run pasted**: every pane whose input is still waiting
  gets its `Enter` in the same instant. One trailing newline is stripped from
  each paste, so the pasted command waits at the prompt even in `cmd` /
  PowerShell (which, unlike Claude Code and PSReadLine-style apps, don't
  negotiate bracketed paste through ConPTY) instead of executing on paste.
  The button shows how many panes are waiting and lights up while armed;
  panes you didn't paste into are never touched (no stray `Enter` inside
  `nano` etc. — a paste into a full-screen TUI never arms). Running the line
  by hand, `Esc`, `Ctrl+C` or closing/exiting the pane disarms it. Note: a
  Claude Code pane counts as waiting too, so clicking the button submits its
  input box; a multi-line pasted block in plain `cmd`/PowerShell still runs
  its earlier lines at paste time (only the last one waits).
- **Sync input across panes** — toggle **⇉ Sync input** and everything you type
  in the focused pane is mirrored into all the others at once: commands,
  arrow-key history and cursor moves, `nano`/`vim` editing, Claude Code — all
  consoles stay in lockstep, tmux `synchronize-panes` style. Click again to
  stop. (Keyboard only — mouse clicks stay per-pane, since each pane has its
  own geometry.)
- **Tag sync groups** — right-click a tile's title bar to open the tag menu:
  create tags (`fe`, `be`, `deploy`… — lowercase, 1–16 letters/digits/`-`/`_`,
  Cyrillic welcome) and check them per pane; each tag shows as a small
  colored chip in the header. Once tags exist, **⇉ Sync input** mirrors only
  within your group: panes sharing a tag type in lockstep, a pane with two
  tags joins both groups at once, untagged panes sync among themselves, and
  tagged/untagged never cross — so several independent groups can be
  synchronized simultaneously, each driven by typing in any of its panes.
  While sync is on, the pane you type in lights its header together with
  every pane that will receive your keys: the lit headers *are* your
  audience. With no tags anywhere, everything syncs together as before. The
  broadcast bar also understands a scoped run: `@fe git pull` executes only
  in the `fe` panes (chat tiles never count as targets); a `@tag` that
  doesn't exist runs everywhere and says so in the flash, and `@`-style
  shell lines are never intercepted — `echo`/`rem` can't be tags, so
  `@echo off` keeps working. Tags travel with the pane across drag-swaps
  and disappear when it closes; **↵ Run pasted** deliberately ignores
  groups (your hands placed those commands pane by pane).
- **Linked windows (separate MegaCode instances)** — launch several MegaCode
  windows (separate processes, not tiles in one window) and they link
  automatically over a local named pipe, same Windows user and session. A
  **⛓ N windows** chip appears in the toolbar once a partner is found, and
  everything above grows to span every linked window: tag groups become
  cross-window (a `fe` pane here and a `fe` pane there are one group),
  **⇉ Sync input** reaches same-group panes in the other windows (their
  headers pulse cool-blue as keys arrive — the warm source/peer tint stays
  meaningful per window), and a scoped `@fe git pull` also runs in that
  tag's panes in the linked windows (an unscoped run stays in this window).
  The sync toggle itself is one shared state: turning it on in one window
  turns it on everywhere, with a "set in W2" flash saying where it came
  from. Uncheck the ⛓ chip to isolate a window for the session (one click
  links it back); the Add-menu **Link windows** item is the persistent
  preference across restarts. Privacy: the pipe is per-user and
  challenge–response authenticated with a per-user secret, and only tag
  names, pane counts and the mirrored keystrokes themselves ever leave the
  process — no titles, commands, paths or terminal output. Linking is
  disabled for elevated processes and over network-profile home folders,
  caps at 16 windows, and a crashed or closed window is dropped from the
  group automatically (the survivor re-elects a hub within a moment).

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
  tags.py             # pure tag grammar + palette for the tag sync groups
  sync_protocol.py    # wire grammar for linked windows (pure framing + validation)
  sync_security.py    # per-user link state: secret rotation, settings, atomic writes
  sync_bus.py         # the cross-window link: QLockFile-elected hub + pipe clients
  remote_registry.py  # what the other linked windows look like (tags/panes/names)
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
the layout math, the terminal widget's scrollback viewport, selection
resolution, mouse encoding and resize-history behaviour, the tag-group
grammar and scoped runs, and the cross-window link — including full
two-workspace/two-bus integration tests on a real named pipe inside one
process (input relay, sync replication, scoped runs, the kill switch and
the hostile-endpoint cases: forged handshakes, flooders, squatters, epoch
races, hub death and re-election).
