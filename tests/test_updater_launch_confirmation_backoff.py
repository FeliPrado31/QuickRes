"""Launch confirmation uses the exact process, not a slow global name scan.

The old batch script repeatedly searched ``tasklist`` for an image name. A
failed launch could therefore spend 36 seconds polling, and a portable copy
on the Desktop could be delayed or misdetected by OneDrive/Defender. The
generated script now starts the replacement through ``Start-Process
-PassThru`` and checks that exact process is still alive after a short health
window. It preserves rollback, with one bounded retry for transient launch
failures.
"""
import pytest

from quickres import updater


def _valid_pe_payload():
    header = bytearray(64)
    header[0:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    return bytes(header) + b"PE\x00\x00" + b"restofheader"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _generated_script(monkeypatch, exe_dir, exe_name="QuickRes.exe"):
    exe_dir.mkdir(parents=True, exist_ok=True)
    fake_exe = exe_dir / exe_name
    fake_exe.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

    payload = _valid_pe_payload()

    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResp(payload)

    monkeypatch.setattr(
        updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
    )
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    with pytest.raises(SystemExit):
        updater.apply_update("https://lxzy.my/QuickRes_new.exe")

    return (exe_dir / "update.bat").read_text()


def _launch_section(script):
    lines = script.splitlines()
    lower = [line.strip().lower() for line in lines]
    launch_idx = lower.index(":launch")
    launchfail_idx = lower.index(":launchfail")
    assert launchfail_idx > launch_idx
    return lines[launch_idx : launchfail_idx + 1]


class TestLaunchHealthCheck:
    def test_uses_exact_started_process_instead_of_tasklist(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        section = "\n".join(line.strip() for line in _launch_section(script)).lower()

        assert "start-process -filepath" in section
        assert "-workingdirectory" in section
        assert "-passthru" in section
        assert "start-sleep -seconds 2" in section
        assert "tasklist" not in section
        assert "launchpoll" not in section

    def test_has_one_short_retry_before_rollback(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        section = "\n".join(line.strip() for line in _launch_section(script)).lower()

        assert "set launchretries=0" in section
        assert "set /a launchretries+=1" in section
        assert "if %launchretries% geq 2 goto :launchfail" in section
        assert "goto :launchtry" in section
        # Round 28 finding: `timeout /t 1 /nobreak >nul` is a no-op under
        # this script's actual no-console launch flags (see
        # test_no_console_safe_delay_actually_waits below) -- the
        # between-retries wait must use the PowerShell-based delay instead.
        assert "timeout /t 1 /nobreak >nul" not in section

    def test_between_retries_wait_uses_the_working_delay_command(
        self, monkeypatch, tmp_path
    ):
        """Round 28 finding: a substring check anywhere in the whole launch
        section cannot tell this delay apart from the UNRELATED
        pre-first-attempt settle delay elsewhere in the same section --
        empirically confirmed by reconstructing the section with only this
        occurrence removed and observing the old assertion still passed.
        This test scopes strictly to the text between the retry-count
        check and `goto :launchtry`, the only place this specific delay
        can legitimately appear.
        """
        script = _generated_script(monkeypatch, tmp_path)
        section = _launch_section(script)
        stripped = [line.strip().lower() for line in section]
        geq_idx = stripped.index("if %launchretries% geq 2 goto :launchfail")
        goto_idx = stripped.index("goto :launchtry")
        assert goto_idx > geq_idx

        between_retries = "\n".join(stripped[geq_idx + 1 : goto_idx])

        assert 'start-sleep -seconds 1"' in between_retries
        assert "timeout /t 1" not in between_retries

    def test_settles_before_first_launch_attempt_to_avoid_a_racy_false_confirm(
        self, monkeypatch, tmp_path
    ):
        """Round 27 finding: `move /y` (or the `:restore` rename) can hand
        control back before the OS/antivirus have fully released the
        just-written exe -- `Start-Process` can then launch a process that
        immediately shows a native loader error (e.g. "Failed to load
        Python DLL ... LoadLibrary") while technically staying "alive" (a
        blocking MessageBox), fooling the 2-second health check into
        reporting `:confirmed` on a build that never actually started. A
        short settle delay right at `:launch`, before the FIRST
        `Start-Process` attempt, narrows this window the same way the
        existing renwait/reverify steps narrow their own races -- must sit
        strictly between `:launch` and `set LAUNCHRETRIES=0`, not just the
        retry loop's own existing between-attempts timeout.
        """
        script = _generated_script(monkeypatch, tmp_path)
        section = _launch_section(script)
        stripped = [line.strip().lower() for line in section]
        launch_idx = stripped.index(":launch")
        retries_idx = stripped.index("set launchretries=0")

        before_first_attempt = stripped[launch_idx + 1 : retries_idx]

        # Round 28 finding: `timeout` is a no-op under this script's actual
        # no-console launch flags -- see test_no_console_safe_delay_actually_waits.
        assert 'start-sleep -seconds 1"' in "\n".join(before_first_attempt)

    def test_no_console_safe_delay_actually_waits(self):
        """Round 28 finding: the string-only assertions above cannot catch a
        delay command that generates valid-looking text but does not
        actually wait -- `timeout /t 1 /nobreak >nul` looked correct yet
        exited in ~50ms (measured) under `CREATE_NO_WINDOW |
        DETACHED_PROCESS` (the exact flags apply_update() launches
        update.bat with), because `timeout.exe` requires an attached
        console it does not have there. This test actually EXECUTES the
        same PowerShell-based mechanism `_NO_CONSOLE_SAFE_DELAY_CMD` uses
        under those same flags and measures wall-clock time, so a future
        regression back to a console-dependent delay command fails loudly
        instead of silently. Uses a short synthetic duration (not the
        production command's actual 1-second value) -- the mechanism being
        proven is "does this command family wait at all under these
        flags", which a 250ms wait demonstrates just as well as 1s, for a
        fraction of the real time cost on every test run.
        """
        import subprocess
        import time

        assert "Start-Sleep" in updater._NO_CONSOLE_SAFE_DELAY_CMD
        command = 'powershell -NoProfile -NonInteractive -Command "Start-Sleep -Milliseconds 250"'
        start = time.monotonic()
        subprocess.run(
            ["cmd", "/c", command],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            check=True,
        )
        elapsed = time.monotonic() - start

        assert elapsed >= 0.2, (
            f"delay command returned after {elapsed:.2f}s -- expected it to "
            f"actually wait ~0.25s under no-console flags, not exit immediately"
        )

    def test_escapes_executable_and_working_directory_for_powershell(self):
        malicious = r"C:\Users\evil' ; Remove-Item C:\ -Recurse -Force #\QuickRes.exe"
        command = updater._build_launch_healthcheck_command(malicious)

        assert "evil'' ;" in command
        assert "evil' ;" not in command
