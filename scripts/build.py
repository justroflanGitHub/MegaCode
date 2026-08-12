"""Build the standalone MegaCode.exe with PyInstaller.

Run from the project root with the project venv active, or directly:

    .venv/Scripts/python.exe scripts/build.py

The venv interpreter is used automatically if present, otherwise the current
interpreter. Previous ``build/`` and ``dist/`` folders are removed first for a
clean, reproducible artifact.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def main() -> int:
    python = VENV_PY if os.path.exists(VENV_PY) else sys.executable
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

    spec = os.path.join(ROOT, "megacode.spec")
    cmd = [python, "-m", "PyInstaller", "--noconfirm", "--clean", spec]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    out = os.path.join(ROOT, "dist", "MegaCode.exe")
    if os.path.exists(out):
        size_mb = os.path.getsize(out) / (1024 * 1024)
        print(f"\nBuild OK: {out} ({size_mb:.1f} MB)")
        return 0
    print("\nBuild FAILED: MegaCode.exe not found in dist/")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
