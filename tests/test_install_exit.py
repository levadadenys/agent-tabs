"""A failed recorder install must not exit 0.

The recorder not running is the entire failure this tool exists to prevent, and
it is what actually happened for six days. A bootstrap script checking $? must
not read a dead recorder as success.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import install  # noqa: E402
from runner import run  # noqa: E402


def with_launchctl(returncode: int, err: str = ""):
    original = install._launchctl
    install._launchctl = lambda *a: subprocess.CompletedProcess(  # type: ignore[assignment]
        a, returncode, "", err
    )
    return original


def test_failed_load_exits_non_zero():
    original = with_launchctl(1, "permission denied")
    try:
        assert install.run() == 1, "a dead recorder must not report success"
    finally:
        install._launchctl = original  # type: ignore[assignment]


def test_successful_load_exits_zero():
    original = with_launchctl(0)
    try:
        assert install.run() == 0
    finally:
        install._launchctl = original  # type: ignore[assignment]


def test_install_agent_reports_whether_it_loaded():
    original = with_launchctl(1, "nope")
    try:
        _, loaded = install._install_agent()
        assert loaded is False
    finally:
        install._launchctl = original  # type: ignore[assignment]

    original = with_launchctl(0)
    try:
        _, loaded = install._install_agent()
        assert loaded is True
    finally:
        install._launchctl = original  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(run(globals()))
