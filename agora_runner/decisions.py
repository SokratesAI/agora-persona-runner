"""Where a taken decision lives, so a reversal can be counted.

Issue #227's Product management key result `pm-kr-reversals` -- *"Decisions
hold"*, measured as decisions reversed within 30 days of being taken -- had no
instrument, and for the same reason `pm-kr-calibration` had none: **nothing on
this box recorded a decision at all.** `identity.md` carries at least two
reversals in prose (the `projects/nova` database move, the bridge edit-scope
rule) and neither is a row anything can count. This module is the record that
was missing, and it is the sibling of `agora_runner.expectations`.

One document, `decisions.md`, one ```decision fence per decision:

    ```decision
    id: dec-projects-nova-database
    about: issue #227
    decision: <one sentence saying what was decided>
    taken: 2026-09-02
    source: edvard
    reversed: 2026-09-13
    because: <one sentence saying what changed>
    ```

`reversed` and `because` are absent while the decision stands and are written
together when it is overturned. Everything outside a fence is prose that
nothing parses, the same rule `project-goals.md`, `goals.md` and
`expectations.md` hold.

**The bias here runs the opposite way from calibration's, and that is the
thing to keep in mind when reading the number.** An expectation can be cheated
by writing it after the fact, so `measure_calibration` refuses a hindsight
date. A reversal count cannot be cheated that way -- it can only be cheated by
never writing the decision down, which scores a perfect zero forever. So there
is no date rule that can save this number; what protects it is that
`measure_reversals` always prints how many decisions the record holds beside
the count. A zero against three decisions and a zero against three hundred are
different readings, and the detail line says which one you are looking at.
"""
from __future__ import annotations

import re

#: Where the document lives in the vault. His folder, beside the other capture
#: files, for `expectations.md`'s reason -- most of the decisions worth
#: counting are his.
DECISIONS_PATH = "projects/sokrates/projects/nova/decisions.md"

DECISION_FIELDS = (
    "id", "about", "decision", "taken", "source", "reversed", "because")

#: Who took it. Same two words as `expectations.SOURCES`, and deliberately not
#: imported from there: the two records are allowed to diverge, and a shared
#: constant would make a change to one silently change the other.
SOURCES = ("edvard", "nova")

#: The window, in days, inside which a reversal counts against this key
#: result. A decision reversed after longer than this is not a reversal that
#: failed to hold -- it is a decision that held for a month and then the world
#: moved, which is `identity.md`'s own rule: *"he is allowed to change his
#: mind."*
HELD_DAYS = 30

#: `issue #227` / `idea #38`, in words, and optional -- a decision does not
#: have to be about a board row. Validated only when present, for
#: `expectations.ABOUT_RE`'s reason: a bare `#227` names a row on both boards.
ABOUT_RE = re.compile(r"^(?P<kind>issue|idea)\s+#(?P<number>\d+)$", re.I)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE_OPEN_RE = re.compile(r"^[ \t]*```[ \t]*(?P<name>[a-z-]+)[ \t]*$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*```[ \t]*$")
_FIELD_RE = re.compile(r"^(?P<key>[a-z-]+):[ \t]*(?P<value>.*?)[ \t]*$")


def _fields(lines):
    """Body lines of one fence -> `{field: value}`, unknown keys dropped."""
    out = {}
    for line in lines:
        match = _FIELD_RE.match(line.strip())
        if match and match.group("key") in DECISION_FIELDS:
            out[match.group("key")] = match.group("value")
    return out


def parse_decisions(markdown):
    """`decisions.md` -> a list of decision dicts, in document order.

    A fence this module does not own is skipped to its close, so a ```python
    sample in the prose can never be read as fields.
    """
    out, fence, body = [], None, []
    for line in (markdown or "").split("\n"):
        if fence is not None:
            if _FENCE_CLOSE_RE.match(line):
                if fence == "decision":
                    out.append(_fields(body))
                fence, body = None, []
            else:
                body.append(line)
            continue
        opened = _FENCE_OPEN_RE.match(line)
        if opened:
            fence, body = opened.group("name"), []
    return out


def about_target(row):
    """`{"about": "issue #227"}` -> `("issue", 227)`, or `None`."""
    match = ABOUT_RE.match((row.get("about") or "").strip())
    if not match:
        return None
    return match.group("kind").lower(), int(match.group("number"))


def days_held(row):
    """Days between `taken` and `reversed`, or `None` if either is unreadable.

    Plain ISO dates, so `datetime.date` does the arithmetic rather than a
    subtraction on strings.
    """
    from datetime import date
    taken = (row.get("taken") or "").strip()
    reversed_on = (row.get("reversed") or "").strip()
    if not (_ISO_RE.match(taken) and _ISO_RE.match(reversed_on)):
        return None
    return (date.fromisoformat(reversed_on) - date.fromisoformat(taken)).days


def problems(markdown_or_rows):
    """Everything wrong with the document that can be found by reading it.

    Deliberately not in here: whether the sentence in `decision` records a
    decision anybody actually took. Nothing readable off this box can tell
    that, and a regex pretending otherwise would only teach whoever writes one
    to phrase past it -- `expectations.problems` draws the same line.
    """
    rows = (parse_decisions(markdown_or_rows)
            if isinstance(markdown_or_rows, str) else list(markdown_or_rows))
    found, seen = [], {}
    for index, row in enumerate(rows, start=1):
        row_id = (row.get("id") or "").strip()
        where = row_id or f"the decision at position {index}"
        if not row_id:
            found.append(f"{where} has no `id`")
        elif row_id.lower() in seen:
            found.append(f"`{row_id}` is used by two decisions")
        else:
            seen[row_id.lower()] = index
        if not (row.get("decision") or "").strip():
            found.append(f"{where} has no `decision` -- a row with no sentence "
                         "records nothing that could later be reversed")
        taken = (row.get("taken") or "").strip()
        if not _ISO_RE.match(taken):
            found.append(f"{where} has `taken: {taken}`, which is not a "
                         "YYYY-MM-DD date -- without it nothing can say "
                         "whether a reversal came inside 30 days")
        about = (row.get("about") or "").strip()
        if about and about_target(row) is None:
            found.append(
                f"{where} has `about: {about}`, which names no board row -- "
                "write `issue #N` or `idea #N`, in words, because a bare `#N` "
                "names a row on both boards")
        reversed_on = (row.get("reversed") or "").strip()
        because = (row.get("because") or "").strip()
        if reversed_on and not _ISO_RE.match(reversed_on):
            found.append(f"{where} has `reversed: {reversed_on}`, which is not "
                         "a YYYY-MM-DD date")
        if reversed_on and not because:
            found.append(f"{where} is reversed and has no `because` -- a "
                         "reversal with no reason teaches the next decision "
                         "nothing")
        if because and not reversed_on:
            found.append(f"{where} has a `because` and no `reversed` date, so "
                         "nothing says when the decision was overturned")
        held = days_held(row)
        if held is not None and held < 0:
            found.append(f"{where} was reversed on {reversed_on}, before it "
                         f"was taken on {taken}")
        source = (row.get("source") or "").strip().lower()
        if source and source not in SOURCES:
            found.append(f"{where} has `source: {source}`, which is not one "
                         f"of {', '.join(SOURCES)}")
    return found


def measure_reversals(rows, since, until):
    """Decisions reversed inside the window, within `HELD_DAYS` of being taken.

    Returns `(count, detail)`. Zero is a real reading here and blank is not --
    the window is the unit (*per month*), the same call
    `measure_pm_deprecations` makes for its 30 days and
    `measure_nova_dropped_ticks` for its 24 hours.

    A reversal inside the window that came *after* `HELD_DAYS` is not counted
    and is named in the detail rather than dropped silently, because the two
    are different findings: a decision that lasted a day is a decision taken
    badly, and one that lasted five months is one the world outgrew.
    """
    counted, held_longer, undated = [], 0, 0
    for row in rows or ():
        reversed_on = (row.get("reversed") or "").strip()
        if not _ISO_RE.match(reversed_on):
            continue
        if reversed_on < since or reversed_on > until:
            continue
        held = days_held(row)
        if held is None:
            undated += 1
            continue
        if held > HELD_DAYS:
            held_longer += 1
            continue
        counted.append((row.get("id") or "").strip() or reversed_on)
    detail = (
        f"{len(counted)} decision(s) reversed in {since}..{until} within "
        f"{HELD_DAYS} days of being taken, out of {len(rows or ())} decision(s) "
        "on record; the count is a floor, because a decision nobody wrote down "
        "cannot be counted as reversed"
    )
    if counted:
        detail += " (" + ", ".join(sorted(counted)) + ")"
    if held_longer:
        detail += (f"; {held_longer} more were reversed in the window but had "
                   f"held longer than {HELD_DAYS} days, which is a decision "
                   "the world outgrew rather than one that did not hold")
    if undated:
        detail += (f"; {undated} carried a reversal date and no readable "
                   "`taken` date, so nothing could say how long they held")
    return len(counted), detail
