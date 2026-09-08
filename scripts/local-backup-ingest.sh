#!/usr/bin/env bash
#
# THE BACKUP INGEST — this machine, when GitHub Actions has stopped answering.
#
# `.github/workflows/ingest.yml` runs every two hours and is the primary. This
# is not a second scheduler racing it: it does nothing at all unless the data
# the SITE IS ACTUALLY SERVING has gone stale, which is the only condition that
# matters and the only one worth acting on.
#
# ⚠️ STALENESS IS MEASURED ON raw.githubusercontent.com, NOT ON THE LOCAL CLONE
# AND NOT ON THE ACTIONS RUN LIST. The page fetches `output/ev_status.json` from
# raw; if raw is current the reader is fine no matter what the workflow's badge
# says, and if raw is stale the reader is not, no matter how green Actions
# looks. Measuring the thing the reader sees is the whole design.
#
# The threshold is deliberately well past one missed slot. ingest.yml's own
# header records the measured behaviour of shared runners: on 2026-09-06 the
# 06:00, 12:00 and 18:00 slots fired 3.9, 2.6 and 1.8 hours late and a fourth
# was skipped outright. A threshold under four hours would fire on a normal bad
# morning and produce exactly the concurrent-writer conflicts the workflow's
# `concurrency` block exists to prevent.
#
# Run by launchd hourly (~/Library/LaunchAgents/com.electindex.evbackup.plist).
# Run it by hand any time; --force skips the staleness gate.

set -euo pipefail

REPO="/Users/davisliggett/ElectIndex/earlyvote"
BRANCH="main"
STATUS_URL="https://raw.githubusercontent.com/ElectIndex/26_early_vote/main/output/ev_status.json"
THRESHOLD_HOURS="${EV_BACKUP_THRESHOLD_HOURS:-5}"
LOCK="/tmp/electindex-ev-backup.lock"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

say() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# One at a time. Two ingests would each commit their own rewrite of output/ and
# the second would spend its life rebasing onto the first -- the same reason
# ingest.yml declares `concurrency`. mkdir is the atomic test-and-set.
if ! mkdir "$LOCK" 2>/dev/null; then
	say "another backup run holds $LOCK; leaving it alone"
	exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

cd "$REPO"

# ---------------------------------------------------------- the staleness gate
if [ "$FORCE" -eq 0 ]; then
	published="$(curl -fsS --max-time 30 "$STATUS_URL" 2>/dev/null || true)"
	if [ -z "$published" ]; then
		# No network, or GitHub is down. Either way this machine cannot publish
		# either, so there is nothing useful to do and nothing to shout about.
		say "could not read the published status file; skipping"
		exit 0
	fi

	age_hours="$(printf '%s' "$published" | /usr/bin/python3 -c '
import json, sys
from datetime import datetime, timezone
d = json.load(sys.stdin)
# `generated_at` is written by publish.write_status on EVERY run, including a
# run where every state was pending -- which is exactly the point. A file whose
# generated_at is moving is a pipeline that is alive; rows can legitimately be 0
# all September.
stamp = d.get("generated_at") or ""
try:
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
except ValueError:
    print("999")  # unparseable == treat as stale, and say so below
    sys.exit(0)
print(f"{(datetime.now(timezone.utc) - t).total_seconds() / 3600:.2f}")
')"

	if [ "$(printf '%s < %s\n' "$age_hours" "$THRESHOLD_HOURS" | bc -l)" -eq 1 ]; then
		say "published data is ${age_hours}h old (< ${THRESHOLD_HOURS}h) — Actions is fine, standing down"
		exit 0
	fi
	say "published data is ${age_hours}h old (>= ${THRESHOLD_HOURS}h) — Actions looks stuck, taking over"
else
	say "--force: skipping the staleness gate"
fi

# ------------------------------------------------------------------- the work
say "syncing $BRANCH"
git fetch --quiet origin "$BRANCH"
# --autostash so a dirty working tree (a theme edit, a scratch file) does not
# stop the backup from doing its job. It is restored afterwards.
git pull --rebase --autostash --quiet origin "$BRANCH"

say "ev ingest"
PYTHONPATH=src /usr/local/bin/python3 -m ev ingest

# ⚠️ SAME TWO COMMANDS, SAME ORDER, SAME LACK OF FILTERS as ingest.yml's
# "Refresh the derived models" step. party_estimate.csv and counterfactual.csv
# are pure functions of the tables ingest just rewrote, and both writers REPLACE
# on a full rebuild and MERGE on a filtered one -- a --state run here would
# leave rows the model no longer produces in the file forever.
say "ev estimate"
PYTHONPATH=src /usr/local/bin/python3 -m ev estimate
say "ev counterfactual"
PYTHONPATH=src /usr/local/bin/python3 -m ev counterfactual

# ONLY output/. Nothing else this run touched may ride along -- an editable
# install drops an egg-info and pip leaves caches, and neither belongs in a
# data commit. Copied from ingest.yml deliberately rather than broadened.
git add -- output
if git diff --cached --quiet; then
	say "output/ unchanged — nothing to commit"
	exit 0
fi

git -c user.name="electindex-local" \
    -c user.email="liggett.davis@gmail.com" \
    commit --quiet -m "data: early vote $(date -u +%F) (local backup run)"

for attempt in 1 2; do
	if git pull --rebase --autostash --quiet origin "$BRANCH" \
	   && git push --quiet origin "HEAD:$BRANCH"; then
		say "pushed on attempt $attempt"
		exit 0
	fi
	say "push attempt $attempt failed; retrying in 10s"
	sleep 10
done

say "ERROR: could not push output/ after 2 attempts"
exit 1
