"""Restore must not type into a tab that holds live work.

The original rule only recognised the three agent CLIs, so a tab that had since
been given to vim, psql or ssh was treated as free -- and over ssh the typed
text executes on the remote host.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import procs, restore


def proc(command: str) -> procs.Proc:
    return procs.Proc(4242, 1, "ttys001", command)


def test_agent_processes_are_busy():
    for command in ("claude --resume abc", "codex resume abc", "grok"):
        assert restore.is_busy(proc(command)), command


def test_non_agent_work_is_also_busy():
    # The regression the reviewers found: these were all treated as free.
    for command in ("ssh prod-box", "vim notes.txt", "psql postgres://db",
                    "tail -f log", "docker attach web", "python3 manage.py"):
        assert restore.is_busy(proc(command)), command


def test_idle_shells_are_not_busy():
    for command in ("-zsh", "zsh", "bash", "-bash", "fish", "tcsh", "ksh", "dash",
                    "/usr/bin/login -fpl someone"):
        assert not restore.is_busy(proc(command)), command


def test_a_shell_running_a_script_is_busy():
    # argv0 is a shell name, but the tab is not at an idle prompt.
    for command in ("bash deploy.sh", "zsh -c 'make build'", "sh ./release.sh"):
        assert restore.is_busy(proc(command)), command


def test_empty_tab_is_not_busy():
    assert not restore.is_busy(None)


def _plan_for(live_kind: str, busy: bool):
    window = {
        "window_index": 1,
        "tabs": [{"tab_index": 1, "pane_index": 1, "title": "A", "kind": "claude",
                  "restore": "cd /repo && claude --resume aaa"}],
    }
    live = [{"window_index": 1, "window_id": "w1", "tab_index": 1, "pane_index": 1, "tty": "/dev/ttys001",
             "title": "A", "kind": live_kind, "busy": busy}]
    restore.current_tty = lambda: "ttys999"  # type: ignore[assignment]
    return restore.plan(window, live, "w1", force=False)[0]


def test_planner_skips_a_live_ssh_tab_without_force():
    action = _plan_for("ssh", busy=True)
    assert not action.will_run
    assert "already running ssh" in action.skip_reason


def test_planner_still_restores_an_idle_tab():
    action = _plan_for("zsh", busy=False)
    assert action.will_run


if __name__ == "__main__":
    raise SystemExit(run(globals()))
