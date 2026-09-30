"""Rebuilding windows iTerm did not restore, without starting anything twice.

`--all --create-missing` exists for a reboot where iTerm brought back no
windows. That is also the moment you are most likely to have resumed a few
sessions by hand first -- so every restore path now skips a session already
running anywhere, not just a busy target tab.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import iterm, restore, snapshot as snap_mod  # noqa: E402
from runner import run  # noqa: E402

WINDOW = {
    "window_index": 1,
    "tabs": [
        {"tab_index": 1, "pane_index": 1, "title": "A", "kind": "claude",
         "session_id": "sess-a", "restore": "cd /r && claude --resume sess-a"},
        {"tab_index": 2, "pane_index": 1, "title": "B", "kind": "codex",
         "session_id": "sess-b", "restore": "cd /r && codex resume sess-b"},
        {"tab_index": 3, "pane_index": 1, "title": "C", "kind": "shell",
         "session_id": None, "restore": "cd /r"},
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
    live_capture = {"windows": [
        {"window_index": 2, "tabs": [
            {"tab_index": 1, "kind": "claude", "session_id": "s1"},
            {"tab_index": 2, "kind": "shell", "session_id": None},
            {"tab_index": 3, "kind": "command", "session_id": "not-an-agent"},
        ]},
    ]}
    original = snap_mod.build
    snap_mod.build = lambda: live_capture  # type: ignore[assignment]
    try:
        running = restore.running_sessions()
    finally:
        snap_mod.build = original  # type: ignore[assignment]
    assert running == {"s1": "live window 2 tab 1"}, running


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
            {"window_index": n, "window_id": wid, "tab_index": t + 1, "pane_index": 1,
             "tty": f"/dev/ttys9{n}{t}", "title": ""}
            for n, (wid, count) in enumerate(self.windows.items(), start=1)
            for t in range(count)
        ]

    def create_tab(self, window_id):
        self.windows[window_id] += 1

    def run_in_tab(self, window_id, tab, text):
        self.typed.append((window_id, tab, text))


def install(fake):
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


if __name__ == "__main__":
    raise SystemExit(run(globals()))
