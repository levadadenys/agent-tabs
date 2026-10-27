"""A Codex tab must resume under the account it was running as.

Exercised against a real process and a real lsof call, because the mechanism
being tested is exactly the OS-level lookup -- a mocked open-file list would
have kept passing when the original CODEX_HOME-from-environ approach silently
returned nothing on macOS.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import adapters, config, procs

UUID = "01a08085-3793-7381-aee8-61af378efc44"


def _rollout(home: Path) -> Path:
    path = home / "sessions" / "2026" / "09" / "09" / f"rollout-2026-09-09T00-00-00-{UUID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"session_meta"}\n')
    return path


def _holder(path: Path) -> subprocess.Popen:
    """A process that keeps `path` open, so lsof can see it."""
    proc = subprocess.Popen(
        # Bind the handle: an unassigned open() is collected and closed at once.
        [sys.executable, "-c", f"f = open({str(path)!r}); import time; time.sleep(30)"]
    )
    for _ in range(50):
        if any(Path(p).resolve() == path for p in procs.open_files(proc.pid)):
            return proc
        time.sleep(0.1)
    proc.kill()
    raise AssertionError("holder process never showed the file as open")


def _resolve(home: Path, profiles: dict) -> dict:
    with tempfile.TemporaryDirectory() as state:
        cfg = Path(state) / "config.json"
        cfg.write_text(json.dumps({"codex_profiles": profiles}))
        original = config.CONFIG_PATH
        config.CONFIG_PATH = cfg
        holder = _holder(_rollout(home))
        try:
            proc = procs.Proc(holder.pid, 1, "ttys999", "codex")
            return adapters.resolve_codex(proc, "/work")
        finally:
            holder.kill()
            holder.wait()
            config.CONFIG_PATH = original


def test_alternate_account_resumes_with_its_own_command():
    with tempfile.TemporaryDirectory() as tmp:
        home = (Path(tmp).resolve()) / ".codex-alt"
        result = _resolve(home, {str(home): "codex alt"})
    assert result["session_id"] == UUID
    assert result["restore"] == f"cd /work && codex alt resume {UUID}", result["restore"]


def test_default_account_resumes_with_plain_codex():
    with tempfile.TemporaryDirectory() as tmp:
        home = (Path(tmp).resolve()) / ".codex"
        # A profile map that does not mention this home must not match it.
        result = _resolve(home, {"/somewhere/else": "codex other"})
    assert result["restore"] == f"cd /work && codex resume {UUID}", result["restore"]


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    raise SystemExit(1 if failures else 0)
