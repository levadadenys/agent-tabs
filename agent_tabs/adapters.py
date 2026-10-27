"""Per-agent identification: turn a running process into a resumable command.

Every supported agent scopes its resume picker to the working directory, so a
restore command is always `cd <cwd> && <resume>` -- never the resume alone.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
from pathlib import Path

from . import config, procs

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Characters that make a shell do something other than run one command. A
# recorded command line is replayed verbatim, so anything carrying these is not
# safe to type into a shell unattended.
SHELL_METACHARACTERS = set(";&|<>`$()\n\r\\!*?{}[]\"'")

HOME = Path.home()
CLAUDE_SESSIONS = HOME / ".claude" / "sessions"
GROK_SESSIONS = HOME / ".grok" / "sessions"


def _cd(cwd: str | None) -> str:
    return f"cd {shlex.quote(cwd)}" if cwd else ""


def _join(cwd: str | None, command: str) -> str:
    prefix = _cd(cwd)
    return f"{prefix} && {command}" if prefix else command


def _result(kind: str, session_id: str | None, cwd: str | None, command: str) -> dict:
    """Uniform adapter result, with a warning when the command is not trustworthy.

    A resume run from the wrong directory reports the session as missing, so a
    command with no `cd` is worse than useless -- it looks right and fails. Say
    so rather than emitting it silently.
    """
    warning = None
    if command and not cwd:
        warning = "working directory unknown - resume is cwd-scoped and will likely fail"
    return {
        "kind": kind,
        "session_id": session_id,
        "cwd": cwd,
        "restore": _join(cwd, command) if command else _cd(cwd),
        "warning": warning,
    }


def resolve_claude(proc: procs.Proc, cwd: str | None) -> dict:
    """Claude writes a live pointer per pid: ~/.claude/sessions/<pid>.json."""
    session_id = None
    pointer = CLAUDE_SESSIONS / f"{proc.pid}.json"
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text())
            session_id = data.get("sessionId")
            cwd = data.get("cwd") or cwd
        except (json.JSONDecodeError, OSError):
            pass
    if not session_id:
        # Fallback for a session started as `claude --resume <uuid>`, which
        # carries its id in argv even if the pointer file is missing.
        match = UUID_RE.search(proc.command)
        if match:
            session_id = match.group(0)
    # The id is interpolated into a command that gets typed into a shell, so it
    # must be a UUID and nothing else -- including when it came from the JSON
    # pointer file, which is not ours to trust.
    if session_id and not UUID_RE.fullmatch(session_id):
        session_id = None
    command = f"claude --resume {session_id}" if session_id else "claude"
    return _result("claude", session_id, cwd, command)


def resolve_codex(proc: procs.Proc, cwd: str | None) -> dict:
    """Codex has no pid pointer, but holds its rollout transcript open.

    That open path also identifies the account: rollouts live at
    <CODEX_HOME>/sessions/<yyyy>/<mm>/<dd>/rollout-<ts>-<uuid>.jsonl, so the
    prefix before /sessions/ is the CODEX_HOME. Reading the process environment
    would be the obvious route but macOS does not expose it -- `ps eww` returns
    the command line only.
    """
    session_id = None
    codex_home = None
    for path in procs.open_files(proc.pid):
        if "/sessions/" in path and path.endswith(".jsonl"):
            match = UUID_RE.search(os.path.basename(path))
            if match:
                session_id = match.group(0)
                codex_home = path.split("/sessions/")[0]
                break
    if not session_id:
        match = UUID_RE.search(proc.command)
        if match:
            session_id = match.group(0)

    if session_id and not UUID_RE.fullmatch(session_id):
        session_id = None
    # Which command selects a given account is installation-specific.
    binary = config.codex_command(codex_home)
    command = f"{binary} resume {session_id}" if session_id else binary
    return _result("codex", session_id, cwd, command)


def resolve_grok(proc: procs.Proc, cwd: str | None) -> dict:
    """Grok stores sessions under ~/.grok/sessions/<urlencoded-cwd>/<uuid>/."""
    session_id = None
    # Trailing separator so a sibling like ~/.grok/sessions-archive cannot match.
    root = str(GROK_SESSIONS) + "/"
    for path in procs.open_files(proc.pid):
        if not path.startswith(root):
            continue
        rest = path[len(root) :].strip("/").split("/")
        if len(rest) >= 2:
            match = UUID_RE.fullmatch(rest[1])
            if match:
                session_id = rest[1]
                if not cwd:
                    cwd = urllib.parse.unquote(rest[0])
                break
    if not session_id:
        match = UUID_RE.search(proc.command)
        if match:
            session_id = match.group(0)
    if session_id and not UUID_RE.fullmatch(session_id):
        session_id = None
    command = f"grok --resume {session_id}" if session_id else "grok"
    return _result("grok", session_id, cwd, command)


def resolve_command(proc: procs.Proc, cwd: str | None) -> dict:
    """ssh, and anything else long-running: replay the command verbatim.

    A recorded command line is typed into a live shell and submitted, so it is
    only replayed when it is a plain argument list. Anything carrying shell
    syntax is recorded but downgraded to a bare `cd`, because argv is data an
    arbitrary process controls and this runs unattended, long after capture.
    """
    kind = "ssh" if proc.argv0 == "ssh" else "command"
    if set(proc.command) & SHELL_METACHARACTERS:
        result = _result(kind, None, cwd, "")
        note = (
            "command contains shell syntax and was not replayed; "
            f"run it yourself if you trust it: {proc.command}"
        )
        # Keep any cwd warning too rather than replacing it.
        result["warning"] = f"{result['warning']}; {note}" if result["warning"] else note
        return result
    return _result(kind, None, cwd, proc.command)


def resolve_shell(proc: procs.Proc, cwd: str | None) -> dict:
    """A tab sitting at a prompt: restore its directory, run nothing."""
    return {"kind": "shell", "session_id": None, "cwd": cwd, "restore": _cd(cwd), "warning": None}


AGENT_RESOLVERS = {
    "claude": resolve_claude,
    "codex": resolve_codex,
    "grok": resolve_grok,
}

# The one place that defines what counts as an agent. Everything else derives
# from this: undercounting here would let an empty capture overwrite a good
# snapshot, and a missing entry would let restore clobber a live session.
AGENT_KINDS = frozenset(AGENT_RESOLVERS)
ALL_KINDS = (*tuple(AGENT_RESOLVERS), "ssh", "command", "shell", "empty")


def resolve(proc: procs.Proc | None, cwd: str | None) -> dict:
    if proc is None:
        return {"kind": "empty", "session_id": None, "cwd": cwd, "restore": "", "warning": None}
    resolver = AGENT_RESOLVERS.get(proc.argv0)
    if resolver:
        return resolver(proc, cwd)
    if proc.is_shell:
        return resolve_shell(proc, cwd)
    return resolve_command(proc, cwd)
