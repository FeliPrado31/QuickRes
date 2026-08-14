import ctypes
import uuid

import pytest

from quickres.monitors import (
    GUID_DEVCLASS_MONITOR,
    PendingDisableGuard,
    _make_guid,
    _normalize_interface_id,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            r"\\?\DISPLAY#DEL4110#5&2e2fefea&0&UID1078018#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\DEL4110\5&2e2fefea&0&UID1078018",
        ),
        (
            r"\\?\DISPLAY#SAM0E5D#4&1a2b3c4d&0&UID4352#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\SAM0E5D\4&1a2b3c4d&0&UID4352",
        ),
        (
            r"\\?\DISPLAY#AUS2609#5&1f2e3d4c&0&UID37#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\AUS2609\5&1f2e3d4c&0&UID37",
        ),
    ],
)
def test_normalize_interface_id(raw, expected):
    assert _normalize_interface_id(raw) == expected


def test_make_guid_round_trip():
    g = _make_guid(GUID_DEVCLASS_MONITOR)
    raw_bytes = ctypes.string_at(ctypes.byref(g), ctypes.sizeof(g))
    round_tripped = uuid.UUID(bytes_le=raw_bytes)
    assert round_tripped == uuid.UUID(GUID_DEVCLASS_MONITOR)


class FakeScheduler:
    def __init__(self):
        self.scheduled = []
        self.cancelled = set()
        self._next_handle = 1

    def schedule(self, delay_seconds, callback):
        handle = self._next_handle
        self._next_handle += 1
        self.scheduled.append((handle, delay_seconds, callback))
        return handle

    def cancel(self, handle):
        self.cancelled.add(handle)

    def fire(self, handle):
        for h, _delay, callback in self.scheduled:
            if h == handle:
                callback()
                return
        raise AssertionError(f"No scheduled callback with handle {handle}")


def _make_guard(revert_callback, scheduler):
    return PendingDisableGuard(
        revert_callback=revert_callback,
        schedule_fn=scheduler.schedule,
        cancel_fn=scheduler.cancel,
        timeout_seconds=10,
    )


def test_confirm_before_timeout_prevents_revert():
    scheduler = FakeScheduler()
    calls = []
    guard = _make_guard(lambda: calls.append("reverted"), scheduler)

    guard.start()
    guard.confirm()

    handle = scheduler.scheduled[0][0]
    assert handle in scheduler.cancelled
    scheduler.fire(handle)  # simulate a stray late firing after cancel

    assert calls == []
    assert guard.confirmed is True
    assert guard.reverted is False


def test_timeout_without_confirm_reverts_once():
    scheduler = FakeScheduler()
    calls = []
    guard = _make_guard(lambda: calls.append("reverted"), scheduler)

    guard.start()
    handle = scheduler.scheduled[0][0]
    scheduler.fire(handle)

    assert calls == ["reverted"]
    assert guard.reverted is True
    assert guard.confirmed is False


def test_confirm_after_timeout_is_noop():
    scheduler = FakeScheduler()
    calls = []
    guard = _make_guard(lambda: calls.append("reverted"), scheduler)

    guard.start()
    handle = scheduler.scheduled[0][0]
    scheduler.fire(handle)

    # Should not raise, and should not call anything again.
    guard.confirm()

    assert calls == ["reverted"]
    assert guard.confirmed is False


def test_duplicate_timeout_fire_does_not_revert_twice():
    scheduler = FakeScheduler()
    calls = []
    guard = _make_guard(lambda: calls.append("reverted"), scheduler)

    guard.start()
    handle = scheduler.scheduled[0][0]
    scheduler.fire(handle)
    scheduler.fire(handle)  # stray duplicate Tk .after firing

    assert calls == ["reverted"]
