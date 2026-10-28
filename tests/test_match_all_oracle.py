"""match_all against an independent brute-force oracle.

The matcher was rewritten six times in succession, each time reacting to a
reviewer. Hand-written cases only cover what someone thought to write; this
checks the whole behaviour against the specification on hundreds of random
instances built to collide, so the next rewrite cannot quietly break an earlier
guarantee.

The oracle is written from the spec, not from restore.py, and deliberately
shares none of its code:
- score(live, snapshot) = distinct non-blank titles they share, not counting
  titles that are only a shell name, plus tab positions whose working
  directories are equal (a trailing slash aside), not counting the home
  directory or /, and counted only when at least 2 tabs agree or every tab of
  two same-size windows does (directories
  carry over a reboot, where iTerm resets every title to the shell's name);
- an assignment is one-to-one over pairs with score >= 1, windows may be left
  unassigned;
- optimal = maximum total score, then maximum number of pairs;
- a pairing is returned only if EVERY optimal assignment contains it.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import restore

SEED = 20260930
INSTANCES = 600


# Written independently of restore.GENERIC_TITLES on purpose: a title that is
# only a shell name identifies no window, so it is not evidence.
_SHELLS = ("zsh", "bash", "sh", "fish", "tcsh", "csh", "ksh", "dash", "login")
SHELL_NAMES = {name for base in _SHELLS for name in (base, f"-{base}")}


# Likewise independent of restore.GENERIC_DIRS: every new tab starts in one of
# these, so they identify nothing.
NOWHERE = {str(Path.home()), "/"}


def titles(window):
    names = (title.strip() for title, _ in window)
    return {n for n in names if n and n.lower() not in SHELL_NAMES}


def same_dirs(a, b):
    """Tab positions where both windows sit in the same real directory.

    A trailing slash names the same directory; the root and the home
    directory are no evidence.
    """

    def real(d):
        d = (d or "").rstrip("/")
        return d if d and d not in {n.rstrip("/") for n in NOWHERE} else None

    hits = sum(1 for (_, x), (_, y) in zip(a, b) if real(x) and real(x) == real(y))
    # One matching tab is too weak on its own, unless the windows are the same
    # size and every tab matches.
    return hits if hits >= 2 or (len(a) == len(b) and hits == len(a)) else 0


def oracle(snapshot_windows, live_windows):
    """Forced (live, snapshot) pairs, 1-based, by exhaustive enumeration."""
    score = [
        [len(titles(l) & titles(s)) + same_dirs(l, s) for s in snapshot_windows]
        for l in live_windows
    ]
    best, optima = None, []

    def walk(i, used, pairs, total):
        nonlocal best, optima
        if i == len(live_windows):
            key = (total, len(pairs))
            if best is None or key > best:
                best, optima = key, [dict(pairs)]
            elif key == best:
                optima.append(dict(pairs))
            return
        walk(i + 1, used, pairs, total)
        for s in range(len(snapshot_windows)):
            if s not in used and score[i][s] >= 1:
                pairs.append((i, s))
                walk(i + 1, used | {s}, pairs, total + score[i][s])
                pairs.pop()

    walk(0, frozenset(), [], 0)
    forced = set(optima[0].items())
    for other in optima[1:]:
        forced &= set(other.items())
    return {(l + 1, s + 1) for l, s in forced}, score


def fixtures(snapshot_windows, live_windows):
    snap = {
        "windows": [
            {
                "window_index": i + 1,
                "tabs": [
                    {
                        "tab_index": j + 1,
                        "pane_index": 1,
                        "title": t,
                        "kind": "claude",
                        "cwd": d,
                        "restore": "x",
                    }
                    for j, (t, d) in enumerate(ts)
                ],
            }
            for i, ts in enumerate(snapshot_windows)
        ]
    }
    live = [
        {
            "window_index": wi,
            "window_id": f"w{wi}",
            "tab_index": ti,
            "pane_index": 1,
            "tty": f"/dev/t{wi}{ti}",
            "title": t,
            "kind": None,
            "busy": False,
            "cwd": d,
        }
        for wi, ts in enumerate(live_windows, 1)
        for ti, (t, d) in enumerate(ts, 1)
    ]
    return snap, live


def random_instance(rnd):
    alphabet = "abcdef"[: rnd.randint(3, 6)]

    def window():
        def title():
            roll = rnd.random()
            if roll < 0.12:
                return rnd.choice([" ", ""])
            if roll < 0.25:
                return rnd.choice(sorted(SHELL_NAMES))  # shell names: not evidence
            return rnd.choice(alphabet)

        def directory():
            roll = rnd.random()
            if roll < 0.3:
                return rnd.choice([None, "", str(Path.home()), str(Path.home()) + "/", "/"])
            return rnd.choice(["/p/x", "/p/y", "/p/z", "/p/x/"])

        return [(title(), directory()) for _ in range(rnd.randint(1, 4))]

    snapshot_windows = [window() for _ in range(rnd.randint(1, 5))]
    live_windows = [window() for _ in range(rnd.randint(1, 5))]
    return snapshot_windows, live_windows


def test_match_all_agrees_with_the_brute_force_oracle():
    rnd = random.Random(SEED)
    for n in range(INSTANCES):
        snapshot_windows, live_windows = random_instance(rnd)
        expected, _ = oracle(snapshot_windows, live_windows)
        pairs, _, _ = restore.match_all(*fixtures(snapshot_windows, live_windows))
        got = {(w, m["window_index"]) for w, m in pairs}
        assert got == expected, (
            f"instance {n}: snapshot={snapshot_windows} live={live_windows} "
            f"expected={sorted(expected)} got={sorted(got)}"
        )


def test_invariants_hold_on_every_instance():
    rnd = random.Random(SEED + 1)
    for n in range(INSTANCES):
        snapshot_windows, live_windows = random_instance(rnd)
        _, score = oracle(snapshot_windows, live_windows)
        pairs, unmatched, notes = restore.match_all(*fixtures(snapshot_windows, live_windows))
        claimed = [m["window_index"] for _, m in pairs]
        context = f"instance {n}: snapshot={snapshot_windows} live={live_windows}"
        assert len(claimed) == len(set(claimed)), f"{context}: a snapshot restored twice"
        for w, m in pairs:
            assert score[w - 1][m["window_index"] - 1] >= 1, f"{context}: zero-overlap pair"
        paired = {w for w, _ in pairs}
        for w in range(1, len(live_windows) + 1):
            if w not in paired and any(score[w - 1]):
                assert any(f"live window {w}:" in note for note in notes), (
                    f"{context}: live window {w} left alone without a note"
                )
        expected_unmatched = {i + 1 for i in range(len(snapshot_windows))} - set(claimed)
        assert {w["window_index"] for w in unmatched} == expected_unmatched, context


def test_the_oracle_itself_is_not_vacuous():
    # Guard against an oracle so weak it agrees with anything: the instance
    # set must actually contain forced pairs and ambiguous ones.
    rnd = random.Random(SEED)
    forced = ambiguous = 0
    for _ in range(INSTANCES):
        snapshot_windows, live_windows = random_instance(rnd)
        expected, score = oracle(snapshot_windows, live_windows)
        forced += bool(expected)
        overlapping = sum(1 for row in score if any(row))
        ambiguous += overlapping > len(expected)
    assert forced > INSTANCES // 4, forced
    assert ambiguous > INSTANCES // 4, ambiguous


if __name__ == "__main__":
    raise SystemExit(run(globals()))
