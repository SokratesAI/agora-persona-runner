"""Did the deploy-rollback watchdog revert something, and how often does it fire?

The owner, `issues.md` 2026-09-03: *"When the platform-config CronJob watchdog
(#600) triggers a revert, it should leave a note/message with what and when, so
a Nova cycle can investigate why the deploy failed -- otherwise we have no
record of how often this fallback fires."*

`platform-config`'s `deploy-rollback` CronJob undoes a digest whose deploy never
came up, with no human in the loop. It is the layer that catches what CI's
smoke test cannot reproduce, and when it fires, something upstream of it is
wrong. Nothing here read that. The evidence lived in the Job pod's log, and
`successfulJobsHistoryLimit` is 3 at a five-minute cadence, so the crash
evidence is garbage-collected about fifteen minutes after it is printed --
roughly one Nova cycle. A revert could fire, fix the symptom, and be gone before
any cycle woke up.

The revert commit is the record: it is off this box, it is permanent, and the
job was already writing it. platform-config#601 puts `decide()`'s own verdict --
which pod, how many restarts, which exit code -- and the UTC instant into the
commit body, and stamps every automatic revert with a trailer. This reads them.

**Why the raising rule is "the newest revert is still HEAD" rather than "a
revert happened recently".** A time window has to pick a number, and every
cycle inside it sees the same finding, so a six-hour window at an 18-minute
cadence is twenty cycles told to act on one incident. `-config` carries one
commit per digest and CI writes a new one on every merge, so a revert that is
no longer HEAD has been superseded by a deploy somebody shipped afterwards --
the incident is over and the entry is history. A revert that *is* HEAD means
this loop is running rolled-back code and nobody has shipped the fix yet, which
is worth a cycle's attention every time it is true and stops being true on its
own. No state file, and nothing to acknowledge.
"""

import argparse
import datetime
import json
import subprocess
import sys
import zoneinfo

CONFIG_REPO = "SokratesAI/agora-persona-runner-config"
BRANCH = "main"

#: The line platform-config's `revert_message()` writes into every automatic
#: revert. Matched as a whole line, never as a substring: a substring match
#: turns a *mention* of the marker -- in this docstring, in a journal entry
#: quoted into a commit -- into a hit, which is the failure this loop keeps
#: paying for elsewhere.
TRAILER = "Automatic-Rollback: deploy-rollback"

#: Reverts pushed before platform-config#601 carry no trailer. This exact line
#: is what their body ends with, so they are still findable, and still by whole
#: line rather than by substring. It stays until no revert older than it can be
#: in the swept window, which is a judgement about this repo's history rather
#: than a rule, so it is not on a timer.
LEGACY_LINE = "Opened by the deploy-rollback CronJob, not by a person. At most one"

#: How many commits to read. `-config` holds one commit per digest, so this is
#: a few days of merges rather than the whole history -- and the report says
#: how far back it actually reached, because "no reverts" is only ever a claim
#: about the window that was read.
MAX_COMMITS = 300
PER_PAGE = 100

OSLO = zoneinfo.ZoneInfo("Europe/Oslo")


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def is_automatic_revert(message):
    """True when this commit was written by the watchdog, judged line by line."""
    lines = (message or "").splitlines()
    return TRAILER in lines or LEGACY_LINE in lines


def reason_of(message):
    """The `Measured at ...` line the watchdog wrote, or None on a legacy commit.

    A revert from before platform-config#601 genuinely carries no reason, and
    saying so is the honest answer -- inventing one out of the subject line
    would read as evidence that was never recorded.
    """
    for line in (message or "").splitlines():
        if line.startswith("Measured at "):
            return line
    return None


def oslo(stamp):
    """An RFC3339 stamp as `YYYY-MM-DD HH:MM Oslo`, or the raw text if unreadable.

    Every timestamp GitHub returns is UTC and this loop writes Oslo; the
    conversion is here rather than at each call site so no caller can forget it.
    """
    text = (stamp or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return stamp or "?"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(OSLO).strftime("%Y-%m-%d %H:%M Oslo")


def read_commits(run=None):
    """`(commits, error)` — the newest `MAX_COMMITS` on `-config`, newest first."""
    commits = []
    caller = run or _gh
    for page in range(1, (MAX_COMMITS // PER_PAGE) + 1):
        code, out, err = caller([
            "api",
            f"repos/{CONFIG_REPO}/commits?sha={BRANCH}&per_page={PER_PAGE}&page={page}",
        ])
        if code != 0:
            blob = (err or out or "").strip()
            return commits, blob.splitlines()[0] if blob else f"gh exited {code}"
        try:
            batch = json.loads(out)
        except ValueError as exc:
            return commits, f"could not parse the commit list: {exc}"
        if not isinstance(batch, list):
            return commits, "the commit list was not a list"
        for entry in batch:
            commit = entry.get("commit") or {}
            commits.append({
                "sha": entry.get("sha") or "",
                "message": commit.get("message") or "",
                "date": ((commit.get("committer") or {}).get("date")) or "",
            })
        if len(batch) < PER_PAGE:
            break
    return commits, None


def judge(commits):
    """`(reverts, pending)` — every automatic revert read, and whether one is HEAD.

    `pending` is the raising condition and it is deliberately positional: it is
    true only when the newest commit on the branch is itself a watchdog revert,
    which is the state "this loop is running rolled-back code and no fix has
    shipped". A revert further down the list has been superseded by a later
    deploy and is history.
    """
    reverts = [c for c in commits if is_automatic_revert(c["message"])]
    pending = bool(commits) and is_automatic_revert(commits[0]["message"])
    return reverts, pending


def format_report(commits, reverts, pending, error):
    lines = []
    if error:
        lines.append(f"CANNOT SEE — {CONFIG_REPO} could not be read: {error}")
        lines.append(
            "That is not the same as no reverts. Nothing below is a claim about "
            "what the watchdog did."
        )
        return "\n".join(lines)

    oldest = oslo(commits[-1]["date"]) if commits else "?"
    if pending:
        head = reverts[0]
        lines.append(
            "REVERT STANDING — the newest commit on %s is an automatic rollback, so "
            "this loop is running the previous image and no fix has shipped yet."
            % CONFIG_REPO
        )
        lines.append(f"  {head['sha'][:12]} at {oslo(head['date'])}")
        subject = (head["message"].splitlines() or [""])[0]
        lines.append(f"  {subject}")
        reason = reason_of(head["message"])
        lines.append(f"  {reason}" if reason
                     else "  no reason recorded — this revert predates platform-config#601")
        lines.append(
            "  Investigate why that digest died before shipping anything on top of it; "
            "the next merge to the runner buries this line."
        )

    older = reverts[1:] if pending else reverts
    if older:
        lines.append(
            "PREVIOUS AUTOMATIC REVERTS — %d, already superseded by a later deploy, "
            "so history rather than something to act on:" % len(older)
        )
        for commit in older:
            lines.append(f"  {commit['sha'][:12]} at {oslo(commit['date'])}")
            reason = reason_of(commit["message"])
            if reason:
                lines.append(f"    {reason}")

    if not reverts:
        lines.append(
            "The watchdog has not fired in the window below. It is a CronJob in `agents`, "
            "every 5 minutes; that it did nothing is what a healthy week looks like."
        )
    # Last, because `preflight` shows the final line as this check's one-line
    # summary, and the count with its window is the answer to the question that
    # put this tool here: how often does this fire, and over what.
    lines.append(
        "%d automatic revert(s) in the newest %d commit(s) of %s, back to %s."
        % (len(reverts), len(commits), CONFIG_REPO, oldest)
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    commits, error = read_commits()
    reverts, pending = judge(commits)
    print(format_report(commits, reverts, pending, error))
    if error:
        return 1
    if not commits:
        print("No commits were read at all, which is not a state this repo can be in.")
        return 1
    return 2 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
