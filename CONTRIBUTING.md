# Contributing

Thanks for helping. Bug reports, support for more agent CLIs, and fixes are all welcome.

agent-tabs types commands into live shells, so a wrong result is not a cosmetic bug. Most of
this guide is about not breaking that.

## Getting started

You need macOS, iTerm2 and Python 3.9 or later; agent-tabs itself has no dependencies.

```sh
git clone https://github.com/levadadenys/agent-tabs && cd agent-tabs
python3 tests/run_all.py                          # standard library only
```

The lint and type-check tools are pinned in `requirements-dev.txt` and need Python 3.10 or later
(mypy no longer supports 3.9). Install them in a virtual environment:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt               # ruff and mypy
brew install shellcheck
ruff check . && ruff format --check . && mypy && shellcheck .githooks/pre-push
```

The tests do not need iTerm: AppleScript is faked, while `ps`, `lsof` and the filesystem are real
where the mechanism under test depends on them.

**Enable the leak-scanning pre-push hook** once per clone:

```sh
brew install gitleaks
git config core.hooksPath .githooks
```

It refuses a push that adds a home-directory path or a personal email address. CI runs the same
scan over the whole history on every pull request, so the hook only saves you a round trip.

## How the code is laid out

| Module | Does |
|---|---|
| `agent_tabs/iterm.py` | AppleScript: list windows and tabs, open windows and tabs, type into a tab |
| `agent_tabs/procs.py` | `ps` and `lsof`: which process runs on a tab's tty, and what it holds open |
| `agent_tabs/adapters.py` | per-agent lookup: process → session id and working directory → restore command |
| `agent_tabs/snapshot.py` | capture, history, and the guards that keep a shutdown from overwriting a good snapshot |
| `agent_tabs/restore.py` | planning, pairing live windows with snapshot windows, and typing |
| `agent_tabs/install.py` | the launchd recorder and the symlinks |
| `agent_tabs/config.py` | `~/.agent-tabs/config.json`: the Codex account map |
| `bin/agent-tabs` | the command line |

## Making a change

- **The safety rules must not regress.** They are listed in
  [AGENTS.md](AGENTS.md#safety-rules-that-must-not-regress): dry run by default, never typing into
  a busy tab or starting a session twice, never replaying shell syntax, pairing windows only when
  the evidence forces it. A change near any of them needs a test that walks the case that could go
  wrong.
- **Mutation-check every new test.** Break the code it covers, confirm the test fails, then
  restore it from a copy (`cp`). A test that passes against broken code is worse than none.
- **Drive the real call site.** A test that asserts on a mocked-out version of the mechanism it
  is meant to check proves nothing.
- **Standard library only**, and no packaging: agent-tabs runs from a clone.
- **Nothing machine-specific in the repo.** Examples use `/Users/you`.
- One topic per pull request. Commit subjects follow
  [Conventional Commits](https://www.conventionalcommits.org/); the body says why the old
  behaviour was wrong, not what the diff shows. CI (tests on Python 3.9–3.14, ruff, mypy,
  shellcheck, gitleaks) must pass.
- Update `README.md` and `CHANGELOG.md` when behaviour or commands change.

When a change touches capture or restore, check it against real iTerm state too: run
`agent-tabs snapshot`, then the dry run of the restore you changed, and read the plan. Nothing is
typed without `--go`.

## Adding an agent CLI

Add a resolver to `AGENT_RESOLVERS` in `agent_tabs/adapters.py`. Given a process and its working
directory, it returns the session id and the command that resumes it. The existing three show the
usual ways to find the id: a file named by pid (Claude), a transcript the process holds open
(Codex, Grok). Things to get right:

- the id must come from something the agent itself wrote, not from guessing by timestamps;
- restore commands are `cd <cwd> && <resume>`, because agents scope their session pickers to the
  working directory;
- anything taken from a process's argv is data any local process controls. Extract the id with a
  strict pattern; never interpolate free text into the command.

Give it a one-letter mark in `KIND_MARK` in `bin/agent-tabs`, or `list` shows `?`.

Being in `AGENT_RESOLVERS` is what makes a tab count as an agent everywhere else, including the
guard that refuses to let an empty capture overwrite a good snapshot.

## Reporting a bug

Use the bug report form. **Never paste a snapshot or unredacted output**: it holds every tab's
working directory, command line and session id. Replace them with placeholders.
