"""A transient AppleScript failure must not cost a whole capture.

Closing a tab mid-enumeration raises "Invalid index (-1719)" and was observed
losing captures in real use.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import iterm

REAL_OSASCRIPT = iterm._osascript  # other tests stub it

GOOD = "1\x1f80\x1f1\x1f1\x1f/dev/ttys001\x1fa title\n"


def with_script(results):
    """Stub _osascript to yield each result in turn: str returns, Exception raises."""
    calls = {"n": 0}

    def fake(_script):
        i = calls["n"]
        calls["n"] += 1
        outcome = results[min(i, len(results) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    iterm._osascript = fake  # type: ignore[assignment]
    iterm.ENUMERATE_BACKOFF_SECONDS = 0  # type: ignore[assignment]
    return calls


def test_transient_failure_is_retried():
    calls = with_script([iterm.ITermError("Invalid index. (-1719)"), GOOD])
    rows = iterm.enumerate_tabs()
    assert len(rows) == 1 and rows[0]["tty"] == "/dev/ttys001"
    assert calls["n"] == 2, "should have retried exactly once"


def test_succeeds_without_retry_when_healthy():
    calls = with_script([GOOD])
    assert len(iterm.enumerate_tabs()) == 1
    assert calls["n"] == 1, "a healthy call must not retry"


def test_persistent_failure_still_raises():
    err = iterm.ITermError("Invalid index. (-1719)")
    calls = with_script([err, err, err, err])
    try:
        iterm.enumerate_tabs()
    except iterm.ITermError as exc:
        assert "1719" in str(exc)
    else:
        raise AssertionError("a persistent failure must surface, not be swallowed")
    assert calls["n"] == iterm.ENUMERATE_ATTEMPTS, f"tried {calls['n']} times"


def test_a_slow_settle_is_outlasted():
    # During a reboot restore iTerm kept failing the listing for longer than
    # three quick attempts, and the restore stopped part-way.
    failures = [iterm.ITermError("Invalid index. (-1719)")] * 5
    calls = with_script([*failures, GOOD])
    assert len(iterm.enumerate_tabs()) == 1, "gave up before iTerm settled"
    assert calls["n"] == 6


def test_the_wait_grows_between_attempts():
    # A fixed short wait gave up while iTerm was still settling.
    slept = []
    original = iterm.time.sleep
    iterm.time.sleep = slept.append  # type: ignore[assignment]
    try:
        with_script([iterm.ITermError("Invalid index. (-1719)")] * 5 + [GOOD])
        iterm.ENUMERATE_BACKOFF_SECONDS = 0.5  # type: ignore[assignment]
        iterm.enumerate_tabs()
    finally:
        iterm.time.sleep = original  # type: ignore[assignment]
    assert slept == [0.5, 1.0, 1.5, 2.0, 2.5], slept


def test_a_hung_osascript_becomes_an_error_not_a_wait():
    # osascript's own AppleEvent timeout is about 120 s per call; every retry
    # would wait that long behind a wedged iTerm.
    import subprocess

    calls = []

    def hang(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    original = iterm.subprocess.run
    iterm.subprocess.run = hang  # type: ignore[assignment]
    try:
        REAL_OSASCRIPT('return "x"')
    except iterm.ITermError as exc:
        assert "did not answer" in str(exc), exc
    else:
        raise AssertionError("a hung osascript did not raise ITermError")
    finally:
        iterm.subprocess.run = original  # type: ignore[assignment]
    assert calls == [iterm.OSASCRIPT_TIMEOUT_SECONDS], calls


if __name__ == "__main__":
    raise SystemExit(run(globals()))
