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

from runner import run


def fresh(state: Path, settle: float = 0):
    """Reload snapshot against a temp state dir.

    `settle` defaults to 0 so tests exercise the capture-count rule directly;
    test_a_burst_of_captures_is_not_time_enough covers the real window.
    """
    os.environ["AGENT_TABS_HOME"] = str(state)
    import agent_tabs.snapshot as m

    m = importlib.reload(m)
    m.COLLAPSE_SETTLE_SECONDS = settle
    return m


def snap(agents: int, label: str) -> dict:
    tabs = [
        {
            "tab_index": i + 1,
            "pane_index": 1,
            "title": f"t{i}",
            "kind": "claude",
            "session_id": f"id{i}",
            "cwd": "/repo",
            "restore": f"claude --resume id{i}",
        }
        for i in range(agents)
    ]
    return {
        "version": 1,
        "captured_at": label,
        "windows": [{"window_index": 1, "window_id": "1", "tabs": tabs}],
    }


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
        m.save(snap(3, "blip"))  # streak 1
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
        assert latest is None, "a partial capture after a gap was promoted over the full snapshot"
        assert m.load()["captured_at"] == "FULL"


def test_the_first_ever_capture_is_promoted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        latest, _ = m.save(snap(2, "FIRST"))
        assert latest is not None


def test_a_corrupt_snapshot_does_not_disable_the_guard():
    """An unreadable snapshot.json must not read as "no history".

    Treating it as zero switched the collapse guard off entirely, so a
    collapsed capture promoted on its first observation -- reintroducing the
    bug this guard exists to prevent, exactly when state is already suspect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        m.LATEST.write_text("{not valid json!!")
        latest, _ = m.save(snap(3, "collapse"))
        assert latest is None, "a corrupt snapshot.json switched the guard off"


def test_a_corrupt_history_entry_is_conservative():
    # Unreadable history must delay promotion, never license it.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        m.save(snap(3, "c0"))
        newest = sorted(m.HISTORY_DIR.glob("*.json"), reverse=True)[0]
        newest.write_text("garbage")
        latest, _ = m.save(snap(3, "after-corrupt"))
        assert latest is None, "unreadable history licensed a promotion"
        assert m.load()["captured_at"] == "FULL"


def test_a_burst_of_captures_is_not_time_enough():
    """Several captures landing at once must not satisfy the guard.

    A manual `agent-tabs snapshot` racing the launchd tick can produce
    back-to-back captures; counting them alone would clear the streak
    instantly, which is not the same as the collapse persisting.
    """
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        m.save(snap(22, "FULL"))
        latest = "unset"
        for i in range(m.COLLAPSE_STREAK + 2):
            latest, _ = m.save(snap(3, f"burst{i}"))
        assert latest is None, "a rapid burst satisfied the settle window"
        assert m.load()["captured_at"] == "FULL"


def test_no_extra_mutable_state_is_kept():
    # The streak is derived from append-only history; a separate counter was a
    # second corruptible, unlocked file and is deliberately gone.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, "FULL"))
        m.save(snap(3, "collapse"))
        strays = [
            f.name for f in m.STATE_DIR.iterdir() if f.name not in {"snapshot.json", "history"}
        ]
        assert strays == [], f"unexpected mutable state: {strays}"


def test_a_collapse_spanning_the_settle_window_is_accepted():
    # Backdated archives stand in for captures 30s apart.
    from datetime import datetime, timedelta
    from datetime import timezone as tz

    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        m.save(snap(22, "FULL"))
        now = datetime.now(tz.utc)
        # Backdate the full capture's own archive: left at "now" it would sit
        # newer than the collapses below and correctly break the run.
        for old_archive in m.HISTORY_DIR.glob("*.json"):
            old_archive.rename(
                m.HISTORY_DIR
                / ((now - timedelta(seconds=300)).strftime("%Y%m%dT%H%M%S%f") + ".json")
            )
        for i in range(m.COLLAPSE_STREAK):
            stamp = (now - timedelta(seconds=30 * (m.COLLAPSE_STREAK - i))).strftime(
                "%Y%m%dT%H%M%S%f"
            )
            (m.HISTORY_DIR / f"{stamp}.json").write_text(
                __import__("json").dumps(snap(3, f"old{i}"))
            )
        latest, _ = m.save(snap(3, "settled"))
        assert latest is not None, "a collapse that genuinely persisted was blocked"


def history(m, full_ago: float, collapsed_ago: list) -> None:
    """A full capture and collapsed ones, as save() would have named them that
    many seconds ago. Archive times come from names, so real-clock names would
    put every capture at "now" and a span of about 0 s."""
    import json
    from datetime import datetime, timedelta
    from datetime import timezone as tz

    m.save(snap(22, "FULL"))
    now = datetime.now(tz.utc)
    for archive in m.HISTORY_DIR.glob("*.json"):
        archive.unlink()
    for ago, agents in [(full_ago, 22)] + [(a, 3) for a in collapsed_ago]:
        stamp = (now - timedelta(seconds=ago)).strftime("%Y%m%dT%H%M%S%f")
        (m.HISTORY_DIR / f"{stamp}.json").write_text(json.dumps(snap(agents, f"at-{ago}")))


def settles(collapsed_ago: list, full_ago: float = 3600) -> bool:
    """With the real settle time, does one more collapsed capture now promote?"""
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        history(m, full_ago, collapsed_ago)
        latest, _ = m.save(snap(3, "now"))
        return latest is not None


def test_at_10_seconds_a_genuine_drop_is_accepted():
    # Three captures 10 s apart span 20 s, under the 45 s settle: a guard that
    # read a fixed three archives never accepted at this interval.
    assert not settles([30, 20, 10]), "accepted after 30 s, under the settle time"
    assert settles([50, 40, 30, 20, 10]), "a drop persisting 50 s at 10 s was never accepted"


def test_at_300_seconds_three_captures_are_needed():
    assert not settles([300]), "two captures accepted: a gap supplied the time"
    assert settles([600, 300]), "three captures over 10 minutes were not enough"


def test_a_collapse_either_side_of_a_reboot_does_not_promote():
    # A half-dead capture from the shutdown, then the first small one after
    # boot: two collapsed captures hours apart. Time alone would accept them;
    # the pre-boot pin cannot be relied on to have saved the fleet first.
    assert not settles([3 * 3600], full_ago=4 * 3600), "a reboot gap promoted a collapse"
    assert settles([3 * 3600, 30], full_ago=4 * 3600), "a third capture should complete it"


def test_a_healthy_capture_inside_the_run_restarts_it():
    # The run is consecutive: a recovery between collapses breaks it.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        history(m, 3600, [120, 90])
        import json
        from datetime import datetime, timedelta
        from datetime import timezone as tz

        stamp = (datetime.now(tz.utc) - timedelta(seconds=60)).strftime("%Y%m%dT%H%M%S%f")
        (m.HISTORY_DIR / f"{stamp}.json").write_text(json.dumps(snap(22, "recovered")))
        latest, _ = m.save(snap(3, "now"))
        assert latest is None, "a run broken by a recovery was still accepted"
    assert settles([120, 90, 60]), "setup: the same run without the recovery promotes"


def test_a_stray_unparseable_archive_does_not_block_a_genuine_drop():
    # Walked by the time in each name, a name with no time sorts oldest and
    # is never reached; by filename it would sort newest and stop every run.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        history(m, 3600, [90, 60, 30])
        (m.HISTORY_DIR / "stray.json").write_text("{}")
        latest, _ = m.save(snap(3, "now"))
        assert latest is not None, "a stray file blocked a genuine drop"


def test_an_unreadable_capture_inside_the_run_stops_it():
    # Skipping it would let the captures either side add up to a promotion;
    # an entry that cannot be read must delay one, never license it.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        history(m, 3600, [90, 60, 30])
        middle = sorted(m.HISTORY_DIR.glob("*.json"))[2]  # the capture 60 s ago
        middle.write_text("garbage")
        latest, _ = m.save(snap(3, "now"))
        assert latest is None, "an unreadable capture was skipped over"
    assert settles([90, 60, 30]), "setup: the same run, readable, promotes"


def test_concurrent_saves_do_not_corrupt_the_decision():
    # Two captures can overlap (a manual snapshot racing the 30s tick). With no
    # read-modify-write state there is nothing to lose an update on.
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), settle=45)
        m.save(snap(22, "FULL"))
        results = []

        def worker(n):
            results.append(m.save(snap(3, f"race{n}"))[0])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert m.load()["captured_at"] == "FULL", (
            "a racing pair of collapsed captures promoted over the full snapshot"
        )


if __name__ == "__main__":
    raise SystemExit(run(globals()))
