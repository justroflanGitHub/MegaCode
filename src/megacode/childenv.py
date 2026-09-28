"""Environment hygiene for child processes spawned by the app.

The PyInstaller (onedir) bootloader prepends the bundle directory to
``LD_LIBRARY_PATH`` and leaves it set for the life of the process: the
app's own late ``dlopen()``s (Qt plugins, extension modules) must resolve
to the bundled ``libstdc++.so.6`` / libssl / ..., not the system ones.
Every child the app spawns inherits that pointer, and the bundled
libraries were built against the docker build host's older glibc -- on a
target with newer system libraries any C++ tool run inside the terminal
dies on load::

    /usr/bin/qpdf: /opt/MegaCode/libstdc++.so.6: version
        `GLIBCXX_3.4.32' not found (required by /usr/bin/qpdf)

So every spawn site (the PTY, external terminals, the chat CLI) hands its
child a scrubbed copy: bundle entries are dropped from
``LD_LIBRARY_PATH``, user-set entries survive untouched, and the app's
own environment is never modified. Running from source is a no-op --
nothing points into a bundle, the env passes through as-is.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Mapping, Optional


def bundle_dirs() -> List[str]:
    """Realpaths that mean "inside the frozen bundle" (empty from source).

    Frozen: the executable's directory (onedir -- where libstdc++.so.6
    sits next to the binary) plus ``sys._MEIPASS`` (onefile extraction
    dir; the bootloader points it at the bundle dir in onedir mode too).
    """
    if not getattr(sys, "frozen", False):
        return []
    dirs = {os.path.dirname(os.path.abspath(sys.executable))}
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.add(meipass)
    return [os.path.realpath(d) for d in dirs]


def _inside(entry: str, roots: List[str]) -> bool:
    real = os.path.realpath(entry)
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def drop_bundle_entries(value: str) -> Optional[str]:
    """``value`` (an ``os.pathsep``-joined path list) minus bundle entries.

    None means nothing survives -- remove the variable from the child env
    entirely rather than setting it empty. Empty entries (a bare separator
    means "also the cwd" to ld.so) are not bundle paths and are preserved
    verbatim, like any other user-set entry.
    """
    if not value:
        return None
    roots = bundle_dirs()
    if not roots:
        return value
    kept = [e for e in value.split(os.pathsep)
            if not e or not _inside(e, roots)]
    return os.pathsep.join(kept) if kept else None


def scrub_child_env(env: Mapping[str, str]) -> Dict[str, str]:
    """A child-safe copy of ``env`` (see the module docstring)."""
    out = dict(env)
    if "LD_LIBRARY_PATH" in out:
        cleaned = drop_bundle_entries(out["LD_LIBRARY_PATH"] or "")
        if cleaned is None:
            del out["LD_LIBRARY_PATH"]
        else:
            out["LD_LIBRARY_PATH"] = cleaned
    return out
