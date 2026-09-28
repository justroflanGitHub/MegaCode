"""Platform facade: the Win32 helpers on Windows, their POSIX twins on Linux.

Import this module -- never ``win32_helpers``/``posix_helpers`` directly --
for everything the sync link and launcher need across platforms. The
Windows module executes ``ctypes.WinDLL`` at import time, so a direct
import crashes on Linux before any code runs.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .win32_helpers import (
        close_window,
        enum_top_level_windows,
        find_terminal_windows,
        get_process_name,
        get_process_path,
        get_window_rect,
        get_work_area,
        harden_user_only,
        is_network_dir,
        is_pid_alive,
        is_process_elevated,
        move_window,
        primary_work_area,
        process_session_id,
        socket_path,
        user_name,
    )
else:
    from .posix_helpers import (  # noqa: F401 (re-export facade)
        close_window,
        enum_top_level_windows,
        find_terminal_windows,
        get_process_name,
        get_process_path,
        get_window_rect,
        get_work_area,
        harden_user_only,
        is_network_dir,
        is_pid_alive,
        is_process_elevated,
        move_window,
        primary_work_area,
        process_session_id,
        socket_path,
        user_name,
    )
