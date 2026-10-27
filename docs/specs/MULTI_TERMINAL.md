# Spec: more terminals, and Linux

**Status:** design only, not started; written to decide *whether* to build this.
Written 2026-10-28, against `main` at the merge of #16. Facts about other
terminals come from their documentation on that date and are cited; anything
not yet confirmed by running it is marked **verify before building**.

## Today

agent-tabs is macOS and iTerm2 only. The terminal is reached through
`agent_tabs/iterm.py`, five functions over AppleScript:

| Function | Used by | Does |
|---|---|---|
| `is_running()` | `bin/agent-tabs` | Is the terminal up at all |
| `enumerate_tabs()` | `snapshot.build`, `restore.live_tabs` | Every pane: window index, window **id**, tab index, pane index, tty, title |
| `run_in_tab(window_id, tab_index, text)` | `restore.execute` | Type a command into one tab and press Enter |
| `create_window(tab_count)` | `restore` (`--new-window`, `--create-missing`) | Open a window, return its id |
| `create_tab(window_id)` | `restore.ensure_tabs` | Add a tab to a window |

Everything else is terminal-independent, but not platform-independent:

| macOS-specific | Where | Linux equivalent |
|---|---|---|
| `ps -Ao pid,ppid,tty,command` | `procs.process_table`, `install` | `/proc/<pid>/stat` (ppid, tty_nr), `/proc/<pid>/cmdline` |
| `lsof -Fpn -d cwd` | `procs.cwd_of` | `readlink /proc/<pid>/cwd` |
| `lsof -Fn -p` (open files) | `procs.open_files` (Codex, Grok) | `readlink /proc/<pid>/fd/*` |
| `sysctl kern.boottime` | `snapshot.boot_time` | `btime` in `/proc/stat` |
| launchd agent | `install` | systemd user timer, or a cron `@reboot` loop |

The tty is the join key: the terminal says which tty each pane owns, `ps`
says which process runs on that tty, and the agent adapters turn that process
into a session id. **A terminal whose automation does not expose a pane's tty
or PID cannot be supported**, whatever else it offers.

## 1. A terminal-backend interface

Extract `iterm.py` behind an interface, iTerm2 as the only implementation at
first, with no behaviour change. This is the prerequisite for everything
below and is worth doing on its own: it makes the safety rules' dependencies
on the terminal explicit.

```python
class Pane(TypedDict):
    window_key: str      # stable for the window's life; never a position
    window_order: int    # display order, for humans only
    tab_index: int
    pane_index: int
    tty: str | None      # one of tty / pid is required (the join key)
    pid: int | None      # the pane's shell, when the backend knows it
    cwd: str | None      # when the backend knows it; else from the process
    title: str

class Backend(Protocol):
    name: str                                   # recorded in the snapshot
    def is_running(self) -> bool: ...
    def list_panes(self) -> list[Pane]: ...     # raises on a listing it cannot trust
    def own_pane(self, panes: list[Pane]) -> Pane | None: ...
    def create_window(self, tabs: int, cwd: str | None) -> str: ...
    def create_tab(self, window_key: str, cwd: str | None) -> None: ...
    def type_into(self, window_key: str, tab_index: int, text: str) -> None: ...
```

What each safety rule in `AGENTS.md` needs from a backend:

- **Target windows by a stable id, never position** → `window_key`. A backend
  without stable window ids cannot be supported.
- **Never type into a busy tab, re-checked at execute time** → `list_panes()`
  again before typing, joined to processes by tty or pid.
- **Never type into the tab restore runs from; refuse `--force` if that tab
  cannot be found** → `own_pane()`. Today that is the tty of the standard
  streams, else `$TTY`. Several
  backends do better: they set an environment variable in every pane
  (`TMUX_PANE`, `WEZTERM_PANE`, `KITTY_WINDOW_ID`), and a process can always
  read *its own* environment, even on macOS.
- **A listing that cannot be trusted fails** → `list_panes()` raises rather
  than skipping rows, as `iterm._enumerate_once` does now.
- **Typed text is literal** → `type_into` must not let the terminal interpret
  the text: no bracketed-paste surprises, no key-name parsing. Each backend
  states how it guarantees that.

The snapshot gains a `"terminal": "<backend name>"` field. A snapshot is only
restored into the same backend: `window_key`s mean nothing across terminals,
and title pairing assumes the same title conventions. Snapshots without the
field are iTerm2's.

## 2. Candidates

Effort assumes the interface above exists. S is days, M about a week, L more.

### tmux: recommended first

- **API:** the `tmux` command line, stable for years.
  `list-panes -a -F '#{window_id} #{window_index} #{pane_index} #{pane_tty} #{pane_pid} #{pane_current_path} #{pane_title}'`
  lists every pane with a tty, a pid and the cwd. `@N` window ids and `%N` pane ids
  are stable for the server's life. `new-window -c <cwd>`, `split-window`,
  `send-keys -t %N -l '<text>'` (`-l`: literal) then `send-keys -t %N Enter`.
  `own_pane()` from `$TMUX_PANE`.
- **Feasibility:** high. Every field the interface needs is there, no
  permission prompts, and it runs headless, so **the backend can be tested for
  real in CI on cheap Linux runners**, which no GUI terminal allows.
- **What it brings:** Linux (with the `/proc` layer below), any terminal
  emulator on top, and remote machines over ssh. Panes are first-class, so
  **split panes could be recreated**, which iTerm2 cannot do here.
- **What breaks or changes:**
  - A tmux server survives a terminal crash, so the crash case shrinks to a
    reboot or a killed server.
  - The overlap with [tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect)
    and tmux-continuum is real. They restore layouts and programs, but not
    agent sessions by id. agent-tabs' contribution is the session resolution
    and the safety rules; that has to be stated plainly, or the backend looks
    redundant.
  - Window titles are often the running command, so title pairing is weaker.
    Pairing by `session:window` name is the better key.
- **Effort:** M for the backend, plus M for the Linux process layer if Linux is
  in scope.

### WezTerm

- **API:** `wezterm cli list --format json` ([docs](https://wezterm.org/cli/cli/list.html))
  gives `window_id`, `tab_id`, `pane_id`, `cwd`, `title` and `tty_name` per pane;
  `wezterm cli spawn --new-window --cwd` ([docs](https://wezterm.org/cli/cli/spawn.html))
  prints the new pane id; `wezterm cli send-text --pane-id N --no-paste`
  types into a pane. `own_pane()` from `$WEZTERM_PANE`.
- **Feasibility:** high, macOS and Linux.
- **Verify before building:**
  - `tty_name` is optional in the JSON, as one client declares it
    `Option<String>`. Find out when it is absent.
  - Whether `wezterm cli` reaches the GUI from a launchd or systemd job,
    which has no `WEZTERM_UNIX_SOCKET` in its environment.
  - That `--no-paste` sends the text literally.
- **Effort:** S–M.

### kitty

- **API:** remote control ([docs](https://sw.kovidgoyal.net/kitty/remote-control/)).
  `kitten @ ls` returns JSON: OS windows → tabs → windows, each window with an
  `id`, title, cwd, pid and command line. `kitten @ launch --type=os-window|tab --cwd`
  prints the new window's id. `kitten @ send-text --match id:N`.
  `own_pane()` from `$KITTY_WINDOW_ID`.
- **Feasibility:** medium.
  - Remote control is off by default. A recorder outside kitty needs the user
    to set `allow_remote_control` and a `listen_on` socket.
  - kitty's own docs call `allow_remote_control` "a blunt instrument" that lets
    any local program drive the terminal. A password-scoped mode exists since
    0.26.0, but it prompts interactively, which a background recorder cannot
    answer.
  - Asking users to weaken their terminal's security for a recovery tool is a
    real cost, and the README would have to say so.
- **Verify before building:** the exact JSON field names (`pid`, `cwd`,
  `cmdline`, and whether `foreground_processes` exists), which the prose docs
  do not spell out. And whether `send-text` errors on a missing window: the docs
  say errors are not reported.
- **Effort:** S–M for the code, plus documentation of the trade-off.

### Terminal.app

- **API:** AppleScript, verified locally with `sdef`. A tab has `tty`, `busy`,
  `processes` and `custom title`; a window has an integer `id`; `do script` runs a
  command in a tab.
- **Feasibility:** medium. The listing side matches iTerm2 almost one to one,
  since tty is the same join key.
- **What breaks:**
  - **There is no command to create a tab.** A bare `do script` opens a new
    window or tab depending on a system preference, and adding a tab to a given
    window needs UI scripting (System Events ⌘T). That requires the
    Accessibility permission, and fails silently without it.
  - `--new-window` and `--create-missing` would therefore build windows one tab
    at a time, or not be offered.
  - `do script` *runs* text instead of typing it. Check whether it can target a
    busy tab, and how it interacts with the busy check.
- **Effort:** M, most of it in tab creation.

### Ghostty

- **API:** AppleScript since 1.3.0 ([docs](https://ghostty.org/docs/features/applescript)):
  windows → tabs → terminals, each with an `id`; terminals expose `working
  directory` and `name`; `new window` / `new tab … with configuration`
  (`initial working directory`); `input text … to <terminal>` and `send key`.
- **Feasibility:** **blocked.** The docs list no tty and no PID for a terminal,
  so there is no join from a terminal to the process running in it. Without
  that, nothing can identify the agent in a tab or tell whether it is busy.
  Working directory alone is not a key: several tabs often share one.
- **Unblock:** ask upstream to expose `tty` (or the shell's pid) on the
  terminal class. Recent tip builds are still extending the dictionary. The
  docs also do not say whether ids are stable; **verify before building**.
- **Effort:** S once a tty or pid exists, given the iTerm2 code it would mirror.

## 3. Process lookup on Linux

`procs.py` is the only module that would change, and `/proc` is better than
`ps`:

- **Argv:** read from `/proc/<pid>/cmdline`, which is NUL-separated. That
  keeps argument boundaries, which `ps` loses (`tail -f "my file"` replays as
  two arguments today). It also makes the line-separator forgery fixed in #11
  impossible by construction.
- **Process tree:** `/proc/<pid>/stat` has the ppid, `tty_nr` and `tpgid`, the
  foreground process group, which identifies the process in the foreground
  more directly than today's walk from the shell down.
- **cwd and open files:** `readlink` of `/proc/<pid>/cwd` and
  `/proc/<pid>/fd/*` replace both `lsof` calls, with no subprocess.
- **Boot time:** `btime` in `/proc/stat`.
- **Recorder:** a systemd user service and timer, written by `install` the
  way the launchd plist is now. Same robustness rules: an absolute
  interpreter path, and say when the job did not load.
- **Agents:** confirm where each CLI keeps sessions on Linux. The adapters
  assume `~/.claude/sessions/<pid>.json`, Codex rollout files under
  `CODEX_HOME`, and `~/.grok/sessions/`. **Verify before building.**

Selection is by platform (`sys.platform`), not by probing for tools, so a test
cannot pass on one layer while production uses the other.

## 4. Recommended order

1. **The interface, with iTerm2 behind it.** No behaviour change. The current
   suite is the contract: it must pass untouched except for import paths.
2. **tmux, on macOS first.** It is the only backend testable for real in CI
   without a display. It brings split-pane recreation, it needs no new
   permission model, and it reuses the macOS process layer unchanged.
3. **The Linux process layer and a systemd recorder.** Together with tmux, this
   is Linux support: the largest new audience for the least new surface.
4. **WezTerm**, once its three verify-before-building questions are answered.
   It is the best-documented GUI candidate and runs on both platforms.
5. **kitty**, only with the security trade-off documented and the user opting
   in.
6. **Terminal.app**, if users ask. Built in to macOS, but tab creation needs a
   permission that fails silently.
7. **Ghostty**, when it exposes a tty or pid. Worth an upstream request now.

**Do not build:** a backend that cannot give a stable window id, a pane-to-
process join, and literal typing into a chosen pane. Every safety rule rests on
those three.

## Decision needed

Whether to do step 1 at all. It costs about a week, changes no behaviour, and
is only worth it if at least tmux follows. If agent-tabs stays an iTerm2 tool,
this spec is the record of why the others were not pursued.
