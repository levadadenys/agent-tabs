"""Capture the live window -> tab -> agent-session map, and persist it."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import adapters, iterm, procs

STATE_DIR = Path(os.environ.get("AGENT_TABS_HOME", Path.home() / ".agent-tabs"))
LATEST = STATE_DIR / "snapshot.json"
# The last snapshot captured before the most recent boot, pinned on the first
# promotion after that boot. History alone cannot protect it: it keeps only the
# last HISTORY_KEEP captures, and an idle iTerm after a reboot fills history
# with empty captures within about half an hour.
PRE_BOOT = STATE_DIR / "pre-boot.json"
HISTORY_DIR = STATE_DIR / "history"
HISTORY_KEEP = 60

# A capture keeping at least this share of the last good count is an ordinary
# change (you closed a tab); below it looks like a collapse.
# Coarse at low session counts: with two tabs, closing one is a 50% drop and
# reads as a collapse, delaying the snapshot by the settle window. Safe
# direction, but the two cases are not distinguishable by ratio alone.
COLLAPSE_RATIO = 0.67
# Consecutive collapsed captures before we believe it. At 30s each, a shutdown
# is over long before this, so it cannot overwrite a good snapshot.
COLLAPSE_STREAK = 3
# ...and spanning at least this long. Counting captures is not the same as the
# collapse persisting: several captures can land at once (a manual snapshot
# racing the launchd tick), which would satisfy a pure count instantly.
COLLAPSE_SETTLE_SECONDS: float = 45

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


def boot_time() -> datetime | None:
    """When the machine last booted, or None if it cannot be determined."""
    try:
        raw = subprocess.run(
            ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return None
    match = re.search(r"sec = (\d+)", raw)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)), timezone.utc)


def captured_at(snap: dict) -> datetime | None:
    try:
        when = datetime.fromisoformat(snap["captured_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _read(path: Path) -> dict | None:
    """A stored snapshot, or None if it is missing, unreadable or not one.

    ValueError covers both bad JSON and bad UTF-8. These readers run inside
    `list` and `restore`, and on every capture: a damaged file must cost a
    feature, never crash them.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _agents(snap: dict) -> int:
    """agent_tab_count, treating a malformed snapshot as holding none."""
    try:
        return agent_tab_count(snap)
    except (AttributeError, TypeError):
        return 0


def _pinned_for(booted: datetime) -> bool:
    pinned = _read(PRE_BOOT)
    return pinned is not None and pinned.get("pinned_for_boot") == booted.isoformat()


def _fullest_pre_boot_archive(booted: datetime) -> dict | None:
    """The pre-reboot capture with the most agent sessions still in history.

    Not simply the newest: during shutdown, tabs die one at a time, so the last
    captures before a reboot are half-dead ones -- archived, never promoted.
    Picking the newest would pin 3 sessions instead of 22.
    """
    best, best_count = None, 0
    for archive in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        snap = _read(archive)
        if snap is None:
            continue
        # The capture's own time, as everywhere else in the pin logic; the
        # filename is only the save time, and the two must not disagree.
        when = captured_at(snap) or _archive_time(archive)
        if when is None or when >= booted:
            continue
        count = _agents(snap)
        if count > best_count:  # strict: on a tie the newer one, seen first, wins
            best, best_count = snap, count
    return best


def _pin_pre_boot(incoming: dict) -> None:
    """Keep the pre-reboot fleet recoverable once a post-reboot capture lands.

    Runs on promotion. Pins once per boot: the fuller of snapshot.json, if it
    still predates the boot, and the fullest pre-boot capture in history.
    Either alone can miss the fleet. snapshot.json may be unreadable, already
    replaced because an earlier pin failed, or a half-dead shutdown capture
    the collapse guard accepted because the shutdown dragged on; history keeps
    only about half an hour, so after a long idle spell before the reboot the
    fleet survives only in snapshot.json. On a tie snapshot.json wins: it is
    the capture the guard vetted. A failure here must never block a capture.
    """
    booted = boot_time()
    if booted is None:
        return
    after = captured_at(incoming)
    if after is None or after < booted:
        return  # still before this boot: nothing to protect yet
    if _pinned_for(booted):
        return  # already pinned for this boot

    candidates: list[dict] = []
    current = _read(LATEST)
    taken = captured_at(current) if current is not None else None
    if current is not None and taken is not None and taken < booted:
        candidates.append(current)
    archived = _fullest_pre_boot_archive(booted)
    if archived is not None:
        candidates.append(archived)
    if not candidates:
        return
    candidate = max(candidates, key=_agents)  # first, snapshot.json, on a tie
    # Record which boot this pin belongs to. A pre-boot.json left from an
    # earlier reboot must never be offered as "the last one before the
    # reboot" -- it would point a restore at an ancient fleet.
    try:
        _write_atomic(PRE_BOOT, {**candidate, "pinned_for_boot": booted.isoformat()})
    except OSError:
        pass


def pre_boot_warning(snap: dict) -> str | None:
    """Explain when the snapshot in use postdates a reboot and lost sessions.

    After a reboot with no restored windows, starting even one session before
    restoring lets that small capture replace the full one about a minute
    later. The full one is pinned in pre-boot.json; say so rather than let a
    restore quietly bring back the wrong fleet.
    """
    booted = boot_time()
    taken = captured_at(snap)
    if booted is None or taken is None or taken < booted or not PRE_BOOT.exists():
        return None
    pinned = _read(PRE_BOOT)
    if pinned is None or pinned.get("pinned_for_boot") != booted.isoformat():
        return None  # unreadable, or left over from an earlier reboot
    now, before = _agents(snap), _agents(pinned)
    if before <= now:
        return None
    # --create-missing: this fires for a reboot iTerm did not restore, where
    # --all alone pairs nothing and creates nothing. Windows that did come back
    # are still restored in place, so it is safe either way.
    return (
        f"This snapshot was captured after the last reboot and holds {now} "
        f"agent session(s). The last one before the reboot held {before}, "
        f"captured {pinned.get('captured_at', '?')}. To restore that instead:\n"
        f"  agent-tabs restore --all --create-missing -f {PRE_BOOT}"
    )


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


def _count_of(path: Path) -> int | None:
    """Agent sessions in a stored snapshot, or None if it cannot be read."""
    try:
        return agent_tab_count(json.loads(path.read_text()))
    # ValueError, not just JSONDecodeError: bad UTF-8 raises UnicodeDecodeError,
    # and this runs on every capture.
    except (ValueError, OSError, TypeError, AttributeError):
        return None


def _last_good_count(exclude: Path | None = None) -> int | None:
    """Sessions in the snapshot a recovery would currently use.

    Falls back to the newest readable archive if `snapshot.json` cannot be
    parsed: treating an unreadable file as "no history" would switch the
    collapse guard off exactly when the state is already suspect. The capture
    being judged is excluded, or it would compare against itself.
    """
    if LATEST.exists():
        count = _count_of(LATEST)
        if count is not None:
            return count
    for archive in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        if exclude is not None and archive == exclude:
            continue
        count = _count_of(archive)
        if count is not None:
            return count
    return None


def _archive_time(path: Path) -> datetime | None:
    """Capture time from the archive filename, which save() always controls."""
    try:
        return datetime.strptime(path.stem, "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _recent_entries(limit: int) -> list[tuple[int, datetime]] | None:
    """Counts from the newest `limit` archives, or None if any is unreadable.

    History replaces a hand-kept streak counter: it is append-only, uniquely
    named, and written before this decision, so there is no second piece of
    mutable state to corrupt, race, or wedge.
    """
    archives = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    entries = []
    for archive in archives:
        count = _count_of(archive)
        when = _archive_time(archive)
        if count is None or when is None:
            return None
        entries.append((count, when))
    return entries


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
    # Harden the state directory itself, not only history/: creating history/
    # makes its parent with the default umask, and a capture with no agent
    # sessions returns before snapshot.json would harden it.
    _harden(STATE_DIR)
    _write_atomic(archive, snap)
    _prune_history()

    count = agent_tab_count(snap)
    if count == 0:
        return None, archive

    previous = _last_good_count(exclude=archive)
    if previous and count < previous * COLLAPSE_RATIO:
        floor = previous * COLLAPSE_RATIO
        recent = _recent_entries(COLLAPSE_STREAK)
        # Promote only once the collapse has persisted, in both captures and
        # wall-clock time. A zero capture or a recovery inside the window
        # breaks the run by itself, so gaps and blips need no bookkeeping.
        persistent = False
        if recent is not None and len(recent) >= COLLAPSE_STREAK:
            if all(0 < c < floor for c, _ in recent):
                span = (recent[0][1] - recent[-1][1]).total_seconds()
                persistent = span >= COLLAPSE_SETTLE_SECONDS
        if not persistent:
            return None, archive

    _pin_pre_boot(snap)
    _write_atomic(LATEST, snap)
    return LATEST, archive


def load(path: Path | None = None) -> dict:
    target = path or LATEST
    if not target.exists():
        # An explicit -f path that does not exist is a typo or a rotated-out
        # history file, not a missing install -- say which.
        if path is not None:
            raise FileNotFoundError(
                f"No snapshot at {target}. Check the path; history files rotate "
                f"out after {HISTORY_KEEP} captures (ls {HISTORY_DIR})."
            )
        raise FileNotFoundError(
            f"No snapshot at {target}. Is the recorder installed? Run: agent-tabs install"
        )
    return json.loads(target.read_text())


def history() -> list[Path]:
    if not HISTORY_DIR.exists():
        return []
    return sorted(HISTORY_DIR.glob("*.json"), reverse=True)
