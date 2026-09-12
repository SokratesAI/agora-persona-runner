"""Shares of cycles, so a project is never starved by the one above it.

The owner, `issues.md` #214 (2026-09-12): *"The project priority system
doesn't work: it exhausts one project before touching the next, and
projects never finish, they only get deprecated. Replace the project stack
with shares of cycles -- each project gets a share derived from my hand
order, Paused/Deprecated gets 0, and every cycle works on whichever project
is furthest below its share over the last ~30 cycles (claims ledger) ...
Floor: any project with zero cycles in 14 days gets one. Starting shares:
Marcus 35, Nova 30, Infra+Maintenance 20, rest 15."*

`nova_next.project_ranks` is the stack he is describing: it turns his `Order`
column into a strict total order, so the top project keeps the picker until
it runs out of open rows. That is why every project below Nova on his list
has gone weeks without a cycle while its rows aged.

This module replaces the *ordering* and leaves every tier around it alone.
`rank` still sorts an unanswered comment, a row he rated Immediately and the
every-5th maintenance reservation above the project tier, and his milestone
and row order below it -- the only thing that changes is which project the
project tier names first.

**Two numbers decide it**: the share a project is owed, and the share it
actually got. The first is `project_shares`; the second is
`cycle_attribution`, read off the claims ledger, which is the only record
this loop keeps of what a cycle actually worked on. `share_ranks` subtracts
one from the other and puts the biggest deficit first.

**Why the seed is written down rather than derived.** He asked for shares
"derived from my hand order" and then named four numbers that his hand order
does not produce -- Marcus is third on his list and gets the largest share,
Nova is first and gets less. So the seed below is his four numbers, and the
hand order decides the rest: every project he did not name splits what is
left by `1/order`, which is the ordinary reading of a ranked list. If he
wants Marcus derived rather than seeded, deleting its line here is the whole
change.
"""

from datetime import datetime, timedelta, timezone

from agora_runner.nova_boards import parse_project_meta

#: His starting shares, `issues.md` #214, keyed lowercase the way
#: `parse_project_meta` keys everything. "Infra+Maintenance 20" is two
#: projects on his board and he named them as one bucket, so it is split
#: evenly between them -- the alternative is inventing a ratio he did not
#: give. Percentages of cycles, not of anything else.
SHARE_SEED = {"marcus": 35.0, "nova": 30.0, "infra": 10.0, "maintenance": 10.0}

#: Lifecycle stages that earn no cycles at all. His words: *"Paused/Deprecated
#: gets 0"*. A project with a **blank** lifecycle cell is not one of these --
#: most of his rows have never had the cell filled in, and reading blank as
#: paused would zero the whole board.
DORMANT_STAGES = {"paused", "deprecated"}

#: How many recent cycles the actual share is measured over. His "~30".
WINDOW = 30

#: A project with a share and no cycle in this many days jumps the queue,
#: whatever the arithmetic says. His floor, and it is the part that stops a
#: 3% project waiting forever for its 3% to come round.
FLOOR_DAYS = 14

#: Claim slugs that are bookkeeping rather than work. `journal-seq-<n>` is
#: taken by every cycle to reserve its own entry number, so counting it would
#: attribute every cycle to whatever project the seq slug resolved to --
#: which is none, but it would still make every cycle look "counted".
_BOOKKEEPING_PREFIXES = ("journal-seq-",)


def _dormant(meta_row):
    return (meta_row.get("lifecycle") or "").strip().lower() in DORMANT_STAGES


def project_shares(projects_markdown, seed=SHARE_SEED):
    """`projects.md` -> `{lowercased project: share of cycles, summing to 100}`.

    A Paused or Deprecated project gets exactly `0.0` and is still present in
    the map, because "this project is owed nothing" and "I have never heard
    of this project" are different answers and `share_ranks` needs to tell
    them apart.

    Everything he did not seed splits what is left of 100 by `1/order`, so
    his hand order decides it and a project he has never placed sits behind
    the whole placed list -- the same rule `project_ranks` already applies,
    reached the same way.
    """
    meta = parse_project_meta(projects_markdown or "")
    shares = {name: 0.0 for name in meta}
    live = {name: row for name, row in meta.items() if not _dormant(row)}
    if not live:
        return shares
    seeded = {name: float(seed[name]) for name in live if name in seed}
    rest = [name for name in live if name not in seeded]
    # An unplaced project sorts behind every placed one rather than at
    # position 0, which `1/order` would otherwise turn into a divide by zero
    # and, worse, into the largest weight on the board.
    placed = [live[name].get("order") or 0 for name in live]
    floor = max(placed) if placed else 0
    pool = max(0.0, 100.0 - sum(seeded.values()))
    weights = {name: 1.0 / (live[name].get("order") or (floor + 1))
               for name in rest}
    total_weight = sum(weights.values())
    for name in rest:
        shares[name] = pool * weights[name] / total_weight if total_weight else 0.0
    shares.update(seeded)
    # Normalise last: a seed that does not add to 100, or a board where every
    # seeded project is paused, must still hand back percentages.
    total = sum(shares.values())
    if total:
        shares = {name: value * 100.0 / total for name, value in shares.items()}
    return shares


def _claim_project(item, project_of):
    """A claim slug -> the lowercased project it belongs to, or `None`.

    `project_of` is `{slug: project}` built from the board rows, which is the
    only place the mapping exists: a claim records the slug and the cycle and
    nothing about which project the row was filed under.
    """
    if not item or item.startswith(_BOOKKEEPING_PREFIXES):
        return None
    return project_of.get(item)


def cycle_attribution(claims, project_of, window=WINDOW):
    """The recent claims ledger -> `({project: cycles}, cycles counted)`.

    A cycle counts **once per project**, not once per claim: a cycle that
    claimed two Nova rows spent one cycle on Nova. Only cycles that resolved
    to at least one project are counted at all, so the denominator is "cycles
    I can attribute" rather than "cycles that happened" -- a cycle whose slug
    was free text (`health-line-reviewer-findings`) is invisible to this and
    counting it in the denominator would silently deflate every project's
    actual share.

    That undercount is the honest weakness of this instrument and it is worth
    naming rather than hiding: roughly half the slugs in the ledger today are
    free text, so the actual shares are measured over the attributable half.
    The fix is slugs that carry their project, not arithmetic here.
    """
    by_cycle = {}
    for claim in claims or []:
        project = _claim_project(claim.get("item"), project_of)
        if project is None:
            continue
        by_cycle.setdefault(claim.get("cycle"), set()).add(project)
    recent = sorted((c for c in by_cycle if isinstance(c, int)), reverse=True)[:window]
    counts = {}
    for cycle in recent:
        for project in by_cycle[cycle]:
            counts[project] = counts.get(project, 0) + 1
    return counts, len(recent)


def ledger_horizon(claims):
    """The oldest stamp in the ledger, or `None` -- how far back it can see.

    **The floor below cannot be evaluated past this**, and that is not a
    detail. `tools.claim prune` collects finished claims, so the live ledger
    held 68 rows going back 24 hours when this was written -- which means
    "no cycle has touched this project in 14 days" was true of nine of the
    eleven projects on his board *whatever the loop had actually done*. A
    test whose positive result is guaranteed in advance is not evidence, so
    `share_ranks` refuses to apply the floor until the ledger is older than
    the floor itself.
    """
    stamps = [_parse_stamp(claim.get("at")) for claim in claims or []]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return min(stamps) if stamps else None


def floor_is_measurable(horizon, now=None, floor_days=FLOOR_DAYS):
    """Can the ledger see `floor_days` back? `None` horizon means no."""
    if horizon is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - horizon) >= timedelta(days=floor_days)


def last_worked(claims, project_of):
    """`{project: datetime of its most recent claim}`, for the 14-day floor."""
    out = {}
    for claim in claims or []:
        project = _claim_project(claim.get("item"), project_of)
        if project is None:
            continue
        stamp = _parse_stamp(claim.get("at"))
        if stamp is None:
            continue
        if project not in out or stamp > out[project]:
            out[project] = stamp
    return out


def _parse_stamp(text):
    try:
        stamp = datetime.fromisoformat(str(text or ""))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def share_deficits(shares, counts, counted):
    """`{project: (share, actual, deficit)}`, all three as percentages.

    Handed back whole rather than as the single number the ranking needs,
    because his issue asks to *"show share vs actual on the projects page"* --
    a caller that only got the deficit would have to re-derive the two halves
    it came from, which is the second-answer-to-one-question shape this repo
    keeps paying for.
    """
    out = {}
    for name, share in shares.items():
        actual = (100.0 * counts.get(name, 0) / counted) if counted else 0.0
        out[name] = (share, actual, share - actual)
    return out


def share_ranks(shares, counts, counted, worked=None, now=None,
                floor_days=FLOOR_DAYS, horizon=None):
    """`{lowercased project: rank}`, furthest below its share first.

    A drop-in for `nova_next.project_ranks` -- same shape, same "lower is
    better", so `reserve_maintenance` still rewrites it and `rank` still
    reads it. What changes is the meaning: the number is no longer his hand
    position, it is this project's place in the queue *today*.

    Three bands, in order:

    1. **Starved.** A project with a share above zero that no claim has
       touched in `floor_days` (or ever). His floor. Inside the band the
       larger share goes first, because if two are starved the one he owes
       more to is the one to take.
    2. **Everyone else**, by deficit -- share owed minus share taken -- and
       a tie broken by the larger share.
    3. **Zero-share projects last**: Paused and Deprecated earn no cycles,
       and their rows are still listed so he can see them rather than
       wondering where they went.
    """
    worked = worked or {}
    now = now or datetime.now(timezone.utc)
    hungry = set(starved(shares, worked, now, floor_days, horizon))
    deficits = share_deficits(shares, counts, counted)

    def key(name):
        share, _actual, deficit = deficits[name]
        if share <= 0:
            return (2, 0.0, name)
        if name in hungry:
            return (0, -share, name)
        return (1, -deficit, name)

    return {name: position
            for position, name in enumerate(sorted(shares, key=key), start=1)}


def starved(shares, worked=None, now=None, floor_days=FLOOR_DAYS, horizon=None):
    """The projects the floor is currently rescuing, best share first.

    **Empty whenever the ledger cannot see `floor_days` back**, including
    when there is no ledger at all (`horizon=None`), because then every
    project looks untouched and the floor would rescue the whole board rather
    than the one project it is for -- see `ledger_horizon`. A floor that
    fires on everything is the same as no floor, except that it also hides
    the deficit ordering underneath it.
    """
    worked = worked or {}
    now = now or datetime.now(timezone.utc)
    if not floor_is_measurable(horizon, now, floor_days):
        return []
    cutoff = now - timedelta(days=floor_days)
    names = [name for name, share in shares.items()
             if share > 0 and (worked.get(name) is None or worked[name] < cutoff)]
    return sorted(names, key=lambda name: (-shares[name], name))
