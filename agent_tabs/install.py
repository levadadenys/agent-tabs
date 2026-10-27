"""Install the snapshot recorder and a convenience symlink."""

from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config

REPO = Path(__file__).resolve().parent.parent
BIN_DIR = Path.home() / ".local" / "bin"
SKILL_SRC = REPO / "skills" / "resume-tabs"
SKILL_DST = Path.home() / ".claude" / "skills" / "resume-tabs"

LABEL = "com.agent-tabs.recorder"
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT_PLIST = AGENT_DIR / f"{LABEL}.plist"
LOG = Path.home() / ".agent-tabs" / "recorder.log"

# Left behind by versions that ran the recorder from iTerm's AutoLaunch folder.
# That needed iTerm's Python runtime, which is not installed by default, so it
# could fail silently -- the reason this moved to launchd.
LEGACY_AUTOLAUNCH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "iTerm2"
    / "Scripts"
    / "AutoLaunch"
    / "agent_tabs_recorder.py"
)


def _stop_legacy_processes() -> list[str]:
    """Kill an AutoLaunch recorder iTerm already started.

    Unlinking the script does not stop a running instance -- its loop lives
    until iTerm exits. Left alone it would keep capturing on its own timer alongside
    the launchd job, and the two would race on snapshot.json.
    """
    try:
        listing = subprocess.run(
            ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return []

    target = str(LEGACY_AUTOLAUNCH)
    stopped = []
    # Newlines only, as in procs.process_table: ps passes U+2028 in argv
    # through, so splitlines() would let any process forge a line naming
    # another pid alongside the target path, and that pid would be signalled.
    for line in listing.split("\n"):
        # Match the full path, not the basename: an unrelated process whose
        # command line merely mentions the script name must not be signalled.
        if target not in line:
            continue
        pid_text = line.split(None, 1)[0]
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            stopped.append(f"could not stop the old recorder (pid {pid}): {exc}")
            continue
        # Wait for it to actually go: until it does, it is still capturing on
        # its own timer alongside the new job, and the two race on snapshot.json.
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            stopped.append(f"old recorder (pid {pid}) did not exit; it may still be capturing")
            continue
        stopped.append(f"stopped the old recorder still running as pid {pid}")
    return stopped


def _link(src: Path, dst: Path, force: bool) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() and os.readlink(dst) == str(src):
            return f"already linked: {dst}"
        if not force:
            return f"SKIP (exists, use --force): {dst}"
        if dst.is_dir() and not dst.is_symlink():
            return f"SKIP (real directory, remove it yourself): {dst}"
        dst.unlink()
    dst.symlink_to(src)
    return f"linked {dst} -> {src}"


def main_checkout() -> Path:
    """The primary working tree, even when install runs from a git worktree.

    A launchd plist records an absolute path. Pointing it at a worktree copy
    would leave the recorder silently dead the moment that worktree is removed,
    which is the failure mode this whole tool exists to avoid.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None

    if result is None or result.returncode != 0 or not result.stdout.strip():
        # A worktree's .git is a FILE pointing at the real repo. Without git we
        # cannot resolve it, and returning REPO would bake in a path that
        # disappears with the worktree -- the very bug this resolver exists to
        # prevent. A plain checkout or zip download has no such ambiguity.
        if (REPO / ".git").is_file():
            raise RuntimeError(
                f"{REPO} looks like a git worktree but git is unavailable, so the "
                "main checkout cannot be resolved. Install from the main checkout."
            )
        return REPO
    return Path(result.stdout.strip()).parent


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _install_agent() -> tuple[list[str], bool]:
    """Returns its output lines and whether the recorder is actually loaded."""
    lines: list[str] = []
    interval, note = config.interval_seconds()
    if note:
        lines.append(note)
    cli = main_checkout() / "bin" / "agent-tabs"
    LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Built with plistlib rather than string substitution: a path containing
    # &, < or > would otherwise produce malformed XML and a plist launchd
    # refuses to load.
    #
    # The interpreter is the absolute one running this installer. launchd gives
    # a job a minimal PATH, so a `#!/usr/bin/env python3` shebang could resolve
    # to a different python than the one installing, or to none at all.
    job = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(cli), "snapshot", "--quiet"],
        "StartInterval": interval,
        "RunAtLoad": True,
        # A capture taken while iTerm is closed finds nothing and is not
        # promoted, so routine failures here are expected and must not spam.
        "StandardErrorPath": str(LOG),
    }
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    previous = AGENT_PLIST.read_bytes() if AGENT_PLIST.exists() else None
    with AGENT_PLIST.open("wb") as handle:
        plistlib.dump(job, handle)
    lines.append(f"wrote {AGENT_PLIST}")

    domain = f"gui/{os.getuid()}"
    # Try to load without booting out first: tearing down a healthy job and
    # then failing to replace it would leave no recorder at all, which is worse
    # than not upgrading. Only replace once the new plist is known good.
    result = _launchctl("bootstrap", domain, str(AGENT_PLIST))
    if result.returncode != 0:
        # Most likely already loaded; replace it, then fall back to load -w.
        # Only tear down when we can put something back: if there was no prior
        # plist to restore, an existing healthy job is the only recorder there
        # is, and losing it is worse than not upgrading.
        if previous is None and _launchctl("print", f"{domain}/{LABEL}").returncode == 0:
            lines.append(
                "a recorder is already loaded and no previous definition was "
                "saved, so it was left alone rather than risk removing it. It "
                f"keeps its old interval, not {interval}s; run install again to apply it"
            )
            return lines, True
        _launchctl("bootout", f"{domain}/{LABEL}")
        result = _launchctl("bootstrap", domain, str(AGENT_PLIST))
        if result.returncode != 0:
            result = _launchctl("load", "-w", str(AGENT_PLIST))
        if result.returncode != 0 and previous is not None:
            # Put the working job back rather than leaving nothing running.
            AGENT_PLIST.write_bytes(previous)
            restore = _launchctl("bootstrap", domain, str(AGENT_PLIST))
            lines.append(
                "restored the previous recorder"
                if restore.returncode == 0
                else "could not restore the previous recorder"
            )
    if result.returncode == 0:
        lines.append(f"recorder loaded, every {interval}s")
        return lines, True
    lines.append(f"FAILED to load the recorder: {(result.stderr or result.stdout).strip()}")
    return lines, False


def run(force: bool = False) -> int:
    # Link the main checkout, never a worktree: a worktree is deleted when its
    # branch merges, and the symlink would dangle. Same reasoning as the plist.
    root = main_checkout()
    lines = [
        _link(root / "bin" / "agent-tabs", BIN_DIR / "agent-tabs", force),
        _link(root / "skills" / "resume-tabs", SKILL_DST, force),
    ]
    agent_lines, loaded = _install_agent()
    lines += agent_lines

    # The legacy entry is only worth keeping if it could still run. It is
    # normally a symlink to a script this version deletes, so after an upgrade
    # it dangles and preserving it "as a fallback" would be theatre -- it would
    # simply leave a broken entry in iTerm's AutoLaunch folder.
    if LEGACY_AUTOLAUNCH.is_symlink() or LEGACY_AUTOLAUNCH.exists():
        dangling = not LEGACY_AUTOLAUNCH.resolve().exists()
        if loaded or dangling:
            LEGACY_AUTOLAUNCH.unlink()
            lines.append(
                f"removed the old iTerm AutoLaunch recorder: {LEGACY_AUTOLAUNCH}"
                + (" (it was already dangling)" if dangling and not loaded else "")
            )
        else:
            lines.append(
                f"kept the old AutoLaunch recorder at {LEGACY_AUTOLAUNCH}: "
                "the replacement did not load and that one still resolves"
            )
    if loaded:
        lines += _stop_legacy_processes()

    for line in lines:
        print(line)

    if not loaded:
        # The recorder not running is the whole failure this tool exists to
        # prevent, so it must not exit 0 -- a bootstrap script checking $? would
        # otherwise treat a dead recorder as a successful install.
        print(
            "\nThe recorder is NOT running, so nothing is being captured.\n"
            f"Retry with:  launchctl bootstrap gui/{os.getuid()} {AGENT_PLIST}",
            file=sys.stderr,
        )
        return 1

    print(
        "\nNo iTerm restart needed. Verify with:  agent-tabs list\n"
        f"Stop it with:  launchctl bootout gui/{os.getuid()}/{LABEL}"
    )
    return 0
