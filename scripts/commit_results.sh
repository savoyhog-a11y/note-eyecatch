#!/usr/bin/env bash
# Commit and push results/ (safe to call concurrently; serialized with flock). Usage: commit_results.sh "message"
# On a rebase conflict our own results win (-X theirs keeps the commit being replayed); a stuck rebase is
# aborted before retrying, so one conflict can no longer block every later push.
exec 9>/tmp/commit_results.lock
flock 9
cd "$(git rev-parse --show-toplevel)"
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git rebase --abort 2>/dev/null || true
git add results
if git diff --cached --quiet && [ -z "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]; then exit 0; fi
git diff --cached --quiet || git commit -q -m "${1:-results}"
for i in 1 2 3 4 5; do
  if git pull -q --rebase -X theirs --autostash && git push -q; then exit 0; fi
  git rebase --abort 2>/dev/null || true
  sleep $((i * 5))
done
echo "commit_results: push failed after retries (commits are kept locally and retried at the next call)" >&2
exit 1
