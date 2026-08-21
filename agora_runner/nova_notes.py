"""Edvard's notes page.

His capture, `issues.md` 2026-08-21: *"I do not have a notes page that
shows any overview of the notes made."*

He has three capture files and the Nova app showed two of them. `/issues`
and `/ideas` are real pages with boards, priorities and comment threads;
`notes.md` had a button that writes to it and nothing that reads it back.
So the only way he could see a note he had left -- or find out whether a
cycle ever acted on it -- was to open Obsidian.

A note is not a board row and this deliberately does not turn it into
one. `notes.md`'s contract says a note is *"never numbered, never
boarded"*: bare bullets at the top are unread, and `prompt.md` step 1a
tells a cycle to move each one under `## Read` with one line on what it
did. That structure is already the whole answer to "what happened to my
note", so the page shows exactly it -- waiting notes first, then answered
ones with the cycle's reply attached.

The one thing worth reading twice is `_response_cycle`. A cycle's reply
opens `Read Cycle 290.` or `Corrected Cycle 244,`, which is a link to a
journal card the site already renders at `/cycle/290`. Pulling that
number out here rather than in `app.js` keeps the shape of a reply line
in one place, next to the parser that produced it.
"""

import re

from agora_runner.nova_journal import render_blocks
from agora_runner.nova_sources import notes_markdown


# `## Read` is the heading `prompt.md` step 1a names, and it is the only
# heading the file has. Matched case-insensitively because the contract
# is prose in a frontmatter field, not a schema.
READ_HEADING = "read"

# `- ` at the start of a line is a note; two or more leading spaces then
# `- ` is a cycle answering the note above it. The file is written by
# hand and by `nova_capture`, so the indent is not guaranteed to be
# exactly two -- anything indented counts as a response.
# `\s*` rather than `\s+` after the dash, so a bare `-` still reads as a
# bullet. That is Edvard's cursor, and it has to be *recognised* and then
# dropped for being empty -- matched as prose instead, it gets joined
# onto the note above it as a stray dash.
_NOTE_RE = re.compile(r"^-\s*(.*)$")
_RESPONSE_RE = re.compile(r"^\s+[-*]\s*(.*)$")
# Anything that is not a heading and not a bullet continues whatever came
# before it, indented or not. It has to accept column zero: this file is
# written one line per paragraph *by convention*, and a note pasted in
# from somewhere that hard-wraps would otherwise have every line after
# the first silently vanish off the page. `|` is excluded for
# `parse_notes`' reason -- a table row is not prose.
_CONTINUATION_RE = re.compile(r"^\s*([^\s|].*)$")

# "Read Cycle 290." / "Corrected Cycle 244," / "Cycle 247:" -- the number
# is what links; the verb in front of it varies and is not worth pinning.
_CYCLE_RE = re.compile(r"\bCycle\s+(\d+)\b")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)


def _strip_frontmatter(markdown):
    """Everything after a leading `---` block, or the text unchanged."""
    text = markdown or ""
    if not text.startswith("---"):
        return text
    close = text.find("\n---", 3)
    if close == -1:
        return text
    newline = text.find("\n", close + 1)
    return text[newline + 1:] if newline != -1 else ""


def _response_cycle(text):
    """`"Read Cycle 290. Reviewed..."` -> `290`. No number -> `None`."""
    match = _CYCLE_RE.search(text or "")
    return int(match.group(1)) if match else None


def _bullets(body):
    """A block of markdown -> `[{text, responses: [str]}]`, in file order.

    Continuations are joined onto whatever they continue -- a note or a
    response -- for `parse_notes`' reason: the line break belongs to
    whoever wrapped the text, not to the text. The distinction that
    matters is `- ` at column zero (a new note) against an indented
    bullet (a cycle answering the note above it), and a continuation is
    neither.
    """
    items = []
    for line in (body or "").split("\n"):
        if not line.strip():
            continue
        if _HEADING_RE.match(line):
            break
        note = _NOTE_RE.match(line)
        if note:
            text = note.group(1).strip()
            # His cursor. `nova_capture` keeps an empty bullet at the top
            # of every capture file so there is somewhere to type; it is
            # not a note and the page must not draw an empty card for it.
            if text:
                items.append({"text": text, "responses": []})
            continue
        if not items:
            continue
        response = _RESPONSE_RE.match(line)
        if response:
            items[-1]["responses"].append(response.group(1).strip())
            continue
        continuation = _CONTINUATION_RE.match(line)
        if continuation:
            if items[-1]["responses"]:
                items[-1]["responses"][-1] += " " + continuation.group(1).strip()
            else:
                items[-1]["text"] += " " + continuation.group(1).strip()
    return items


def parse_notes_page(markdown):
    """`notes.md` -> `{"waiting": [...], "read": [...]}`.

    `waiting` is the bare bullets above the first heading -- notes no
    cycle has picked up yet. `read` is what is under `## Read`, newest
    first, each carrying the cycle replies written under it.

    A file with no `## Read` heading yet parses to an empty `read` and
    whatever is at the top, which is what the very first note in a fresh
    file looks like.
    """
    text = _strip_frontmatter(markdown)
    heading = None
    for match in _HEADING_RE.finditer(text):
        heading = match
        break
    above = text[: heading.start()] if heading else text
    waiting = _bullets(above)

    read = []
    for match in _HEADING_RE.finditer(text):
        if match.group(2).strip().lower() != READ_HEADING:
            continue
        end = text.find("\n#", match.end())
        body = text[match.end(): end if end != -1 else len(text)]
        read.extend(_bullets(body))
        break
    return {"waiting": waiting, "read": read}


def _shape(note, waiting):
    responses = [
        {"blocks": render_blocks(line), "cycle": _response_cycle(line)}
        for line in note["responses"]
    ]
    return {
        "text": note["text"],
        "blocks": render_blocks(note["text"]),
        "responses": responses,
        # A note under `## Read` that nobody wrote a line under is a real
        # state -- a cycle moved it and skipped the half of the contract
        # that says what it did -- and the page says so rather than
        # drawing an answered card with nothing in it.
        "answered": bool(responses),
        "waiting": waiting,
    }


def notes_payload():
    """Everything the `/notes` page draws.

    Not windowed. The whole file is 11KB against `issues.md`'s 68KB, and
    it grows by a note every few days rather than by one an hour -- so a
    limit here would be the cap with no measurement behind it that
    `personality.md` spends a section on.
    """
    parsed = parse_notes_page(notes_markdown())
    waiting = [_shape(note, True) for note in parsed["waiting"]]
    read = [_shape(note, False) for note in parsed["read"]]
    return {
        "notes": waiting + read,
        "waitingTotal": len(waiting),
        "readTotal": len(read),
    }
