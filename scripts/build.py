"""Build the standalone MegaCode bundle with PyInstaller.

Run from the project root with the project venv active, or directly:

    .venv/Scripts/python.exe scripts/build.py        (Windows -> dist/MegaCode.exe)
    .venv/bin/python scripts/build.py                (Linux  -> dist/MegaCode/)

The venv interpreter is used automatically if present, otherwise the current
interpreter. Previous ``build/`` and ``dist/`` folders are removed first for a
clean, reproducible artifact.

Platform notes:
* Windows: onefile .exe via megacode.spec (winpty binaries included).
* Linux (Astra 1.7.6): onedir via CLI flags — the repo's megacode.spec is
  Windows-only (winpty collection), and onefile would unpack to /tmp at
  every start, which fails on noexec-/tmp air-gapped hosts. Building inside
  docker/Dockerfile.astra-build (debian:10 base) matches the target's
  glibc exactly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IS_WIN = sys.platform == "win32"
VENV_BIN = os.path.join(ROOT, ".venv",
                        "Scripts" if IS_WIN else "bin", "python" +
                        (".exe" if IS_WIN else ""))


def main() -> int:
    python = VENV_BIN if os.path.exists(VENV_BIN) else sys.executable
    print(f"Using interpreter: {python}")

    for folder in ("build", "dist"):
        path = os.path.join(ROOT, folder)
        if os.path.isdir(path):
            print(f"Cleaning {path}")
            try:
                shutil.rmtree(path)
            except OSError as exc:
                # Windows sometimes keeps a transient handle on the folder
                # (Explorer, antivirus). Retry once, then continue: PyInstaller
                # overwrites the contents anyway.
                print(f"  rmtree failed ({exc}); retrying...")
                time.sleep(2)
                try:
                    shutil.rmtree(path)
                except OSError:
                    print(f"  could not fully remove {path}; continuing")

    if IS_WIN:
        spec = os.path.join(ROOT, "megacode.spec")
        cmd = [python, "-m", "PyInstaller", "--noconfirm", "--clean", spec]
        out = os.path.join(ROOT, "dist", "MegaCode.exe")
    else:
        cmd = [
            python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--windowed", "--onedir", "--name", "MegaCode",
            "--paths", os.path.join(ROOT, "src"),
            os.path.join(ROOT, "run_megacode.py"),
        ]
        out = os.path.join(ROOT, "dist", "MegaCode", "MegaCode")
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    if os.path.exists(out):
        size_mb = sum(
            os.path.getsize(os.path.join(dirpath, name))
            for dirpath, _dirs, files in os.walk(os.path.dirname(out))
            for name in files
        ) / (1024 * 1024)
        print(f"\nBuild OK: {out} ({size_mb:.1f} MB)")
        return 0
    print(f"\nBuild FAILED: {out} not found in dist/")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
