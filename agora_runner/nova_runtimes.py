"""How long a cycle actually ran, joined onto the journal (issues.md #59).

Edvard asked for "runtime for how long a cycle ran for" on a journal
card. The write-up under that row has said since 2026-08-11 that this is
not knowable -- "Nothing writes down when a cycle stops" -- and offered
him either a rough estimate from heartbeat-fired to entry-written, or a
new stamp in the runner. Both were wrong: the number already exists and
has for the whole life of the cost page. `publish_costs` in the bridge
reads the real transcript and writes `startedAt`/`endedAt`/
`durationSeconds` for every session into `cost-ledger.json`. Nothing had
ever connected it to a cycle number, so nothing could show it.

The reason nobody connected it is stated in `nova_costs`: *"The ledger
carries no cycle numbers -- it is built from transcript sessions, which
know when they ran and not what the journal called them."* So the join
has to go through the one thing both sides have, which is time -- and
time is exactly the kind of key that produces a plausible wrong answer
rather than an error.

**The join, and why it is the shape it is.** An entry's heading stamp is
written by the cycle partway through its own run, so it falls after that
session's start and usually before its end. Matching on *containment*
gets 136 of 235 live entries, because `endedAt` is written before the
cycle's own wrap-up finishes and a late-stamped entry falls past it.
Matching on *nearest preceding session within one heartbeat interval*
gets 208 of 209 stamped entries, and agrees with all 136 containment
matches by construction, since a stamp inside `[start, end]` has that
start as its greatest predecessor.

**That alone is 11% wrong, which is the part worth reading.** Of the 160
sessions the nearest-preceding rule matches, 18 are claimed by two
different cycle numbers -- cycles 23 and 24 both land in one session,
and so do 26/27, 28/29, 30/31, 64/65 and thirteen more. Those are real:
a cycle writes its entry when it finishes, cycles have overlapped, and
`journal-digest.md` says so out loud ("overlapping cycles will look out
of order, and that is real information"). A rule that printed a runtime
for both would put one cycle's wall-clock on another cycle's card, and
it would look completely correct.

So `cycle_runtimes` drops any session more than one cycle claims. That
is a deliberate trade of coverage for not lying: 70% of all entries get
a runtime, 92% of the last sixty, and the ones that lose out are the
overlapping era rather than a random sample. A card with no runtime says
nothing; a card with the wrong one is unfalsifiable from the outside.
"""

import bisect
import json
from datetime import datetime

from .config import OSLO

# How long after a session starts an entry may be stamped and still be
# that session's. One heartbeat interval: the next session's start is the
# real boundary, and `bisect` already stops there, so this only rejects an
# entry whose session never got written to the ledger at all. Measured
# over 209 live stamped entries, the lag from session start to heading
# stamp is 18.8 min median, 39.9 at p90, 59.6 at p95 -- and exactly one
# entry (cycle 38, from the 72-minute-heartbeat era) sits past an hour.
MAX_LAG_SECONDS = 3600


def _sessions(document):
    """`[(start, seconds)]` from the raw ledger, sorted, bad rows dropped.

    Unparseable JSON raises, matching `nova_costs.costs_payload`: a ledger
    that will not parse is a fault worth a 502, not a page that quietly
    claims no cycle ever ran. An *absent* ledger is `""` and is simply no
    runtimes, which is the same answer as a ledger that predates a cycle.
    """
    if not (document or "").strip():
        return []
    rows = []
    for cycle in json.loads(document).get("cycles") or []:
        started, seconds = cycle.get("startedAt"), cycle.get("durationSeconds")
        if not started or not seconds:
            continue
        try:
            at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        rows.append((at, float(seconds)))
    rows.sort()
    return rows


def _stamped(entry):
    """An entry's heading stamp as an aware datetime, or `None`.

    The heading is written in Oslo time by instruction (`identity.md`
    rule 7) while the ledger is UTC, so this is where the two meet. An
    entry with no date, no time, or no cycle number is not a failure --
    26 live entries have no parseable stamp and the reports have no cycle
    number at all -- it simply cannot be joined.
    """
    if entry.get("cycle") is None:
        return None
    date, time = entry.get("date"), entry.get("time")
    if not date or not time:
        return None
    try:
        return datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=OSLO)
    except ValueError:
        return None


def cycle_runtimes(document, entries):
    """`{cycle number: seconds it ran}`, only where the answer is certain.

    Two passes on purpose. The first asks which session each entry was
    written during; the second throws away every session that more than
    one cycle answered with. It cannot be done in one, because whether
    cycle 23's match is trustworthy is a fact about cycle 24.

    A cycle that matches two sessions keeps the earlier one. That is the
    addendum case -- one cycle, two entry documents, the second written
    later -- and the cycle's own run is the one it started in.
    """
    sessions = _sessions(document)
    if not sessions:
        return {}
    starts = [row[0] for row in sessions]

    claimed = {}
    for entry in entries or []:
        when = _stamped(entry)
        if when is None:
            continue
        index = bisect.bisect_right(starts, when) - 1
        if index < 0:
            continue
        start, seconds = sessions[index]
        if (when - start).total_seconds() > MAX_LAG_SECONDS:
            continue
        claimed.setdefault(index, set()).add(entry["cycle"])

    runtimes = {}
    for index, cycles in claimed.items():
        if len(cycles) != 1:
            # Two cycles wrote during one session; neither can be told
            # apart from the other, so neither gets a number.
            continue
        cycle = next(iter(cycles))
        start, seconds = sessions[index]
        previous = runtimes.get(cycle)
        if previous is None or start < previous[0]:
            runtimes[cycle] = (start, seconds)
    return {cycle: seconds for cycle, (_, seconds) in runtimes.items()}


def attach_runtimes(entries, document):
    """Write `runtimeSeconds` onto every entry whose cycle has one.

    Mutates in place and returns the same list, because the caller is
    `journal_payload` and the entries it holds are what the cache keeps.
    An entry with no certain runtime gets no key at all rather than
    `None`, so the client's own "is there a runtime" test is the presence
    of the field and there is no second falsy value to get wrong.
    """
    runtimes = cycle_runtimes(document, entries)
    for entry in entries or []:
        seconds = runtimes.get(entry.get("cycle"))
        if seconds:
            entry["runtimeSeconds"] = round(seconds)
    return entries
