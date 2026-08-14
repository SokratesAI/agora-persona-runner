"""The backlog boards, parsed for the site: issues and ideas, his and mine.

Edvard, issues.md #57: *"I need more visualisations in the Nova app.
Create more pages to contain more, such as issue list, idea list
(separate pages) ..."* This is that page's data.

**Four files, two shapes.** `issues.md` and `ideas.md` in
`projects/sokrates/projects/nova/` are his -- a sibling of the `agora`
folder, not inside it, since 2026-08-12 -- and they are boarded: a run of bare capture bullets at the top, a
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
        # Moved out of the agora folder 2026-08-12, still in his database.
        # See the note on `nova_capture.CAPTURE_TARGETS`.
        "edvard": "projects/sokrates/projects/nova/issues.md",
        "nova": "projects/sokrates/projects/agora/nova/resources/issues.md",
        # Where `tools/roll_captures.py` files the older half of `nova`.
        # The site has to read it or rolling the live file deletes two
        # thirds of this page -- the blocker Cycle 112 found and refused
        # to roll around. Same shape as `DIGEST_ARCHIVE_PATH`, and safe in
        # either deploy order: the file does not exist until the first
        # roll, and a missing archive parses to no notes at all.
        "nova_archive": (
            "projects/sokrates/projects/agora/nova/resources/issues-archive.md"
        ),
    },
    "ideas": {
        "edvard": "projects/sokrates/projects/nova/ideas.md",
        "nova": "projects/sokrates/projects/agora/nova/resources/ideas.md",
        "nova_archive": (
            "projects/sokrates/projects/agora/nova/resources/ideas-archive.md"
        ),
    },
}

_SECTION_RE = re.compile(r"^(#{1,2})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# `| [[#57 — More pages in the Nova app|57]] | More pages ... | 🟡 In progress | 08-11 |`
# The wiki-link is Obsidian's, so the number is read out of the `#N`
# rather than out of the alias after the pipe -- the alias is a display
# label and two rows in the live file spell it differently.
_ROW_NUMBER_RE = re.compile(r"#(\d+)")
# A detail heading inside `# Details`, in either shape the live files use:
# `## 57 — More pages in the Nova app` and `### #84 — Edit and delete a
# boarded idea or issue by holding the card`.
#
# **The second shape was unreadable for twenty-one of Edvard's rows.** This
# pattern was `^##[ \t]+(\d+)` -- two hashes, no `#` before the number --
# and `_sections` only ever offered it `#`/`##` headings, so a `### #84`
# write-up was not a section and never reached it. Measured against the
# live files 2026-08-14: 21 of 85 issue rows and 4 of 72 idea rows had a
# write-up in the file and showed *"No write-up yet -- only the board
# row."* on the page, including every issue from #70 up. Whichever cycle
# started writing `### #N —` changed the shape and nothing here noticed,
# because a missing detail renders as a legitimate state rather than an
# error.
#
# Both shapes are accepted rather than one normalised, because these are
# Edvard's files: rewriting 87 headings to suit the parser is a large diff
# through his prose to fix a regex.
_DETAIL_RE = re.compile(
    r"^(#{2,3})[ \t]+(#?)(\d+)[ \t]*[—–-][ \t]*(.*?)[ \t]*$", re.MULTILINE)
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


# The fifth status, and the exact cell text a cycle writes it as.
# Edvard, `issues.md` #85: *"Some of them are implemented and some of them
# are outdated. We need to clean it up. Maybe we need a new status called
# 'outdated', so i can go through them and delete them myself."* So the
# split of labour is his: a cycle proposes, he deletes. Nothing here sets
# it -- the sweep is a vault edit -- but the string lives beside
# `PRIORITY_LABELS` so the app's `chip-outdated` and this file cannot
# drift into two different spellings of the same status.
OUTDATED_STATUS = "⚫ Outdated"

# The exact cell text a status is written as, keyed by what `status_key`
# reduces it to -- the same shape as `PRIORITY_LABELS`, and beside it for
# the same reason: the app's `chip-outdated` class, `OUTDATED_STATUS` and
# whatever a cycle types into the cell must not drift into three
# spellings of one status. `🟢 Done` is live on issue #63 and reduces to
# `done` here, so it is read correctly and rewritten to the ✅ spelling
# the other 91 rows use if anything ever sets it again.
STATUS_LABELS = {
    "backlog": "⚪ Backlog",
    "in-progress": "🟡 In progress",
    "done": "✅ Done",
    "outdated": OUTDATED_STATUS,
}

# The statuses that mean the row is finished with, either way. A finished
# row takes no rating: `set_row_priority` already refused `done` because a
# chip on a shipped item is noise, and "will never be built" is the same
# state for the same reason.
_CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})


# Edvard's four ratings, and the words he actually used for them:
# "low, medium, high, immediately priority" (`ideas.md` capture,
# 2026-08-14). "immediately" is his word and "immediate" is the one a
# hand-edit is likely to reach for, so both land in the same bucket
# rather than one of them falling off the sort.
_PRIORITY_ALIASES = {"immediately": "immediate", "now": "immediate", "urgent": "immediate"}

# The exact cell text a rating is written as, keyed by what `priority_key`
# reduces it to. `""` clears the cell back to unrated, which has to stay
# reachable: Cycle 188 rated all 71 open rows itself, so every rating on
# both boards right now is mine, and "actually nobody has decided this"
# is an answer Edvard must be able to give me back.
PRIORITY_LABELS = {
    "": "",
    "low": "⚪ Low",
    "medium": "🔵 Medium",
    "high": "🟠 High",
    "immediate": "🔴 Immediately",
}


def priority_key(priority):
    """`🔴 Immediately` -> `immediate`, for a CSS class and a filter.

    Emoji-stripped and aliased the same way `status_key` is, and for the
    same reason: the rating is written by hand, by Edvard, in Obsidian,
    so it has to survive him typing a synonym or dropping the emoji. An
    unrated row returns `""` rather than a bucket, because "nobody has
    rated this" is a real state -- `prompt.md` tells a cycle to fill it
    in, and inventing a default here would hide the ones it must visit.
    """
    words = _EMOJI_RE.sub(" ", priority or "").strip().lower()
    words = re.sub(r"\s+", "-", words)
    return _PRIORITY_ALIASES.get(words, words)


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


def split_capture_priority(bullet):
    """`🟠 text` -> `("🟠 High", "text")`. Unrated -> `("", bullet)`.

    The other half of Edvard's capture: *"i want that aswell both when i
    input in the textbox in the Nova app"*. A capture is a bare bullet in
    his file and has nowhere to put a column, so the rating rides at the
    front of the bullet as the one glyph the rating already is -- which
    reads correctly in Obsidian, survives him editing the line by hand,
    and is what a cycle lifts into the `Priority` cell when it boards the
    item and strips from the title.

    Matched on the emoji alone rather than on `emoji + word`, because the
    bullet is his text from that point on and he may well rewrite the
    word. Only a leading glyph counts: a rating is a prefix, and a 🔴 in
    the middle of a sentence about a red dot is prose.
    """
    text = (bullet or "").strip()
    for key, label in PRIORITY_LABELS.items():
        if not key:
            continue
        glyph = label.split(" ", 1)[0]
        if text.startswith(glyph):
            return label, text[len(glyph):].strip()
    return "", text


def set_row_priority(markdown, number, priority):
    """Rewrite one `## Board` row's rating cell. Returns markdown, or `None`.

    Edvard's capture, `issues.md` 2026-08-14: *"You made it possible for
    yourself to rate the priority of tasks, but i want that aswell both
    when i input in the textbox in the Nova app, and when they are boarded
    its possible for me to change the priority."* Until now the only way
    to change a cell I had written was to open Obsidian.

    `None` means "not written", never "written unchanged", and there are
    three ways to get it: no row carries that number, the row is in
    `## Done`, or `priority` is not one of the four ratings. A caller
    cannot tell those apart and does not need to -- all three mean the
    file must not be touched. Refusing a finished row is not fussiness,
    and it is not enough to refuse the `## Done` table: most finished rows
    never move there. `#76` is `✅ Done` and still sits in `## Board`,
    where `parse_board` sets `done=False` and *would* read a fifth cell
    back -- so accepting one here puts a rating chip on a finished item,
    which is the one state Cycle 188 deliberately left empty. The status
    cell is what decides, not which table the row is in.

    The rewrite is line-wise on the raw file rather than a reparse-and-
    render, because these files are Edvard's and everything I am not
    editing has to come back byte-identical -- his prose, his detail
    sections, the blank lines the two files disagree about. Only the one
    row is rebuilt, and only from cells that were already in it.
    """
    if priority not in PRIORITY_LABELS.values():
        return None
    lines = (markdown or "").split("\n")
    in_board = False
    for index, line in enumerate(lines):
        heading = _SECTION_RE.match(line)
        if heading:
            in_board = len(heading.group(1)) == 2 and heading.group(2).strip().lower() == "board"
            continue
        if not in_board or not line.strip().startswith("|"):
            continue
        masked = _WIKILINK_RE.sub(lambda m: m.group(0).replace("|", _ALIAS_PIPE), line.strip())
        cells = [
            cell.strip().replace(_ALIAS_PIPE, "|") for cell in masked.strip("|").split("|")
        ]
        if len(cells) < 4:
            continue
        found = _ROW_NUMBER_RE.search(cells[0])
        if not found or int(found.group(1)) != number:
            continue
        if status_key(cells[2]) in _CLOSED_STATUS_KEYS:
            return None
        # Appended, never inserted -- the same reason `parse_board` reads
        # it at index 4. A row that never had a fifth cell grows one here,
        # so an unrated file does not have to be migrated first.
        while len(cells) < 5:
            cells.append("")
        cells[4] = priority
        lines[index] = "| " + " | ".join(cells) + " |"
        return "\n".join(lines)
    return None


def set_row_status(markdown, number, status, updated=None):
    """Rewrite one row's status cell. Returns markdown, or `None`.

    The missing sibling of `set_row_priority`, and the reason it is here:
    Cycle 202 marked nine rows `⚫ Outdated` by splitting each row on `|`
    by hand, because nothing in this module set a status. The first column
    is a wiki-link containing an escaped `\\|`, so that split yields five
    cells where the table has four and shifts every column one to the
    right -- the file it was about to write had the row's *title* in the
    status column. It caught that by diffing before the write. The sweep
    of the remaining ~62 open rows is many more chances to make the same
    mistake, so the split moves in here where `_row_span` already masks
    that character.

    **A closed row loses its rating.** `set_row_priority` refuses a `done`
    or `outdated` row outright, so a status cell moving *into* one of them
    would otherwise strand a chip that no call could ever clear again --
    the two functions would disagree about whether a finished row can
    carry a rating. It cannot; Cycle 188 rated 71 open rows and
    deliberately left the finished ones blank.

    `updated` writes the fourth cell, and `None` leaves it alone. The
    caller passes it rather than this function reaching for a clock,
    because these files use `MM-DD` in Oslo time and a module that
    formats dates on its own is a module that formats them in UTC.

    `None` means not written: no such row in `## Board`, or a status that
    is not one of the four. A row in `## Done` is deliberately out of
    reach -- see `_row_span`, whose two tables put a date where this one
    writes a status.
    """
    if status not in STATUS_LABELS.values():
        return None
    # `status` is whitelisted above and cannot carry a delimiter; `updated`
    # is free text from a caller and can. `set_row_title` refuses the same
    # two characters for the same reason -- a `|` splits the row into an
    # extra column and a newline splits it into two rows, and both land in
    # Edvard's file looking like a table he wrote.
    if updated is not None and ("|" in updated or "\n" in updated):
        return None
    lines = (markdown or "").split("\n")
    index, cells = _row_span(lines, number, tables=("board",))
    if index is None:
        return None
    cells[2] = status
    if updated is not None:
        cells[3] = updated
    if status_key(status) in _CLOSED_STATUS_KEYS and len(cells) > 4:
        cells[4] = ""
    lines[index] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def _detail_spans(markdown):
    """`{number: (heading_line, body_start, end_line)}` for every write-up.

    Scanned line-wise instead of through `_sections`, because the two
    jobs genuinely differ. `_sections` exists to find `## Board` and
    `## Done`, so it stops at depth two -- and a detail heading may be
    depth three. Widening it to `#{1,3}` would have been the small change
    and it is the wrong one: three write-ups in the live `ideas.md` carry
    their own `### Where it lives` / `### The 20` / `### First slice`
    subheadings, and a section splitter that treats those as new sections
    would silently truncate the write-up at the first one.

    So a block ends at the *next item* heading, or at any `#`/`##`
    heading that is not one -- `# Details` itself, `## Board`, `## Done`.
    Anything deeper stays inside the body where its author put it.

    Returns line indices rather than text so the delete path and the
    read path address a block the same way; `parse_board` slices the
    body out of them.
    """
    lines = (markdown or "").split("\n")
    starts = []
    for index, line in enumerate(lines):
        match = _DETAIL_RE.match(line)
        if match:
            starts.append((index, int(match.group(3))))
            continue
        shallow = _SECTION_RE.match(line)
        if shallow:
            # A non-item heading at depth 1 or 2 closes whatever is open.
            starts.append((index, None))
    spans = {}
    for position, (index, number) in enumerate(starts):
        if number is None:
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        # A number written twice keeps the first block, the same way
        # `parse_board` keeps the first row for a number in both tables.
        spans.setdefault(number, (index, index + 1, end))
    return spans


def _row_span(lines, number, tables=("board", "done")):
    """`(index, cells)` for the `## Board` or `## Done` row numbered `number`.

    Unlike `set_row_priority` this does not care by default which of the
    two tables the row is in. Rating a finished item is meaningless, so
    that function refuses one; deleting a finished item is the *most*
    likely thing Edvard wants, since `## Done` is where the rows he has
    stopped caring about accumulate.

    `tables` narrows it, and `set_row_status` is why it exists. **The two
    tables do not share a column layout** -- `## Board` is
    `# | Item | Status | Updated | Priority` and `## Done` is
    `# | Item | Landed | Where`, so the third cell is a status in one and
    a date in the other. A status write addressed at a `## Done` row
    would land on the date, and `parse_board` derives that row's status
    from the table it is in, so it would keep reporting `✅ Done` and the
    corruption would be invisible on the page. Caught by a test written
    to check the opposite behaviour.
    """
    in_table = False
    for index, line in enumerate(lines):
        heading = _SECTION_RE.match(line)
        if heading:
            in_table = (len(heading.group(1)) == 2
                        and heading.group(2).strip().lower() in tables)
            continue
        if not in_table or not line.strip().startswith("|"):
            continue
        masked = _WIKILINK_RE.sub(lambda m: m.group(0).replace("|", _ALIAS_PIPE), line.strip())
        cells = [
            cell.strip().replace(_ALIAS_PIPE, "|") for cell in masked.strip("|").split("|")
        ]
        if len(cells) < 4:
            continue
        found = _ROW_NUMBER_RE.search(cells[0])
        if found and int(found.group(1)) == number:
            return index, cells
    return None, None


# `[[#84 — Edit and delete a boarded card\|84]]` -- Obsidian's link from a
# board row to its own write-up, and the only place a row's title appears
# twice. The escaped pipe is how it survives being a table cell.
_ROW_LINK_RE = re.compile(r"^\[\[#(\d+)[ \t]*[—–-][ \t]*(.*?)\\?\|(\d+)\]\]$")


def set_row_title(markdown, number, title):
    """Retitle one boarded row, in all three places at once. Or `None`.

    Edvard, issue #84: *"I need to be able to edit and especially delete
    boarded ideas and issues from the agora app."*

    **A title is written three times in these files and only one of them
    is on the page.** The table cell is what the board shows; the wiki-link
    beside it repeats the title *as the link target*, because Obsidian
    resolves a heading link by its text; and the `### #84 — ...` heading
    over the write-up is that target. Rewriting the cell alone would leave
    him a board that reads correctly in the app and, in Obsidian on his
    phone, a row whose link goes nowhere and a write-up still carrying the
    old title. That is a worse state than not offering the edit, so all
    three move together or the file is not touched.

    A row with no wiki-link and no write-up is still editable -- both
    repetitions are optional and only the cell is required.

    `None` means not written: no such row, or an empty title. An empty
    title is a delete, and `delete_row` is a separate call for the same
    reason `/api/capture/delete` is a separate route -- the destructive
    one should never be reachable by a field arriving blank.
    """
    title = (title or "").strip()
    if not title or "|" in title or "\n" in title:
        return None
    lines = (markdown or "").split("\n")
    index, cells = _row_span(lines, number)
    if index is None:
        return None

    link = _ROW_LINK_RE.match(cells[0])
    if link:
        cells[0] = f"[[#{number} — {title}\\|{link.group(3)}]]"
    cells[1] = title
    lines[index] = "| " + " | ".join(cells) + " |"

    span = _detail_spans(markdown).get(number)
    if span:
        heading_line = span[0]
        match = _DETAIL_RE.match(lines[heading_line])
        # The hashes and the `#` before the number are kept exactly as
        # they were found. Both shapes are live in his files and this is
        # an edit to one title, not a migration.
        lines[heading_line] = f"{match.group(1)} {match.group(2)}{number} — {title}"
    return "\n".join(lines)


def delete_row(markdown, number):
    """Remove one boarded row and its write-up. Returns markdown, or `None`.

    The other half of #84 -- *"and especially delete"* -- and the server
    path #85 needs for the same reason.

    **Both halves go, or neither.** A write-up whose row is gone is
    unreachable from the page and invisible in the board tables, so
    leaving it behind is not a conservative choice: it is an orphan that
    only shows up the next time a cycle renumbers something. The row is
    the thing Edvard is looking at when he asks for this, and the write-up
    is mine.

    The deletion is line-wise on the raw file, like `set_row_priority`,
    so everything else comes back byte-identical. `None` if the number is
    not on either table -- there is nothing to delete, which is a
    different answer to Edvard than a failed write.
    """
    lines = (markdown or "").split("\n")
    index, _ = _row_span(lines, number)
    if index is None:
        return None
    drop = {index}
    span = _detail_spans(markdown).get(number)
    if span:
        heading_line, _, end = span
        drop.update(range(heading_line, end))
    return "\n".join(line for i, line in enumerate(lines) if i not in drop)


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
        elif stripped and bullets and not stripped.startswith(("-", "*", "|")):
            # A capture that wrapped. `nova_capture.clean_capture_text`
            # splits a paste on newlines so this should not happen from
            # the box, but the same file is edited in Obsidian on a phone,
            # and half of Edvard's sentence going missing with no error is
            # the worst failure this page has.
            bullets[-1] = bullets[-1] + " " + stripped
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
                # Priority is a fifth column on `## Board`, appended
                # rather than inserted so every cell above keeps its
                # index and an unrated file still parses. `## Done` has
                # a different four-column shape and never carries one --
                # a finished item has no priority left to argue about.
                priority = cells[4] if (not done and len(cells) > 4) else ""
                items.append({
                    "number": number,
                    "title": cells[1],
                    "status": "✅ Done" if done else cells[2],
                    "statusKey": "done" if done else status_key(cells[2]),
                    "updated": cells[3] if not done else cells[2],
                    "where": cells[3] if done else "",
                    "priority": priority,
                    "priorityKey": priority_key(priority),
                    "done": done,
                })
            continue
    lines = (markdown or "").split("\n")
    for number, (_, body_start, end) in _detail_spans(markdown).items():
        details[number] = "\n".join(lines[body_start:end]).strip()
    return {"captures": captures, "items": items, "details": details}


def parse_notes(markdown):
    """One of my own capture files -> `[{date, cycle, text}]`, in file order.

    **File order is not reliably newest-first, and a reviewer caught this
    docstring claiming it was.** Measured against the live
    `nova/resources/issues.md` on 2026-08-11, and re-measured by Cycle 113
    when the first reading turned out to be off: 324 notes, the first 118
    descending from Cycle 112 to Cycle 24 and the remaining 206 *ascending*
    from Cycle 26 to Cycle 111. Two conventions, one file -- `vault_tool.py
    append` inserts under the `## Entries` marker when handed one and at
    the end of the file when not -- so the genuinely newest material is at
    both ends at once. `ideas.md` has the same break, at entry 92.

    Nothing is sorted here anyway, and that is deliberate: only 89 of the
    324 live notes carry a cycle marker at all, so a sort would rank a
    quarter of the file and dump the rest. The real fix is to normalise
    the file, which is `tools/normalise_captures.py` -- a one-time merge
    of the two streams, run against the vault rather than from here.
    Until that has been run this returns what the file says and the page does not claim an
    order it does not have -- see the pager's label in app.js.
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
