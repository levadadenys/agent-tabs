"""Optional machine-local config, read from ~/.agent-tabs/config.json.

Anything installation-specific belongs here rather than in the source tree, so
the repository stays free of personal paths and account names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("AGENT_TABS_HOME", Path.home() / ".agent-tabs"))
CONFIG_PATH = STATE_DIR / "config.json"

DEFAULTS: dict = {
    # Maps an absolute CODEX_HOME to the command that selects that Codex
    # account, for people who run more than one. Example:
    #   {"/Users/you/.codex-work": "codex work"}
    "codex_profiles": {},
}

# How often the recorder captures, in seconds. Applied when `install` writes
# the launchd job; the snapshot code never reads it (see snapshot.HISTORY_MIN).
INTERVAL_DEFAULT = 30
# Below 10 s a ~2 s capture is a real share of the machine; above 300 s the
# sessions a crash can lose stop being a rounding error.
INTERVAL_MIN = 10
INTERVAL_MAX = 300


def load() -> dict:
    config = dict(DEFAULTS)
    if not CONFIG_PATH.exists():
        return config
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (ValueError, OSError):
        # A broken config must not stop a recovery; fall back to defaults.
        # ValueError covers bad JSON and bad UTF-8 (UnicodeDecodeError) alike.
        return config
    if isinstance(data, dict):
        config.update(data)
    return config


def codex_command(codex_home: str | None) -> str:
    """The command that resumes Codex under a given CODEX_HOME.

    Called for every Codex tab during a capture, so a malformed entry must
    fall back to plain `codex` rather than raise.
    """
    if not codex_home:
        return "codex"
    profiles = load().get("codex_profiles")
    if not isinstance(profiles, dict):
        return "codex"
    command = profiles.get(codex_home.rstrip("/"))
    return command if isinstance(command, str) and command.strip() else "codex"


def interval_seconds() -> tuple[int, str | None]:
    """The recorder interval, and a note when the configured value was not used as is.

    One rule: a JSON integer is clamped to INTERVAL_MIN..INTERVAL_MAX; anything
    else (a bool, a float, a string, null, a list, an object) falls back to the
    default. A missing key gives the default silently. Never raises: install
    must not be refused over a setting.
    """
    config = load()
    if "interval_seconds" not in config:
        return INTERVAL_DEFAULT, None
    value = config["interval_seconds"]
    if isinstance(value, bool) or not isinstance(value, int):
        return INTERVAL_DEFAULT, (
            f"interval_seconds {value!r} is not a whole number of seconds; using {INTERVAL_DEFAULT}"
        )
    clamped = min(max(value, INTERVAL_MIN), INTERVAL_MAX)
    if clamped != value:
        return clamped, (
            f"interval_seconds {value} is outside {INTERVAL_MIN}-{INTERVAL_MAX}; using {clamped}"
        )
    return value, None
