"""Is Marcus's off-box backup still being written, or has the hourly job died quietly?

Marcus is the owner's training log. Its whole state is one 11KB JSON document on a
1Gi node-local PersistentVolume with no snapshot and no replica, which is what
idea #233 is about. `platform-config#603` put an hourly CronJob in `agents` that
reads `GET /api/state` over HTTP and commits it to `SokratesAI/marcus-backup`.

Nothing read whether that job still works. The gap is the same one
`schedule_health` found on GitHub Actions and `heartbeat_health` found on Agora,
one layer over again: a backup job that has been dead for a week and a backup
job with nothing new to save produce **the same silence**. Only a dated stamp
separates them, and the job writes one for exactly that reason -- `last-run.txt`
carries the UTC date, so a working job commits at least once per UTC day even
when his data has not moved, and at most once when it has not.

**This reads GitHub, never the cluster, and that is the design.** The subject is
a backup of server1, so a check that runs against server1's own CronJob object
goes silent in precisely the incident the backup exists for -- the box dying.
`preflight`'s own `SUBJECT` table calls that an on-box status readout rather
than a monitor. The newest commit on the backup repo is visible from anywhere
with a token, and it is the job's own output rather than a report about it.

**Why the threshold is 26 hours and not a number I liked.** The stamp is a
date, so two consecutive successful runs can legitimately be 24 hours apart --
23:20 UTC one day and 00:20 the next is one minute, but 00:20 and 23:20 is
23 hours, and the worst legal gap is a shade under 24. 26 gives two hours of
slack for a missed run or two without inventing a window: one skipped hourly
run must not read as a dead backup, and a whole day of them must.

**`NEVER BACKED UP` and `STALE` are separate verdicts**, the same call
`heartbeat_health` makes on `OFF` versus `OVERDUE` and `schedule_health` on
`NEVER FIRED` versus `OVERDUE`. A repo holding only its seed commit means the
job has never once completed, which is a wiring problem; a repo whose newest
backup is three days old means it worked and then stopped, which is an
incident. They are found the same way and fixed differently, so merging them
into one red would send a cycle looking in the wrong place.

**The stamp file is the measurement, not the commit subject.** Matching
`[backup] marcus state rev ...` would re-spell the job's own message here, so
renaming the message in `platform-config` would quietly turn this green
forever. `last-run.txt` is read from the default branch by path: it exists only
because the job wrote it, and its content is the date the job last ran.
"""

import argparse
import datetime
import json
import subprocess
import sys
import zoneinfo

BACKUP_REPO = "SokratesAI/marcus-backup"

#: The dated liveness stamp `platform-config`'s `marcus-backup.py` writes on
#: every successful run. Read by path rather than by commit message, so a
#: reworded commit subject cannot silence this check.
RUN_FILE = "last-run.txt"

#: Hours. See the module docstring: the stamp is a date, so the worst legal gap
#: between two successful runs is just under 24, and this adds two hours rather
#: than picking a round number.
STALE_AFTER_HOURS = 26

OSLO = zoneinfo.ZoneInfo("Europe/Oslo")


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def parse_stamp(text):
    """An RFC3339 stamp as an aware UTC datetime, or None if it is unreadable."""
    raw = (text or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def oslo(stamp):
    """An RFC3339 stamp as `YYYY-MM-DD HH:MM Oslo`, or the raw text if unreadable."""
    parsed = parse_stamp(stamp)
    if parsed is None:
        return stamp or "?"
    return parsed.astimezone(OSLO).strftime("%Y-%m-%d %H:%M Oslo")


def read_stamp_commit(run=None):
    """`(commit, error)` — the newest commit that touched `last-run.txt`.

    `None` for `commit` with no error means the file exists in no commit on the
    default branch, which is the `NEVER BACKED UP` case: the seed commit is
    there, the job's output is not.
    """
    caller = run or _gh
    code, out, err = caller([
        "api",
        f"repos/{BACKUP_REPO}/commits?path={RUN_FILE}&per_page=1",
    ])
    if code != 0:
        blob = (err or out or "").strip()
        return None, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        batch = json.loads(out)
    except ValueError as exc:
        return None, f"could not parse the commit list: {exc}"
    if not isinstance(batch, list):
        return None, "the commit list was not a list"
    if not batch:
        return None, None
    entry = batch[0]
    commit = entry.get("commit") or {}
    return {
        "sha": entry.get("sha") or "",
        "subject": ((commit.get("message") or "").splitlines() or [""])[0],
        "date": ((commit.get("committer") or {}).get("date")) or "",
    }, None


def judge(commit, now):
    """`(verdict, age_hours)` — one of `never`, `stale`, `unreadable`, `fresh`."""
    if commit is None:
        return "never", None
    written = parse_stamp(commit.get("date"))
    if written is None:
        return "unreadable", None
    age = (now - written).total_seconds() / 3600.0
    return ("stale" if age > STALE_AFTER_HOURS else "fresh"), age


def format_report(commit, verdict, age, error):
    lines = []
    if error:
        lines.append(f"CANNOT SEE — {BACKUP_REPO} could not be read: {error}")
        lines.append(
            "That is not the same as a healthy backup. Nothing below is a claim "
            "about whether Marcus's data is being saved."
        )
        return "\n".join(lines)

    if verdict == "never":
        lines.append(
            "NEVER BACKED UP — %s carries no commit touching %s, so the hourly job "
            "has never once completed." % (BACKUP_REPO, RUN_FILE)
        )
        lines.append(
            "  Marcus's only copy of his training log is the 1Gi node-local volume. "
            "Read the CronJob's pods: kubectl logs -n agents job/marcus-backup-<n>"
        )
        return "\n".join(lines)

    if verdict == "unreadable":
        lines.append(
            "CANNOT SEE — the newest %s commit carries a date I could not parse: %r"
            % (RUN_FILE, commit.get("date"))
        )
        return "\n".join(lines)

    when = oslo(commit.get("date"))
    if verdict == "stale":
        lines.append(
            "BACKUP STALE — Marcus's last saved state is %.1f hours old, past the %d-hour "
            "threshold, so the hourly job has stopped succeeding." % (age, STALE_AFTER_HOURS)
        )
        lines.append(f"  newest {RUN_FILE} commit {commit['sha'][:12]} at {when}")
        lines.append(f"  {commit['subject']}")
        lines.append(
            "  Read the CronJob's pods: kubectl logs -n agents job/marcus-backup-<n>. "
            "A refused connection to marcus:8080 is the netpol ipset race, not a policy gap."
        )
        return "\n".join(lines)

    lines.append(
        "Marcus's backup is %.1f hours old, inside the %d-hour threshold: %s at %s."
        % (age, STALE_AFTER_HOURS, BACKUP_REPO, when)
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    commit, error = read_stamp_commit()
    now = datetime.datetime.now(datetime.timezone.utc)
    verdict, age = judge(commit, now) if not error else (None, None)
    print(format_report(commit, verdict, age, error))
    if error or verdict == "unreadable":
        return 1
    return 2 if verdict in ("never", "stale") else 0


if __name__ == "__main__":
    sys.exit(main())
