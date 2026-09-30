---
name: resume-tabs
description: Restore iTerm tabs full of agent sessions (Claude, Codex, Grok, ssh) after a crash or reboot. Use when the user says their Mac crashed or rebooted, they lost their terminal tabs, or they want to reopen the sessions their windows had before.
---

# Resume terminal tabs after a crash or reboot

`agent-tabs` records the iTerm window/tab map every 30 seconds and replays it
afterwards. Session transcripts are never lost in a crash; what is lost is the
*grouping* — which session was in which window and tab, in which directory — and
that is what the snapshot preserves.

Restore types commands into live shells. Always run the dry run and read it
before adding `--go`, and never pass `--go` on the user's behalf without them
seeing the plan.

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

2. Dry-run the whole fleet, from any iTerm tab:

   ```bash
   agent-tabs restore --all                    # iTerm restored the windows
   agent-tabs restore --all --create-missing   # iTerm restored none, or only some
   ```

   `--all` pairs each live window with the snapshot window it came from, by tab
   title — iTerm restores titles when *Reopen windows when logging back in* was
   ticked at shutdown. `--create-missing` also rebuilds, in new windows, the
   snapshot windows nothing matched.

3. Read the plan, then execute:

   ```bash
   agent-tabs restore --all --go               # or with --create-missing
   ```

## Reading the plan

- `SKIP (session already running in …)` — that session is live elsewhere;
  restoring it would start a duplicate. Expected, nothing to do.
- `SKIP (already running …)` — the target tab is busy. `--force` types into it
  anyway; it never starts a duplicate session.
- `Not restored:` — windows it declined to guess about (ambiguous, too many
  windows sharing titles, or no live match), each with the command that
  restores it.

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
