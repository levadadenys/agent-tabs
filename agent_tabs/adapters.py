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

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

HOME = Path.home()
CLAUDE_SESSIONS = HOME / ".claude" / "sessions"
GROK_SESSIONS = HOME / ".grok" / "sessions"


def _cd(cwd: str | None) -> str:
    return f"cd {shlex.quote(cwd)}" if cwd else ""


def _join(cwd: str | None, command: str) -> str:
    prefix = _cd(cwd)
    return f"{prefix} && {command}" if prefix else command


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
    command = f"claude --resume {session_id}" if session_id else "claude"
    return {"kind": "claude", "session_id": session_id, "cwd": cwd,
            "restore": _join(cwd, command)}


def resolve_codex(proc: procs.Proc, cwd: str | None) -> dict:
    """Codex has no pid pointer, but holds its rollout transcript open."""
    session_id = None
    for path in procs.open_files(proc.pid):
        if "/sessions/" in path and path.endswith(".jsonl"):
            match = UUID_RE.search(os.path.basename(path))
            if match:
                session_id = match.group(0)
                break
    if not session_id:
        match = UUID_RE.search(proc.command)
        if match:
            session_id = match.group(0)

    # Multiple Codex accounts are selected by CODEX_HOME; the command that
    # picks a given one is installation-specific, so it comes from config.
    env = procs.environ_of(proc.pid)
    binary = config.codex_command(env.get("CODEX_HOME"))
    command = f"{binary} resume {session_id}" if session_id else binary
    return {"kind": "codex", "session_id": session_id, "cwd": cwd,
            "restore": _join(cwd, command)}


def resolve_grok(proc: procs.Proc, cwd: str | None) -> dict:
    """Grok stores sessions under ~/.grok/sessions/<urlencoded-cwd>/<uuid>/."""
    session_id = None
    root = str(GROK_SESSIONS)
    for path in procs.open_files(proc.pid):
        if not path.startswith(root):
            continue
        rest = path[len(root):].strip("/").split("/")
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
    command = f"grok --resume {session_id}" if session_id else "grok"
    return {"kind": "grok", "session_id": session_id, "cwd": cwd,
            "restore": _join(cwd, command)}


def resolve_command(proc: procs.Proc, cwd: str | None) -> dict:
    """ssh, and anything else long-running: replay the command verbatim."""
    kind = "ssh" if proc.argv0 == "ssh" else "command"
    return {"kind": kind, "session_id": None, "cwd": cwd,
            "restore": _join(cwd, proc.command)}


def resolve_shell(proc: procs.Proc, cwd: str | None) -> dict:
    """A tab sitting at a prompt: restore its directory, run nothing."""
    return {"kind": "shell", "session_id": None, "cwd": cwd, "restore": _cd(cwd)}


AGENT_RESOLVERS = {
    "claude": resolve_claude,
    "codex": resolve_codex,
    "grok": resolve_grok,
}


def resolve(proc: procs.Proc | None, cwd: str | None) -> dict:
    if proc is None:
        return {"kind": "empty", "session_id": None, "cwd": cwd, "restore": ""}
    resolver = AGENT_RESOLVERS.get(proc.argv0)
    if resolver:
        return resolver(proc, cwd)
    if proc.is_shell:
        return resolve_shell(proc, cwd)
    return resolve_command(proc, cwd)
