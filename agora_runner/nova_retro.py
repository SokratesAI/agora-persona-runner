"""The Friday retrospective ledger, and the page that plots it.

The owner, `issues.md` 2026-08-13: *"Every Friday, spend a full cycle to do a
full retrospective on yourself. ... Rate yourself on a scale from 1 to 10
on how you feel its going, how effective do you think you are, whats
good, whats bad, whats the overall feeling (which is the most important
metric). Actually note down data and compare it to previous retros (lets
also make a page that shows these data as graphs)."*

The comparison is the whole ask, and it is the part that does not survive
being written in prose: a retro that can only be read next to the last
one by re-reading the last one is a diary. So the scores go in a JSON
ledger with a fixed shape, and this module is the one place that says
what that shape is -- `tools/append_retro.py` validates against it before
writing, and the site reads it back through the same names.

**Why the shape is pinned in code rather than described in `prompt.md`.**
The first retro writes the first row, and a retro happens once a week: a
column named wrong on 2026-08-14 is not noticed until 2026-08-21, by a
cycle with no memory of the first one, which will then either match the
mistake or fork the file. There is no cheap second reading. A validator
that refuses is the only thing that makes the second Friday agree with
the first.

Nothing here does I/O. The document arrives as text and leaves as a
payload, the same split every other page on this server follows.
"""

import json
import re
from datetime import datetime, timezone

RETRO_LEDGER_PATH = "projects/sokrates/projects/agora/nova/resources/retro-ledger.json"

# The three things he asked to be rated, in the order he asked for them,
# with the label the page shows. `feeling` is last because it is the one
# he called the most important metric -- the page draws it heaviest, and
# a reader scanning a legend reads the last line last.
SCORE_KEYS = (
    ("going", "How it's going"),
    ("effectiveness", "How effective"),
    ("feeling", "Overall feeling"),
)

# The prose fields, all required. `overall` is the sentence behind the
# `feeling` score and is required for the same reason the score is: he
# named it the most important metric, so a row without it has dropped the
# one column that mattered while still looking complete.
PROSE_KEYS = ("overall", "good", "bad")

# The one-screen summary (ideas.md #120), in the order he wrote the four
# parts, with the label the card shows. He asked for *"one screen -- what
# shipped, what broke, what is still stuck, and the one thing you would
# want to change"*, because a chat-style report read better to him twice
# than the journal did.
#
# It is a separate block rather than four more `PROSE_KEYS` because it is
# written to a different reader. `good`/`bad`/`overall` are the retro
# talking to itself about how the loop is going; these four are the week
# reported to a person holding a phone, and the page draws them first and
# on their own. Mixing them would make the card decide which sentences
# are for him, every week, from a flat bag of prose.
WEEK_KEYS = (
    ("shipped", "What shipped"),
    ("broke", "What broke"),
    ("stuck", "What is still stuck"),
    ("change", "The one thing I would change"),
)

SCORE_MIN = 1
SCORE_MAX = 10

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RetroError(ValueError):
    """A row that must not be written, with why in the message."""


def _ms(date):
    """`YYYY-MM-DD` -> epoch milliseconds at midnight UTC.

    A weekly series does not care about the hour, and midnight UTC lands
    on the same calendar day in Oslo, so nothing a reader sees moves. The
    payload carries a number rather than the string because the chart
    needs a position on a time axis, and the browser is the one thing
    that knows the reader's timezone -- the same reason `nova_costs`
    converts on this side and formats on the other.
    """
    stamp = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return round(stamp.timestamp() * 1000)


def validate_row(row):
    """Raise `RetroError` unless `row` is a retro this ledger will accept.

    Strict on purpose, and strict in both directions: an unknown key is
    refused as loudly as a missing one. A cycle that invents `mood`
    alongside `feeling` gets a ledger with two columns that mean the same
    thing and no way to tell which one the graph should plot, and it
    finds out a week later.
    """
    if not isinstance(row, dict):
        raise RetroError("a retro must be a JSON object")

    date = row.get("date")
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise RetroError(f"date must be YYYY-MM-DD, got {date!r}")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise RetroError(f"date {date!r} is not a real date: {exc}") from exc

    cycle = row.get("cycle")
    # `bool` is an `int` in Python and `True` would sail through the
    # range check as cycle 1.
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle < 1:
        raise RetroError(f"cycle must be a positive integer, got {cycle!r}")

    scores = row.get("scores")
    if not isinstance(scores, dict):
        raise RetroError("scores must be an object with one entry per rated question")
    wanted = {key for key, _ in SCORE_KEYS}
    missing = sorted(wanted - set(scores))
    if missing:
        raise RetroError(f"scores is missing {', '.join(missing)}")
    extra = sorted(set(scores) - wanted)
    if extra:
        raise RetroError(f"scores has unknown key(s) {', '.join(extra)}")
    for key in sorted(wanted):
        value = scores[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise RetroError(f"scores.{key} must be a whole number 1-10, got {value!r}")
        if not SCORE_MIN <= value <= SCORE_MAX:
            raise RetroError(f"scores.{key} must be 1-10, got {value}")

    for key in PROSE_KEYS:
        text = row.get(key)
        if not isinstance(text, str) or not text.strip():
            raise RetroError(f"{key} must be a non-empty string")

    week = row.get("week")
    if not isinstance(week, dict):
        raise RetroError(
            "week must be an object with " + ", ".join(k for k, _ in WEEK_KEYS)
            + " (ideas.md #120 -- the one screen he reads on his phone)"
        )
    wanted_week = {key for key, _ in WEEK_KEYS}
    missing_week = sorted(wanted_week - set(week))
    if missing_week:
        raise RetroError(f"week is missing {', '.join(missing_week)}")
    extra_week = sorted(set(week) - wanted_week)
    if extra_week:
        raise RetroError(f"week has unknown key(s) {', '.join(extra_week)}")
    for key in sorted(wanted_week):
        text = week[key]
        if not isinstance(text, str) or not text.strip():
            raise RetroError(f"week.{key} must be a non-empty string")

    changes = row.get("changes")
    if not isinstance(changes, list):
        raise RetroError("changes must be a list of strings (use [] if you chose none)")
    for change in changes:
        if not isinstance(change, str) or not change.strip():
            raise RetroError("every entry in changes must be a non-empty string")

    known = {"date", "cycle", "scores", "changes", "week", *PROSE_KEYS}
    unknown = sorted(set(row) - known)
    if unknown:
        raise RetroError(f"unknown field(s) {', '.join(unknown)}")


def load(document):
    """The ledger's rows out of its raw text; `[]` when it does not exist.

    Absent and unparseable are deliberately different, the same split
    `nova_costs.costs_payload` draws: a vault that has never had a retro
    run against it is a page with nothing to plot, and a ledger that will
    not parse is a failure that must not render as "no retros have ever
    happened".
    """
    if not (document or "").strip():
        return []
    ledger = json.loads(document)
    if not isinstance(ledger, dict):
        raise RetroError("the retro ledger must be a JSON object")
    retros = ledger.get("retros")
    if retros is None:
        return []
    if not isinstance(retros, list):
        raise RetroError("retros must be a list")
    return retros


def dump(retros):
    """Rows -> the exact bytes to write back, oldest first.

    Sorted here rather than trusted from the caller for the reason
    `nova_costs` sorts its series: the chart draws straight through from
    the first row to the last, so one row out of order puts a mark
    outside the plot box. Sorting on write means the page never has to.
    """
    ordered = sorted(retros, key=lambda row: (row.get("date", ""), row.get("cycle", 0)))
    return json.dumps({"retros": ordered}, indent=2, ensure_ascii=False) + "\n"


def append(document, row):
    """`document` plus `row`, as text. Raises rather than writing a bad row.

    One retro per date. A second cycle waking on the same Friday morning
    and running the retro again would otherwise put two rows on one date,
    and every later comparison would silently weight that week twice --
    which is exactly the failure this ledger exists to prevent, arriving
    through the door marked "be lenient".
    """
    validate_row(row)
    retros = load(document)
    for existing in retros:
        if existing.get("date") == row["date"]:
            raise RetroError(
                f"a retro for {row['date']} is already in the ledger "
                f"(cycle {existing.get('cycle')}). One retro per Friday: edit that "
                "row rather than adding a second."
            )
    return dump([*retros, row])


def _week(block):
    """The one-screen summary as the page reads it, or `None`.

    `None` and a partial block are deliberately the same answer here, and
    that is not leniency: `validate_row` refuses anything but all four
    parts, so a row missing one cannot have been written by this loop --
    it is one of the three retros that predate #120. A card drawn from
    half a summary would read as this week's report with two sections
    silently missing, which is worse than the page saying nothing yet.
    """
    if not isinstance(block, dict):
        return None
    out = {}
    for key, _label in WEEK_KEYS:
        text = block.get(key)
        if not isinstance(text, str) or not text.strip():
            return None
        out[key] = text.strip()
    return out


def retros_payload(document):
    """The ledger, as the page reads it.

    Deliberately *not* compacted into positional rows the way
    `nova_costs` compacts its 714 quota readings. This series grows by
    one row a week -- 52 a year against that page's 110 cycles in nine
    days -- so dropping the keys would save a few hundred bytes and cost
    the payload its readability. The right answer to "should this be
    compact" is a measurement, and here the measurement says no.

    `at` is added rather than replacing `date`, because the chart needs a
    number and the cards show the day he wrote it.
    """
    retros = []
    for row in load(document):
        date = row.get("date")
        if not isinstance(date, str) or not _DATE_RE.match(date):
            continue
        scores = row.get("scores") or {}
        retros.append(
            {
                "at": _ms(date),
                "date": date,
                "cycle": row.get("cycle"),
                "scores": {key: scores.get(key) for key, _ in SCORE_KEYS},
                "overall": row.get("overall") or "",
                "good": row.get("good") or "",
                "bad": row.get("bad") or "",
                "changes": [c for c in (row.get("changes") or []) if isinstance(c, str)],
                "week": _week(row.get("week")),
            }
        )
    retros.sort(key=lambda entry: entry["at"])
    return {
        "scoreKeys": [{"key": key, "label": label} for key, label in SCORE_KEYS],
        "weekKeys": [{"key": key, "label": label} for key, label in WEEK_KEYS],
        "range": [SCORE_MIN, SCORE_MAX],
        "retros": retros,
    }
