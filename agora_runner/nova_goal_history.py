"""Every week's number for every goal, so `/plan` can draw the history.

Idea #38 asked for two things and only one of them got built. The slate
of goals in `goals.md` and the weekly review against it landed at Cycle
229; *"show some history in some charts or some cool visualisations"* did
not, and it could not have, because there was no history to show. A goal
carries one ``now:`` in its ```goal``` fence and the weekly review
overwrites it, so last week's reading is gone the moment this week's is
written. The scoreboard on `/plan` has always been a photograph of one
Monday.

This is the ledger that keeps the earlier photographs. It is the same
shape as `retro-ledger.json` and written the same way -- a cycle appends
one row, `tools/append_goal_snapshot.py` validates it, and nothing else
touches the file:

    [
      {"date": "2026-08-16", "cycle": 229, "values": {"G1": 2.8, "G3": 4}},
      {"date": "2026-08-17", "cycle": 257, "values": {"G1": 2.5, "G3": 3}}
    ]

**Rows are keyed by the goal's short id, not by its name.** A goal is
written ``name: G1 -- Working on what you asked for``, and the half after
the dash is a sentence Edvard is explicitly invited to rewrite -- the
whole slate is a proposal until he edits it. Keying the series on the
full name would break every earlier point the first time he did, and it
would break it silently: the chart would simply start again from one
point, which looks like a new goal rather than like a lost series. So
`goal_key` takes the leading `G1`-style token when there is one and falls
back to the whole name when there is not.

No vault I/O here, the same split as `nova_retro` and `nova_costs`: this
module parses and shapes, `nova_sources.goal_history_json` fetches.
"""

import json
import re


GOAL_HISTORY_PATH = "projects/sokrates/projects/agora/nova/resources/goal-history.json"

# `G1`, `G12`, `A3` -- a letter run followed by digits, at the very start
# of the name and followed by either the end of the name or a separator.
# Deliberately not "anything before the first dash": a goal named
# "Ship the thing -- properly" would key on four words that are exactly
# as rewritable as the rest of the sentence.
_KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z]+\d+)(?:\s|$|[-—:.])")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class GoalHistoryError(ValueError):
    """A row, or a whole ledger, that must not be written."""


def goal_key(name):
    """The stable series key for a goal's `name:` field.

    `"G1 — Working on what you asked for"` -> `"G1"`. A name with no
    leading id keys on itself, whitespace-collapsed, so a slate that
    never adopted the convention still gets a series -- it just gets one
    that a rename breaks, which is the honest consequence rather than a
    silent one.
    """
    text = (name or "").strip()
    match = _KEY_RE.match(text)
    if match:
        return match.group("key").upper()
    return " ".join(text.split())


def _row(raw, index):
    """One validated ledger row, or raise.

    Every failure below is a row that would render as a chart rather than
    as an error, which is why they refuse rather than skip: a point at
    the wrong date, or a value that is a string, draws a line Edvard
    would read as a measurement.
    """
    if not isinstance(raw, dict):
        raise GoalHistoryError(f"row {index} is {type(raw).__name__}, not an object")

    date = raw.get("date")
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise GoalHistoryError(f"row {index} needs a 'date' as YYYY-MM-DD, got {date!r}")

    cycle = raw.get("cycle")
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 1:
        raise GoalHistoryError(f"row {index} ({date}) needs a positive integer 'cycle'")

    values = raw.get("values")
    if not isinstance(values, dict) or not values:
        raise GoalHistoryError(f"row {index} ({date}) needs a non-empty 'values' object")

    clean = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name.strip():
            raise GoalHistoryError(f"row {index} ({date}) has a value under an empty key")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GoalHistoryError(
                f"row {index} ({date}) has a non-numeric value for {name!r}: {value!r}"
            )
        clean[goal_key(name)] = value

    extra = set(raw) - {"date", "cycle", "values"}
    if extra:
        raise GoalHistoryError(
            f"row {index} ({date}) carries unknown field(s): {', '.join(sorted(extra))}"
        )

    return {"date": date, "cycle": cycle, "values": clean}


def load(document):
    """The ledger text -> a list of validated rows, oldest first.

    `""` and whitespace are an empty ledger rather than an error: this
    file does not exist until the first snapshot is taken, and
    `vault_read_path` returns `""` for a path that is not there.
    """
    text = (document or "").strip()
    if not text:
        return []

    raw = json.loads(text)
    if not isinstance(raw, list):
        raise GoalHistoryError("the ledger must be a JSON array of rows")

    rows = [_row(item, index) for index, item in enumerate(raw)]
    rows.sort(key=lambda row: (row["date"], row["cycle"]))
    return rows


def append(document, row):
    """The ledger text with one more row on the end, as text.

    Refuses a second row for a date already in the ledger. The weekly
    review runs once a week by instruction, so a same-date append is
    either a cycle running the tool twice or two cycles racing to write
    the same Monday -- and both of those want to be told, not merged.
    """
    rows = load(document)
    fresh = _row(row, len(rows))
    if any(existing["date"] == fresh["date"] for existing in rows):
        raise GoalHistoryError(
            f"{fresh['date']} is already in the ledger; a date is written once"
        )
    rows.append(fresh)
    rows.sort(key=lambda item: (item["date"], item["cycle"]))
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def series(document):
    """`{goal key: [{date, cycle, value}, ...]}`, oldest first.

    Only the dates where that goal was actually measured appear. A goal
    added to the slate this week has one point and no line, which is the
    true state of it -- filling the gap with the earliest reading would
    invent a flat week nobody measured.
    """
    out = {}
    for row in load(document):
        for key, value in row["values"].items():
            out.setdefault(key, []).append(
                {"date": row["date"], "cycle": row["cycle"], "value": value}
            )
    return out
