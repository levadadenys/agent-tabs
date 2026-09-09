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


def load() -> dict:
    config = dict(DEFAULTS)
    if not CONFIG_PATH.exists():
        return config
    try:
        config.update(json.loads(CONFIG_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        # A broken config must not stop a recovery; fall back to defaults.
        pass
    return config


def codex_command(codex_home: str | None) -> str:
    """The command that resumes Codex under a given CODEX_HOME."""
    if not codex_home:
        return "codex"
    profiles = load().get("codex_profiles") or {}
    return profiles.get(codex_home.rstrip("/"), "codex")
