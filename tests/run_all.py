#!/usr/bin/env python3
"""Run every test module and report a single verdict."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    failures, total = [], 0
    for path in sorted(HERE.glob("test_*.py")):
        # Each run gets a private, empty bytecode cache. Mutation testing edits
        # a file, runs the tests, then restores it; when the edit is the same
        # byte length and the mtime matches, Python's mtime+size check passes
        # and it loads the STALE mutant. Observed: source read
        # ENUMERATE_ATTEMPTS = 3 while Python had loaded 1.
        #
        # -B alone does NOT fix this -- it only stops *writing* .pyc files,
        # while import still reads an existing valid one. Verified: with -B the
        # stale value is still loaded. A fresh cache prefix is what works.
        with tempfile.TemporaryDirectory() as cache:
            env = {**os.environ, "PYTHONPYCACHEPREFIX": cache}
            result = subprocess.run(
                [sys.executable, str(path)],
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
