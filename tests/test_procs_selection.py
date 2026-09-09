"""Which process a tab is 'running' decides the whole snapshot.

Pure data in, pure data out: Proc is a plain dataclass that foreground_proc
consumes directly, so these drive the real call site with no subprocess.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import procs  # noqa: E402
from runner import run  # noqa: E402

AGENTS = frozenset({"claude", "codex", "grok"})


def chain(*commands: str) -> list[procs.Proc]:
    """A login -> shell -> ... parent chain on one tty."""
    out = []
    for i, command in enumerate(commands):
        out.append(procs.Proc(100 + i, 99 + i, "ttys001", command))
    return out


def test_priority_agent_beats_its_own_deeper_child():
    # codex spawns node_repl; without the priority set the child wins and the
    # tab looks like an unknown command instead of a resumable codex session.
    got = procs.foreground_proc(
        chain("/usr/bin/login -fpl me", "-zsh", "codex resume", "node_repl"), AGENTS
    )
    assert got.argv0 == "codex", got.command


def test_claude_beats_a_caffeinate_child():
    got = procs.foreground_proc(chain("-zsh", "claude", "caffeinate -i -t 300"), AGENTS)
    assert got.argv0 == "claude", got.command


def test_deepest_non_shell_wins_when_no_agent_present():
    got = procs.foreground_proc(chain("-zsh", "ssh host", "vim"), AGENTS)
    assert got.argv0 == "vim", got.command


def test_falls_back_to_the_shell_when_everything_is_a_shell():
    got = procs.foreground_proc(chain("/usr/bin/login -fpl me", "-zsh"), AGENTS)
    assert got is not None and got.is_shell


def test_empty_tty_gives_nothing():
    assert procs.foreground_proc([], AGENTS) is None


def test_parent_cycle_does_not_hang():
    # A recycled pid can make the parent chain loop; the guard must survive it.
    a = procs.Proc(10, 11, "ttys001", "vim")
    b = procs.Proc(11, 10, "ttys001", "less")
    got = procs.foreground_proc([a, b], AGENTS)
    assert got in (a, b)


def test_tty_normalisation_matches_ps_and_iterm_forms():
    assert procs.normalise_tty("/dev/ttys003") == procs.normalise_tty("ttys003")


if __name__ == "__main__":
    raise SystemExit(run(globals()))
