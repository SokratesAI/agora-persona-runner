"""The cost and cadence page (issues.md #57, page 2).

The owner, 2026-08-08: *"I want you to figure out the optimal method of quota
spendage for projects. I do not know the optimal way."* That record exists
-- every cycle's weighted tokens, turn count, wall-clock and both quota
windows -- and until this page nobody could see it. `publish_costs` in the
bridge writes it into the vault after every cycle; this reads it.

Nothing here does I/O and nothing here parses markdown: the fetch is
`nova_sources.cost_ledger_json`, and the document is already JSON. What
this module does is *shape* it, which is worth its own file for one
reason: the ledger is 93KB and most of that is bytes the page has no use
for. Rows go out as arrays rather than objects, because 714 quota
readings spelling out `"five_hour_pace"` 714 times is 9KB of key.

The ledger carries no cycle numbers -- it is built from transcript
sessions, which know when they ran and not what the journal called them
-- so nothing here invents one. The x-axis is time, which is a fact the
document actually contains.
"""

import json

COST_LEDGER_PATH = "projects/sokrates/projects/agora/nova/resources/cost-ledger.json"

# The columns each compact row carries, in order. Sent alongside the rows
# as documentation of the wire format -- the client indexes by position,
# not by these names, so reordering either tuple silently swaps what the
# charts plot. `test_a_cycle_row_is_the_five_columns_in_the_declared_order`
# is what actually holds the two sides together; this is what tells a
# reader of the payload what they are looking at.
CYCLE_COLUMNS = (
    "at", "minutes", "turns", "toolCalls", "weighted",
    "subagentTurns", "subagentWeighted",
)
QUOTA_COLUMNS = ("at", "fiveHour", "fiveHourPace", "sevenDay", "sevenDayPace")

_SUMMARY_KEYS = (
    "cycles",
    "first_cycle",
    "last_cycle",
    "total_weighted",
    "mean_weighted",
    "median_weighted",
    "max_weighted",
    "mean_duration_seconds",
    "median_duration_seconds",
    "cost_share",
)

# `summary.totals` and `totals_weighted` are deliberately not on that list.
# They are five raw token counts each, the page plots none of them, and
# `cost_share` is the same information already divided through -- which is
# the form the question "where does the money go" is actually asked in.
#
# `summary.subagent_weighted` and `subagent_sessions` are left off for a
# different reason: they disagree with the rows. Both are computed from the
# transcripts on disk *now*, the way `other_sessions` is, while the cycle
# rows are carried forward forever -- and the summary also counts orphans, a
# subagent whose parent transcript has been pruned, which by construction is
# attributed to no row. Live ledger, 2026-08-28: the summary says 48,656,353
# and the rows add to 48,401,932. A tile fed by the first would not match the
# chart under it, so the page adds up the column instead.


def _subagent(cycle):
    """A cycle row's two delegation numbers, or two holes if nobody counted.

    `weightedTokens` on a parent deliberately does not absorb its
    children's -- `analytics.attribute_subagents` says so in as many words,
    because that column is what `calibration.py` joins against quota
    readings and double-counting a charge would skew the constant. The
    consequence is that the `weighted` column understates every delegating
    cycle, and until these two columns existed the page had no way to say
    by how much.

    The hole is the part worth reading twice, and it is the same call
    `test_a_reading_from_before_pace_existed_is_a_hole_not_a_zero` makes
    one field over. Subagent *cost* attribution landed 2026-08-19, and
    every one of the 265 rows older than that carries `subagentTurns: 0`
    with no `subagentWeightedTokens` key at all -- a default that was
    written once and then carried forward by every republish. Delegation
    was routine well before that date, so those zeros are not measurements
    of nothing, they are the absence of an instrument. Plotted as zeros
    they draw a flat floor until 19 August and a step up, which reads as
    "this is when Nova started delegating" and is false. `None` is what
    makes a client break the line instead.

    The key that decides it is `subagentWeightedTokens`, not
    `subagentTurns`: the turn count is present on all 588 rows and is 0 on
    every pre-attribution one, so it cannot tell the two eras apart on its
    own.
    """
    if "subagentWeightedTokens" not in cycle:
        return None, None
    return (
        cycle.get("subagentTurns") or 0,
        round(cycle.get("subagentWeightedTokens") or 0),
    )


def _ms(iso):
    """An ISO stamp -> epoch milliseconds, without importing a date parser.

    The ledger writes UTC stamps ending in `Z` and the page renders Oslo
    time, so the conversion has to happen somewhere. It happens in the
    browser, from a number, because that is the one place that knows the
    reader's timezone -- see `renderCosts`.
    """
    from datetime import datetime, timezone

    text = (iso or "").replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return round(stamp.timestamp() * 1000)


def costs_payload(document):
    """The ledger, cut to what the four charts and the tiles actually plot.

    `document` is the raw text of the vault file. A ledger that will not
    parse raises, which the server turns into a 502 -- an empty page that
    looks like "no cycles have ever run" is the worse failure of the two.

    An *absent* ledger is the one case that is not a failure: the publish
    lives in the bridge repo and this one only reads what it wrote, so a
    vault that has never had a cycle run against it is a page with nothing
    to plot rather than a 502 on a nav tab. Empty and unparseable are
    deliberately different answers.
    """
    if not (document or "").strip():
        return {
            "generatedAt": None,
            "cycleColumns": list(CYCLE_COLUMNS),
            "quotaColumns": list(QUOTA_COLUMNS),
            "cycles": [],
            "quota": [],
            "summary": {},
            "weights": {},
        }
    ledger = json.loads(document)
    summary = ledger.get("summary") or {}
    cycles = []
    for cycle in ledger.get("cycles") or []:
        at = _ms(cycle.get("startedAt"))
        if at is None:
            continue
        cycles.append(
            [
                at,
                round((cycle.get("durationSeconds") or 0) / 60.0, 1),
                cycle.get("turns") or 0,
                cycle.get("toolCalls") or 0,
                round(cycle.get("weightedTokens") or 0),
                *_subagent(cycle),
            ]
        )
    quota = []
    for reading in ledger.get("quota") or []:
        at = reading.get("at")
        if at is None:
            continue
        quota.append(
            [
                round(at * 1000),
                reading.get("five_hour"),
                reading.get("five_hour_pace"),
                reading.get("seven_day"),
                reading.get("seven_day_pace"),
            ]
        )
    # Both charts take the first and last row as the ends of their x-axis
    # and draw straight through everything between, so time order is not a
    # nicety here -- one row out of place puts a mark outside the plot box
    # and, in the quota chart, a line that doubles back on itself.
    #
    # The live ledger is sorted (checked, 2026-08-11) and the publisher
    # that writes it lives in the other repo, which is exactly why this
    # sorts anyway: the client's assumption is cheap to make true on this
    # side of the wire and impossible to notice breaking on the other.
    cycles.sort(key=lambda row: row[0])
    quota.sort(key=lambda row: row[0])
    return {
        "generatedAt": _ms(ledger.get("generatedAt")),
        "cycleColumns": list(CYCLE_COLUMNS),
        "quotaColumns": list(QUOTA_COLUMNS),
        "cycles": cycles,
        "quota": quota,
        "summary": {key: summary[key] for key in _SUMMARY_KEYS if key in summary},
        "weights": ledger.get("weights") or {},
    }
