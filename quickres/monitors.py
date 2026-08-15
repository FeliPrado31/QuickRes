import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass

from quickres.config import APP_DIR

user32 = ctypes.windll.user32
setupapi = ctypes.windll.setupapi
cfgmgr32 = ctypes.windll.cfgmgr32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

GUID_DEVCLASS_MONITOR = "4d36e96e-e325-11ce-bfc1-08002be10318"

DIGCF_PRESENT = 0x00000002
SPDRP_DEVICEDESC = 0x00000000
SPDRP_FRIENDLYNAME = 0x0000000C
CR_SUCCESS = 0
CM_PROB_DISABLED = 22
CM_LOCATE_DEVNODE_NORMAL = 0
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

# SetupAPI instance IDs are vendor/EDID-derived (e.g.
# "DISPLAY\DEL4110\5&2e2fefea&0&UID1078018") and normally only ever contain
# these characters. This is interpolated into a double-quoted elevated-helper
# command line (run_elevated_monitor_op) with no escaping, so a value outside
# this shape is refused rather than risking a quote-breaking/argv-corrupting
# injection into an admin-privileged process launch.
_SAFE_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_&\\]+$")

# Distinct from an ordinary failure: WaitForSingleObject timing out means the
# elevated process (likely still waiting on a slow UAC prompt) is UNKNOWN,
# not failed — it could still disable/enable the device moments later, off
# in the background, after this function has already returned. Callers must
# not treat this the same as a confirmed failure (e.g. must not clear a
# crash-recovery flag on the strength of it).
TIMEOUT_MESSAGE = "Elevated operation timed out (still running in the background, outcome unknown)"

# Prefix for the "device no longer resolvable" failure (unplugged, replaced,
# or its instance id changed since the flag was written). Unlike other
# failures, this one means there is nothing left to protect against, so
# callers may treat it as safe to clear a stale crash-recovery flag over
# rather than leaving the user permanently stuck retrying.
DEVICE_NOT_FOUND_PREFIX = "Could not locate device"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _make_guid(guid_str: str) -> GUID:
    u = uuid.UUID(guid_str)
    g = GUID()
    ctypes.memmove(ctypes.byref(g), u.bytes_le, 16)
    return g


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hKeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


@dataclass
class Monitor:
    instance_id: str
    friendly_name: str
    is_enabled: bool
    is_primary: bool


# HDEVINFO is a pointer-sized opaque handle. ctypes' default restype (c_int,
# 32-bit) would truncate/corrupt it on 64-bit Windows, and passing it back
# into later calls without a declared argtype risks an OverflowError since
# the raw address is usually well outside the 32-bit range. Declare explicit
# prototypes for every SetupAPI/cfgmgr32 entry point that touches an HDEVINFO
# or DEVINST handle so these stay correct on 64-bit builds.
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA),
    wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

cfgmgr32.CM_Get_DevNode_Status.restype = wintypes.DWORD
cfgmgr32.CM_Get_DevNode_Status.argtypes = [
    ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
    wintypes.DWORD, wintypes.ULONG,
]
cfgmgr32.CM_Locate_DevNodeW.restype = wintypes.DWORD
cfgmgr32.CM_Locate_DevNodeW.argtypes = [
    ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG,
]
cfgmgr32.CM_Enable_DevNode.restype = wintypes.DWORD
cfgmgr32.CM_Enable_DevNode.argtypes = [wintypes.DWORD, wintypes.ULONG]
cfgmgr32.CM_Disable_DevNode.restype = wintypes.DWORD
cfgmgr32.CM_Disable_DevNode.argtypes = [wintypes.DWORD, wintypes.ULONG]

shell32.ShellExecuteExW.restype = wintypes.BOOL
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]

kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def _normalize_interface_id(device_id: str) -> str:
    """Convert an EnumDisplayDevices interface path into a SetupAPI instance id.

    e.g. ``\\\\?\\DISPLAY#DEL4110#5&2e2fefea&0&UID1078018#{guid}``
    -> ``DISPLAY\\DEL4110\\5&2e2fefea&0&UID1078018``
    """
    s = device_id
    if s.startswith("\\\\?\\"):
        s = s[4:]
    # Strip the trailing interface-class GUID segment, e.g. #{...}
    hash_index = s.rfind("#{")
    if hash_index != -1 and s.endswith("}"):
        s = s[:hash_index]
    return s.replace("#", "\\")


def _get_attached_monitor_states() -> dict:
    states = {}
    adapter = DISPLAY_DEVICEW()
    adapter.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    adapter_index = 0
    while user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
        if adapter.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            monitor = DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            monitor_index = 0
            while user32.EnumDisplayDevicesW(
                adapter.DeviceName, monitor_index, ctypes.byref(monitor),
                EDD_GET_DEVICE_INTERFACE_NAME
            ):
                instance_id = _normalize_interface_id(monitor.DeviceID)
                is_primary = bool(adapter.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)
                states[instance_id] = is_primary
                monitor_index += 1
                monitor = DISPLAY_DEVICEW()
                monitor.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        adapter_index += 1
        adapter = DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    return states


def enumerate_monitors() -> list:
    monitors = []
    class_guid = _make_guid(GUID_DEVCLASS_MONITOR)
    hdevinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(class_guid), None, None, DIGCF_PRESENT
    )
    if hdevinfo == INVALID_HANDLE_VALUE or not hdevinfo:
        return monitors

    try:
        attached_states = _get_attached_monitor_states()

        index = 0
        devinfo = SP_DEVINFO_DATA()
        devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        while setupapi.SetupDiEnumDeviceInfo(hdevinfo, index, ctypes.byref(devinfo)):
            instance_id = _get_device_instance_id(hdevinfo, devinfo)
            friendly_name = _get_device_property(hdevinfo, devinfo, SPDRP_FRIENDLYNAME)
            if not friendly_name:
                friendly_name = _get_device_property(hdevinfo, devinfo, SPDRP_DEVICEDESC)
            if not friendly_name:
                friendly_name = instance_id or "Unknown monitor"

            try:
                is_enabled = _is_devnode_enabled(devinfo.DevInst)
            except OSError:
                # Can't determine this device's current status right now —
                # omit it rather than guess. Callers that need this specific
                # device (the crash-recovery re-check in gui.py) correctly
                # treat "not found in the list" as "can't confirm" and fail
                # safe, instead of trusting a wrong default.
                index += 1
                devinfo = SP_DEVINFO_DATA()
                devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                continue

            is_primary = attached_states.get(instance_id, False)

            monitors.append(Monitor(
                instance_id=instance_id,
                friendly_name=friendly_name,
                is_enabled=is_enabled,
                is_primary=is_primary,
            ))

            index += 1
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)

    return monitors


def _get_device_instance_id(hdevinfo, devinfo) -> str:
    required_size = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceInstanceIdW(
        hdevinfo, ctypes.byref(devinfo), None, 0, ctypes.byref(required_size)
    )
    if required_size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(required_size.value)
    if setupapi.SetupDiGetDeviceInstanceIdW(
        hdevinfo, ctypes.byref(devinfo), buf, required_size.value, ctypes.byref(required_size)
    ):
        return buf.value
    return ""


def _get_device_property(hdevinfo, devinfo, prop_id) -> str:
    required_size = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdevinfo, ctypes.byref(devinfo), prop_id, None, None, 0, ctypes.byref(required_size)
    )
    if required_size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(required_size.value // 2 + 1)
    if setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdevinfo, ctypes.byref(devinfo), prop_id, None,
        ctypes.byref(buf), required_size.value, ctypes.byref(required_size)
    ):
        return buf.value
    return ""


def _is_devnode_enabled(devinst) -> bool:
    status = wintypes.ULONG(0)
    problem_number = wintypes.ULONG(0)
    result = cfgmgr32.CM_Get_DevNode_Status(
        ctypes.byref(status), ctypes.byref(problem_number), devinst, 0
    )
    if result != CR_SUCCESS:
        # A CfgMgr32 failure here does NOT mean the device is enabled — it
        # means we genuinely don't know. Silently defaulting to "enabled"
        # would be dangerous: gui.py's crash-recovery false-negative
        # re-check trusts this as ground truth to decide whether to clear
        # pending_restore.json, and a wrong "enabled" reading for a monitor
        # that's actually still disabled would erase its recovery flag.
        # Raise instead so callers can fail safe (enumerate_monitors omits
        # the device rather than guessing).
        raise OSError(f"CM_Get_DevNode_Status failed (CONFIGRET error {result})")
    return problem_number.value != CM_PROB_DISABLED


def _set_devnode_enabled(instance_id: str, enable: bool):
    """Only ever call this from within an already-elevated process."""
    devinst = wintypes.DWORD(0)
    result = cfgmgr32.CM_Locate_DevNodeW(
        ctypes.byref(devinst), instance_id, CM_LOCATE_DEVNODE_NORMAL
    )
    if result != CR_SUCCESS:
        return False, f"{DEVICE_NOT_FOUND_PREFIX} {instance_id} (CONFIGRET error {result})"

    if enable:
        result = cfgmgr32.CM_Enable_DevNode(devinst, 0)
    else:
        result = cfgmgr32.CM_Disable_DevNode(devinst, 0)

    if result == CR_SUCCESS:
        return True, f"Monitor {'enabled' if enable else 'disabled'} successfully"
    return False, (
        f"Failed to {'enable' if enable else 'disable'} monitor "
        f"(CONFIGRET error {result})"
    )


def _cleanup_stale_result_files(max_age_s: float = 300.0):
    """Best-effort sweep for orphaned monitor_op_result_*.json files.

    A timed-out run_elevated_monitor_op() call returns before it can clean
    up its own result_path, because the elevated helper may still be
    running and write it moments later — deleting it immediately would race
    the helper's own write. Each call after that is a good opportunity to
    sweep anything old enough to be a genuine orphan, so these don't
    accumulate in APP_DIR indefinitely.
    """
    try:
        now = time.time()
        for name in os.listdir(APP_DIR):
            if not (name.startswith("monitor_op_result_") and name.endswith(".json")):
                continue
            path = os.path.join(APP_DIR, name)
            try:
                if now - os.path.getmtime(path) > max_age_s:
                    os.remove(path)
            except Exception:
                pass
    except Exception:
        pass


def run_elevated_monitor_op(op: str, instance_id: str, timeout_s: float = 30.0):
    if not _SAFE_INSTANCE_ID_RE.match(instance_id):
        return False, f"Refusing to process unexpected device id: {instance_id!r}"

    _cleanup_stale_result_files()
    result_path = os.path.join(
        APP_DIR, f"monitor_op_result_{os.getpid()}_{int(time.time() * 1000)}.json"
    )

    if getattr(sys, "frozen", False):
        target = sys.executable
        params = f'--monitor-op {op} --instance-id "{instance_id}" --result-file "{result_path}"'
    else:
        target = sys.executable
        script_path = os.path.abspath(sys.argv[0])
        params = (
            f'"{script_path}" --monitor-op {op} '
            f'--instance-id "{instance_id}" --result-file "{result_path}"'
        )

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = target
    sei.lpParameters = params
    sei.lpDirectory = None
    sei.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return False, "Elevation was cancelled or failed"

    wait_result = kernel32.WaitForSingleObject(sei.hProcess, int(timeout_s * 1000))
    if wait_result == WAIT_TIMEOUT:
        kernel32.CloseHandle(sei.hProcess)
        return False, TIMEOUT_MESSAGE
    if wait_result != WAIT_OBJECT_0:
        # A genuine wait error (e.g. WAIT_FAILED) is a confirmed, immediate
        # failure — distinct from WAIT_TIMEOUT, which just means the process
        # might still be running. Don't fold this into TIMEOUT_MESSAGE, or
        # callers would wrongly treat a known failure as "unresolved, don't
        # touch the recovery flag".
        kernel32.CloseHandle(sei.hProcess)
        return False, f"Elevated operation wait failed (code {wait_result})"

    exit_code = wintypes.DWORD(0)
    kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
    kernel32.CloseHandle(sei.hProcess)

    if not os.path.exists(result_path):
        return False, "Elevated helper did not report a result"

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False, "Elevated helper wrote an unreadable result"
    finally:
        try:
            os.remove(result_path)
        except Exception:
            pass

    return bool(data.get("ok", False)), data.get("message", "")


def disable_monitor(instance_id: str):
    return run_elevated_monitor_op("disable", instance_id)


def enable_monitor(instance_id: str):
    return run_elevated_monitor_op("enable", instance_id)


def run_elevated_worker_op(op: str, instance_id: str):
    return _set_devnode_enabled(instance_id, enable=(op == "enable"))


class PendingDisableGuard:
    """Framework-agnostic 10s confirm-or-revert timer.

    `schedule_fn(delay_seconds, callback)` should return an opaque handle
    (e.g. Tkinter's `.after()` id); `cancel_fn(handle)` should cancel it
    (e.g. Tkinter's `.after_cancel()`). This lets it be unit tested with a
    fake scheduler instead of a real Tk mainloop.
    """

    def __init__(self, revert_callback, schedule_fn, cancel_fn, timeout_seconds=10):
        self._revert_callback = revert_callback
        self._schedule_fn = schedule_fn
        self._cancel_fn = cancel_fn
        self._timeout_seconds = timeout_seconds
        self._handle = None
        self._confirmed = False
        self._reverted = False

    def start(self):
        self._confirmed = False
        self._reverted = False
        self._handle = self._schedule_fn(self._timeout_seconds, self._on_timeout)

    def confirm(self):
        if self._reverted:
            return
        self._confirmed = True
        if self._handle is not None:
            self._cancel_fn(self._handle)
            self._handle = None

    def _on_timeout(self):
        if self._confirmed or self._reverted:
            return
        self._reverted = True
        self._revert_callback()

    @property
    def confirmed(self):
        return self._confirmed

    @property
    def reverted(self):
        return self._reverted