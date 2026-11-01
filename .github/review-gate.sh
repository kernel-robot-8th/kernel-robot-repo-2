#!/usr/bin/env bash
# Review gate that switches itself on by headcount.
# People = human authors of main commits in the last WINDOW_DAYS days, of open PRs, and this PR.
# One person: pass (solo work needs no reviewer). Two or more: a non-author's latest review must approve.
# Env: GH_TOKEN, REPO (owner/name), PR (number), AUTHOR (PR author login),
#      WINDOW_DAYS (default 14), IGNORE (space-separated logins that are agents, not people).
set -euo pipefail
since=$(date -u -d "${WINDOW_DAYS:-14} days ago" +%Y-%m-%dT%H:%M:%SZ)
people=$({
  gh api "repos/$REPO/commits?sha=main&since=$since&per_page=100" --paginate \
    --jq '.[].author | select(. != null and .type == "User") | .login'
  gh api "repos/$REPO/pulls?state=open&per_page=100" --paginate \
    --jq '.[].user | select(.type == "User") | .login'
  echo "$AUTHOR"
} | sort -u | grep -vxF -f <(tr ' ' '\n' <<<"${IGNORE:-}" | grep .) || true)
count=$(grep -c . <<<"$people" || true)
echo "active people (${WINDOW_DAYS:-14}d): $count"; echo "$people"
if [ "$count" -le 1 ]; then
  echo "solo: review not required"
  exit 0
fi
# shortcut: first 100 reviews only; paginate if a PR ever collects more.
approvers=$(AUTHOR="$AUTHOR" gh api "repos/$REPO/pulls/$PR/reviews?per_page=100" --jq '
  map(select(.state == "APPROVED" or .state == "CHANGES_REQUESTED" or .state == "DISMISSED"))
  | group_by(.user.login) | map(last) | .[]
  | select(.state == "APPROVED" and .user.login != env.AUTHOR) | .user.login')
if [ -z "$approvers" ]; then
  echo "::error::$count people are active, so a teammate other than $AUTHOR must approve this PR"
  exit 1
fi
echo "approved by: $approvers"
