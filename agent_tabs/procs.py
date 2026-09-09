"""Process-table helpers: map a tty to the interesting process running on it."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

# Shells and login wrappers are never the "interesting" process in a tab; they
# are what remains once an agent exits.
SHELL_NAMES = {"zsh", "bash", "sh", "fish", "-zsh", "-bash", "-sh", "login"}


@dataclass
class Proc:
    pid: int
    ppid: int
    tty: str  # normalised, e.g. "ttys003"
    command: str

    @property
    def argv0(self) -> str:
        first = self.command.split(" ", 1)[0]
        return os.path.basename(first).lstrip("-")

    @property
    def is_shell(self) -> bool:
        return self.argv0 in SHELL_NAMES or self.command.lstrip("-") in SHELL_NAMES


@dataclass
class TabProcs:
    tty: str
    procs: list[Proc] = field(default_factory=list)


def normalise_tty(tty: str) -> str:
    """iTerm reports '/dev/ttys003'; ps reports 'ttys003'."""
    return tty.replace("/dev/", "").strip()


def process_table() -> list[Proc]:
    out = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,tty=,command="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    procs: list[Proc] = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, tty, command = parts
        if tty == "??":
            continue
        try:
            procs.append(Proc(int(pid), int(ppid), normalise_tty(tty), command))
        except ValueError:
            continue
    return procs


def by_tty(procs: list[Proc]) -> dict[str, list[Proc]]:
    grouped: dict[str, list[Proc]] = {}
    for p in procs:
        grouped.setdefault(p.tty, []).append(p)
    return grouped


def foreground_proc(
    procs: list[Proc], priority: frozenset[str] = frozenset()
) -> Proc | None:
    """Pick the process that represents what the tab is *doing*.

    A name in `priority` always wins, because agents spawn helper children --
    codex runs a `node_repl` child that would otherwise look like the deepest
    process in the tab. Otherwise prefers the deepest non-shell descendant, and
    falls back to the shell itself, which is what an idle tab looks like.
    """
    if not procs:
        return None
    preferred = [p for p in procs if p.argv0 in priority]
    if preferred:
        return min(preferred, key=lambda p: p.pid)
    by_pid = {p.pid: p for p in procs}
    depth: dict[int, int] = {}

    def compute_depth(p: Proc, seen: frozenset[int] = frozenset()) -> int:
        if p.pid in depth:
            return depth[p.pid]
        # Guard against a pid-reuse cycle in the parent chain.
        if p.pid in seen:
            return 0
        parent = by_pid.get(p.ppid)
        d = 0 if parent is None else compute_depth(parent, seen | {p.pid}) + 1
        depth[p.pid] = d
        return d

    for p in procs:
        compute_depth(p)

    non_shell = [p for p in procs if not p.is_shell]
    pool = non_shell or procs
    return max(pool, key=lambda p: (depth[p.pid], p.pid))


def cwd_of(pids: list[int]) -> dict[int, str]:
    """Resolve each pid's working directory in a single lsof call."""
    if not pids:
        return {}
    out = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-Fpn"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    result: dict[int, str] = {}
    current: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                current = int(line[1:])
            except ValueError:
                current = None
        elif line.startswith("n") and current is not None:
            result.setdefault(current, line[1:])
    return result


def open_files(pid: int) -> list[str]:
    """Regular files a process currently holds open."""
    out = subprocess.run(
        ["lsof", "-a", "-d", "0-99", "-p", str(pid), "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [line[1:] for line in out.splitlines() if line.startswith("n")]


def environ_of(pid: int) -> dict[str, str]:
    """Best-effort read of a process's environment (macOS `ps eww`)."""
    out = subprocess.run(
        ["ps", "eww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    env: dict[str, str] = {}
    for token in out.split():
        if "=" in token:
            key, _, value = token.partition("=")
            if key.isupper() and key.replace("_", "").isalnum():
                env[key] = value
    return env
