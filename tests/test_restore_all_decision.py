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
    pairs, create, reported, notes, _ = restore.decide_restore_all(
        s, l, launcher, create_missing
    )
    return (sorted((w, m["window_index"]) for w, m in pairs),
            [w["window_index"] for w in create],
            [w["window_index"] for w in reported], notes)


def test_the_launcher_window_is_never_paired():
    """Even an exact match: one of its tabs is the one being typed in, so
    pairing it can only restore that snapshot window partly."""
    s = snap(["web", "api"], ["notes"])
    pairs, create, reported, notes = decide(s, live(["web", "api"], ["notes"]), launcher=1)
    assert pairs == [(2, 2)], pairs
    # Not "no live window matched": one did, the launcher, and the note says so.
    assert reported == [], reported
    assert any(
        "the one you are in) is snapshot window 1" in n and "Cmd-N" in n for n in notes
    ), notes


def test_a_stray_window_never_takes_the_launchers_window():
    """Sonnet review: with the launcher left out of the matching, a window
    sharing one title with it took its snapshot window -- 4 sessions typed into
    an unrelated window, and a re-run then skipped them as running."""
    s = snap(["alpha", "beta", "gamma", "delta"])
    l = live(["alpha", "beta", "gamma", "delta"], ["alpha", "other"])
    for create_missing in (False, True):
        pairs, create, reported, notes = decide(s, l, launcher=1, create_missing=create_missing)
        assert pairs == [], f"snapshot window 1 went to a stray window: {pairs}"
        assert reported == [], reported
        assert create == ([1] if create_missing else []), create
        assert any("live window 2" in n and "elsewhere" in n for n in notes), notes


def test_the_launchers_own_matching_notes_are_not_shown():
    # It is never paired; "no snapshot window matches" about it is noise.
    s = snap(["web"])
    _, _, _, notes = decide(s, live(["unrelated"], ["web"]), launcher=1)
    assert not any(n.startswith("live window 1:") for n in notes), notes


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
    assert sum("not restored" in n for n in notes) == 2, notes
    # Explained once, in its own note -- not also listed as rebuildable.
    assert reported == [], reported


def test_without_create_missing_a_window_a_live_window_might_be_is_not_offered_a_rebuild():
    """Sonnet review: plain --all listed these as "no live window matched --
    rebuild it", which is false and would duplicate the live window."""
    s = snap(["notes", "a"], ["notes", "b"], ["solo"])
    _, _, reported, notes = decide(s, live(["-zsh"], ["notes"]), launcher=1)
    assert reported == [3], reported
    assert sum("not restored" in n and "might be it" in n for n in notes) == 2, notes


def test_too_many_live_windows_on_one_title_are_refused_outright():
    """The cap on live windows, not only on candidates: thirty windows sharing
    one title took ~10s to solve. One candidate, thirteen windows."""
    over = restore.EXACT_ASSIGNMENT_LIMIT + 1
    s = snap(["shared"])
    pairs, _, notes = restore.match_all(s, live(*[["shared"]] * over))
    assert pairs == [], pairs
    assert sum("too many other windows" in n for n in notes) == over, notes


def test_without_a_launcher_every_window_is_a_candidate():
    # Run outside iTerm: there is no window to exclude.
    s = snap(["web"], ["api"])
    pairs, _, _, _ = decide(s, live(["web"], ["api"]), launcher=None)
    assert pairs == [(1, 1), (2, 2)], pairs


def test_would_create_matches_what_create_missing_actually_does():
    """Copilot round 3: a hint promised --create-missing would rebuild "every"
    unmatched window, while it deliberately refuses ones a live window might
    be. The hint now names would_create, so it must equal the real outcome."""
    s = snap(["notes", "a"], ["notes", "b"], ["solo"], ["other"])
    l = live(["-zsh"], ["notes"])
    _, _, reported, _, would = restore.decide_restore_all(s, l, 1, False)
    _, create, _, _, would_again = restore.decide_restore_all(s, l, 1, True)
    assert would == [w["window_index"] for w in create] == would_again == [3, 4], (
        would, [w["window_index"] for w in create])
    # 1 and 2 are explained as "might be it", not listed as unmatched.
    assert sorted(w["window_index"] for w in reported) == [3, 4], reported


def test_a_refused_window_gets_commands_that_work():
    # "restore N" alone, run from the wrong window, part-restores again.
    s = snap(["notes", "a"], ["notes", "b"])
    _, _, _, notes, _ = restore.decide_restore_all(s, live(["-zsh"], ["notes"]), 1, True)
    refusal = next(n for n in notes if "not restored" in n)
    assert "from a new tab in that window" in refusal, refusal
    assert "--new-window --go" in refusal, refusal


def test_every_canonical_shell_is_not_evidence():
    """Copilot round 3: the list was a copy missing tcsh, csh, ksh and dash."""
    from agent_tabs import procs
    for base in procs.SHELL_NAMES | procs.LOGIN_NAMES:
        for title in (base, f"-{base}"):
            s = snap(["web", title])
            pairs, create, _, _, _ = restore.decide_restore_all(s, live([title]), None, False)
            assert pairs == [], f"paired on the shell title {title!r}"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
