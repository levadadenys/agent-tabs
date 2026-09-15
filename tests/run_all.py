#!/usr/bin/env python3
"""Run every test module and report a single verdict."""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    failures, total = [], 0
    for path in sorted(HERE.glob("test_*.py")):
        # -B disables bytecode caching. Mutation testing edits a file, runs the
        # tests, then restores it; when the edit is the same byte length and
        # lands in the same second, Python's mtime+size cache check passes and
        # it silently runs the STALE mutant. Observed: source read
        # ENUMERATE_ATTEMPTS = 3 while Python had loaded 1.
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, "-B", str(path)],
            capture_output=True,
            text=True,
            env=env,
        )
        passed = result.stdout.count("PASS ")
        failed = result.stdout.count("FAIL ")
        total += passed + failed
        status = "ok" if result.returncode == 0 else "FAILED"
        print(f"{path.name:<32} {passed:>3} passed  {failed:>2} failed  {status}")
        if result.returncode != 0:
            failures.append((path.name, result.stdout, result.stderr))
    print(f"\n{total} tests, {len(failures)} module(s) failing")
    for name, out, err in failures:
        print(f"\n----- {name} -----\n{out}\n{err}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
