import pytest

from quickres import config
from quickres.webview.bridge import Api


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))


def test_start_update_reuses_the_active_background_job(monkeypatch):
    instances = []

    class FakeJob:
        def __init__(self, url, version_info=None):
            self.url = url
            self.version_info = version_info
            self.state = {
                "stage": "downloading", "downloaded_bytes": 0,
                "total_bytes": None, "error": None,
            }
            instances.append(self)

        def start(self):
            return True

        def snapshot(self):
            return dict(self.state)

    monkeypatch.setattr("quickres.webview.bridge.updater.UpdateJob", FakeJob)
    api = Api()

    first = api.start_update("https://lxzy.my/QuickRes_new.exe", {"version": "2.0"})
    second = api.start_update("https://lxzy.my/QuickRes_new.exe", {"version": "2.0"})

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(instances) == 1
    assert api.get_update_status()["data"]["stage"] == "downloading"


def test_install_verified_update_uses_safety_handoff_and_existing_rollback_path(monkeypatch):
    class ReadyJob:
        version_info = {"version": "2.0"}

        def snapshot(self):
            return {
                "stage": "ready", "downloaded_bytes": 20,
                "total_bytes": 20, "error": None,
            }

    api = Api()
    api._update_job = ReadyJob()
    events = []
    monkeypatch.setattr(api, "_prepare_update_handoff_locked", lambda: events.append("handoff"))

    # install_downloaded_update() must route through updater's own
    # force-exit wrapper (not call apply_update directly) -- a plain
    # SystemExit from apply_update(reuse_download=True) only kills the
    # pywebview worker thread it runs on, not the whole process, so the
    # original window never actually closes. See test_updater_exit_behavior.py
    # for the os._exit(0) coverage; this test only checks the bridge wires
    # the call through correctly.
    def fake_install_downloaded_update(version_info=None):
        events.append("apply")
        assert version_info == {"version": "2.0"}
        return {"started": True}

    monkeypatch.setattr(
        "quickres.webview.bridge.updater.install_downloaded_update",
        fake_install_downloaded_update,
    )

    result = api.install_downloaded_update()

    assert result["ok"] is True
    assert result["data"] == {"started": True}
    assert events == ["handoff", "apply"]


def test_failed_install_clears_the_stale_job_instead_of_deadlocking_retries(
    monkeypatch,
):
    # Round 28 finding (4th pass): if updater.install_downloaded_update()
    # raises (network/disk failure, a corrupted staged file caught by
    # apply_update's own re-verification, etc.) and self._update_job is
    # never reset, get_update_status()/start_update()'s busy-check keeps
    # seeing the SAME stale "ready" job forever -- every future update
    # attempt reports the old failed job's state instead of starting
    # fresh, with no recovery short of restarting the whole app.
    class ReadyJob:
        version_info = {"version": "2.0"}

        def snapshot(self):
            return {
                "stage": "ready", "downloaded_bytes": 20,
                "total_bytes": 20, "error": None,
            }

    api = Api()
    api._update_job = ReadyJob()
    monkeypatch.setattr(api, "_prepare_update_handoff_locked", lambda: None)

    def _raise(version_info=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr(
        "quickres.webview.bridge.updater.install_downloaded_update", _raise
    )

    result = api.install_downloaded_update()

    assert result["ok"] is False
    assert api._update_job is None
    assert api.get_update_status()["data"]["stage"] == "idle"
