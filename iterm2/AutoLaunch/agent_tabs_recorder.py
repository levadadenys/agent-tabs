#!/usr/bin/env python3
"""iTerm2 AutoLaunch recorder.

Runs for as long as iTerm2 is open and snapshots the window/tab map on a timer,
so that after a crash `agent-tabs restore` has something to replay. Kept
deliberately thin: it shells out to the `agent-tabs` CLI so that capture logic
lives in exactly one place and does not depend on iTerm's bundled Python.
"""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import iterm2  # provided by iTerm2's AutoLaunch runtime

# This file is symlinked into the iTerm2 AutoLaunch folder, so resolve through
# the symlink to find the repo it belongs to. Override with AGENT_TABS_CLI.
CLI = os.environ.get(
    "AGENT_TABS_CLI",
    str(Path(__file__).resolve().parent.parent.parent / "bin" / "agent-tabs"),
)
INTERVAL_SECONDS = 30
LOG = os.path.expanduser("~/.agent-tabs/recorder.log")


def log(message: str) -> None:
    """Best-effort logging. Never raises: this is called from inside the loop's
    own exception handler, where an error would escape `while True` and end the
    recorder permanently, silently, until the next iTerm restart."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:
        pass


async def main(connection):
    log(f"recorder started (every {INTERVAL_SECONDS}s)")
    while True:
        try:
            result = subprocess.run(
                [CLI, "snapshot", "--quiet"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log(f"snapshot failed rc={result.returncode}: {result.stderr.strip()[:200]}")
        except Exception as exc:  # keep the loop alive across any transient failure
            log(f"snapshot error: {exc!r}")
        try:
            await asyncio.sleep(INTERVAL_SECONDS)
        except asyncio.CancelledError:
            log("recorder cancelled")
            raise


iterm2.run_forever(main)
