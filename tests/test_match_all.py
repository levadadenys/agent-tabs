"""Pairing every live window with a snapshot window, for `restore --all`.

Restoring per window meant one invocation per window after a reboot. --all
does them in one pass, so the pairing has to be conservative: never restore the
same snapshot window twice, and say plainly what it left alone.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_tabs import restore  # noqa: E402
from runner import run  # noqa: E402


def snap(*windows):
    return {"windows": [
        {"window_index": i + 1, "tabs": [
            {"tab_index": j + 1, "pane_index": 1, "title": t, "kind": "claude",
             "restore": f"claude --resume {t}"}
            for j, t in enumerate(titles)]}
        for i, titles in enumerate(windows)]}


def live(*windows):
    rows = []
    for wi, titles in enumerate(windows, start=1):
        for ti, title in enumerate(titles, start=1):
            rows.append({"window_index": wi, "window_id": f"w{wi}",
                         "tab_index": ti, "pane_index": 1,
                         "tty": f"/dev/ttys{wi}{ti}", "title": title,
                         "kind": None, "busy": False})
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
    # Two live windows showing the same titles must not both be restored, or
    # every session in that window would start twice.
    s = snap(["a", "b"])
    pairs, _, notes = restore.match_all(s, live(["a", "b"], ["a", "b"]))
    assert len(pairs) == 1, f"claimed twice: {pairs}"
    assert any("already being restored" in n for n in notes)


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
    s = snap(["zsh"], ["zsh"])
    pairs, unmatched, notes = restore.match_all(s, live(["zsh"]))
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
    pairs, unmatched, notes = restore.match_all(
        s, live(["p", "unrelated"], ["p", "q", "r"])
    )
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
    pairs, unmatched, _ = restore.match_all(
        s, live(["a", "b", "d"], ["a", "b", "c"])
    )
    got = sorted((w, m["window_index"]) for w, m in pairs)
    assert got == [(1, 2), (2, 1)], got
    assert unmatched == []


def test_a_window_with_no_remaining_option_is_reported():
    s = snap(["a", "b"])
    pairs, _, notes = restore.match_all(s, live(["a", "b"], ["a", "b"]))
    assert len(pairs) == 1
    assert any("already being restored elsewhere" in n for n in notes), notes


def test_no_live_windows_pairs_nothing():
    pairs, unmatched, _ = restore.match_all(snap(["a"]), [])
    assert pairs == []
    assert len(unmatched) == 1


if __name__ == "__main__":
    raise SystemExit(run(globals()))
