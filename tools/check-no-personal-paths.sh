#!/bin/sh
# Fail if a tracked file contains a home directory path or a personal email.
#
# This repo is intended to become public, and history stays visible once it is,
# so the check belongs before the push rather than in a later cleanup.
#
# Two deliberate details:
#   - it scans `git ls-files`, not a list of extensions, so extensionless files
#     like bin/agent-tabs and .githooks/pre-push are covered too;
#   - it excludes itself, because a scanner necessarily contains the pattern it
#     searches for. Nothing else is excluded.
#
# Placeholders in examples use /Users/you, which is allowed.
SELF="tools/check-no-personal-paths.sh"
PATTERN='/Users/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@(gmail|googlemail|icloud|yahoo|hotmail|outlook)\.'

cd "$(git rev-parse --show-toplevel)" || exit 2

hits=$(
    git ls-files -z \
        | xargs -0 grep -nIE "$PATTERN" 2>/dev/null \
        | grep -v '/Users/you' \
        | grep -v "^${SELF}:"
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
