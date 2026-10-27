"""snapshot.save() must never let a degraded capture destroy a good one.

This is the tool's core safety property: the recorder fires every 30s by default, and a
capture taken while iTerm is shutting down sees no agents. If that were
promoted, the last good snapshot would be gone at exactly the moment it is
needed. Driven through the real save/load/history call sites -- the bug this
guards against is filesystem state, so there is nothing worth mocking.
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run


def fresh_module(state_dir: Path):
    """Re-import snapshot with AGENT_TABS_HOME pointed at a temp directory."""
    os.environ["AGENT_TABS_HOME"] = str(state_dir)
    import agent_tabs.snapshot as snap_mod

    return importlib.reload(snap_mod)


def snap_with(agent_tabs: int, captured_at="2026-01-01T00:00:00+00:00") -> dict:
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
        for i in range(agent_tabs)
    ]
    tabs.append(
        {
            "tab_index": 99,
            "pane_index": 1,
            "title": "shell",
            "kind": "shell",
            "session_id": None,
            "cwd": "/tmp",
            "restore": "cd /tmp",
        }
    )
    return {
        "version": 1,
        "captured_at": captured_at,
        "windows": [{"window_index": 1, "window_id": "1", "tabs": tabs}],
    }


def test_empty_capture_never_overwrites_a_good_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        m.save(snap_with(3, "GOOD"))
        assert m.load()["captured_at"] == "GOOD"

        latest, archive = m.save(snap_with(0, "SHUTDOWN"))
        assert latest is None, "an agent-less capture must not be promoted"
        assert archive.exists(), "it should still be archived to history"
        assert m.load()["captured_at"] == "GOOD", "the good snapshot must survive"


def test_capture_with_agents_is_promoted():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        m.save(snap_with(1, "FIRST"))
        m.save(snap_with(2, "SECOND"))
        assert m.load()["captured_at"] == "SECOND"


def test_agent_count_ignores_non_agent_tabs():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        assert m.agent_tab_count(snap_with(0)) == 0, "a lone shell tab is not an agent"
        assert m.agent_tab_count(snap_with(2)) == 2


def test_each_capture_is_archived_under_its_own_name():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        for i in range(12):
            m.save(snap_with(1, f"c{i}"))
        # Same-second captures must not collide into one filename.
        assert len(list(m.HISTORY_DIR.glob("*.json"))) == 12


def archive_at(m, seconds_ago: float) -> Path:
    """A history archive named as save() would have named it, that long ago."""
    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    path = m.HISTORY_DIR / f"{when.strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap_with(1)))
    return path


def test_history_keeps_about_half_an_hour_at_30_seconds():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        old = [archive_at(m, 30 * k) for k in range(1, 241)]  # two hours of captures
        _, new = m.save(snap_with(1))
        kept = set(m.HISTORY_DIR.glob("*.json"))
        assert new in kept
        # The archive exactly 30 minutes old has aged out by the time save() prunes.
        assert kept - {new} == set(old[:59]), f"kept {len(kept)}: not the last half hour"


def test_the_cap_holds_when_captures_come_fast():
    # 5 s apart: 359 archives inside the window, so the cap, not the window,
    # decides. (At exactly 10 s the window alone lands on 180.)
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        old = [archive_at(m, 5 * k) for k in range(1, 721)]
        _, new = m.save(snap_with(1))
        kept = set(m.HISTORY_DIR.glob("*.json"))
        assert len(kept) == m.HISTORY_CAP == 180, f"kept {len(kept)}"
        assert kept == {new, *old[:179]}, "the cap must drop the oldest"


def test_a_long_gap_keeps_the_floor():
    # After a long sleep every archive is older than the window; age alone
    # would delete the whole pre-sleep history on the first capture.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        old = [archive_at(m, 3 * 3600 + 30 * k) for k in range(100)]
        _, new = m.save(snap_with(1))
        kept = set(m.HISTORY_DIR.glob("*.json"))
        # save() archives before it prunes, so the new capture takes one slot.
        assert kept == {new, *old[:59]}, f"kept {len(kept)}"


def test_an_unparseable_name_is_kept_only_within_the_floor():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        junk = m.HISTORY_DIR / "not-a-time.json"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text(json.dumps(snap_with(1)))
        for k in range(5):
            archive_at(m, 3 * 3600 + k)
        m.save(snap_with(1))
        assert junk.exists(), "within the floor, an unreadable name is kept"
        for k in range(70):
            archive_at(m, 3 * 3600 + 100 + k)
        m.save(snap_with(1))
        assert not junk.exists(), "it sorts oldest, so it goes first"


def test_a_clock_jump_back_never_deletes_the_capture_just_written():
    # After the clock goes back, every archive from the "lost" stretch is
    # dated later than the new capture. With enough of them to fill the cap,
    # dropping the oldest by time would drop the capture just written.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        for k in range(200):
            archive_at(m, -3600 - k)
        _, new = m.save(snap_with(1))
        assert new.exists(), "the capture just written was pruned"


def test_a_future_dated_archive_does_not_cost_real_new_captures():
    # The clock went back: an archive from "tomorrow" sorts newest. The cap
    # must still drop the oldest real captures, not the new ones.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        future = archive_at(m, -86400)
        old = [archive_at(m, 5 * k) for k in range(1, 201)]
        _, new = m.save(snap_with(1))
        kept = set(m.HISTORY_DIR.glob("*.json"))
        assert len(kept) == 180 and future in kept and new in kept
        assert set(old[:178]) <= kept, "a newer real capture was dropped"


def test_load_without_a_snapshot_explains_itself():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        try:
            m.load()
        except FileNotFoundError as exc:
            assert "install" in str(exc).lower()
        else:
            raise AssertionError("expected FileNotFoundError")


def test_a_missing_explicit_path_is_not_blamed_on_the_install():
    # `-f <typo>` must not tell you to reinstall the recorder.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        try:
            m.load(Path(tmp) / "no-such-file.json")
        except FileNotFoundError as exc:
            assert "install" not in str(exc), str(exc)
            assert "Check the path" in str(exc)
        else:
            raise AssertionError("expected FileNotFoundError")


def test_directories_we_create_are_owner_only():
    with tempfile.TemporaryDirectory() as tmp:
        # Assert on HISTORY_DIR, not STATE_DIR: mkdtemp already makes STATE_DIR
        # 0700, so checking it would pass even with our mode= argument removed.
        m = fresh_module(Path(tmp) / "state")
        m.save(snap_with(1))
        mode = m.HISTORY_DIR.stat().st_mode & 0o777
        assert mode == 0o700, f"history dir mode {oct(mode)}; snapshots hold argv"


def test_an_existing_world_readable_directory_gets_hardened():
    # mkdir(mode=) is ignored when the directory already exists, so an install
    # created before this rule existed would stay world-readable forever.
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        (state / "history").mkdir(parents=True)
        state.chmod(0o755)
        (state / "history").chmod(0o755)
        m = fresh_module(state)
        m.save(snap_with(1))
        assert state.stat().st_mode & 0o777 == 0o700, "pre-existing state dir not hardened"
        assert (state / "history").stat().st_mode & 0o777 == 0o700


def test_a_capture_with_no_agents_hardens_the_state_dir_too():
    # Such a capture is archived but never promoted, and only promotion used to
    # harden STATE_DIR. Creating history/ makes STATE_DIR with the umask, so a
    # recorder that only ever saw idle tabs left it world-readable.
    previous = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            created = Path(tmp) / "created"
            m = fresh_module(created)
            m.save(snap_with(0))
            mode = created.stat().st_mode & 0o777
            assert mode == 0o700, f"state dir created by a capture is {oct(mode)}"

            existing = Path(tmp) / "existing"
            existing.mkdir(mode=0o755)
            m = fresh_module(existing)
            m.save(snap_with(0))
            mode = existing.stat().st_mode & 0o777
            assert mode == 0o700, f"existing state dir left at {oct(mode)}"
    finally:
        os.umask(previous)


def test_snapshot_files_are_owner_only():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp) / "state")
        m.save(snap_with(1))
        mode = m.LATEST.stat().st_mode & 0o777
        assert mode == 0o600, f"snapshot file mode {oct(mode)}"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
