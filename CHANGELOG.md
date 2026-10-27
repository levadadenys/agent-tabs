# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

The first release: recording which agent session runs in which iTerm tab, and replaying it after
a crash or reboot.

### Recording
- A launchd agent captures every iTerm window and tab every 30 seconds: the working directory,
  the title, and the Claude, Codex or Grok session running there. It needs no iTerm restart and
  survives iTerm restarts (#2).
- A capture taken while tabs die during a shutdown cannot overwrite a good snapshot: one with no
  agent sessions is never promoted, and a collapsed one only once the drop has persisted (#3).
- The last snapshot from before a reboot is pinned in `pre-boot.json`, so starting a session
  before restoring does not lose the pre-reboot fleet (#5).
- Codex sessions resume under the account they ran as, derived from the rollout file's path
  (`codex_profiles` in `~/.agent-tabs/config.json`).

### Restoring
- `restore` refills the window it runs in; `restore N --new-window` rebuilds one snapshot window.
- `restore --all` pairs every live window with the snapshot window it came from, by tab title, as
  a one-to-one assignment. A pairing is used only when the evidence forces it; ties are reported,
  never guessed (#4).
- `restore --all --create-missing` rebuilds, in new windows, the snapshot windows iTerm did not
  restore (#5).
- A dry run unless `--go`. Never types into a tab holding live work or the tab it runs from, never
  starts a session that is already running anywhere, and never replays a command line containing
  shell syntax, zsh's included; `--go --force` is refused without a terminal. Busy tabs and
  running sessions are re-checked immediately before typing (#4, #5).

### Project
- Contributor guide, a pre-push hook that refuses home paths and personal emails (gitleaks), and
  a PR template (#1).
- MIT license (#7). CI on macOS for Python 3.9–3.14, with ruff, mypy, shellcheck and a
  full-history gitleaks scan (#8); the code base formatted with ruff (#9).
- CONTRIBUTING, SECURITY, this changelog, issue forms and Dependabot.
