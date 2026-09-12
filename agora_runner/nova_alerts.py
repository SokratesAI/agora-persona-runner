"""What Prometheus is complaining about, for the `/alerts` page.

Idea #122, the owner's own words: *"The sentinel has its own heartbeat and
its findings go into a conversation nobody opens. Node disk, pod restarts
and anything Forbidden that should not be -- those belong on a page beside
the costs page, where a number changing is visible without anyone having to
go looking."*

The Sentinel itself is gone: `tools.alerts` replaced it with Prometheus
rules that every cycle reads at the top of `preflight`. That closed the
half of #122 about *detecting* things. It did nothing for the half the row
is actually about -- **the owner still has nowhere to look.** A finding that
only ever appears inside a cycle's own preflight output is a finding he
reads about, at best, in a journal entry hours later.

So this is the same data `tools.alerts` reads, served to his phone. It is a
separate module rather than an import because `tools/` is not in the site
image at all -- the Dockerfile copies `agora_runner/` and the two entry
points -- so an `/api/alerts` route that imported `tools.alerts` would pass
every test here and `ImportError` on the running pod.

**The one design decision worth arguing, and it is the whole reason this is
not five lines: a page that says "nothing is firing" must not be able to say
that when it could not reach Prometheus.** That is `prompt.md`'s
negative-result-guaranteed-in-advance rule at its most literal -- an empty
alert list is exactly what a dead scrape, an unloaded rules file and a
healthy cluster all produce. `summarise` therefore never reports quiet
without also reporting how many rules answered, and `reachable` is a
separate field from `firing`. A rules file that fails to load leaves
Prometheus Running, green, and evaluating nothing forever, so zero rules is
reported as `blind`, not as calm.

Pending alerts are carried and kept separate: `for:` has not elapsed, so the
condition may still be a spike, and drawing them the same red as a firing
one would teach him to ignore the colour.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

PROMETHEUS = "http://prometheus.infra.svc.cluster.local:9090"
TIMEOUT = 8

# The labels worth putting on a phone card. Prometheus attaches everything
# the series carried, which on a kubelet rule is a dozen keys of scrape
# bookkeeping; these are the ones that say *where*.
_WHERE = ("namespace", "pod", "node", "instance", "job", "container")


def _label(labels, *names):
    for name in names:
        value = labels.get(name)
        if value:
            return value
    return ""


def _one(alert):
    """One Prometheus alert, flattened to what a card draws."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    where = [
        key + " " + labels[key]
        for key in _WHERE
        if labels.get(key)
    ]
    return {
        "name": _label(labels, "alertname") or "(unnamed rule)",
        "severity": _label(labels, "severity"),
        # `summary` is what our own rules write; `description` is the
        # convention upstream rule packs use. Neither is guaranteed, so the
        # card falls back to the rule name rather than to an empty line.
        "text": _label(annotations, "summary", "description", "message"),
        "where": where,
        "since": alert.get("activeAt") or "",
        "state": alert.get("state") or "",
    }


def summarise(alerts_json, rules_json, error=""):
    """The `/api/alerts` payload. Pure -- no I/O, so tests need no server.

    `error` non-empty means the fetch never landed, and then every count
    below is a fact about nothing. `reachable` carries that separately
    instead of letting an empty list speak for it.
    """
    if error:
        return {
            "reachable": False,
            "error": error,
            "firing": [],
            "pending": [],
            "rules": 0,
            "blind": True,
        }

    raw = ((alerts_json or {}).get("data") or {}).get("alerts") or []
    firing = [_one(a) for a in raw if (a.get("state") or "") == "firing"]
    pending = [_one(a) for a in raw if (a.get("state") or "") == "pending"]

    groups = ((rules_json or {}).get("data") or {}).get("groups") or []
    rules = sum(len(g.get("rules") or []) for g in groups)

    return {
        "reachable": True,
        "error": "",
        "firing": firing,
        "pending": pending,
        "rules": rules,
        # Zero rules is not quiet. Prometheus serves an empty alert list
        # whether the rules file loaded or silently failed to, and the
        # difference is invisible from the alert list alone.
        "blind": rules == 0,
    }


def _get(path, timeout=TIMEOUT, opener=urllib.request.urlopen):
    with opener(PROMETHEUS + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def alerts_payload(get=_get):
    """Ask Prometheus, and never raise -- this is a page, not a check."""
    try:
        alerts = get("/api/v1/alerts")
        rules = get("/api/v1/rules")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return summarise(None, None, error=str(exc) or exc.__class__.__name__)
    return summarise(alerts, rules)
