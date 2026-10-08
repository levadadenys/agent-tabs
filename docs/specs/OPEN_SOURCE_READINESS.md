# Spec: open-source readiness

**Status:** saved for later, not started. To be picked up when the repo is made
public. Written 2026-10-08, against `main` at the merge of #5.

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
| `COLLAPSE_STREAK` | `snapshot.py` | 3 captures | drop accepted after ~1 min | after 45 s (settle governs) | after ~15 min |
| `COLLAPSE_SETTLE_SECONDS` | `snapshot.py` | 45 s | already time-based | same | same |

History length also bounds the pre-reboot pin's fallback
(`_fullest_pre_boot_archive`), which can only find what history still holds.

### Design

1. **Setting.** `"interval_seconds"` in `~/.agent-tabs/config.json`, default
   30. Clamp it to 10–300: below 10 s, a ~2 s capture is a real share of the
   machine; above 300 s, the crash-loss window stops being a rounding error.
   An out-of-range or non-integer value is clamped, or falls back to 30, with a
   line in `install`'s output. It must never refuse to install.
2. **Applying it.** launchd reads the interval from the job file, so a change
   takes effect only after `agent-tabs install --force` rewrites the plist.
   Say so in the README, in `config.example.json`, and in `install`'s output
   (`recorder loaded, every Ns`, which already exists). `INTERVAL_SECONDS`
   becomes the default rather than the value.
3. **History by time, with a floor.** Replace the fixed 60-capture prune with:
   keep an archive if it is among the newest `HISTORY_MIN` (60), **or** younger
   than `HISTORY_WINDOW_SECONDS` (1800). Age comes from the archive filename,
   as `_archive_time` already reads it. Cap the total at
   `HISTORY_WINDOW_SECONDS / 10` (180) so the fastest interval stays bounded.
   - Why the floor: age alone would delete everything older than 30 minutes
     on the first capture after a long sleep or idle spell. That is exactly
     the pre-sleep history the pin fallback and the collapse guard read.
   - Pruning must not depend on the interval read from config: the recorder
     may be running an older plist than the config says. Time-based pruning
     makes that mismatch harmless.
4. **Collapse guard by time.** Accept a collapse once it has lasted
   `COLLAPSE_SETTLE_SECONDS` **and** at least 2 captures. That is not 3: at
   300 s, 3 captures would hold a genuine close-most-tabs for 15 minutes. Two
   captures still rule out a burst that lands at once, which is the case the
   count exists for. Re-check the reasoning in `snapshot.py`'s comments and
   the existing shutdown-guard tests before changing this. It is the most
   safety-relevant part of this spec.
5. **Config reading must be as robust as the snapshot readers.** Today
   `config.load()` catches `JSONDecodeError` and `OSError` only, so bad UTF-8
   raises `UnicodeDecodeError`. It also accepts non-object JSON, and a JSON
   list makes `config.update` raise. Install would now read the config too.
   Catch `ValueError` and require a dict, as `snapshot._read` does.

### Tests

- Clamping: 5 becomes 10, 9999 becomes 300, `"abc"` and `null` become 30, and
  a missing key gives 30. The plist carries the clamped value; drive
  `install`'s plist builder, not a helper.
- Pruning: with 30 s captures, about 30 minutes is kept. With 10 s captures,
  the cap holds. A 3-hour gap followed by a new capture keeps the 60 pre-gap
  archives.
- The collapse guard at 10 s, 30 s and 300 s: a burst of captures within
  seconds is never accepted, and a genuine drop is accepted after the settle
  time plus 2 captures.
- Damaged `config.json` (bad UTF-8, a list, `{"interval_seconds": []}`)
  never breaks install or a capture.
- Mutation-check each test, restoring with `cp`, as for every test in this
  repo.

### Docs

- README: Install (the setting and `install --force`), Snapshots and reboots
  (history by time, the derived numbers), Config.
- `config.example.json`: add `"interval_seconds": 30`.
- AGENTS.md: the history floor and why pruning must not depend on config.

## 2. Before flipping the repo public

- **License.** None yet. Pick one (MIT is the usual choice for a small tool)
  and add `LICENSE`.
- **History audit.** The pre-push hook scans each push, but scan the whole
  history once before going public, since anything committed is published
  with it:

  ```bash
  gitleaks git --no-banner          # every commit, all rules in .gitleaks.toml
  git log --all -p | grep -nE '/Users/[a-z]|@[a-z0-9-]+\.(org|com)' | head
  ```

  A full-history `gitleaks git --log-opts=--all` was clean on 2026-10-08;
  re-run it, since commits land after this. Known blind spots of the scan
  (see AGENTS.md): a home path inside a file gitleaks classifies as binary,
  and annotated tag messages. Check both by hand (`git tag -n99`).
- **README for strangers.**
  - Requirements: macOS, iTerm2, and the Python version actually needed.
  - Install from a fresh clone.
  - Update "What has been exercised for real" once a real reboot has been
    restored from.
  - A one-paragraph "what it does / doesn't do" at the top.
- **Repo-local files.** `CLAUDE.md` and `AGENTS.md` are fine to publish: they
  are agent instructions, with no personal paths. Re-read them anyway.
- **GitHub.** Branch protection on `main` becomes available for free on a
  public repo. It can then back up the pre-push hook. Moving the gitleaks gate
  into CI is the open idea from the hook's review history.
