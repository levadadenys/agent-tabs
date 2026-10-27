# Spec: open-source readiness

**Status:** reviewed 2026-10-27. Part 1: implemented, items 1–3 and 5 in #13, item 4 (the
collapse guard) in `fix/collapse-guard-by-time`. Part 2: in progress. Written 2026-10-08,
against `main` at the merge of #5.

Two parts: a configurable snapshot interval, which other people will want, and
a short checklist for flipping the repository public.

## 1. Configurable snapshot interval

### Today

The recorder runs every 30 seconds. `INTERVAL_SECONDS = 30` in
`agent_tabs/install.py` is written into the launchd job as `StartInterval`.
There is no setting for it.

Measured on 2026-10-08 with 24 sessions: one capture takes about 1.9 s, mostly
AppleScript, `ps` and `lsof`. At 30 s that is roughly 6% of one core, in
bursts. The worst case after a crash is losing sessions started in the last
30 seconds.

30 s stays the default. The point is to let others make their own trade-off.

### What assumes 30 seconds

Other settings were tuned for 30 s and would drift if only the interval
changed:

| Setting | Where | Value | Effect at 30 s | At 10 s | At 300 s |
|---|---|---|---|---|---|
| `HISTORY_KEEP` | `snapshot.py` | 60 captures | ~30 min of history | 10 min | 5 h |
| `COLLAPSE_STREAK` | `snapshot.py` | 3 captures | drop accepted after ~1 min | **never** (see below) | after ~10 min (3 captures span 2 intervals) |
| `COLLAPSE_SETTLE_SECONDS` | `snapshot.py` | 45 s | already time-based | same | same |

The 10 s column is a latent bug, not a drift: `save()` reads exactly
`COLLAPSE_STREAK` archives (`_recent_entries(COLLAPSE_STREAK)`), and three
captures 10 s apart span 20 s, which never reaches the 45 s settle. A genuine
drop would never be promoted. The design below must not inherit that fixed
window.

History length also bounds the pre-reboot pin's fallback
(`_fullest_pre_boot_archive`), which can only find what history still holds.

### Design

1. **Setting.** `"interval_seconds"` in `~/.agent-tabs/config.json`, default
   30. Below 10 s, a ~2 s capture is a real share of the machine; above
   300 s, the crash-loss window stops being a rounding error. One rule, no
   judgment calls:
   - a JSON integer (not `true`/`false`, which Python treats as ints) is
     clamped to 10–300;
   - anything else — a float (including `60.0`, `NaN`, `Infinity`, which
     Python's `json` accepts), a string (including `"60"`), `null`, a list, an
     object — falls back to 30;
   - a missing key gives 30 silently; every other clamp or fallback prints one
     line in `install`'s output naming the value used.

   It must never refuse to install.
2. **Applying it.** launchd reads the interval from the job file, so a change
   takes effect only when the plist is rewritten. `agent-tabs install` already
   rewrites it on every run; `--force` only affects the symlinks, so do not
   tell users to pass it. Say "re-run `agent-tabs install`" in the README, in
   `config.example.json`, and in `install`'s output (`recorder loaded, every
   Ns`, which already exists). `INTERVAL_SECONDS` becomes the default rather
   than the value.
   - One path does not apply it: when a recorder is already loaded and no
     plist was on disk, `_install_agent` leaves the running job alone and
     returns success. That job keeps its old interval, so the output must say
     the new interval was **not** applied, instead of nothing.
3. **History by time, with a floor.** Replace the fixed 60-capture prune with:
   keep an archive if it is among the newest `HISTORY_MIN` (60), **or** younger
   than `HISTORY_WINDOW_SECONDS` (1800). Age comes from the archive filename,
   as `_archive_time` already reads it. Cap the total at
   `HISTORY_WINDOW_SECONDS / 10` (180), counting every archive, manual
   snapshots included.
   - Why the floor: age alone would delete everything older than 30 minutes
     on the first capture after a long sleep or idle spell. That is exactly
     the pre-sleep history the pin fallback and the collapse guard read.
   - Pruning must not depend on the interval read from config: the recorder
     may be running an older plist than the config says. Time-based pruning
     makes that mismatch harmless.
   - A filename `_archive_time` cannot parse counts as old: only the floor
     keeps it. An archive dated in the future (the clock went back) counts as
     young; the cap bounds it.
   - `HISTORY_KEEP` is renamed `HISTORY_MIN`. Update its users:
     `tests/test_snapshot_store.py` and the "history files rotate out after N
     captures" message in `snapshot.load()`.
   - **Order archives by parsed time**, here and in the collapse guard, not by
     filename sort. Today the two agree, because `save()` names archives by
     UTC time. A name `_archive_time` cannot parse sorts oldest: only the
     floor keeps it, and the collapse guard skips it. An archive dated in the
     future (the clock went back) sorts newest and counts as young. The cap
     drops the oldest by parsed time, so it never deletes the real new
     captures in favour of the future-dated ones. The collapse guard reads
     the future-dated archive as the newest. While it is not collapsed, a
     collapse cannot promote, which holds a good snapshot rather than
     overwriting it. That lasts until the clock passes it.
4. **Collapse guard: a minimum count, and time over the whole run.** Walk back
   from the newest archive through the consecutive run of collapsed captures
   (`0 < count < floor`), stopping at the first that is not collapsed or
   cannot be read. Promote once that run holds at least `COLLAPSE_STREAK`
   (3) captures **and** spans at least `COLLAPSE_SETTLE_SECONDS`.
   - What changes: today's guard reads exactly the newest three archives
     (`_recent_entries(COLLAPSE_STREAK)`), which at 10 s span 20 s and never
     reach the settle. Walking the whole run fixes that: at 10 s a drop is
     accepted at ~50 s. At 30 s nothing changes. At 300 s it stays ~10 min,
     as today.
   - Why the count stays: dropping to "span only" was considered and
     rejected. A gap supplies span for free: a half-dead capture from a
     shutdown and the first small capture after boot are two collapsed
     captures more than 45 s apart, and would promote at once, at any
     interval. The pre-reboot pin does not make that safe: `_pin_pre_boot`
     skips pinning when the boot time cannot be read, and swallows a failed
     write, and promotion goes ahead in both cases. The count is what still
     protects the fleet then. The settle time rules out a burst of captures
     landing at once; the count rules out a gap. Each covers what the other
     cannot.
   - The walk is bounded by history.
   - This is the most safety-relevant part of the spec. Ship it as its own
     PR, separate from items 1–3 and 5, and re-read the existing
     shutdown-guard tests before changing the code.
5. **Config reading must be as robust as the snapshot readers.** Today
   `config.load()` catches `JSONDecodeError` and `OSError` only, so bad UTF-8
   raises `UnicodeDecodeError`. It also accepts non-object JSON and merges it
   with `dict.update` inside `load()`. Most non-objects then raise uncaught
   (`5`, `null`, `"ab"`, `[1, 2]`). Some lists are silently accepted (`[]`,
   `[["a", 1]]`). Install would now read the config too. Catch `ValueError`
   and require a dict, as `snapshot._read` does.
   - `config.codex_command` has the same flaw one level down. It calls
     `.get` on `codex_profiles`, so a non-object value there crashes a
     capture: `adapters` calls it for every Codex tab. Require a dict there
     too, and treat a non-string value as no entry.

### Tests

Drive the real call sites (`install`'s plist builder, `snapshot.save()`), not
helpers. Install tests must point `AGENT_TABS_HOME` at a temporary directory.
The current sandbox in `test_install_exit.py` does not redirect the config
path, so a new test would read the real `~/.agent-tabs/config.json`.

- Clamping, read back from the written plist: `5` → 10, `9999` → 300, `0`
  and `-5` → 10, `60` → 60; `true`, `60.0`, `NaN`, `"60"`, `"abc"`, `null`,
  `[]` → 30; a missing key → 30 with no warning line.
- The already-loaded, no-plist path says the interval was not applied.
- Pruning:
  - 30 s captures keep about 30 minutes.
  - 10 s captures past 180 archives keep exactly 180. The test must write
    more than 180, or a no-op prune passes it.
  - A 3-hour gap followed by one new capture keeps the new capture plus the
    newest 59 pre-gap archives (`save()` archives before it prunes).
  - An unparseable filename is kept only within the floor.
  - A future-dated archive does not cause the cap to delete newer real
    captures.
- The collapse guard at 10 s, 30 s and 300 s:
  - A burst of captures within seconds is never accepted.
  - A genuine drop is accepted once the run holds three captures and spans
    the settle time.
  - The 10 s case must fail against today's fixed-window code.
  - Two collapsed captures either side of a long gap do not promote. This
    is the regression that dropping the count would cause.

  Archive times come from filenames, so backdate the filenames, as
  `test_a_burst_of_captures_is_not_time_enough` does. Real-clock names give a
  span of about 0 s, and a test asserting "not promoted" passes against
  anything. Where a test expects no promotion, show that the same setup with
  one more capture does promote.
- Damaged `config.json` never breaks install or a capture: bad UTF-8, `5`,
  `null`, a list, `{"interval_seconds": []}`, `{"interval_seconds": NaN}`,
  `{"codex_profiles": []}`. For a capture, drive it through a Codex tab's
  resume command, the path that calls `codex_command`.
- Mutation-check each test, restoring with `cp`, as for every test in this
  repo.

### Docs

Prose that states 30 s, 60 captures or three captures, and goes stale:

- README:
  - the intro ("every 30 seconds");
  - Install (the setting, and re-running `install`);
  - Commands (the `snapshot` comment);
  - Snapshots and reboots ("the last 60 captures, about half an hour", "three
    captures and at least 45 seconds", and history rotating the full
    snapshot out "within half an hour");
  - Config.
- `snapshot.py`:
  - the comments above `PRE_BOOT` and `COLLAPSE_STREAK`;
  - the `_pin_pre_boot` docstring ("about half an hour");
  - the `save()` docstring ("a shutdown never lasts that long").
- `install.py`: the `_stop_legacy_processes` docstring ("every 30s").
- Test docstrings that state the same numbers: `test_pre_boot.py`,
  `test_shutdown_guard.py`, `test_install_exit.py`. Grep for `30`, `60` and
  `half an hour`.
- `config.example.json`: add `"interval_seconds": 30`.
- AGENTS.md, under the safety rules: history is pruned by time with a floor
  of the newest 60, and pruning never reads the configured interval.

## 2. Before flipping the repo public

- **License.** MIT. Add `LICENSE`.
- **History audit.** The pre-push hook scans each push, but scan the whole
  history once before going public, since anything committed is published
  with it:

  ```bash
  git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'  # PR heads go public too
  gitleaks git --no-banner --log-opts="--all -m"
  ```

  `-m` matters: without it gitleaks skips a merge's own conflict resolution,
  the same reason the pre-push hook passes it.

  gitleaks reads diffs only. Read the following by hand. They are listings
  that print everything for a person to read, not a scanner that can report
  clean, so AGENTS.md's rule against hand-rolled scanners does not apply. Run
  each one so that a failing `git` is visible: `set -o pipefail`, or write to
  a file and check the exit status. An empty listing is the expected result
  for the binary check, so an empty listing from a failed command must not
  pass for clean.

  - Author and committer metadata:
    `git log --all --format='%an <%ae> | %cn <%ce>' | sort | uniq -c`.
  - Commit messages: `git log --all --format=%B`.
  - Annotated tags: the tagger and the full annotation, since `git tag -n99`
    shows neither the tagger nor more than 99 lines:
    `git for-each-ref refs/tags --format='%(refname:short) %(taggername) %(taggeremail)%0a%(contents)'`.
  - Files git treats as binary, whose content gitleaks never sees:
    `git log --all -m --numstat --format= | awk '$1 == "-"'`.
  - Session ids in fixtures, since a test may have been written against a
    live session. List the UUIDs, then check each against
    `~/.claude/projects` and `~/.codex*/sessions`:
    `git log --all -m -p | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | sort -u`.
  - Everything on GitHub that goes public with the code: PR and issue
    bodies, review comments, and stale remote branches. Comment edit history
    is public too: delete old revisions from the comment's "edited" menu.

  **Run 2026-10-27:**
  - gitleaks was clean with `-m` across all refs.
  - No tags and no binary files.
  - Every commit is authored with a personal gmail address. This was accepted
    as is: the address is already public through another public repo, and a
    rewrite would change every SHA and break the merged PRs' commit links.
  - One real Codex session id was found, in `tests/test_codex_profile.py`. It
    is to be replaced in a normal commit. History keeps it, but it identifies
    a file on one machine and grants nothing.
  - One PR review comment named a private sibling repo. It was edited, and
    its old revision is to be deleted by hand.
  - A real Claude session id sits in a PR #6 comment. That is pending a
    decision.
- **README for strangers.**
  - Requirements: macOS, iTerm2, and Python 3.9 or later. The macOS system
    `python3` is 3.9; the suite passes on 3.9–3.14 (checked 2026-10-27).
  - Install from a fresh clone.
  - Update "What has been exercised for real" once a real reboot has been
    restored from.
  - A one-paragraph "what it does / doesn't do" at the top.
- **Repo-local files.** `CLAUDE.md` and `AGENTS.md` are fine to publish: they
  are agent instructions, with no personal paths. Re-read them anyway.
- **GitHub.** Branch protection on `main` becomes available for free on a
  public repo. It replaces the pre-push hook as the enforcement, as AGENTS.md
  already asks. Run gitleaks in CI on every PR, so the gate no longer depends
  on each clone enabling the hook.
