"""The pre-reboot fleet must survive a reboot that iTerm does not restore.

With no windows restored, starting even one session before running restore
lets that small capture replace snapshot.json about a minute later (the
collapse guard accepts a persistent drop). History keeps only the last 60
captures, and an idle iTerm fills it with empty ones within about half an hour,
so the full fleet would be gone. It is pinned in pre-boot.json on the first
promotion after a boot, and restore/list warn when the snapshot in use is the
smaller post-reboot one.
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run  # noqa: E402

BOOT = datetime(2026, 9, 26, 13, 33, tzinfo=timezone.utc)


def fresh(state: Path, boot=BOOT, settle: float = 0):
    os.environ["AGENT_TABS_HOME"] = str(state)
    import agent_tabs.snapshot as m
    m = importlib.reload(m)
    m.boot_time = (lambda: boot)  # type: ignore[assignment]
    m.COLLAPSE_SETTLE_SECONDS = settle
    return m


def snap(sessions: int, at: datetime) -> dict:
    tabs = [
        {"tab_index": i + 1, "pane_index": 1, "title": f"t{i}", "kind": "claude",
         "session_id": f"id{i}", "cwd": "/repo", "restore": f"claude --resume id{i}"}
        for i in range(sessions)
    ]
    return {"version": 1, "captured_at": at.isoformat(timespec="seconds"),
            "windows": [{"window_index": 1, "window_id": "1", "tabs": tabs}]}


def before(minutes):
    return BOOT - timedelta(minutes=minutes)


def after(minutes):
    return BOOT + timedelta(minutes=minutes)


def test_the_first_promotion_after_a_boot_pins_the_previous_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.save(snap(20, after(2)))
        assert m.PRE_BOOT.exists(), "the pre-reboot snapshot was not pinned"
        pinned = m.load(m.PRE_BOOT)
        assert m.agent_tab_count(pinned) == 20
        assert pinned["captured_at"] == before(5).isoformat(timespec="seconds")


def test_no_pin_without_a_reboot_in_between():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, after(1)))
        m.save(snap(21, after(2)))
        assert not m.PRE_BOOT.exists(), "pinned although nothing rebooted"


def test_a_later_post_boot_promotion_does_not_replace_the_pin():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.save(snap(20, after(1)))
        m.save(snap(22, after(3)))
        assert m.load(m.PRE_BOOT)["captured_at"] == before(5).isoformat(timespec="seconds")


def test_the_exact_scenario_keeps_the_full_fleet_recoverable():
    """20 sessions before the reboot; after it, one session is started before
    restoring. The collapse guard eventually accepts the drop, so snapshot.json
    becomes the one-session capture -- but the 20 are pinned and flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        for minute in range(1, m.COLLAPSE_STREAK + 2):
            m.save(snap(1, after(minute)))
        assert m.agent_tab_count(m.load()) == 1, "setup: the drop should be accepted"
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, "the full fleet was lost"
        warning = m.pre_boot_warning(m.load())
        assert warning and "held 20" in warning and "holds 1" in warning, warning
        assert f"-f {m.PRE_BOOT}" in warning, "the warning must give the command"
        # The scenario is a reboot iTerm did not restore; there --all alone
        # pairs nothing and creates nothing, so the command must create.
        assert "--all --create-missing" in warning, warning


def test_no_warning_when_the_snapshot_predates_the_boot():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        assert m.pre_boot_warning(m.load()) is None


def test_no_warning_when_nothing_was_lost():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.save(snap(20, after(2)))
        assert m.pre_boot_warning(m.load()) is None, "warned though nothing was lost"


def test_a_pin_from_an_earlier_reboot_is_never_offered():
    """A pre-boot.json can outlive its boot.

    Normally the first promotion after each boot re-pins it. When that pin
    fails -- here snapshot.json is unreadable and history holds nothing from
    before this boot -- the file left from an earlier reboot survives next to
    a post-reboot snapshot. It must not
    be offered as "the last one before the reboot": it would send a restore
    back to a fleet from days ago.
    """
    with tempfile.TemporaryDirectory() as tmp:
        old_boot = BOOT - timedelta(days=3)
        m = fresh(Path(tmp), boot=old_boot)
        m.save(snap(20, old_boot - timedelta(minutes=5)))
        m.save(snap(20, old_boot + timedelta(minutes=1)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, "setup: an old pin exists"

        m.boot_time = (lambda: BOOT)          # the machine reboots again
        m.LATEST.write_text("{unreadable")    # so this boot's pin cannot happen...
        for archive in m.HISTORY_DIR.glob("*.json"):  # ...not even from history
            archive.unlink()
        latest, _ = m.save(snap(20, after(1)))
        assert latest is not None, "setup: the post-boot capture should promote"
        assert m.load(m.PRE_BOOT).get("pinned_for_boot") != BOOT.isoformat(), (
            "setup: the pin for this boot should have failed"
        )

        m.save(snap(5, after(2)))             # sessions drop, as after a reboot
        for minute in range(3, 3 + m.COLLAPSE_STREAK):
            m.save(snap(5, after(minute)))
        assert m.agent_tab_count(m.load()) == 5, "setup: the drop is accepted"
        assert m.pre_boot_warning(m.load()) is None, (
            "offered a pin left over from an earlier reboot"
        )


def test_an_unreadable_snapshot_is_pinned_from_history_instead():
    """Copilot round 4: an unreadable snapshot.json skipped the pin, then the
    post-reboot capture replaced it, and the pre-reboot fleet -- still readable
    in history -- rotated out within half an hour."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.LATEST.write_text("{unreadable")
        m.save(snap(20, after(1)))
        assert m.PRE_BOOT.exists(), "the pre-reboot fleet was not pinned"
        pinned = m.load(m.PRE_BOOT)
        assert m.agent_tab_count(pinned) == 20
        assert pinned["pinned_for_boot"] == BOOT.isoformat()


def test_the_fallback_pins_the_full_fleet_not_the_dying_shutdown():
    # The last captures before a reboot are half-dead: archived, never
    # promoted. The newest pre-boot archive would pin 3 sessions instead of 20.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(10)))
        m.save(snap(8, before(2)))          # shutdown in progress
        m.save(snap(3, before(1)))          # nearly gone
        m.LATEST.write_text("{unreadable")
        m.save(snap(20, after(1)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, "pinned a dying capture"


def test_a_missed_pin_is_made_on_a_later_promotion():
    # If the first post-boot pin could not happen, the next promotion tries
    # again rather than giving up for the whole boot.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        real = m._write_atomic
        def flaky(path, data):
            if path == m.PRE_BOOT:
                raise OSError("disk full")
            return real(path, data)
        m._write_atomic = flaky
        m.save(snap(20, after(1)))          # the pin fails
        m._write_atomic = real
        assert not m.PRE_BOOT.exists()
        m.save(snap(21, after(2)))          # snapshot.json is post-boot now
        assert m.PRE_BOOT.exists(), "a missed pin was never retried"
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20


def test_the_fullest_archive_wins_wherever_it_sits_in_history():
    # Neither the newest nor the oldest: the full fleet in the middle.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(5, before(30)))
        m.save(snap(22, before(20)))
        m.save(snap(3, before(1)))          # dying; held back, archived
        m.LATEST.write_text("{unreadable")
        m.save(snap(5, after(1)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 22, "missed the full fleet"


def test_a_half_dead_snapshot_loses_to_the_fuller_archive():
    """Sonnet review: a shutdown that dragged on past the collapse guard got
    its half-dead capture promoted, and the pin took snapshot.json as-is --
    3 sessions, while history still held all 22."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(22, before(20)))
        for minute in range(10, 10 - m.COLLAPSE_STREAK - 1, -1):
            m.save(snap(3, before(minute)))
        assert m.agent_tab_count(m.load()) == 3, "setup: the drop should be accepted"
        m.save(snap(3, after(1)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 22, "pinned the half-dead capture"


def test_with_history_rotated_out_the_snapshot_itself_is_pinned():
    """The reason snapshot.json is a candidate at all: after a long idle spell
    before the reboot, history holds only empty captures (here: none), and the
    fleet survives in snapshot.json alone. Pinning only after it is
    overwritten would lose it."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(60)))
        for archive in m.HISTORY_DIR.glob("*.json"):
            archive.unlink()
        m.save(snap(20, after(1)))        # windows restored by iTerm; promoted
        assert m.PRE_BOOT.exists(), "the fleet in snapshot.json was not pinned"
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20


def test_a_pin_is_never_redone_within_the_same_boot():
    # Once pinned for this boot, later promotions leave it alone -- even if a
    # fuller pre-boot capture turns up in history afterwards.
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.save(snap(20, after(1)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, "setup"
        name = before(30).strftime("%Y%m%dT%H%M%S%f") + ".json"
        (m.HISTORY_DIR / name).write_text(json.dumps(snap(25, before(30))))
        m.save(snap(21, after(2)))
        assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, "the pin was redone"


def test_a_pre_boot_snapshot_gets_no_warning_even_with_a_pin_present():
    # Reading an older snapshot (e.g. -f a history file) is not "replaced".
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp))
        m.save(snap(20, before(5)))
        m.save(snap(20, after(1)))
        assert m.PRE_BOOT.exists(), "setup"
        assert m.pre_boot_warning(snap(5, before(3))) is None


def test_boot_time_reads_kern_boottime_as_utc():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AGENT_TABS_HOME"] = tmp
        import agent_tabs.snapshot as m
        m = importlib.reload(m)

        class Done:
            stdout = "{ sec = 1790429604, usec = 512000 } Sat Sep 26 16:33:24 2026\n"
        m.subprocess.run = lambda *a, **k: Done()  # type: ignore[assignment]
        try:
            assert m.boot_time() == datetime(2026, 9, 26, 13, 33, 24, tzinfo=timezone.utc), (
                m.boot_time()
            )
        finally:
            importlib.reload(m.subprocess)


def test_a_naive_capture_time_is_read_as_utc():
    import agent_tabs.snapshot as m
    when = m.captured_at({"captured_at": "2026-09-26T13:33:00"})
    assert when == datetime(2026, 9, 26, 13, 33, tzinfo=timezone.utc), when


def test_damaged_files_never_crash_list_restore_or_a_capture():
    """These readers run inside `list`, `restore` and every capture: bad UTF-8,
    JSON that is not an object, and an object of the wrong shape."""
    for content in (b"\xff\xfe not utf-8", b"[1, 2, 3]", b'{"windows": 7}'):
        with tempfile.TemporaryDirectory() as tmp:
            m = fresh(Path(tmp))
            m.save(snap(20, before(5)))
            m.LATEST.write_bytes(content)
            (m.HISTORY_DIR / "20260101T000000000000.json").write_bytes(content)
            m.PRE_BOOT.write_bytes(content)
            m.save(snap(20, after(1)))       # the pin reads all three
            assert m.agent_tab_count(m.load(m.PRE_BOOT)) == 20, content
            m.PRE_BOOT.write_bytes(content)
            assert m.pre_boot_warning(snap(1, after(3))) is None, content


def test_an_unknown_boot_time_is_harmless():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), boot=None)
        m.save(snap(20, before(5)))
        m.save(snap(20, after(2)))
        assert not m.PRE_BOOT.exists()
        assert m.pre_boot_warning(m.load()) is None


if __name__ == "__main__":
    raise SystemExit(run(globals()))
