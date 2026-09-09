"""iTerm2 control via AppleScript.

AppleScript is used rather than the iTerm2 Python API so that the same code
path works from a plain shell and from an AutoLaunch script, with no dependency
on iTerm's bundled Python environment.
"""

from __future__ import annotations

import subprocess

FIELD = "@@|@@"


class ITermError(RuntimeError):
    pass


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ITermError(result.stderr.strip() or "osascript failed")
    return result.stdout


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
                set out to out & wi & "{FIELD}" & (id of win) & "{FIELD}" & ti & "{FIELD}" & si & "{FIELD}" & (tty of s) & "{FIELD}" & (name of s) & linefeed
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


def enumerate_tabs() -> list[dict]:
    """Every split-pane session in every window, with its tty and title."""
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


def run_in_tab(window_index: int, tab_index: int, text: str) -> None:
    """Type a command into an existing tab and press Enter."""
    script = f'''
tell application "iTerm2"
    tell current session of tab {tab_index} of window {window_index}
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


def create_tab(window_index: int) -> None:
    """Append one tab to an existing window."""
    _osascript(
        f'tell application "iTerm2" to tell window {window_index} '
        f"to create tab with default profile"
    )
