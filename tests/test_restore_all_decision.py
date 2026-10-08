"""What `restore --all` decides, end to end, for the situations it exists for.

Copilot's round-2 concerns: the window you run from could be paired and come
back part-restored (its own tab is never typed into), and --create-missing
could rebuild a window that was deliberately left ambiguous.
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
             "session_id": f"s{i}{j}", "restore": f"claude --resume s{i}{j}"}
            for j, t in enumerate(titles)]}
        for i, titles in enumerate(windows)]}


def live(*windows):
    return [
        {"window_index": wi, "window_id": f"w{wi}", "tab_index": ti, "pane_index": 1,
         "tty": f"/dev/ttys{wi}{ti}", "title": t, "kind": None, "busy": False}
        for wi, titles in enumerate(windows, start=1)
        for ti, t in enumerate(titles, start=1)]


def decide(s, l, launcher, create_missing=False):
    pairs, create, reported, notes = restore.decide_restore_all(s, l, launcher, create_missing)
    return (sorted((w, m["window_index"]) for w, m in pairs),
            [w["window_index"] for w in create],
            [w["window_index"] for w in reported], notes)


def test_the_launcher_window_is_never_paired():
    """Even an exact match: one of its tabs is the one being typed in, so
    pairing it can only restore that snapshot window partly."""
    s = snap(["web", "api"], ["notes"])
    pairs, create, reported, notes = decide(s, live(["web", "api"], ["notes"]), launcher=1)
    assert pairs == [(2, 2)], pairs
    assert reported == [1], reported
    assert any("the one you are in" in n and "Cmd-N" in n for n in notes), notes


def test_create_missing_rebuilds_the_launchers_window_in_full():
    s = snap(["web", "api"], ["notes"])
    pairs, create, reported, notes = decide(
        s, live(["web", "api"], ["notes"]), launcher=1, create_missing=True
    )
    assert pairs == [(2, 2)] and create == [1] and reported == [], (pairs, create, reported)
    assert any("rebuilt in a new window" in n for n in notes), notes


def test_reboot_with_no_windows_rebuilds_everything():
    """The scenario --create-missing is for: iTerm restored nothing and you
    opened one window, titled '-zsh', to run the command. Two snapshot windows
    had idle '-zsh' tabs; that must neither pair nor block anything."""
    s = snap(["proj-a", "proj-b"], ["api", "-zsh"], ["web", "-zsh"], ["notes"])
    pairs, create, reported, notes = decide(s, live(["-zsh"]), launcher=1, create_missing=True)
    assert pairs == [], pairs
    assert create == [1, 2, 3, 4], (create, notes)
    assert reported == [] and notes == [], (reported, notes)


def test_copilots_partial_restore_case_now_restores_fully():
    """Before: the launcher '-zsh' paired with snapshot window 3, whose first
    tab ('web') sat at the launcher's own position and was never restored."""
    s = snap(["proj-a", "proj-b"], ["api"], ["web", "-zsh"], ["notes"])
    pairs, create, _, _ = decide(s, live(["-zsh"]), launcher=1, create_missing=True)
    assert pairs == [], f"the launcher was paired: {pairs}"
    assert 3 in create, create


def test_an_ambiguous_window_is_reported_not_rebuilt():
    # Live 2 might be snapshot 1 or 2; rebuilding either would sidestep the tie
    # and leave the window that may be the real one idle.
    s = snap(["notes", "a"], ["notes", "b"], ["solo"])
    pairs, create, reported, notes = decide(
        s, live(["-zsh"], ["notes"]), launcher=1, create_missing=True
    )
    assert create == [3], (create, notes)
    assert sum("not rebuilt" in n for n in notes) == 2, notes


def test_without_a_launcher_every_window_is_a_candidate():
    # Run outside iTerm: there is no window to exclude.
    s = snap(["web"], ["api"])
    pairs, _, _, _ = decide(s, live(["web"], ["api"]), launcher=None)
    assert pairs == [(1, 1), (2, 2)], pairs


if __name__ == "__main__":
    raise SystemExit(run(globals()))
