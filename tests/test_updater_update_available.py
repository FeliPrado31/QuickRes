"""Round 6 approved feature: real update-available detection.

check_updates() used to return fetch_version_info()'s raw response with
nothing ever comparing it against quickres.__version__ to determine whether
it's actually a newer version. updater.is_newer_version()/update_available()
own that comparison; bridge.py's check_updates() bundles the verdict into an
"update_available" field alongside the existing raw response fields.
"""
import pytest

from quickres import updater
from quickres.webview.bridge import Api


class TestParseVersionTuple:
    def test_plain_semver(self):
        assert updater._parse_version_tuple("1.0.7") == (1, 0, 7)

    def test_leading_v_prefix(self):
        assert updater._parse_version_tuple("v1.2.3") == (1, 2, 3)

    def test_prerelease_suffix_truncated(self):
        assert updater._parse_version_tuple("1.2.3-beta") == (1, 2, 3)

    def test_two_segment_version(self):
        assert updater._parse_version_tuple("2.5") == (2, 5)

    def test_non_string_returns_none(self):
        assert updater._parse_version_tuple(None) is None
        assert updater._parse_version_tuple(123) is None

    def test_unparsable_string_returns_none(self):
        assert updater._parse_version_tuple("not-a-version") is None
        assert updater._parse_version_tuple("") is None


class TestIsNewerVersion:
    def test_strictly_newer_patch(self):
        assert updater.is_newer_version("1.0.7", "1.0.8") is True

    def test_strictly_newer_minor(self):
        assert updater.is_newer_version("1.0.7", "1.1.0") is True

    def test_strictly_newer_major(self):
        assert updater.is_newer_version("1.0.7", "2.0.0") is True

    def test_equal_version_is_not_newer(self):
        assert updater.is_newer_version("1.0.7", "1.0.7") is False

    def test_older_remote_is_not_newer(self):
        assert updater.is_newer_version("1.0.7", "1.0.6") is False

    def test_shorter_tuple_zero_padded_equal(self):
        assert updater.is_newer_version("1.2.0", "1.2") is False
        assert updater.is_newer_version("1.2", "1.2.0") is False

    def test_v_prefix_does_not_affect_comparison(self):
        assert updater.is_newer_version("1.0.7", "v1.0.8") is True

    def test_unparsable_remote_fails_closed(self):
        assert updater.is_newer_version("1.0.7", "not-a-version") is False
        assert updater.is_newer_version("1.0.7", None) is False

    def test_unparsable_current_fails_closed(self):
        assert updater.is_newer_version("garbage", "1.0.8") is False


class TestUpdateAvailable:
    def test_newer_version_field_reports_true(self):
        assert updater.update_available("1.0.7", {"version": "1.0.8"}) is True

    def test_same_version_field_reports_false(self):
        assert updater.update_available("1.0.7", {"version": "1.0.7"}) is False

    def test_missing_version_field_fails_closed(self):
        assert updater.update_available("1.0.7", {"download_url": "https://x"}) is False

    def test_non_dict_response_fails_closed(self):
        assert updater.update_available("1.0.7", None) is False
        assert updater.update_available("1.0.7", "not a dict") is False


class TestResolveDownloadUrl:
    def test_download_url_field_is_used_when_present(self):
        assert (
            updater.resolve_download_url({"download_url": "https://lxzy.my/QuickRes.exe"})
            == "https://lxzy.my/QuickRes.exe"
        )

    def test_falls_back_to_url_field(self):
        # The live version.json response actually names the field "url",
        # not "download_url" -- this is the real current server shape.
        assert (
            updater.resolve_download_url({"version": "1.2.0", "url": "https://lxzy.my/QuickRes.exe"})
            == "https://lxzy.my/QuickRes.exe"
        )

    def test_download_url_field_takes_precedence_over_url(self):
        assert (
            updater.resolve_download_url(
                {"download_url": "https://lxzy.my/a.exe", "url": "https://lxzy.my/b.exe"}
            )
            == "https://lxzy.my/a.exe"
        )

    def test_missing_both_fields_returns_none(self):
        assert updater.resolve_download_url({"version": "1.2.0"}) is None

    def test_non_dict_returns_none(self):
        assert updater.resolve_download_url(None) is None
        assert updater.resolve_download_url("not a dict") is None

    def test_non_string_value_fails_closed_to_none(self):
        # A malformed/unexpected non-string value under either field must
        # not propagate to _validate_download_url() -- urllib.parse.urlsplit()
        # raises AttributeError (not the intended ValueError) on a non-string
        # input, which would surface as an unhandled crash instead of the
        # module's usual fail-closed "Refusing to download..." error.
        assert updater.resolve_download_url({"url": 12345}) is None

    def test_non_string_download_url_still_falls_back_to_valid_url_field(self):
        # `A.get() or B.get()` short-circuits on ANY truthy value, including
        # a truthy-but-non-string one -- a malformed download_url must not
        # discard a perfectly usable url fallback.
        assert (
            updater.resolve_download_url(
                {"download_url": 12345, "url": "https://lxzy.my/QuickRes.exe"}
            )
            == "https://lxzy.my/QuickRes.exe"
        )


class TestBridgeCheckUpdatesReportsUpdateAvailable:
    def _frozen_api(self, monkeypatch):
        monkeypatch.setattr("quickres.webview.bridge.sys.frozen", True, raising=False)
        return Api()

    def test_newer_remote_version_sets_update_available_true(self, monkeypatch):
        api = self._frozen_api(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.fetch_version_info",
            lambda: {"version": "99.0.0", "download_url": "https://lxzy.my/QuickRes.exe"},
        )

        result = api.check_updates()

        assert result["ok"] is True
        assert result["data"]["update_available"] is True
        # Original raw fields are preserved alongside the new flag.
        assert result["data"]["version"] == "99.0.0"
        assert result["data"]["download_url"] == "https://lxzy.my/QuickRes.exe"

    def test_same_remote_version_sets_update_available_false(self, monkeypatch):
        from quickres import __version__

        api = self._frozen_api(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.fetch_version_info",
            lambda: {"version": __version__},
        )

        result = api.check_updates()

        assert result["ok"] is True
        assert result["data"]["update_available"] is False

    def test_older_remote_version_sets_update_available_false(self, monkeypatch):
        api = self._frozen_api(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.fetch_version_info",
            lambda: {"version": "0.0.1"},
        )

        result = api.check_updates()

        assert result["ok"] is True
        assert result["data"]["update_available"] is False

    def test_real_server_url_field_is_exposed_as_download_url(self, monkeypatch):
        # The live version.json response actually names the field "url", not
        # "download_url" -- panel.html reads S.updateInfo.download_url, so
        # without this normalization the "Update Now" button silently no-ops.
        api = self._frozen_api(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.fetch_version_info",
            lambda: {"version": "99.0.0", "url": "https://lxzy.my/QuickRes.exe"},
        )

        result = api.check_updates()

        assert result["ok"] is True
        assert result["data"]["download_url"] == "https://lxzy.my/QuickRes.exe"
        # Raw field is preserved alongside the normalized one.
        assert result["data"]["url"] == "https://lxzy.my/QuickRes.exe"

    def test_malformed_raw_download_url_does_not_leak_past_failed_resolution(
        self, monkeypatch
    ):
        # {**info} in check_updates() copies the server's raw fields first,
        # INCLUDING a malformed download_url -- the resolved value must
        # always overwrite that key (even to None), never leave the raw
        # value in place just because resolution didn't find anything
        # better. Otherwise a truthy-but-invalid value (12345) reaches
        # panel.html's `if (!S.updateInfo.download_url) return;` guard,
        # passes it (12345 is truthy in JS), and crashes deep inside
        # apply_update() with a confusing AttributeError instead of the
        # clean "no update available" no-op this should be.
        api = self._frozen_api(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.fetch_version_info",
            lambda: {"version": "99.0.0", "download_url": 12345},
        )

        result = api.check_updates()

        assert result["ok"] is True
        assert result["data"]["download_url"] is None

    def test_not_frozen_still_returns_none_without_fetching(self, monkeypatch):
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
