"""Capture the live window -> tab -> agent-session map, and persist it."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import adapters, iterm, procs

STATE_DIR = Path(os.environ.get("AGENT_TABS_HOME", Path.home() / ".agent-tabs"))
LATEST = STATE_DIR / "snapshot.json"
HISTORY_DIR = STATE_DIR / "history"
HISTORY_KEEP = 60

# Both derive from adapters, which owns the definition of "is an agent".
AGENT_KINDS = adapters.AGENT_KINDS
AGENT_NAMES = adapters.AGENT_KINDS
SCHEMA_VERSION = 1


def build() -> dict:
    tabs = iterm.enumerate_tabs()
    table = procs.process_table()
    grouped = procs.by_tty(table)

    # Resolve the interesting process per pane first, then batch the cwd lookup:
    # one lsof call instead of one per tab.
    resolved: dict[str, procs.Proc | None] = {}
    for tab in tabs:
        tty = procs.normalise_tty(tab["tty"])
        resolved[tty] = procs.foreground_proc(grouped.get(tty, []), AGENT_NAMES)
    cwds = procs.cwd_of([p.pid for p in resolved.values() if p is not None])

    windows: dict[int, dict] = {}
    for tab in tabs:
        tty = procs.normalise_tty(tab["tty"])
        proc = resolved.get(tty)
        cwd = cwds.get(proc.pid) if proc else None
        info = adapters.resolve(proc, cwd)
        window = windows.setdefault(
            tab["window_index"],
            {"window_index": tab["window_index"], "window_id": tab["window_id"], "tabs": []},
        )
        window["tabs"].append(
            {
                "tab_index": tab["tab_index"],
                "pane_index": tab["pane_index"],
                "tty": tab["tty"],
                "title": tab["title"],
                "pid": proc.pid if proc else None,
                "command": proc.command if proc else None,
                **info,
            }
        )

    ordered = [windows[k] for k in sorted(windows)]
    for window in ordered:
        window["tabs"].sort(key=lambda t: (t["tab_index"], t["pane_index"]))
    return {
        "version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "windows": ordered,
    }


def agent_tab_count(snap: dict) -> int:
    return sum(
        1
        for window in snap.get("windows", [])
        for tab in window.get("tabs", [])
        if tab.get("kind") in AGENT_KINDS
    )


def _write_atomic(path: Path, snap: dict) -> None:
    # Snapshots hold every tab's command line, which can include credentials
    # passed on another tab's argv; keep the directory owner-only too.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(snap, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _prune_history() -> None:
    files = sorted(HISTORY_DIR.glob("*.json"))
    for stale in files[:-HISTORY_KEEP]:
        stale.unlink(missing_ok=True)


def save(snap: dict) -> tuple[Path | None, Path]:
    """Persist a snapshot.

    Always archived to history. Only promoted to `snapshot.json` when it holds
    at least one agent session, so a capture taken while iTerm is shutting down
    cannot overwrite a good snapshot with an empty one.
    """
    # Microseconds: two captures in the same second must not collide.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    archive = HISTORY_DIR / f"{stamp}.json"
    _write_atomic(archive, snap)
    _prune_history()

    if agent_tab_count(snap) == 0:
        return None, archive
    _write_atomic(LATEST, snap)
    return LATEST, archive


def load(path: Path | None = None) -> dict:
    target = path or LATEST
    if not target.exists():
        raise FileNotFoundError(
            f"No snapshot at {target}. Is the recorder installed? Run: agent-tabs install"
        )
    return json.loads(target.read_text())


def history() -> list[Path]:
    if not HISTORY_DIR.exists():
        return []
    return sorted(HISTORY_DIR.glob("*.json"), reverse=True)
