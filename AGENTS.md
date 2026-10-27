# agent-tabs — contributor guide

Shared by all coding agents. `CLAUDE.md` points here; edit this file, not that one.

## What this is

A macOS/iTerm2 crash-recovery tool: it records which agent CLI session runs in
which tab and working directory, and replays that afterwards. It types commands
into live shells, so a wrong result is not a cosmetic bug.

## Workflow (required)

1. **Always work in a git worktree**, never directly in the main checkout:
   `git worktree add .worktrees/<topic> -b <type>/<topic>`
2. **Always open a PR.** Branch names follow the commit types below
   (`fix/`, `feat/`, `chore/`, …).

   GitHub branch protection requires a paid plan for private repositories, so
   `main` is guarded by a pre-push hook instead. Enable it once per clone:

   ```bash
   git config core.hooksPath .githooks
   ```

   It rejects any push to `main`; `--no-verify` bypasses it deliberately. If
   this repo ever goes public, replace the hook with a real ruleset — a local
   hook is a reminder, not an enforcement boundary.
3. **One commit per file**, conventional-commit style. The subject says what
   changed; the body says *why it was wrong*, not what the diff shows.
4. Before ending a session that used a worktree, ask whether to keep or delete it.

## Tests (required)

`python3 tests/run_all.py` must pass. No test framework, no dependencies.

CI (`.github/workflows/ci.yml`) runs the suite on macOS for Python 3.9–3.14,
plus `ruff check .`, `ruff format --check .`, `mypy` and
`shellcheck .githooks/pre-push`. Run `ruff format .` before committing. The lint
tools are pinned in `requirements-dev.txt`; they are development tools, not
dependencies of agent-tabs.

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
  from a plain shell and from the launchd recorder, with no dependency on
  iTerm's Python runtime (not installed by default — the reason the original
  AutoLaunch recorder never ran).
- macOS does not expose process environments, so `CODEX_HOME` is derived from
  the rollout file path, not from `ps`.
- Split panes are recorded but only the first pane of each tab is recreated.

## The repo is intended to become public

Nothing installation-specific may be committed. Personal paths, account names
and machine details belong in `~/.agent-tabs/config.json`, which is outside the
repo.

Scanning is done by [gitleaks](https://gitleaks.io), configured in
`.gitleaks.toml`. Its default rules cover credentials; two custom rules add
what matters here — machine-specific home paths and personal email addresses.

```bash
brew install gitleaks     # once
gitleaks git --no-banner --log-opts="origin/main..HEAD"
```

The pre-push hook runs it automatically over **the commits being pushed**, not
the working tree, so pushing another branch's ref from this checkout is still
scanned. If gitleaks is missing the hook refuses the push rather than passing
it unscanned.

CI runs the same scan over the whole history on every PR and on `main`, so a
clone that never enabled the hook is still caught before merge. Like the hook,
it refuses a scan that covered no commits: gitleaks exits 0 when its own `git`
call fails.

A hand-rolled `grep` scanner lived here first and was replaced after five
rounds of review found five ways it could report clean while a leak existed
(options injection from a dash-named file, a regex-dot over-match in its
self-exclusion, whole-line exclusion hiding a real path beside the allowed
placeholder, a discarded stderr, and a malformed range that scanned nothing).
Do not reintroduce one. A leak check that fails open is worse than none,
because it manufactures confidence.

**Known limitation:** gitleaks reads diffs, and git does not emit content for
files it classifies as binary, so a home path inside a binary blob is not
scanned. Verified, not theoretical. This repo is all text; if that changes,
revisit it.

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
- Never start a session that is already running anywhere, or twice in one
  restore. Check at planning time AND immediately before typing, and hold
  `restore_lock()` around any `--go`. `--force` does not override this.
- Never replay a recorded command line containing shell syntax, zsh's
  included: the macOS default shell expands `~`, `=cmd`, and `^`/`#` under
  `extendedglob`, which a bash-minded filter misses.
- Refuse `--go --force` when restore has no terminal: the tab it runs from is
  found through its tty, and without one only that tab being busy keeps
  restore out of it, which is exactly what `--force` lifts.
- Target windows by iTerm's window **id**, never its index: the index is
  front-to-back position and changes whenever a window is focused.
- Pair a live window with a snapshot window only when the pairing is forced —
  the same in every optimal assignment. Report ties; never guess.
  `tests/test_match_all_oracle.py` checks this against a brute force; keep it
  passing.
- Never create a window unless asked (`--new-window`, `--create-missing`), and
  never rebuild a snapshot window that an unpaired live window might be.
- Never pair the window `restore --all` runs in: one of its tabs is the one
  being typed in, so it could only ever be part-restored. Keep a pairing only
  if it is the same with the launcher in the matching and without it: excluded,
  its snapshot window went to any window sharing one title with it; included,
  one coincidental title on it settled another window's tie.
- A title that is only a shell name is not evidence of which window it was.
- Never let a capture with no agent sessions overwrite a good snapshot, nor a
  collapsed one until the drop has persisted.
- Keep the pre-reboot snapshot recoverable: `pre-boot.json` is pinned on the
  first promotion after each boot, tagged with that boot: the fuller of
  `snapshot.json` (if it predates the boot) and the fullest pre-boot capture
  in history.
- Re-check running sessions before creating a window and before adding tabs,
  not only before typing: a session started since the plan must not leave a
  blank window or tab behind.
