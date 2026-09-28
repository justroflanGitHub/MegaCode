# Changelog

All notable changes to MegaCode are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions match
`pyproject.toml`.

## [Unreleased]

### Fixed

- **Frozen-bundle `LD_LIBRARY_PATH` leaked into every child process.** The
  PyInstaller onedir bootloader prepends the bundle dir (`/opt/MegaCode`) to
  `LD_LIBRARY_PATH` and leaves it set; PTY shells, external terminals and the
  chat CLI inherited it, so system C++ tools run inside the terminal resolved
  the bundled `libstdc++.so.6` (built against debian:10, max
  `GLIBCXX_3.4.25`) and died on load on hosts with newer libraries:
  `qpdf: /opt/MegaCode/libstdc++.so.6: version 'GLIBCXX_3.4.32' not found`.
  New `megacode.childenv` strips bundle entries from the environment handed
  to children (`unixpty`, `terminal_posix`, `chat_backend`); user-set entries
  survive, the app's own environment is untouched, and running from source
  is a no-op. Verified A/B on the frozen binary: pre-fix bash panes carried
  `LD_LIBRARY_PATH=/opt/MegaCode`, fixed panes carry none.

## [0.1.0] — 2026-09-24

First working version: single-window tiled terminal workspace for Claude
Code on Windows 10/11 and Astra Linux SE 1.7.6.

### Added

- **Embedded terminal workspace**: a grid of real terminals
  (ConPTY + pyte on Windows, Unix PTY on Linux) in one window; drag a
  terminal's header onto another to swap positions — running sessions
  survive the swap. Fallback "separate windows" mode tiles N external
  terminals (Windows Terminal / xterm).
- **Per-pane commands**: Claude Code, a shell (PowerShell / Command
  Prompt / Bash), or a custom command — chosen at launch, mixed later via
  the Add menu.
- **Terminal emulator niceties**: scrollback, mouse selection and
  copy/paste, QuickEdit interplay, TUI-correct stream parsing (bracketed
  paste, mouse reporting, bare-LF handling), resize-safe rendering.
- **AI Chat tile**: a chat-panel backed by one persistent `claude` CLI
  session over stdio (streaming markdown, Stop/resume, drag-and-drop file
  access grants).
- **Broadcast toolbar**: Run all, Run pasted, and Sync input — keystrokes
  mirrored to every pane at once.
- **Tag groups**: color-tag panes into scoped groups; left-click sync
  mirrors every window, right-click scopes by tag, Run-all accepts `@tag`.
- **Cross-window linking**: separately launched MegaCode windows
  synchronize over an HMAC-authenticated local pipe (roster, per-user
  session isolation, network-filesystem refusal).
- **Astra Linux port**: platform layer for Linux (process/session/socket
  helpers, xterm tiling), `.deb` packaging (self-contained onedir bundle in
  `/opt/MegaCode`, offline-installable, only `libc6` + `libfontconfig1`
  dependencies) and a source install script for air-gapped hosts.
- **Windows standalone exe**: PyInstaller onefile bundle with the winpty
  binaries collected in.
- **Theming**: light/dark color schemes, live switch from the toolbar.
