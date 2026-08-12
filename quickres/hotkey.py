import ctypes
from ctypes import wintypes
import threading

from quickres.config import log_msg
from quickres.display import set_resolution

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 1

HOTKEY_OPTIONS = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22, "`": 0xC0,
}


class HotkeyToggle:
    def __init__(self, key_name, native_res, stretched_res, on_status):
        self.key_name = key_name
        self.vk_code = HOTKEY_OPTIONS.get(key_name, 0x75)
        self.native_res = native_res
        self.stretched_res = stretched_res
        self.on_status = on_status
        self._thread = None
        self._thread_id = None
        self.is_stretched = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, self.vk_code):
            self.on_status(f"Could not register {self.key_name}, it may be in use")
            self._thread_id = None
            return

        self.on_status(f"Hotkey active: {self.key_name} toggles resolution")
        log_msg(f"Hotkey {self.key_name} registered")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                self.is_stretched = not self.is_stretched
                target = self.stretched_res if self.is_stretched else self.native_res
                ok, result_msg = set_resolution(*target)
                self.on_status(result_msg)
                log_msg(f"Hotkey toggle -> {target[0]}x{target[1]} ({'ok' if ok else 'fail'})")

        user32.UnregisterHotKey(None, HOTKEY_ID)
        self._thread_id = None