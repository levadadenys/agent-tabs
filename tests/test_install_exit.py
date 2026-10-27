"""A failed recorder install must not exit 0.

The recorder not running is the entire failure this tool exists to prevent, and
it is what actually happened for six days. A bootstrap script checking $? must
not read a dead recorder as success.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import config, install


class Sandbox:
    """Point every path the installer writes at a temp dir.

    Without this the suite writes the real ~/Library/LaunchAgents plist and the
    real CLI/skill symlinks -- running the tests would mutate a live install.
    """

    ATTRS = ("AGENT_DIR", "AGENT_PLIST", "BIN_DIR", "SKILL_DST", "LOG", "LEGACY_AUTOLAUNCH")

    def __init__(self, returncode: int, err: str = ""):
        self.returncode, self.err = returncode, err

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = {a: getattr(install, a) for a in self.ATTRS}
        self._saved["_launchctl"] = install._launchctl
        install.AGENT_DIR = root / "LaunchAgents"
        install.AGENT_PLIST = install.AGENT_DIR / "test.plist"
        install.BIN_DIR = root / "bin"
        install.SKILL_DST = root / "skills" / "resume-tabs"
        install.LOG = root / "state" / "recorder.log"
        install.LEGACY_AUTOLAUNCH = root / "legacy" / "agent_tabs_recorder.py"
        install._launchctl = lambda *a: subprocess.CompletedProcess(
            a, self.returncode, "", self.err
        )
        return root

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(install, name, value)
        self._tmp.cleanup()
        return False


def test_failed_load_exits_non_zero():
    with Sandbox(1, "permission denied"):
        assert install.run() == 1, "a dead recorder must not report success"


def test_successful_load_exits_zero():
    with Sandbox(0):
        assert install.run() == 0


def test_install_agent_reports_whether_it_loaded():
    with Sandbox(1, "nope"):
        _, loaded = install._install_agent()
        assert loaded is False
    with Sandbox(0):
        _, loaded = install._install_agent()
        assert loaded is True


def test_a_failed_load_keeps_a_legacy_recorder_that_still_works():
    # Removing a usable fallback after a failed migration leaves nothing.
    with Sandbox(1, "denied"):
        install.LEGACY_AUTOLAUNCH.parent.mkdir(parents=True, exist_ok=True)
        install.LEGACY_AUTOLAUNCH.write_text("legacy")
        install.run()
        assert install.LEGACY_AUTOLAUNCH.exists(), "legacy removed despite failure"


def test_a_dangling_legacy_link_is_removed_even_on_failure():
    # After an upgrade the link points at a script this version deletes, so
    # keeping it "as a fallback" would just leave a broken AutoLaunch entry.
    with Sandbox(1, "denied") as root:
        install.LEGACY_AUTOLAUNCH.parent.mkdir(parents=True, exist_ok=True)
        install.LEGACY_AUTOLAUNCH.symlink_to(root / "gone" / "recorder.py")
        install.run()
        assert not install.LEGACY_AUTOLAUNCH.is_symlink(), (
            "a dangling legacy link was preserved as if it were a fallback"
        )


def test_a_successful_load_removes_the_legacy_recorder():
    with Sandbox(0):
        install.LEGACY_AUTOLAUNCH.parent.mkdir(parents=True, exist_ok=True)
        install.LEGACY_AUTOLAUNCH.write_text("legacy")
        install.run()
        assert not install.LEGACY_AUTOLAUNCH.exists()


def test_a_running_legacy_recorder_is_stopped():
    # Unlinking the script does not stop a process iTerm already started; both
    # would then capture on its own timer and race on snapshot.json.
    import subprocess
    import time

    with Sandbox(0):
        script = install.LEGACY_AUTOLAUNCH
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("import time\ntime.sleep(60)\n")
        proc = subprocess.Popen([sys.executable, str(script)])
        for _ in range(50):
            listing = subprocess.run(
                ["ps", "-Ao", "pid=,command="], capture_output=True, text=True
            ).stdout
            if any(script.name in l and str(proc.pid) in l for l in listing.splitlines()):
                break
            time.sleep(0.1)
        try:
            install.run()
            for _ in range(50):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc.poll() is not None, "legacy recorder still running"
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_an_unrelated_process_with_the_same_basename_is_spared():
    # Matching the basename would signal any process whose command line merely
    # mentions the script -- including a real recorder while tests run.
    import subprocess
    import tempfile
    import time

    with Sandbox(0):
        other_dir = tempfile.mkdtemp()
        decoy = Path(other_dir) / install.LEGACY_AUTOLAUNCH.name
        decoy.write_text("import time\ntime.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, str(decoy)])
        time.sleep(0.5)
        try:
            install.run()
            time.sleep(0.5)
            assert proc.poll() is None, "an unrelated process was killed"
        finally:
            proc.kill()
            proc.wait()


def test_a_failed_load_leaves_the_legacy_recorder_alone():
    with Sandbox(1, "denied"):
        script = install.LEGACY_AUTOLAUNCH
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("x")
        install.run()
        assert script.exists(), "legacy removed despite a failed load"


def test_a_failed_reinstall_restores_the_previous_job():
    # Tearing down a healthy recorder and then failing to replace it is worse
    # than not upgrading at all.
    with Sandbox(1, "bootstrap refused"):
        install.AGENT_DIR.mkdir(parents=True, exist_ok=True)
        install.AGENT_PLIST.write_bytes(b"PREVIOUS")
        install.run()
        assert install.AGENT_PLIST.read_bytes() == b"PREVIOUS", (
            "a failed reinstall left the new plist in place of a working one"
        )


def test_worktree_without_git_refuses_rather_than_baking_a_doomed_path():
    import subprocess as sp

    original = install.REPO
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "wt"
            fake.mkdir()
            (fake / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt")
            install.REPO = fake
            saved_run = sp.run
            sp.run = lambda *a, **k: (_ for _ in ()).throw(OSError("no git"))
            try:
                install.main_checkout()
            except RuntimeError as exc:
                assert "worktree" in str(exc)
            else:
                raise AssertionError("should refuse a worktree it cannot resolve")
            finally:
                sp.run = saved_run
    finally:
        install.REPO = original


def test_the_suite_does_not_touch_the_real_install():
    real_plist = Path.home() / "Library" / "LaunchAgents" / "com.agent-tabs.recorder.plist"
    before = real_plist.stat().st_mtime if real_plist.exists() else None
    with Sandbox(0):
        install.run()
    after = real_plist.stat().st_mtime if real_plist.exists() else None
    assert before == after, "the tests wrote the real launchd plist"


def test_a_line_separator_cannot_get_another_process_signalled():
    # ps passes U+2028 in argv through. Split on it, a holder whose argument is
    # "x<U+2028><decoy pid> <legacy path>" forges a line naming the decoy next
    # to the legacy path, and install would SIGTERM the decoy. Real ps, real
    # processes, both this test's own.
    import sys as _sys
    import time

    with tempfile.TemporaryDirectory() as tmp:
        original = install.LEGACY_AUTOLAUNCH
        install.LEGACY_AUTOLAUNCH = Path(tmp) / "legacy" / "agent_tabs_recorder.py"
        decoy = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
        holder = subprocess.Popen(
            [
                _sys.executable,
                "-c",
                "import time; time.sleep(30)",
                f"x\u2028{decoy.pid} {install.LEGACY_AUTOLAUNCH}",
            ]
        )
        try:
            time.sleep(0.5)
            install._stop_legacy_processes()
            assert decoy.poll() is None, "install signalled a process named only by a forged line"
        finally:
            install.LEGACY_AUTOLAUNCH = original
            holder.kill()
            decoy.kill()


def interval_written(raw: "str | None") -> "tuple[int, list[str]]":
    """Install with this config.json text (None: no file); return the plist's
    StartInterval and install's output lines. The real plist builder, with
    config redirected so the real ~/.agent-tabs/config.json is never read."""
    import plistlib

    with Sandbox(0) as root:
        original = config.CONFIG_PATH
        config.CONFIG_PATH = root / "config.json"
        if raw is not None:
            config.CONFIG_PATH.write_bytes(raw.encode("utf-8", "surrogateescape"))
        try:
            lines, _ = install._install_agent()
            interval = plistlib.loads(install.AGENT_PLIST.read_bytes())["StartInterval"]
            return interval, lines
        finally:
            config.CONFIG_PATH = original


def test_the_interval_setting_reaches_the_plist_clamped():
    # (config.json, StartInterval written, whether install says it changed it)
    cases = (
        ('{"interval_seconds": 60}', 60, False),
        ('{"interval_seconds": 5}', 10, True),
        ('{"interval_seconds": 0}', 10, True),
        ('{"interval_seconds": -5}', 10, True),
        ('{"interval_seconds": 9999}', 300, True),
        ('{"interval_seconds": true}', 30, True),
        ('{"interval_seconds": 60.0}', 30, True),
        ('{"interval_seconds": NaN}', 30, True),
        ('{"interval_seconds": "60"}', 30, True),
        ('{"interval_seconds": "abc"}', 30, True),
        ('{"interval_seconds": null}', 30, True),
        ('{"interval_seconds": []}', 30, True),
    )
    for raw, expected, noted in cases:
        interval, lines = interval_written(raw)
        assert interval == expected, (raw, interval)
        assert f"recorder loaded, every {expected}s" in lines, (raw, lines)
        notes = [line for line in lines if "interval_seconds" in line]
        assert len(notes) == int(noted), (raw, lines)


def test_a_missing_or_damaged_config_never_breaks_install():
    for raw in (None, "{}", "\udcff\udcfe", "5", "null", "[1, 2]", "not json"):
        interval, lines = interval_written(raw)
        assert interval == 30, (raw, interval)
        assert not [line for line in lines if "interval_seconds" in line], (raw, lines)


def test_a_recorder_left_alone_says_it_keeps_its_old_interval():
    # A loaded job with no plist on disk is never torn down (nothing to put
    # back), so the new interval does not apply until the next install.
    with Sandbox(0) as root:
        original = config.CONFIG_PATH
        config.CONFIG_PATH = root / "config.json"
        config.CONFIG_PATH.write_text('{"interval_seconds": 60}')
        install._launchctl = lambda *a: subprocess.CompletedProcess(
            a, 0 if a[0] == "print" else 1, "", "already loaded"
        )
        try:
            lines, loaded = install._install_agent()
        finally:
            config.CONFIG_PATH = original
        assert loaded
        assert any("keeps its old interval, not 60s" in line for line in lines), lines
        assert not any(line.startswith("recorder loaded, every") for line in lines), lines


if __name__ == "__main__":
    raise SystemExit(run(globals()))
