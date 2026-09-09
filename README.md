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
| Grok | open files under `~/.grok/sessions/<cwd>/<uuid>/` | `grok --resume <uuid>` |
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

That symlinks three things:

- the recorder into iTerm2's `Scripts/AutoLaunch/`, so it runs for exactly as
  long as iTerm is open — no launchd plist, no daemon when iTerm is closed
- `agent-tabs` into `~/.local/bin`
- a `resume-tabs` skill into `~/.claude/skills`

Restart iTerm2 to start recording. Verify with `agent-tabs list`.

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

Restore is a **dry run by default**. It refuses to write into a tab that is
already running an agent (override with `--force`) and never writes into the tab
you are running it from.

## Config

Everything installation-specific lives in `~/.agent-tabs/config.json`, outside
the repository. It is optional; see `config.example.json`.

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

A capture with zero agent sessions is archived to history but never promoted to
`snapshot.json`, so a capture taken while iTerm is shutting down cannot
overwrite a good snapshot with an empty one.

## Limits

- iTerm2 only, macOS only.
- Split panes are recorded but only the first pane of each tab is recreated.
- Only helps for crashes **after** it is installed. For an earlier crash, see
  the manual reconstruction notes in `skills/resume-tabs/SKILL.md`.
- A deleted git worktree must be recreated at its exact old path before a
  session that ran inside it can be resumed.

## Tests

```bash
python3 tests/test_restore_plan.py
```
