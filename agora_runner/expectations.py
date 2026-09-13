"""Where a written expectation lives, so calibration can be counted.

Issue #227's Product management key result `pm-kr-calibration` -- *"You write
down what you expect before, and we check after"* -- had no instrument, and
the reason was not wiring. **Nothing on this box recorded a prediction at
all**, so there was no number to read and `now` was left blank rather than
written as zero. This module is the record that was missing.

One document, `expectations.md`, one ```expectation fence per prediction:

    ```expectation
    id: exp-227-calibration
    about: issue #227
    expect: <one sentence that is either true or false afterwards>
    written: 2026-09-14
    by: 2026-09-21
    source: edvard
    outcome: right
    checked: 2026-09-21
    ```

`outcome` and `checked` are absent while the prediction is open and written
together when it is settled. Everything outside a fence is prose that nothing
parses, the same rule `project-goals.md` and `goals.md` hold.

**The one rule that makes the number mean anything is the date.** An
expectation counts only when `written` is strictly *before* the day the item
it names was closed. Without that, the cheapest way to score well is to write
the expectation after the fact -- which is not calibration, it is
transcription, and it would read 100% forever. `measure_calibration` drops a
hindsight expectation and says how many it dropped, rather than silently
counting it or silently ignoring it.

**Why the denominator is closed board rows and not merged pull requests.**
The measure says *shipped items*. A pull request is a change, and five of them
routinely serve one row; the thing he had an expectation about is the row. It
is also the same denominator `measure_g1` divides by, so the two numbers
describe the same set of shipped work.
"""
from __future__ import annotations

import re

#: Where the document lives in the vault. His folder, beside the other capture
#: files, because an expectation is his to write -- the same reason `notes.md`
#: is not in Nova's own resources folder.
EXPECTATIONS_PATH = "projects/sokrates/projects/nova/expectations.md"

EXPECTATION_FIELDS = (
    "id", "about", "expect", "written", "by", "source", "outcome", "checked")

#: Three words and no fourth. `partly` exists because a prediction that was
#: half right is the common case and forcing it into `wrong` would make the
#: record lie in the direction that flatters nobody; it counts as *checked*
#: either way, which is what this key result measures.
OUTCOMES = ("right", "wrong", "partly")

#: Who wrote it. Both are legitimate and the field exists so the two can be
#: told apart when reading the record back -- the key result is about *his*
#: craft, so a document full of `nova` rows scoring well would be answering a
#: different question.
SOURCES = ("edvard", "nova")

#: `issue #227` / `idea #38`, in words. A bare `#227` is deliberately not
#: accepted, for `measure_pm_written_why`'s reason: the two boards are
#: separate pages with their own numbering, so a bare number names two rows.
ABOUT_RE = re.compile(r"^(?P<kind>issue|idea)\s+#(?P<number>\d+)$", re.I)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE_OPEN_RE = re.compile(r"^[ \t]*```[ \t]*(?P<name>[a-z-]+)[ \t]*$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*```[ \t]*$")
_FIELD_RE = re.compile(r"^(?P<key>[a-z-]+):[ \t]*(?P<value>.*?)[ \t]*$")


def _fields(lines):
    """Body lines of one fence -> `{field: value}`, unknown keys dropped.

    Dropping rather than keeping, for `project_goals._fields`' reason: an
    unrecognised key is a typo far more often than a field somebody added.
    """
    out = {}
    for line in lines:
        match = _FIELD_RE.match(line.strip())
        if match and match.group("key") in EXPECTATION_FIELDS:
            out[match.group("key")] = match.group("value")
    return out


def parse_expectations(markdown):
    """`expectations.md` -> a list of expectation dicts, in document order.

    A fence this module does not own is skipped to its close, so a ```python
    sample in the prose can never be read as fields.
    """
    out, fence, body = [], None, []
    for line in (markdown or "").split("\n"):
        if fence is not None:
            if _FENCE_CLOSE_RE.match(line):
                if fence == "expectation":
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


def problems(markdown_or_rows):
    """Everything wrong with the document that can be found by reading it.

    Deliberately not in here: whether the sentence in `expect` is actually
    checkable afterwards. *"The landing page will be better"* passes every
    check below and predicts nothing, and a regex pretending otherwise would
    only teach whoever writes one to phrase mush past it.
    """
    rows = (parse_expectations(markdown_or_rows)
            if isinstance(markdown_or_rows, str) else list(markdown_or_rows))
    found, seen = [], {}
    for index, row in enumerate(rows, start=1):
        row_id = (row.get("id") or "").strip()
        where = row_id or f"the expectation at position {index}"
        if not row_id:
            found.append(f"{where} has no `id`")
        elif row_id.lower() in seen:
            found.append(f"`{row_id}` is used by two expectations")
        else:
            seen[row_id.lower()] = index
        if not (row.get("expect") or "").strip():
            found.append(f"{where} has no `expect` -- an expectation with no "
                         "sentence cannot be checked afterwards")
        if about_target(row) is None:
            found.append(
                f"{where} has `about: {(row.get('about') or '').strip()}`, "
                "which names no board row -- write `issue #N` or `idea #N`, "
                "in words, because a bare `#N` names a row on both boards")
        written = (row.get("written") or "").strip()
        if not _ISO_RE.match(written):
            found.append(f"{where} has `written: {written}`, which is not a "
                         "YYYY-MM-DD date -- the date is what separates a "
                         "prediction from a description")
        outcome = (row.get("outcome") or "").strip().lower()
        checked = (row.get("checked") or "").strip()
        if outcome and outcome not in OUTCOMES:
            found.append(f"{where} has `outcome: {outcome}`, which is not one "
                         f"of {', '.join(OUTCOMES)}")
        if outcome and not _ISO_RE.match(checked):
            found.append(f"{where} is settled but has `checked: {checked}`, "
                         "which is not a YYYY-MM-DD date")
        if checked and not outcome:
            found.append(f"{where} has a `checked` date and no `outcome`, so "
                         "nothing says how the prediction turned out")
        source = (row.get("source") or "").strip().lower()
        if source and source not in SOURCES:
            found.append(f"{where} has `source: {source}`, which is not one "
                         f"of {', '.join(SOURCES)}")
    return found


def closed_items(boards_by_kind, since, until):
    """Every board row closed inside the window, as `{(kind, number): date}`.

    `boards_by_kind` is `{"issue": rows, "idea": rows}` -- named rather than
    positional so a caller cannot silently swap the two boards. A row's
    closing date is its `updated` cell, which is the only date it carries:
    nothing on a board record dates a status change, so a row closed in July
    and edited in September reads as September.
    """
    year = (since or "")[:4]
    out = {}
    for kind, rows in (boards_by_kind or {}).items():
        for row in rows or ():
            if (row.get("statusKey") or "") != "done":
                continue
            day = _in_year(row.get("updated"), year)
            if not day or day < since or day > until:
                continue
            number = row.get("number")
            if isinstance(number, int):
                out[(kind, number)] = day
    return out


def _in_year(value, year):
    """`"09-13"` -> `"2026-09-13"`; an already-full date is left alone."""
    text = (value or "").strip()
    if _ISO_RE.match(text):
        return text
    if re.match(r"^\d{2}-\d{2}$", text) and year:
        return f"{year}-{text}"
    return None


def measure_calibration(rows, boards_by_kind, since, until):
    """Share of items shipped in the window that carried a checked prediction.

    Returns `(percentage, detail)`, or `(None, why)` when nothing shipped in
    the window -- a share with no denominator is missing, not zero.

    **Zero here is a real reading and blank is not.** Before this document
    existed there was nothing to divide, so `now` was blank; with it, a window
    where nothing was predicted genuinely measures 0% and should say so.
    """
    closed = closed_items(boards_by_kind, since, until)
    if not closed:
        return None, ("no board row was closed in the window, so a share of "
                      "shipped items has no denominator")
    settled, hindsight = set(), 0
    for row in rows or ():
        target = about_target(row)
        if target is None or target not in closed:
            continue
        if (row.get("outcome") or "").strip().lower() not in OUTCOMES:
            continue
        written = (row.get("written") or "").strip()
        if not _ISO_RE.match(written) or written >= closed[target]:
            hindsight += 1
            continue
        settled.add(target)
    share = round(100 * len(settled) / len(closed))
    detail = (
        f"{len(settled)} of {len(closed)} row(s) closed in the window carried "
        "an expectation written before the day they closed and checked after; "
        "a row's closing date is its Updated cell, the only date it carries"
    )
    if hindsight:
        detail += (f"; {hindsight} settled expectation(s) were NOT counted "
                   "because they were written on or after the day the row "
                   "closed, which is a description and not a prediction")
    return share, detail
