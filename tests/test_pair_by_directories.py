"""Windows iTerm reopens after a reboot pair by their tabs' directories.

Found in a real reboot: iTerm reopened every window with its tabs in the right
directories, but each tab titled only with the shell's name. Titles were the
only evidence, so none could be paired, and --create-missing rebuilt each one
beside a blank twin.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import iterm, procs, restore

HOME = str(Path.home())


def snap(*windows):
    """Snapshot windows, each a list of tab directories."""
    return {
        "windows": [
            {
                "window_index": wi,
                "window_id": f"s{wi}",
                "tabs": [
                    {
                        "tab_index": ti,
                        "pane_index": 1,
                        "title": "claude",
                        "kind": "claude",
                        "cwd": cwd,
                        "restore": f"cd {cwd} && claude",
                    }
                    for ti, cwd in enumerate(dirs, start=1)
                ],
            }
            for wi, dirs in enumerate(windows, start=1)
        ]
    }


def reopened(*windows, running=()):
    """Live windows as iTerm reopens them: right directories, '-zsh' titles."""
    return [
        {
            "window_index": wi,
            "window_id": f"w{wi}",
            "tab_index": ti,
            "pane_index": 1,
            "tty": f"/dev/ttys{wi}{ti}",
            "title": "-zsh",
            "kind": None,
            "busy": wi in running,
            "cwd": cwd,
        }
        for wi, dirs in enumerate(windows, start=1)
        for ti, cwd in enumerate(dirs, start=1)
    ]


def paired(snapshot, live):
    pairs, _, _ = restore.match_all(snapshot, live)
    return {w: m["window_index"] for w, m in pairs}


def test_reopened_windows_pair_by_their_tab_directories():
    s = snap(["/p/mentat"] * 3, ["/p/web", "/p/web", "/p/web/docs"])
    l = reopened(["/p/web", "/p/web", "/p/web/docs"], ["/p/mentat"] * 3)
    assert paired(s, l) == {1: 2, 2: 1}, paired(s, l)


def test_the_home_directory_is_not_evidence():
    # Every new window starts there; it identifies nothing.
    s = snap([HOME, HOME], ["/p/web"])
    assert paired(s, reopened([HOME, HOME])) == {}
    assert paired(s, reopened([HOME + "/", "/"])) == {}, "a trailing slash or / slipped through"


def test_tab_position_tells_two_windows_of_one_project_apart():
    # Both windows worked in one project; how many tabs, and where, differs.
    s = snap(["/p/mentat", "/p/mentat"], ["/p/mentat"] * 14)
    l = reopened(["/p/mentat"] * 14, ["/p/mentat", "/p/mentat"])
    assert paired(s, l) == {1: 2, 2: 1}, paired(s, l)


def test_directory_evidence_never_settles_a_real_tie():
    # Two snapshot windows identical by every measure stay a reported tie.
    s = snap(["/p/web"], ["/p/web"])
    pairs, _, notes = restore.match_all(s, reopened(["/p/web"]))
    assert pairs == [], pairs
    assert any("fits equally well" in n for n in notes), notes


def test_an_idle_window_that_might_be_one_does_not_block_its_rebuild():
    # Nothing runs in a window iTerm reopened: rebuilding beside it leaves at
    # worst an empty window, and nothing can run twice.
    s = snap(["/p/web"], ["/p/web"], ["/p/solo"])
    l = reopened([HOME], ["/p/web"])
    _, create, _, _, _ = restore.decide_restore_all(s, l, 1, True)
    assert [w["window_index"] for w in create] == [1, 2, 3], create


def test_a_window_with_something_running_still_blocks_it():
    # It may be the real one, part-restored: an ssh tab in it has no session
    # id to deduplicate, so rebuilding would run it twice.
    s = snap(["/p/web"], ["/p/web"], ["/p/solo"])
    l = reopened([HOME], ["/p/web"], running={2})
    _, create, _, notes, _ = restore.decide_restore_all(s, l, 1, True)
    assert [w["window_index"] for w in create] == [3], create
    assert sum("might be it" in n for n in notes) == 2, notes


def test_live_tabs_carries_each_tabs_directory():
    # The real join, with only iTerm and the process listing stubbed: each tab
    # gets the directory of the process running in it, in one lsof call.
    saved = (iterm.enumerate_tabs, procs.process_table, procs.cwd_of)
    calls = []
    try:
        iterm.enumerate_tabs = lambda: [  # type: ignore[assignment]
            {
                "window_index": 1,
                "window_id": "9",
                "tab_index": t,
                "pane_index": 1,
                "tty": f"/dev/ttys0{t}",
                "title": "-zsh",
            }
            for t in (1, 2)
        ]
        procs.process_table = lambda: [  # type: ignore[assignment]
            procs.Proc(101, 1, "ttys01", "-zsh"),
            procs.Proc(102, 1, "ttys02", "-zsh"),
        ]

        def cwd_of(pids):
            calls.append(sorted(pids))
            return {101: "/p/web", 102: "/p/api"}

        procs.cwd_of = cwd_of  # type: ignore[assignment]
        tabs = restore.live_tabs()
    finally:
        iterm.enumerate_tabs, procs.process_table, procs.cwd_of = saved  # type: ignore[assignment]
    assert [t["cwd"] for t in tabs] == ["/p/web", "/p/api"], tabs
    assert calls == [[101, 102]], f"expected one batched lsof call: {calls}"


def test_one_matching_tab_does_not_claim_a_bigger_window():
    # A fresh window opened in a project directory resembles any snapshot
    # window whose first tab worked there; its other tabs would be filled.
    s = snap(["/p/web", "/p/web/docs", "/p/api"])
    assert paired(s, reopened(["/p/web"])) == {}, "one tab claimed a three-tab window"
    assert paired(s, reopened(["/p/web", "/p/web/docs", "/p/api"])) == {1: 1}, "setup"


def test_a_reopened_one_tab_window_still_pairs_with_its_own():
    # Same size, every tab agrees: as strong as one tab can be.
    s = snap(["/p/helpers"], ["/p/mentat"] * 3)
    assert paired(s, reopened(["/p/helpers"])) == {1: 1}, paired(s, reopened(["/p/helpers"]))


def test_a_split_tab_is_judged_by_its_first_pane():
    # Only the first pane is ever recreated; a later pane's directory is not
    # what the tab was. Here pane 1 is in HOME, so tab 1 says nothing.
    s = snap(["/p/web", "/p/web/docs"])
    live = reopened([HOME, "/p/web/docs"])
    live.insert(1, {**live[0], "pane_index": 2, "tty": "/dev/ttys112", "cwd": "/p/web"})
    assert paired(s, live) == {}, "a second pane's directory was used as the tab's"


def test_a_failed_process_listing_fails_closed():
    # ps failing returns no processes; read as "all idle" it would let restore
    # type into live work, and let busy windows stop blocking rebuilds.
    saved = (iterm.enumerate_tabs, procs.process_table)
    try:
        iterm.enumerate_tabs = lambda: [  # type: ignore[assignment]
            {
                "window_index": 1,
                "window_id": "9",
                "tab_index": 1,
                "pane_index": 1,
                "tty": "/dev/ttys01",
                "title": "-zsh",
            }
        ]
        procs.process_table = lambda: []  # type: ignore[assignment]
        # Both readers: live_tabs (busy state) and running_sessions, whose
        # capture says which sessions must not be started twice.
        for reader in (restore.live_tabs, restore.running_sessions):
            try:
                reader()
            except iterm.ITermError:
                continue
            raise AssertionError(f"{reader.__name__} read an empty process table as all idle")
        # With iTerm holding no tabs at all, an empty table is simply correct.
        iterm.enumerate_tabs = lambda: []  # type: ignore[assignment]
        assert restore.live_tabs() == [] and restore.running_sessions() == {}
    finally:
        iterm.enumerate_tabs, procs.process_table = saved  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(run(globals()))
