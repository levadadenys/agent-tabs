---
name: resume-tabs
description: Restore iTerm tabs full of agent sessions (Claude, Codex, Grok, ssh) after a crash or reboot. Use when the user says their Mac crashed or rebooted, they lost their terminal tabs, or they want to reopen the sessions their windows had before.
---

# Resume terminal tabs after a crash or reboot

`agent-tabs` records the iTerm window/tab map every 30 seconds (by default) and replays it
afterwards. Session transcripts are never lost in a crash; what is lost is the
*grouping* — which session was in which window and tab, in which directory — and
that is what the snapshot preserves.

Restore types commands into live shells. Always run the dry run and read it
before adding `--go`, and never pass `--go` on the user's behalf without them
seeing the plan.

## Before you start

- You run commands without a terminal, so restore cannot find the window you
  are in, and would treat any other tab in it like the rest. Make sure the user
  started you in a **new window holding only your tab**; if not, ask them to.
  Your own tab is busy, so restore never types into it.
- Never pass `--force`. It is refused without a terminal anyway, and restoring
  never needs it.
- If `agent-tabs` is not found, use `~/.local/bin/agent-tabs`.

## Recovering

1. Check what was recorded, and when:

   ```bash
   agent-tabs list
   ```

   The first line gives the snapshot's age. It should be from just before the
   crash or reboot. If it is hours or days old, the recorder was not running:
   check `launchctl list | grep agent-tabs` and `~/.agent-tabs/recorder.log`
   before restoring anything.

   If `list` prints a warning that the snapshot was captured after the last
   reboot and holds fewer sessions, the pre-reboot fleet is in
   `~/.agent-tabs/pre-boot.json`. Use the command it prints, which adds
   `-f ~/.agent-tabs/pre-boot.json`.

2. Dry-run the whole fleet (with `-f ~/.agent-tabs/pre-boot.json` if step 1
   warned):

   ```bash
   agent-tabs restore --all --create-missing
   ```

   Windows iTerm reopened after a reboot are recognized by each tab's working
   directory and filled in place; windows that survived a crash also by their
   tab titles. Snapshot windows nothing matches are rebuilt in new windows,
   including one an idle reopened window might be (the idle one can then be
   closed). One that a window with something running might be is reported
   with the commands for either case: tell the user rather than guess.

3. Show the user the plan and ask before executing. Then run exactly the same
   command with `--go`:

   ```bash
   agent-tabs restore --all --create-missing --go
   ```

   If it stops part-way, run the same command again: anything already running
   is skipped.

## Reading the plan

- `SKIP (session already running in …)` — that session is live elsewhere;
  restoring it would start a duplicate. Expected, nothing to do.
- `SKIP (same session as tab N of snapshot window W, started once)` — the
  snapshot lists that session twice; it is restored once.
- `SKIP (already running …)` — the target tab is busy. `--force` types into it
  anyway; it never starts a duplicate session.
- A restored tab says `No conversation found with session ID` — Claude Code
  deleted that transcript: it removes conversations not modified for
  `cleanupPeriodDays` (30 by default). Nothing can resume it; tell the user,
  and suggest `"cleanupPeriodDays": 365` in `~/.claude/settings.json`.
- `Not restored:` — windows it declined to guess about (ambiguous, too many
  windows sharing titles, no live match, one a live window might be, or the
  window it ran from), each with the command that restores it. Rerunning from a new window is safe: anything
  already running is skipped.

For a single window, run from inside it: `agent-tabs restore` (auto-matched) or
`agent-tabs restore <n>`. To rebuild one snapshot window from scratch:
`agent-tabs restore <n> --new-window`.

## If there is no snapshot

The recorder only helps for crashes after it was installed. To reconstruct an
older one by hand:

- Claude transcripts are at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`.
  The directory is keyed by **exact cwd**, so sessions started in a git worktree
  live in their own folder and do not appear in `/resume` from the repo root.
- `claude --resume <id>` needs the **full UUID** and only resolves from the
  directory the session ran in. The same goes for `codex resume <id>` and
  `grok --resume <id>`: all three scope their pickers by cwd.
- Live Claude sessions are listed in `~/.claude/sessions/<pid>.json`, with
  sessionId and cwd.

## Notes

- A deleted worktree must be recreated at its exact old path before a session
  that ran in it can be resumed, or the resume has nowhere to land.
- Restoring starts every listed session at once. That is why the dry run comes
  first.
