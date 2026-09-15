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

# A capture keeping at least this share of the last good count is an ordinary
# change (you closed a tab); below it looks like a collapse.
COLLAPSE_RATIO = 0.67
# Consecutive collapsed captures before we believe it. At 30s each, a shutdown
# is over long before this, so it cannot overwrite a good snapshot.
COLLAPSE_STREAK = 3
STREAK_FILE = STATE_DIR / "collapse-streak"

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


def _harden(directory: Path) -> None:
    """Create a directory owner-only, and fix it if it already exists.

    `mkdir(mode=...)` is applied only at creation time and silently does
    nothing when the directory is already there, so an install created before
    this rule existed would stay world-readable forever. Snapshots hold every
    tab's command line, which can include credentials on another tab's argv.
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if directory.stat().st_mode & 0o777 != 0o700:
            directory.chmod(0o700)
    except OSError:
        pass  # a permission problem here must never block a capture


def _read_streak() -> int:
    try:
        return int(STREAK_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def _write_streak(value: int) -> None:
    try:
        STREAK_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        STREAK_FILE.write_text(str(value))
    except OSError:
        pass  # a streak we cannot record must never block a capture


def _write_atomic(path: Path, snap: dict) -> None:
    _harden(path.parent)
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

    Always archived to history. Promotion to `snapshot.json` is guarded twice,
    because the moment a snapshot matters most is a reboot -- and during
    shutdown the tabs die one by one:

    - a capture with no agent sessions is never promoted;
    - a capture that has collapsed relative to the last good one is not
      promoted until it repeats, so a half-dead shutdown state cannot overwrite
      a full one. A genuine drop is accepted after COLLAPSE_STREAK captures; a
      shutdown never lasts that long.
    """
    # Microseconds: two captures in the same second must not collide.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    archive = HISTORY_DIR / f"{stamp}.json"
    _write_atomic(archive, snap)
    _prune_history()

    count = agent_tab_count(snap)
    if count == 0:
        # Restart the streak: collapses either side of a gap are not
        # consecutive. Without this, a shutdown's collapses carry across the
        # zero captures while iTerm is closed, and the first partial capture
        # after a reboot is promoted over the full snapshot.
        _write_streak(0)
        return None, archive

    previous = 0
    if LATEST.exists():
        try:
            previous = agent_tab_count(json.loads(LATEST.read_text()))
        except (json.JSONDecodeError, OSError):
            previous = 0

    if previous and count < previous * COLLAPSE_RATIO:
        streak = _read_streak() + 1
        if streak < COLLAPSE_STREAK:
            _write_streak(streak)
            return None, archive

    _write_streak(0)
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
