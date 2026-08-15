import json
import os
import re
import sys
import threading
import time
import webbrowser

from quickres import __version__
from quickres.config import (
    load_config, update_config,
    save_pending_restore, load_pending_restore, clear_pending_restore,
)
from quickres.display import (
    QUICK_LIST, set_resolution, get_supported_resolutions,
    get_current_resolution, detect_gpu_vendors,
    open_nvidia_control_panel, open_amd_software, open_intel_graphics_software,
)
from quickres.hotkey import HotkeyToggle, HOTKEY_OPTIONS
from quickres import monitors as monitors_mod
from quickres.monitors import PendingDisableGuard
from quickres.updater import fetch_version_info, apply_update

MAX_CUSTOM_RES = 6
REVERT_TIMEOUT_SECONDS = 10

FAQ_ITEMS = [
    {"q": "Why does it say \"Resolution not found\"?", "a": (
        "QuickRes checks your GPU driver's list of registered resolutions "
        "before switching. If a resolution isn't on that list, Windows has "
        "no way to switch to it yet.\n\n"
        "When this happens, QuickRes shows a popup with a button for your "
        "graphics software (NVIDIA Control Panel, AMD Software, or Intel "
        "Graphics Software, based on what's actually detected in your PC). "
        "Click it, then add the resolution as a custom resolution there:\n\n"
        "NVIDIA: Display > Change Resolution > Customize > Create Custom Resolution\n\n"
        "AMD: Display > Custom Resolutions > Create New\n\n"
        "Intel: Display > General > Resolution > + (create custom profile)"
    )},
    {"q": "Nothing happens when I click a button?",
     "a": "Try running QuickRes as Administrator."},
    {"q": "Screen looks wrong after switching? (valorant)", "a": (
        "Make sure Valorant is on the fill/agent select screen before you "
        "switch to a stretched res. You have to do this every match, since "
        "loading into a new game resets your resolution and switching too "
        "early will give you black bars. Set a hotkey below so you can "
        "toggle between native and stretched with one keypress instead of "
        "clicking through the app."
    )},
    {"q": "What does the hotkey do?", "a": (
        "Once you press Start Hotkey, the key you picked toggles your "
        "display between the Native and Stretched resolutions set above. "
        "Press it once on the fill/agent select screen to go stretched, "
        "press it again after the match to go back to native."
    )},
]


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _ratio_label(w, h):
    g = _gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _classify(w, h, native):
    if native and (w, h) == native:
        return "Native"
    if native and h == native[1] and w < native[0]:
        return "Stretched"
    if native and w * h < 0.6 * native[0] * native[1]:
        return "Low"
    if native and w > native[0]:
        return "Wide"
    return "Stretched"


def _parse_res(text):
    match = re.match(r"^(\d{2,5})\s*[x, ]\s*(\d{2,5})$", (text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _schedule(seconds, callback):
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


def _cancel(timer):
    timer.cancel()


# The pywebview Window object must never be stored as an attribute on the
# Api instance: pywebview's WinForms/pythonnet backend recursively walks a
# js_api object's attributes when marshalling calls, and a Window's native
# WinForms control graph contains genuine circular references (SyncRoot,
# AccessibilityObject) that blow the recursion limit. Keep it in a plain
# module-level holder instead, well outside the js_api object's __dict__.
_window_holder = {"window": None}


class Api:
    def __init__(self):
        self.supported_resolutions = get_supported_resolutions()
        self.gpu_vendors = None
        self.hotkey_toggle = None
        self.hotkey_running = False
        self.pending_guard = None
        self._monitor_op_in_flight = False
        self._active_pending_instance_id = None
        self._confirm_dialog_instance_id = None

    def bind_window(self, window):
        _window_holder["window"] = window

    def _push_js(self, code):
        window = _window_holder["window"]
        if window is None:
            return
        try:
            window.evaluate_js(code)
        except Exception:
            pass

    def _push_status(self, message, kind="ok"):
        self._push_js(f"window.qrPushStatus({json.dumps(message)}, {json.dumps(kind)})")

    # ---- bootstrap ----
    def get_initial_state(self):
        cfg = load_config()
        native = get_current_resolution()
        native_str = f"{native[0]}x{native[1]}" if native else f"{QUICK_LIST[0][1]}x{QUICK_LIST[0][2]}"

        quick = []
        for _, w, h in QUICK_LIST:
            quick.append({
                "res": f"{w}x{h}", "width": w, "height": h,
                "tag": _classify(w, h, native), "ratio": _ratio_label(w, h),
            })

        pending = load_pending_restore()
        monitor_notice = None
        if pending and pending.get("instance_id"):
            monitor_notice = {
                "instance_id": pending["instance_id"],
                "friendly_name": pending.get("friendly_name", "a monitor"),
            }
            # Re-arm the same lock the old Tkinter startup path held: until
            # this gets reconciled (next list_monitors() call), refuse other
            # monitor actions rather than let the app forget a disable that
            # may still be unresolved from a previous session/crash.
            self._active_pending_instance_id = pending["instance_id"]

        saved_hotkey = cfg.get("hotkey", "F6")

        return {
            "theme": cfg.get("theme", "dark"),
            "quick": quick,
            "customs": cfg.get("custom_resolutions", []),
            "hotkeyOptions": list(HOTKEY_OPTIONS.keys()),
            "hotkey": saved_hotkey if saved_hotkey in HOTKEY_OPTIONS else "F6",
            "native": cfg.get("native_res", native_str),
            "stretched": cfg.get("stretched_res", "1568x1080"),
            "monitorNotice": monitor_notice,
            "faq": FAQ_ITEMS,
            "version": __version__,
        }

    def set_theme(self, theme):
        update_config({"theme": theme})
        return True

    def open_external(self, url):
        webbrowser.open(url)
        return True

    # ---- resolutions ----
    def pick_resolution(self, width, height):
        width, height = int(width), int(height)
        if (width, height) not in self.supported_resolutions:
            if self.gpu_vendors is None:
                self.gpu_vendors = detect_gpu_vendors()
            vendors = sorted(self.gpu_vendors) if self.gpu_vendors else ["nvidia", "amd", "intel"]
            return {"ok": False, "reason": "unsupported", "width": width, "height": height, "vendors": vendors}
        ok, msg = set_resolution(width, height)
        return {"ok": ok, "message": msg}

    def open_driver_panel(self, vendor):
        {
            "nvidia": open_nvidia_control_panel,
            "amd": open_amd_software,
            "intel": open_intel_graphics_software,
        }.get(vendor, lambda: None)()
        return True

    def add_custom(self, text):
        parsed = _parse_res(text)
        if not parsed:
            return {"ok": False, "message": "Format like 1440x1080"}
        width, height = parsed
        res_str = f"{width}x{height}"
        preset_strs = {f"{w}x{h}" for _, w, h in QUICK_LIST}
        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])
        if res_str not in preset_strs:
            if res_str in customs:
                customs.remove(res_str)
            customs.append(res_str)
            if len(customs) > MAX_CUSTOM_RES:
                customs = customs[-MAX_CUSTOM_RES:]
            update_config({"custom_resolutions": customs})
        result = self.pick_resolution(width, height)
        result["customs"] = customs
        return result

    def remove_custom(self, res_str):
        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])
        if res_str in customs:
            customs.remove(res_str)
            update_config({"custom_resolutions": customs})
        return customs

    # ---- hotkey ----
    def start_hotkey(self, key, native, stretched):
        native_t = _parse_res(native)
        stretched_t = _parse_res(stretched)
        if not native_t or not stretched_t:
            return {"ok": False, "message": "Native/Stretched must look like 1920x1080"}
        update_config({"hotkey": key, "native_res": native, "stretched_res": stretched})
        self.hotkey_toggle = HotkeyToggle(
            key_name=key, native_res=native_t, stretched_res=stretched_t,
            on_status=self._push_status,
        )
        self.hotkey_toggle.start()
        self.hotkey_running = True
        return {"ok": True}

    def stop_hotkey(self):
        if self.hotkey_toggle:
            if self.hotkey_toggle.is_stretched:
                set_resolution(*self.hotkey_toggle.native_res)
            self.hotkey_toggle.stop()
        self.hotkey_running = False
        return {"ok": True}

    # ---- updates ----
    def check_updates(self):
        if not getattr(sys, "frozen", False):
            return {"kind": "idle", "message": "Update checks only run in the built exe."}
        try:
            data = fetch_version_info()
        except Exception as e:
            return {"kind": "err", "message": f"Could not reach the update server: {e}"}

        latest_version = data.get("version", "")
        download_url = data.get("url", "")
        if not latest_version or not download_url:
            return {"kind": "err", "message": "Update server returned bad data."}

        def version_tuple(v):
            return tuple(int(p) for p in v.split("."))

        try:
            is_newer = version_tuple(latest_version) > version_tuple(__version__)
        except ValueError:
            return {"kind": "err", "message": "Update server returned bad data."}

        if not is_newer:
            return {"kind": "ok", "message": f"You're up to date ({__version__})."}

        return {
            "kind": "available",
            "message": f"QuickRes {latest_version} is available (you have {__version__}).",
            "version": latest_version,
            "url": download_url,
        }

    def confirm_update(self, download_url):
        try:
            apply_update(download_url)
        except SystemExit:
            # apply_update() ends with sys.exit(0) on success, but js_api
            # calls run off the main thread — a SystemExit raised there only
            # kills that worker thread, not the process. The staged update
            # .bat waits for this exe to actually exit before it can
            # rename/relaunch it, so force a real process exit here.
            os._exit(0)
        except Exception as e:
            # Anything apply_update() didn't already handle itself (e.g. it
            # failed to write update.bat, or Popen failed to launch it) must
            # NOT be swallowed by an unconditional exit — that would kill
            # the app while reporting nothing, indistinguishable from a
            # successful update. Report it and keep running instead.
            self._push_status(f"Update failed: {e}", "err")

    # ---- monitors ----
    def _find_monitor(self, instance_id):
        for m in monitors_mod.enumerate_monitors():
            if m.instance_id == instance_id:
                return m
        return None

    def _start_revert_guard(self, instance_id, friendly_name):
        self.pending_guard = PendingDisableGuard(
            revert_callback=lambda: self._auto_revert(instance_id, friendly_name),
            schedule_fn=_schedule,
            cancel_fn=_cancel,
            timeout_seconds=REVERT_TIMEOUT_SECONDS,
        )
        self.pending_guard.start()

    def _auto_revert(self, instance_id, friendly_name):
        self._monitor_op_in_flight = True
        try:
            ok, message = monitors_mod.enable_monitor(instance_id)
        except Exception as e:
            ok, message = False, f"Unexpected error: {e}"
        result = self._finish_revert(ok, message, friendly_name, instance_id)
        self._push_status(result["message"], result["kind"])
        self._push_js("window.qrOnMonitorsChanged && window.qrOnMonitorsChanged()")

    def _reconcile_stuck_pending(self):
        # A disable that hit TIMEOUT_MESSAGE leaves _active_pending_instance_id
        # set with no confirm UI driving it (the outcome wasn't known yet, so
        # none was shown) — actions stay locked until this resolves. By the
        # time Monitors is reopened, the elevated helper has almost certainly
        # finished one way or the other, so re-check now instead of leaving
        # the user stuck until an app restart.
        if self._active_pending_instance_id is None:
            return
        if self._confirm_dialog_instance_id is not None:
            return  # already a normal, resolved confirm state
        if self._monitor_op_in_flight:
            return

        pending = load_pending_restore()
        if pending is None:
            self._active_pending_instance_id = None
            return
        instance_id = pending.get("instance_id")
        friendly_name = pending.get("friendly_name", "the monitor")
        if not instance_id:
            clear_pending_restore()
            self._active_pending_instance_id = None
            return

        actual = self._find_monitor(instance_id)
        if actual is None:
            return  # still can't confirm — stay locked, fail safe
        if actual.is_enabled:
            clear_pending_restore()
            self._active_pending_instance_id = None
        else:
            self._active_pending_instance_id = instance_id
            self._confirm_dialog_instance_id = instance_id
            self._start_revert_guard(instance_id, friendly_name)

    def list_monitors(self):
        self._reconcile_stuck_pending()
        monitors = monitors_mod.enumerate_monitors()
        return {
            "monitors": [
                {
                    "instance_id": m.instance_id,
                    "friendly_name": m.friendly_name,
                    "is_enabled": m.is_enabled,
                    "is_primary": m.is_primary,
                }
                for m in monitors
            ],
            "op_in_flight": self._monitor_op_in_flight or self._active_pending_instance_id is not None,
            "confirming_instance_id": self._confirm_dialog_instance_id,
            "countdown": REVERT_TIMEOUT_SECONDS,
        }

    def monitor_action(self, instance_id):
        if self._monitor_op_in_flight or self._active_pending_instance_id is not None:
            return {"ok": False, "kind": "err", "message": "Another monitor operation is already in progress."}

        monitor = self._find_monitor(instance_id)
        if monitor is None:
            return {"ok": False, "kind": "err", "message": "Monitor no longer found."}

        if monitor.is_enabled:
            # Refuse to disable the only currently-enabled monitor — QuickRes
            # is for turning off the *extra* monitor(s), not the one the
            # user is actually looking at.
            other_enabled = [
                m for m in monitors_mod.enumerate_monitors()
                if m.is_enabled and m.instance_id != instance_id
            ]
            if not other_enabled:
                return {"ok": False, "kind": "err", "message": (
                    f"Refusing to disable {monitor.friendly_name} — it's the only "
                    f"enabled monitor. QuickRes is for disabling the extra "
                    f"monitor(s), not your only display."
                )}
            # Disabling is the risky direction: write the crash-recovery flag
            # before ever handing off to the elevated helper.
            if not save_pending_restore({
                "instance_id": instance_id,
                "friendly_name": monitor.friendly_name,
                "action": "disable",
                "started_at": time.time(),
            }):
                return {"ok": False, "kind": "err", "message": (
                    f"Could not write the crash-recovery flag — refusing to disable "
                    f"{monitor.friendly_name}. Check disk space/permissions and try again."
                )}
            self._monitor_op_in_flight = True
            try:
                ok, message = monitors_mod.disable_monitor(instance_id)
            except Exception as e:
                ok, message = False, f"Unexpected error: {e}"
            return self._finish_disable(ok, message, monitor)
        else:
            self._monitor_op_in_flight = True
            try:
                ok, message = monitors_mod.enable_monitor(instance_id)
            except Exception as e:
                ok, message = False, f"Unexpected error: {e}"
            return self._finish_enable(ok, message, monitor)

    def _finish_disable(self, ok, message, monitor):
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            # Genuinely unknown outcome, not a confirmed failure — stay
            # locked (via _active_pending_instance_id) but don't show a
            # confirm/revert dialog for a state we haven't confirmed yet.
            self._active_pending_instance_id = monitor.instance_id
            return {"ok": True, "kind": "idle", "message": (
                f"Still waiting on admin approval to disable {monitor.friendly_name} — "
                f"reopen Monitors in a moment to see the real state."
            )}
        if not ok:
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and not actual.is_enabled:
                ok = True
                message = f"{monitor.friendly_name} disabled (result was unconfirmed, verified by re-check)"
        if ok:
            self._active_pending_instance_id = monitor.instance_id
            self._confirm_dialog_instance_id = monitor.instance_id
            self._start_revert_guard(monitor.instance_id, monitor.friendly_name)
            return {"ok": True, "kind": "ok", "message": message}
        clear_pending_restore()
        return {"ok": False, "kind": "err", "message": message}

    def _finish_enable(self, ok, message, monitor):
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            return {"ok": True, "kind": "idle", "message": (
                f"Still waiting on admin approval to enable {monitor.friendly_name} — "
                f"check again in a moment."
            )}
        if not ok:
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                message = f"{monitor.friendly_name} enabled (result was unconfirmed, verified by re-check)"
        return {"ok": ok, "kind": "ok" if ok else "err", "message": message}

    def _finish_revert(self, ok, message, friendly_name, instance_id):
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            return {"ok": True, "kind": "idle", "message": (
                f"Still waiting on admin approval to revert {friendly_name} — check again in a moment."
            )}
        if not ok and message.startswith(monitors_mod.DEVICE_NOT_FOUND_PREFIX):
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._confirm_dialog_instance_id = None
            return {"ok": False, "kind": "err", "message": (
                f"{friendly_name} is no longer present on this system — cleared the stale recovery flag."
            )}
        status_text = f"Reverted {friendly_name}"
        if not ok:
            actual = self._find_monitor(instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                status_text = f"Reverted {friendly_name} (result was unconfirmed, verified by re-check)"
        if ok:
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._confirm_dialog_instance_id = None
            return {"ok": True, "kind": "ok", "message": status_text}
        return {"ok": False, "kind": "err", "message": (
            f"Failed to revert {friendly_name}: {message}. Please retry manually."
        )}

    def keep_disabled(self, instance_id):
        if self.pending_guard is not None:
            self.pending_guard.confirm()
            self.pending_guard = None
        clear_pending_restore()
        self._active_pending_instance_id = None
        self._confirm_dialog_instance_id = None
        return {"ok": True, "kind": "ok", "message": "Monitor kept disabled"}

    def revert_now(self, instance_id):
        if self.pending_guard is not None:
            self.pending_guard.confirm()
            self.pending_guard = None
        monitor = self._find_monitor(instance_id)
        friendly_name = monitor.friendly_name if monitor else "the monitor"
        self._monitor_op_in_flight = True
        try:
            ok, message = monitors_mod.enable_monitor(instance_id)
        except Exception as e:
            ok, message = False, f"Unexpected error: {e}"
        return self._finish_revert(ok, message, friendly_name, instance_id)

    def restore_pending(self):
        if self._monitor_op_in_flight:
            return {"ok": False, "kind": "idle", "message": "Another operation is already in progress."}
        pending = load_pending_restore()
        if pending is None:
            return {"ok": True, "kind": "ok", "message": "Nothing to restore."}
        instance_id = pending.get("instance_id")
        friendly_name = pending.get("friendly_name", "the monitor")
        if not instance_id:
            clear_pending_restore()
            return {"ok": True, "kind": "ok", "message": "Cleared a stale recovery flag."}
        self._monitor_op_in_flight = True
        try:
            ok, message = monitors_mod.enable_monitor(instance_id)
        except Exception as e:
            ok, message = False, f"Unexpected error: {e}"
        return self._finish_revert(ok, message, friendly_name, instance_id)
