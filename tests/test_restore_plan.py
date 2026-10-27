"""The restore planner must never clobber a live agent or its own tab."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import restore

SNAP_WINDOW = {
    "window_index": 1,
    "tabs": [
        {
            "tab_index": 1,
            "pane_index": 1,
            "title": "A",
            "kind": "claude",
            "restore": "cd /repo && claude --resume aaa",
        },
        {
            "tab_index": 2,
            "pane_index": 1,
            "title": "B",
            "kind": "claude",
            "restore": "cd /repo && claude --resume bbb",
        },
        {
            "tab_index": 3,
            "pane_index": 1,
            "title": "C",
            "kind": "codex",
            "restore": "cd /repo && codex resume ccc",
        },
        {"tab_index": 4, "pane_index": 1, "title": "D", "kind": "shell", "restore": ""},
    ],
}


def live(window=1):
    return [
        {
            "window_index": window,
            "window_id": f"w{window}",
            "tab_index": 1,
            "pane_index": 1,
            "tty": "/dev/ttys001",
            "title": "A",
            "kind": "claude",
            "busy": True,
        },
        {
            "window_index": window,
            "window_id": f"w{window}",
            "tab_index": 2,
            "pane_index": 1,
            "tty": "/dev/ttys002",
            "title": "B",
            "kind": "zsh",
            "busy": False,
        },
        {
            "window_index": window,
            "window_id": f"w{window}",
            "tab_index": 3,
            "pane_index": 1,
            "tty": "/dev/ttys003",
            "title": "C",
            "kind": "zsh",
            "busy": False,
        },
        {
            "window_index": window,
            "window_id": f"w{window}",
            "tab_index": 4,
            "pane_index": 1,
            "tty": "/dev/ttys004",
            "title": "D",
            "kind": "zsh",
            "busy": False,
        },
    ]


def run(monkey_tty, force=False, target="w1"):
    restore.current_tty = lambda: monkey_tty  # type: ignore[assignment]
    return {a.tab_index: a for a in restore.plan(SNAP_WINDOW, live(), target, force)}


def test_skips_tab_already_running_an_agent():
    actions = run("ttys999")
    assert not actions[1].will_run
    assert "already running claude" in actions[1].skip_reason
    assert actions[2].will_run and actions[3].will_run


def test_skips_the_tab_the_command_runs_from():
    actions = run("ttys002")
    assert not actions[2].will_run
    assert "running from" in actions[2].skip_reason
    # An unrelated idle tab is still restored.
    assert actions[3].will_run


def test_force_overrides_busy_but_never_own_tab():
    actions = run("ttys002", force=True)
    assert actions[1].will_run, "force should reclaim a busy tab"
    assert not actions[2].will_run, "force must not clobber our own tab"


def test_tab_with_nothing_recorded_is_skipped():
    actions = run("ttys999")
    assert not actions[4].will_run
    assert actions[4].skip_reason == "nothing recorded"


def test_new_window_target_ignores_live_busy_state():
    actions = {a.tab_index: a for a in restore.plan(SNAP_WINDOW, live(), None, False)}
    assert actions[1].will_run, "a fresh window has no busy tabs to protect"


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    raise SystemExit(1 if failures else 0)
