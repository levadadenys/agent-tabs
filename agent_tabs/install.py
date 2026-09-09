"""Install the iTerm2 AutoLaunch recorder and a convenience symlink."""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUTOLAUNCH_DIR = (
    Path.home() / "Library" / "Application Support" / "iTerm2" / "Scripts" / "AutoLaunch"
)
RECORDER_SRC = REPO / "iterm2" / "AutoLaunch" / "agent_tabs_recorder.py"
BIN_DIR = Path.home() / ".local" / "bin"
SKILL_SRC = REPO / "skills" / "resume-tabs"
SKILL_DST = Path.home() / ".claude" / "skills" / "resume-tabs"


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


def run(force: bool = False) -> int:
    for line in (
        _link(RECORDER_SRC, AUTOLAUNCH_DIR / RECORDER_SRC.name, force),
        _link(REPO / "bin" / "agent-tabs", BIN_DIR / "agent-tabs", force),
        _link(SKILL_SRC, SKILL_DST, force),
    ):
        print(line)
    print(
        "\nRestart iTerm2 (or Scripts > AutoLaunch > agent_tabs_recorder.py) to start recording.\n"
        "Verify with: agent-tabs list"
    )
    return 0
