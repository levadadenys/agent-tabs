"""execute() is what turns a vetted plan into real keystrokes.

Everything the planner verified was true at plan time; the operator then reads
the dry run and decides. These tests pin down what actually gets sent, and that
a tab which became busy in the meantime is not typed into.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import iterm, restore  # noqa: E402
from runner import run  # noqa: E402


class FakeITerm:
    def __init__(self, tabs=3, window=1):
        self.window = window
        self.tabs = tabs
        self.typed: list[tuple[int, int, str]] = []
        self.created = 0

    def enumerate_tabs(self):
        return [
            {"window_index": self.window, "tab_index": i + 1, "pane_index": 1,
             "tty": f"/dev/ttys10{i}", "title": f"t{i}"}
            for i in range(self.tabs)
        ]

    def run_in_tab(self, window, tab, text):
        self.typed.append((window, tab, text))

    def create_tab(self, window):
        self.created += 1
        self.tabs += 1


def install(fake, busy_tabs=(), own_tty=None):
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
    ran = restore.execute(actions((1, "cmd1", None), (2, "cmd2", None)), 1)
    assert ran == 2
    assert fake.typed == [(1, 1, "cmd1"), (1, 2, "cmd2")]


def test_never_sends_a_skipped_action():
    fake = FakeITerm(tabs=3)
    install(fake)
    restore.execute(actions((1, "cmd1", "already running claude"), (2, "cmd2", None)), 1)
    assert fake.typed == [(1, 2, "cmd2")], fake.typed


def test_creates_exactly_the_missing_tabs():
    fake = FakeITerm(tabs=2)
    install(fake)
    restore.execute(actions((1, "a", None), (4, "d", None)), 1)
    assert fake.created == 2, f"expected 2 new tabs, created {fake.created}"


def test_creates_nothing_when_tabs_already_exist():
    fake = FakeITerm(tabs=5)
    install(fake)
    restore.execute(actions((1, "a", None)), 1)
    assert fake.created == 0


def test_a_tab_that_became_busy_after_planning_is_skipped():
    # The gap between the dry run and --go is however long the operator reads.
    fake = FakeITerm(tabs=3)
    install(fake, busy_tabs={2})
    ran = restore.execute(actions((1, "a", None), (2, "b", None)), 1)
    assert ran == 1
    assert fake.typed == [(1, 1, "a")], fake.typed


def test_force_overrides_a_tab_that_became_busy():
    fake = FakeITerm(tabs=3)
    install(fake, busy_tabs={2})
    ran = restore.execute(actions((1, "a", None), (2, "b", None)), 1, force=True)
    assert ran == 2


def test_never_types_into_our_own_tab():
    fake = FakeITerm(tabs=3)
    install(fake, own_tty="ttys101")  # tab 2
    restore.execute(actions((1, "a", None), (2, "b", None)), 1)
    assert fake.typed == [(1, 1, "a")], fake.typed


def test_nothing_runnable_touches_nothing():
    fake = FakeITerm(tabs=3)
    install(fake)
    assert restore.execute(actions((1, "a", "skip me")), 1) == 0
    assert fake.typed == [] and fake.created == 0


if __name__ == "__main__":
    raise SystemExit(run(globals()))
