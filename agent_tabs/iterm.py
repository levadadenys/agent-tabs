"""iTerm2 control via AppleScript.

AppleScript is used rather than the iTerm2 Python API so that this works from
any context -- a plain shell, or the launchd recorder -- with no dependency on
iTerm's bundled Python runtime, which is not installed by default.
"""

from __future__ import annotations

import subprocess
import time

# ASCII unit separator: a terminal strips control characters from titles, so
# unlike a printable delimiter this cannot collide with tab content and
# silently drop a whole row.
FIELD = "\x1f"
FIELD_AS = "(character id 31)"


class ITermError(RuntimeError):
    pass


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ITermError(result.stderr.strip() or "osascript failed")
    return result.stdout


def _as_id(window_id: str) -> str:
    """Render a window id for AppleScript: numeric bare, anything else quoted."""
    text = str(window_id)
    return text if text.lstrip("-").isdigit() else f'"{_escape(text)}"'


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


ENUMERATE = f'''
tell application "iTerm2"
    set out to ""
    set wi to 0
    repeat with win in windows
        set wi to wi + 1
        set ti to 0
        repeat with tb in tabs of win
            set ti to ti + 1
            set si to 0
            repeat with s in sessions of tb
                set si to si + 1
                set out to out & wi & {FIELD_AS} & (id of win) & {FIELD_AS} & ti & {FIELD_AS} & si & {FIELD_AS} & (tty of s) & {FIELD_AS} & (name of s) & linefeed
            end repeat
        end repeat
    end repeat
    return out
end tell
'''


def is_running() -> bool:
    script = 'tell application "System Events" to return (name of processes) contains "iTerm2"'
    try:
        return _osascript(script).strip() == "true"
    except ITermError:
        return False


ENUMERATE_ATTEMPTS = 3
ENUMERATE_BACKOFF_SECONDS = 0.4


def enumerate_tabs() -> list[dict]:
    """Every split-pane session in every window, with its tty and title.

    Retries a few times: closing a tab while AppleScript is walking the window
    list raises "Invalid index (-1719)" and loses the whole capture. Observed in
    practice, not theoretical. A lost capture is cheap on any single tick and
    expensive if it is the last one before a crash.
    """
    last: ITermError | None = None
    for attempt in range(ENUMERATE_ATTEMPTS):
        try:
            return _enumerate_once()
        except ITermError as exc:
            last = exc
            if attempt < ENUMERATE_ATTEMPTS - 1:
                time.sleep(ENUMERATE_BACKOFF_SECONDS)
    raise last if last else ITermError("enumeration failed")


def _enumerate_once() -> list[dict]:
    rows = []
    for line in _osascript(ENUMERATE).splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD)
        if len(parts) != 6:
            continue
        window_index, window_id, tab_index, pane_index, tty, title = parts
        rows.append(
            {
                "window_index": int(window_index),
                "window_id": window_id,
                "tab_index": int(tab_index),
                "pane_index": int(pane_index),
                "tty": tty.strip(),
                "title": title.strip(),
            }
        )
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


def create_window(tab_count: int) -> int:
    """Open a window with `tab_count` tabs; returns its 1-based window index."""
    if tab_count < 1:
        raise ValueError("tab_count must be at least 1")
    extra = tab_count - 1
    script = f'''
tell application "iTerm2"
    set win to (create window with default profile)
    repeat {extra} times
        tell win to create tab with default profile
    end repeat
    set wi to 0
    set found to 0
    repeat with w in windows
        set wi to wi + 1
        if (id of w) is (id of win) then set found to wi
    end repeat
    return found
end tell
'''
    return int(_osascript(script).strip())


def create_tab(window_id: str) -> None:
    """Append one tab to an existing window, addressed by its stable id."""
    _osascript(
        f'tell application "iTerm2" to tell window id {_as_id(window_id)} '
        f"to create tab with default profile"
    )
