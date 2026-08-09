"""Comments on a cycle: Edvard replying to a particular entry, not filing work.

Agora ideas.md #44, in his words -- *"add a button with a chat bubble icon
that opens a multiline text input so that i can add a comment more
directly towards your cycles. An example is for cycle 63, i want to say
'great research, keep it up! Do more research to make yourself more token
efficient'"*.

**Why this is not the capture box.** The box at the top of the site files
a bullet into `issues.md`/`ideas.md`, which is a backlog. His own example
is not a backlog item -- it is a reply *about cycle 63*, and stripped of
which cycle it answers it loses most of its meaning. So a comment is
stored keyed by cycle number, and the number is the whole point.

**The channel only exists if a cycle reads it.** A comment nobody collects
is Cycle 58's "Needs Edvard" box all over again: built, tested, shipped
and dead. So the file has two sections and a comment is not done when it
is written -- `## New` is Edvard's outbox and Nova's inbox, and a cycle
moves what it has acted on down to `## Acknowledged` with what it did,
the same shape `inbox.md` already uses and rule 8's "organised, not
annotated inline". `prompt.md` step 1a reads `## New` every cycle and
never delegates it, because these are Edvard's exact words.

**Parsing is structural, never positional** -- the same rule
`nova_capture` follows. A comment heading is recognised by its shape
(`### Cycle <n> · <stamp>`) rather than by being the third line of
something, and the two section headings are matched exactly. That is what
lets the body be stored *verbatim*: his text is never escaped, quoted or
reflowed on the way in, so what a future cycle reads is character for
character what he typed. The residual collision -- a comment whose own
body contains a line reading exactly `## Acknowledged` -- would mis-split
the *display*, never the file, and no text is lost either way. Escaping
his prose to defend against that would cost more than it buys.

**The write is a read-modify-write against a live vault and can lose,**
so it retries on 409 exactly as `nova_capture.capture` does, re-reading
each time rather than resending. Any non-409 failure is not a conflict
and would fail identically on a retry.
"""

import re
from datetime import datetime, timedelta, timezone

from agora_runner.log import log
from agora_runner.vault import vault_read_path, vault_write_path

COMMENTS_PATH = "projects/sokrates/projects/agora/nova/resources/comments.md"

NEW_HEADING = "## New"
ACKNOWLEDGED_HEADING = "## Acknowledged"

WRITE_ATTEMPTS = 3

# `### Cycle 63 · 2026-08-09 22:40`. The separator is matched loosely so a
# heading hand-edited in Obsidian still parses; the cycle number is the
# only part anything depends on.
_COMMENT_HEADING_RE = re.compile(
    r"^###[ \t]+Cycle[ \t]+(?P<cycle>\d+)[ \t]*(?:[·\-—][ \t]*(?P<stamp>.*?))?[ \t]*$",
    re.IGNORECASE,
)

_SECTION_RE = re.compile(r"^##[ \t]+(?P<name>.+?)[ \t]*$")

# He lives in Oslo and asked for Oslo time in anything he reads
# (identity.md rule 7). CET/CEST without pulling in a tz database: Norway
# is UTC+1, UTC+2 between the last Sunday of March and the last Sunday of
# October. A stamp an hour out on two nights a year is worth less than a
# dependency this image does not otherwise need.
def _oslo_now():
    now = datetime.now(timezone.utc)
    return now + timedelta(hours=2 if _is_summer_time(now) else 1)


def _last_sunday(year, month):
    """UTC datetime of 01:00 on the last Sunday of `month` -- when EU DST turns."""
    day = 31
    while True:
        candidate = datetime(year, month, day, 1, 0, tzinfo=timezone.utc)
        if candidate.weekday() == 6:  # Sunday
            return candidate
        day -= 1


def _is_summer_time(moment):
    return _last_sunday(moment.year, 3) <= moment < _last_sunday(moment.year, 10)


def format_stamp(moment=None):
    return (moment or _oslo_now()).strftime("%Y-%m-%d %H:%M")


def clean_comment_text(text):
    """Text as typed -> text as stored.

    Only trailing whitespace per line and blank lines at either end are
    removed; the interior is untouched. Unlike a capture, a comment is
    prose and its paragraph breaks are his, so nothing here splits, joins
    or re-wraps -- see the verbatim note in the module docstring.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _section_bounds(lines, heading):
    """(start, end) of the body of `heading`, or None if it is absent.

    `start` is the line after the heading; `end` is the next `##` heading
    or end of file.
    """
    start = None
    for i, line in enumerate(lines):
        if start is None:
            if line.strip().lower() == heading.lower():
                start = i + 1
            continue
        if _SECTION_RE.match(line):
            return start, i
    if start is None:
        return None
    return start, len(lines)


def insert_comment(markdown, cycle, text, stamp):
    """Add one comment to the top of `## New`, newest first.

    Newest first matches every other file this loop maintains and means a
    cycle reads the freshest thing Edvard said without scrolling. If the
    section is missing entirely it is created, so a comment can never be
    dropped for want of a heading it did not have.
    """
    lines = markdown.split("\n") if markdown else []
    block = [f"### Cycle {cycle} · {stamp}", ""] + text.split("\n") + [""]

    bounds = _section_bounds(lines, NEW_HEADING)
    if bounds is None:
        # No `## New` at all. Put it above `## Acknowledged` if that
        # exists, so the two stay in their documented order.
        ack = None
        for i, line in enumerate(lines):
            if line.strip().lower() == ACKNOWLEDGED_HEADING.lower():
                ack = i
                break
        section = [NEW_HEADING, ""] + block
        if ack is not None:
            return "\n".join(lines[:ack] + section + lines[ack:])
        tail = [] if (lines and not lines[-1].strip()) else [""]
        return "\n".join(lines + tail + section)

    start, _ = bounds
    # Skip the blank line that follows the heading so the new comment lands
    # under it rather than jammed against it.
    while start < len(lines) and not lines[start].strip():
        start += 1
    return "\n".join(lines[:start] + block + lines[start:])


def parse_comments(markdown):
    """Markdown -> [{cycle, stamp, text, acknowledged}], newest-first per section.

    Order within the file is preserved rather than sorted: `## New` is
    written newest-first and `## Acknowledged` accumulates in the order
    cycles retired things, and both are information.
    """
    out = []
    section = None
    current = None

    def flush():
        if current is None:
            return
        body = "\n".join(current["lines"]).strip("\n")
        out.append({
            "cycle": current["cycle"],
            "stamp": current["stamp"],
            "text": body,
            "acknowledged": current["acknowledged"],
        })

    for line in (markdown or "").split("\n"):
        heading = _COMMENT_HEADING_RE.match(line)
        if heading and section is not None:
            flush()
            current = {
                "cycle": int(heading.group("cycle")),
                "stamp": (heading.group("stamp") or "").strip(),
                "acknowledged": section == "acknowledged",
                "lines": [],
            }
            continue
        match = _SECTION_RE.match(line)
        if match:
            flush()
            current = None
            name = match.group("name").strip().lower()
            if name == "new":
                section = "new"
            elif name == "acknowledged":
                section = "acknowledged"
            else:
                section = None
            continue
        if current is not None:
            current["lines"].append(line)
    flush()
    return out


def comments_by_cycle(markdown):
    """`{cycle: [comment, ...]}` -- what the site hangs off each card."""
    grouped = {}
    for comment in parse_comments(markdown):
        grouped.setdefault(comment["cycle"], []).append(comment)
    return grouped


def add_comment(cycle, text, stamp=None):
    """Store one comment against `cycle`. Returns (ok, message)."""
    try:
        cycle = int(cycle)
    except (TypeError, ValueError):
        return False, f"cycle must be a number, got {cycle!r}"
    if cycle < 0:
        return False, "cycle must not be negative"
    body = clean_comment_text(text)
    if not body:
        return False, "nothing to comment"

    stamp = stamp or format_stamp()
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current = vault_read_path(COMMENTS_PATH)
        if current is None:
            # First comment ever, or the file was moved. Creating it is
            # strictly better than refusing: the alternative is losing
            # what he typed to a missing heading.
            current = ""
        result = vault_write_path(COMMENTS_PATH, insert_comment(current, cycle, body, stamp))
        if result == "written":
            log(f"nova-comment stored on cycle {cycle}")
            return True, f"commented on cycle {cycle}"
        if "409" not in result:
            break
    log(f"nova-comment failed writing cycle {cycle}: {result}")
    return False, f"could not write comment: {result}"
