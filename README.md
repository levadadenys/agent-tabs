# agent-tabs

Restore a window full of agent sessions after a crash.

Claude, Codex and Grok all keep their transcripts on disk, so a crash never
loses a conversation. What it loses is the **grouping** — which session was in
which iTerm window and tab, and in what working directory. Nothing records that,
which is why recovery normally means guessing from file timestamps.

`agent-tabs` records it while things are running, and replays it afterwards.

## How it works

A snapshot joins three sources:

| Source | Gives |
|---|---|
| iTerm2 AppleScript | window → tab → tty, plus the tab title |
| `ps` | tty → the process running in that tab |
| per-agent lookup | that process → session id + working directory |

Per-agent session lookup:

| Agent | Identified by | Resumed with |
|---|---|---|
| Claude | `~/.claude/sessions/<pid>.json` | `claude --resume <uuid>` |
| Codex | the rollout transcript it holds open (`lsof`) | `codex resume <uuid>` |
| Grok | open files under `~/.grok/sessions/<urlencoded-cwd>/<uuid>/` | `grok --resume <uuid>` |
| ssh / other | the command line itself | replayed verbatim |
| plain shell | working directory only | `cd <cwd>` |

Every restore command is `cd <cwd> && <resume>`, never the resume alone: all
three agents scope their session pickers to the working directory, so a resume
run from the wrong directory reports the session as missing.

## Install

```bash
git clone https://github.com/<you>/agent-tabs.git
cd agent-tabs && ./bin/agent-tabs install
```

That does three things:

- installs the recorder as a **launchd agent** that captures every 30s
- links `agent-tabs` into `~/.local/bin`
- links a `resume-tabs` skill into `~/.claude/skills`

No iTerm restart is needed, and recording continues across iTerm restarts.
Verify with `agent-tabs list` — the timestamp should be seconds old.

```bash
launchctl list | grep agent-tabs                      # is it registered
launchctl bootout gui/$UID/com.agent-tabs.recorder     # stop it
```

An earlier version ran the recorder from iTerm2's `Scripts/AutoLaunch/` folder.
That needed iTerm's Python runtime, which is not installed by default, so it
could fail silently — and did, for six days. `install` removes that old symlink
if it finds one.

## Use

```bash
agent-tabs list                 # what the last snapshot holds
agent-tabs list --kind codex    # only Codex tabs (filters on kind, not title)
agent-tabs list --commands      # bare restore commands, one per line
agent-tabs restore              # dry run, auto-matching the current window
agent-tabs restore 2            # dry run for snapshot window 2
agent-tabs restore 2 --go       # actually start the tabs
agent-tabs restore 2 --go --new-window
```

Restore is a **dry run by default**. It refuses to write into a tab that holds
any live process -- an agent, but equally a `vim`, `psql` or `ssh` session
(override with `--force`) -- and never writes into the tab you are running it
from. Typing into a live `ssh` session would run the text on the remote host,
so "live" deliberately means more than just the agent CLIs.

A recorded command line is only replayed when it is a plain argument list.
Anything containing shell syntax is reported but downgraded to a bare `cd`,
because argv is data any local process controls and restore runs unattended.

## Config

Everything installation-specific lives in `~/.agent-tabs/config.json`, outside
the repository. It is optional; see `config.example.json`.

Two environment variables override paths, mainly for testing:
`AGENT_TABS_HOME` relocates the state directory, and `AGENT_TABS_CLI` tells the
recorder where the CLI lives.

`codex_profiles` maps an absolute `CODEX_HOME` to the command that selects that
Codex account, for people running more than one:

```json
{"codex_profiles": {"/Users/you/.codex-work": "codex work"}}
```

Without an entry, Codex is resumed with plain `codex`.

## State

- `~/.agent-tabs/snapshot.json` — latest good snapshot
- `~/.agent-tabs/history/` — last 60 captures
- `~/.agent-tabs/recorder.log` — recorder errors

Promotion to `snapshot.json` is guarded twice, because the moment a snapshot
matters most is a reboot, and during shutdown tabs die one at a time:

- a capture with no agent sessions is never promoted;
- a capture that has collapsed relative to the last good one — keeping less
  than `COLLAPSE_RATIO` of its sessions — is not promoted until it repeats
  `COLLAPSE_STREAK` times. A shutdown is over long before that, so it cannot
  overwrite a full snapshot; genuinely closing most of your sessions is
  accepted about 90 seconds later.

Closing a tab or two is an ordinary change and promotes immediately.

## Rebooting

Nothing to do. The recorder is a launchd agent, so it comes back at login and
resumes capturing. Tick **Reopen windows when logging back in** at shutdown and
iTerm restores the window and tab layout; then `agent-tabs restore --go` in each
window refills them.

If a snapshot ever does look wrong, `~/.agent-tabs/history/` keeps the last 60
captures and both `list` and `restore` accept `-f <file>`.

## Limits

- iTerm2 only, macOS only.
- Split panes are recorded but only the first pane of each tab is recreated.
- Only helps for crashes **after** it is installed. For an earlier crash, see
  the manual reconstruction notes in `skills/resume-tabs/SKILL.md`.
- A deleted git worktree must be recreated at its exact old path before a
  session that ran inside it can be resumed.

## Tests

```bash
python3 tests/run_all.py
```

Each module runs with a private, empty bytecode cache
(`PYTHONPYCACHEPREFIX`). That matters for mutation testing: edit a file, run the
tests, restore it — and if the edit was the same byte length with a matching
mtime, Python's mtime+size check passes and the tests keep running the stale
mutant. Seen in practice, with the source reading `ENUMERATE_ATTEMPTS = 3` while
Python had loaded `1`.

`-B` does **not** fix this. It only stops Python *writing* `.pyc` files; import
still reads an existing valid one, and the stale value is still loaded —
verified. When mutating by hand outside this runner, set
`PYTHONPYCACHEPREFIX=$(mktemp -d)` or delete `__pycache__`.

No test framework and no dependencies. The tests deliberately drive real call
sites: the Codex account test spawns a real process holding a real rollout file
and resolves it through a real `lsof`, and the snapshot tests exercise actual
filesystem state. Each was mutation-checked -- reverting the fix it covers makes
it fail -- because a test that passes against broken code is worse than none.
