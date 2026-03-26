#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "not a git repository" >&2
  exit 1
}

if git diff --quiet && git diff --cached --quiet; then
  echo "Nothing to commit, working tree clean."
  exit 0
fi

msg="${1:-update}"
git add -A
git commit -m "$msg"
git push
