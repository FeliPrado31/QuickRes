"""1c: updater.apply_update must validate download_url's scheme + host
against an explicit allowlist before ever opening it, mirroring bridge.py's
open_external discipline. UPDATE_URL (lxzy.my/version.json) is the
version-CHECK endpoint; the real download_url comes from that endpoint's
JSON response, so the allowlist covers the project's actual release hosts:
lxzy.my (same domain as the version-check endpoint) and github.com (GitHub
Releases is a common asset host for this kind of app).

RISK FLAGGED FOR USER CONFIRMATION: the exact intended download host was
genuinely ambiguous from the repo alone (no release asset URL is checked
into the repo) -- this allowlist is the most defensible default, not a
verified fact.
"""
import pytest

from quickres import updater


class TestValidateDownloadUrl:
    def test_rejects_non_https_scheme(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("http://lxzy.my/QuickRes.exe")

    def test_rejects_non_allowlisted_host(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("https://evil.example.com/QuickRes.exe")

    def test_accepts_lxzy_my_with_any_path(self):
        updater._validate_download_url("https://lxzy.my/QuickRes.exe")

    def test_accepts_github_url_under_this_projects_own_repo(self):
        updater._validate_download_url(
            "https://github.com/lxzydev/QuickRes/releases/download/v1.0.7/QuickRes.exe"
        )

    def test_rejects_github_url_under_a_different_repo(self):
        # Round 24 finding: the allowlist previously accepted ANY
        # github.com host+path, so a trusted-but-compromised version-check
        # response could point download_url at an attacker-controlled
        # release asset in someone else's repo and it would still pass.
        # The path must now be scoped to this project's actual repository
        # (https://github.com/lxzydev/QuickRes, per webview/bridge.py's own
        # open_external allowlist comment naming the same identity).
        with pytest.raises(ValueError):
            updater._validate_download_url(
                "https://github.com/attacker/evil-repo/releases/download/v1/QuickRes.exe"
            )

    def test_rejects_github_url_with_no_path_at_all(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("https://github.com/QuickRes.exe")

    def test_rejects_path_traversal_that_escapes_repo_scope(self):
        # Round 28 finding: the prefix check compared the RAW,
        # unnormalized path string, so a URL containing enough `../`
        # segments to actually escape the repo scope after normalization
        # (e.g. by a server that normalizes dot-segments before routing,
        # standard RFC 3986 behavior) still passed because the raw string
        # literally starts with "/lxzydev/QuickRes/". Confirmed via
        # posixpath.normpath: this exact path normalizes to
        # "/lxzydev/attacker-org/evil-repo/x", outside the repo.
        with pytest.raises(ValueError):
            updater._validate_download_url(
                "https://github.com/lxzydev/QuickRes/releases/download/v1/"
                "../../../../attacker-org/evil-repo/x"
            )

    def test_accepts_dot_segments_that_stay_within_repo_scope(self):
        # A `../` that normalizes back to somewhere still under
        # /lxzydev/QuickRes/ must not be rejected just for containing dots.
        updater._validate_download_url(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/"
            "../../../attacker-org/evil-repo/x"
        )


class TestApplyUpdateEnforcesAllowlistBeforeDownload:
    def test_rejects_non_https_scheme_before_urlopen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: calls.append(1))

        with pytest.raises(ValueError):
            updater.apply_update("http://lxzy.my/QuickRes_new.exe")

        assert calls == []

    def test_rejects_non_allowlisted_host_before_urlopen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: calls.append(1))

        with pytest.raises(ValueError):
            updater.apply_update("https://evil.example.com/QuickRes_new.exe")

        assert calls == []

    def test_allowlisted_host_proceeds_to_download(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        urlopen_calls = []

        # Round 2 addition: apply_update now verifies the downloaded bytes
        # look like a plausible Windows PE executable (see
        # test_updater_integrity_and_rollback.py) before staging, so this
        # allowlist-focused fixture must return a minimal valid DOS/PE
        # header rather than arbitrary bytes.
        _header = bytearray(64)
        _header[0:2] = b"MZ"
        _header[60:64] = (64).to_bytes(4, "little")
        _valid_pe_payload = bytes(_header) + b"PE\x00\x00" + b"restofheader"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _valid_pe_payload

        # Round 3: apply_update now downloads via a dedicated opener
        # (urllib.request.build_opener(_AllowlistRedirectHandler())) rather
        # than the bare module-level urlopen(), so the redirect target can
        # be re-validated before it is followed. Mock at that integration
        # point instead.
        class _FakeOpener:
            def open(self, request, timeout=None):
                urlopen_calls.append(1)
                return _FakeResp()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert urlopen_calls == [1]


class TestAllowlistRedirectHandler:
    """Round 3 finding: _validate_download_url only ever checked the
    INITIAL download_url. urllib's default opener transparently follows
    301/302/303/307 redirects to ANY host with zero re-validation, so a
    real GitHub Releases asset URL (github.com, allowlisted) redirecting to
    objects.githubusercontent.com (NOT allowlisted) bypassed the allowlist
    entirely once a redirect happened. Fix: a custom
    HTTPRedirectHandler subclass that validates the redirect target BEFORE
    building the follow-up request, not after the fact via resp.geturl().
    """

    def test_rejects_redirect_to_non_allowlisted_host_before_following(
        self, monkeypatch
    ):
        # Track whether the base class's redirect_request (the step that
        # actually builds/would-open the follow-up request) is ever
        # reached. It must NOT be reached for a non-allowlisted target.
        base_calls = []
        monkeypatch.setattr(
            updater.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, *a, **k: base_calls.append(1),
        )
        handler = updater._AllowlistRedirectHandler()
        req = updater.urllib.request.Request(
            "https://github.com/owner/repo/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/evil-swap/QuickRes.exe",
            )

        assert base_calls == []

    def test_allows_redirect_to_allowlisted_host(self, monkeypatch):
        base_calls = []
        monkeypatch.setattr(
            updater.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, *a, **k: base_calls.append(1) or "built-request",
        )
        handler = updater._AllowlistRedirectHandler()
        req = updater.urllib.request.Request("https://lxzy.my/QuickRes_new.exe")

        result = handler.redirect_request(
            req, None, 302, "Found", {}, "https://lxzy.my/moved/QuickRes_new.exe"
        )

        assert base_calls == [1]
        assert result == "built-request"


class TestProvenanceScopedCdnRedirectTrust:
    """Round 25 finding: a real GitHub Releases redirect
    (github.com/lxzydev/QuickRes/... -> objects.githubusercontent.com) was
    being rejected by the unconditional _validate_download_url(newurl) check
    in redirect_request, because the CDN host is intentionally NOT in
    _DOWNLOAD_URL_ALLOWED_HOSTS (a server-supplied CDN host would defeat the
    allowlist). Fix: _AllowlistRedirectHandler now takes the call's origin
    URL at construction time and computes ONCE whether that origin (a)
    already passed _validate_download_url AND (b) has hostname exactly
    "github.com" -- which, combined with (a), implies the repo path prefix
    also passed. Only when both hold, and only for the hardcoded CDN host
    constant, is the redirect target's ordinary allowlist check skipped.
    lxzy.my origins satisfy (a) but never (b), so they must never earn this
    trust.
    """

    def _base_redirect_request_tracker(self, monkeypatch):
        base_calls = []
        monkeypatch.setattr(
            updater.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, *a, **k: base_calls.append(1) or "built-request",
        )
        return base_calls

    def test_repo_scoped_github_origin_redirect_to_cdn_reaches_base(
        self, monkeypatch
    ):
        # Covers spec scenario "GitHub release URL redirects to CDN and
        # succeeds".
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1.2.3/QuickRes.exe"
        )
        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1.2.3/QuickRes.exe"
        )

        result = handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
        )

        assert base_calls == [1]
        assert result == "built-request"

    def test_lxzy_my_origin_redirect_to_cdn_is_rejected(self, monkeypatch):
        # Covers spec scenario "lxzy.my version-check redirecting to the
        # GitHub CDN is rejected" -- passing _validate_download_url alone is
        # NOT sufficient; the origin hostname must be exactly github.com.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler("https://lxzy.my/version.json")
        req = updater.urllib.request.Request("https://lxzy.my/version.json")

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )

        assert base_calls == []

    def test_out_of_repo_github_origin_redirect_to_cdn_is_rejected(
        self, monkeypatch
    ):
        # Covers spec scenario "Different repo's GitHub URL redirecting to
        # CDN stays rejected". Construction itself must not raise.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/attacker/evil-repo/releases/download/v1/QuickRes.exe"
        )
        req = updater.urllib.request.Request(
            "https://github.com/attacker/evil-repo/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )

        assert base_calls == []

    def test_default_no_origin_redirect_to_cdn_is_rejected(self, monkeypatch):
        # Fail-closed default: _AllowlistRedirectHandler() with no origin_url
        # must never earn CDN-redirect trust.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler()
        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )

        assert base_calls == []

    def test_repo_scoped_origin_redirect_to_non_https_cdn_host_is_rejected(
        self, monkeypatch
    ):
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "http://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )

        assert base_calls == []

    def test_repo_scoped_origin_redirect_to_lookalike_cdn_host_is_rejected(
        self, monkeypatch
    ):
        # Exact hostname match only, never suffix matching.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com.evil.com/QuickRes.exe",
            )

        assert base_calls == []

    def test_repo_scoped_origin_redirect_to_other_non_allowlisted_host_is_rejected(
        self, monkeypatch
    ):
        # Provenance widens only the CDN, nothing else.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://evil.example.com/QuickRes.exe",
            )

        assert base_calls == []

    def test_two_handlers_with_different_origins_hold_independent_flags(
        self, monkeypatch
    ):
        # Covers spec scenario "Provenance does not leak across calls" --
        # no shared/module-level state.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        trusted_handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        untrusted_handler = updater._AllowlistRedirectHandler(
            "https://lxzy.my/version.json"
        )

        req = updater.urllib.request.Request("https://lxzy.my/version.json")
        with pytest.raises(ValueError):
            untrusted_handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )
        assert base_calls == []

        req2 = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        result = trusted_handler.redirect_request(
            req2,
            None,
            302,
            "Found",
            {},
            "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
        )
        assert base_calls == [1]
        assert result == "built-request"

    def test_chained_redirects_within_one_call_retain_provenance(
        self, monkeypatch
    ):
        # Covers spec scenario "Chained redirects within one call retain
        # correct provenance" -- more than one hop before reaching the CDN,
        # all validated by the same handler instance.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )

        req = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        # First hop: github.com -> github.com intermediate redirect page,
        # still allowlisted (not the CDN), must reach base normally.
        result1 = handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "https://github.com/lxzydev/QuickRes/releases/download/v1/asset-redirect",
        )
        assert base_calls == [1]
        assert result1 == "built-request"

        # Second hop: same handler instance, now redirecting to the CDN --
        # provenance from the original origin must still apply.
        req2 = updater.urllib.request.Request(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/asset-redirect"
        )
        result2 = handler.redirect_request(
            req2,
            None,
            302,
            "Found",
            {},
            "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
        )
        assert base_calls == [1, 1]
        assert result2 == "built-request"

    def test_intermediate_non_github_hop_revokes_cdn_trust(self, monkeypatch):
        # Round 28 finding: `_release_cdn_trusted` was computed ONCE at
        # construction from the caller-supplied origin_url and never
        # re-checked per hop, so a chain that starts at a trusted
        # github.com origin but routes through an intermediate
        # non-github.com hop (e.g. lxzy.my) before reaching the CDN kept
        # its ORIGINAL trust -- even though the immediately-preceding hop
        # was not itself a repo-scoped github.com URL. Trust must be
        # re-derived from the actual immediately-preceding request
        # (`req.full_url`) on every hop, not just inherited from
        # construction.
        base_calls = self._base_redirect_request_tracker(monkeypatch)
        handler = updater._AllowlistRedirectHandler(
            "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
        )
        req_intermediate = updater.urllib.request.Request(
            "https://lxzy.my/some/unrelated/path"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req_intermediate,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/release-assets/QuickRes.exe",
            )

        assert base_calls == []


class TestApplyUpdateUsesRedirectValidatingOpener:
    def test_apply_update_installs_allowlist_redirect_handler(
        self, monkeypatch, tmp_path
    ):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        valid_payload = bytes(header) + b"PE\x00\x00" + b"restofheader"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return valid_payload

        build_opener_calls = []

        class _FakeOpener:
            def open(self, request, timeout=None):
                assert timeout == 30
                return _FakeResp()

        def _fake_build_opener(*handlers):
            build_opener_calls.append(handlers)
            return _FakeOpener()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", _fake_build_opener
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert len(build_opener_calls) == 1
        assert any(
            isinstance(h, updater._AllowlistRedirectHandler)
            for h in build_opener_calls[0]
        )

    def test_apply_update_wires_download_url_as_handler_origin(
        self, monkeypatch, tmp_path
    ):
        # Round 28 finding: the isinstance-only check above cannot tell
        # apart a correctly-wired `_AllowlistRedirectHandler(download_url)`
        # from a regressed bare `_AllowlistRedirectHandler()` -- both are
        # still instances of the same class. A repo-scoped github.com
        # download_url must produce a handler whose own provenance flag is
        # actually True, proving the origin was really threaded through
        # (a bare-constructed handler would read False here instead).
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        valid_payload = bytes(header) + b"PE\x00\x00" + b"restofheader"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return valid_payload

        build_opener_calls = []

        class _FakeOpener:
            def open(self, request, timeout=None):
                return _FakeResp()

        def _fake_build_opener(*handlers):
            build_opener_calls.append(handlers)
            return _FakeOpener()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", _fake_build_opener
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update(
                "https://github.com/lxzydev/QuickRes/releases/download/v1/QuickRes.exe"
            )

        handler = next(
            h
            for h in build_opener_calls[0]
            if isinstance(h, updater._AllowlistRedirectHandler)
        )
        assert handler._release_cdn_trusted is True
