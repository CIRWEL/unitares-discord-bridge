#!/usr/bin/env bash
# Verify ship.sh splits multi-line commit messages into title + body
# instead of dumping the whole message into the PR title (the bug that
# produced a 20-line title on PR #6).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SHIP="$SCRIPT_DIR/../scripts/dev/ship.sh"

fail() { echo "FAIL: $1" >&2; exit 1; }

# Note: command substitution $(...) strips trailing newlines, so all
# EXPECTED values below omit the final \n that the script prints.

# Case 1: single-line message → title = full message, body empty.
OUT=$("$SHIP" --split-preview "just a one-liner")
EXPECTED=$'just a one-liner\n===BODY==='
[[ "$OUT" == "$EXPECTED" ]] || fail "single-line: got $(printf '%q' "$OUT")"

# Case 2: conventional title + blank line + body.
MSG=$'fix(foo): subject line\n\nParagraph one.\n\nParagraph two.'
OUT=$("$SHIP" --split-preview "$MSG")
EXPECTED=$'fix(foo): subject line\n===BODY===\nParagraph one.\n\nParagraph two.'
[[ "$OUT" == "$EXPECTED" ]] || fail "multiline: got $(printf '%q' "$OUT")"

# Case 3: title with no blank separator (non-idiomatic but plausible).
MSG=$'subject\nbody immediately after'
OUT=$("$SHIP" --split-preview "$MSG")
EXPECTED=$'subject\n===BODY===\nbody immediately after'
[[ "$OUT" == "$EXPECTED" ]] || fail "no-separator: got $(printf '%q' "$OUT")"

echo "ok: all ship.sh split cases pass"
