"""Round 32 corrective fix for quickres/updater.py (webview-security-reliability-fixes):

update.bat's launch-confirmation window used exactly two back-to-back fixed
`timeout /t 2` waits (~4-6s total, including the pre-start wait) before
giving up and running `:launchfail`'s destructive rollback (deleting the
freshly-staged new exe and restoring the old backup). On a real Windows
machine with on-access AV/Defender scanning enabled (the default), a
brand-new, previously-unseen, UNSIGNED executable (see the standing
deferred-signing gap documented above `_looks_like_pe_executable` in
updater.py) can easily take longer than ~4-6s to actually start under
first-run scanning -- so a perfectly good update could get silently rolled
back purely because the launch-confirmation heuristic's timing budget was
too tight and non-adaptive (a fixed constant, no backoff).

This test asserts the generated script's launch-confirmation step now (a)
retries starting the exe at least 3 times before giving up (previously 2),
and (b) actually polls in a real backward-jumping loop (a label inside the
:launch..:launchfail region that a `goto` jumps back to more than once) --
rather than a fixed, unrolled, always-the-same-total-wait sequence -- so the
total grace period before `:launchfail`'s rollback adapts/grows across
retries instead of being a single hard-coded ~4-6s ceiling.
"""
import re

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

    bat_path = exe_dir / "update.bat"
    return bat_path.read_text()


def _launch_section(script):
    lines = script.splitlines()
    lower = [l.strip().lower() for l in lines]
    launch_idx = lower.index(":launch")
    launchfail_idx = lower.index(":launchfail")
    assert launchfail_idx > launch_idx
    return lines[launch_idx : launchfail_idx + 1]


class TestLaunchConfirmationHasMoreThanTwoAttempts:
    def test_start_is_reachable_at_least_three_times_before_launchfail(
        self, monkeypatch, tmp_path
    ):
        """The old script unrolled exactly 2 `start ""` attempts in a
        straight line. The fix must allow at least 3 attempts. A loop-based
        implementation only writes the `start ""` line once in the text but
        makes it reachable multiple times via a bounded retry `goto` -- so
        this asserts on the retry bound (a numeric comparison against an
        attempt counter), not on literal text repetition.
        """
        script = _generated_script(monkeypatch, tmp_path)
        section = _launch_section(script)
        section_text = "\n".join(l.strip() for l in section)
        section_lower = section_text.lower()

        assert 'start "" "' in section_lower, section_lower

        # Either the `start ""` line is textually repeated >= 3 times
        # (an unrolled implementation), or there is a numeric retry bound
        # (>= 3) gating a backward `goto` to a label whose block contains
        # the `start ""` line (a loop-based implementation).
        literal_starts = section_lower.count('start "" "')
        if literal_starts >= 3:
            return

        bound_matches = re.findall(
            r"if\s+%\w+%\s+(?:lss|leq|gtr|geq|neq|equ)\s+(\d+)\s+goto\s+:?(\w+)",
            section_lower,
        )
        assert bound_matches, (
            "expected either >=3 literal `start \"\" \"` lines or a numeric "
            f"retry-bound goto driving a launch-retry loop\n{section_text}"
        )

        retry_capable = False
        for bound, target_label in bound_matches:
            if int(bound) < 3:
                continue
            # The retry target's own block (from its label to the next
            # label) must actually contain a `start ""` line for this bound
            # to represent real launch retries rather than an unrelated loop.
            label_idx = next(
                (
                    i
                    for i, l in enumerate(section)
                    if l.strip().lower() == f":{target_label}"
                ),
                None,
            )
            if label_idx is None:
                continue
            rest = section[label_idx:]
            next_label_offset = next(
                (
                    i
                    for i, l in enumerate(rest[1:], start=1)
                    if l.strip().startswith(":")
                ),
                len(rest),
            )
            block = "\n".join(rest[:next_label_offset]).lower()
            if 'start "" "' in block:
                retry_capable = True

        assert retry_capable, (
            "expected a retry bound (>=3) whose target label's block "
            f"actually re-invokes `start \"\" \"`\n{section_text}"
        )


class TestLaunchConfirmationActuallyPolls:
    def test_launch_section_contains_a_real_backward_polling_loop(
        self, monkeypatch, tmp_path
    ):
        """A fixed, unrolled sequence of `timeout /t N` + `tasklist` checks
        gives every user the exact same total grace period regardless of how
        long the new exe is actually taking to start. A real loop (a label
        that a `goto` jumps back to more than once) lets the script keep
        polling instead of just sleeping a hard-coded total and giving up.
        """
        script = _generated_script(monkeypatch, tmp_path)
        section = _launch_section(script)
        section_lower = [l.strip().lower() for l in section]

        label_def_idx = {
            l[1:]: i for i, l in enumerate(section_lower) if l.startswith(":") and " " not in l
        }

        backward_looped_labels = []
        for label, def_idx in label_def_idx.items():
            for i, line in enumerate(section_lower):
                if i <= def_idx:
                    continue
                if re.search(rf"goto\s+:?{re.escape(label)}\b", line):
                    backward_looped_labels.append(label)
                    break

        assert backward_looped_labels, (
            "expected at least one label inside the :launch section whose "
            "definition a LATER line's `goto` jumps back to (a real "
            f"backward polling loop), found none. Labels: {label_def_idx}\n"
            + "\n".join(section_lower)
        )


class TestLaunchConfirmationBudgetGrowsAcrossRetries:
    def test_budget_is_not_a_single_fixed_total_wait(self, monkeypatch, tmp_path):
        """The old behavior granted exactly one fixed total wait (two
        back-to-back `timeout /t 2` waits) no matter what. The fix must make
        the grace period depend on how many retries have happened so far
        (an adaptive/backoff budget), not just a bigger version of the same
        fixed constant. Evidence of that: an arithmetic `set /a` computation
        inside the :launch section that scales with a retry/attempt counter.
        """
        script = _generated_script(monkeypatch, tmp_path)
        section = _launch_section(script)
        section_lower = "\n".join(l.strip().lower() for l in section)

        assert "set /a" in section_lower, (
            "expected an arithmetic (set /a) computation driving an "
            f"adaptive poll/retry budget inside the :launch section\n{section_lower}"
        )
