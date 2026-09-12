"""Which of my own capture lines name a symbol that was built after I wrote them.

Three cycles in a row wrote the same warning into `resources/issues.md`:
a capture line naming a gap is stale by the time a later cycle picks it
up. Cycle 1442 wrote it, Cycle 1450 wrote it, and Cycle 1451 spent eight
minutes proving it four more times -- every Marcus gap it chased had
already been built. `prompt.md` step 2 is explicit about what to do with
that shape: *"Three fixes of the same shape means the shape is the bug ...
the pick is not check number four. It is deleting the thing that generates
them."* A fourth note would have been check number four.

The mechanical half is the one worth taking, and it is narrower than
"is this note still true". **A note is suspect when it backticks a symbol
that did not exist on the day the note was written.** That is a date
comparison against `git log -S`, not a judgement: if `draftVolume` first
appears in the repo on 09-12 and the note naming it is dated 09-11, then
whatever the note said about `draftVolume` was written about something
that was not there yet -- which is almost always a gap that has since
been filled.

The three verdicts are deliberately separate, for the reason
`heartbeat_health` keeps `OFF` and `OVERDUE` apart:

- `BUILT AFTER` -- the symbol postdates the note. Read the code first.
- `PREDATES` -- the symbol was already there when I wrote the note, so the
  note knew about it and names something else. No signal either way.
- `ABSENT` -- nothing in any checkout carries the symbol. Still a gap.

`ABSENT` is the measured negative that makes the other two mean something.
If every line came back `BUILT AFTER` the check would be worthless, so the
tool prints the counts for all three rather than only the findings.

**This never edits and never refuses.** Same call `lint_entry` makes on an
over-scoped claim: a symbol appearing after a note is strong evidence and
not proof -- the note may be about a rename, or about the symbol being
wrong rather than missing -- so the verdict goes in front of the cycle and
the cycle decides. A tool that deleted a capture line on this rule would
eventually delete a true one.

No I/O here: text and dates in, verdicts out. `tools/capture_stale.py` does
the greps and the `git log` calls.
"""

import re

#: A backticked span that is plausibly a code identifier rather than English.
#: The span must be a bare identifier, at least four characters, and carry
#: either an interior capital (`draftVolume`) or an underscore
#: (`parse_notes`). That shape is what excludes the ordinary words this
#: journal backticks constantly -- `main`, `note`, `plan`, `get`, `put` --
#: without keeping a list of English words, which would be a second copy of
#: the language to maintain. A symbol that is genuinely all-lowercase and
#: unpunctuated (`weekTarget` is fine, `homegoal` would not be) is missed on
#: purpose: the cost of a false positive here is a cycle reading code it did
#: not need to, and the cost of a false negative is the status quo.
_IDENTIFIER = re.compile(r"^(?=.{4,})[A-Za-z_][A-Za-z0-9_]*$")
_BACKTICKED = re.compile(r"`([^`\n]+)`")

BUILT_AFTER = "BUILT AFTER"
PREDATES = "PREDATES"
ABSENT = "ABSENT"


def _is_symbol(span):
    if not _IDENTIFIER.match(span):
        return False
    # All-lowercase with no underscore is an English word far more often
    # than it is a symbol, and the ones that are symbols are usually also
    # spelled somewhere with a capital or an underscore.
    return any(c.isupper() for c in span[1:]) or "_" in span


def symbols(text):
    """The backticked identifiers in one capture line, in order, deduplicated.

    Order is kept rather than sorted so the report reads in the order the
    note does; a `dict` is the deduplicator because it preserves insertion
    order and a `set` does not.
    """
    found = {}
    for span in _BACKTICKED.findall(text or ""):
        span = span.strip()
        if _is_symbol(span):
            found[span] = None
    return list(found)


def verdict(note_date, first_seen):
    """One symbol's verdict: `ABSENT`, `BUILT AFTER` or `PREDATES`.

    `first_seen` is the ISO date (`YYYY-MM-DD`) of the oldest commit that
    added the symbol, or a falsy value if no checkout carries it. Dates are
    compared as strings, which is exact for ISO-8601 and needs no parsing --
    the one thing to know is that an equal date reads as `PREDATES`. That is
    the safe direction: a symbol committed on the same day I wrote the note
    could have landed either side of it, and calling that `BUILT AFTER`
    would send a cycle to read code over a coin flip.
    """
    if not first_seen:
        return ABSENT
    if not note_date:
        # An undated note cannot be compared to anything. It is not a
        # finding, and it is not clean either -- it is just unjudgeable,
        # and PREDATES is the verdict that adds no noise.
        return PREDATES
    return BUILT_AFTER if first_seen > note_date else PREDATES


def line_verdict(symbol_verdicts):
    """The verdict for a whole capture line, from its symbols'.

    One `BUILT AFTER` is enough to make the line worth reading the code
    for -- a line naming three symbols, two of them old and one brand new,
    is exactly the stale-gap shape. A line whose symbols are all absent is
    `ABSENT`; anything else is `PREDATES`.
    """
    if not symbol_verdicts:
        return None
    if BUILT_AFTER in symbol_verdicts:
        return BUILT_AFTER
    if all(v == ABSENT for v in symbol_verdicts):
        return ABSENT
    return PREDATES


def render(rows, scanned, skipped):
    """The report a cycle reads: findings first, then what was measured.

    `rows` is one dict per capture line that carried at least one symbol:
    `{date, cycle, text, verdict, symbols}` where `symbols` is a list of
    `{name, verdict, first_seen, where}`.
    """
    findings = [r for r in rows if r["verdict"] == BUILT_AFTER]
    out = []
    if findings:
        out.append(f"{len(findings)} capture line(s) name a symbol that did not exist "
                   f"when the line was written — read the code before picking one up:")
        for row in findings:
            stamp = row.get("date") or "(undated)"
            cycle = f" (Cycle {row['cycle']})" if row.get("cycle") else ""
            out.append(f"  {stamp}{cycle} — {row['text'][:160]}")
            for sym in row["symbols"]:
                if sym["verdict"] != BUILT_AFTER:
                    continue
                out.append(f"      `{sym['name']}` first committed {sym['first_seen']} "
                           f"in {sym['where']}")
    else:
        out.append("No capture line names a symbol newer than itself.")
    out.append("")
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    out.append(
        f"Judged {len(rows)} line(s) carrying a backticked identifier: "
        f"{counts.get(BUILT_AFTER, 0)} built after, "
        f"{counts.get(PREDATES, 0)} predating, "
        f"{counts.get(ABSENT, 0)} still absent everywhere."
    )
    out.append(f"Scanned {scanned} checkout(s). "
               f"{skipped} line(s) backtick nothing that looks like an identifier "
               f"and cannot be judged this way.")
    return "\n".join(out)
