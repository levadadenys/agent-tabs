---
name: resume-tabs
description: Restore iTerm tabs full of agent sessions (Claude, Codex, Grok, ssh) after a crash or reboot. Use when the user says their Mac crashed, they lost their terminal tabs, or they want to reopen the sessions a window had before it died.
---

# Resume terminal tabs after a crash

`agent-tabs` records the iTerm window/tab map every 30s while iTerm is open, and
replays it afterwards. Session transcripts are never lost in a crash; what is
lost is the *grouping* — which session was in which window and tab — and that is
what the snapshot preserves.

## Recovering

1. Check a snapshot exists and see what it holds:

   ```bash
   agent-tabs list
   ```

2. From inside a tab of the window you want to rebuild, dry-run it. With no
   window number it auto-matches by tab title, since iTerm restores tab titles
   after a crash:

   ```bash
   agent-tabs restore          # dry run, auto-matched
   agent-tabs restore 2        # dry run, snapshot window 2
   ```

3. Execute once the plan looks right:

   ```bash
   agent-tabs restore --go
   ```

Repeat per window. Tabs holding any live process (an agent, but also vim, psql
or an ssh session) are skipped, as is the tab the command runs from.

## If there is no snapshot

The recorder only helps for crashes that happen after it is installed. To
reconstruct an older crash by hand:

- Claude transcripts: `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`.
  The directory is keyed by **exact cwd**, so sessions started in a git worktree
  live in their own folder and will not appear in `/resume` from the repo root.
- `claude --resume <id>` needs the **full UUID** and only resolves from the
  directory the session ran in. Same for `codex resume <id>` and
  `grok --resume <id>` — all three scope their pickers by cwd.
- Live sessions are listed in `~/.claude/sessions/<pid>.json` (sessionId + cwd).

## Notes

- A worktree that was deleted must be recreated at its exact old path before
  its session can be resumed, or the resume has no directory to land in.
- Restoring a window starts every agent in it at once. Check the dry run first.
