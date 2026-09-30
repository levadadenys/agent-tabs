"""A new window must be identified by the id captured when it is created.

iTerm's window index is front-to-back position. Creating a window, returning
its index, and resolving that back to an id afterwards left a gap in which
focusing any other window made the restore target the wrong one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import iterm  # noqa: E402
from runner import run  # noqa: E402


def with_osascript(reply):
    calls = []

    def fake(script):
        calls.append(script)
        return reply

    original = iterm._osascript
    iterm._osascript = fake  # type: ignore[assignment]
    return calls, original


def test_create_window_returns_the_id_from_the_creating_script():
    calls, original = with_osascript("4242\n")
    try:
        assert iterm.create_window(3) == "4242"
    finally:
        iterm._osascript = original  # type: ignore[assignment]
    assert len(calls) == 1, "the id must come from the same script that creates it"
    assert "id of win" in calls[0]


def test_create_window_does_not_walk_window_order():
    # Walking `windows` to find a position is exactly the index-based lookup
    # that focus changes break.
    calls, original = with_osascript("7\n")
    try:
        iterm.create_window(2)
    finally:
        iterm._osascript = original  # type: ignore[assignment]
    assert "repeat with w in windows" not in calls[0]


def test_create_window_refuses_an_empty_id():
    calls, original = with_osascript("\n")
    try:
        try:
            iterm.create_window(1)
        except iterm.ITermError:
            pass
        else:
            raise AssertionError("an empty id must not be returned as a target")
    finally:
        iterm._osascript = original  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(run(globals()))
