# Spec: more terminals, and Linux

**Status:** design only, not started; written to decide *whether* to build this.
Written 2026-10-28, against `main` at the merge of #16, and reviewed once
against the code. Facts about other terminals come from their documentation on
that date and are cited; anything not yet confirmed by running it is marked
**verify before building**.

## Today

agent-tabs is macOS and iTerm2 only. The terminal is reached through
`agent_tabs/iterm.py`, five functions over AppleScript:

| Function | Used by | Does |
|---|---|---|
| `is_running()` | `bin/agent-tabs` | Is the terminal up at all |
| `enumerate_tabs()` | `snapshot.build`; in `restore`: `live_tabs`, `execute` (the re-check before typing), `_tty_of`, `ensure_tabs` | Every pane: window index, window **id**, tab index, pane index, tty, title |
| `run_in_tab(window_id, tab_index, text)` | `restore.execute` | Type a command into one tab and press Enter |
| `create_window(tab_count)` | `restore` (`--new-window`, `--create-missing`) | Open a window, return its id |
| `create_tab(window_id)` | `restore.ensure_tabs` | Add a tab to a window |

`iterm.py` also carries listing-safety rules that any backend inherits: tab
titles are program-controlled, so separators are cleaned out of them in the
AppleScript; output is decoded from bytes and split on `\n` only; and a
malformed or repeated row fails the whole listing.

Everything else is terminal-independent, but not platform-independent:

| macOS-specific | Where | Linux equivalent |
|---|---|---|
| `ps -Ao pid,ppid,tty,command` | `procs.process_table` | `/proc/<pid>/stat`, `/proc/<pid>/cmdline` |
| `lsof -Fpn -d cwd` | `procs.cwd_of` | `readlink /proc/<pid>/cwd` |
| `lsof -Fn -p` (open files) | `procs.open_files` (Codex, Grok) | `readlink /proc/<pid>/fd/*` |
| `sysctl kern.boottime` | `snapshot.boot_time` | `btime` in `/proc/stat` |
| launchd agent | `install` | systemd user service and timer |

(`install` also runs `ps` to stop a legacy iTerm AutoLaunch recorder. That is
macOS-only history: drop it on Linux, do not port it.)

The tty is the join key: the terminal says which tty each pane owns,
`procs.by_tty` groups processes by tty, and the agent adapters turn the
foreground process into a session id. **A terminal whose automation exposes
neither a pane's tty nor its shell's PID cannot be supported**, whatever else
it offers.

## 1. A terminal-backend interface

Extract `iterm.py` behind an interface, with iTerm2 as the only implementation
at first and no behaviour change. This is the prerequisite for everything below.

```python
class Pane(TypedDict):
    window_key: str      # stable for the window's life; never a position
    window_number: int   # 1-based display order: the N in `restore N`, and
                         # today's pairing key (window_index); see below
    tab_key: str | None  # stable id of the tab, where the backend has one
    tab_number: int      # 1-based position within the window, no gaps
    pane_number: int
    tty: str | None      # at least one of tty / pid is required
    pid: int | None      # the pane's shell, when the backend knows it
    cwd: str | None      # a plain path; backends that report URLs convert
    title: str           # program-controlled: cleaned by the backend
    busy_hint: bool      # the backend knows the pane cannot take typing
                         # (tmux copy-mode, a dead pane); joined with the
                         # process-based busy check, never replacing it

class Backend(Protocol):
    name: str                                    # recorded in the snapshot
    def is_running(self) -> bool: ...            # "not running" is a state, not an error
    def list_panes(self) -> list[Pane]: ...      # raises on a listing it cannot trust
    def own_pane(self, panes: list[Pane]) -> Pane | None: ...
    def create_window(self, tabs: int, cwd: str | None) -> str: ...   # returns a window_key
    def create_tab(self, window_key: str, cwd: str | None) -> None: ...
    def type_into(self, window_key: str, tab_number: int, tab_key: str | None,
                  text: str) -> None: ...
```

### What the safety rules need from a backend

- **Stable window identity.** `window_key`, never a position. A backend
  without stable window ids cannot be supported.
- **Tab addressing.** Today a tab is addressed by its position in a stable
  window. Where a backend has stable tab ids (tmux, WezTerm, kitty), plan by id
  and type by id. That closes the "tabs reordered between the dry run and
  `--go`" limit the README lists. Where it has none (iTerm2, Terminal.app),
  position stays.
- **Busy detection, re-checked at execute time.** `list_panes()` is called
  again just before typing (`execute`, `_tty_of` and `ensure_tabs` all
  re-list today), and joined to processes. `busy_hint` adds what only the
  terminal knows.
- **Process join.** By tty today (`procs.by_tty`). A backend that reports only
  a shell PID (kitty, possibly) needs a second join: that PID's descendants,
  from the process table. Build that before any PID-only backend.
- **Restore's own tab.** `own_pane()` is re-evaluated against each fresh
  listing, not computed once (`execute` checks it per tab). Today it is the tty
  of the standard streams, else `$TTY`. Backends that set a variable in every
  pane (`TMUX_PANE`, `WEZTERM_PANE`, `KITTY_WINDOW_ID`) can do better: a
  process can always read its *own* environment, even on macOS. `--go --force`
  is refused when `own_pane()` is None, as now.
- **Listing safety.** Titles are cleaned and parsing is strict, as in
  `iterm.py`. Any backend reading program-controlled text from a CLI's output
  (tmux `-F`, `wezterm cli list`) splits on `\n` only, rejects malformed or
  duplicate rows, and treats titles as untrusted.
- **Literal typing.** `type_into` must not let the terminal interpret the text:
  no bracketed paste, no key-name parsing, no option parsing of text starting
  with `-`. Each backend states how it guarantees that.

### Pairing moves to `window_key`

`window_index` is not only a label. Pairing (`_best_match`, `match_all`, the
launcher exclusion) and the CLI (`restore N`) are keyed on it throughout
`restore.py` and `bin/agent-tabs`. The refactor re-keys pairing internally on
`window_key` and keeps `window_number` as the number users see and type.

That touches the tests. About fifteen test files build or assert rows with
`window_index`, `window_id` or `iterm.` directly, including the
brute-force oracle. The goal is still **no behaviour change**, but the bar is
"every test's assertion is preserved, with fixtures rewritten mechanically",
not "the suite passes untouched". Estimate: one to two weeks.

The snapshot gains a `"terminal": "<backend name>"` field. A snapshot is only
restored into the same backend: `window_key`s mean nothing across terminals.
Snapshots without the field are iTerm2's.

## 2. Candidates

Effort assumes the interface exists. S is days, M about a week, L more.

### tmux: recommended first

- **Mapping:**
  - **window** = a tmux *session* (`$N`), which is what a person treats as a
    window of related work;
  - **tab** = a tmux *window* (`@N`);
  - **pane** = a tmux pane (`%N`).

  tmux window indexes have gaps (`base-index`, `move-window`), so
  `tab_number` is the ordinal position, and `@N` is the `tab_key`.
- **API:**
  - Listing: `tmux list-panes -a -F` with `#{session_id}`, `#{window_id}`,
    `#{pane_id}`, `#{pane_tty}`, `#{pane_pid}`, `#{pane_current_path}`,
    `#{pane_title}`, `#{pane_in_mode}`, `#{pane_dead}`.
  - Creating: `new-session -d -c <cwd>` and `new-window -t $N -c <cwd>`.
  - Typing: `send-keys -t %N -l -- '<text>'`, then `send-keys -t %N Enter`.
    `-l` is literal, and `--` stops text starting with `-` being read as an
    option.
  - `own_pane()` from `$TMUX_PANE`.
- **Feasibility:** high. Every field is there and there are no permission
  prompts. It also runs headless, so it can be tested for real in CI, on the
  existing macOS runners or on Linux. A Linux job would be a second workflow,
  so that part is a cost saving, not a necessity.
- **What it brings:** Linux (with the `/proc` layer below), any terminal
  emulator on top, and remote machines over ssh. Panes are first-class, so
  split panes could be recreated, which iTerm2 cannot do here.
- **What breaks or changes:**
  - **No server is a state.** `tmux list-panes` errors when no server runs;
    `is_running()` must report that, not raise.
  - **After a reboot the server is gone**, so every tmux restore goes through
    `--new-window` or `--create-missing`. Sessions created by restore are
    detached, so restore must print `tmux attach -t <session>`; nothing appears
    on screen by itself.
  - **A tmux server survives a terminal crash**, so the crash case shrinks to a
    reboot or a killed server.
  - **A pane in copy-mode or a dead pane** interprets typed keys itself, so
    `pane_in_mode` and `pane_dead` are `busy_hint`.
  - **Overlap with [tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect)
    and tmux-continuum.** They restore layouts and programs, but not agent
    sessions by id. agent-tabs' contribution is the session resolution and the
    safety rules; the README must say that plainly.
  - **Window titles are often the running command**, so title pairing is
    weak. The session name is the better pairing key.
  - **Ops:** the recorder runs with launchd's minimal PATH, so `tmux` is called
    by absolute path. Tests use a private server (`tmux -L <name>`), never the
    developer's.
- **Effort:** M for the backend, plus M for the Linux process layer if Linux is
  in scope.

### WezTerm

- **API:**
  - Listing: `wezterm cli list --format json`
    ([docs](https://wezterm.org/cli/cli/list.html)) gives `window_id`,
    `tab_id`, `pane_id`, `cwd`, `title` and `tty_name` per pane.
  - Creating: `wezterm cli spawn --new-window --cwd`
    ([docs](https://wezterm.org/cli/cli/spawn.html)). It prints the new
    **pane** id, so `create_window` maps it to its window through a fresh
    listing.
  - Typing: `wezterm cli send-text --pane-id N --no-paste`.
  - `own_pane()` from `$WEZTERM_PANE`.
- **Feasibility:** high, on macOS and Linux.
- **Verify before building:**
  - `tty_name` is optional in the JSON (one client declares it
    `Option<String>`). Find out when it is absent.
  - `cwd` is likely a `file://` URL to convert.
  - Whether `wezterm cli` reaches the GUI from a launchd or systemd job, which
    has no `WEZTERM_UNIX_SOCKET` in its environment.
  - That `--no-paste` sends the text literally, and that Enter needs an explicit
    `\r`.
- **Effort:** S–M.

### kitty

- **API:** remote control ([docs](https://sw.kovidgoyal.net/kitty/remote-control/)).
  - Listing: `kitten @ ls` returns JSON, OS windows → tabs → windows, each
    window with an `id`, title, cwd, pid and command line.
  - Creating: `kitten @ launch --type=os-window|tab --cwd` prints the new
    window's id.
  - Typing: `kitten @ send-text --match id:N`.
  - `own_pane()` from `$KITTY_WINDOW_ID`.
- **Feasibility:** medium. Remote control is off by default. A recorder outside
  kitty needs the user to set `allow_remote_control` and a `listen_on` socket.
  kitty's docs call plain `allow_remote_control` "a blunt instrument" that lets
  any local program drive the terminal. The password mode (since 0.26.0) can be
  scoped to specific actions, which suits a recorder that mostly reads.
- **Verify before building:**
  - Whether `kitten @` can pass the password non-interactively
    (`--password-file` / `--password-env`), and whether a password can be scoped
    to `ls` plus the typing actions.
  - The exact JSON field names, including `is_self` and `foreground_processes`,
    which the prose docs do not spell out.
  - Whether `send-text` reports a missing window: the docs say errors are not
    reported.
  - The process join is by **pid**: see "Process join" above.
- **Effort:** S–M for the code, plus documentation of the trade-off.

### Terminal.app

- **API:** AppleScript, verified locally with `sdef`. A tab has `tty`, `busy`,
  `processes` and `custom title`; a window has an integer `id`; `do script` runs a
  command in a tab. Tabs have no id, so they are addressed by position, as in
  iTerm2.
- **Feasibility:** medium. The listing side matches iTerm2 almost one to one,
  with the tty as the same join key.
- **What breaks:**
  - **Creating a tab in a given window is uncertain.** The dictionary has only
    the generic `make`, and users report that `make new tab` fails. A bare
    `do script` opens a new window or tab depending on a system preference.
    UI scripting (⌘T through System Events) works, but it needs the
    Accessibility permission and fails silently without it. Verify
    `make new tab` before building.
  - `do script` *runs* text rather than typing it into the tab's shell. Check how
    it behaves on a busy tab.
- **Effort:** M, most of it in tab creation.

### Ghostty

- **API:** AppleScript since 1.3.0 ([docs](https://ghostty.org/docs/features/applescript)).
  - windows → tabs → terminals, each with an `id`;
  - terminals expose `working directory` and `name`;
  - `new window` / `new tab … with configuration` (`initial working directory`);
  - `input text … to <terminal>` and `send key`.
- **Feasibility:** **blocked as documented.** The docs list no tty and no PID
  for a terminal, so there is no join from a terminal to its process, and
  nothing can identify the agent in a tab or tell whether it is busy. Working
  directory alone is not a key: several tabs often share one.
- **Verify before building:** whether the dictionary in a current build
  (`sdef`) has a tty or pid the web docs omit (tip builds are still extending
  it), and whether ids are stable.
- **Unblock:** if not, ask upstream to expose `tty` or the shell's pid on the
  terminal class.
- **Effort:** S once a tty or pid exists.

## 3. Process lookup on Linux

`procs.py` is the module that changes. `/proc` replaces both `ps` and `lsof`:

- **Process tree:** read `/proc/<pid>/stat`. Parse after the *last* `)`, since
  the command name can contain spaces and parentheses. It gives the ppid,
  `tty_nr` and `tpgid` (the foreground process group, which identifies the
  foreground process more directly than today's walk down from the shell).
  `tty_nr` is a device number, not a name. Map it through its major and minor
  numbers to `pts/N`, or read the name from `readlink /proc/<pid>/fd/0`.
- **Argv:** read from `/proc/<pid>/cmdline`, which is NUL-separated, so argument
  boundaries survive. They cannot be *replayed* as such, though. Restore types
  one line into a shell, preserving boundaries would need quoting, and the
  shell-syntax filter rejects quotes. An argument containing a space still
  downgrades the tab to a bare `cd`; the gain is that nothing is mis-split.
  `Proc.command` becomes a list, with every user of `.command` updated.
- **No line-forgery class:** NUL-separated argv makes the line-separator
  forgery fixed in #11 impossible by construction.
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

1. **The interface, with iTerm2 behind it.** No behaviour change; every test
   assertion preserved; pairing re-keyed on `window_key`. One to two weeks.
2. **tmux, on macOS first.** It reuses the macOS process layer (`pane_tty` is
   a `/dev/ttysNNN` that `normalise_tty` already handles). It can be tested for
   real in CI with no display, brings split-pane recreation and stable tab
   ids, and needs no new permission model.
3. **The Linux process layer and a systemd recorder.** Together with tmux, this
   is Linux support: the largest new audience for the least new surface.
4. **WezTerm**, once its open questions are answered. It is the best-documented
   GUI candidate and runs on both platforms.
5. **kitty**, with its remote-control trade-off documented and opted into, and
   the pid join built first.
6. **Terminal.app**, if users ask, and if `make new tab` works.
7. **Ghostty**, when it exposes a tty or pid. Worth an upstream request now.

**Do not build** a backend that cannot give a stable window id, a pane-to-
process join, and literal typing into a chosen pane. Every safety rule rests on
those three.

## Decision needed

Whether to do step 1 at all. It costs one to two weeks, changes no behaviour,
and is only worth it if at least tmux follows. If agent-tabs stays an iTerm2
tool, this spec is the record of why the others were not pursued.
