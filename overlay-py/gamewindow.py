"""Locate the League of Legends game window.

The augment cards are laid out relative to the *game window*, not the monitor, so
windowed / borderless clients at any size can reuse the same normalised layout.
"""

import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.windll.user32

GAME_CLASSES = ("RiotWindowClass",)
GAME_TITLES = ("League of Legends", "League of Legends (TM) Client")
GAME_PROCESS = "league of legends.exe"


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def find_client_rect():
    """Return (x, y, width, height) of the game window's client area, or None."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = _class_name(hwnd)
        title = _title(hwnd)
        process = _process_name(hwnd)
        if cls in GAME_CLASSES or process == GAME_PROCESS:
            rect = wt.RECT()
            if user32.GetClientRect(hwnd, ctypes.byref(rect)):
                point = wt.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(point))
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 640 and height > 480:
                    found.append((point.x, point.y, width, height))
        return True

    user32.EnumWindows(enum_proc, 0)
    if not found:
        return None
    # Prefer the largest matching window (the game itself).
    found.sort(key=lambda r: r[2] * r[3], reverse=True)
    return found[0]


def is_overlay_foreground():
    """True when League Soraka's own Tk window is the foreground window."""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        title = _title(hwnd).lower()
        return "league soraka" in title
    except Exception:
        return False


def foreground_info():
    """Return (class_name, title) for diagnostics."""
    try:
        hwnd = user32.GetForegroundWindow()
        return (_class_name(hwnd), _title(hwnd), _process_name(hwnd)) if hwnd else (None, None, None)
    except Exception:
        return (None, None)


def _process_name(hwnd):
    try:
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def is_foreground():
    """True when the League game window is the foreground window."""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        if _class_name(hwnd) in GAME_CLASSES:
            return True
        return _process_name(hwnd) == GAME_PROCESS
    except Exception:
        return False


def find_monitor_rect():
    """Fallback: the primary monitor in physical pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    return (0, 0, width, height)
