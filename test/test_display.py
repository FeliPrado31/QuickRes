import ctypes
import unittest
from unittest.mock import patch

from quickres import display


class FakeUser32:
    def __init__(self, *, refresh_rate=0, enum_result=True, change_results=(0, 0)):
        self.refresh_rate = refresh_rate
        self.enum_result = enum_result
        self.change_results = list(change_results)
        self.enum_calls = []
        self.change_calls = []

    def EnumDisplaySettingsW(self, device_name, mode_num, devmode_ptr):
        self.enum_calls.append((device_name, mode_num))
        if self.enum_result:
            devmode = ctypes.cast(devmode_ptr, ctypes.POINTER(display.DEVMODE)).contents
            devmode.dmDisplayFrequency = self.refresh_rate
        return self.enum_result

    def ChangeDisplaySettingsW(self, devmode_ptr, flags):
        mode = None
        if devmode_ptr:
            devmode = ctypes.cast(devmode_ptr, ctypes.POINTER(display.DEVMODE)).contents
            mode = {
                "width": devmode.dmPelsWidth,
                "height": devmode.dmPelsHeight,
                "frequency": devmode.dmDisplayFrequency,
                "fields": devmode.dmFields,
            }
        self.change_calls.append((mode, flags))
        return self.change_results.pop(0)


class SetResolutionTests(unittest.TestCase):
    def test_preserves_current_refresh_rate(self):
        user32 = FakeUser32(refresh_rate=240)

        with patch.object(display, "user32", user32):
            ok, _ = display.set_resolution(1568, 1080)

        self.assertTrue(ok)
        self.assertEqual(user32.enum_calls, [(None, display.ENUM_CURRENT_SETTINGS)])
        mode, flags = user32.change_calls[0]
        self.assertEqual(mode["width"], 1568)
        self.assertEqual(mode["height"], 1080)
        self.assertEqual(mode["frequency"], 240)
        self.assertEqual(
            mode["fields"],
            display.DM_PELSWIDTH | display.DM_PELSHEIGHT | display.DM_DISPLAYFREQUENCY,
        )
        self.assertEqual(flags, display.CDS_UPDATEREGISTRY | display.CDS_NORESET)
        self.assertEqual(user32.change_calls[1], (None, display.CDS_RESET))

    def test_retries_without_refresh_rate_when_driver_rejects_it(self):
        user32 = FakeUser32(refresh_rate=240, change_results=(1, 0, 0))

        with patch.object(display, "user32", user32):
            ok, _ = display.set_resolution(1568, 1080)

        self.assertTrue(ok)
        first_mode, _ = user32.change_calls[0]
        second_mode, _ = user32.change_calls[1]
        self.assertTrue(first_mode["fields"] & display.DM_DISPLAYFREQUENCY)
        self.assertEqual(second_mode["fields"], display.DM_PELSWIDTH | display.DM_PELSHEIGHT)
        self.assertEqual(user32.change_calls[2], (None, display.CDS_RESET))

    def test_applies_resolution_without_refresh_rate_when_it_cannot_be_read(self):
        user32 = FakeUser32(enum_result=False)

        with patch.object(display, "user32", user32):
            ok, _ = display.set_resolution(1568, 1080)

        self.assertTrue(ok)
        mode, _ = user32.change_calls[0]
        self.assertEqual(mode["fields"], display.DM_PELSWIDTH | display.DM_PELSHEIGHT)
        self.assertEqual(len(user32.change_calls), 2)


if __name__ == "__main__":
    unittest.main()