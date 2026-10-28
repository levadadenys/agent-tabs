# How agent-tabs works

The README covers what to do; this is the detail behind two of its decisions,
for anyone who wants to check them before trusting `--go`.

## Pairing live windows with snapshot windows (`restore --all`)


A wrong pairing types one window's sessions into another, so:

- windows are matched by evidence, as a one-to-one assignment that maximises
  the total — a snapshot window goes to at most one live window. The evidence
  is the distinct tab titles two windows share, plus the tab positions where
  both sit in the same working directory. Titles carry over a crash;
  directories carry over a reboot, where iTerm reopens each window with its
  tabs in the right directories but every title reset to the shell's name. A
  title that is only a shell name (`-zsh`, `bash`, …) and the home directory or
  `/` do not count: every idle tab looks like that. Directories are compared by
  position, so a 14-tab window and a 2-tab window in one project still differ,
  and count only when at least two tabs agree, or every tab of two windows of
  the same size does: one tab in a project directory would otherwise let a
  fresh window opened there claim a bigger window;
- the window `--all` runs in is never filled, since one of its tabs is the one
  being typed in. Run it from a new window. Run from inside a restored window
  instead, it is reported, and running again from a new window restores it —
  anything already running is skipped. Meanwhile it neither hands its snapshot
  window to another window nor settles another window's tie: a pairing is kept
  only if it is the same with and without that window, and any other is
  reported;
- a pairing is used only when it is **forced**: the same in every best
  assignment. If the evidence fits two pairings equally well, those windows are
  reported, never guessed;
- a group of windows tangled by shared evidence — more than 12 live windows, or
  more than 12 snapshot windows they could be — is reported rather than
  matched;
- windows are targeted by iTerm's stable window id, not their position, which
  changes whenever you focus a window.

Anything not paired is listed with the command that restores it. A snapshot
window that an unpaired live window might be is listed as such, with the
command for either case, rather than as unmatched.

`tests/test_match_all_oracle.py` checks the forced-pairing rule against a
brute-force search over every assignment, on hundreds of random cases.

With `--create-missing`, a snapshot window nothing was paired with is rebuilt in
a new window. One that an unpaired live window *might* be — the evidence fits
both — is rebuilt too when nothing runs in that live window (typically one
iTerm reopened): at worst it is left standing empty, and nothing can run twice.
If anything runs in it, it may be the real window, part-restored, and an ssh
tab or other replayed command has no session id to deduplicate, so the snapshot
window is reported, with commands for either case.

## Which capture becomes the snapshot

A capture is not always promoted to `snapshot.json`, because the moment a
snapshot matters most is a shutdown, when tabs die one at a time:

- a capture with **no** agent sessions is never promoted;
- a capture that **collapsed** — kept less than 67% of the last good one's
  sessions — is held back until the drop has lasted three captures in a row and
  at least 45 seconds: about a minute at the default interval, 50 seconds at
  10 s, 10 minutes at 300 s. A shutdown is over long before that. Genuinely
  closing most of your sessions is accepted after that; closing a tab or two is
  promoted immediately.

**Across a reboot**, the first promotion after boot pins the pre-reboot sessions
in `pre-boot.json`: the fuller of the outgoing `snapshot.json` and the fullest
capture from before the reboot still in history. Not simply the newest: the
last captures before a reboot are half-dead ones from the shutdown, and one
can reach `snapshot.json` if the shutdown drags on. Not history alone either:
after a long idle spell it may no longer hold those sessions. That matters if
iTerm restored nothing and you started a session before restoring: that small capture replaces `snapshot.json` about a
minute later, and history rotates the full one out after about half an hour. When
that has happened, `list` and `restore` say so and print the command to restore
the pre-reboot sessions instead.
