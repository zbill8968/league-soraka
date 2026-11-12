"""Global hotkeys via RegisterHotKey on a dedicated message thread.

Polling GetAsyncKeyState proved unreliable for keys pressed inside the game, so the
overlay registers real hotkeys instead. RegisterHotKey needs a thread message loop;
this module owns it and exposes a thread-safe queue of hotkey ids.
"""

import ctypes
import ctypes.wintypes as wt
import queue
import threading

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32


class Hotkeys:
    def __init__(self):
        self.queue = queue.Queue()
        self._thread = None
        self._ready = threading.Event()
        self.registered = {}
        self.failed = {}

    def start(self, keys):
        """keys: {id: virtual_key} or {id: (virtual_key, modifiers)}."""
        self._keys = {}
        for hid, spec in dict(keys).items():
            if isinstance(spec, (tuple, list)):
                vk, mods = spec[0], spec[1]
            else:
                vk, mods = spec, 0
            self._keys[hid] = (int(vk), int(mods))
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return {hid: (hid in self.registered) for hid in self._keys}

    def _run(self):
        try:
            for hid, (vk, mods) in self._keys.items():
                if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                    self.registered[hid] = (vk, mods)
                else:
                    self.failed[hid] = ctypes.windll.kernel32.GetLastError()
        finally:
            self._ready.set()

        msg = wt.MSG()
        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                self.queue.put(int(msg.wParam))

    def stop(self):
        for hid in list(self.registered):
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self.registered.clear()

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                return items
