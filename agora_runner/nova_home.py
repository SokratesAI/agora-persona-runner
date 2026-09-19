"""The landing page's one payload -- `/api/home`.

He asked for `/` to stop being the journal feed and become a progress
surface: *"Lets make a landing page instead! ... more status updates, the
12 hour summary of what has happened, project updates, links to journals
than needs input and comments"*. The spec he and I wrote is
`projects/sokrates/projects/nova/landing-page.md`, and its hardest
constraint is the one this module exists to satisfy:

> Everything except the galaxy strip must come from a **single cached
> response**. He reads this on a phone, sometimes roaming, and a dashboard
> that fans out to six endpoints re-creates the ten-second chat load that
> took most of 2026-09-08 to fix.

So this is **composition and not measurement**. Every number here is
already computed by a payload the site serves; `home_payload` is a pure
function over those payloads so that the page and the endpoint it came
from can never disagree, and so that the whole thing is testable without
a vault. The route in `nova_site` does the I/O and hands the results in.
"""

#: How many projects the landing page carries. His call, 2026-09-08:
#: *"Project block only displays top 3 projects."* Eight cards is most of
#: a phone screen before he reaches anything else, and the ordered list
#: already says which ones matter. It is a spec number rather than one I
#: picked, which is why it is named here instead of being a literal.
TOP_PROJECTS = 3


def _project_card(name, summary, priority, ranked_rows, group=None):
    """One project's card: where it stands, and what is next in it.

    `summary` is `nova_site._project_summary`'s dict, unchanged -- that is
    the "done / left" the spec left open, and the cheap definition wins
    for a reason rather than by default: the project index and the project
    page both already draw these exact counts, so a third definition here
    would put two different truths about one project on two pages of the
    same app. `dropped` stays out of the denominator there, so a project
    cannot reach 100% by abandoning its rows.

    The milestone and the next task come off the **top-ranked open row**
    in the project rather than being derived again. `/api/next` already
    ranks his whole board the way a cycle picks from it, so the first row
    it lists under a project is, by construction, the next task and the
    milestone that task sits in. Re-deriving either would be a second
    opinion about an ordering he can drag by hand.
    """
    top = ranked_rows[0] if ranked_rows else None
    # `ranked_rows` is this project's slice of `/api/next`'s `next`, which
    # is `ranked[:5]` -- so a project with open rows has none here
    # whenever five higher-ranked rows belong to other projects. That is
    # the common case and not an edge: on 2026-09-12 all five were Nova
    # rows, and the 2nd and 3rd cards came back `next: null` with an empty
    # milestone while both projects had open work. `group` is the same
    # payload's `projects` entry, which `next_payload_from_contents`
    # builds by walking *every* ranked row, so it already carries that
    # project's own top row. Preferring the narrow list keeps the cards
    # byte-identical whenever the row is in it.
    if top is None and group:
        top = {
            "board": group.get("topBoard") or "",
            "number": group.get("topNumber"),
            "title": group.get("top") or "",
            "milestone": group.get("topMilestone") or "",
        }
        # A group with no title is a project whose rows are all closed --
        # the card should say it is clear rather than draw a blank task.
        if not top["title"]:
            top = None
    return {
        "name": name,
        "priority": priority.get("priority") or "",
        "priorityKey": priority.get("priorityKey") or "",
        "done": summary.get("done") or 0,
        "open": summary.get("open") or 0,
        "dropped": summary.get("dropped") or 0,
        "percentDone": summary.get("percentDone") or 0,
        # The milestone the next task sits in, not "the project's
        # milestone" -- a project has several open at once and only this
        # one is being worked.
        "milestone": (top or {}).get("milestone") or "",
        # `None` rather than an empty object when a project has no open
        # row left: the card then says the project is clear, and an empty
        # title would draw a blank task line instead.
        "next": None if top is None else {
            "board": top.get("board") or "",
            "number": top.get("number"),
            "title": top.get("title") or "",
        },
    }


def home_payload(recap, projects, next_up, asks, *, health=None, top=TOP_PROJECTS):
    """The landing page, composed from payloads that already exist.

    **`health` and `top` are keyword-only on purpose.** `health` was
    inserted in front of `top` when the health line shipped, which is a
    silent trap rather than a live bug: no caller passes `top`
    positionally today, so nothing broke, and the next one that tries
    binds its project count to `health` and gets a landing page with a
    dict where its line should be and the default number of cards. A
    `*` costs nothing and makes that a `TypeError` at the call site.

    `recap` is `/api/recap`, `projects` is `/api/project` with no name
    (its index build: the hand-ordered project list, the ratings and
    `projectSummary`), `next_up` is `/api/next`, and `asks` is the open-ask
    list off `/api/journal`'s `status`.

    **The project order is his and is not re-derived here.** `projects`
    arrives already ranked by `rank_projects`, which reads the `Order`
    column he drags in the app, so this takes the first `top` names off
    the front of that list. Cycle 1453 shipped the reorder by learning
    that lesson the expensive way on the writer side; re-ranking on the
    reader side is the same mistake facing the other way.

    **`needsYou` is only the half the server can know.** Open asks are a
    server-side fact -- `open_asks` computes them off the journal. Unread
    comment replies are not: the read marks live in his browser
    (`markRepliesRead`), so the page fills that half itself from the
    `/replies` route's own data. Inventing a server-side count here would
    mean a badge that says 3 on a phone that has read all three.
    """
    ranked = {}
    for row in (next_up or {}).get("next") or []:
        ranked.setdefault((row.get("project") or "").strip().lower(), []).append(row)
    groups = {}
    for group in (next_up or {}).get("projects") or []:
        groups.setdefault((group.get("name") or "").strip().lower(), group)

    summaries = (projects or {}).get("projectSummary") or {}
    priorities = (projects or {}).get("projectPriority") or {}
    cards = []
    for name in ((projects or {}).get("projects") or [])[:top]:
        key = name.lower()
        cards.append(_project_card(
            name, summaries.get(key) or {}, priorities.get(key) or {},
            ranked.get(key) or [], groups.get(key),
        ))

    open_asks = list(asks or [])
    return {
        "recap": recap or {},
        "projects": cards,
        # A flat list plus a count, rather than the page counting the list
        # itself: the page's rule is "only when non-empty", and a single
        # field to test is what keeps that rule in one place.
        "needsYou": {"asks": open_asks, "count": len(open_asks)},
        # What cycles are holding right now, which the galaxy strip draws
        # on `/galaxy`. Carried here as the list fallback the spec asks
        # for -- *"falls back to the plain list the galaxy page already
        # carries under its picture"* -- so the strip failing to poll
        # leaves the page saying what is running rather than nothing.
        "active": (next_up or {}).get("active") or [],
        "claimsReadable": bool((next_up or {}).get("claimsReadable", True)),
        # The health line -- `health_block`'s dict, or `None` when the
        # caller did not build one. `None` rather than an empty dict
        # because the page's rule is "silent when fine" and an absent
        # block has to be told apart from one that looked and found
        # nothing: the first draws nothing, the second draws nothing, and
        # only the second is reassurance.
        "health": health,
    }


#: When a seven-day pace counts as running hot. `prompt.md` step 6b sets
#: the three bands a cycle sizes its own pick against -- below ~0.8 is
#: headroom, ~1.0 is ordinary, above ~1.2 the week is running hot -- so
#: this is that number rather than one I picked for this line.
QUOTA_HOT_PACE = 1.2

#: When the week is nearly spent. The `QUOTA LOW` warning fires at 10%
#: remaining, which is the point `prompt.md` says to stop starting things;
#: the health line should say so before the warning is the only thing that
#: does.
QUOTA_LOW_REMAINING = 10.0

#: Where `tools.cli_pin --publish` leaves its verdict on the Claude Code
#: pin (idea #308). The measurement only ever ran inside a cycle's own
#: preflight, so the gap went from six releases to eleven with nobody but
#: me able to see it; the health line reads this document instead.
PIN_READING_PATH = "projects/sokrates/projects/agora/nova/resources/cli-pin.json"

#: Where `tools.eol_watch --publish` leaves the Kubernetes version each node
#: runs and when its security support ends (idea #322). Both nodes sat three
#: weeks past the end of 1.34's standard support with that fact visible only
#: inside my own cycle's check output.
NODE_VERSION_PATH = "projects/sokrates/projects/agora/nova/resources/node-versions.json"

#: How close to losing security support a node's Kubernetes may get before
#: the health line names it. k3s upgrades one minor at a time, and falling
#: two minors behind -- where 1.34 is today, with 1.36 current -- is two
#: upgrades, each needing a node restart and a few days of watching. Two
#: months is room for that; the 180 days `eol_watch` itself uses would keep
#: the line lit for about half of every release's supported life.
NODE_WARN_DAYS = 60


def health_block(status, alerts, quota, cadence_minutes, pin=None, nodes=None):
    """The health line's facts (idea #274, step 3b of the landing page).

    His spec: *"One quiet line: cycle running, gaps in numbering, critical
    alerts, quota burn. Silent when fine."*

    **The clock is deliberately not consulted here, and that is the one
    design decision worth arguing.** `/api/home` is served
    stale-while-revalidate: a request gets the last build and starts the
    next one behind it, so the body he receives when he opens `/` after a
    quiet evening can be hours old. A `stalled` computed on this side
    would be frozen inside that body at "fine" for exactly the hours it
    would need to say otherwise -- which is the same trap
    `nova_site._with_silence` exists to get out of, and `journal_page`
    pays for with per-request `record_age` bookkeeping.

    So the split is by whether a fact can go stale in a cache. `cycle`,
    `lastWrittenAt` and `cadenceMinutes` are absolute and go out as they
    are; the page divides them by *its own* clock, which is always live,
    and appends "nothing has been written in N intervals" to `concerns`
    itself. Everything in `concerns` here is clock-free and therefore
    still true however old the body is.

    `alerts` is `nova_alerts.alerts_payload`'s dict and `quota` is the
    newest row of the cost ledger's quota series (`[at, fiveHour,
    fiveHourPace, sevenDay, sevenDayPace]`) or `None`. **An instrument
    that could not answer is a concern, not quiet** -- `nova_alerts`
    already refuses to report calm without saying how many rules replied,
    and a health line that goes silent because Prometheus is unreachable
    is the negative-result-guaranteed-in-advance failure with a green
    tick on it.
    """
    # Imported inside the function the way `nova_site._with_silence` does
    # it, so this module stays importable without the runner's heavier
    # dependencies -- and read rather than restated, because the grace is
    # the one number the page and `cycle_health` must agree on. The page
    # does the subtraction; it must not also own the threshold.
    from agora_runner.cycle_health import STALL_GRACE_INTERVALS

    concerns = []

    gaps = list((status or {}).get("recentMissingCycles") or [])
    if gaps:
        named = ", ".join(str(cycle) for cycle in gaps)
        concerns.append(
            ("1 cycle wrote no entry: " if len(gaps) == 1
             else str(len(gaps)) + " cycles wrote no entry: ") + named
        )

    alerts = alerts or {}
    if not alerts.get("reachable"):
        concerns.append("I could not reach Prometheus, so nothing here is a verdict on alerts")
    elif alerts.get("blind"):
        concerns.append("Prometheus has no alerting rules loaded, so quiet means blind")
    elif alerts.get("firing"):
        firing = alerts["firing"]
        concerns.append(
            ("1 alert is firing: " if len(firing) == 1
             else str(len(firing)) + " alerts are firing: ")
            + ", ".join(a.get("name") or "?" for a in firing)
        )

    pin_line = _pin_concern(pin)
    if pin_line:
        concerns.append(pin_line)

    node_line = _node_concern(nodes)
    if node_line:
        concerns.append(node_line)

    seven_day, pace = _quota(quota)
    if seven_day is None:
        concerns.append("the cost ledger carries no quota reading, so I cannot say what the week has spent")
    else:
        remaining = 100.0 - seven_day
        if remaining <= QUOTA_LOW_REMAINING:
            concerns.append(
                "the week is nearly spent: " + _pct(remaining) + "% of the quota left"
            )
        elif pace is not None and pace > QUOTA_HOT_PACE:
            concerns.append(
                "the week is running hot: " + _pct(seven_day) + "% spent at pace "
                + format(round(pace, 2), "g")
            )

    return {
        "cycle": (status or {}).get("cycle"),
        "lastWrittenAt": (status or {}).get("lastWrittenAt") or "",
        "cadenceMinutes": cadence_minutes,
        # The grace the page waits before calling the loop quiet. Carried
        # rather than hardcoded on the far side: a cycle files its entry
        # at the end of its run, so the honest threshold is a property of
        # `cycle_health` and not of the renderer.
        "stallGrace": STALL_GRACE_INTERVALS,
        "gaps": gaps,
        "sevenDay": seven_day,
        "sevenDayPace": pace,
        # The server's half of "silent when fine". The page ANDs its own
        # stale check onto this; `quiet` here can never mean "and nothing
        # has gone quiet", because this side has no clock to know.
        "concerns": concerns,
    }


def _pin_concern(pin):
    """One sentence when the Claude Code pin is stale, else `None`.

    `pin` is the JSON `tools.cli_pin --publish` writes. Only `stale` speaks:
    the tool already owns the threshold (behind for longer than a week), and
    a gap of a day or two is the normal state of a CLI that publishes most
    weekdays. `None` -- no document yet -- stays silent, because the check
    runs once a day and the first reading lands a day after this ships; a
    reading that says it could not measure is a concern, not quiet.
    """
    if not pin:
        return None
    if pin.get("error"):
        return "I could not check the Claude Code pin: " + str(pin["error"])
    if not pin.get("stale"):
        return None
    behind = pin.get("behind")
    days = pin.get("ageDays")
    gap = (str(behind) + " release" + ("" if behind == 1 else "s") + " behind"
           if behind is not None else "behind")
    age = ", published " + str(int(days)) + " days ago" if days is not None else ""
    return ("Claude Code is " + gap + ": on " + str(pin.get("subject") or "?")
            + age + ", newest " + str(pin.get("latest") or "?"))


def _node_concern(nodes):
    """One sentence when a node's Kubernetes is near or past the end of its
    security support, else `None`.

    `nodes` is the JSON `tools.eol_watch --publish` writes, and `warn` is its
    verdict -- made there, because it needs today's date and this side has
    none. The sentence carries the date rather than a count of days, so it
    stays true however long the page has been cached. Nodes on one version
    are named together: one upgrade, one line.
    """
    if not nodes:
        return None
    if nodes.get("error"):
        return "I could not read the nodes' Kubernetes version: " + str(nodes["error"])
    if not nodes.get("warn"):
        return None
    by_version = {}
    for node in nodes.get("nodes") or []:
        if node.get("securityEnds") and node.get("days", NODE_WARN_DAYS + 1) <= NODE_WARN_DAYS:
            key = (node.get("kubelet") or "?", node["securityEnds"], node["days"] < 0)
            by_version.setdefault(key, []).append(node.get("node") or "?")
    parts = []
    for (kubelet, ends, passed), names in sorted(by_version.items()):
        parts.append(
            "Kubernetes " + kubelet + " on " + " and ".join(names)
            + (" lost security support on " if passed else " loses security support on ")
            + ends
        )
    return "; ".join(parts) or None


def _quota(row):
    """`(sevenDay, sevenDayPace)` off a cost-ledger quota row.

    Indices 3 and 4 of `nova_costs.QUOTA_COLUMNS`. Read positionally
    because that is how the row is serialised -- the costs page reads the
    same two columns the same way, and naming them here would be a second
    spelling of one contract.
    """
    if not row or len(row) < 5:
        return None, None
    return row[3], row[4]


def _pct(value):
    """A percentage with no trailing `.0`, so a line reads as a sentence."""
    return format(round(float(value), 1), "g")
