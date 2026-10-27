"""The restore command itself, driven end to end with iTerm and typing faked.

The library pieces are tested on their own elsewhere; these pin down the
wiring in bin/agent-tabs that no other test reaches: the lock is taken, one
`running` map is shared by every window of a restore, and --force is passed
through as given. Each was a surviving mutation in review.
"""

import argparse
import importlib.util
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner import run  # noqa: E402

from agent_tabs import iterm, restore  # noqa: E402
from agent_tabs import snapshot as snap_mod  # noqa: E402

_loader = SourceFileLoader("agent_tabs_cli", str(ROOT / "bin" / "agent-tabs"))
_spec = importlib.util.spec_from_loader("agent_tabs_cli", _loader)
assert _spec is not None
cli = importlib.util.module_from_spec(_spec)
_loader.exec_module(cli)


def snap(*windows):
    """Each window is a list of (title, session id) tabs."""
    return {
        "captured_at": "2026-10-08T10:00:00+00:00",
        "windows": [
            {
                "window_index": i + 1,
                "tabs": [
                    {
                        "tab_index": j + 1,
                        "pane_index": 1,
                        "title": title,
                        "kind": "claude",
                        "session_id": sid,
                        "restore": f"claude --resume {sid}",
                    }
                    for j, (title, sid) in enumerate(tabs)
                ],
            }
            for i, tabs in enumerate(windows)
        ],
    }


def live(*windows):
    return [
        {
            "window_index": wi,
            "window_id": f"w{wi}",
            "tab_index": ti,
            "pane_index": 1,
            "tty": f"/dev/ttys{wi}{ti}",
            "title": t,
            "kind": None,
            "busy": False,
        }
        for wi, titles in enumerate(windows, start=1)
        for ti, t in enumerate(titles, start=1)
    ]


class Recorder:
    """Stands in for everything that would touch iTerm or type."""

    def __init__(self):
        self.calls = []  # (where, force, session ids that would run)

    def execute(self, actions, window_id, force=False):
        self.calls.append((window_id, force, [a.session_id for a in actions if a.will_run]))
        return sum(a.will_run for a in actions)

    def new_window(self, actions, force=False):
        return self.execute(actions, "new", force)

    def started(self):
        return [sid for _, _, sids in self.calls for sid in sids]


def setup(state: Path, the_snap, the_live) -> Recorder:
    rec = Recorder()
    snap_mod.STATE_DIR = state
    snap_mod.load = lambda path=None: the_snap  # type: ignore[assignment]
    snap_mod.pre_boot_warning = lambda s: None  # type: ignore[assignment]
    restore.live_tabs = lambda: the_live  # type: ignore[assignment]
    restore.current_window_index = lambda l: None  # type: ignore[assignment]
    restore.current_tty = lambda: None  # type: ignore[assignment]
    restore.running_sessions = lambda: {}  # type: ignore[assignment]
    restore.execute = rec.execute  # type: ignore[assignment]
    restore.restore_into_new_window = rec.new_window  # type: ignore[assignment]
    iterm.create_window = _no_real_windows  # type: ignore[assignment]
    return rec


def _no_real_windows(*_):
    raise AssertionError("reached the real iTerm create_window")


def args(**overrides):
    base = dict(
        file=None,
        go=True,
        all=True,
        create_missing=False,
        window=None,
        new_window=False,
        force=False,
    )
    return argparse.Namespace(**{**base, **overrides})


def test_go_is_refused_while_another_restore_holds_the_lock():
    with tempfile.TemporaryDirectory() as tmp:
        rec = setup(Path(tmp), snap([("web", "s1")]), live(["web"]))
        with restore.restore_lock():
            assert cli.cmd_restore(args()) == 2
        assert rec.calls == [], f"typed while another restore held the lock: {rec.calls}"
        assert cli.cmd_restore(args()) == 0
        assert rec.started() == ["s1"], "setup: once released, the restore runs"


def test_a_session_in_two_paired_windows_starts_once():
    with tempfile.TemporaryDirectory() as tmp:
        rec = setup(
            Path(tmp),
            snap([("web", "dup"), ("web2", "a")], [("api", "dup"), ("api2", "b")]),
            live(["web", "web2"], ["api", "api2"]),
        )
        assert cli.cmd_restore(args()) == 0
        started = rec.started()
        assert started.count("dup") == 1, f"started twice: {rec.calls}"
        assert sorted(started) == ["a", "b", "dup"], started


def test_a_session_in_a_paired_and_a_new_window_starts_once():
    with tempfile.TemporaryDirectory() as tmp:
        rec = setup(
            Path(tmp),
            snap([("web", "dup")], [("api", "dup"), ("api2", "b")]),
            live(["web"]),
        )
        assert cli.cmd_restore(args(create_missing=True)) == 0
        assert [where for where, _, _ in rec.calls] == ["w1", "new"], rec.calls
        assert rec.started().count("dup") == 1, f"started twice: {rec.calls}"


def test_go_force_without_a_terminal_types_nothing():
    # Without a terminal restore cannot find the tab it runs from; only that
    # tab being busy keeps restore out of it, and --force lifts exactly that.
    with tempfile.TemporaryDirectory() as tmp:
        rec = setup(Path(tmp), snap([("web", "s1")]), live(["web"]))
        assert cli.cmd_restore(args(force=True)) == 2
        assert rec.calls == [], f"typed with --force and no terminal: {rec.calls}"
        # A terminal iTerm does not own (tmux, ssh) is no better: restore still
        # cannot find its own tab.
        restore.current_tty = lambda: "ttys555"  # type: ignore[assignment]
        assert cli.cmd_restore(args(force=True)) == 2
        assert rec.calls == [], f"typed with --force from a foreign terminal: {rec.calls}"
        restore.current_tty = lambda: None  # type: ignore[assignment]
        # Not --force, and not --go: both are still allowed without a terminal.
        assert cli.cmd_restore(args(go=False, force=True)) == 0
        assert cli.cmd_restore(args()) == 0
        assert rec.started() == ["s1"], "setup: without --force, the restore runs"


def test_force_is_passed_through_as_given():
    for force in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            rec = setup(Path(tmp), snap([("web", "s1")]), live(["web"], ["launcher"]))
            # From an iTerm tab (window 2's): without one, --go --force is refused.
            restore.current_tty = lambda: "ttys21"  # type: ignore[assignment]
            assert cli.cmd_restore(args(force=force)) == 0
            assert [f for _, f, _ in rec.calls] == [force], rec.calls


def test_window_counts_name_only_windows_that_start_something():
    import contextlib
    import io

    with tempfile.TemporaryDirectory() as tmp:
        the_snap = snap([("web", "s1")], [("api", "s2")])
        setup(Path(tmp), the_snap, live(["web"], ["api"]))
        restore.running_sessions = lambda: {"s2": "live window 9 tab 1"}  # type: ignore[assignment]
        for go in (False, True):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert cli.cmd_restore(args(go=go)) == 0
            assert "across 1 windows" in out.getvalue(), out.getvalue()


def test_a_single_window_new_window_restore_goes_through_the_recheck():
    # restore N --new-window --go must use restore_into_new_window, which
    # re-checks running sessions before creating anything.
    with tempfile.TemporaryDirectory() as tmp:
        rec = setup(Path(tmp), snap([("web", "s1")]), live(["web"]))
        assert cli.cmd_restore(args(all=False, window=1, new_window=True)) == 0
        assert [where for where, _, _ in rec.calls] == ["new"], rec.calls


def test_flag_errors_come_before_touching_iterm():
    def boom():
        raise AssertionError("asked iTerm before validating flags")

    with tempfile.TemporaryDirectory() as tmp:
        setup(Path(tmp), snap([("web", "s1")]), live(["web"]))
        restore.live_tabs = boom  # type: ignore[assignment]
        assert cli.cmd_restore(args(all=False, create_missing=True, go=False)) == 2
        assert cli.cmd_restore(args(new_window=True, go=False)) == 2


if __name__ == "__main__":
    raise SystemExit(run(globals()))
