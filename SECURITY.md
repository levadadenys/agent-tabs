# Security

## What agent-tabs holds

`~/.agent-tabs/` (or `$AGENT_TABS_HOME`) holds snapshots and their history: for every iTerm tab,
its working directory, title, the command line of the process running in it, and agent session
ids. A command line can contain a credential that another program was given on its argv. The
directory is created owner-only (`0700`) and corrected to that if it already exists.

Nothing leaves the machine: agent-tabs makes no network connections.

## What it does with it

`restore --go` types commands into iTerm tabs through AppleScript. What it types is built to be
safe against what it recorded, because any local process controls its own argv and working
directory:

- every command is `cd <working directory> && <resume command>`, with the directory quoted for
  the shell;
- session ids are only ever taken from a strict UUID match;
- a recorded command line containing shell syntax is never replayed: it is reported and
  downgraded to the bare `cd`;
- it never types into the tab it was run from, nor into a tab that is running something unless
  you pass `--force`, and re-checks both immediately before typing.

Out of scope: anyone who can already write to `~/.agent-tabs/` as you, or type into your
terminal, needs no help from agent-tabs.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/levadadenys/agent-tabs/security/advisories/new)
for this repository, with the steps to reproduce. You'll get an answer within a week.

Especially welcome: any way to make `restore` type something other than the quoted `cd` and a
resume command, to type into a tab doing something, or to expose a snapshot's contents to another
user.
