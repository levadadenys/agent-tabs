"""Install the snapshot recorder and a convenience symlink."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN_DIR = Path.home() / ".local" / "bin"
SKILL_SRC = REPO / "skills" / "resume-tabs"
SKILL_DST = Path.home() / ".claude" / "skills" / "resume-tabs"

LABEL = "com.agent-tabs.recorder"
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT_PLIST = AGENT_DIR / f"{LABEL}.plist"
LOG = Path.home() / ".agent-tabs" / "recorder.log"
INTERVAL_SECONDS = 30

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
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
         "--git-common-dir"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).parent
    return REPO


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, check=False
    )


def _install_agent() -> list[str]:
    lines: list[str] = []
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
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        # A capture taken while iTerm is closed finds nothing and is not
        # promoted, so routine failures here are expected and must not spam.
        "StandardErrorPath": str(LOG),
    }
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    with AGENT_PLIST.open("wb") as handle:
        plistlib.dump(job, handle)
    lines.append(f"wrote {AGENT_PLIST}")

    domain = f"gui/{os.getuid()}"
    # Replace any previous copy, so re-running install picks up a new path.
    _launchctl("bootout", f"{domain}/{LABEL}")
    result = _launchctl("bootstrap", domain, str(AGENT_PLIST))
    if result.returncode != 0:
        # Older macOS, or a domain that refuses bootstrap.
        result = _launchctl("load", "-w", str(AGENT_PLIST))
    if result.returncode == 0:
        lines.append(f"recorder loaded, every {INTERVAL_SECONDS}s")
    else:
        lines.append(
            f"FAILED to load the recorder: {(result.stderr or result.stdout).strip()}"
        )
    return lines


def run(force: bool = False) -> int:
    lines = [
        _link(REPO / "bin" / "agent-tabs", BIN_DIR / "agent-tabs", force),
        _link(SKILL_SRC, SKILL_DST, force),
    ]
    lines += _install_agent()

    if LEGACY_AUTOLAUNCH.is_symlink() or LEGACY_AUTOLAUNCH.exists():
        LEGACY_AUTOLAUNCH.unlink()
        lines.append(f"removed the old iTerm AutoLaunch recorder: {LEGACY_AUTOLAUNCH}")

    for line in lines:
        print(line)
    print(
        "\nNo iTerm restart needed. Verify with:  agent-tabs list\n"
        f"Stop it with:  launchctl bootout gui/{os.getuid()}/{LABEL}"
    )
    return 0
