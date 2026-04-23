#!/usr/bin/env bash
# ship.sh — agent-friendly commit-and-deliver
#
# Routes changes to the right delivery path based on what they touch:
#   - Runtime code (src/bridge/, pyproject.toml) → feature branch + PR +
#     auto-merge-on-green.
#   - Everything else (docs, tests, scripts/) → direct commit + push on
#     the current branch.
#
# The bridge bot runs continuously against Discord; a bad change can
# spam a live server. PR + CI gate keeps runtime changes auditable.
#
# Usage:
#   ./scripts/dev/ship.sh "commit message"
#   ./scripts/dev/ship.sh --classify          # just print "runtime" or "other"
#
# Requirements: staged changes (git add already done), gh CLI authed.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

RUNTIME_PATTERNS=(
    '^src/bridge/'
    '^pyproject\.toml$'
)

classify() {
    local files; files=$(git diff --cached --name-only)
    if [[ -z "$files" ]]; then
        echo "empty"; return
    fi
    while IFS= read -r f; do
        for pat in "${RUNTIME_PATTERNS[@]}"; do
            if [[ "$f" =~ $pat ]]; then
                echo "runtime"; return
            fi
        done
    done <<< "$files"
    echo "other"
}

# Split a multi-line commit message into PR title (first line) and body
# (remainder, leading blank separator line stripped per git convention).
# Two functions rather than one-with-nameref so we stay compatible with
# bash 3.2 shipped on macOS (which lacks `local -n`).
split_title() {
    printf '%s' "${1%%$'\n'*}"
}
split_body() {
    local msg="$1"
    local title="${msg%%$'\n'*}"
    if [[ "$msg" == "$title" ]]; then
        return 0
    fi
    local rest="${msg#*$'\n'}"
    printf '%s' "${rest#$'\n'}"
}

if [[ "${1:-}" == "--classify" ]]; then
    classify
    exit 0
fi

if [[ "${1:-}" == "--split-preview" ]]; then
    # Testing hook — prints title on one line, then a fixed marker, then
    # the raw body. Used by tests/test_ship_split.sh.
    printf '%s\n===BODY===\n%s' "$(split_title "${2:-}")" "$(split_body "${2:-}")"
    exit 0
fi

MESSAGE="${1:-}"
if [[ -z "$MESSAGE" ]]; then
    echo "usage: ship.sh \"commit message\"" >&2
    exit 2
fi

KIND=$(classify)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

case "$KIND" in
    empty)
        echo "nothing staged — stage files with 'git add' first" >&2
        exit 2 ;;
    runtime)
        # Split commit message: first line → PR title, rest → PR body.
        # Previously the whole multi-line MESSAGE was passed as --title,
        # producing a PR with a 20-line title and an empty body.
        TITLE="$(split_title "$MESSAGE")"
        BODY_REST="$(split_body "$MESSAGE")"
        if [[ -z "$BODY_REST" ]]; then
            BODY="Auto-shipped by ship.sh — runtime path. CI gate applies."
        else
            BODY="${BODY_REST}

---
Auto-shipped by ship.sh — runtime path. CI gate applies."
        fi
        SLUG=$(printf '%s' "$TITLE" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-40)
        NEW_BRANCH="codex/auto/$(date +%Y%m%d-%H%M%S)-${SLUG}"
        echo "[ship] runtime path → $NEW_BRANCH (PR + auto-merge)"
        git checkout -b "$NEW_BRANCH"
        git commit -m "$MESSAGE"
        git push -u origin "$NEW_BRANCH"
        PR_URL=$(gh pr create --title "$TITLE" --body "$BODY")
        echo "$PR_URL"
        gh pr merge --auto --squash "$PR_URL" || \
            echo "[ship] auto-merge not enabled (branch protection may require manual setup); PR is open"
        ;;
    other)
        echo "[ship] non-runtime → direct commit + push on $BRANCH"
        git commit -m "$MESSAGE"
        # Push to the same-name branch on origin, not whatever upstream tracks
        # (a feature branch may track master and would otherwise push ambiguously).
        git push origin "HEAD:$BRANCH"
        ;;
esac
