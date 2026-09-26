"""Turn a snapshot back into running tabs."""

from __future__ import annotations

import os
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


def match_all(
    snap: dict, live: list[dict]
) -> tuple[list[tuple[int, dict]], list[dict], list[str]]:
    """Pair every live window with a snapshot window, by tab-title overlap.

    Returns the pairs, the snapshot windows nothing matched, and notes about
    windows that were deliberately left alone. A snapshot window is claimed by
    at most one live window: restoring the same sessions into two windows would
    start every one of them twice.
    """
    pairs: list[tuple[int, dict]] = []
    notes: list[str] = []
    claimed: set[int] = set()

    # Claim in descending confidence, not window order. Window order is
    # front-to-back position, so claiming greedily by it let a weak partial
    # match take a snapshot window away from an exact match that happened to
    # sit further back.
    candidates: list[tuple[int, int, dict]] = []
    for live_index in sorted({t["window_index"] for t in live}):
        try:
            best = _best_match(snap, live, live_index)
        except Ambiguous as exc:
            notes.append(f"live window {live_index}: {exc}")
            continue
        if best is None:
            notes.append(
                f"live window {live_index}: no snapshot window matches its tab titles"
            )
            continue
        candidates.append((best[0], live_index, best[1]))

    for _score, live_index, matched in sorted(
        candidates, key=lambda c: (-c[0], c[1])
    ):
        index = matched["window_index"]
        if index in claimed:
            notes.append(
                f"live window {live_index}: snapshot window {index} is already "
                "being restored into another window, so this one was left alone"
            )
            continue
        claimed.add(index)
        pairs.append((live_index, matched))

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
