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

The ping carries a second answer as of idea #117: the commit *message* holds
`hb=<token>`, the box's own verdict on whether every Agora heartbeat is still
firing, read off `/api/health` by the CronJob just before it pushes. That
question can only be asked from inside the tailnet and only answered by a
process that outlives the hourly loop, so nova-site computes it and the ping
carries it out. The two findings stay two alarms with two issues: "the box is
gone" and "the box is fine and a scheduled run stopped" need different work.

Exit contract, the same one `security_alerts` and `heartbeat_health` use:
2 means the ping has stopped or the box reported a heartbeat that is not
firing, 1 means something could not be read -- the ref, the alarm channel, or
the heartbeat verdict -- which never reads as clean, and 0 means the cluster
pinged inside the grace and said every heartbeat is up.
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
# Deliberately a second issue with its own title, not a second paragraph in the
# first one. "The box is gone" and "the box is fine and a heartbeat stopped" need
# different actions from him and resolve independently -- folding them into one
# alarm would mean the heartbeat finding is closed the moment the pings resume,
# which is the `agentic_health` streak-counter mistake with two causes behind one
# number. Idea #117.
HEARTBEAT_TITLE = "nova-deadman: a heartbeat has stopped firing"
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


def parse_heartbeat_token(message: str) -> str | None:
    """The `hb=` token off the ping's commit subject, or None if it carries none.

    `cronjobs/nova-alive-ping.yaml` writes `nova alive <ts> hb=<token>`. Every
    ping written before that change has no token at all, and so does any ping
    from a cluster running an older manifest -- which is a different thing from
    a token that says the verdict is unknown, and is why this returns None
    rather than "unknown".
    """
    subject = message.splitlines()[0] if message else ""
    marker = " hb="
    idx = subject.rfind(marker)
    if idx < 0:
        return None
    return subject[idx + len(marker):].strip() or None


def assess_heartbeats(token: str | None) -> tuple[str, str]:
    """Pure decision: (verdict, one-line reason).

    Three outcomes, and only one of them is an alarm:

    - OK -- every heartbeat on the box is firing on its own schedule.
    - BAD -- at least one is off, overdue or on a schedule the checker cannot
      parse. This is the finding, and it is the one that opens an issue.
    - UNKNOWN -- the ping ran but could not read `/api/health`, or the ping is
      too old to carry the token. This is never an alarm and never clean: the
      cluster is demonstrably up (it pinged) and this watchdog simply cannot
      say anything about the heartbeats on it.

    An UNKNOWN carries *why* when the ping said why. `hb=unknown` on its own is
    all this ever got, and three of the fast rung's last four runs failed on it
    saying only "unreadable" -- the reason (a refused connection from `obsidian`
    to nova-site:8083) was in a CronJob pod log that expires, so reading it took
    a cluster round trip nobody off-box can make. The ping now writes
    `hb=unknown:<slug>` and the slug is reproduced here verbatim.
    """
    if token is None:
        return "UNKNOWN", "the ping carries no hb= token: the cluster is running an older manifest"
    if token == "ok":
        return "OK", "every heartbeat is firing"
    if token == "unknown":
        return "UNKNOWN", ("the ping could not read /api/health on nova-site and did not say "
                           "why: the cluster is running a manifest older than the reason slug")
    if token.startswith("unknown:"):
        slug = token[len("unknown:"):]
        return "UNKNOWN", f"the ping could not read /api/health on nova-site: {slug}"
    if token.startswith("bad("):
        return "BAD", f"the cluster reported {token[4:].split(')', 1)[0]} heartbeat(s) not firing, first: {token.split(':', 1)[-1]}"
    return "UNKNOWN", f"unrecognised hb= token {token!r}"


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def read_ping() -> tuple[datetime, str] | None:
    """(committer date, commit message) on the ref, or None if the ref is absent.

    A missing ref answers 404 and that is a real answer, not a failure; any
    other error is left to raise, because "I could not read it" must never
    reach the caller wearing the same face as "it is fine".

    The message is read as well as the date because the ping now writes the
    box's own heartbeat verdict into it (idea #117). The date stays the
    liveness signal -- nothing about the alarm below depends on the message
    being there.
    """
    try:
        out = _gh("api", f"/repos/{REPO}/git/ref/{REF}")
    except subprocess.CalledProcessError as exc:
        if "404" in (exc.stderr or "") or "Not Found" in (exc.stderr or ""):
            return None
        raise
    sha = json.loads(out)["object"]["sha"]
    commit = json.loads(_gh("api", f"/repos/{REPO}/git/commits/{sha}"))
    return (
        datetime.fromisoformat(commit["committer"]["date"].replace("Z", "+00:00")),
        commit.get("message", ""),
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


def open_alarm_issue(title: str = TITLE) -> dict | None:
    rows = json.loads(
        _gh("api", f"/repos/{REPO}/issues?state=open&per_page=100")
    )
    for row in rows:
        if row.get("title") == title and "pull_request" not in row:
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


def heartbeat_alarm_body(reason: str) -> str:
    return (
        f"@{ASSIGNEE} {reason}\n\n"
        "The cluster is **up** — it is still pinging `refs/nova/alive` every 5 "
        "minutes, and that ping is what carried this verdict out. What has "
        "stopped is one of the heartbeats *on* it, so a scheduled run is not "
        "happening and nothing on the box was going to say so.\n\n"
        "The full picture is `heartbeats` on `/api/health` on the Nova site, "
        "which names every heartbeat, its schedule and when it last ran. A "
        "verdict of `off` means Agora has it disabled without saying so in its "
        "name, `overdue` means it is enabled and has not fired inside its own "
        "cadence, and `unjudged` means its schedule string could not be parsed "
        "and so nothing is watching it at all.\n\n"
        "Closed automatically on the first ping that reports every heartbeat "
        "firing again."
    )


def raise_alarm(
    verdict: str,
    reason: str,
    *,
    title: str = TITLE,
    body: str | None = None,
    renotify: bool = True,
) -> None:
    """One issue, reopened and commented rather than duplicated.

    A cron that files a fresh issue every 30 minutes is a channel he mutes,
    and a muted channel is the same silence this whole thing exists to end.

    `renotify=False` goes further and says nothing at all on an issue that is
    already open. A comment every 30 minutes is a live-outage signal and reads
    as one; a heartbeat that is switched off can stay that way for a week,
    which is 336 comments saying the same sentence. The open issue is the
    alarm. Repeating it is what mutes it.
    """
    existing = open_alarm_issue(title)
    body = body if body is not None else alarm_body(verdict, reason)
    if existing:
        if not renotify:
            print(f"alarm #{existing['number']} is already open: {title}")
            return
        _gh("api", f"/repos/{REPO}/issues/{existing['number']}/comments",
            "-f", f"body={body}")
        print(f"commented on existing alarm #{existing['number']}")
        return
    _gh("api", f"/repos/{REPO}/issues", "-f", f"title={title}", "-f", f"body={body}",
        "-f", f"assignees[]={ASSIGNEE}")
    print(f"opened alarm issue: {title}")


def clear_alarm(reason: str, *, title: str = TITLE) -> None:
    existing = open_alarm_issue(title)
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
        ping = read_ping()
    except Exception as exc:  # unreadable is its own verdict, never clean
        print(f"UNREADABLE: could not read refs/{REF} on {REPO}: {exc}", file=sys.stderr)
        return 1
    pinged_at, message = ping if ping else (None, "")
    verdict, reason = assess(pinged_at, datetime.now(timezone.utc), GRACE)
    print(f"{verdict}: {reason}")

    if verdict == "OK":
        # Only ask the second question when the first one answered. A stale
        # ping's hb= token is as old as the ping, so reading it during an
        # outage would report a heartbeat verdict from before the box died.
        hb_verdict, hb_reason = assess_heartbeats(parse_heartbeat_token(message))
        print(f"HEARTBEATS {hb_verdict}: {hb_reason}")
        if hb_verdict == "BAD" and channel_ok:
            try:
                raise_alarm(hb_verdict, hb_reason, title=HEARTBEAT_TITLE,
                            body=heartbeat_alarm_body(hb_reason), renotify=False)
            except Exception as exc:
                print(f"COULD NOT FILE THE HEARTBEAT ALARM: {exc}", file=sys.stderr)
        elif hb_verdict == "OK" and channel_ok:
            clear_alarm(hb_reason, title=HEARTBEAT_TITLE)

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
        # A heartbeat verdict this could not read is not a reason to alarm and
        # not a reason to call the run clean either -- same contract as the
        # channel check above, and as every other tool in this directory. The
        # cluster is up; what is missing is this watchdog's second answer.
        if hb_verdict == "UNKNOWN":
            # The reason goes on this line as well as the HEARTBEATS one above,
            # because this is the line the failed run is judged by: `gh run
            # view` shows it beside the non-zero exit, and a reader who stops
            # there used to be told only that something was unreadable.
            print(f"NOT CLEAN: the ping is healthy but its heartbeat verdict is "
                  f"unreadable -- {hb_reason}", file=sys.stderr)
            return 1
        return 2 if hb_verdict == "BAD" else 0

    try:
        raise_alarm(verdict, reason)
    except Exception as exc:
        # The outage is still the finding. Say the alarm could not be filed
        # instead of dying with a traceback that reads as a broken workflow.
        print(f"COULD NOT FILE THE ALARM: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
