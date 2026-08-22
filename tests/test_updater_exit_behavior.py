import pytest

from quickres import updater
from quickres.webview.bridge import Api


def test_check_updates_noop_when_not_frozen(monkeypatch):
    # APP-2: update checking only runs on a frozen (built exe) app.
    monkeypatch.setattr("quickres.webview.bridge.sys.frozen", False, raising=False)
    fetch_calls = []
    monkeypatch.setattr(
        "quickres.webview.bridge.updater.fetch_version_info",
        lambda: fetch_calls.append(1),
    )
    api = Api()

    result = api.check_updates()

    assert result["ok"] is True
    assert result["data"] is None
    assert fetch_calls == []


def test_confirm_update_system_exit_forces_os_exit(monkeypatch):
    monkeypatch.setattr(
        updater,
        "apply_update",
        lambda url, version_info=None: (_ for _ in ()).throw(SystemExit(0)),
    )
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    updater.confirm_update("https://example.com/QuickRes.exe")

    assert exit_calls == [0]


def test_confirm_update_other_exception_does_not_force_exit(monkeypatch):
    def _raise(url, version_info=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr(updater, "apply_update", _raise)
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    with pytest.raises(ConnectionError):
        updater.confirm_update("https://example.com/QuickRes.exe")

    assert exit_calls == []


def test_bridge_confirm_update_reports_other_exception_without_force_exit(monkeypatch):
    # The bridge layer: a non-SystemExit failure must surface as ok:false
    # in the envelope, with the app staying alive (no os._exit call at all).
    def _raise(url, version_info=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr("quickres.webview.bridge.updater.confirm_update", _raise)
    exit_calls = []
    monkeypatch.setattr("quickres.webview.bridge.updater.os._exit", lambda code: exit_calls.append(code))
    api = Api()

    result = api.confirm_update("https://example.com/QuickRes.exe")

    assert result["ok"] is False
    assert "network is down" in result["message"]
    assert exit_calls == []


def test_install_downloaded_update_system_exit_forces_os_exit(monkeypatch):
    # Same hazard as confirm_update(): apply_update(reuse_download=True)
    # ends in sys.exit(0) on success, but a plain SystemExit raised inside
    # a pywebview JS-bridge call only kills that worker thread, not the
    # whole process -- leaving the original window alive forever while
    # update.bat's own launch of the replacement races its single-instance
    # mutex. install_downloaded_update() must force-exit via os._exit(0)
    # exactly like confirm_update() already does.
    monkeypatch.setattr(
        updater,
        "apply_update",
        lambda url, version_info=None, **kwargs: (_ for _ in ()).throw(SystemExit(0)),
    )
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    updater.install_downloaded_update(version_info={"version": "2.0"})

    assert exit_calls == [0]


def test_install_downloaded_update_other_exception_does_not_force_exit(monkeypatch):
    def _raise(url, version_info=None, **kwargs):
        raise ConnectionError("disk is full")

    monkeypatch.setattr(updater, "apply_update", _raise)
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    with pytest.raises(ConnectionError):
        updater.install_downloaded_update(version_info={"version": "2.0"})

    assert exit_calls == []


def test_install_downloaded_update_passes_reuse_download_and_no_url(monkeypatch):
    calls = []

    def fake_apply_update(url, version_info=None, **kwargs):
        calls.append((url, version_info, kwargs))

    monkeypatch.setattr(updater, "apply_update", fake_apply_update)

    updater.install_downloaded_update(version_info={"version": "2.0"})

    assert calls == [(None, {"version": "2.0"}, {"reuse_download": True})]


def test_bridge_op_force_exits_on_system_exit_as_a_structural_backstop(monkeypatch):
    # Round 28 finding: bridge_op's own `except Exception` cannot catch
    # SystemExit (it's a BaseException, not an Exception), so a wrapped
    # method that raises SystemExit directly -- not just apply_update()'s
    # two known callers, which route through their own
    # _force_exit_on_expected_system_exit wrapper -- would previously kill
    # only the pywebview worker thread it ran on, leaving the window hung
    # forever. bridge_op itself must force-exit as a backstop, protecting
    # every current AND FUTURE Api method automatically instead of relying
    # on each call site to remember to wrap itself.
    from quickres.webview import bridge as bridge_module

    exit_calls = []
    monkeypatch.setattr(bridge_module.os, "_exit", lambda code: exit_calls.append(code))

    @bridge_module.bridge_op()
    def raises_system_exit(self):
        raise SystemExit(0)

    class _FakeApi:
        pass

    raises_system_exit(_FakeApi())

    assert exit_calls == [0]
