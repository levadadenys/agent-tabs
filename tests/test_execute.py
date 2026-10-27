"""execute() is what turns a vetted plan into real keystrokes.

Everything the planner verified was true at plan time; the operator then reads
the dry run and decides. These tests pin down what actually gets sent, and that
a tab which became busy in the meantime is not typed into.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import iterm, restore
from agent_tabs import snapshot as snap_mod


class FakeITerm:
    """A window whose front-to-back index can move under us mid-run."""

    def __init__(self, tabs=3, window=1, window_id="w1"):
        self.window = window
        self.window_id = window_id
        self.tabs = tabs
        self.typed: list[tuple[int, int, str]] = []
        self.created = 0

    def enumerate_tabs(self):
        return [
            {"window_index": self.window, "window_id": self.window_id,
             "tab_index": i + 1, "pane_index": 1,
             "tty": f"/dev/ttys10{i}", "title": f"t{i}"}
            for i in range(self.tabs)
        ]

    def run_in_tab(self, window_id, tab, text):
        self.typed.append((window_id, tab, text))

    def create_tab(self, window_id):
        assert window_id == self.window_id, f"created a tab in {window_id}"
        self.created += 1
        self.tabs += 1


def install(fake, busy_tabs=(), own_tty=None, running=None):
    # execute() re-checks running sessions before typing; stub it so these
    # tests never reach the real ps / lsof / AppleScript.
    restore.running_sessions = lambda: dict(running or {})  # type: ignore[assignment]
    iterm.enumerate_tabs = fake.enumerate_tabs          # type: ignore[assignment]
    iterm.run_in_tab = fake.run_in_tab                  # type: ignore[assignment]
    iterm.create_tab = fake.create_tab                  # type: ignore[assignment]
    restore.current_tty = lambda: own_tty               # type: ignore[assignment]
    restore.live_tabs = lambda: [                       # type: ignore[assignment]
        {**t, "kind": "vim" if t["tab_index"] in busy_tabs else None,
         "busy": t["tab_index"] in busy_tabs}
        for t in fake.enumerate_tabs()
    ]


def actions(*specs):
    out = []
    for tab_index, command, skip in specs:
        out.append(restore.Action(tab_index, f"t{tab_index}", "claude", command, skip))
    return out


def test_sends_one_command_per_runnable_action():
    fake = FakeITerm(tabs=3)
    install(fake)
    ran = restore.execute(actions((1, "cmd1", None), (2, "cmd2", None)), "w1")
    assert ran == 2
    assert fake.typed == [("w1", 1, "cmd1"), ("w1", 2, "cmd2")]


def test_never_sends_a_skipped_action():
    fake = FakeITerm(tabs=3)
    install(fake)
    restore.execute(actions((1, "cmd1", "already running claude"), (2, "cmd2", None)), "w1")
    assert fake.typed == [("w1", 2, "cmd2")], fake.typed


def test_creates_exactly_the_missing_tabs():
    fake = FakeITerm(tabs=2)
    install(fake)
    restore.execute(actions((1, "a", None), (4, "d", None)), "w1")
    assert fake.created == 2, f"expected 2 new tabs, created {fake.created}"


def test_creates_nothing_when_tabs_already_exist():
    fake = FakeITerm(tabs=5)
    install(fake)
    restore.execute(actions((1, "a", None)), "w1")
    assert fake.created == 0


def test_a_tab_that_became_busy_after_planning_is_skipped():
    # The gap between the dry run and --go is however long the operator reads.
    fake = FakeITerm(tabs=3)
    install(fake, busy_tabs={2})
    ran = restore.execute(actions((1, "a", None), (2, "b", None)), "w1")
    assert ran == 1
    assert fake.typed == [("w1", 1, "a")], fake.typed


def test_force_overrides_a_tab_that_became_busy():
    fake = FakeITerm(tabs=3)
    install(fake, busy_tabs={2})
    ran = restore.execute(actions((1, "a", None), (2, "b", None)), "w1", force=True)
    assert ran == 2


def test_never_types_into_our_own_tab():
    fake = FakeITerm(tabs=3)
    install(fake, own_tty="ttys101")  # tab 2
    restore.execute(actions((1, "a", None), (2, "b", None)), "w1")
    assert fake.typed == [("w1", 1, "a")], fake.typed


def test_nothing_runnable_touches_nothing():
    fake = FakeITerm(tabs=3)
    install(fake)
    assert restore.execute(actions((1, "a", "skip me")), "w1") == 0
    assert fake.typed == [] and fake.created == 0


def test_typing_survives_the_window_index_moving():
    """iTerm's window index is its front-to-back position.

    Focusing any window renumbers them, so an index captured while planning can
    point at a different window by the time we type. Targeting by id makes the
    renumber irrelevant; targeting by index would send one window's sessions
    into another.
    """
    fake = FakeITerm(tabs=3, window=1, window_id="stable-id")
    install(fake)
    acts = actions((1, "cmd1", None), (2, "cmd2", None))
    # The operator clicks another window: ours is now third from front.
    fake.window = 3
    ran = restore.execute(acts, "stable-id")
    assert ran == 2
    assert fake.typed == [("stable-id", 1, "cmd1"), ("stable-id", 2, "cmd2")], fake.typed


def test_a_window_that_disappeared_types_nothing():
    """A window closed after planning must receive nothing at all."""
    fake = FakeITerm(tabs=3, window_id="still-here")
    install(fake)
    ran = restore.execute(actions((1, "cmd1", None), (2, "cmd2", None)), "gone")
    assert ran == 0
    assert fake.typed == [], f"typed into a vanished window: {fake.typed}"
    assert fake.created == 0, "created tabs in a vanished window"


def test_a_session_started_after_planning_is_not_started_again():
    """Copilot round 4: the plan's duplicate check is a snapshot. A session
    resumed by hand -- or by another restore -- between planning and typing
    must not be started a second time."""
    fake = FakeITerm(tabs=3)
    install(fake, running={"sess-2": "live window 9 tab 1"})
    acts = [
        restore.Action(1, "t1", "claude", "claude --resume sess-1", None, "sess-1"),
        restore.Action(2, "t2", "claude", "claude --resume sess-2", None, "sess-2"),
    ]
    ran = restore.execute(acts, "w1")
    assert ran == 1
    assert fake.typed == [("w1", 1, "claude --resume sess-1")], fake.typed


def test_force_does_not_override_the_execute_time_check():
    fake = FakeITerm(tabs=3)
    install(fake, running={"sess-1": "live window 9 tab 1"})
    acts = [restore.Action(1, "t1", "claude", "claude --resume sess-1", None, "sess-1")]
    assert restore.execute(acts, "w1", force=True) == 0
    assert fake.typed == []


def test_no_blank_tab_is_created_for_a_session_started_since_the_plan():
    """Sonnet review: tabs were added for every planned action before the
    recheck, so a session started meanwhile left a blank tab behind."""
    fake = FakeITerm(tabs=1)
    install(fake, running={"sess-3": "live window 9 tab 1"})
    acts = [
        restore.Action(1, "t1", "claude", "claude --resume sess-1", None, "sess-1"),
        restore.Action(3, "t3", "claude", "claude --resume sess-3", None, "sess-3"),
    ]
    assert restore.execute(acts, "w1") == 1
    assert fake.created == 0, f"created {fake.created} blank tab(s)"


def test_an_unusable_state_dir_is_a_message_not_a_traceback():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "file"
        blocker.write_text("")
        original = snap_mod.STATE_DIR
        snap_mod.STATE_DIR = blocker / "state"  # a directory under a file
        try:
            with restore.restore_lock():
                raise AssertionError("took a lock in an impossible directory")
        except restore.RestoreLockError as exc:
            assert "Nothing was typed" in str(exc), exc
        finally:
            snap_mod.STATE_DIR = original


def test_two_restores_cannot_run_at_once():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        original = snap_mod.STATE_DIR
        snap_mod.STATE_DIR = Path(tmp)
        try:
            with restore.restore_lock():
                try:
                    with restore.restore_lock():
                        raise AssertionError("a second restore acquired the lock")
                except restore.RestoreInProgress as exc:
                    assert "Another" in str(exc)
            with restore.restore_lock():
                pass  # released once the first finished
        finally:
            snap_mod.STATE_DIR = original


if __name__ == "__main__":
    raise SystemExit(run(globals()))
