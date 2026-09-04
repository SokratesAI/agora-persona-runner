"""What is Prometheus actually complaining about right now?

The owner's capture, 2026-09-04: *"Now that we have multiple servers and multiple
pods, I want more monitoring ... And while you are on it, we can implement
alerting that replaces the Sentinel. Alerts may be picked up by the job that
reverts commits or maybe we can have you quickly check alerts on the start of
each cycle. Not sure what the best is, please choose the best method of
reactive actions."*

The call I made, and the reason: **alerts are read by the loop at the start of
every cycle, not routed to a notifier.** An Alertmanager would need somewhere
to send to, and every destination available here is either a channel the owner has
asked not to be paged on for anything routine, or a dashboard nobody has open
at 04:00. A cycle, on the other hand, wakes every eighteen minutes anyway,
has a shell, and can fix the thing. So the alert lands in front of the only
reader here that can act on it, which is what "reactive" has to mean.

`platform-config#662` is the other half: Prometheus had no ServiceAccount and
scraped three static targets, one of them a StatefulSet deleted two days
earlier. It now scrapes both kubelets through the API server node proxy and
evaluates six rules.

**Three states, and the middle one is the whole point of this module.**

* Prometheus unreachable, or reachable and serving no rules at all -> exit 1.
  Not clean. A rules file that fails to load leaves Prometheus Running and
  green with zero alerts, forever, and `up == 0` cannot report it because a
  rule that was never loaded evaluates nothing.
* **A declared scrape job with no active targets -> exit 2.** No alert can
  catch this and `TargetDown` specifically cannot: `up == 0` needs an `up`
  series, and a `kubernetes_sd_configs` job that discovers nothing produces no
  series at all. Its absence looks exactly like health. That is the failure
  shape `prompt.md` spends four paragraphs on -- a negative result that was
  guaranteed in advance -- so it is checked directly rather than alerted on.
* Firing alerts -> exit 2, each printed whole with its labels and the time it
  started firing. Pending ones are printed and do not raise: `for:` has not
  elapsed, so the condition may still be a spike.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

PROMETHEUS = "http://prometheus.infra.svc.cluster.local:9090"
TIMEOUT = 15

# Coverage is declared, never inferred -- the same rule `backup_health` follows,
# and for the same reason. A job that discovers nothing is absent from
# `activeTargets` entirely, so "which jobs should be there" cannot be read back
# off the answer. A scrape job added to `monitoring/prometheus.yaml` and not
# added here reads as covered, which is the wrong direction to be wrong in; a
# name left here after its job is deleted raises, which is the right one.
EXPECTED_POOLS = frozenset({
    "prometheus",
    "agora",
    "kubelet-resource",
    "kubelet-cadvisor",
})


# Which findings are allowed to wake him. His constraint, capture 2026-09-04:
# *"I can't answer anything between 22:00 and 07:00 Oslo time as I'm asleep."*
# and, in the same capture, *"important alerts get sent to me like if one
# server is down"*. Those pull opposite ways at 03:00, so the breakthrough set
# is declared here in one place rather than decided per call site: a `high`
# severity, or a scrape target that has stopped answering at all, may ring his
# phone at night. Everything else waits for 07:00. Emptying this frozenset is
# the whole of "stop waking me", and that is deliberate -- I picked this and he
# can reverse it in one line.
URGENT_SEVERITIES = frozenset({"high"})


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=TIMEOUT) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ValueError(f"{path} answered status={payload.get('status')!r}")
    return payload["data"]


def _age(stamp: str) -> str:
    """Oslo-relative wording for an RFC3339 instant, or the raw string."""
    try:
        started = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
    if minutes < 90:
        return f"firing for {minutes:.0f}m"
    return f"firing for {minutes / 60:.1f}h"


def _describe(alert: dict) -> str:
    labels = dict(alert.get("labels") or {})
    name = labels.pop("alertname", "<unnamed>")
    severity = labels.pop("severity", "")
    summary = (alert.get("annotations") or {}).get("summary", "")
    where = " ".join(f"{k}={v}" for k, v in sorted(labels.items()) if v)
    head = f"{name}" + (f" [{severity}]" if severity else "")
    parts = [head, _age(alert.get("activeAt", ""))]
    if summary:
        parts.append(summary)
    if where:
        parts.append(where)
    return "  ".join(p for p in parts if p)


def collect(base: str = PROMETHEUS) -> dict:
    """Everything read in one place, as data. `report` renders it; `paging` judges it.

    Split out so the pager decides on labels rather than on the wording of a
    printed line -- a detector that re-spells the rule as a string match goes
    quietly wrong the first time the wording changes.
    """
    try:
        rules_data = _get(base, "/api/v1/rules")
        targets_data = _get(base, "/api/v1/targets?state=active")
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"unreadable": f"COULD NOT READ Prometheus at {base}: {exc}", "base": base}

    alerts: list[dict] = []
    rule_count = 0
    for group in rules_data.get("groups", []):
        for rule in group.get("rules", []):
            if rule.get("type") != "alerting":
                continue
            rule_count += 1
            alerts.extend(rule.get("alerts") or [])

    if rule_count == 0:
        return {"no_rules": True, "base": base}

    active = targets_data.get("activeTargets", [])
    by_pool: dict[str, list[dict]] = {}
    for target in active:
        by_pool.setdefault(target.get("scrapePool", "?"), []).append(target)

    missing = sorted(EXPECTED_POOLS - set(by_pool))

    unhealthy = [t for t in active if t.get("health") != "up"]
    firing = [a for a in alerts if a.get("state") == "firing"]
    pending = [a for a in alerts if a.get("state") == "pending"]

    return {
        "base": base,
        "rule_count": rule_count,
        "pools": by_pool,
        "active": active,
        "missing": missing,
        "unhealthy": unhealthy,
        "firing": firing,
        "pending": pending,
    }


def report(base: str = PROMETHEUS, found: dict | None = None) -> tuple[int, list[str]]:
    """Return (exit code, lines). Never raises on a reachable Prometheus.

    `found` lets a caller that has already run `collect` render the same
    reading instead of querying Prometheus twice -- two reads a second apart
    can disagree, and a report that disagrees with the message sent about it
    is worse than either.
    """
    found = collect(base) if found is None else found
    if found.get("unreadable"):
        return 1, [
            found["unreadable"],
            "Not the same thing as no alerts. Nothing here is judged.",
        ]
    if found.get("no_rules"):
        return 1, [
            f"Prometheus at {found['base']} answers, and has loaded ZERO alerting rules.",
            "A rules file that fails to load leaves it green and silent forever.",
            "Read the pod log for the rule_files line before trusting any check here.",
        ]

    by_pool = found["pools"]
    active = found["active"]
    unhealthy = found["unhealthy"]
    missing = found["missing"]
    firing = found["firing"]
    pending = found["pending"]

    lines = [
        f"{found['rule_count']} alerting rule(s) loaded, {len(by_pool)} scrape pool(s), "
        f"{len(active)} target(s)."
    ]

    status = 0
    if not active:
        lines.append(
            "NO SCRAPE TARGETS AT ALL. `up == 0` cannot fire on a series that "
            "does not exist, so this is invisible to every rule."
        )
        status = 2
    for target in unhealthy:
        lines.append(
            f"TARGET DOWN  {target.get('scrapePool')}  {target.get('scrapeUrl')}  "
            f"{(target.get('lastError') or '')[:160]}"
        )
        status = 2
    for pool in missing:
        lines.append(
            f"SCRAPE JOB WITH NO TARGETS  {pool}  -- it discovered nothing, so it "
            "publishes no `up` series and no rule can see it"
        )
        status = 2

    for alert in sorted(firing, key=lambda a: a.get("activeAt", "")):
        lines.append("FIRING  " + _describe(alert))
        status = 2
    for alert in sorted(pending, key=lambda a: a.get("activeAt", "")):
        lines.append("pending " + _describe(alert))

    if status == 0 and not pending:
        lines.append("Nothing firing and every target up.")
    return status, lines


def _distinct_firing(found: dict) -> list[dict]:
    """Firing alerts, minus the ones that only restate a target already reported down.

    `TargetDown` is the alert form of `up == 0`, and `collect` already reports
    the same outage as an unhealthy target the moment it starts -- ten minutes
    before the rule's `for:` elapses. So one machine going down produces the
    target line first and then, at the next cycle, the target line *and* the
    alert. Left alone that is two different page keys for one incident, and the
    six-hour dedupe never sees the second one coming: he gets paged twice,
    twenty minutes apart, for one server. It is also two lines in one message
    saying the same thing.
    """
    if not found.get("unhealthy"):
        return list(found.get("firing", []))
    return [
        a for a in found.get("firing", [])
        if (dict(a.get("labels") or {})).get("alertname") != "TargetDown"
    ]


def paging(found: dict) -> tuple[bool, bool, str]:
    """(worth telling him, may wake him, the message) from a `collect` result.

    Reads labels and target health, never the rendered lines. Three deliberate
    calls in here:

    * An unreadable Prometheus does **not** page. It is a real finding and it
      exits 1 in `report`, but "my instrument is broken" is not an outage, and
      a monitoring stack that pages about itself at 04:00 is the fastest way
      to teach him to ignore the channel.
    * A pending alert does not page. `for:` has not elapsed, so the condition
      may still be a spike -- the same call `report` already makes.
    * A scrape target that is down **is** urgent, because a kubelet that
      stopped answering is the "one server is down" case in his own words,
      and `TargetDown` cannot fire on a job that discovered nothing.
    """
    if found.get("unreadable") or found.get("no_rules"):
        return False, False, ""

    reasons: list[str] = []
    urgent = False

    if not found["active"]:
        reasons.append("Prometheus has NO scrape targets at all.")
        urgent = True
    for target in found["unhealthy"]:
        reasons.append(
            f"Target down: {target.get('scrapePool')} {target.get('scrapeUrl')} "
            f"{(target.get('lastError') or '')[:120]}".strip()
        )
        urgent = True
    for pool in found["missing"]:
        reasons.append(f"Scrape job '{pool}' discovered no targets, so nothing watches it.")
    for alert in _distinct_firing(found):
        labels = dict(alert.get("labels") or {})
        severity = labels.get("severity", "")
        if severity in URGENT_SEVERITIES:
            urgent = True
        reasons.append(_describe(alert))

    if not reasons:
        return False, False, ""
    head = "Cluster alert" if urgent else "Cluster warning"
    return True, urgent, head + ":\n" + "\n".join(f"- {r}" for r in reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=PROMETHEUS, help="Prometheus base URL")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="also send the owner a Telegram message, through tools.notify's quiet-hours gate",
    )
    args = parser.parse_args(argv)

    found = collect(args.url)
    status, lines = report(args.url, found)
    for line in lines:
        print(line)

    if args.notify:
        worth, urgent, text = paging(found)
        if not worth:
            print("nothing worth paging him about")
        else:
            from tools import notify as notify_tool

            code, line = notify_tool.notify(text, key=_page_key(found), urgent=urgent)
            print(f"telegram: {line}")
    return status


def _page_key(found: dict) -> str:
    """One key per distinct set of problems, so a changing duration does not re-page.

    Built from alert names and target URLs -- the identity of the problem --
    and never from the message text, which carries "firing for 41m" and would
    therefore be a new message every eighteen minutes.
    """
    names = sorted(
        (dict(a.get("labels") or {})).get("alertname", "?") for a in _distinct_firing(found)
    )
    downs = sorted(str(t.get("scrapeUrl")) for t in found.get("unhealthy", []))
    missing = sorted(found.get("missing", []))
    if not found.get("active"):
        names = ["no-targets"] + names
    return "alerts:" + "|".join(names + downs + missing)


if __name__ == "__main__":
    sys.exit(main())
