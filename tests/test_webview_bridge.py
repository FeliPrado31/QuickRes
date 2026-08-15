import os

import pytest

import quickres.config as config
from quickres.monitors import Monitor, TIMEOUT_MESSAGE, DEVICE_NOT_FOUND_PREFIX
from quickres.webview import bridge


def _use_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PENDING_RESTORE_PATH", os.path.join(str(tmp_path), "pending_restore.json"))
    monkeypatch.setattr(config, "CONFIG_PATH", os.path.join(str(tmp_path), "config.json"))
    monkeypatch.setattr(config, "LOG_PATH", os.path.join(str(tmp_path), "quickres.log"))


class FakeScheduler:
    """Same shape as tests/test_monitors_logic.py's FakeScheduler, adapted
    to bridge._schedule/_cancel's (seconds, callback) -> handle signature."""

    def __init__(self):
        self.scheduled = []
        self.cancelled = set()
        self._next_handle = 1

    def schedule(self, seconds, callback):
        handle = self._next_handle
        self._next_handle += 1
        self.scheduled.append((handle, seconds, callback))
        return handle

    def cancel(self, handle):
        self.cancelled.add(handle)

    def fire_latest(self):
        handle, _seconds, callback = self.scheduled[-1]
        callback()
        return handle


class FakeWindow:
    def __init__(self):
        self.calls = []

    def evaluate_js(self, code):
        self.calls.append(code)


def _monitor(instance_id="DISPLAY\\X\\1", friendly_name="Test Monitor", is_enabled=True, is_primary=False):
    return Monitor(instance_id=instance_id, friendly_name=friendly_name, is_enabled=is_enabled, is_primary=is_primary)


@pytest.fixture
def scheduler(monkeypatch):
    sched = FakeScheduler()
    monkeypatch.setattr(bridge, "_schedule", sched.schedule)
    monkeypatch.setattr(bridge, "_cancel", sched.cancel)
    return sched


@pytest.fixture
def api(monkeypatch, tmp_path, scheduler):
    _use_tmp_paths(monkeypatch, tmp_path)
    instance = bridge.Api()
    yield instance
    # _window_holder is module-level (deliberately, see bridge.py's comment
    # on why the Window can't live on the Api instance) — reset it so a
    # bound FakeWindow from one test can't leak into the next.
    bridge._window_holder["window"] = None


# ---- get_initial_state: pending-restore lock seeding ------------------

def test_get_initial_state_seeds_lock_from_pending_restore(api):
    config.save_pending_restore({
        "instance_id": "DISPLAY\\X\\1", "friendly_name": "Old Monitor", "action": "disable",
    })

    state = api.get_initial_state()

    assert state["monitorNotice"] == {"instance_id": "DISPLAY\\X\\1", "friendly_name": "Old Monitor"}
    # Regression coverage for the R3 review finding: without this, actions on
    # OTHER monitors weren't blocked after a restart even though a disable
    # from a previous session/crash might still be unresolved.
    assert api._active_pending_instance_id == "DISPLAY\\X\\1"


def test_get_initial_state_without_pending_restore_stays_unlocked(api):
    state = api.get_initial_state()

    assert state["monitorNotice"] is None
    assert api._active_pending_instance_id is None


# ---- monitor_action: guards -------------------------------------------

def test_monitor_action_refuses_when_locked(api, monkeypatch):
    api._active_pending_instance_id = "DISPLAY\\OTHER\\1"
    calls = []
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: calls.append(i))
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: calls.append(i))

    result = api.monitor_action("DISPLAY\\Y\\1")

    assert result["ok"] is False
    assert "already in progress" in result["message"]
    assert calls == []


def test_monitor_action_refuses_to_disable_only_enabled_monitor(api, monkeypatch):
    target = _monitor(is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target])
    disable_calls = []
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: disable_calls.append(i))

    result = api.monitor_action(target.instance_id)

    assert result["ok"] is False
    assert "only" in result["message"] and "enabled monitor" in result["message"]
    assert disable_calls == []
    assert config.load_pending_restore() is None


def test_monitor_action_disable_refuses_when_crash_recovery_flag_fails_to_write(api, monkeypatch):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge, "save_pending_restore", lambda data: False)
    disable_calls = []
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: disable_calls.append(i))

    result = api.monitor_action(target.instance_id)

    assert result["ok"] is False
    assert "crash-recovery flag" in result["message"]
    assert disable_calls == []


# ---- monitor_action: disable outcomes ----------------------------------

def test_monitor_action_disable_success_locks_and_starts_revert_guard(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (True, "Disabled OK"))

    result = api.monitor_action(target.instance_id)

    assert result == {"ok": True, "kind": "ok", "message": "Disabled OK"}
    assert api._active_pending_instance_id == target.instance_id
    assert api._confirm_dialog_instance_id == target.instance_id
    assert config.load_pending_restore()["instance_id"] == target.instance_id
    assert len(scheduler.scheduled) == 1


def test_monitor_action_disable_timeout_locks_without_confirm_dialog(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (False, TIMEOUT_MESSAGE))

    result = api.monitor_action(target.instance_id)

    assert result["kind"] == "idle"
    assert api._active_pending_instance_id == target.instance_id
    # The outcome is genuinely unknown at this point — no confirm/revert
    # dialog should appear for a state that hasn't been confirmed yet.
    assert api._confirm_dialog_instance_id is None
    assert len(scheduler.scheduled) == 0


def test_monitor_action_disable_reported_failure_but_actually_disabled_recovers(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    # enumerate_monitors is consulted twice: once for the "only enabled
    # monitor" guard (target still enabled) and once by _find_monitor's
    # re-check after the reported failure (target now disabled for real).
    calls = {"n": 0}

    def fake_enumerate():
        calls["n"] += 1
        if calls["n"] == 1:
            return [target, other]
        return [_monitor(instance_id=target.instance_id, is_enabled=False), other]

    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", fake_enumerate)
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (False, "transient report failure"))

    result = api.monitor_action(target.instance_id)

    assert result["ok"] is True
    assert "unconfirmed, verified by re-check" in result["message"]
    assert api._active_pending_instance_id == target.instance_id
    assert api._confirm_dialog_instance_id == target.instance_id
    assert len(scheduler.scheduled) == 1


def test_monitor_action_disable_genuine_failure_clears_pending_restore(api, monkeypatch):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (False, "real failure"))

    result = api.monitor_action(target.instance_id)

    assert result["ok"] is False
    assert api._active_pending_instance_id is None
    assert config.load_pending_restore() is None


# ---- monitor_action: enable outcomes -----------------------------------

def test_monitor_action_enable_success(api, monkeypatch):
    target = _monitor(is_enabled=False)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target])
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: (True, "Enabled OK"))

    result = api.monitor_action(target.instance_id)

    assert result == {"ok": True, "kind": "ok", "message": "Enabled OK"}
    assert api._monitor_op_in_flight is False


def test_monitor_action_enable_timeout(api, monkeypatch):
    target = _monitor(is_enabled=False)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target])
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: (False, TIMEOUT_MESSAGE))

    result = api.monitor_action(target.instance_id)

    assert result["kind"] == "idle"
    assert "check again" in result["message"]


# ---- _reconcile_stuck_pending (fires via list_monitors) -----------------

def test_reconcile_clears_lock_when_actually_enabled(api, monkeypatch):
    api._active_pending_instance_id = "DISPLAY\\X\\1"
    config.save_pending_restore({"instance_id": "DISPLAY\\X\\1", "friendly_name": "X"})
    monkeypatch.setattr(
        bridge.monitors_mod, "enumerate_monitors",
        lambda: [_monitor(instance_id="DISPLAY\\X\\1", is_enabled=True)],
    )

    api.list_monitors()

    assert api._active_pending_instance_id is None
    assert config.load_pending_restore() is None


def test_reconcile_opens_confirm_dialog_when_actually_disabled(api, monkeypatch, scheduler):
    api._active_pending_instance_id = "DISPLAY\\X\\1"
    config.save_pending_restore({"instance_id": "DISPLAY\\X\\1", "friendly_name": "X"})
    monkeypatch.setattr(
        bridge.monitors_mod, "enumerate_monitors",
        lambda: [_monitor(instance_id="DISPLAY\\X\\1", is_enabled=False)],
    )

    api.list_monitors()

    assert api._active_pending_instance_id == "DISPLAY\\X\\1"
    assert api._confirm_dialog_instance_id == "DISPLAY\\X\\1"
    assert len(scheduler.scheduled) == 1
    assert config.load_pending_restore() is not None


def test_reconcile_stays_locked_when_device_cannot_be_found(api, monkeypatch):
    api._active_pending_instance_id = "DISPLAY\\X\\1"
    config.save_pending_restore({"instance_id": "DISPLAY\\X\\1", "friendly_name": "X"})
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [])

    api.list_monitors()

    # Fail safe: an unconfirmable device state must not silently unlock.
    assert api._active_pending_instance_id == "DISPLAY\\X\\1"
    assert api._confirm_dialog_instance_id is None
    assert config.load_pending_restore() is not None


def test_reconcile_does_not_reopen_an_already_resolved_confirm_dialog(api, monkeypatch):
    api._active_pending_instance_id = "DISPLAY\\X\\1"
    api._confirm_dialog_instance_id = "DISPLAY\\X\\1"
    guard_starts = []
    monkeypatch.setattr(api, "_start_revert_guard", lambda *a: guard_starts.append(a))
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [])

    api.list_monitors()

    assert guard_starts == []


# ---- keep_disabled / revert_now / auto-revert --------------------------

def test_keep_disabled_confirms_guard_and_clears_flags(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (True, "Disabled OK"))
    api.monitor_action(target.instance_id)
    guard = api.pending_guard

    result = api.keep_disabled(target.instance_id)

    assert result["ok"] is True
    assert guard.confirmed is True
    assert api._active_pending_instance_id is None
    assert api._confirm_dialog_instance_id is None
    assert config.load_pending_restore() is None
    # The scheduled auto-revert must not fire after being confirmed.
    scheduler.fire_latest()


def test_revert_now_re_enables_and_clears_flags(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (True, "Disabled OK"))
    api.monitor_action(target.instance_id)
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: (True, "Reverted OK"))

    result = api.revert_now(target.instance_id)

    assert result["ok"] is True
    assert api._active_pending_instance_id is None
    assert api._confirm_dialog_instance_id is None
    assert config.load_pending_restore() is None


def test_auto_revert_fires_when_guard_times_out(api, monkeypatch, scheduler):
    target = _monitor(is_enabled=True, friendly_name="Auto Revert Monitor")
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (True, "Disabled OK"))
    api.monitor_action(target.instance_id)

    window = FakeWindow()
    api.bind_window(window)
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: (True, "Reverted OK"))

    scheduler.fire_latest()  # simulate the 10s auto-revert timeout elapsing

    assert api._active_pending_instance_id is None
    assert config.load_pending_restore() is None
    assert any("Reverted Auto Revert Monitor" in c for c in window.calls)
    assert any("qrOnMonitorsChanged" in c for c in window.calls)


def test_finish_revert_device_not_found_clears_stale_flag(api, monkeypatch):
    target = _monitor(is_enabled=True)
    other = _monitor(instance_id="DISPLAY\\OTHER\\1", is_enabled=True)
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [target, other])
    monkeypatch.setattr(bridge.monitors_mod, "disable_monitor", lambda i: (True, "Disabled OK"))
    api.monitor_action(target.instance_id)
    monkeypatch.setattr(
        bridge.monitors_mod, "enable_monitor",
        lambda i: (False, f"{DEVICE_NOT_FOUND_PREFIX} {i}"),
    )

    result = api.revert_now(target.instance_id)

    assert result["ok"] is False
    assert api._active_pending_instance_id is None
    assert config.load_pending_restore() is None


# ---- restore_pending (startup banner) -----------------------------------

def test_restore_pending_reenables_and_clears(api, monkeypatch):
    config.save_pending_restore({"instance_id": "DISPLAY\\X\\1", "friendly_name": "X"})
    monkeypatch.setattr(bridge.monitors_mod, "enumerate_monitors", lambda: [_monitor(instance_id="DISPLAY\\X\\1", is_enabled=False)])
    monkeypatch.setattr(bridge.monitors_mod, "enable_monitor", lambda i: (True, "Restored OK"))

    result = api.restore_pending()

    assert result["ok"] is True
    assert config.load_pending_restore() is None


def test_restore_pending_refuses_when_op_in_flight(api):
    api._monitor_op_in_flight = True

    result = api.restore_pending()

    assert result["ok"] is False
    assert "already in progress" in result["message"]


def test_restore_pending_noop_when_nothing_pending(api):
    result = api.restore_pending()

    assert result == {"ok": True, "kind": "ok", "message": "Nothing to restore."}


# ---- confirm_update: process-exit safety (R3 review finding) -----------

def test_confirm_update_success_forces_process_exit(api, monkeypatch):
    monkeypatch.setattr(bridge, "apply_update", lambda url: (_ for _ in ()).throw(SystemExit(0)))
    exit_calls = []
    monkeypatch.setattr(bridge.os, "_exit", lambda code: exit_calls.append(code))

    api.confirm_update("https://example.invalid/QuickRes.exe")

    assert exit_calls == [0]


def test_confirm_update_generic_failure_does_not_exit_and_reports(api, monkeypatch):
    def _boom(url):
        raise OSError("disk full")

    monkeypatch.setattr(bridge, "apply_update", _boom)
    exit_calls = []
    monkeypatch.setattr(bridge.os, "_exit", lambda code: exit_calls.append(code))
    window = FakeWindow()
    api.bind_window(window)

    api.confirm_update("https://example.invalid/QuickRes.exe")

    # The whole point of this fix: a failure that isn't the expected
    # SystemExit must NOT silently kill the process — the user needs to see
    # it, and the app must stay alive to show it.
    assert exit_calls == []
    assert any("Update failed" in c and "disk full" in c for c in window.calls)
