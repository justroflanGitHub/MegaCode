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

kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wintypes.DWORD

kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL


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


def is_pid_alive(pid: int) -> bool:
    """True when ``pid`` names a live process.

    Used by the sync link's election: a QLockFile whose holder died is safe
    to take over (removeStaleLockFile), a live one never is -- correctness
    over availability. OpenProcess failing for a same-user pid means the
    process is gone; GetExitCodeProcess still reporting STILL_ACTIVE (259)
    is the classic zombie check.
    """
    if pid <= 0:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_session_id() -> str:
    """This process's Terminal Services session id (fast user switching and
    RDP give one user several sessions; the sync link must not cross them)."""
    sid = wintypes.DWORD(0)
    if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid)):
        return str(sid.value)
    return "0"


def is_process_elevated() -> bool:
    """True when running elevated (Administrator).

    The sync link refuses to start elevated unless the user forces it:
    named pipes don't respect UIPI, so a normal-integrity instance's keys
    could otherwise drive an elevated instance's shells.
    """
    class TOKEN_ELEVATION(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    handle = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(handle)):
        return False
    try:
        elev = TOKEN_ELEVATION()
        size = wintypes.DWORD(0)
        ok = advapi32.GetTokenInformation(
            handle, TokenElevation, ctypes.byref(elev),
            ctypes.sizeof(elev), ctypes.byref(size),
        )
        return bool(ok and elev.TokenIsElevated)
    finally:
        kernel32.CloseHandle(handle)


def harden_user_only(path: str) -> bool:
    """Best-effort DACL: the file's owner + SYSTEM, full control only.

    Applies to the sync link's secret/settings files. The user-profile
    directory ACL is the primary boundary; this explicit ACE list is
    defense in depth. Failure is non-fatal (some filesystems, e.g. network
    redirection, reject SetNamedSecurityInfo) -- callers log and move on.
    """
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.AllocateAndInitializeSid.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte * 6), wintypes.BYTE,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.AllocateAndInitializeSid.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [wintypes.LPVOID]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.CopySid.argtypes = [
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
    ]
    advapi32.CopySid.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [wintypes.LPVOID]
    advapi32.FreeSid.restype = wintypes.LPVOID
    advapi32.InitializeAcl.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
    ]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAceEx.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID,
    ]
    advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.restype = wintypes.HANDLE

    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 1
    DACL_SECURITY_INFORMATION = 4
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    ACL_REVISION = 2
    FILE_ALL_ACCESS = 0x1F01FF
    ERROR_SUCCESS = 0
    NT_AUTHORITY = (ctypes.c_ubyte * 6)(0, 0, 0, 0, 0, 5)
    LOCAL_SYSTEM_RID = 18

    # the file's current owner SID (GetNamedSecurityInfo hands us buffers
    # it allocated: copy the SID out, free the descriptor)
    psid_owner = wintypes.LPVOID()
    psd = wintypes.LPVOID()
    if advapi32.GetNamedSecurityInfoW(
            path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
            ctypes.byref(psid_owner), None, None, None,
            ctypes.byref(psd)) != ERROR_SUCCESS:
        return False
    owner_len = advapi32.GetLengthSid(psid_owner)
    owner_sid = ctypes.create_string_buffer(max(owner_len, 1))
    ok = advapi32.CopySid(owner_len, owner_sid, psid_owner)
    kernel32.LocalFree(wintypes.HANDLE(psd.value))
    if not ok:
        return False

    # SYSTEM = S-1-5-18
    psys = wintypes.LPVOID()
    if not advapi32.AllocateAndInitializeSid(
            NT_AUTHORITY, 1, LOCAL_SYSTEM_RID, 0, 0, 0, 0, 0, 0, 0,
            ctypes.byref(psys)):
        return False

    # a fresh DACL: owner full access, SYSTEM full access, nobody else
    acl = ctypes.create_string_buffer(256)
    ok = (advapi32.InitializeAcl(acl, 256, ACL_REVISION)
          and advapi32.AddAccessAllowedAceEx(
              acl, ACL_REVISION, 0, FILE_ALL_ACCESS, owner_sid)
          and advapi32.AddAccessAllowedAceEx(
              acl, ACL_REVISION, 0, FILE_ALL_ACCESS, psys))
    if ok:
        # PROTECTED: without it Windows composes our explicit ACE list with
        # any inheritable ACEs from the profile directory, silently widening
        # the file beyond owner+SYSTEM
        ok = advapi32.SetNamedSecurityInfoW(
            path, SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None,
        ) == ERROR_SUCCESS
    advapi32.FreeSid(psys)
    return bool(ok)


# --- cross-platform additions (POSIX twins live in posix_helpers) ------------
def user_name() -> str:
    """The login name (hashes into the sync rendezvous name)."""
    return os.environ.get("USERNAME", "user")


def socket_path(name: str) -> str:
    """Windows named pipes need no filesystem path -- the bare name IS the
    rendezvous (Qt maps it to ``\\\\.\\pipe\\<name>``)."""
    return name


def is_network_dir(path: str) -> bool:
    """True for UNC paths (``\\\\server\\share``): the sync state must not
    live on a network profile (no reliable byte-range locking, and the
    secret would sit on a share)."""
    return str(path).startswith("\\\\")
