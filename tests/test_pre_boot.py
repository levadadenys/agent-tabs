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
    fails -- here snapshot.json is unreadable at that moment -- the file left
    from an earlier reboot survives next to a post-reboot snapshot. It must not
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
        m.LATEST.write_text("{unreadable")    # so this boot's pin cannot happen
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


def test_an_unknown_boot_time_is_harmless():
    with tempfile.TemporaryDirectory() as tmp:
        m = fresh(Path(tmp), boot=None)
        m.save(snap(20, before(5)))
        m.save(snap(20, after(2)))
        assert not m.PRE_BOOT.exists()
        assert m.pre_boot_warning(m.load()) is None


if __name__ == "__main__":
    raise SystemExit(run(globals()))
