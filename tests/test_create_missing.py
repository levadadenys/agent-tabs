"""Rebuilding windows iTerm did not restore, without starting anything twice.

`--all --create-missing` exists for a reboot where iTerm brought back no
windows. That is also the moment you are most likely to have resumed a few
sessions by hand first -- so every restore path now skips a session already
running anywhere, not just a busy target tab.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import iterm, restore
from agent_tabs import snapshot as snap_mod

# Captured before any test stubs it: install() replaces restore.running_sessions
# for the tests that drive execute(), and a later test of the real function must
# not end up exercising the stub instead.
REAL_RUNNING_SESSIONS = restore.running_sessions

WINDOW = {
    "window_index": 1,
    "tabs": [
        {
            "tab_index": 1,
            "pane_index": 1,
            "title": "A",
            "kind": "claude",
            "session_id": "sess-a",
            "restore": "cd /r && claude --resume sess-a",
        },
        {
            "tab_index": 2,
            "pane_index": 1,
            "title": "B",
            "kind": "codex",
            "session_id": "sess-b",
            "restore": "cd /r && codex resume sess-b",
        },
        {
            "tab_index": 3,
            "pane_index": 1,
            "title": "C",
            "kind": "shell",
            "session_id": None,
            "restore": "cd /r",
        },
    ],
}


def plan_new(running=None, force=False):
    restore.current_tty = lambda: None  # type: ignore[assignment]
    return {a.tab_index: a for a in restore.plan(WINDOW, [], None, force, running)}


def test_a_session_running_elsewhere_is_not_started_again():
    actions = plan_new({"sess-a": "live window 3 tab 2"})
    assert not actions[1].will_run
    assert actions[1].skip_reason == "session already running in live window 3 tab 2"
    assert actions[2].will_run, "an unrelated session must still start"


def test_force_does_not_start_a_duplicate_session():
    # --force is about typing into a busy tab, not about duplicating a session.
    actions = plan_new({"sess-a": "live window 3 tab 2"}, force=True)
    assert not actions[1].will_run


def test_nothing_running_starts_everything():
    actions = plan_new({})
    assert all(a.will_run for a in actions.values())


def test_tabs_without_a_session_are_not_deduplicated():
    # Plain shells and ssh have no session id; there is nothing to duplicate.
    actions = plan_new({"sess-a": "x", "sess-b": "y"})
    assert actions[3].will_run


def test_running_sessions_reads_agents_from_the_live_capture():
    live_capture = {
        "windows": [
            {
                "window_index": 2,
                "tabs": [
                    {"tab_index": 1, "kind": "claude", "session_id": "s1"},
                    {"tab_index": 2, "kind": "shell", "session_id": None},
                    {"tab_index": 3, "kind": "command", "session_id": "not-an-agent"},
                ],
            },
        ]
    }
    original = snap_mod.build
    snap_mod.build = lambda: live_capture  # type: ignore[assignment]
    try:
        running = REAL_RUNNING_SESSIONS()
    finally:
        snap_mod.build = original  # type: ignore[assignment]
    assert running == {"s1": "live window 2 tab 1"}, running


def window_of(index, sessions):
    return {
        "window_index": index,
        "tabs": [
            {
                "tab_index": i + 1,
                "pane_index": 1,
                "title": f"t{i}",
                "kind": "claude",
                "session_id": sid,
                "restore": f"claude --resume {sid}",
            }
            for i, sid in enumerate(sessions)
        ],
    }


def test_a_session_in_two_windows_of_one_restore_starts_once():
    """Copilot: dedup must hold across windows within one restore.

    The live set is captured before planning, so a session appearing twice in
    the snapshot itself -- in two windows here -- was planned twice.
    """
    restore.current_tty = lambda: None  # type: ignore[assignment]
    running = {}
    first = restore.plan(window_of(1, ["same", "x"]), [], None, False, running)
    second = restore.plan(window_of(2, ["same"]), [], None, False, running)
    started = [a.command for a in first + second if a.will_run]
    assert started.count("claude --resume same") == 1, started
    assert second[0].skip_reason == "same session as tab 1 of snapshot window 1, started once"


def test_a_session_twice_in_one_window_starts_once():
    restore.current_tty = lambda: None  # type: ignore[assignment]
    actions = restore.plan(window_of(1, ["same", "same"]), [], None, False, {})
    assert [a.will_run for a in actions] == [True, False]


def test_an_empty_running_map_stays_the_callers():
    # `running or {}` replaced an empty caller dict with a fresh one, so a
    # reservation made while planning one window vanished before the next.
    restore.current_tty = lambda: None  # type: ignore[assignment]
    running: dict = {}
    restore.plan(window_of(1, ["sid"]), [], None, False, running)
    assert "sid" in running, "the reservation did not reach the caller's map"


def test_a_live_session_still_reads_as_running():
    restore.current_tty = lambda: None  # type: ignore[assignment]
    actions = restore.plan(window_of(1, ["s"]), [], None, False, {"s": "live window 4 tab 2"})
    assert actions[0].skip_reason == "session already running in live window 4 tab 2"


class NewWindowITerm:
    """Models iTerm creating a window with a given number of tabs."""

    def __init__(self):
        self.windows = {}
        self.typed = []

    def create_window(self, tab_count):
        window_id = f"new{len(self.windows) + 1}"
        self.windows[window_id] = tab_count
        return window_id

    def enumerate_tabs(self):
        return [
            {
                "window_index": n,
                "window_id": wid,
                "tab_index": t + 1,
                "pane_index": 1,
                "tty": f"/dev/ttys9{n}{t}",
                "title": "",
            }
            for n, (wid, count) in enumerate(self.windows.items(), start=1)
            for t in range(count)
        ]

    def create_tab(self, window_id):
        self.windows[window_id] += 1

    def run_in_tab(self, window_id, tab, text):
        self.typed.append((window_id, tab, text))


def install(fake):
    restore.running_sessions = lambda: {}  # type: ignore[assignment]
    iterm.create_window = fake.create_window  # type: ignore[assignment]
    iterm.enumerate_tabs = fake.enumerate_tabs  # type: ignore[assignment]
    iterm.create_tab = fake.create_tab  # type: ignore[assignment]
    iterm.run_in_tab = fake.run_in_tab  # type: ignore[assignment]
    restore.current_tty = lambda: None  # type: ignore[assignment]
    restore.live_tabs = lambda: [  # type: ignore[assignment]
        {**t, "kind": None, "busy": False} for t in fake.enumerate_tabs()
    ]


def test_restore_into_new_window_fills_the_window_it_created():
    fake = NewWindowITerm()
    install(fake)
    actions = list(plan_new({}).values())
    ran = restore.restore_into_new_window(actions)
    assert ran == 3
    assert {w for w, _, _ in fake.typed} == {"new1"}, "typed outside the new window"
    assert [t for _, t, _ in fake.typed] == [1, 2, 3]


def test_restore_into_new_window_creates_nothing_when_nothing_runs():
    # Every session already running: no empty window should appear.
    fake = NewWindowITerm()
    install(fake)
    actions = list(plan_new({"sess-a": "x", "sess-b": "y"}).values())
    actions = [a for a in actions if a.tab_index != 3]  # drop the plain shell
    assert restore.restore_into_new_window(actions) == 0
    assert fake.windows == {}, "created an empty window"


def test_no_window_is_created_when_everything_started_since_the_plan():
    """Sonnet review: the window was created first and the recheck came
    after, leaving an empty window when every session had been resumed by
    hand meanwhile."""
    fake = NewWindowITerm()
    install(fake)
    actions = [a for a in plan_new({}).values() if a.session_id]
    restore.running_sessions = lambda: {  # type: ignore[assignment]
        a.session_id: "live window 9 tab 1" for a in actions
    }
    assert restore.restore_into_new_window(actions) == 0
    assert fake.windows == {}, f"created an empty window: {fake.windows}"


def test_a_new_window_gets_only_the_tabs_it_will_use():
    fake = NewWindowITerm()
    install(fake)
    actions = [a for a in plan_new({}).values() if a.session_id]
    last = max(actions, key=lambda a: a.tab_index)
    restore.running_sessions = lambda: {  # type: ignore[assignment]
        last.session_id: "live window 9 tab 1"
    }
    ran = restore.restore_into_new_window(actions)
    assert ran == len(actions) - 1, ran
    assert list(fake.windows.values()) == [last.tab_index - 1], fake.windows


if __name__ == "__main__":
    raise SystemExit(run(globals()))
