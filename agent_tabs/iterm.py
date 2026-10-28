"""iTerm2 control via AppleScript.

AppleScript is used rather than the iTerm2 Python API so that this works from
any context -- a plain shell, or the launchd recorder -- with no dependency on
iTerm's bundled Python runtime, which is not installed by default.
"""

from __future__ import annotations

import subprocess
import time

# ASCII unit separator between fields, linefeed between rows. A tab title is
# the one field a program controls (any process can set its tab's title), so
# CLEAN_HANDLER replaces every control character and line separator in it with
# a space before it is emitted: a title can never carry a forged row. By code
# point (`id of t`), not with text item delimiters: those match whole grapheme
# clusters, so a linefeed followed by a combining mark was never matched.
FIELD = "\x1f"
FIELD_AS = "(character id 31)"

CLEAN_HANDLER = """
on clean(t)
    if t is "" then return ""
    set ids to id of t
    if class of ids is integer then set ids to {ids}
    set out to {}
    repeat with c in ids
        set c to contents of c
        if c < 32 or (c > 126 and c < 160) or c = 8232 or c = 8233 then
            set end of out to 32
        else
            set end of out to c
        end if
    end repeat
    return character id out
end clean
"""


class ITermError(RuntimeError):
    pass


def _osascript(script: str) -> str:
    # Bytes, decoded here: text mode translates a CR into a newline, which
    # would turn a CR inside a title into a row break.
    result = subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise ITermError(stderr or "osascript failed")
    return result.stdout.decode("utf-8", "replace")


def _as_id(window_id: str) -> str:
    """Render a window id for AppleScript: numeric bare, anything else quoted."""
    text = str(window_id)
    return text if text.lstrip("-").isdigit() else f'"{_escape(text)}"'


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


ENUMERATE = (
    CLEAN_HANDLER
    + f"""
tell application "iTerm2"
    set out to ""
    -- Windows by id, never by position: the position is front-to-back order,
    -- which changes whenever a window is created or focused, so walking
    -- "window 1, window 2, ..." while that happens visits one window twice or
    -- runs past the end (-1719). wi is the order at the moment the ids were read.
    set wids to id of every window
    set wi to 0
    repeat with wid in wids
        set wi to wi + 1
        set win to window id wid
        set ti to 0
        repeat with tb in tabs of win
            set ti to ti + 1
            set si to 0
            repeat with s in sessions of tb
                set si to si + 1
                set out to out & wi & {FIELD_AS} & (id of win) & {FIELD_AS} & ti & {FIELD_AS} & si & {FIELD_AS} & (tty of s) & {FIELD_AS} & (my clean(name of s)) & linefeed
            end repeat
        end repeat
    end repeat
    return out
end tell
"""
)


def is_running() -> bool:
    script = 'tell application "System Events" to return (name of processes) contains "iTerm2"'
    try:
        return _osascript(script).strip() == "true"
    except ITermError:
        return False


ENUMERATE_ATTEMPTS = 6
ENUMERATE_BACKOFF_SECONDS = 0.5  # times the attempt number: 0.5 s, 1 s, ... 2.5 s


def enumerate_tabs() -> list[dict]:
    """Every split-pane session in every window, with its tty and title.

    Retries with a growing wait: a tab closing while AppleScript walks a window
    raises "Invalid index (-1719)", and iTerm can be slow to answer while it is
    restoring or starting many sessions (-1712). Observed in practice, during a
    real reboot restore. A lost capture is cheap on any single tick and
    expensive if it is the last one before a crash, and a restore that gives up
    stops part-way.
    """
    last: ITermError | None = None
    for attempt in range(ENUMERATE_ATTEMPTS):
        try:
            return _enumerate_once()
        except ITermError as exc:
            last = exc
            if attempt < ENUMERATE_ATTEMPTS - 1:
                time.sleep(ENUMERATE_BACKOFF_SECONDS * (attempt + 1))
    raise last if last else ITermError("enumeration failed")


def _enumerate_once() -> list[dict]:
    """Parse the listing, refusing anything that is not exactly one row per pane.

    Restore decides which tabs are busy from these rows, so a forged row could
    make a busy tab look idle. Titles are cleaned in the script; this is the
    second line of defence: newlines only (never splitlines(), which also
    breaks on U+2028 and the like), and a malformed or repeated row fails the
    whole listing rather than being skipped or guessed at.
    """
    rows = []
    seen: set[tuple[str, int, int]] = set()
    for line in _osascript(ENUMERATE).split("\n"):
        # Only a truly empty line (the end of the output) is skipped: str.strip()
        # counts the field separator as whitespace, so a row of nothing but
        # separators would vanish instead of failing the listing.
        if line == "":
            continue
        parts = line.split(FIELD)
        if len(parts) != 6:
            raise ITermError(f"unexpected row in the tab listing: {line!r}")
        window_index, window_id, tab_index, pane_index, tty, title = parts
        try:
            key = (window_id, int(tab_index), int(pane_index))
            row = {
                "window_index": int(window_index),
                "window_id": window_id,
                "tab_index": key[1],
                "pane_index": key[2],
                "tty": tty.strip(),
                "title": title.strip(),
            }
        except ValueError:
            raise ITermError(f"unexpected row in the tab listing: {line!r}") from None
        if key in seen:
            raise ITermError(f"the tab listing names window {key[0]} tab {key[1]} twice")
        seen.add(key)
        rows.append(row)
    return rows


def run_in_tab(window_id: str, tab_index: int, text: str) -> None:
    """Type a command into an existing tab and press Enter.

    Addressed by window *id*, never by index: iTerm's window index is its
    front-to-back position (`orderedScriptingWindows` in the scripting
    dictionary), so it changes the moment any window is focused. Typing into
    "window 2" moments after planning could reach a different window entirely.
    """
    script = f'''
tell application "iTerm2"
    tell current session of tab {tab_index} of window id {_as_id(window_id)}
        write text "{_escape(text)}"
    end tell
end tell
'''
    _osascript(script)


def create_window(tab_count: int) -> str:
    """Open a window with `tab_count` tabs; returns its stable window id.

    The id is captured from the window reference inside the same AppleScript
    that creates it. Returning its front-to-back index instead -- and resolving
    that back to an id afterwards -- left a window in which focusing anything
    else would point the restore at a different window.
    """
    if tab_count < 1:
        raise ValueError("tab_count must be at least 1")
    extra = tab_count - 1
    script = f"""
tell application "iTerm2"
    set win to (create window with default profile)
    repeat {extra} times
        tell win to create tab with default profile
    end repeat
    return (id of win)
end tell
"""
    window_id = _osascript(script).strip()
    if not window_id:
        raise ITermError("iTerm created a window but did not report its id")
    return window_id


def create_tab(window_id: str) -> None:
    """Append one tab to an existing window, addressed by its stable id."""
    _osascript(
        f'tell application "iTerm2" to tell window id {_as_id(window_id)} '
        f"to create tab with default profile"
    )
