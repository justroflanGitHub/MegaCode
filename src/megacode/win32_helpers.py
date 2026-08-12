"""Thin Win32 helpers built on ``ctypes`` (no ``pywin32`` dependency).

Only the calls needed for discovering, measuring and moving windows are
exposed. Everything here targets *physical* pixels, which matches the units
used by :mod:`megacode.layouts`.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import List, Optional

from .layouts import Rect

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- constants --------------------------------------------------------------
GW_OWNER = 4
MONITOR_DEFAULTTONEAREST = 0x00000002
SPI_GETWORKAREA = 0x0030
SW_RESTORE = 9
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# --- function prototypes ----------------------------------------------------
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.MoveWindow.argtypes = [
    wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL,
]
user32.MoveWindow.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE

user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL

user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
]
user32.SystemParametersInfoW.restype = wintypes.BOOL

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


# --- process helpers --------------------------------------------------------
def get_process_path(pid: int) -> Optional[str]:
    """Return the full executable path for ``pid``, or ``None`` on failure."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def get_process_name(pid: int) -> Optional[str]:
    """Return the lower-cased executable file name (e.g. ``windowsterminal.exe``)."""
    path = get_process_path(pid)
    if not path:
        return None
    return os.path.basename(path).lower()


# --- window enumeration -----------------------------------------------------
def enum_top_level_windows() -> List[tuple[int, int]]:
    """Return ``[(hwnd, pid), ...]`` for visible, unowned top-level windows."""
    results: List[tuple[int, int]] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True  # owned popup / dialog -> skip
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((hwnd, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return results


def find_terminal_windows() -> List[int]:
    """Return hwnds of top-level Windows Terminal windows (``WindowsTerminal.exe``)."""
    hwnds: List[int] = []
    for hwnd, pid in enum_top_level_windows():
        name = get_process_name(pid)
        if name and name == "windowsterminal.exe":
            hwnds.append(hwnd)
    return hwnds


# --- geometry ---------------------------------------------------------------
def _rect_from_win32(rc: wintypes.RECT) -> Rect:
    return Rect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def primary_work_area() -> Rect:
    """Work area of the primary monitor (excludes the taskbar)."""
    rc = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)
    return _rect_from_win32(rc)


def get_work_area(hwnd: int) -> Rect:
    """Work area of the monitor that contains ``hwnd`` (nearest, by default)."""
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return _rect_from_win32(info.rcWork)
    return primary_work_area()


def get_window_rect(hwnd: int) -> Optional[Rect]:
    """Current outer rectangle of ``hwnd`` in physical pixels, or ``None``."""
    rc = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return _rect_from_win32(rc)
    return None


def move_window(hwnd: int, rect: Rect) -> None:
    """Move and resize ``hwnd`` to ``rect``.

    A minimized window ignores geometry changes, so it is restored first. The
    call is best-effort: Windows Terminal enforces a minimum window size, so on
    very small screens a cell may end up larger than requested.
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.MoveWindow(hwnd, rect.x, rect.y, rect.w, rect.h, True)


def close_window(hwnd: int) -> None:
    """Politely ask ``hwnd`` to close (used by the self-test cleanup)."""
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
