#!/usr/bin/env bash
# Saves only the data files on top of whatever is newest in the repo, so it can never conflict with code changes.
cd "$(dirname "$0")"
files="docs/data.json docs/status.json docs/history.csv data/state.json"
for i in 1 2 3; do
  git fetch -q origin main && git reset -q --mixed origin/main
  git add $files
  if git diff --cached --quiet; then echo "nothing changed"; exit 0; fi
  git commit -q -m "GEX update $(TZ=America/New_York date '+%Y-%m-%d %H:%M ET')"
  if git push -q origin HEAD:main; then echo "saved"; exit 0; fi
  sleep 5
done
echo "::error::Could not save the new data to the repo"
exit 1
