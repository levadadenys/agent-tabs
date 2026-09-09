"""Auto-matching the current window is the default recovery path.

After a crash iTerm restores tab titles but not processes, so titles are the
only carry-over signal. Picking the wrong window replays another window's
sessions into this one.
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


def live(*titles, window=1):
    return [{"window_index": window, "tab_index": i + 1, "pane_index": 1,
             "tty": f"/dev/ttys00{i}", "title": t, "kind": None, "busy": False}
            for i, t in enumerate(titles)]


def test_picks_the_window_with_the_most_shared_titles():
    got = restore.match_window(snap(["a", "b", "c"], ["x", "y"]), live("a", "b"), 1)
    assert got["window_index"] == 1


def test_returns_none_when_nothing_overlaps():
    assert restore.match_window(snap(["a"], ["b"]), live("zzz"), 1) is None


def test_a_tie_is_refused_rather_than_guessed():
    # Two windows of generically-titled idle shells match equally well; picking
    # the first silently would replay the wrong window.
    try:
        restore.match_window(snap(["zsh"], ["zsh"]), live("zsh"), 1)
    except restore.Ambiguous as exc:
        assert "window 1" in str(exc) and "window 2" in str(exc)
    else:
        raise AssertionError("an ambiguous match must not be resolved silently")


def test_a_clear_winner_beats_a_partial_match():
    got = restore.match_window(snap(["a", "b"], ["b"]), live("a", "b"), 1)
    assert got["window_index"] == 1


def test_blank_titles_do_not_create_a_match():
    assert restore.match_window(snap([""], [""]), live(""), 1) is None


if __name__ == "__main__":
    raise SystemExit(run(globals()))
