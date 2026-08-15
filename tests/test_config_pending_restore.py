import os

import quickres.config as config


def _use_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PENDING_RESTORE_PATH", os.path.join(str(tmp_path), "pending_restore.json"))


def test_round_trip(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    data = {"instance_id": "DISPLAY\\DEL4110\\5&2e2fefea&0&UID1078018", "action": "disable"}
    assert config.save_pending_restore(data) is True

    loaded = config.load_pending_restore()
    assert loaded == data

    config.clear_pending_restore()
    assert config.load_pending_restore() is None


def test_save_returns_false_on_write_failure(monkeypatch, tmp_path):
    # Callers (webview/bridge.py) rely on this return value to refuse a disable action
    # when the crash-recovery flag can't be persisted — the safety net is
    # void otherwise, so this must surface as False, not a swallowed error.
    _use_tmp_paths(monkeypatch, tmp_path)

    import builtins

    def _raise(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(builtins, "open", _raise)

    assert config.save_pending_restore({"instance_id": "x"}) is False


def test_load_returns_none_when_missing(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    assert config.load_pending_restore() is None


def test_load_returns_none_on_corrupt_json(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    with open(config.PENDING_RESTORE_PATH, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    assert config.load_pending_restore() is None


def test_clear_pending_restore_is_safe_when_missing(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    # Should not raise even though the file doesn't exist.
    config.clear_pending_restore()
