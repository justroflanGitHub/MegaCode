"""PyInstaller entry point: imports the app the same way ``python -m megacode`` does.

Rebuild the Windows exe from the repo root with::

    .venv/Scripts/python -m PyInstaller --noconfirm --clean --windowed \
        --onefile --name MegaCode --paths src \
        --collect-binaries winpty \
        --add-binary ".venv/Lib/site-packages/winpty/winpty-agent.exe;winpty" \
        --add-binary ".venv/Lib/site-packages/winpty/OpenConsole.exe;winpty" \
        run_megacode.py

(--collect-binaries winpty: pywinpty ships no PyInstaller hook, and its
conpty.dll / winpty.dll sit as loose binaries inside the package directory
-- without the flag the frozen app crashes on the first pane spawn.
--add-binary x2: collect-binaries only picks library patterns (.dll/.pyd),
never .exe -- the two agent executables are pywinpty's pre-ConPTY fallback
and are unused on Win10+, but bundled so the exe also runs there.)

On Linux (Astra 1.7.6) there is no winpty layer at all, and the build is
onedir (onefile unpacks to /tmp at every start -- fails on noexec-/tmp
air-gapped hosts)::

    .venv/bin/python -m PyInstaller --noconfirm --clean --windowed \
        --onedir --name MegaCode --paths src run_megacode.py

Build it inside docker/Dockerfile.astra-build (debian:10 base) so glibc
matches the target. scripts/build.py wraps both variants.
"""

from megacode.runtime_env import prepare as _prepare_env

_prepare_env()  # QPA + fontconfig defaults must win over any Qt import

from megacode.app import run  # noqa: E402 (import order is the point here)

if __name__ == "__main__":
    raise SystemExit(run())
