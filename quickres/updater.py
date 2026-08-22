import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request

from quickres import UPDATE_URL
from quickres.config import LOG_PATH, _open_no_reparse_follow, log_msg
from quickres.display import _escape_ps_single_quoted

# Same allowlist discipline as webview/bridge.py's open_external --
# require https + an explicit host allowlist before ever opening a download
# URL. UPDATE_URL (lxzy.my/version.json) is only the version-CHECK endpoint;
# the real download_url comes from that endpoint's own JSON response, so this
# allowlist covers the project's actual release-asset hosts: lxzy.my (same
# domain as the version-check endpoint) and github.com (GitHub Releases is a
# common asset host for this kind of app). RISK: the exact intended download
# host could not be verified from the repo alone -- this is the most
# defensible default, flagged for maintainer confirmation.
_DOWNLOAD_URL_ALLOWED_HOSTS = {"lxzy.my", "github.com"}

# A bare github.com host check accepts ANY repository's release assets, not
# just this project's own. The trusted version-check response (lxzy.my) is
# the only thing that ever supplies download_url, so a host-only allowlist
# relies entirely on that endpoint never being tricked into naming someone
# else's github.com release asset; the SHA-256 integrity gate that would
# otherwise catch a substituted binary is also currently dormant (see the
# _HASH_FIELD_NAME note above -- the live version.json response does not yet
# supply that field). Scoping the github.com allowlist entry down to this
# project's own repository path shrinks that gap: a URL now has to fall
# under this project's actual GitHub identity, the same one webview/bridge.py
# names as the target of panel.html's GitHub link
# (https://github.com/lxzydev/QuickRes), not merely live on github.com at
# all. This does not depend on or change the lxzy.my host check above, which
# stays host-only exactly as it already was.
_GITHUB_REPO_PATH_PREFIX = "/lxzydev/QuickRes/"
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


def _has_https_scheme_and_allowed_host(url: str, allowed_hosts) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in allowed_hosts


def _validate_download_url(url: str) -> None:
    if not _has_https_scheme_and_allowed_host(url, _DOWNLOAD_URL_ALLOWED_HOSTS):
        raise ValueError(f"Refusing to download update from non-allowlisted URL: {url!r}")
    parsed = urllib.parse.urlsplit(url)
    # Round 28 finding: comparing the RAW, unnormalized path let a URL
    # containing enough `../` segments to actually escape the repo scope
    # after normalization (standard RFC 3986 behavior for most HTTP
    # servers/CDNs, including how GitHub's own edge is expected to route)
    # still pass, since the raw string can literally start with
    # "/lxzydev/QuickRes/" while normalizing to somewhere else entirely.
    # posixpath.normpath collapses `..`/`.` segments the same way; this
    # module's new CDN-redirect-trust mechanism (_origin_permits_release_cdn)
    # builds its trust decision on this exact check passing, so a bypass
    # here would also earn undeserved CDN-redirect trust.
    normalized_path = posixpath.normpath(parsed.path)
    if parsed.hostname == "github.com" and not normalized_path.startswith(
        _GITHUB_REPO_PATH_PREFIX
    ):
        raise ValueError(
            f"Refusing to download update from a github.com URL outside "
            f"this project's own repository ({_GITHUB_REPO_PATH_PREFIX!r}): "
            f"{url!r}"
        )


# Round 25 finding: a real GitHub Releases redirect
# (github.com/lxzydev/QuickRes/... -> objects.githubusercontent.com) was
# being rejected outright, because the CDN host is intentionally NOT in
# _DOWNLOAD_URL_ALLOWED_HOSTS -- a server-supplied CDN host would defeat the
# allowlist entirely (see _origin_permits_release_cdn below: this constant
# is hardcoded, NEVER read from any network response). GitHub has also been
# migrating release-asset redirects between these two hosts, so both are
# listed; each is only ever reachable through the provenance gate below, so
# listing both widens nothing outside a repo-scoped GitHub origin.
_GITHUB_RELEASE_CDN_HOSTS = frozenset(
    {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)


def _origin_permits_release_cdn(origin_url) -> bool:
    """Returns True only when `origin_url` (a) already passed
    `_validate_download_url` AND (b) has hostname exactly "github.com" --
    which, combined with (a), implies the origin's path also passed the
    `_GITHUB_REPO_PATH_PREFIX` check. Condition (b) is NOT redundant with
    (a): `lxzy.my` origins independently satisfy (a) but must NOT satisfy
    (b), so they must never earn CDN-redirect trust.
    """
    if not isinstance(origin_url, str):
        return False
    try:
        _validate_download_url(origin_url)
    except ValueError:
        return False
    return urllib.parse.urlsplit(origin_url).hostname == "github.com"


def _is_release_cdn_url(url: str) -> bool:
    return _has_https_scheme_and_allowed_host(url, _GITHUB_RELEASE_CDN_HOSTS)


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """`_validate_download_url` only ever validates the
    INITIAL `download_url` passed into `apply_update`. Python's default
    urlopen() opener transparently follows HTTP redirects (301/302/303/307)
    to ANY host with zero re-validation -- so a real GitHub Releases asset
    URL (github.com, allowlisted, the exact scenario this module's own
    allowlist comment names) redirecting to
    objects.githubusercontent.com (NOT allowlisted) would bypass the
    allowlist entirely once the redirect happened. Checking `resp.geturl()`
    after the fact would be too late: the (possibly unauthorized) request to
    the redirect target -- headers, timing, connection -- already happened
    by then, which is itself part of what the allowlist is meant to
    prevent.

    This handler re-validates every redirect target with
    `_validate_download_url` BEFORE `HTTPRedirectHandler` is allowed to
    build the follow-up request, so an unallowlisted redirect target is
    refused pre-flight rather than merely detected post-hoc.

    `origin_url` is the caller's own already-known request URL for this one
    call (see `fetch_version_info`/`apply_update`). Whether THAT origin
    earns CDN-redirect trust is computed ONCE here, at construction time,
    and held only as per-instance state -- never module-level or otherwise
    shared across calls -- so it cannot leak between unrelated requests.
    Defaulting to `None` fails closed (no provenance) and keeps direct
    no-arg construction (as in existing tests) unchanged.

    Round 28 finding: that construction-time flag alone is NOT sufficient
    for a multi-hop redirect chain -- `redirect_request` also re-checks
    `req.full_url` (the URL of the request that just received THIS
    specific redirect, i.e. the immediately-preceding hop) on every call,
    and only grants CDN trust when BOTH the original construction-time
    origin AND the immediately-preceding hop independently qualify.
    Without this, a chain that starts at a trusted github.com origin but
    is redirected through an intermediate non-github.com hop (e.g.
    lxzy.my) before reaching the CDN would keep the ORIGINAL trust even
    though the hop that actually preceded the CDN redirect was never
    itself a repo-scoped github.com URL.
    """

    def __init__(self, origin_url=None):
        super().__init__()
        # Deliberately NOT named `_origin_permits_release_cdn` -- that name
        # is already the module-level function computing this value, and a
        # same-named instance attribute would shadow it, inviting a future
        # `self._origin_permits_release_cdn(...)` call-site typo to try
        # calling a bool instead of the function.
        self._release_cdn_trusted = _origin_permits_release_cdn(origin_url)

    def _redirect_skips_standard_validation(self, req, newurl) -> bool:
        """True only when ALL THREE hold: (1) this handler's own
        construction-time origin earned CDN trust, (2) the request that
        just received THIS redirect (`req.full_url`, the immediately
        preceding hop) independently earns it too, and (3) the redirect
        target is actually one of the trusted CDN hosts. Named for what it
        returns (may `_validate_download_url` be skipped for `newurl`),
        not for any one of the three conditions alone.
        """
        return (
            self._release_cdn_trusted
            and _origin_permits_release_cdn(req.full_url)
            and _is_release_cdn_url(newurl)
        )

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self._redirect_skips_standard_validation(req, newurl):
            _validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Real update-available detection: `webview/bridge.py`'s `check_updates()`
# compares `fetch_version_info()`'s response against `quickres.__version__`
# so the "Updates" button/dialog has something real to trigger on, rather
# than just returning the raw response with nothing ever comparing it.
#
# Like `_HASH_FIELD_NAME` above, the server-side version.json schema is
# external to this repo and not otherwise documented anywhere in it -- the
# only field this module (or anything else in the codebase) has ever named
# on that response is `_HASH_FIELD_NAME` ("sha256"). `_VERSION_FIELD_NAME`
# ("version") is this module's own best-effort convention: a plain top-level
# string field on the same response, matching the project's `__version__`
# format (see quickres/__init__.py, e.g. "1.0.7"). *** ACTION NEEDED FROM
# THE SERVER/PROJECT MAINTAINER ***: confirm the real version.json schema
# actually uses this key; if it uses a different one, only this constant
# needs to change.
_VERSION_FIELD_NAME = "version"


def _parse_version_tuple(version_str):
    """Best-effort `x.y.z...` integer-tuple parse of a semver-ish version
    string (an optional leading 'v', digits, dot-separated -- e.g. "1.0.7"
    or "v1.0.7"). A pre-release/build suffix ("1.2.3-beta", "1.2.3+build4")
    is truncated at the first non-digit-non-dot run, matching the format
    `quickres.__version__` actually uses today. Returns `None` for anything
    that isn't a string or doesn't start with a recognizable numeric
    version at all, so callers can fail closed instead of guessing.
    """
    if not isinstance(version_str, str):
        return None
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", version_str)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(current: str, remote) -> bool:
    """True only when `remote` parses to a version STRICTLY greater than
    `current`. Shorter tuples are zero-padded before comparing (so "1.2" is
    treated as equal to "1.2.0", never as older). Fails closed (`False`) on
    anything unparsable on either side -- an update is never reported as
    available on ambiguous/malformed data, matching this module's existing
    fail-closed convention (e.g. `_looks_like_pe_executable`, the SHA-256
    gate above).
    """
    current_tuple = _parse_version_tuple(current)
    remote_tuple = _parse_version_tuple(remote)
    if current_tuple is None or remote_tuple is None:
        return False
    length = max(len(current_tuple), len(remote_tuple))
    current_padded = current_tuple + (0,) * (length - len(current_tuple))
    remote_padded = remote_tuple + (0,) * (length - len(remote_tuple))
    return remote_padded > current_padded


def update_available(current_version: str, version_info) -> bool:
    """True when `version_info` (`fetch_version_info()`'s raw response)
    names a version genuinely newer than `current_version`. Reads the
    remote version out from under `_VERSION_FIELD_NAME` and fails closed
    (`False`) if `version_info` isn't a dict, or the field is missing --
    owns the whole "does the response say there's an update" decision so
    callers (`webview/bridge.py`'s `check_updates()`) never need to know
    the field name themselves.
    """
    remote_version = (
        version_info.get(_VERSION_FIELD_NAME) if isinstance(version_info, dict) else None
    )
    return is_newer_version(current_version, remote_version)


# Round 26 finding: the live version.json response actually names the
# download URL "url", not "download_url". Neither fetch_version_info() nor
# apply_update() ever assumed a specific key here -- apply_update() always
# receives download_url explicitly from its caller -- but panel.html reads
# `S.updateInfo.download_url` off check_updates()'s raw passthrough, so a
# server response using "url" left that field undefined and made the
# "Update Now" button a silent no-op (panel.html's startUpdateDownload()
# returns early with no error when download_url is falsy). This is the same
# kind of server-schema convention as `_VERSION_FIELD_NAME` above:
# `_DOWNLOAD_URL_FIELD_NAME` is this module's own best-effort convention
# going forward, `_DOWNLOAD_URL_FALLBACK_FIELD_NAME` covers the server's
# actual current key so today's live response keeps working without a
# server-side change.
_DOWNLOAD_URL_FIELD_NAME = "download_url"
_DOWNLOAD_URL_FALLBACK_FIELD_NAME = "url"


def resolve_download_url(version_info):
    """Returns the download URL named in `version_info` under
    `_DOWNLOAD_URL_FIELD_NAME` if present, else `_DOWNLOAD_URL_FALLBACK_FIELD_NAME`
    (the server's actual current key), else `None`. Owns the field-name
    convention so callers (`webview/bridge.py`'s `check_updates()`) never
    need to know either key themselves, matching `update_available()`'s
    existing pattern for `_VERSION_FIELD_NAME`.

    Fails closed to `None` on a non-string value under either field --
    `_validate_download_url()` (the only place this return value is ever
    handed to) parses it with `urllib.parse.urlsplit()`, which raises an
    unhandled `AttributeError` on a non-string input instead of the
    intended, cleanly-reported `ValueError`. A present-but-falsy
    `download_url` (`""`, `None`) still falls back to `url` deliberately:
    an empty string is not a usable URL either, so treating it the same as
    a missing key is correct, not a bug to "fix" by distinguishing them.

    Each field is checked independently rather than joined with a single
    `A.get() or B.get()` -- that pattern short-circuits on ANY truthy
    primary value, including a truthy-but-non-string one (e.g. a stray
    integer under `download_url`), discarding a perfectly usable `url`
    fallback instead of ever trying it.
    """
    if not isinstance(version_info, dict):
        return None
    primary = version_info.get(_DOWNLOAD_URL_FIELD_NAME)
    if isinstance(primary, str) and primary:
        return primary
    fallback = version_info.get(_DOWNLOAD_URL_FALLBACK_FIELD_NAME)
    return fallback if isinstance(fallback, str) and fallback else None


def fetch_version_info():
    request = urllib.request.Request(
        UPDATE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"
        },
    )
    # The bare module-level urlopen()'s process-wide default opener does NOT
    # have _AllowlistRedirectHandler installed and would follow redirects to
    # any host unvalidated -- inconsistent with apply_update()'s download
    # path, which re-validates every redirect target against the host
    # allowlist. Use the same dedicated opener here so a redirected
    # version-check response is re-validated too.
    opener = urllib.request.build_opener(_AllowlistRedirectHandler(UPDATE_URL))
    with opener.open(request, timeout=5) as resp:
        return json.loads(resp.read().decode())


# Acknowledged design gap: checking only structural PE-header validity
# (_looks_like_pe_executable) would let any attacker-built PE with valid
# MZ/PE magic bytes pass. Real
# code-signing verification (a signing certificate + a signed release
# pipeline embedding/verifying a public key) is genuinely out of scope here:
# it requires external release-pipeline infrastructure this repo does not
# have. This is a pragmatic, PARTIAL, CLIENT-SIDE mitigation instead: if the
# version-check JSON response (fetch_version_info()'s return value) carries
# an expected SHA-256 under this field name, the downloaded file's actual
# hash is verified against it before staging.
#
# *** ACTION NEEDED FROM THE SERVER/PROJECT MAINTAINER ***: the CURRENT
# version.json response served from UPDATE_URL does not include this field
# (verified: no such field is documented or referenced anywhere else in this
# repo), so today this gate is dormant/no-op by construction -- it silently
# skips (see the "no expected sha256 supplied" log_msg below) rather than
# failing, which is required for backward compatibility with the existing
# server response. For this to actually engage as a real integrity gate,
# the server-side version.json schema needs to start emitting this field
# (the release pipeline computing sha256 of the published asset and adding
# it to the JSON payload), and the caller (webview/bridge.py's
# Api.confirm_update, currently `updater.confirm_update(download_url)` with
# no version_info argument) needs to be updated to pass the fetched
# version-check response through as `version_info` -- both are outside this
# module's scope.
_HASH_FIELD_NAME = "sha256"

# expected_sha256 (the value read out from _HASH_FIELD_NAME) comes straight
# from parsed JSON on the version-check network response, with no format
# validation anywhere else in this module. A sha256 hash is always exactly
# 64 hexadecimal characters, so anything else is not a hash at all -- either
# a confused/corrupted server response or (worst case) an attempt to smuggle
# quoting/control characters into the generated update.bat's PowerShell
# reverify command below. _build_reverify_command treats a value that fails
# this check the same way it already treats a missing one: log and skip the
# hash check rather than interpolating an unparseable value.
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _compute_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_progress(callback, stage: str, downloaded_bytes: int = 0,
                     total_bytes: int | None = None, error: str | None = None) -> None:
    """Best-effort progress notification for the pywebview update UI.

    A progress observer must never be able to break a verified update.  The
    actual file operation remains the source of truth; failures in a UI
    callback are logged and otherwise ignored.
    """
    if callback is None:
        return
    try:
        callback({
            "stage": stage,
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "error": error,
        })
    except Exception as exc:
        log_msg(f"Update progress callback failed: {exc}")


def _response_content_length(response) -> int | None:
    headers = getattr(response, "headers", None)
    raw_value = headers.get("Content-Length") if headers is not None else None
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _write_download(response, out_file, progress_callback=None) -> None:
    """Copy an HTTP response while reporting byte progress when available."""
    total_bytes = _response_content_length(response)
    downloaded_bytes = 0
    _report_progress(progress_callback, "downloading", downloaded_bytes, total_bytes)

    # Existing unit-test response doubles intentionally only provide
    # read(), while the real urllib response provides Content-Length.  Keep
    # the no-length path compatible and avoid buffering real known-size
    # downloads in memory.
    if total_bytes is None:
        payload = response.read()
        out_file.write(payload)
        _report_progress(progress_callback, "downloading", len(payload), None)
        return

    while True:
        chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
        if not chunk:
            break
        out_file.write(chunk)
        downloaded_bytes += len(chunk)
        _report_progress(progress_callback, "downloading", downloaded_bytes, total_bytes)


class UpdateJob:
    """Thread-safe download/verify phase of an automatic update.

    The job deliberately stops at ``ready``.  The bridge starts the final
    replacement only after the UI observes that state, giving it a chance to
    refresh the progress message while keeping monitor/hotkey shutdown under
    the normal locked bridge path.
    """

    def __init__(self, download_url: str, version_info=None):
        self._download_url = download_url
        self._version_info = version_info
        self._lock = threading.Lock()
        self._thread = None
        self._state = {
            "stage": "idle",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "error": None,
        }

    @property
    def version_info(self):
        return self._version_info

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None:
                return False
            self._state["stage"] = "downloading"
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set_progress(self, progress: dict) -> None:
        with self._lock:
            self._state.update(progress)

    def _run(self) -> None:
        try:
            apply_update(
                self._download_url,
                version_info=self._version_info,
                progress_callback=self._set_progress,
                download_only=True,
            )
        except Exception as exc:
            log_msg(f"Update download failed: {exc}")
            self._set_progress({"stage": "failed", "error": str(exc)})
            return
        self._set_progress({"stage": "ready", "error": None})


def _looks_like_pe_executable(path: str) -> bool:
    """Cheap defense-in-depth integrity check on a downloaded update binary.

    Full code-signing verification would require the release pipeline to
    sign binaries and this app to embed/verify a public key -- out of scope
    here. Instead, do the minimal DOS/PE header sanity check: the file must
    start with the 'MZ' DOS-stub magic and its `e_lfanew` field (a 4-byte
    little-endian offset at DOS-header offset 0x3C) must point to a valid
    'PE\\0\\0' signature. This catches truncated/corrupted-but-200-OK
    downloads and non-executable payloads (HTML error pages, etc.) before
    they are ever staged/launched. It does NOT verify the binary's
    authenticity/provenance -- only that it is *structurally* a Windows PE.

    apply_update() ALSO accepts an optional
    `version_info` dict and, if it carries a `_HASH_FIELD_NAME` ("sha256")
    field, verifies the downloaded file's actual SHA-256 against it (see
    `_compute_sha256` and the check right after this function's call site in
    apply_update). That is a strictly stronger, additional check layered on
    top of this one -- this function's own structural PE-header check still
    runs unconditionally and is unaffected by whether a hash was supplied.
    """
    try:
        with open(path, "rb") as f:
            dos_header = f.read(64)
            if len(dos_header) < 64 or dos_header[0:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(dos_header[60:64], "little")
            f.seek(pe_offset)
            pe_signature = f.read(4)
            return pe_signature == b"PE\x00\x00"
    except OSError:
        return False


def _escape_batch_percent(value: str) -> str:
    """Escape literal ``%`` characters for safe interpolation into a
    cmd.exe batch script's command text (a double-quoted path argument, or
    a `-Command "..."` argument that is itself parsed off a batch line).

    cmd.exe expands `%name%` into an environment variable's value wherever
    that pattern appears on a batch line -- including inside double
    quotes, since this is a lexical substitution done while the line is
    parsed, not a quoting-aware transform. A literal `%` in interpolated
    text must therefore be written as `%%` (doubled) so cmd.exe treats it
    as a literal character; otherwise a path segment that happens to look
    like `%SOMENAME%` is silently replaced (typically with an empty
    string, since such names are rarely real environment variables),
    corrupting which file the surrounding command actually targets.
    """
    return value.replace("%", "%%")


_BATCH_LABEL_DEF_RE = re.compile(r"^:([A-Za-z_][A-Za-z0-9_]*)\s*$")
_BATCH_GOTO_REF_RE = re.compile(
    r"goto\s+:?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)


def _extract_batch_label_graph(bat_contents: str):
    """Parse a generated cmd.exe batch script's text and return
    ``(defined_labels, referenced_labels)``: the set of label names the
    script defines (a line consisting of ``:label_name`` on its own, cmd.exe
    label syntax) and the set of label names any ``goto`` statement in the
    script references (``goto label`` or ``goto :label``, case-insensitive,
    since both cmd.exe labels and the ``goto`` keyword itself are
    case-insensitive). Both sets are lower-cased so comparisons between them
    are correct regardless of casing differences between a definition and
    its references.

    This is a plain-text, line-oriented parser -- it knows nothing about
    cmd.exe control flow beyond "does this label name exist somewhere in
    this script", which is exactly what `_validate_batch_label_graph` below
    needs to catch a typo'd `goto` target.
    """
    defined_labels = set()
    for line in bat_contents.splitlines():
        stripped = line.strip()
        match = _BATCH_LABEL_DEF_RE.match(stripped)
        if match:
            defined_labels.add(match.group(1).lower())

    referenced_labels = {
        m.group(1).lower() for m in _BATCH_GOTO_REF_RE.finditer(bat_contents)
    }

    return defined_labels, referenced_labels


def _validate_batch_label_graph(bat_contents: str) -> None:
    """Static consistency check for a generated update.bat's label/goto
    graph: every label name any `goto` in `bat_contents` targets must have a
    matching `:label` definition somewhere in that same script.

    `apply_update` builds its multi-step update.bat as one large
    string-concatenated literal with several `goto`/label branches (rename
    retry loop, reverify-failure/move-failure restore path, launch
    confirmation, cleanup). Nothing else in this module or the test suite
    statically checks that every `goto` target actually resolves -- a
    typo'd label introduced by a future edit to that literal would
    otherwise only be discovered at runtime, by an actual failed update on
    a user's machine, rather than by this check.

    Raises `AssertionError` naming every dangling `goto` target (a
    referenced label with no matching definition) if any exist. Does not
    return or raise anything for a defined-but-never-`goto`'d label -- a
    label can legitimately be reached purely by falling through from the
    line above it (several branches in the real script rely on exactly
    that), so an unreferenced definition is not, by itself, a defect.
    """
    defined_labels, referenced_labels = _extract_batch_label_graph(bat_contents)
    dangling = sorted(referenced_labels - defined_labels)
    assert not dangling, (
        f"generated batch script has goto target(s) with no matching label "
        f"definition: {dangling!r}"
    )


def _build_reverify_command(new_exe_path: str, expected_sha256) -> str:
    """Build the `update.bat` step that re-verifies the staged
    `QuickRes_new.exe` IMMEDIATELY BEFORE the `move /y` that stages it, to
    close the TOCTOU window rather than trusting the earlier Python-side
    check (which ran once, right after download, up to several seconds
    before this point) blindly.

    A lightweight `powershell -NoProfile -NonInteractive -Command`
    one-liner re-reads the file's DOS/PE header and re-checks the same
    'MZ' + `e_lfanew` + 'PE\\0\\0' structural signature that
    `_looks_like_pe_executable` already checks on the Python side (see that
    function's docstring for why this is a *structural*, not
    provenance/authenticity, check). If `expected_sha256` was supplied
    (i.e. the version-check response carried one -- see the
    `_HASH_FIELD_NAME` NOTE above), the SHA-256 is ALSO recomputed and
    compared again here, matching the strength of the Python-side check.
    The command exits non-zero (via PowerShell's implicit exit-code
    propagation) on any failure, so the caller's
    `if errorlevel 1 goto :restore` gate rejects a swapped/corrupted file
    right before it would ever be moved into place and executed.

    `new_exe_path` lands in two nested quoting contexts at once: it is
    interpolated into a PowerShell single-quoted string literal, AND that
    whole `-Command "..."` argument is itself one line of the generated
    batch script, parsed by cmd.exe before PowerShell ever sees it. It is
    therefore escaped twice, independently, for each context: doubled `'`
    (`_escape_ps_single_quoted`, same convention `quickres/display.py`
    already uses for its own PowerShell one-liners) so an embedded single
    quote cannot close the literal early, and doubled `%`
    (`_escape_batch_percent`) so cmd.exe's line-parsing does not treat an
    embedded `%name%` run as an environment-variable reference.

    `expected_sha256` sits in the exact same two nested quoting contexts
    (it is interpolated into the same kind of PowerShell single-quoted
    literal, inside the same batch line) and comes from the same
    externally-controlled JSON response `new_exe_path`'s directory
    ultimately derives from, so it gets the identical two-layer escaping
    treatment. On top of that, it is also format-validated against
    `_SHA256_HEX_RE` first (see that constant's comment) -- a value that
    isn't exactly 64 hex characters is not a hash at all, and is logged and
    skipped rather than interpolated, the same way a missing value already
    is.
    """
    ps_safe_path = _escape_batch_percent(_escape_ps_single_quoted(new_exe_path))
    checks = [
        f"$b=[System.IO.File]::ReadAllBytes('{ps_safe_path}')",
        "if ($b.Length -lt 64 -or $b[0] -ne 0x4D -or $b[1] -ne 0x5A) { exit 1 }",
        "$off=[BitConverter]::ToInt32($b,60)",
        "if ($off -lt 0 -or ($off+4) -gt $b.Length) { exit 1 }",
        "if (-not ($b[$off] -eq 0x50 -and $b[$off+1] -eq 0x45 -and "
        "$b[$off+2] -eq 0 -and $b[$off+3] -eq 0)) { exit 1 }",
    ]
    if expected_sha256:
        expected_sha256_str = str(expected_sha256).upper()
        if _SHA256_HEX_RE.match(expected_sha256_str):
            ps_safe_hash = _escape_batch_percent(
                _escape_ps_single_quoted(expected_sha256_str)
            )
            checks.append(
                f"$h=(Get-FileHash -Algorithm SHA256 -Path '{ps_safe_path}').Hash"
            )
            checks.append(f"if ($h -ne '{ps_safe_hash}') {{ exit 1 }}")
        else:
            log_msg(
                "Update version-check response supplied a malformed sha256 "
                "field (expected exactly 64 hexadecimal characters); "
                "skipping the SHA-256 reverify check rather than trusting "
                f"an unparseable value: {expected_sha256!r}"
            )
    ps_command = "; ".join(checks)
    return f'powershell -NoProfile -NonInteractive -Command "{ps_command}"\n'


def _build_launch_healthcheck_command(exe_path: str) -> str:
    """Start the replacement client and verify that *that process* stays alive.

    ``tasklist | find`` only searches by image name.  Besides taking a long
    time to poll, it can see an unrelated QuickRes process (or miss a process
    whose image-name lookup is delayed by Explorer, OneDrive or Defender).
    PowerShell's ``Start-Process -PassThru`` gives the updater the exact
    process it created for the exact replacement path.  A short health check
    then distinguishes an immediate loader/startup failure from a client that
    is merely still initializing its UI, without making the batch script wait
    through the old 36-second adaptive ``tasklist`` loop.

    The executable and working-directory paths both live in nested
    PowerShell-single-quote and cmd.exe-batch contexts, so use the same
    two-layer escaping as the pre-move re-verification command.
    """
    exe_dir = os.path.dirname(exe_path)
    ps_safe_path = _escape_batch_percent(_escape_ps_single_quoted(exe_path))
    ps_safe_dir = _escape_batch_percent(_escape_ps_single_quoted(exe_dir))
    ps_command = (
        "$ErrorActionPreference='Stop'; "
        "try { "
        f"$p=Start-Process -FilePath '{ps_safe_path}' "
        f"-WorkingDirectory '{ps_safe_dir}' -PassThru; "
        "Start-Sleep -Seconds 2; "
        "if ($p.HasExited) { exit 1 }; "
        "exit 0 "
        "} catch { exit 1 }"
    )
    return f'powershell -NoProfile -NonInteractive -Command "{ps_command}"\n'


# Round 28 finding: cmd.exe's `timeout` (and `ping`-based delay tricks) rely
# on an attached console -- under the exact flags apply_update() actually
# launches update.bat with (subprocess.CREATE_NO_WINDOW |
# subprocess.DETACHED_PROCESS, see below), `timeout.exe` has no console to
# read from and exits almost immediately with a non-zero errorlevel
# instead of waiting. Measured directly: `timeout /t 3 /nobreak >nul` under
# those same flags completed in ~50ms, not 3000ms. This silently no-op'd
# BOTH the Round 27 pre-launch settle delay below AND this script's
# pre-existing between-retries wait inside `:launchtry` -- neither ever
# actually waited. PowerShell's `Start-Sleep`, already proven to work
# under these exact flags by `_build_launch_healthcheck_command` above,
# does not have this problem (measured: `Start-Sleep -Seconds 2` under the
# same no-console flags took ~2.4s, as requested).
_NO_CONSOLE_SAFE_DELAY_CMD = (
    'powershell -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 1"\n'
)


def apply_update(download_url, version_info=None, *, progress_callback=None,
                 download_only: bool = False, reuse_download: bool = False):
    """Download, verify and stage an update.

    ``download_only`` is used by :class:`UpdateJob` to keep the network
    operation off the UI bridge thread.  ``reuse_download`` performs only
    the short replacement/restart phase against the already verified staged
    ``QuickRes_new.exe``.  The default remains the original one-shot flow.
    """
    if reuse_download and download_only:
        raise ValueError("An update cannot be download-only and reuse a download")
    if not reuse_download:
        _validate_download_url(download_url)

    exe_path = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe_path)
    exe_name = os.path.basename(exe_path)
    new_exe_path = os.path.join(exe_dir, "QuickRes_new.exe")
    old_exe_path = os.path.join(exe_dir, f"{exe_name}.old")
    prev_old_exe_path = os.path.join(exe_dir, f"{exe_name}.old.prev")
    bat_path = os.path.join(exe_dir, "update.bat")

    # A download failure must propagate as a raised exception here rather
    # than being caught and swallowed, since the headless pywebview
    # architecture has no window to show an error popup against directly.
    # Letting the exception propagate out of this function lets
    # `updater.confirm_update`/`bridge_op` convert it into a
    # `{ok:false, message:...}` envelope that reaches the JS panel, so
    # update failures are reported to the UI instead of failing silently
    # (e.g. urllib.error.URLError/socket.timeout).
    if not reuse_download:
        request = urllib.request.Request(
            download_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"
            },
        )
        # The bare module-level urlopen() uses Python's process-wide default
        # opener, which does NOT have _AllowlistRedirectHandler installed and
        # would silently follow redirects to any host. A dedicated opener with
        # that handler must be built and used here so redirect targets are
        # actually re-validated for THIS request.
        opener = urllib.request.build_opener(_AllowlistRedirectHandler(download_url))
        # Same TOCTOU/symlink concern quickres/config.py's write_json_atomic
        # already guards against: plain open(new_exe_path, "wb") would
        # transparently follow an NTFS reparse point (symlink/junction) planted
        # at that path and truncate whatever it points at with the downloaded
        # bytes, before any later check could refuse it. Opened instead through
        # the same _open_no_reparse_follow() atomic call config.py uses, which
        # opens a reparse point as itself and reports it via a None return
        # rather than ever following it -- see that function's own docstring.
        with opener.open(request, timeout=30) as resp:
            out_file = _open_no_reparse_follow(new_exe_path, binary=True)
            if out_file is None:
                raise OSError(
                    f"{new_exe_path} is a reparse point/symlink; refusing to "
                    f"write the downloaded update through it"
                )
            with out_file:
                _write_download(resp, out_file, progress_callback)

    # Pragmatic, cheap defense-in-depth: verify the downloaded bytes are at
    # least a plausible Windows PE executable before ever staging/launching
    # them. This does not replace real code-signing verification (which
    # would need a release-pipeline signature + an embedded public key --
    # out of scope here), but it does catch a truncated/corrupted-but-200-OK
    # response body. Fail closed: clean up the bad download and refuse to
    # proceed.
    _report_progress(progress_callback, "verifying")
    if not _looks_like_pe_executable(new_exe_path):
        try:
            os.remove(new_exe_path)
        except OSError:
            pass
        raise ValueError(
            f"Downloaded update failed integrity check (not a valid Windows "
            f"PE executable) -- refusing to stage {new_exe_path!r}"
        )

    # Client-side SHA-256 verification, gated on the
    # version-check response actually supplying an expected hash (see the
    # module-level NOTE above _HASH_FIELD_NAME for why this is dormant until
    # a server-side + bridge.py change ships). Fail closed on mismatch,
    # cleaning up the bad download the same way the PE check above does.
    # Absent the field, skip gracefully -- must not break current behavior.
    expected_sha256 = (version_info or {}).get(_HASH_FIELD_NAME)
    if expected_sha256:
        actual_sha256 = _compute_sha256(new_exe_path)
        if actual_sha256.lower() != str(expected_sha256).lower():
            try:
                os.remove(new_exe_path)
            except OSError:
                pass
            raise ValueError(
                f"Downloaded update failed SHA-256 integrity check "
                f"(expected {expected_sha256!r}, got {actual_sha256!r}) -- "
                f"refusing to stage {new_exe_path!r}"
            )
    else:
        log_msg(
            "Update download has no expected sha256 in the version-check "
            "response; skipping hash verification (server-side version.json "
            "does not supply this field yet)."
        )

    if download_only:
        return {"staged_path": new_exe_path}

    _report_progress(progress_callback, "installing")

    # The backup (".old") rename must NOT be
    # deleted unconditionally right after the move succeeds -- "moved
    # successfully" says nothing about whether the moved exe is actually
    # valid/launchable. Deletion is deferred until AFTER the existing
    # process-specific launch health check confirms the new exe actually
    # started (see `:confirmed`); if it never gets confirmed, `:launchfail`
    # automatically restores the ".old"
    # backup back onto the canonical exe path -- a build that passed every
    # structural/hash check can still fail to actually run (a missing
    # dependency DLL, a wrong CPU architecture), and there is no one at the
    # keyboard to notice and roll back by hand once this detached script is
    # running, so the rollback has to be automatic here to avoid leaving the
    # machine on an exe that will not start.
    #
    # This same principle also extends to a backup left over from an
    # EARLIER, still-unconfirmed update attempt: deleting any pre-existing
    # "<exe>.old" unconditionally as the very first step, purely to free up
    # that name for the `ren` below, would discard the prior attempt's
    # rollback backup before this attempt had proven anything about
    # itself. Instead, a pre-existing "<exe>.old" is renamed (not deleted)
    # to "<exe>.old.prev", keeping the canonical name free for this
    # attempt's own backup without destroying the previous one. That
    # prior backup is only deleted once THIS attempt reaches `:confirmed`;
    # if this attempt fails validation (`:restore`) or fails launch
    # confirmation (`:launchfail`), the same rename sequence restores it
    # back onto the canonical "<exe>.old" name, so it is still the backup
    # in place the next time an update is attempted (both branches use the
    # identical restore-then-rename pattern, just against different files --
    # see each label's own comment below).
    #
    # TOCTOU window: the PE-header + optional
    # SHA-256 checks above run exactly once, immediately after download --
    # but the actual replace-and-relaunch used to happen up to 5 seconds
    # later, because a fixed `timeout /t 5` here existed just to let the
    # currently-running exe (`exe_path`) fully release its own file handle
    # before `ren` could rename it away. In the typical portable
    # no-install install this directory is an ordinary user-writable
    # folder (see the module-level allowlist NOTE above), so another local
    # process with write access to it had a multi-second window to swap
    # the already-validated `new_exe_path` for a malicious file before the
    # batch script ever moved it into place.
    #
    # Two complementary mitigations, both applied below:
    #  1. The fixed 5-second sleep is replaced with a short polling loop
    #     (`:renwait`) that retries the `ren` step itself, in 1-second
    #     increments, up to the same 5-attempt ceiling the old fixed sleep
    #     used -- faster in the common case (the old process usually
    #     releases its handle almost immediately), without widening the
    #     worst-case wait.
    #  2. `_build_reverify_command` re-checks `new_exe_path`'s PE header
    #     (and its SHA-256 again, if `expected_sha256` was supplied)
    #     immediately before the `move /y` that stages it, so a file
    #     swapped into place during the wait above is caught and rejected
    #     right there instead of being trusted blindly on the strength of
    #     the earlier Python-side check alone.
    #
    # RESIDUAL RISK: this meaningfully shrinks, but does not fully close,
    # the window -- the re-verification step and the move are still two
    # separate commands, so a swap that wins a race against the batch
    # script's own execution (rather than just the wait above) could still
    # slip through. That residual is deliberately not solved here: a local
    # attacker with write access to the exe's own directory has other,
    # easier attack avenues anyway (e.g. replacing QuickRes.exe itself
    # before any update ever runs), so chasing perfect atomicity out of a
    # batch script's limited primitives would not be a proportionate fix
    # here.
    reverify_cmd = _build_reverify_command(new_exe_path, expected_sha256)
    launch_healthcheck_cmd = _build_launch_healthcheck_command(exe_path)

    # All path-derived values below are interpolated as plain batch text
    # (double-quoted command arguments), not through PowerShell -- each is
    # escaped for cmd.exe's own %-expansion via `_escape_batch_percent` so
    # an install directory that happens to contain a literal `%` cannot be
    # silently mis-substituted. `reverify_cmd` above escapes `new_exe_path`
    # separately for its own (PowerShell + batch) nested quoting context.
    exe_path_bat = _escape_batch_percent(exe_path)
    exe_name_bat = _escape_batch_percent(exe_name)
    new_exe_path_bat = _escape_batch_percent(new_exe_path)
    old_exe_path_bat = _escape_batch_percent(old_exe_path)
    prev_old_exe_path_bat = _escape_batch_percent(prev_old_exe_path)
    log_path_bat = _escape_batch_percent(LOG_PATH)

    # The batch script runs fully detached (no console, no stdout any
    # caller could observe) -- these `echo ... >>"%QR_LOG%"` lines are its
    # only diagnostic trail. They write to the SAME quickres.log the
    # Python side already logs to via `log_msg`, so a failed update
    # (rename exhaustion, a rejected reverify, or a failed move) leaves a
    # timestamped record showing which step the update actually died on,
    # instead of vanishing the moment control leaves this process.
    bat_contents = (
        "@echo off\n"
        f'set "QR_LOG={log_path_bat}"\n'
        'echo %DATE% %TIME% update.bat: starting update >>"%QR_LOG%"\n'
        f'if exist "{old_exe_path_bat}" ren "{old_exe_path_bat}" "{exe_name_bat}.old.prev"\n'
        "set RETRIES=0\n"
        ":renwait\n"
        f'ren "{exe_path_bat}" "{exe_name_bat}.old"\n'
        "if not errorlevel 1 goto :renamed\n"
        "set /a RETRIES+=1\n"
        "if %RETRIES% GEQ 5 goto :renfail\n"
        # Round 28 finding: this was `timeout /t 1 /nobreak >nul` -- a
        # no-op under this script's actual no-console launch flags, the
        # same bug the launch-retry delays elsewhere in this script were
        # fixed for. See `_NO_CONSOLE_SAFE_DELAY_CMD`.
        f"{_NO_CONSOLE_SAFE_DELAY_CMD}"
        "goto :renwait\n"
        ":renfail\n"
        'echo %DATE% %TIME% update.bat: rename failed after retries, restoring >>"%QR_LOG%"\n'
        "goto :restore\n"
        ":renamed\n"
        'echo %DATE% %TIME% update.bat: renamed old exe, reverifying staged update >>"%QR_LOG%"\n'
        f"{reverify_cmd}"
        "if errorlevel 1 goto :reverifyfail\n"
        f'move /y "{new_exe_path_bat}" "{exe_path_bat}"\n'
        "if errorlevel 1 goto :movefail\n"
        "goto :launch\n"
        ":reverifyfail\n"
        'echo %DATE% %TIME% update.bat: reverify failed, restoring >>"%QR_LOG%"\n'
        "goto :restore\n"
        ":movefail\n"
        'echo %DATE% %TIME% update.bat: move failed, restoring >>"%QR_LOG%"\n'
        ":restore\n"
        f'if exist "{new_exe_path_bat}" del "{new_exe_path_bat}"\n'
        f'if exist "{old_exe_path_bat}" ren "{old_exe_path_bat}" "{exe_name_bat}"\n'
        f'if exist "{prev_old_exe_path_bat}" ren "{prev_old_exe_path_bat}" "{exe_name_bat}.old"\n'
        # `:restore` falls straight through into `:launch` below (it is not
        # a `goto` target from there, just the next line) so the just-
        # restored, last-known-working exe still gets a real relaunch
        # attempt after a rejected/failed update -- this is existing
        # behavior, unchanged here. That means `:launchfail` further down
        # can be reached in two genuinely different situations: (a) a fresh
        # build that was just moved into place and never got confirmed
        # running, where `exe_path` holds a possibly-broken NEW build and
        # `old_exe_path` still holds the backup to restore from; or (b) the
        # already-restored ORIGINAL build's own relaunch (right here) not
        # getting confirmed either, where `exe_path` already holds the
        # correct, last-known-working build and `old_exe_path` has already
        # been consumed by the rename above. `:launchfail`'s restore steps
        # must never run for case (b) -- there is nothing broken to recover
        # from there, and `old_exe_path` no longer holds a real backup, so
        # blindly repeating the restore steps would delete the correct
        # exe with no backup left to bring back. QR_RESTORED marks exactly
        # that: set only on this path, checked at the very top of
        # `:launchfail`.
        "set QR_RESTORED=1\n"
        # Start through PowerShell so the health check follows the exact
        # newly-launched process, rather than repeatedly scanning the whole
        # machine by image name with `tasklist`.  Defender or OneDrive can
        # delay CreateProcess itself for a portable app on the Desktop; that
        # delay is naturally included in Start-Process, after which a live
        # process is confirmed after two seconds.  A genuine launch failure
        # gets one short retry before rollback, keeping the failure path
        # bounded to roughly six seconds (the settle delay below plus two
        # health-check windows and one between-attempts wait) instead of
        # the old 36-second poll.
        ":launch\n"
        'echo %DATE% %TIME% update.bat: launching updated exe >>"%QR_LOG%"\n'
        # Round 27 finding: `move /y` (and `:restore`'s own rename) can
        # hand control back before the OS/antivirus have fully released
        # the just-written exe. Start-Process launched it immediately in
        # that window once and hit a native loader error ("Failed to load
        # Python DLL ... LoadLibrary") -- the process still counted as
        # "alive" for the 2-second health check (a blocking MessageBox
        # keeps it running) even though it never actually started, so
        # :confirmed fired on a broken build. This settle delay sits
        # BEFORE the first Start-Process attempt (not just between retries,
        # which :launchtry's own delay below already covers), narrowing
        # -- not eliminating -- that race, the same proportionate way the
        # renwait polling loop and pre-move reverify step already narrow
        # theirs elsewhere in this script. See `_NO_CONSOLE_SAFE_DELAY_CMD`
        # for why this can't be a plain `timeout` command.
        f"{_NO_CONSOLE_SAFE_DELAY_CMD}"
        "set LAUNCHRETRIES=0\n"
        ":launchtry\n"
        f"{launch_healthcheck_cmd}"
        "if not errorlevel 1 goto :confirmed\n"
        "set /a LAUNCHRETRIES+=1\n"
        "if %LAUNCHRETRIES% GEQ 2 goto :launchfail\n"
        f"{_NO_CONSOLE_SAFE_DELAY_CMD}"
        "goto :launchtry\n"
        "goto :launchfail\n"
        ":confirmed\n"
        'echo %DATE% %TIME% update.bat: launch confirmed, removing backup >>"%QR_LOG%"\n'
        f'if exist "{old_exe_path_bat}" del "{old_exe_path_bat}"\n'
        f'if exist "{prev_old_exe_path_bat}" del "{prev_old_exe_path_bat}"\n'
        "goto :cleanup\n"
        # :launchfail mirrors :restore's own rename sequence for case (a)
        # above -- the staged build reached :launch by passing the
        # structural PE-header check (and the optional SHA-256 check) and
        # being moved into place, but neither of those checks actually
        # EXECUTES the file, so a build that is a structurally valid,
        # correctly-hashed PE can still not be a build that runs on this
        # machine (a missing dependency DLL, a wrong CPU architecture). By
        # this point `new_exe_path` has already been consumed by the move
        # above (it no longer exists), and the broken build is sitting at
        # the canonical `exe_path` instead -- the mirror image of
        # :restore's situation, where the move never happened and the
        # leftover file was still at `new_exe_path`. So this branch (guarded
        # by the QR_RESTORED check above) deletes the broken build at
        # `exe_path` (rather than at `new_exe_path`) and then renames the
        # backups back exactly as :restore does, before falling through to
        # the same self-deleting :cleanup both paths share.
        ":launchfail\n"
        "if defined QR_RESTORED goto :cleanup\n"
        'echo %DATE% %TIME% update.bat: launch not confirmed, restoring >>"%QR_LOG%"\n'
        f'if exist "{exe_path_bat}" del "{exe_path_bat}"\n'
        f'if exist "{old_exe_path_bat}" ren "{old_exe_path_bat}" "{exe_name_bat}"\n'
        f'if exist "{prev_old_exe_path_bat}" ren "{prev_old_exe_path_bat}" "{exe_name_bat}.old"\n'
        ":cleanup\n"
        'echo %DATE% %TIME% update.bat: cleanup, self-deleting >>"%QR_LOG%"\n'
        'del "%~f0"\n'
    )

    # Defense-in-depth: statically verify the batch script's own goto/label
    # graph is internally consistent BEFORE it is ever written to disk or
    # launched, so a typo'd label introduced by a future edit to the
    # `bat_contents` literal above surfaces immediately (as an
    # AssertionError raised here, reported to the UI the same way any other
    # `apply_update` failure is) instead of only at runtime during an actual
    # failed update.
    _validate_batch_label_graph(bat_contents)

    # Same reparse-point concern as the download write above, arguably more
    # severe here: this is the file that gets EXECUTED (via subprocess.Popen
    # right below), not merely moved into place after checks. A pre-planted
    # symlink at bat_path must be refused before update.bat is ever written
    # through it, let alone run.
    bat_file = _open_no_reparse_follow(bat_path)
    if bat_file is None:
        raise OSError(
            f"{bat_path} is a reparse point/symlink; refusing to write "
            f"the update script through it"
        )
    with bat_file:
        bat_file.write(bat_contents)

    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )

    sys.exit(0)


def _force_exit_on_expected_system_exit(run) -> None:
    """Shared by `confirm_update` and `install_downloaded_update`: calls
    `run()` and, if it raises the `SystemExit` that a successful
    `apply_update` always ends in, force-exits via `os._exit(0)` instead of
    letting it propagate as a normal `SystemExit`.

    `os._exit(0)` skips Python's normal interpreter shutdown/atexit
    handlers -- safe and desired here: the exe on disk has just been
    renamed out from under the running process, so a normal clean shutdown
    path could still try to touch it. It also guarantees the WHOLE
    process exits when `run()` is invoked from a pywebview JS-bridge call:
    pywebview dispatches `js_api` calls off the main thread, and a plain
    `SystemExit` raised there only terminates that ONE worker thread, not
    the process -- exactly the bug `install_downloaded_update` closes (see
    its own docstring). `webview/bridge.py`'s `bridge_op` decorator ALSO
    force-exits on `SystemExit` as a structural backstop for every wrapped
    `Api` method, including these two -- so for the actual production call
    paths (both go through `bridge_op`), this wrapper's own `os._exit(0)`
    is redundant with that backstop, not the only thing standing between a
    stray `SystemExit` and a hung window. Deliberate belt-and-suspenders,
    matching this module's existing TOCTOU-reverify philosophy elsewhere:
    kept here anyway so `confirm_update`/`install_downloaded_update` are
    each independently correct even if ever called from somewhere that
    ISN'T `bridge_op`-wrapped.

    Any OTHER exception (network failure, disk-full, etc.) is NOT
    force-exited -- it propagates normally so `bridge_op` (or, for the
    one-shot `confirm_update` path, its own caller) reports it to the UI
    while the app stays alive.
    """
    try:
        run()
    except SystemExit:
        log_msg("Update staged successfully; force-exiting to complete install.")
        os._exit(0)


def confirm_update(download_url: str, version_info=None) -> None:
    """Distinguishes an expected clean-exit case from a genuine failure
    that must be reported to the UI, owned here rather than in
    `webview/bridge.py` so `Api.confirm_update` can stay a zero-try/except
    one-line delegation (bridge.py enforces exactly one `try:` in the whole
    file, inside `bridge_op`). See `_force_exit_on_expected_system_exit`
    for why a plain `SystemExit` isn't enough here.

    `version_info` is an optional pass-through of fetch_version_info()'s
    JSON response so `apply_update` can pull an expected sha256 out of it,
    if the server ever starts supplying one -- see the NOTE above
    `_HASH_FIELD_NAME` in this module. Defaults to `None` so the current
    `webview/bridge.py` call site (`updater.confirm_update(download_url)`)
    keeps working unchanged.
    """
    _force_exit_on_expected_system_exit(
        lambda: apply_update(download_url, version_info=version_info)
    )


def install_downloaded_update(version_info=None) -> None:
    """Force-exit counterpart of `confirm_update` for the `reuse_download`
    (already-downloaded, already-verified) install path used by
    `UpdateJob`/`webview/bridge.py`'s non-blocking download-then-install
    flow. See `_force_exit_on_expected_system_exit` for why this can't
    just let `apply_update`'s terminal `sys.exit(0)` propagate on its
    own: called from a pywebview JS-bridge thread, a plain `SystemExit`
    there only kills that ONE worker thread, leaving the original window
    running indefinitely (its own poll loop stuck on a JS promise that
    never resolves) while `update.bat`'s own `Start-Process` launch of the
    replacement exe races that still-alive original process's
    single-instance mutex.
    """
    _force_exit_on_expected_system_exit(
        lambda: apply_update(None, version_info=version_info, reuse_download=True)
    )
