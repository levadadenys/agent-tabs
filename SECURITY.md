# Security

## What agent-tabs holds

`~/.agent-tabs/` (or `$AGENT_TABS_HOME`) holds snapshots and their history: for every iTerm tab,
its working directory, title, the command line of the process running in it, and agent session
ids. A command line can contain a credential that another program was given on its argv. Snapshot
and history files are created owner-only (`0600`), and so is `history/`; `install` creates
`~/.agent-tabs` owner-only.

Nothing leaves the machine: agent-tabs makes no network connections.

## What it types

`restore --go` types commands into iTerm tabs through AppleScript, built from what was recorded.
Any local process controls its own argv and working directory, so:

- a command starts with `cd <working directory>`, quoted for the shell. If the directory could
  not be recorded, the rest is typed without it, and the plan flags the tab with `!!`;
- an idle shell tab gets only the `cd`;
- an agent tab gets the agent's resume command, with a session id taken only from a strict UUID
  match. Without an id, the agent's bare command (`claude`). For Codex, the command comes from
  `codex_profiles` in your own `config.json`, typed as written;
- any other tab that was running something (`ssh prod-box`, `tail -f app.log`) gets **the
  recorded command line, replayed as it was**, unless it contains shell syntax, in which case it
  is reported and downgraded to the bare `cd`.

The shell-syntax filter rejects shell metacharacters, so a recorded line cannot chain or
substitute commands. It does not check what the program is: treat every listed command as one the
recording process chose. That is why restore is a dry run by default. **Read the plan before
`--go`**; it shows exactly what will be typed.

Where it types:

- never into a tab running something, unless you pass `--force`;
- never into the tab it runs from. It finds that tab from its own terminal; run without one (from
  a script, say), it cannot, and only the busy check above protects that tab;
- never a session already running in any iTerm pane, `--force` or not.

Busy tabs and running sessions are read again just before typing starts, so the plan is not
trusted blindly.

Out of scope: anyone who can already write to `~/.agent-tabs/` as you, or type into your
terminal, needs no help from agent-tabs. A snapshot passed with `-f` is trusted as written.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/levadadenys/agent-tabs/security/advisories/new)
for this repository, with the steps to reproduce. You'll get an answer within a week.

Especially welcome: any way to get shell syntax past the filter, to make `restore` type into a
tab doing something or into the tab it runs from, to start a session that is already running, or
to expose a snapshot's contents to another user. Replaying a plain recorded command line is by
design, as described above.
