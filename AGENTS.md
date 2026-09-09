# agent-tabs — contributor guide

Shared by all coding agents. `CLAUDE.md` points here; edit this file, not that one.

## What this is

A macOS/iTerm2 crash-recovery tool: it records which agent CLI session runs in
which tab and working directory, and replays that afterwards. It types commands
into live shells, so a wrong result is not a cosmetic bug.

## Workflow (required)

1. **Always work in a git worktree**, never directly in the main checkout:
   `git worktree add .worktrees/<topic> -b <type>/<topic>`
2. **Always open a PR.** `main` is protected; direct pushes are rejected.
   Branch names follow the commit types below (`fix/`, `feat/`, `chore/`, …).
3. **One commit per file**, conventional-commit style. The subject says what
   changed; the body says *why it was wrong*, not what the diff shows.
4. Before ending a session that used a worktree, ask whether to keep or delete it.

## Tests (required)

`python3 tests/run_all.py` must pass. No test framework, no dependencies.

Two rules, both learned the hard way in this repo:

- **Mutation-check every new test.** Break the code it covers, confirm the test
  fails, restore with `cp` from a backup — never `git checkout`. A test that
  passes against broken code is worse than no test.
- **Drive the real call site.** A test that asserts on a mocked-out version of
  the mechanism under test proves nothing. One test here asserted `0700` on a
  directory `mkdtemp` had already made `0700`; it passed with the production
  code deleted. Ask what the assertion would catch, not whether it is green.

## Constraints

These are deliberate. Do not "fix" them:

- macOS and iTerm2 only; python3 standard library only, no packaging.
- AppleScript rather than the iTerm2 Python API, so the same code path works
  from a plain shell and from an AutoLaunch script.
- macOS does not expose process environments, so `CODEX_HOME` is derived from
  the rollout file path, not from `ps`.
- Split panes are recorded but only the first pane of each tab is recreated.

## The repo is intended to become public

Nothing installation-specific may be committed. Personal paths, account names
and machine details belong in `~/.agent-tabs/config.json`, which is outside the
repo. Before pushing, check:

```bash
grep -rniE '/Users/|@gmail' --include='*.py' --include='*.md' --include='*.json' . \
  | grep -v '/Users/you'
```

Placeholders in examples use `/Users/you`. History was audited before the first
push; keep it that way rather than relying on a later cleanup, because history
stays visible once the repo is public.

Runtime state in `~/.agent-tabs/` holds real paths and session ids. It is
gitignored by virtue of living outside the repo — never paste a snapshot into
an issue.

## Safety rules that must not regress

- Restore is a dry run by default; `--go` is explicit.
- Never type into a tab holding live work, and never into the tab the command
  runs from. Liveness is re-checked at execute time, not just at plan time.
- Never replay a recorded command line containing shell syntax.
- Never let a capture with no agent sessions overwrite a good snapshot.
