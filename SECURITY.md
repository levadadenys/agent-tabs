# Security

## What agent-tabs holds

`~/.agent-tabs/` (or `$AGENT_TABS_HOME`) holds snapshots and their history: for every iTerm tab,
its working directory, title, the command line of the process running in it, and agent session
ids. A command line can contain a credential that another program was given on its argv. The
directory is created owner-only (`0700`) and corrected to that if it already exists.

Nothing leaves the machine: agent-tabs makes no network connections.

## What it does with it

`restore --go` types commands into iTerm tabs through AppleScript. Any local process controls its
own argv and working directory, so what is typed is built from recorded data with that in mind:

- every command starts with `cd <working directory>`, quoted for the shell;
- for an agent tab, the rest is the agent's resume command, with a session id taken only from a
  strict UUID match;
- for any other tab that was running something (`ssh prod-box`, `tail -f app.log`), the rest is
  **the recorded command line, replayed as it was**, unless it contains shell syntax, in which
  case it is reported and downgraded to the bare `cd`;
- it never types into the tab it was run from, nor into a tab that is running something unless
  you pass `--force`, and re-checks both immediately before typing.

The shell-syntax filter stops a recorded command line from injecting more commands. It does not
make the program or its arguments safe to run: replaying `ssh prod-box` is the point, and a
process can rewrite its own argv. That is why restore is a dry run by default. **Read the plan
before `--go`**; every replayed command is listed in it.

Out of scope: anyone who can already write to `~/.agent-tabs/` as you, or type into your
terminal, needs no help from agent-tabs.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/levadadenys/agent-tabs/security/advisories/new)
for this repository, with the steps to reproduce. You'll get an answer within a week.

Especially welcome: any way to get shell syntax past the filter, to make `restore` type into a
tab doing something or into the tab it runs from, to start a session that is already running, or
to expose a snapshot's contents to another user. Replaying a plain recorded command line is by
design, as described above.
