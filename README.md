# agent-tabs

Get every terminal window full of agent sessions back after a crash or reboot.

Claude, Codex and Grok keep their transcripts on disk, so a crash never loses a
conversation. What it loses is the **grouping**: which session was in which
iTerm window and tab, and in what working directory. Nothing records that, so
recovery normally means guessing from file timestamps.

`agent-tabs` records the grouping every 30 seconds while you work, and replays it
afterwards.

## After a crash or reboot

Open any iTerm tab and check what was recorded:

```bash
agent-tabs list
```

The first line shows when the snapshot was taken. It should be from just before
the crash or reboot. If it says hours or days, the recorder was not running —
see [Troubleshooting](#troubleshooting) before restoring.

Then it depends on whether iTerm brought your windows back.

**iTerm restored your windows** (tick *Reopen windows when logging back in* when
you shut down, and it will):

```bash
agent-tabs restore --all        # dry run: the plan for every window
agent-tabs restore --all --go   # start the sessions
```

Each live window is paired with the snapshot window it came from, by tab title.

**iTerm opened no windows**, or only some:

```bash
agent-tabs restore --all --create-missing        # dry run
agent-tabs restore --all --create-missing --go   # rebuild them
```

`--create-missing` rebuilds, in a new window, every snapshot window that no live
window matched. Windows that did come back are still restored in place.

**Restore before starting new sessions.** Everything is a dry run until you add
`--go`, and a session that is already running anywhere is never started twice,
so it is safe to run the dry run first and read it.

## Install

```bash
git clone https://github.com/<you>/agent-tabs.git
cd agent-tabs && ./bin/agent-tabs install
```

That:

- installs the recorder as a **launchd agent**, capturing every 30 seconds;
- links `agent-tabs` into `~/.local/bin`;
- links a `resume-tabs` skill into `~/.claude/skills`, so you can ask Claude to
  restore your tabs.

No iTerm restart is needed, and recording continues across iTerm restarts.
Check it with `agent-tabs list`: the snapshot should be seconds old.

`install` exits non-zero if the recorder fails to load, so a script can rely on
its exit code. An earlier version ran the recorder from iTerm2's
`Scripts/AutoLaunch/` folder; that needed iTerm's Python runtime, which is not
installed by default, so it could fail silently. `install` removes that old
entry when it finds one.

### Stop or uninstall

```bash
launchctl list | grep agent-tabs                    # is the recorder registered?
launchctl bootout gui/$UID/com.agent-tabs.recorder   # stop it until next login
```

`bootout` only lasts until the next login: the job is loaded again from
`~/Library/LaunchAgents`. To remove it for good:

```bash
launchctl bootout gui/$UID/com.agent-tabs.recorder
rm ~/Library/LaunchAgents/com.agent-tabs.recorder.plist
rm ~/.local/bin/agent-tabs ~/.claude/skills/resume-tabs
rm -rf ~/.agent-tabs                                  # snapshots and history
```

## Commands

```bash
agent-tabs snapshot                       # capture now (the recorder does this every 30s)
agent-tabs list                           # what the snapshot holds, with restore commands
agent-tabs list --kind codex              # only one kind of tab (filters on kind, not title)
agent-tabs list --commands                # bare restore commands, one per line
agent-tabs list -f <file>                 # read a specific snapshot, e.g. from history

agent-tabs restore --all                  # every window at once (dry run)
agent-tabs restore --all --create-missing # ...and rebuild windows iTerm did not restore
agent-tabs restore                        # just the window you are in (dry run)
agent-tabs restore 2                      # snapshot window 2 into the window you are in
agent-tabs restore 2 --new-window         # snapshot window 2 into a new window
agent-tabs restore ... --go               # actually do it
agent-tabs restore ... -f <file>          # restore from a specific snapshot file
```

`restore` without `--all` or `--new-window` fills the iTerm window it is run
from, so run it from inside that window. `--all` and `--new-window` can be run
from any tab.

## What gets restored

A snapshot joins three sources:

| Source | Gives |
|---|---|
| iTerm2 AppleScript | window → tab → tty, plus the tab title |
| `ps` | tty → the process running in that tab |
| per-agent lookup | that process → session id + working directory |

| Agent | Identified by | Restored with |
|---|---|---|
| Claude | `~/.claude/sessions/<pid>.json` | `claude --resume <uuid>` |
| Codex | the rollout transcript it holds open (`lsof`) | `codex resume <uuid>` |
| Grok | open files under `~/.grok/sessions/<urlencoded-cwd>/<uuid>/` | `grok --resume <uuid>` |
| ssh / other | the command line | replayed, unless it contains shell syntax |
| plain shell | working directory only | `cd <cwd>` |

Every restore command is `cd <cwd> && <resume>`, never the resume alone: all
three agents scope their session pickers to the working directory, so a resume
run from the wrong directory reports the session as missing.

## Safety

Restore types into live shells, so it is conservative by default:

- **Dry run** unless you pass `--go`.
- **Never types into a tab doing something** — an agent, but equally `vim`,
  `psql` or an `ssh` session, where typed text would run on the remote host.
  Override with `--force`. It also never types into the tab it runs from.
  Liveness is checked again immediately before typing, not only when planning.
- **Never starts a session that is already running** anywhere, in any window
  — nor twice in one restore, if the snapshot lists it in two tabs. `--force`
  does not override this: it is about busy tabs, and a duplicate session is
  never wanted.
- **Never replays shell syntax.** A recorded command line containing it is
  reported and downgraded to a bare `cd`, because argv is data any local process
  controls and restore can run unattended.
- **Never creates windows unless asked**, with `--create-missing` or
  `--new-window`.

### How `--all` pairs windows

A wrong pairing types one window's sessions into another, so:

- windows are matched by how many tab titles they share, as a one-to-one
  assignment that maximises total overlap — a snapshot window goes to at most
  one live window;
- a pairing is used only when it is **forced**: the same in every best
  assignment. If the evidence fits two pairings equally well, those windows are
  reported, never guessed;
- a group of more than 12 windows all sharing titles is reported rather than
  matched;
- windows are targeted by iTerm's stable window id, not their position, which
  changes whenever you focus a window.

Anything not paired is listed with the command that restores it.

## Snapshots and reboots

- `~/.agent-tabs/snapshot.json` — the snapshot `list` and `restore` use
- `~/.agent-tabs/history/` — the last 60 captures, about half an hour
- `~/.agent-tabs/pre-boot.json` — the last snapshot from before the most recent reboot
- `~/.agent-tabs/recorder.log` — recorder errors

A capture is not always promoted to `snapshot.json`, because the moment a
snapshot matters most is a shutdown, when tabs die one at a time:

- a capture with **no** agent sessions is never promoted;
- a capture that **collapsed** — kept less than 67% of the last good one's
  sessions — is held back until the drop has lasted three captures and at least
  45 seconds, about a minute in practice. A shutdown is over long before that.
  Genuinely closing most of your sessions is accepted after that minute; closing
  a tab or two is promoted immediately.

**Across a reboot**, the first promotion after boot copies the outgoing snapshot
to `pre-boot.json`. That matters if iTerm restored nothing and you started a
session before restoring: that small capture replaces `snapshot.json` about a
minute later, and history rotates the full one out within half an hour. When
that has happened, `list` and `restore` say so and print the command to restore
the pre-reboot fleet instead:

```bash
agent-tabs restore --all --create-missing -f ~/.agent-tabs/pre-boot.json
```

Any capture in `history/` can be restored the same way with `-f`.

## Troubleshooting

**The snapshot is hours or days old.** The recorder is not running. Check
`launchctl list | grep agent-tabs` and `tail ~/.agent-tabs/recorder.log`, then run
`agent-tabs install` again from the repository. If the log shows AppleScript
errors, check that your terminal is allowed to control iTerm under System
Settings → Privacy & Security → Automation.

**"Could not auto-match this window."** Plain `restore` could not tell which
snapshot window this is. Run `agent-tabs list` and pass the number, or use
`--all`.

**"Not running inside an iTerm tab."** Plain `restore` fills the window it runs
in. Run it from an iTerm tab, or use `--all` or `--new-window`.

**A window was not restored.** `restore --all` lists why under *Not restored*,
with the command for each: ambiguous, too many windows sharing titles, or no
live window matched. `--create-missing` rebuilds the last kind.

**A tab says "session already running in …".** It is: restoring it would start a
duplicate. Nothing to do. "Same session as tab N of snapshot window W, started
once" means the snapshot listed that session twice; it is restored once.

## Config

Optional, in `~/.agent-tabs/config.json`, outside the repository; see
`config.example.json`. `codex_profiles` maps an absolute `CODEX_HOME` to the
command that selects that Codex account, for people running more than one:

```json
{"codex_profiles": {"/Users/you/.codex-work": "codex work"}}
```

Without an entry, Codex is resumed with plain `codex`. `AGENT_TABS_HOME`
relocates `~/.agent-tabs`, mainly for testing.

## Limits

- macOS and iTerm2 only.
- Split panes are recorded, but only the first pane of each tab is recreated.
- It only helps for crashes **after** it is installed. For an earlier one, see
  the manual notes in `skills/resume-tabs/SKILL.md`.
- A deleted git worktree must be recreated at its old path before a session that
  ran inside it can be resumed.
- Pairing is by tab title, so one shared generic title (say `zsh`) is enough to
  pair two windows when nothing else competes.
- Tab positions are read when planning. Reordering tabs *within* a window
  between the dry run and `--go` could put a command in the wrong tab of the
  right window.

**What has been exercised for real:** a genuine crash has been restored from,
window by window. `--all`, `--create-missing` and the pre-reboot copy are covered
by tests and by dry runs against a live fleet, but not yet through a real
reboot. Run the dry run first and read it.

## Tests

```bash
python3 tests/run_all.py
```

No test framework and no dependencies. The tests drive real call sites where
the mechanism is the point — the Codex account test spawns a real process
holding a real rollout file and resolves it through a real `lsof`, and the
snapshot tests exercise actual filesystem state. The window matcher is also
checked against an independent brute-force oracle on hundreds of random
instances. Each test was mutation-checked: reverting the fix it covers makes it
fail.

Each module runs with a private, empty bytecode cache (`PYTHONPYCACHEPREFIX`).
When mutating by hand, an edit of the same byte length restored with a matching
mtime passes Python's mtime+size check, and the tests silently keep running the
stale mutant. `-B` does **not** prevent this — it only stops *writing* `.pyc`
files. Outside this runner, set `PYTHONPYCACHEPREFIX=$(mktemp -d)`.
