"""Turn a snapshot back into running tabs."""

from __future__ import annotations

import os
from functools import lru_cache
from dataclasses import dataclass

from . import adapters, iterm, procs, snapshot as snap_mod

# Anything that is not an idle shell counts as live work. Listing only the
# agent CLIs here meant a tab that had since been given to vim, psql or an ssh
# session was treated as free, and restore typed into it -- over ssh, that
# executes on the remote host.


class Ambiguous(ValueError):
    """More than one snapshot window fits the live window equally well."""


@dataclass
class Action:
    tab_index: int
    title: str
    kind: str
    command: str
    skip_reason: str | None = None

    @property
    def will_run(self) -> bool:
        return self.skip_reason is None and bool(self.command)


def current_tty() -> str | None:
    for stream in (0, 1, 2):
        try:
            return procs.normalise_tty(os.ttyname(stream))
        except OSError:
            continue
    env_tty = os.environ.get("TTY")
    return procs.normalise_tty(env_tty) if env_tty else None


def is_busy(proc: procs.Proc | None) -> bool:
    """Does this tab hold live work that restore must not type into?

    Any process that is not an idle shell counts. Restricting this to the agent
    CLIs left vim, psql and ssh sessions unprotected, and typing into a live ssh
    session runs the text on the remote host.
    """
    return proc is not None and not proc.is_shell


def live_tabs() -> list[dict]:
    """Live iTerm tabs annotated with whatever agent is running in them."""
    tabs = iterm.enumerate_tabs()
    grouped = procs.by_tty(procs.process_table())
    for tab in tabs:
        proc = procs.foreground_proc(
            grouped.get(procs.normalise_tty(tab["tty"]), []), adapters.AGENT_KINDS
        )
        tab["kind"] = proc.argv0 if proc else None
        tab["busy"] = is_busy(proc)
    return tabs


def current_window_index(tabs: list[dict]) -> int | None:
    tty = current_tty()
    if not tty:
        return None
    for tab in tabs:
        if procs.normalise_tty(tab["tty"]) == tty:
            return tab["window_index"]
    return None


def current_window_id(tabs: list[dict]) -> str | None:
    """The stable id of the window this command is running in."""
    tty = current_tty()
    if not tty:
        return None
    for tab in tabs:
        if procs.normalise_tty(tab["tty"]) == tty:
            return tab["window_id"]
    return None


def window_id_for_index(tabs: list[dict], window_index: int) -> str | None:
    for tab in tabs:
        if tab["window_index"] == window_index:
            return tab["window_id"]
    return None


def _best_match(snap: dict, live: list[dict], window_index: int) -> tuple[int, dict] | None:
    """Best-scoring snapshot window for a live window, with its score.

    Raises Ambiguous when two snapshot windows fit equally well.
    """
    live_titles = {
        t["title"].strip()
        for t in live
        if t["window_index"] == window_index and t["title"].strip()
    }
    if not live_titles:
        return None
    scored = []
    for window in snap.get("windows", []):
        titles = {t["title"].strip() for t in window.get("tabs", []) if t["title"].strip()}
        score = len(titles & live_titles)
        if score:
            scored.append((score, window))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    tied = [window for score, window in scored if score == best_score]
    if len(tied) > 1:
        # Silently picking one would replay the wrong window's sessions.
        raise Ambiguous(
            "This window matches "
            + " and ".join(f"snapshot window {w['window_index']}" for w in tied)
            + " equally well. Pass the window number explicitly."
        )
    return best_score, tied[0]


def match_window(snap: dict, live: list[dict], window_index: int) -> dict | None:
    """Guess which snapshot window corresponds to a live window, by title overlap.

    After a crash iTerm restores tab titles but not the processes, so titles are
    the only carry-over signal available.
    """
    best = _best_match(snap, live, window_index)
    return best[1] if best else None


def select_window(snap: dict, number: int | None, live: list[dict]) -> tuple[dict, str]:
    windows = snap.get("windows", [])
    if not windows:
        raise ValueError("Snapshot contains no windows.")
    if number is not None:
        for window in windows:
            if window["window_index"] == number:
                return window, f"snapshot window {number}"
        raise ValueError(
            f"Snapshot has no window {number}. Available: "
            + ", ".join(str(w['window_index']) for w in windows)
        )
    here = current_window_index(live)
    if here is not None:
        matched = match_window(snap, live, here)
        if matched:
            return matched, f"auto-matched snapshot window {matched['window_index']} by tab titles"
    raise ValueError(
        "Could not auto-match this window. Run `agent-tabs list` and pass a window number."
    )


def _overlaps(snap: dict, live: list[dict], window_index: int) -> dict[int, int]:
    """Title overlap between one live window and every snapshot window."""
    live_titles = {
        t["title"].strip()
        for t in live
        if t["window_index"] == window_index and t["title"].strip()
    }
    scores: dict[int, int] = {}
    for window in snap.get("windows", []):
        titles = {t["title"].strip() for t in window.get("tabs", []) if t["title"].strip()}
        score = len(titles & live_titles)
        if score:
            scores[window["window_index"]] = score
    return scores


# Exact assignment is exponential in the number of snapshot windows that one
# group of interlinked live windows competes for. Windows only compete when
# they share a candidate, so the problem is solved per connected group; real
# sessions produce groups of one or two. A group larger than this is reported
# rather than matched approximately -- a silent greedy fallback reintroduced
# exactly the stranding bug the exact solver exists to prevent.
EXACT_ASSIGNMENT_LIMIT = 12


def _groups(order: list[int], scores: dict[int, dict[int, int]]) -> list[list[int]]:
    """Split live windows into groups that compete for shared snapshot windows."""
    parent = {live: live for live in order}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[int, int] = {}
    for live in order:
        for snap_index in scores[live]:
            if snap_index in owner:
                parent[find(live)] = find(owner[snap_index])
            else:
                owner[snap_index] = live
    grouped: dict[int, list[int]] = {}
    for live in order:
        grouped.setdefault(find(live), []).append(live)
    return list(grouped.values())


def _solve(
    order: list[int],
    scores: dict[int, dict[int, int]],
    bit: dict[int, int],
    forbid: tuple[int, int | None] | None = None,
) -> tuple[int, int, tuple] | None:
    """Best (total overlap, windows restored, picks); None if infeasible.

    `forbid` rules out one (live window, choice) pair, where a choice of None
    means leaving that window unrestored. Used to test whether a pairing is
    forced or merely one of several equally good ones.
    """

    @lru_cache(maxsize=None)
    def best(i: int, used: int) -> tuple[int, int, tuple] | None:
        if i == len(order):
            return (0, 0, ())
        live = order[i]
        result = None
        # Assignment options first, so an equal outcome favours restoring the
        # front-most window rather than a later one.
        for snap_index, score in sorted(scores[live].items(), key=lambda kv: (-kv[1], kv[0])):
            if used & bit[snap_index] or forbid == (live, snap_index):
                continue
            sub = best(i + 1, used | bit[snap_index])
            if sub is None:
                continue
            candidate = (sub[0] + score, sub[1] + 1, (snap_index,) + sub[2])
            if result is None or candidate[:2] > result[:2]:
                result = candidate
        if forbid != (live, None):
            sub = best(i + 1, used)
            if sub is not None:
                candidate = (sub[0], sub[1], (None,) + sub[2])
                if result is None or candidate[:2] > result[:2]:
                    result = candidate
        return result

    return best(0, 0)


def _assign_group(
    order: list[int], scores: dict[int, dict[int, int]]
) -> tuple[dict[int, int], set[int]]:
    """Exact one-to-one assignment within one group: (claims, ambiguous).

    Maximises total title overlap, then windows restored. Greedy claiming --
    by window order, then by confidence -- could spend a snapshot window on a
    live window that had an alternative, stranding one that had none.

    Overlap comes first deliberately. Maximising the number of windows restored
    would give up an obvious 5-of-5 match to manufacture two 1-title matches,
    typing a window's sessions into one that barely resembles it.

    Only FORCED pairings are kept: a live window is paired only when every
    optimal assignment pairs it the same way. Two windows can each have a
    clear local favourite and still admit two equally good global assignments;
    picking one would be a guess, and the rule is to report rather than guess.
    Forced pairings agree across every optimum, so keeping only those can
    never produce a conflict.
    """
    contested = sorted({idx for live in order for idx in scores[live]})
    bit = {idx: 1 << k for k, idx in enumerate(contested)}
    top = _solve(order, scores, bit)
    if top is None:
        return {}, set()
    picks = dict(zip(order, top[2]))

    ambiguous: set[int] = set()
    for live in order:
        alternative = _solve(order, scores, bit, forbid=(live, picks[live]))
        if alternative is not None and alternative[:2] == top[:2]:
            ambiguous.add(live)

    claims = {
        live: snap for live, snap in picks.items()
        if snap is not None and live not in ambiguous
    }
    return claims, ambiguous


def _assign(
    order: list[int], scores: dict[int, dict[int, int]], notes: list[str]
) -> dict[int, int]:
    """Assign every group exactly; report any group too tangled to solve."""
    claims: dict[int, int] = {}
    for group in _groups(order, scores):
        contested = {idx for live in group for idx in scores[live]}
        # Both dimensions are capped. Snapshot candidates bound the state
        # space; live windows bound the number of re-solves used to test each
        # pairing for being forced. Capping only the first let ~30 windows
        # sharing a generic title like "zsh" take ~10s -- measured, not guessed.
        if len(contested) > EXACT_ASSIGNMENT_LIMIT or len(group) > EXACT_ASSIGNMENT_LIMIT:
            for live in group:
                notes.append(
                    f"live window {live}: shares titles with too many other "
                    f"windows ({len(group)} windows, {len(contested)} candidates) "
                    "to match confidently; restore it explicitly"
                )
            continue
        group_claims, ambiguous = _assign_group(sorted(group), scores)
        claims.update(group_claims)
        for live in sorted(ambiguous):
            notes.append(
                f"live window {live}: more than one pairing with the other "
                "windows fits equally well; restore it explicitly"
            )
    return claims


def match_all(
    snap: dict, live: list[dict]
) -> tuple[list[tuple[int, dict]], list[dict], list[str]]:
    """Pair every live window with a snapshot window, by tab-title overlap.

    Returns the pairs, the snapshot windows nothing matched, and notes about
    windows that were deliberately left alone. A snapshot window is claimed by
    at most one live window: restoring the same sessions into two windows would
    start every one of them twice.

    Pairings are assigned exactly, and kept only when forced: a window is left
    alone whenever another pairing would fit the evidence equally well once
    every other window is taken into account.
    """
    notes: list[str] = []
    order: list[int] = []
    scores: dict[int, dict[int, int]] = {}

    # Every window with any overlap goes to the solver, tied or not. A window
    # that ties on its own can be forced by the others -- live 'a,b' ties
    # between snapshots 'a' and 'b', but if another live window can only be
    # 'a', it must be 'b'. Filtering local ties out first left 'b' unrestored.
    # Ambiguity is decided by the solver, after every window is considered.
    for live_index in sorted({t["window_index"] for t in live}):
        overlaps = _overlaps(snap, live, live_index)
        if not overlaps:
            notes.append(
                f"live window {live_index}: no snapshot window matches its tab titles"
            )
            continue
        order.append(live_index)
        scores[live_index] = overlaps

    already_noted = len(notes)
    claims = _assign(order, scores, notes)
    reported = {n.split(":")[0] for n in notes[already_noted:]}
    by_index = {w["window_index"]: w for w in snap.get("windows", [])}
    pairs = [(live_index, by_index[claims[live_index]]) for live_index in order
             if live_index in claims]
    for live_index in order:
        if live_index not in claims and f"live window {live_index}" not in reported:
            notes.append(
                f"live window {live_index}: every snapshot window it matches "
                "is already being restored elsewhere, so it was left alone"
            )

    claimed = set(claims.values())
    unmatched = [
        w for w in snap.get("windows", []) if w["window_index"] not in claimed
    ]
    return pairs, unmatched, notes


def plan(window: dict, live: list[dict], target_window: str | None, force: bool) -> list[Action]:
    own_tty = current_tty()
    busy: dict[int, dict] = {}
    if target_window is not None:
        busy = {
            t["tab_index"]: t for t in live if t["window_id"] == target_window
        }

    actions: list[Action] = []
    seen_tabs: set[int] = set()
    for tab in window.get("tabs", []):
        index = tab["tab_index"]
        if index in seen_tabs:
            # Extra panes in a split tab are recorded but not recreated.
            continue
        seen_tabs.add(index)
        action = Action(index, tab.get("title") or "", tab.get("kind") or "", tab.get("restore") or "")
        existing = busy.get(index)
        if not action.command:
            action.skip_reason = "nothing recorded"
        elif existing and procs.normalise_tty(existing["tty"]) == own_tty:
            action.skip_reason = "this is the tab you are running from"
        elif existing and existing.get("busy") and not force:
            action.skip_reason = f"already running {existing['kind']} (use --force)"
        actions.append(action)
    return actions


def execute(actions: list[Action], window_id: str, force: bool = False) -> int:
    """Send each runnable action to its tab.

    Targets a window *id*, not an index. iTerm's window index is its
    front-to-back position, so it changes whenever any window is focused --
    including while a multi-window restore is part-way through. Keying on the
    index could type one window's sessions into another.

    Re-checks liveness immediately before typing: planning and execution are
    separated by however long the operator spent reading the dry run.
    """
    runnable = [a for a in actions if a.will_run]
    if not runnable:
        return 0
    # A window closed since planning must get nothing: not new tabs, and not
    # keystrokes. Checked here rather than left to an AppleScript error.
    if not any(t["window_id"] == window_id for t in iterm.enumerate_tabs()):
        print(f"  window {window_id} is gone; nothing typed into it")
        return 0
    ensure_tabs(window_id, max(a.tab_index for a in runnable))

    busy_now: set[int] = set()
    if not force:
        busy_now = {
            t["tab_index"]
            for t in live_tabs()
            if t["window_id"] == window_id and t["busy"]
        }
    own = current_tty()

    ran = 0
    for action in runnable:
        if action.tab_index in busy_now:
            print(f"  tab {action.tab_index:>2}  SKIP (became busy since the plan)")
            continue
        if own and _tty_of(window_id, action.tab_index) == own:
            print(f"  tab {action.tab_index:>2}  SKIP (this is the tab you are running from)")
            continue
        iterm.run_in_tab(window_id, action.tab_index, action.command)
        ran += 1
    return ran


def _tty_of(window_id: str, tab_index: int) -> str | None:
    for tab in iterm.enumerate_tabs():
        if tab["window_id"] == window_id and tab["tab_index"] == tab_index:
            return procs.normalise_tty(tab["tty"])
    return None


def ensure_tabs(window_id: str, needed: int) -> None:
    """Add tabs to a live window until it has at least `needed` of them."""
    have = len({
        t["tab_index"] for t in iterm.enumerate_tabs() if t["window_id"] == window_id
    })
    for _ in range(max(0, needed - have)):
        iterm.create_tab(window_id)
