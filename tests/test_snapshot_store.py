"""snapshot.save() must never let a degraded capture destroy a good one.

This is the tool's core safety property: the recorder fires every 30s, and a
capture taken while iTerm is shutting down sees no agents. If that were
promoted, the last good snapshot would be gone at exactly the moment it is
needed. Driven through the real save/load/history call sites -- the bug this
guards against is filesystem state, so there is nothing worth mocking.
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run  # noqa: E402


def fresh_module(state_dir: Path):
    """Re-import snapshot with AGENT_TABS_HOME pointed at a temp directory."""
    os.environ["AGENT_TABS_HOME"] = str(state_dir)
    import agent_tabs.snapshot as snap_mod
    return importlib.reload(snap_mod)


def snap_with(agent_tabs: int, captured_at="2026-01-01T00:00:00+00:00") -> dict:
    tabs = [
        {"tab_index": i + 1, "pane_index": 1, "title": f"t{i}", "kind": "claude",
         "session_id": f"id{i}", "cwd": "/repo", "restore": f"claude --resume id{i}"}
        for i in range(agent_tabs)
    ]
    tabs.append({"tab_index": 99, "pane_index": 1, "title": "shell", "kind": "shell",
                 "session_id": None, "cwd": "/tmp", "restore": "cd /tmp"})
    return {"version": 1, "captured_at": captured_at,
            "windows": [{"window_index": 1, "window_id": "1", "tabs": tabs}]}


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


def test_history_is_capped_and_each_capture_is_distinct():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp))
        m.HISTORY_KEEP = 5
        for i in range(12):
            m.save(snap_with(1, f"c{i}"))
        files = list(m.HISTORY_DIR.glob("*.json"))
        assert len(files) == 5, f"expected 5 archives, found {len(files)}"
        # Same-second captures must not collide into one filename.
        assert len({f.name for f in files}) == 5


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


def test_snapshot_files_are_owner_only():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh_module(Path(tmp) / "state")
        m.save(snap_with(1))
        mode = m.LATEST.stat().st_mode & 0o777
        assert mode == 0o600, f"snapshot file mode {oct(mode)}"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
