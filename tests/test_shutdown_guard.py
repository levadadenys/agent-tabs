"""A shutdown must not overwrite a good snapshot with a half-dead one.

During shutdown tabs die one at a time, so a capture can legitimately hold a
few sessions while the last good one held many. Promoting that would leave the
operator restoring 3 tabs instead of 22 -- at exactly the moment the snapshot
matters most.
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run  # noqa: E402


def fresh(state: Path):
    os.environ["AGENT_TABS_HOME"] = str(state)
    import agent_tabs.snapshot as m
    return importlib.reload(m)


def snap(agents: int, label: str) -> dict:
    tabs = [
        {"tab_index": i + 1, "pane_index": 1, "title": f"t{i}", "kind": "claude",
         "session_id": f"id{i}", "cwd": "/repo", "restore": f"claude --resume id{i}"}
        for i in range(agents)
    ]
    return {"version": 1, "captured_at": label,
            "windows": [{"window_index": 1, "window_id": "1", "tabs": tabs}]}


def test_a_shutdown_collapse_does_not_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        latest, _ = m.save(snap(3, "SHUTTING-DOWN"))
        assert latest is None, "a collapsed capture was promoted"
        assert m.load()["captured_at"] == "FULL"


def test_a_collapse_that_persists_is_eventually_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        for i in range(m.COLLAPSE_STREAK - 1):
            assert m.save(snap(3, f"drop{i}"))[0] is None
        latest, _ = m.save(snap(3, "SETTLED"))
        assert latest is not None, "a real drop must be accepted eventually"
        assert m.load()["captured_at"] == "SETTLED"


def test_an_ordinary_drop_is_promoted_immediately():
    # Closing a couple of tabs must not delay the snapshot.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        latest, _ = m.save(snap(20, "CLOSED-TWO"))
        assert latest is not None
        assert m.load()["captured_at"] == "CLOSED-TWO"


def test_growth_is_always_promoted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(5, "FIVE"))
        assert m.save(snap(9, "NINE"))[0] is not None
        assert m.load()["captured_at"] == "NINE"


def test_zero_is_still_never_promoted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        assert m.save(snap(0, "GONE"))[0] is None
        assert m.load()["captured_at"] == "FULL"


def test_a_recovery_resets_the_streak():
    """A blip must not count toward a later collapse being believed.

    Sized so a non-resetting streak would cross the threshold: one blip, a
    recovery, then COLLAPSE_STREAK - 1 more blips. With the reset that is one
    short of acceptance; without it, it is exactly at it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        m.save(snap(3, "blip"))                       # streak 1
        assert m.save(snap(22, "BACK"))[0] is not None, "recovery not promoted"

        latest = "unset"
        for i in range(m.COLLAPSE_STREAK - 1):
            latest, _ = m.save(snap(3, f"later{i}"))
        assert latest is None, (
            "the streak did not reset after a recovery, so an old blip helped "
            "a later collapse get promoted"
        )
        assert m.load()["captured_at"] == "BACK"


def test_a_gap_restarts_the_streak():
    """Collapses either side of a zero capture are not consecutive.

    This is the post-reboot path: shutdown collapses build the streak, iTerm
    closes, and the first partial capture while sessions are being restored
    would otherwise be promoted over the full snapshot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        for i in range(m.COLLAPSE_STREAK - 1):
            m.save(snap(3, f"shutdown{i}"))
        m.save(snap(0, "iterm-closed"))
        latest, _ = m.save(snap(3, "restoring"))
        assert latest is None, (
            "a partial capture after a gap was promoted over the full snapshot"
        )
        assert m.load()["captured_at"] == "FULL"


def test_the_first_ever_capture_is_promoted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        latest, _ = m.save(snap(2, "FIRST"))
        assert latest is not None


if __name__ == "__main__":
    raise SystemExit(run(globals()))
