"""A tab title must never be able to forge a row in iTerm's tab listing.

Any program can set its tab's title. Restore reads busy state from the listing,
so a forged row claiming another tab is idle would let restore type into live
work. The cleaner and the byte decoding run against real osascript (no iTerm
needed); the parser is driven with listings shaped like osascript's output.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runner import run

from agent_tabs import iterm

REAL_OSASCRIPT = iterm._osascript
SEPARATORS = "\n\r\u2028\u2029\x85\x0b\x0c\x1c\x1d\x1e\x1f\x00\x7f\x9f"
# A combining mark or variation selector after a separator makes one grapheme
# cluster of the two; a cleaner that matches graphemes misses the separator.
MARKS = ("", "\u0301", "\ufe0f")


def cleaned(title):
    """Run a title through the listing's real AppleScript cleaner."""
    expr = " & ".join(f"(character id {ord(c)})" for c in title) or '""'
    script = iterm.CLEAN_HANDLER + f"return clean({expr})"
    out = subprocess.run(["osascript", "-e", script], capture_output=True)
    assert out.returncode == 0, f"setup: osascript failed: {out.stderr!r}"
    return out.stdout.decode().removesuffix("\n")


def row(window_id="80", tab=1, pane=1, tty="/dev/ttys001", title="a title"):
    return f"1\x1f{window_id}\x1f{tab}\x1f{pane}\x1f{tty}\x1f{title}"


def listing(text):
    iterm._osascript = lambda _script: text  # type: ignore[assignment]


def test_the_title_cleaner_removes_every_separator():
    for sep in SEPARATORS:
        for mark in MARKS:
            got = cleaned(f"a{sep}{mark}b")
            assert got.startswith("a") and got.endswith("b"), f"setup: {got!r}"
            left = [hex(ord(c)) for c in got if c in SEPARATORS]
            assert not left, f"{hex(ord(sep))}+{mark!r} left {left} in {got!r}"
    assert cleaned("") == "" and cleaned("plain — ✓") == "plain — ✓"


def test_a_cleaned_title_parses_as_one_row():
    # End to end: the real cleaner's output, inside a row, through the parser.
    forged = row(tab=2, tty="/dev/ttys009", title="idle")
    for sep in SEPARATORS:
        for mark in MARKS:
            title = cleaned(f"x{sep}{mark}{forged}")
            listing(row(title=title) + "\n")
            rows = iterm._enumerate_once()
            assert len(rows) == 1, f"{hex(ord(sep))}+{mark!r} produced {len(rows)} rows"


def test_the_listing_cleans_every_title():
    # The script is only safe if the title it emits is the cleaned one. Running
    # ENUMERATE needs a live iTerm, so the wiring is checked on its text; the
    # cleaner itself is exercised for real above.
    assert "(my clean(name of s))" in iterm.ENUMERATE
    assert iterm.ENUMERATE.count("name of s") == 1, "a title is emitted uncleaned"


def test_osascript_output_keeps_a_carriage_return():
    # Text mode would turn this CR into a newline, i.e. a row break.
    iterm._osascript = REAL_OSASCRIPT  # type: ignore[assignment]
    assert iterm._osascript('return "a" & return & "b"') == "a\rb\n"


def test_a_line_separator_in_a_title_does_not_forge_a_row():
    forged = row(tab=2, tty="/dev/ttys009", title="idle")
    for sep in ("\u2028", "\r", "\x85"):
        # A separator alone stays inside the title.
        listing(row(title=f"x{sep}y") + "\n")
        rows = iterm._enumerate_once()
        assert len(rows) == 1 and rows[0]["title"] == f"x{sep}y", (sep, rows)
        # A whole forged row behind it is refused, never parsed as a row.
        listing(row(title=f"x{sep}{forged}") + "\n")
        try:
            rows = iterm._enumerate_once()
        except iterm.ITermError:
            continue
        assert not [r for r in rows if r["tty"] == "/dev/ttys009"], f"{sep!r} forged a row"


def test_a_malformed_or_repeated_row_fails_the_listing():
    for bad in (
        row() + "\n" + row(title="again") + "\n",  # the same pane twice
        row() + "\n" + "1\x1f80\x1f2\n",  # too few fields
        row(tab="two") + "\n",  # not a number
        row() + "\n" + "\x1f" * 5 + "\n",  # only separators: str.strip() calls them blank
    ):
        listing(bad)
        try:
            iterm._enumerate_once()
        except iterm.ITermError:
            continue
        raise AssertionError(f"accepted a listing it cannot trust: {bad!r}")
    listing(row() + "\n" + row(tab=2, tty="/dev/ttys002") + "\n")
    assert len(iterm._enumerate_once()) == 2, "setup: two distinct panes are fine"


def test_the_listing_walks_windows_by_id_not_position():
    # Positions are front-to-back order, which moves whenever a window is
    # created or focused -- exactly what a restore does. Walking by position
    # visited one window twice ("names window 65 tab 1 twice") or ran past the
    # end (-1719) during a real reboot restore. Running ENUMERATE needs a live
    # iTerm, so the walk is checked on the script's text.
    script = iterm.ENUMERATE
    assert "set wids to id of every window" in script
    assert "set win to window id wid" in script
    assert "repeat with win in windows" not in script, "walks windows by position"


if __name__ == "__main__":
    raise SystemExit(run(globals()))
