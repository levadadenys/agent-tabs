"""Pairing every live window with a snapshot window, for `restore --all`.

Restoring per window meant one invocation per window after a reboot. --all
does them in one pass, so the pairing has to be conservative: never restore the
same snapshot window twice, and say plainly what it left alone.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import restore


def snap(*windows):
    return {
        "windows": [
            {
                "window_index": i + 1,
                "tabs": [
                    {
                        "tab_index": j + 1,
                        "pane_index": 1,
                        "title": t,
                        "kind": "claude",
                        "restore": f"claude --resume {t}",
                    }
                    for j, t in enumerate(titles)
                ],
            }
            for i, titles in enumerate(windows)
        ]
    }


def live(*windows, running=()):
    """Live windows by tab title; `running` names windows where something runs."""
    rows = []
    for wi, titles in enumerate(windows, start=1):
        for ti, title in enumerate(titles, start=1):
            rows.append(
                {
                    "window_index": wi,
                    "window_id": f"w{wi}",
                    "tab_index": ti,
                    "pane_index": 1,
                    "tty": f"/dev/ttys{wi}{ti}",
                    "title": title,
                    "kind": None,
                    "busy": wi in running,
                }
            )
    return rows


def test_pairs_every_window_that_matches():
    s = snap(["a", "b"], ["x", "y"])
    pairs, unmatched, _ = restore.match_all(s, live(["a", "b"], ["x", "y"]))
    assert [(w, p["window_index"]) for w, p in pairs] == [(1, 1), (2, 2)]
    assert unmatched == []


def test_pairing_survives_reordered_windows():
    # iTerm does not promise the same window order after a restart.
    s = snap(["a", "b"], ["x", "y"])
    pairs, unmatched, _ = restore.match_all(s, live(["x", "y"], ["a", "b"]))
    assert [(w, p["window_index"]) for w, p in pairs] == [(1, 2), (2, 1)]
    assert unmatched == []


def test_a_snapshot_window_is_claimed_only_once():
    """Never restore one snapshot window into two live windows.

    Two live windows showing identical titles cannot both be restored, or
    every session would start twice. Nor can either be picked: nothing
    distinguishes the real one, so both are reported and left alone.
    """
    s = snap(["a", "b"])
    pairs, unmatched, notes = restore.match_all(s, live(["a", "b"], ["a", "b"]))
    assert len(pairs) <= 1, f"claimed twice: {pairs}"
    assert pairs == [], f"guessed between indistinguishable windows: {pairs}"
    assert sum("fits equally well" in n for n in notes) == 2, notes
    assert [w["window_index"] for w in unmatched] == [1]


def test_unmatched_snapshot_windows_are_reported_not_created():
    s = snap(["a"], ["x"], ["q"])
    pairs, unmatched, _ = restore.match_all(s, live(["a"]))
    assert [p["window_index"] for _, p in pairs] == [1]
    assert sorted(w["window_index"] for w in unmatched) == [2, 3]


def test_a_live_window_matching_nothing_is_noted():
    s = snap(["a"])
    pairs, _, notes = restore.match_all(s, live(["a"], ["totally-unrelated"]))
    assert len(pairs) == 1
    assert any("no snapshot window matches" in n for n in notes)


def test_an_ambiguous_window_is_skipped_not_guessed():
    # Two snapshot windows fit equally well; restoring either could be wrong.
    s = snap(["notes"], ["notes"])
    pairs, unmatched, notes = restore.match_all(s, live(["notes"]))
    assert pairs == []
    assert any("equally well" in n for n in notes)
    assert len(unmatched) == 2


def test_the_stronger_match_claims_the_window():
    """Claiming must follow confidence, not window order.

    Window order is front-to-back position, so claiming greedily by it let a
    weak partial match take a snapshot window away from an exact match sitting
    further back -- and the weak window then had the wrong sessions typed in.
    """
    s = snap(["p", "q", "r"])
    pairs, _unmatched, notes = restore.match_all(s, live(["p", "unrelated"], ["p", "q", "r"]))
    assert [(w, m["window_index"]) for w, m in pairs] == [(2, 1)], pairs
    assert any("already being restored" in n for n in notes)


def test_the_stronger_match_wins_from_either_side():
    # Same shape with the windows swapped: the result must not depend on order.
    s = snap(["p", "q", "r"])
    pairs, _, _ = restore.match_all(s, live(["p", "q", "r"], ["p", "unrelated"]))
    assert [(w, m["window_index"]) for w, m in pairs] == [(1, 1)], pairs


def test_a_window_falls_back_to_its_next_best():
    """A taken first choice must not cost a window its restore.

    Live window 1 matches snapshot 1 weakly and snapshot 2 exactly; live
    window 2 matches snapshot 1 exactly. Window 2 should claim snapshot 1, and
    window 1 should still get snapshot 2 rather than being left out.
    """
    # live 1 prefers snapshot 1 (2 titles) over snapshot 2 (1 title); live 2
    # matches snapshot 1 harder (3 titles) and takes it, so live 1 must fall
    # back to snapshot 2 instead of being dropped.
    s = snap(["a", "b", "c"], ["d"])
    pairs, unmatched, _ = restore.match_all(s, live(["a", "b", "d"], ["a", "b", "c"]))
    got = sorted((w, m["window_index"]) for w, m in pairs)
    assert got == [(1, 2), (2, 1)], got
    assert unmatched == []


def test_a_window_with_no_remaining_option_is_reported():
    # A clearly weaker window whose only candidate went to a stronger one is
    # reported as having nothing left, not silently dropped.
    s = snap(["a", "b", "c"])
    pairs, _, notes = restore.match_all(s, live(["a", "b", "c"], ["a"]))
    assert [(w, m["window_index"]) for w, m in pairs] == [(1, 1)], pairs
    assert any("live window 2" in n and "already being restored elsewhere" in n for n in notes), (
        notes
    )


def test_assignment_restores_both_when_greedy_would_strand_one():
    """The case greedy claiming got wrong.

    Live 1 overlaps snapshot 1 by 3 and snapshot 2 by 2; live 2 overlaps only
    snapshot 1, by 3. Equal top scores sent live 1 to snapshot 1 and stranded
    live 2. Assigning 1 -> 2 and 2 -> 1 restores both.
    """
    s = snap(["a", "b", "c"], ["d", "e"])
    pairs, unmatched, _ = restore.match_all(s, live(["a", "b", "c", "d", "e"], ["a", "b", "c"]))
    got = sorted((w, m["window_index"]) for w, m in pairs)
    assert got == [(1, 2), (2, 1)], got
    assert unmatched == []


def test_a_strong_match_is_not_sacrificed_for_two_weak_ones():
    """Overlap outranks the number of windows restored.

    Live 1 is obviously snapshot 1 (5 of 5 titles); live 2 shares one title
    with it. Maximising windows restored would send live 1 to snapshot 2 (one
    title) and type snapshot 1's five sessions into live 2 -- the wrong window.
    Live 1 keeps its match; live 2 is reported and left alone.
    """
    s = snap(["a", "b", "c", "d", "e"], ["f"])
    pairs, unmatched, notes = restore.match_all(s, live(["a", "b", "c", "d", "e", "f"], ["a"]))
    assert [(w, m["window_index"]) for w, m in pairs] == [(1, 1)], pairs
    assert [w["window_index"] for w in unmatched] == [2]
    assert any("live window 2" in n for n in notes), notes


def test_many_unrelated_windows_do_not_degrade_the_assignment():
    """Copilot's case: the stranding pair plus enough other windows to cross
    the exact-assignment limit.

    A global limit fell back to greedy claiming and stranded live 2 again.
    Windows only compete within a group that shares candidates, so the pair is
    still solved exactly however many unrelated windows are open.
    """
    extra = [[f"solo{i}"] for i in range(restore.EXACT_ASSIGNMENT_LIMIT + 2)]
    s = snap(["a", "b", "c"], ["d", "e"], *extra)
    l = live(["a", "b", "c", "d", "e"], ["a", "b", "c"], *extra)
    pairs, unmatched, _ = restore.match_all(s, l)
    got = dict((w, m["window_index"]) for w, m in pairs)
    assert got[1] == 2 and got[2] == 1, got
    assert len(pairs) == len(extra) + 2, "an unrelated window went unrestored"
    assert unmatched == []


def test_a_group_too_tangled_to_solve_is_reported_not_guessed():
    # Past the limit within ONE group, the windows are reported, never paired
    # approximately.
    orig = restore.EXACT_ASSIGNMENT_LIMIT
    restore.EXACT_ASSIGNMENT_LIMIT = 1
    try:
        s = snap(["a", "b", "c"], ["d", "e"])
        pairs, unmatched, notes = restore.match_all(
            s, live(["a", "b", "c", "d", "e"], ["a", "b", "c"])
        )
        assert pairs == [], pairs
        assert sum("too many other windows" in n for n in notes) == 2, notes
        assert len(unmatched) == 2
    finally:
        restore.EXACT_ASSIGNMENT_LIMIT = orig


def test_grouping_keeps_independent_windows_apart():
    groups = restore._groups([1, 2, 3], {1: {10: 3, 11: 2}, 2: {10: 3}, 3: {20: 1}})
    assert sorted(sorted(g) for g in groups) == [[1, 2], [3]], groups


def test_a_global_tie_is_reported_not_guessed():
    """Codex's case: no window is tied locally, yet the assignment is.

    Each live window overlaps snapshot 1 by two titles and snapshot 2 by one,
    so {1->1, 2->2} and {1->2, 2->1} both total 3. Picking either would put a
    snapshot window's sessions into a window the evidence cannot tell apart.
    """
    s = snap(["a", "b"], ["c"])
    pairs, unmatched, notes = restore.match_all(s, live(["a", "b", "c", "p"], ["a", "b", "c", "q"]))
    assert pairs == [], f"guessed a globally tied assignment: {pairs}"
    assert sum("fits equally well" in n for n in notes) == 2, notes
    assert sorted(w["window_index"] for w in unmatched) == [1, 2]


def test_a_forced_pairing_survives_a_tie_elsewhere():
    # A tie in one group must not cost an unambiguous window in another.
    s = snap(["a", "b"], ["c"], ["solo1", "solo2"])
    pairs, _, _ = restore.match_all(
        s, live(["a", "b", "c", "p"], ["a", "b", "c", "q"], ["solo1", "solo2"])
    )
    assert [(w, m["window_index"]) for w, m in pairs] == [(3, 3)], pairs


def test_a_partly_forced_group_keeps_its_forced_pairs():
    """Ambiguity is per window, not per group.

    Live 3 is certainly snapshot 3; lives 1 and 2 tie for snapshots 1 and 2.
    All three share one group through title 'z', yet live 3 must still be
    restored.
    """
    s = snap(["a", "b"], ["c"], ["x", "y", "w", "z"])
    pairs, _, notes = restore.match_all(
        s, live(["a", "b", "c", "z"], ["a", "b", "c", "z"], ["x", "y", "w", "z"])
    )
    assert [(w, m["window_index"]) for w, m in pairs] == [(3, 3)], (pairs, notes)


def test_a_local_tie_is_resolved_by_the_other_windows():
    """Codex's case: tied alone, forced together.

    Live 'a,b' ties between snapshots 'a' and 'b' on its own, but live 'a'
    can only be snapshot 'a', so the complete pairing is forced. Filtering the
    local tie out before solving left snapshot 'b' unrestored.
    """
    pairs, unmatched, _ = restore.match_all(snap(["a"], ["b"]), live(["a", "b"], ["a"]))
    assert sorted((w, m["window_index"]) for w, m in pairs) == [(1, 2), (2, 1)], pairs
    assert unmatched == []


def test_a_lone_local_tie_is_still_reported():
    # With nothing else to force it, a tie stays a tie.
    pairs, unmatched, notes = restore.match_all(snap(["a"], ["b"]), live(["a", "b"]))
    assert pairs == [], pairs
    assert any("fits equally well" in n for n in notes), notes
    assert len(unmatched) == 2


def test_a_shell_name_alone_is_not_evidence():
    """An idle tab titled just '-zsh' identifies no window.

    Counting it paired a freshly opened window with whichever snapshot window
    happened to have an idle shell tab, and typed that window's sessions into
    the wrong place.
    """
    s = snap(["web", "-zsh"], ["api"])
    pairs, _unmatched, notes = restore.match_all(s, live(["-zsh"]))
    assert pairs == [], f"paired on a shell name: {pairs}"
    assert any("no snapshot window matches" in n for n in notes), notes


def test_shell_names_do_not_add_to_a_real_match():
    # Both live windows share '-zsh' with both snapshots; only real titles decide.
    s = snap(["web", "-zsh"], ["api", "-zsh"])
    pairs, _, _ = restore.match_all(s, live(["api", "-zsh"], ["web", "-zsh", "bash"]))
    assert sorted((w, m["window_index"]) for w, m in pairs) == [(1, 2), (2, 1)], pairs


def test_creatable_skips_windows_an_unpaired_window_might_be():
    """Copilot: --create-missing must not rebuild a window left ambiguous.

    Live 1 could be snapshot 1 or 2 (tied on 'notes'), so neither is rebuilt;
    snapshot 3 resembles nothing, so it is.
    """
    s = snap(["notes", "a"], ["notes", "b"], ["solo"])
    l = live(["notes"], running={1})  # something runs there: it may be the real one
    pairs, unmatched, _ = restore.match_all(s, l)
    create, refused = restore.creatable(s, l, pairs, unmatched)
    assert [w["window_index"] for w in create] == [3], create
    assert sorted(refused) == [1, 2], refused
    assert all("not restored" in n for n in refused.values()), refused


def test_creatable_ignores_stray_overlap_from_a_paired_window():
    # Live 1 is clearly snapshot 1; that it also shares 'x' with snapshot 2
    # says nothing about snapshot 2, which iTerm simply did not bring back.
    s = snap(["a", "b", "c", "x"], ["x", "y"])
    l = live(["a", "b", "c", "x"])
    pairs, unmatched, _ = restore.match_all(s, l)
    assert [(w, m["window_index"]) for w, m in pairs] == [(1, 1)]
    create, refused = restore.creatable(s, l, pairs, unmatched)
    assert [w["window_index"] for w in create] == [2], (create, refused)


def test_after_a_reboot_with_no_windows_everything_is_creatable():
    """The scenario --create-missing exists for: only the launcher window is
    open, it is excluded by the caller, so every snapshot window is rebuilt."""
    s = snap(["web", "-zsh"], ["api", "-zsh"], ["notes"])
    pairs, unmatched, _ = restore.match_all(s, [])
    create, refused = restore.creatable(s, [], pairs, unmatched)
    assert [w["window_index"] for w in create] == [1, 2, 3], create
    assert refused == {}


def test_no_live_windows_pairs_nothing():
    pairs, unmatched, _ = restore.match_all(snap(["a"]), [])
    assert pairs == []
    assert len(unmatched) == 1


if __name__ == "__main__":
    raise SystemExit(run(globals()))
