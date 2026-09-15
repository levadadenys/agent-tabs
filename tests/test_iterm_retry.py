"""A transient AppleScript failure must not cost a whole capture.

Closing a tab mid-enumeration raises "Invalid index (-1719)" and was observed
losing captures in real use.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import iterm  # noqa: E402
from runner import run  # noqa: E402

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


if __name__ == "__main__":
    raise SystemExit(run(globals()))
