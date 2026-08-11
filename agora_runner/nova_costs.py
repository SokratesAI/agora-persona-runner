"""The cost and cadence page (issues.md #57, page 2).

Edvard, 2026-08-08: *"I want you to figure out the optimal method of quota
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

# The columns each compact row carries, in order, sent alongside the rows
# so the client indexes by name rather than by a number written twice.
CYCLE_COLUMNS = ("at", "minutes", "turns", "toolCalls", "weighted")
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
    return {
        "generatedAt": _ms(ledger.get("generatedAt")),
        "cycleColumns": list(CYCLE_COLUMNS),
        "quotaColumns": list(QUOTA_COLUMNS),
        "cycles": cycles,
        "quota": quota,
        "summary": {key: summary[key] for key in _SUMMARY_KEYS if key in summary},
        "weights": ledger.get("weights") or {},
    }
