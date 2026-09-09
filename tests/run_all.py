#!/usr/bin/env python3
"""Run every test module and report a single verdict."""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    failures, total = [], 0
    for path in sorted(HERE.glob("test_*.py")):
        result = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True
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
