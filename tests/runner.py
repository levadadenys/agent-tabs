"""Minimal test runner: no third-party dependency, same style in every file."""

import traceback


def run(namespace: dict) -> int:
    failures = 0
    for name, fn in sorted(namespace.items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failures += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    return 1 if failures else 0
