#!/bin/sh
# Fail if a tracked file contains a home directory path or an email at one of
# the common consumer providers.
#
# This repo is intended to become public, and history stays visible once it is,
# so the check belongs before the push rather than in a later cleanup.
#
# Four deliberate details:
#   - it scans `git ls-files`, not a list of extensions, so extensionless files
#     like bin/agent-tabs and .githooks/pre-push are covered too;
#   - `--` terminates grep's options, because a tracked file whose name begins
#     with a dash would otherwise be read as one, and grep would fail rather
#     than scan. stderr is deliberately not silenced, so that failure is loud;
#   - it excludes itself by fixed-string prefix, because a scanner necessarily
#     contains the pattern it searches for. Nothing else is excluded;
#   - it matches per occurrence (grep -o), not per line, so a line holding the
#     allowed placeholder alongside a real path still reports the real one.
#
# Placeholders in examples use /Users/you, which is allowed.
SELF="tools/check-no-personal-paths.sh"
PATTERN='/Users/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@(gmail|googlemail|icloud|yahoo|hotmail|outlook)\.'

cd "$(git rev-parse --show-toplevel)" || exit 2

files=$(git ls-files)
if [ -z "$files" ]; then
    exit 0
fi

# grep -on prints file:line:match, one row per occurrence. The matched text is
# the final colon-separated field, so the placeholder can be dropped on its own
# rather than taking the whole line with it.
hits=$(
    git ls-files -z \
        | xargs -0 grep -onIE "$PATTERN" -- \
        | awk -F: -v self="${SELF}:" '
            index($0, self) == 1 { next }
            $NF == "/Users/you"  { next }
            { print }
        '
)

if [ -n "$hits" ]; then
    echo "Personal paths or emails found in tracked files:" >&2
    echo "$hits" >&2
    echo "" >&2
    echo "Use /Users/you in examples. Machine-specific values belong in" >&2
    echo "~/.agent-tabs/config.json, outside the repo." >&2
    exit 1
fi

exit 0
