"""Are the off-box backups still being written, or has a job died quietly?

Two things here are backed up to a GitHub repository by a CronJob, and until
Cycle 864 only one of them was watched.

* **Marcus** is the owner's training log. Its whole state is one 11KB JSON
  document on a 1Gi node-local PersistentVolume with no snapshot and no
  replica, which is what idea #233 is about. `platform-config#603` put an
  hourly CronJob in `agents` that reads `GET /api/state` over HTTP and commits
  it to `SokratesAI/marcus-backup`.
* **The vault** is every note, board, journal entry and capture in this
  system -- the thing this loop reads at the top of every cycle and writes at
  the bottom of it. It lives in CouchDB in `obsidian`, on server1, and the
  `vault-backup` CronJob commits an hourly snapshot of it to
  `SokratesAI/vault`. **That check did not exist.** `cronjob_health` reads the
  CronJob object, which is an on-box status readout: it dies with the box, in
  exactly the incident a backup is for. The more valuable of the two datasets
  was the unwatched one.

Nothing read whether these jobs still work. The gap is the same one
`schedule_health` found on GitHub Actions and `heartbeat_health` found on
Agora, one layer over again: a backup job that has been dead for a week and a
backup job with nothing new to save produce **the same silence**.

**And "2 of 2" was the wrong denominator.** Cycle 883: this printed *"Judged 2
off-box backup(s) of 2"* every run, which reads as complete coverage and is
only ever a count of the backups I had told it about. The cluster holds eight
PersistentVolumeClaims. Six of them had no copy anywhere, and two of those six
are things whose loss costs the owner something real -- `agents/agora-data`,
682 MiB of every conversation in the platform since 19 July, and
`infra/whatsapp-bridge-auth`, the session that means he re-pairs his phone if
it goes. Neither had a backup, a snapshot or a replica, and no check anywhere
said so. So this now also sweeps the cluster's own claim list and reports every
volume nothing copies.

**Coverage is declared, never inferred from names.** Each `Backup` names the
claim it covers, and a claim is otherwise either in `ACKNOWLEDGED` with the
measurement that makes losing it cheap, or it is reported. Matching on names
would have been wrong in both directions here: `couchdb-compact` names
`couchdb` and copies nothing, and `marcus-backup` reads Marcus over HTTP and
never mentions `marcus-data` at all. A new claim nobody declares reads as
unprotected, which is the safe direction for a mistake to fall.

**And a third verdict, because two were not enough.** `agents/agora-data` got a
backup the same night (`platform-config/cronjobs/agora-backup.yaml`, nightly to the
NAS, first run verified by sha256 on both sides). It is not `covered` in the sense
the two above are: every `Backup` here is judged by reading GitHub, and the NAS is
not readable from this loop, so nothing watches whether that job still runs. It
prints as **backed up, freshness not judged** rather than joining the green count --
"a backup exists" and "a backup ran last night" are the two things this whole module
exists to keep apart.

**This reads GitHub, never the cluster, and that is the design.** The subject
is a backup of server1, so a check that runs against server1's own CronJob
object goes silent in precisely the incident the backup exists for -- the box
dying. `preflight`'s own `SUBJECT` table calls that an on-box status readout
rather than a monitor. The newest commit on the backup repo is visible from
anywhere with a token, and it is the job's own output rather than a report
about it.

**The two backups are separated by whether the job writes a dated stamp, and
that decides both the measurement and the threshold.**

*Marcus* writes `last-run.txt` carrying the UTC date, so a working job commits
at least once per UTC day even when his data has not moved. The stamp file is
the measurement, not the commit subject: matching `[backup] marcus state rev
...` would re-spell the job's own message here, so renaming the message in
`platform-config` would quietly turn this check green forever. The threshold is
**26 hours** and not a number I liked -- the stamp is a date, so two
consecutive successful runs can legitimately be 24 hours apart (23:20 UTC one
day and 00:20 the next is one minute, but 00:20 and 23:20 is 23 hours, and the
worst legal gap is a shade under 24). 26 gives two hours of slack: one skipped
hourly run must not read as a dead backup, and a whole day of them must.

*The vault* writes no stamp, so the measurement is the newest commit on the
default branch, and a commit only happens when a document actually changed.
That would be a weak signal for a quiet repository and is a strong one here,
because this loop writes to the vault several times an hour -- a claims ledger,
a journal entry, a digest, a board reply. Measured 2026-09-04 against the 15
hourly slots from 07:52Z to 21:52Z on 09-03: **14 of them produced a commit**,
and the single gap (19:52Z) is the known 09-03 server2 no-egress incident that
Cycle 858 traced. So the threshold is **3 hours**, two consecutive misses of an
hourly `50 * * * *` schedule.

**And an hour of silence on the vault is worth reporting even when the backup
job is fine**, which is the one way this check differs from Marcus's. If the
vault stopped changing, either the backup died or the loop that writes to it
did. Both are the incident. This cannot distinguish them and says so rather
than naming a cause.

**`NEVER BACKED UP` and `STALE` are separate verdicts**, the same call
`heartbeat_health` makes on `OFF` versus `OVERDUE` and `schedule_health` on
`NEVER FIRED` versus `OVERDUE`. A repo holding no measurable commit at all
means the job has never once completed, which is a wiring problem; a repo whose
newest backup is three days old means it worked and then stopped, which is an
incident. They are found the same way and fixed differently, so merging them
into one red would send a cycle looking in the wrong place.
"""

import argparse
import collections
import datetime
import json
import subprocess
import sys
import zoneinfo

OSLO = zoneinfo.ZoneInfo("Europe/Oslo")

#: One backup this loop can see from off the box.
#:
#: ``stamp_path`` is the dated liveness file the job writes on every successful
#: run, read by path rather than by commit message so a reworded commit subject
#: cannot silence the check. ``None`` means the job writes no stamp and the
#: newest commit on the default branch is the measurement instead.
#:
#: ``stale_after_hours`` is derived per backup in the module docstring; the two
#: numbers differ because the two measurements do.
Backup = collections.namedtuple(
    "Backup", "name repo stamp_path stale_after_hours subject fix covers"
)

BACKUPS = (
    Backup(
        name="marcus",
        repo="SokratesAI/marcus-backup",
        stamp_path="last-run.txt",
        stale_after_hours=26,
        subject="Marcus's training log",
        fix=(
            "Read the CronJob's pods: kubectl logs -n agents job/marcus-backup-<n>. "
            "A refused connection to marcus:8080 is the netpol ipset race, not a policy gap."
        ),
        covers="agents/marcus-data",
    ),
    Backup(
        name="vault",
        repo="SokratesAI/vault",
        stamp_path=None,
        stale_after_hours=3,
        subject="the vault — every note, board and journal entry here",
        fix=(
            "Read the CronJob's pods: kubectl logs -n obsidian job/vault-backup-<n>. "
            "This cannot tell a dead backup job from a vault nothing is writing to, "
            "and both are worth looking at."
        ),
        covers="obsidian/couchdb-data",
    ),
)

#: A claim that has a backup this module cannot judge the freshness of. `BACKUPS`
#: above are all read out of GitHub, which is the point -- an on-box status readout
#: dies in the incident a backup is for. `agora-backup` writes to the NAS over ssh,
#: which is not readable from here, so the honest verdict is "covered, freshness not
#: judged" rather than either silence or a green line. Kept separate from
#: `ACKNOWLEDGED` on purpose: those are volumes deliberately left unprotected, this
#: is a volume that is protected by something nothing is watching yet.
NOT_JUDGED = {
    "infra/whatsapp-bridge-auth": (
        "the whatsapp-auth-backup CronJob copies it to the NAS four times a day at "
        ":25 Oslo (platform-config/cronjobs/whatsapp-auth-backup.yaml); first run "
        "2026-09-04 06:25 archived 1774 files, 361,678 bytes to 170,594, verified by "
        "sha256 on both sides. Four a day rather than nightly because the session's "
        "keys rotate, so a stale copy can fail to restore. Nothing here reads the "
        "NAS, so this says a backup exists and not that it still runs"
    ),
    "agents/agora-data": (
        "the agora-backup CronJob copies it to the NAS nightly at 03:40 Oslo "
        "(platform-config/cronjobs/agora-backup.yaml); first run 2026-09-04 verified "
        "by sha256 on both sides. Nothing here reads the NAS, so this says a backup "
        "exists and not that it still runs"
    ),
}

#: A volume that is deliberately not backed up, and the measurement that makes
#: that the right call. This is not a list of what is unimportant -- it is a list
#: of claims whose contents can be rebuilt from something that still exists, so
#: losing the disk costs time rather than data. Each entry names what rebuilds it.
#: A claim that is not here and is not covered by a `Backup` above is reported.
ACKNOWLEDGED = {
    "agents/agora-claude-bridge-data": (
        "my own scratch workspace -- git checkouts and cycle drafts, every one of "
        "which is re-clonable from GitHub"
    ),
    "agents/sokrates-docs-data": (
        "measured at 0 bytes, 0 files by a read-only Job on server1 (Cycle 872) "
        "before it was moved to server2"
    ),
    "agents/data-redis-0": (
        "one 14-member sorted set whose newest entry is dated 2026-05-12, with no "
        "connected client (Cycle 875); the 235 KB on disk is append-only history"
    ),
    "infra/ollama-models": (
        "model blobs ollama re-pulls from upstream; losing them costs a download "
        "(Cycle 880 carried all 1.55 GiB across the cluster for that reason)"
    ),
}


def read_claims(run=None):
    """Every PersistentVolumeClaim in the cluster as `namespace/name`, sorted.

    Read from the cluster rather than from git on purpose, and for the same
    reason `running_images` does it: a claim ArgoCD has not synced is not one
    that holds bytes, and a claim nobody put in a repository still does.
    Returns `(claims, error)`; an unreadable cluster is an error, never an
    empty list, because "no volumes" and "I could not look" are opposite
    findings and this cluster demonstrably has some.
    """
    runner = run or (
        lambda args: subprocess.run(args, capture_output=True, text=True, timeout=60)
    )
    proc = runner(["kubectl", "get", "pvc", "-A", "-o", "json"])
    if proc.returncode != 0:
        return [], (proc.stderr or "kubectl exited %d" % proc.returncode).strip()
    try:
        payload = json.loads(proc.stdout)
    except ValueError as err:
        return [], "kubectl printed something that is not JSON: %s" % err
    claims = sorted(
        "%s/%s" % (item["metadata"]["namespace"], item["metadata"]["name"])
        for item in payload.get("items", [])
    )
    if not claims:
        return [], "kubectl answered with no claims at all, which this cluster has"
    return claims, None


def judge_coverage(claims):
    """Split claims into (covered, acknowledged, uncovered).

    Covered means a `Backup` above declares it. There is no name-matching here
    and that is deliberate: `couchdb-data` is named by `couchdb-compact`, which
    is a compaction job and not a copy of anything, so a job that merely
    mentions a volume would have read as coverage.
    """
    declared = {b.covers: b for b in BACKUPS if b.covers}
    covered, acknowledged, unjudged, uncovered = [], [], [], []
    for claim in claims:
        if claim in declared:
            covered.append((claim, declared[claim].name))
        elif claim in NOT_JUDGED:
            unjudged.append((claim, NOT_JUDGED[claim]))
        elif claim in ACKNOWLEDGED:
            acknowledged.append((claim, ACKNOWLEDGED[claim]))
        else:
            uncovered.append(claim)
    return covered, acknowledged, unjudged, uncovered


def format_coverage(claims, error):
    """The coverage section, and the status it contributes."""
    if error:
        return (
            "COVERAGE UNREADABLE — I could not list this cluster's volumes, so "
            "the count above is the backups I know of and not a statement about "
            "what is unprotected: %s" % error
        ), 1
    covered, acknowledged, unjudged, uncovered = judge_coverage(claims)
    lines = []
    for claim in uncovered:
        lines.append(
            "NOT BACKED UP — %s is held in exactly one place and nothing copies it "
            "anywhere. A lost disk is lost data." % claim
        )
    for claim, name in covered:
        lines.append("%s — backed up by the %s job judged above." % (claim, name))
    for claim, reason in unjudged:
        lines.append("%s — backed up, freshness NOT JUDGED here: %s." % (claim, reason))
    for claim, reason in acknowledged:
        lines.append("%s — deliberately not backed up: %s." % (claim, reason))
    lines.append(
        "Swept %d volume(s): %d backed up and judged, %d backed up but not judged, "
        "%d deliberately not, %d unprotected."
        % (len(claims), len(covered), len(unjudged), len(acknowledged), len(uncovered))
    )
    return "\n".join(lines), (2 if uncovered else 0)


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


def commits_query(backup):
    """The `gh api` path for the newest commit that counts as this backup's output.

    With a stamp file that is the newest commit touching it; without one it is
    the newest commit on the default branch, which is what the vault-backup job
    leaves behind because it writes no stamp.
    """
    if backup.stamp_path:
        return f"repos/{backup.repo}/commits?path={backup.stamp_path}&per_page=1"
    return f"repos/{backup.repo}/commits?per_page=1"


def read_stamp_commit(backup, run=None):
    """`(commit, error)` — the newest commit that counts as this backup's output.

    `None` for `commit` with no error means there is no such commit on the
    default branch, which is the `NEVER BACKED UP` case: the job's output is
    not there.
    """
    caller = run or _gh
    code, out, err = caller(["api", commits_query(backup)])
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


def judge(backup, commit, now):
    """`(verdict, age_hours)` — one of `never`, `stale`, `unreadable`, `fresh`."""
    if commit is None:
        return "never", None
    written = parse_stamp(commit.get("date"))
    if written is None:
        return "unreadable", None
    age = (now - written).total_seconds() / 3600.0
    return ("stale" if age > backup.stale_after_hours else "fresh"), age


def format_report(backup, commit, verdict, age, error):
    what = backup.stamp_path or "the default branch"
    lines = []
    if error:
        lines.append(f"CANNOT SEE — {backup.repo} could not be read: {error}")
        lines.append(
            "That is not the same as a healthy backup. Nothing here is a claim "
            "about whether %s is being saved." % backup.subject
        )
        return "\n".join(lines)

    if verdict == "never":
        lines.append(
            "NEVER BACKED UP — %s carries no commit on %s, so the hourly job has "
            "never once completed." % (backup.repo, what)
        )
        lines.append(f"  {backup.subject} has no off-box copy at all.")
        lines.append(f"  {backup.fix}")
        return "\n".join(lines)

    if verdict == "unreadable":
        lines.append(
            "CANNOT SEE — the newest %s commit on %s carries a date I could not "
            "parse: %r" % (backup.repo, what, commit.get("date"))
        )
        return "\n".join(lines)

    when = oslo(commit.get("date"))
    if verdict == "stale":
        lines.append(
            "BACKUP STALE — the newest saved copy of %s is %.1f hours old, past the "
            "%d-hour threshold, so the hourly job has stopped succeeding."
            % (backup.subject, age, backup.stale_after_hours)
        )
        lines.append(f"  newest {backup.repo} commit {commit['sha'][:12]} at {when}")
        lines.append(f"  {commit['subject']}")
        lines.append(f"  {backup.fix}")
        return "\n".join(lines)

    lines.append(
        "%s: the backup of %s is %.1f hours old, inside the %d-hour threshold — %s."
        % (backup.name, backup.subject, age, backup.stale_after_hours, when)
    )
    return "\n".join(lines)


def status_for(verdict, error):
    """The exit status one backup contributes: 2 actionable, 1 unreadable, 0 clean."""
    if error or verdict == "unreadable":
        return 1
    return 2 if verdict in ("never", "stale") else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    now = datetime.datetime.now(datetime.timezone.utc)
    reports = []
    statuses = []
    for backup in BACKUPS:
        commit, error = read_stamp_commit(backup)
        verdict, age = judge(backup, commit, now) if not error else (None, None)
        reports.append(format_report(backup, commit, verdict, age, error))
        statuses.append(status_for(verdict, error))
    print("\n".join(reports))
    print(
        "Judged %d off-box backup(s) of %d, read from GitHub rather than from this "
        "cluster." % (len(BACKUPS) - statuses.count(1), len(BACKUPS))
    )
    claims, claims_error = read_claims()
    coverage, coverage_status = format_coverage(claims, claims_error)
    print(coverage)
    statuses.append(coverage_status)
    if 2 in statuses:
        return 2
    return 1 if 1 in statuses else 0


if __name__ == "__main__":
    sys.exit(main())
