#!/usr/bin/env python3
"""nova-deadman — the off-box half of idea #113's slice 1.

Every watcher this cluster had ran *on* the cluster: Prometheus has no
alerting rules, there is no Alertmanager, the stall notifier lives in
`nova-site`, and the Web Push it sends is signed by `agora`. All of it is
on `server1`, which is the only node. On 2026-08-24 the box went down and
his phone stayed quiet, because a watcher for a machine cannot run on that
machine.

`offbox/nova_watch.py` is the answer that needs a second machine, and both
of its inputs have to be handed over by a human, so it has not run yet.
This is the half that needs neither. It inverts the question: instead of
something asking the cluster whether it is alive -- which requires the
asker to survive the outage -- the cluster **pushes** a ping outward and
GitHub Actions alerts when the ping *stops*. Silence is the alarm, and
silence is exactly what a dead box produces.

The ping is a forced push of an empty commit to `refs/nova/alive` in this
repo, from `cronjobs/nova-alive-ping.yaml` in platform-config. Three
things make that ref the channel rather than a branch or a file:

- a push to `refs/nova/*` starts no workflow (only `refs/heads/*` and
  `refs/tags/*` do), so 288 pings a day cost zero Actions minutes,
- it adds no commit to any branch, so the history of this repo is
  untouched and the objects are unreachable and get collected,
- the commit's own committer date is the timestamp, so nothing has to
  parse a payload we wrote.

This repo is **public**, which is the other half of why the alarm can
live here: scheduled Actions on a public repo are free, and the org has
had at least one run refused for billing this month (`SokratesAI/sokrates-docs`,
2026-08-21). A watchdog that cannot run is worse than none, because it
looks like coverage.

Exit contract, the same one `security_alerts` and `heartbeat_health` use:
2 means the ping has stopped, 1 means the ref could not be read -- which
never reads as clean -- and 0 means the cluster pinged inside the grace.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("DEADMAN_REPO", "SokratesAI/agora-persona-runner")
REF = os.environ.get("DEADMAN_REF", "nova/alive")
# Six missed pings at the 5-minute schedule would be half an hour, which is
# one Nova cycle and far too twitchy: a `Recreate` rollout of the runner or
# a couchdb compaction can eat that without anything being wrong. The grace
# is derived from the slowest thing that legitimately stops the pinger --
# a node reboot plus image pulls -- rounded up to the hour, and a real
# outage stays detected in well under the three hours he waited on 08-24.
GRACE = timedelta(minutes=int(os.environ.get("DEADMAN_GRACE_MINUTES", "60")))
TITLE = "nova-deadman: the cluster has stopped pinging"
# Who the alarm is addressed to. An issue on this repo notifies nobody by
# default: measured 2026-08-27, `subscribers_count` is **0**, so a plain
# bot-authored issue lands in a repository no human is watching. An
# assignment and an @mention are "participating" notifications, which
# every GitHub account receives under its default setting whether or not
# it watches the repo -- so addressing the alarm is what turns it from a
# row on a page into something that can reach him. The last hop, whether
# his phone accepts GitHub pushes, is his to confirm and cannot be read
# from any API.
ASSIGNEE = os.environ.get("DEADMAN_ASSIGNEE", "EdvardGB")


def assess(pinged_at: datetime | None, now: datetime, grace: timedelta) -> tuple[str, str]:
    """Pure decision: (verdict, one-line reason).

    `None` is deliberately not folded into STALE. A ref that has never been
    written and a ref that stopped being written are opposite findings --
    the first says the pinger was never deployed, the second says the box
    is gone -- and merging them is the mistake `agentic_health` had to
    correct one layer up.
    """
    if pinged_at is None:
        return "NEVER", f"{REF} does not exist: the pinger has never run"
    age = now - pinged_at
    mins = int(age.total_seconds() // 60)
    if age > grace:
        return "STALE", (
            f"last ping {pinged_at.isoformat()} — {mins} minute(s) ago, "
            f"grace is {int(grace.total_seconds() // 60)}"
        )
    return "OK", f"last ping {mins} minute(s) ago"


def assess_channel(has_issues: bool | None, assignable: list[str]) -> tuple[bool, str]:
    """Can this alarm actually alarm? Pure decision, so it is testable.

    This exists because the alarm path had never run. `nova-deadman` went
    green on 2026-08-27 08:45 having only ever taken the OK branch, and the
    branch it has never taken was broken the whole time: issues are
    **disabled** on this repository, so `POST /issues` answers `410 Issues
    has been disabled in this repository.` A watchdog whose success is
    guaranteed in advance -- the ping was fresh, so the only code that ran
    was the code that does nothing -- is the exact failure `prompt.md` warns
    about, and it produced a green check over a channel that did not exist.

    So the channel is measured on every run, in the quiet hours, rather than
    discovered during the outage it is meant to report.
    """
    if has_issues is None:
        return False, "could not read whether issues are enabled on the repo"
    if not has_issues:
        return False, (
            "issues are DISABLED on the repo: an alarm would be refused with "
            "410 and this watchdog can see an outage but not report it"
        )
    if ASSIGNEE not in assignable:
        return False, (
            f"issues are enabled but {ASSIGNEE!r} cannot be assigned, so the "
            "alarm would notify only whoever watches this repo"
        )
    return True, f"issues enabled, alarm addressed to @{ASSIGNEE}"


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def read_ping() -> datetime | None:
    """Committer date on the ref, or None if the ref is absent.

    A missing ref answers 404 and that is a real answer, not a failure; any
    other error is left to raise, because "I could not read it" must never
    reach the caller wearing the same face as "it is fine".
    """
    try:
        out = _gh("api", f"/repos/{REPO}/git/ref/{REF}")
    except subprocess.CalledProcessError as exc:
        if "404" in (exc.stderr or "") or "Not Found" in (exc.stderr or ""):
            return None
        raise
    sha = json.loads(out)["object"]["sha"]
    commit = json.loads(_gh("api", f"/repos/{REPO}/git/commits/{sha}"))
    return datetime.fromisoformat(
        commit["committer"]["date"].replace("Z", "+00:00")
    )


def read_channel() -> tuple[bool, str]:
    """Measure the alarm channel against the live repo."""
    try:
        repo = json.loads(_gh("api", f"/repos/{REPO}"))
        assignable = [
            row["login"]
            for row in json.loads(_gh("api", f"/repos/{REPO}/assignees"))
        ]
    except Exception as exc:
        return False, f"could not read the alarm channel: {exc}"
    return assess_channel(repo.get("has_issues"), assignable)


def open_alarm_issue() -> dict | None:
    rows = json.loads(
        _gh("api", f"/repos/{REPO}/issues?state=open&per_page=100")
    )
    for row in rows:
        if row.get("title") == TITLE and "pull_request" not in row:
            return row
    return None


def alarm_body(verdict: str, reason: str) -> str:
    """The two verdicts get different bodies, because they need different work.

    Writing one body for both would put the separation in the title and throw
    it away in the part anyone actually reads.
    """
    if verdict == "NEVER":
        return (
            f"@{ASSIGNEE} {reason}\n\n"
            f"`refs/{REF}` in `{REPO}` has never been written, so the alarm "
            "half of the dead-man's switch is running and the ping half is "
            "not. This is **not** evidence that the cluster is down.\n\n"
            "Most likely one of: `cronjobs/nova-alive-ping.yaml` has not "
            "merged or ArgoCD has not synced it; or `github-bot-token` is "
            "not scoped to push a ref to this repo, which is the one thing "
            "a Nova cycle cannot check for itself.\n\n"
            "Closed automatically on the first ping."
        )
    return (
        f"@{ASSIGNEE} {reason}\n\n"
        f"`refs/{REF}` in `{REPO}` is force-pushed every 5 minutes by the "
        "`nova-alive-ping` CronJob on the cluster. It has stopped.\n\n"
        "Two things this cannot separate from outside, and they need "
        "different actions: the cluster answering nothing at all (the box, "
        "the network or Tailscale) versus the CronJob failing while the box "
        "is fine. Check `kubectl get cronjob nova-alive-ping -n obsidian` "
        "first — if that answers, the box is up and only the pinger is "
        "broken.\n\n"
        "Closed automatically on the next ping."
    )


def raise_alarm(verdict: str, reason: str) -> None:
    """One issue, reopened and commented rather than duplicated.

    A cron that files a fresh issue every 30 minutes is a channel he mutes,
    and a muted channel is the same silence this whole thing exists to end.
    """
    existing = open_alarm_issue()
    body = alarm_body(verdict, reason)
    if existing:
        _gh("api", f"/repos/{REPO}/issues/{existing['number']}/comments",
            "-f", f"body={body}")
        print(f"commented on existing alarm #{existing['number']}")
        return
    _gh("api", f"/repos/{REPO}/issues", "-f", f"title={TITLE}", "-f", f"body={body}",
        "-f", f"assignees[]={ASSIGNEE}")
    print("opened alarm issue")


def clear_alarm(reason: str) -> None:
    existing = open_alarm_issue()
    if not existing:
        return
    _gh("api", f"/repos/{REPO}/issues/{existing['number']}/comments",
        "-f", f"body=Pings resumed: {reason}. Closing.")
    _gh("api", f"/repos/{REPO}/issues/{existing['number']}",
        "-X", "PATCH", "-f", "state=closed")
    print(f"closed alarm #{existing['number']}")


def main() -> int:
    channel_ok, channel_reason = read_channel()
    print(f"CHANNEL {'OK' if channel_ok else 'BROKEN'}: {channel_reason}")

    try:
        pinged_at = read_ping()
    except Exception as exc:  # unreadable is its own verdict, never clean
        print(f"UNREADABLE: could not read refs/{REF} on {REPO}: {exc}", file=sys.stderr)
        return 1
    verdict, reason = assess(pinged_at, datetime.now(timezone.utc), GRACE)
    print(f"{verdict}: {reason}")

    if verdict == "OK":
        if not channel_ok:
            # The cluster is fine and the alarm is not. Exit 1 rather than 0,
            # for the same reason every other check in `tools/` does: "I could
            # not have told you" must never wear the same face as "nothing is
            # wrong". This is the state the repo was actually in on
            # 2026-08-27, reported green.
            print(
                "NOT CLEAN: the ping is healthy but the alarm channel is not usable",
                file=sys.stderr,
            )
            return 1
        clear_alarm(reason)
        return 0

    try:
        raise_alarm(verdict, reason)
    except Exception as exc:
        # The outage is still the finding. Say the alarm could not be filed
        # instead of dying with a traceback that reads as a broken workflow.
        print(f"COULD NOT FILE THE ALARM: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
