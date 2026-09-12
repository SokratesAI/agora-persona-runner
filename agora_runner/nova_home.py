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


def _project_card(name, summary, priority, ranked_rows):
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


def home_payload(recap, projects, next_up, asks, top=TOP_PROJECTS):
    """The landing page, composed from payloads that already exist.

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

    summaries = (projects or {}).get("projectSummary") or {}
    priorities = (projects or {}).get("projectPriority") or {}
    cards = []
    for name in ((projects or {}).get("projects") or [])[:top]:
        key = name.lower()
        cards.append(_project_card(
            name, summaries.get(key) or {}, priorities.get(key) or {},
            ranked.get(key) or [],
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
    }
