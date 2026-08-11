"""The backlog boards, parsed for the site: issues and ideas, his and mine.

Edvard, issues.md #57: *"I need more visualisations in the Nova app.
Create more pages to contain more, such as issue list, idea list
(separate pages) ..."* This is that page's data.

**Four files, two shapes.** `issues.md` and `ideas.md` one level up are
his, and they are boarded: a run of bare capture bullets at the top, a
`## Board` table, a `## Done` table, then a `# Details` section holding
one `## N — Title` block per item. `nova/resources/issues.md` and
`.../ideas.md` are mine, and they are not boarded at all -- they are a
flat `## Entries` list of one-line captures, newest first, which is what
`prompt.md` step 6 tells a cycle to write. So a board page has two tabs
and the tabs render differently, because the files genuinely are
different documents rather than two copies of one convention.

**The tables are the index, the details are the body.** Both carry a
status and they can disagree -- the table row is what a cycle rewrites
when it boards something, the `**Status:**` line inside the detail is
prose. The table wins here, because it is the one a cycle maintains
deliberately and the one that is complete: every item has a row, and
only some have a detail block.

**Nothing here does I/O.** Same split as `nova_journal`: the fetch is in
`nova_sources`, so this module is a pure function of text and its tests
need no vault.
"""

import re

# The four files, keyed the way the client asks for them. Literal paths,
# never composed from anything a request carries -- the same rule
# `nova_capture.CAPTURE_TARGETS` follows, and for the same reason.
BOARD_PATHS = {
    "issues": {
        "edvard": "projects/sokrates/projects/agora/issues.md",
        "nova": "projects/sokrates/projects/agora/nova/resources/issues.md",
    },
    "ideas": {
        "edvard": "projects/sokrates/projects/agora/ideas.md",
        "nova": "projects/sokrates/projects/agora/nova/resources/ideas.md",
    },
}

_SECTION_RE = re.compile(r"^(#{1,2})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# `| [[#57 — More pages in the Nova app|57]] | More pages ... | 🟡 In progress | 08-11 |`
# The wiki-link is Obsidian's, so the number is read out of the `#N`
# rather than out of the alias after the pipe -- the alias is a display
# label and two rows in the live file spell it differently.
_ROW_NUMBER_RE = re.compile(r"#(\d+)")
# `## 57 — More pages in the Nova app` inside `# Details`.
_DETAIL_RE = re.compile(r"^##[ \t]+(\d+)[ \t]*[—–-][ \t]*(.*?)[ \t]*$", re.MULTILINE)
# `- 2026-08-09 (Cycle 63) — the note itself`. Both halves optional: a
# few of my own captures were written without either.
_NOTE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})?[ \t]*(?:\(Cycle[ \t]+(?P<cycle>\d+)\))?"
                      r"[ \t]*[—–-]?[ \t]*(?P<text>.*)$", re.DOTALL)
_EMOJI_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def status_key(status):
    """`🟡 In progress` -> `in-progress`, for a CSS class and a filter.

    The emoji is stripped rather than switched on: the three in the live
    files are 🟡/⚪/✅, and a fourth arriving should still land in a
    readable bucket by its words instead of falling off the filter.
    """
    words = _EMOJI_RE.sub(" ", status or "").strip().lower()
    words = re.sub(r"\s+", "-", words)
    return words or "none"


# Obsidian's alias pipe, inside a wiki-link, inside a table cell:
# `| [[#57 — More pages in the Nova app|57]] | More pages ... |`. Every
# row of both live tables is written this way, so splitting a row on `|`
# naively yields five cells where the table has four and shifts every
# column one to the right -- the status lands in `updated`, the title
# lands in `status`. Masked before the split and restored after, rather
# than parsed with a smarter splitter, because the link is the only
# construct in these files that can contain the delimiter.
_ALIAS_PIPE = "\x00"
_WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")


def _table_rows(body):
    """A markdown table's data rows -> lists of stripped cells.

    The header and the `|---|` rule are dropped by shape rather than by
    position: a separator row is every cell being dashes, and the header
    is whatever came before the first one.
    """
    rows = []
    seen_rule = False
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        masked = _WIKILINK_RE.sub(lambda m: m.group(0).replace("|", _ALIAS_PIPE), line)
        cells = [
            cell.strip().replace(_ALIAS_PIPE, "|") for cell in masked.strip("|").split("|")
        ]
        if all(set(cell) <= set("-: ") and cell for cell in cells):
            seen_rule = True
            continue
        if not seen_rule:
            continue
        rows.append(cells)
    return rows


def _sections(markdown):
    """`[(level, title, body)]` for every `#`/`##` heading, in order."""
    found = []
    matches = list(_SECTION_RE.finditer(markdown or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        found.append((len(match.group(1)), match.group(2), markdown[match.end():end]))
    return found


def _captures(markdown):
    """The bare bullets above the first heading -- Edvard writing directly.

    Found structurally, the same way `nova_capture.insert_captures` finds
    the list it writes into: everything before the first heading, minus
    the frontmatter and minus the empty bullet that is his cursor. These
    are the strongest signal a cycle gets and the page should show them
    as unboarded rather than hiding them until a cycle files them.
    """
    lines = (markdown or "").split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                start = index + 1
                break
    bullets = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("- ") and stripped[2:].strip():
            bullets.append(stripped[2:].strip())
    return bullets


def parse_board(markdown):
    """One of Edvard's board files -> its captures, rows and detail bodies.

    Returns `{"captures": [...], "items": [...], "details": {n: markdown}}`.
    An item is `{number, title, status, statusKey, updated, where, done}`;
    `where` is only ever set from the `## Done` table's fourth column,
    which names the PRs a thing landed in.
    """
    captures = _captures(markdown)
    items = []
    details = {}
    seen = set()
    for level, title, body in _sections(markdown):
        name = title.strip().lower()
        if level == 2 and name in ("board", "done"):
            done = name == "done"
            for cells in _table_rows(body):
                if len(cells) < 4:
                    continue
                number = _ROW_NUMBER_RE.search(cells[0])
                if not number:
                    continue
                number = int(number.group(1))
                # A number in both tables is a boarding slip, not two
                # items. `## Board` is read first and keeps the row.
                if number in seen:
                    continue
                seen.add(number)
                items.append({
                    "number": number,
                    "title": cells[1],
                    "status": "✅ Done" if done else cells[2],
                    "statusKey": "done" if done else status_key(cells[2]),
                    "updated": cells[3] if not done else cells[2],
                    "where": cells[3] if done else "",
                    "done": done,
                })
            continue
        detail = _DETAIL_RE.match("## " + title) if level == 2 else None
        if detail:
            details[int(detail.group(1))] = body.strip()
    return {"captures": captures, "items": items, "details": details}


def parse_notes(markdown):
    """One of my own capture files -> `[{date, cycle, text}]`, newest first.

    The file is already newest-first because `prompt.md` step 6 says to
    prepend, so nothing is sorted here -- re-ordering by the parsed date
    would silently move any note whose date is missing or mistyped, and
    the file's own order is the record of when it was written.
    """
    notes = []
    for _, title, body in _sections(markdown):
        if title.strip().lower() != "entries":
            continue
        # A note is one line by convention and the live files are almost
        # entirely that -- but not entirely: one capture in
        # `nova/resources/issues.md` runs onto a second, indented line, and
        # reading bullets alone drops that sentence without saying so. A
        # continuation is joined into the note above it rather than
        # skipped, for the same reason `render_blocks` joins paragraph
        # lines: the break belongs to whoever wrapped it, not to the text.
        raw = []
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") and stripped[2:].strip():
                raw.append(stripped[2:].strip())
            elif stripped and raw and not stripped.startswith(("#", "|", "-")):
                raw[-1] = raw[-1] + " " + stripped
        for text in raw:
            match = _NOTE_RE.match(text)
            body_text = match.group("text").strip() if match else text
            if not body_text:
                continue
            notes.append({
                "date": (match.group("date") or "") if match else "",
                "cycle": int(match.group("cycle")) if match and match.group("cycle") else None,
                "text": body_text,
            })
    return notes
