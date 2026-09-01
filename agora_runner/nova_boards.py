"""The backlog boards, parsed for the site: issues and ideas, his and mine.

The owner, issues.md #57: *"I need more visualisations in the Nova app.
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
# `DONE (Cycle 247): shipped in runner#228 — <the rest of his bullet>`.
# The shape `prompt.md` step 6 asks a cycle to write when its work closed
# one of the owner's captures; see `split_capture_done`. The colon is
# required so a bullet that merely opens with the word cannot match.
#
# **Anything else inside the parentheses is allowed, and that is a fix.**
# This required the bracket to hold `Cycle N` and nothing else. Cycle 337
# closed a capture and wrote `DONE (Cycle 337, platform-config#516):`,
# naming the PR where the reader would look for it -- the obvious thing to
# write, and nothing in `prompt.md` forbids it. It matched nothing, so at
# 09:05 on 2026-08-23 `tools/top_board_rows.py` printed that finished
# capture under *"these outrank every row below. Take one"*, and
# `roll_done_captures` would never have moved it out of the owner's file.
# That is the precise failure `split_capture_done` was written to prevent,
# walking back in through a comma. The cycle number is still the only
# thing captured, so every caller reads what it always read.
_CAPTURE_DONE_RE = re.compile(r"^DONE\s*\(\s*(Cycle\s*\d+)[^)]*\)\s*:", re.IGNORECASE)

#: `(Project: Marcus) rest` at the head of a capture. Bounded to 40
#: characters and to characters a `Project` cell may legally hold, so a
#: sentence that merely opens with a parenthesis can never be eaten.
_CAPTURE_PROJECT_RE = re.compile(
    r"^\(\s*Project\s*:\s*([^)|*\n]{1,40}?)\s*\)\s*", re.IGNORECASE
)
# A detail heading inside `# Details`, in either shape the live files use:
# `## 57 — More pages in the Nova app` and `### #84 — Edit and delete a
# boarded idea or issue by holding the card`.
#
# **The second shape was unreadable for twenty-one of the owner's rows.** This
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
# the owner's files: rewriting 87 headings to suit the parser is a large diff
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
# The owner, `issues.md` #85: *"Some of them are implemented and some of them
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
# spellings of one status. `🟢 Done` is live on **issue #3** and reduces
# to `done` here, so it is read correctly and rewritten to the ✅ spelling
# the other 84 rows use if anything ever sets it again. (This comment
# said #63 until the reviewer checked: #63 is a different row and is
# already `✅ Done`. A cycle sweeping the board would have gone looking
# for the stray spelling in the wrong place and left the real one alone.)
# The sixth status, and the only one that says *why* a row is not moving.
# Issue #94 is the case it was written for: the investigation finished on
# 2026-08-16, the owner approved the change on 08-17, and the remaining step is
# a click on a GitHub settings page no token in this loop can make. It stayed
# `🟡 In progress` because that was the closest thing available, so it kept
# winning `top_board_rows` on rating and age, and four cycles each spent a
# sentence explaining why they skipped it -- the same tax issue #73 paid
# until Cycle 259 hand-edited its rating down. Lowering a rating to move a
# row down the list is a lie about how much it matters; this says the true
# thing instead. It is deliberately **not** in `_CLOSED_STATUS_KEYS`: the row
# is open, keeps its rating, and comes straight back the moment he acts.
BLOCKED_STATUS = "⏸ Blocked on Edvard"

STATUS_LABELS = {
    "backlog": "⚪ Backlog",
    "in-progress": "🟡 In progress",
    "blocked-on-edvard": BLOCKED_STATUS,
    "done": "✅ Done",
    "outdated": OUTDATED_STATUS,
}

# The statuses that mean the row is finished with, either way. A finished
# row takes no rating: `set_row_priority` already refused `done` because a
# chip on a shipped item is noise, and "will never be built" is the same
# state for the same reason.
_CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})


# The owner's four ratings, and the words he actually used for them:
# "low, medium, high, immediately priority" (`ideas.md` capture,
# 2026-08-14). "immediately" is his word and "immediate" is the one a
# hand-edit is likely to reach for, so both land in the same bucket
# rather than one of them falling off the sort.
_PRIORITY_ALIASES = {"immediately": "immediate", "now": "immediate", "urgent": "immediate"}

# The exact cell text a rating is written as, keyed by what `priority_key`
# reduces it to. `""` clears the cell back to unrated, which has to stay
# reachable: Cycle 188 rated all 71 open rows itself, so every rating on
# both boards right now is mine, and "actually nobody has decided this"
# is an answer the owner must be able to give me back.
#
# **Glyph and word together, and the word is the part that is not
# optional.** Cycle 268 read the owner's *"Please do not use these symbols
# '🟠' as i can't really see the difference as they are colors. Please
# use the full word such as 'high' or 'immediately'"* as "delete the
# glyph", stripped it from these labels and from 87 cells in his two
# files, and got told the next morning that it had over-read him:
# *"There has been a missinderstanding here. I do like the symbols such
# as 🟠 for priority, so please add them back to where it was removed.
# What i ment was in your journals or other explanations, please use
# your words instead of only using the symbol ... But if you use the
# symbol and text, thats completely fine!"* (comments board 2026-08-20,
# said twice).
#
# So the defect was never the glyph. It was the glyph *alone*, in the
# two places no word was printed beside it -- the capture bullet's
# prefix and the capture box's closed picker -- where colour carried the
# whole meaning. Both of those now read `🟠 High`, which fixes what he
# could not read without throwing away what he could. The narrower rule
# that survives, and the one worth applying anywhere else: **a rating is
# allowed to show a glyph, and is never allowed to show only a glyph.**
#
# Both spellings still parse. `priority_key` strips non-word characters,
# so sorting and filtering never saw the difference; `parse_board`
# normalises the cell on the way out, so a row left in either spelling
# displays the current one. `tools/normalise_priority_labels.py` rewrites
# the cells already written into his two files, in whichever direction
# these labels currently point.
PRIORITY_LABELS = {
    "": "",
    "low": "⚪ Low",
    "medium": "🔵 Medium",
    "high": "🟠 High",
    "immediate": "🔴 Immediately",
}

# The glyph each rating carries, indexed the other way round so a bullet
# written as a bare glyph -- every capture the owner typed before Cycle 268,
# and every one his phone writes from an `app.js` cached before Cycle 274
# -- still parses back to its rating. Read by `split_capture_priority`;
# what gets *written* comes from `PRIORITY_LABELS` and always has the
# word in it.
_LEGACY_PRIORITY_GLYPHS = {
    "⚪": "low",
    "🔵": "medium",
    "🟠": "high",
    "🔴": "immediate",
}

# What a rating looks like riding at the front of a capture bullet:
# `🟠 High: fix the sort order`. The colon is load-bearing and the glyph
# is not -- a glyph could never begin an ordinary sentence, but "High"
# can, and `Immediately: ` is the only form that stays unambiguous
# against a bullet that happens to open with one of the four words. The
# glyph is therefore optional in what this matches and always present in
# what `capture()` writes, which is what lets a phone holding a cached
# `app.js` keep writing the wordless spelling without losing a rating.
CAPTURE_PRIORITY_SEP = ": "
_CAPTURE_WORD_RE = re.compile(
    r"^(?:[⚪🔵🟠🔴]\s*)?(Low|Medium|High|Immediately):\s+",
    re.UNICODE)


def canonical_priority(value):
    """A submitted rating -> the exact label to write, or `None` if unknown.

    `"🟠 High"`, `"High"` and `"high"` all give `"High"`; `""` gives `""`,
    which is the real "unrated" answer and not a rejection.

    The validators used to be `value in PRIORITY_LABELS.values()`, which
    was fine while that dict never changed. Cycle 268 changed it, and a
    phone holding a cached `app.js` still sends the coloured spelling --
    so an exact-match check would have answered 400 to every rating
    the owner set from the capture box until he happened to hard-reload,
    with nothing on screen to explain it. Matching on `priority_key`
    accepts the old vocabulary and stores the new one, which costs
    nothing and makes the rename invisible from his side.
    """
    if value is None:
        return ""
    if not str(value).strip():
        return ""
    return PRIORITY_LABELS.get(priority_key(value))


def priority_key(priority):
    """`🔴 Immediately` -> `immediate`, for a CSS class and a filter.

    Emoji-stripped and aliased the same way `status_key` is, and for the
    same reason: the rating is written by hand, by the owner, in Obsidian,
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
    """`🟠 High: text` -> `("🟠 High", "text")`. Unrated -> `("", bullet)`.

    The other half of the owner's capture: *"i want that aswell both when i
    input in the textbox in the Nova app"*. A capture is a bare bullet in
    his file and has nowhere to put a column, so the rating rides at the
    front of the bullet as the one glyph the rating already is -- which
    reads correctly in Obsidian, survives him editing the line by hand,
    and is what a cycle lifts into the `Priority` cell when it boards the
    item and strips from the title.

    Three accepted spellings, and only one of them is ever written.
    `🟠 High: ` is current (Cycle 274, `PRIORITY_LABELS`); `High: ` is
    what Cycle 268 wrote for a day and is still all over his two files;
    the bare `🟠` is every bullet captured before that, and is read for
    that reason alone. All three key to the same rating, so which one a
    bullet happens to carry never changes what it means.

    The word form needs its colon and the glyph form does not, which is
    the whole reason the separator exists. A glyph cannot begin an
    ordinary sentence, so a leading `🔴` is unambiguously a rating; the
    word "High" very much can, and `High: ` is what keeps a bullet
    opening "High memory use in the runner pod" from being read as a
    rating and silently losing its first word.
    """
    text = (bullet or "").strip()
    found = _CAPTURE_WORD_RE.match(text)
    if found:
        return PRIORITY_LABELS[priority_key(found.group(1))], text[found.end():].strip()
    for glyph, key in _LEGACY_PRIORITY_GLYPHS.items():
        if text.startswith(glyph):
            return PRIORITY_LABELS[key], text[len(glyph):].strip()
    return "", text


def split_capture_done(bullet):
    """`DONE (Cycle 247): shipped it — <his text>` -> `("Cycle 247", rest)`.

    Not done -> `("", bullet)`.

    `prompt.md` step 6 tells every cycle to edit a capture it closed so
    the bullet starts with `DONE (Cycle N):` -- that is the only mark
    these bullets carry, because a capture is a bare line in the owner's
    file with nowhere to put a status cell. Nothing read the mark. So a
    closed capture stayed, by every mechanism that looks at these files,
    exactly as unprocessed as one he typed a minute ago: at Cycle 251 all
    five captures on `issues.md` were finished work, and both consumers
    said so out loud -- `tools/top_board_rows.py` printed them under
    *"these outrank every row below. Take one"*, and the app listed them
    on his phone under *"Not boarded yet"*.

    That is issue #88's failure arriving from the other side. The point
    of putting his captures above the board was that they are the
    strongest signal a cycle gets; a section where every item is finished
    trains a cycle to skip the section.

    The marker is matched only as a prefix, for `split_capture_priority`'s
    reason -- the bullet is his text from that point on, and "DONE" in the
    middle of a sentence is prose. The cycle number is returned rather
    than dropped because it is the one thing that says *when* it closed.
    """
    match = _CAPTURE_DONE_RE.match((bullet or "").strip())
    if not match:
        return "", (bullet or "").strip()
    return match.group(1).strip(), match.string[match.end():].strip()


def split_capture_project(bullet):
    """`(Project: Marcus) text` -> `("Marcus", "text")`. Untagged -> `("", bullet)`.

    The third prefix a bare capture can carry, and the last one nothing
    read. The owner tags a bullet with the project it belongs to because
    a capture is one line in his file with nowhere to put a column --
    exactly the reasoning in `split_capture_priority` and
    `split_capture_done` -- and `board_capture` then has to lift it into
    the `Project` cell and strip it from the title, the same way it does
    the rating.

    It did not. Measured on his two boards, 2026-09-01: **38 rows** carry
    a literal `(Project: Marcus)` inside their title text with the
    `Project` cell left at the `Nova` default, so `board_projects` cannot
    see one of them and the app's Marcus overview listed the two rows
    whose cell happened to be filled by hand. He found it himself and
    filed it.

    Matched only as a prefix, for `split_capture_done`'s reason: the
    bullet is his prose from that point on. The name is bounded to the
    characters `set_row_project` will accept and to its 40-character
    limit, so anything this returns is a name that can actually be
    written into a cell -- a longer or `|`-carrying parenthetical is left
    in the title where it does no harm, rather than being lifted into a
    cell that would then be refused.
    """
    text = (bullet or "").strip()
    match = _CAPTURE_PROJECT_RE.match(text)
    if not match:
        return "", text
    return match.group(1).strip(), text[match.end():].strip()


def set_row_priority(markdown, number, priority):
    """Rewrite one `## Board` row's rating cell. Returns markdown, or `None`.

    The owner's capture, `issues.md` 2026-08-14: *"You made it possible for
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
    render, because these files are the owner's and everything I am not
    editing has to come back byte-identical -- his prose, his detail
    sections, the blank lines the two files disagree about. Only the one
    row is rebuilt, and only from cells that were already in it.
    """
    # Normalised rather than exact-matched, so the coloured spelling this
    # function accepted before Cycle 268 still writes -- and writes the
    # wordless one. A caller holding the old vocabulary (a cached
    # `app.js`, a cycle's own script) must not be silently refused; that
    # is the one failure `None` cannot distinguish itself out of, since
    # all three of its meanings are "the file was not touched".
    priority = canonical_priority(priority)
    if priority is None:
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
    # the owner's file looking like a table he wrote.
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


# Who a write-up note may be attributed to. A closed set, because the
# value is interpolated inside `**...**` in the owner's own file -- see
# `append_detail_note`. The key is lowercased so a route may pass either
# case; the value is what gets written.
NOTE_AUTHORS = {"nova": "Nova", "edvard": "Edvard"}


def append_detail_note(markdown, number, note, dated, cycle=None, author=None):
    """Add one dated line to the end of a row's write-up. Or `None`.

    The other side of issue #85. `set_row_status` moves a row to `✅ Done`
    and the *reason* it moved lands in a journal entry, in a different
    file, in a different database -- so the owner opens `issues.md` on his
    phone, sees a row that closed itself, and has nothing in front of him
    that says why. That is the same drift he filed #85 about, pointing the
    other way: last time the row was stale, this time the row is right and
    unaccountable. Cycle 203 closed ten rows and every one of them has this
    hole.

    So a status change gets a sentence written where the status is, and
    this is the call that writes it. The line goes at the *end* of the
    write-up body rather than the top: the write-up is the owner's statement
    of the problem and these are notes accumulating under it in order, so
    the newest-first convention his capture lists use is the wrong one
    here -- it would put a closing note above the problem it closed.

    `dated` is `MM-DD` in Oslo time and the caller supplies it, for
    `set_row_status`'s reason: a module that reaches for a clock reaches
    for it in UTC. `cycle` is optional and named in the line when given.

    **A line break in `note` is refused, and that is the whole safety
    argument.** `_detail_spans` ends a write-up at the next `#` or `##`
    heading, so a note carrying one would not merely look wrong -- it
    would truncate the block it was appended to, and every later line of
    the owner's own text would fall outside the span and stop rendering on
    the page. One line in, one line out.

    **`\\r` counts, and it is the one that gets past a `"\\n" in note`
    check.** Python's `re.MULTILINE` anchors on `\\n` alone, so a bare
    `\\r` does not split a span *here* and every server-side test agrees
    the note is harmless. It is not harmless where it lands: CommonMark
    defines a bare `\\r` as a line ending, so Obsidian on his phone
    renders it as a real break and puts whatever follows on its own line
    under his prose. That is the same corruption, arriving through the
    renderer instead of through the parser, and reachable only because
    this module and its reader disagree about what a line is.

    `author` is who the line is attributed to and defaults to me. Idea
    #64 is what needs the other value: *"Lets me have the same comment
    conversation on ideas, notes and issues like the Journal."* A comment
    from the owner is the same one-line append in the same place -- the
    write-up is already where he goes looking for commentary, and the
    board page already renders it -- so the thread is this call with a
    different name in front of the colon, not a second store.

    **It is checked against a fixed set rather than written through**,
    the same boundary `_post_priority` draws for a rating: the value ends
    up inside `**...**` in his own file, and free text there could close
    the emphasis and keep going. Two names are all this needs, and an
    unknown one is `None` rather than a silent fallback to mine --
    attributing his sentence to me is exactly the corruption worth
    refusing.

    **It also stamps the row's `Updated` cell with `dated`**, via
    `_touch_row_updated` -- read that docstring for why, and for why a
    missing row is not a refusal. The consequence for callers is that
    `dated` now reaches a table cell as well as the prose, so a `|` in it
    is refused alongside the line breaks.

    `None` means not written: no write-up for that number (the row may
    still exist -- only some rows have one), an empty note, a line break
    in either free-text argument, a `|` in `dated`, or an author who is
    not one of the two.
    """
    note = (note or "").strip()
    dated = (dated or "").strip()
    if not note or not dated:
        return None
    if any(c in note or c in dated for c in "\r\n"):
        return None
    # `dated` now lands in a table cell as well as in the prose, so it has
    # to survive being one. A `|` there splits the row into an extra
    # column and shifts `Priority` off the end -- the same corruption
    # `_row_span` masks the wiki-link's escaped pipe to avoid. The note
    # body is unaffected and keeps taking any `|` it likes.
    if "|" in dated:
        return None
    span = _detail_spans(markdown).get(number)
    if span is None:
        return None

    lines = (markdown or "").split("\n")
    _, body_start, end = span
    # Trailing blank lines inside the block are the separator before the
    # next heading, not part of the body. Walk back over them so note two
    # lands directly under note one instead of drifting a line further
    # from the write-up each time.
    tail = end
    while tail > body_start and not lines[tail - 1].strip():
        tail -= 1
    # `None` is "not specified" and means me. An empty or blank string is
    # not the same thing: it is a caller that meant to name someone and
    # sent nothing, which is almost always an unset payload field -- and
    # defaulting *that* to me is how the owner's sentence ends up signed with
    # my name, which is the one outcome this argument exists to prevent.
    name = NOTE_AUTHORS.get(("Nova" if author is None else author).strip().lower())
    if name is None:
        return None
    who = f"{name}, {dated}" if cycle is None else f"{name}, {dated} (Cycle {cycle})"
    entry = ["", f"**{who}:** {note}"]
    # An empty write-up has nothing to separate the note from, and a
    # leading blank line there would render as one.
    if tail == body_start:
        entry = entry[1:]
    return _touch_row_updated(
        "\n".join(lines[:tail] + entry + lines[tail:]), number, dated)


def _touch_row_updated(markdown, number, dated):
    """Set one `## Board` row's `Updated` cell to `dated`. Never fails.

    **The `Updated` cell is a sort key, and until now nothing kept it
    true.** `tools.top_board_rows` ranks the owner's two boards by rating and
    then by `age_key(updated)`, oldest first, so the cell decides which
    row a cycle is told to take. `append_detail_note` is the one call that
    always means the row was genuinely worked -- it is how a status change
    gets its reason, and how the owner's own comments land (`nova_capture`'s
    comment route) -- and it wrote the note into the write-up while
    leaving the cell alone.

    Measured on the live board, 2026-08-20: issue #7 was `Updated 08-16`
    and topped `top_board_rows` as the oldest `High` row, while its
    write-up already carried `**Nova, 08-20 (Cycle 270):**` from four
    hours earlier. The instrument that exists to stop a cycle picking the
    cheapest row was ranking on a date four days stale, on the row it
    named first. The owner filed the general version as issue #85 -- a row
    that does not say what happened to it -- and this is that same drift
    in the column that gets sorted.

    Both authors stamp it. The cell means "when did this row last change",
    not "when did I last work it", and a comment from the owner changes it as
    much as a note from me. That cannot bury an unanswered question: a row
    whose write-up ends on one of his notes is marked `UNANSWERED` by
    `top_board_rows` and outranks every rating, ahead of the age key
    entirely.

    **The `|` refusal in `append_detail_note` makes `dated` safe for a
    table cell; it does not make it a date.** `age_key` falls back to
    `"99-99"` for anything that is not `MM-DD`, which sorts as the
    *newest* row -- so a malformed date would sink the row rather than
    raise. There is no shape check here because there is already one that
    matters more: `_COMMENT_NOTE_RE` requires `\\d{2}-\\d{2}` to read a
    note back, so a date the sort key cannot parse is a note the page
    cannot see, and that fails loudly on the owner's screen rather than
    quietly in a ranking. Reviewer finding on the PR that added this,
    recorded rather than coded.

    Never fails, and that is deliberate -- the note is the caller's
    request and the stamp is bookkeeping on top of it. Only some detail
    write-ups have a board row at all (`append_detail_note`'s own
    docstring says so), and refusing the whole append because the row is
    missing would lose the owner's sentence to fix a sort order. A `## Done`
    row is out of reach for `set_row_status`'s reason -- the two tables do
    not share a column layout and the fourth cell there is `Where`, a list
    of PRs, not a date.
    """
    lines = (markdown or "").split("\n")
    index, cells = _row_span(lines, number, tables=("board",))
    if index is None:
        return markdown
    cells[3] = dated
    lines[index] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


# One dated note, as `append_detail_note` writes it: `**Edvard, 08-15:** ...`  (not-prose: quoting a literal)
# or `**Nova, 08-15 (Cycle 221):** ...`. Anchored at the start of the line
# and built from `NOTE_AUTHORS` rather than a typed-out alternation, so the
# day a third author is allowed this matcher learns about it instead of
# quietly reading that author's lines as prose.
#
# **The `MM-DD` is required, and that is what keeps prose out.** Without it
# the pattern is "his name, a comma, anything, a colon", which a sentence
# like `**Edvard, in his own words:**` in a write-up satisfies -- and a  (not-prose: quoting a literal)
# false positive here is a row that claims to be waiting on a reply
# forever, since no reply of mine can clear a note that was never a note.
# `append_detail_note` refuses an empty `dated`, so every real note has one
# and requiring it costs nothing.
#
# **The year is optional because the live files have both shapes.** `dated`
# is whatever the caller passed, and 7 of the 83 notes in the live
# `issues.md` are `2026-08-15` rather than `08-15` -- so a bare `\d{2}-\d{2}`
# fails at a fixed offset on those (it eats `20`, then wants `-` and finds
# `2`) and reads a real note as prose. Rows #81, #87, #90 and #91 are
# written that way throughout, which means this matcher has been blind to
# their entire threads. Found by review on runner#402, measured against the
# vault before the pattern was widened.
_NOTE_STAMP = r"(?:\d{4}-)?\d{2}-\d{2}"

_COMMENT_NOTE_RE = re.compile(
    r"^\*\*(" + "|".join(sorted(NOTE_AUTHORS.values())) + r"), " + _NOTE_STAMP + r"[^:*]*:\*\*",
    re.MULTILINE,
)


# The same shape as `_COMMENT_NOTE_RE` above, with the stamp captured so the
# page can print it. One `_NOTE_STAMP` between them rather than two copies of
# the date pattern: they are one definition of what a note is, and the review
# that found the missing year found it in the copy, which is the argument.
_COMMENT_SPLIT_RE = re.compile(
    r"^\*\*(" + "|".join(sorted(NOTE_AUTHORS.values()))
    + r"), (" + _NOTE_STAMP + r"[^:*]*):\*\*[ \t]?",
    re.MULTILINE,
)


def split_detail_conversation(body):
    """One write-up -> `(prose, [{"author", "stamp", "text"}])`.

    The owner, `issues.md` capture 2026-08-26: *"i see that boarded issues
    does not have those nice colored comments like there are now in the 'not
    boarded yet' box, so take the best from both worlds here."*

    A board comment is appended into the row's own write-up as a
    `**<author>, 08-26:**` line (`append_detail_note`), so the page has been
    drawing his question and my answer as two more paragraphs of the same
    prose block -- indistinguishable from the problem statement above them
    and from each other. The capture box and the notes page both draw the
    identical exchange as green and purple bubbles. This is the split that
    lets a boarded row do the same, and it changes nothing in the file: the
    markdown he reads in Obsidian is untouched, this is only how the page
    reads it back.

    Everything before the first note marker is the write-up proper. From
    that marker on, every marker starts a message that runs to the next one.
    That is deliberately the same positional rule
    `unanswered_comment_bodies` uses to decide whether a row is waiting on
    me -- one reading of what a note is, not two -- and it means a marker
    written *inside* the problem statement pulls the rest of the statement
    into a bubble. That is one honest failure: such a line is a note by
    every other definition in this module, and inventing a second, looser
    one here is how the two halves drift apart.

    **The other one is measured rather than hypothetical, and it is not
    fixed here.** Two hand-written shapes in the live `issues.md` are real
    notes that neither this nor `_COMMENT_NOTE_RE` reads as one: a stamp
    that does not open with the date (`**<author>, capture 2026-08-20:**`,
    row 96) and a headline note whose bold run carries the whole sentence
    and ends in a full stop rather than a colon (`**<author>, 08-20 (Cycle
    269) - closed on your word.**`, rows 4, 59 and 95). Five lines across
    four rows, counted against the file. Loosening the terminator to catch
    them means letting a bold run end anywhere, which is exactly the prose
    that `[^:*]*` is here to keep out -- so it is a boundary to move
    deliberately with its own measurement, not a character to add at the
    end of the cycle that widened the year.
    """
    text = body or ""
    found = list(_COMMENT_SPLIT_RE.finditer(text))
    if not found:
        return text.strip(), []
    prose = text[: found[0].start()].strip()
    messages = []
    for position, match in enumerate(found):
        end = found[position + 1].start() if position + 1 < len(found) else len(text)
        messages.append({
            "author": match.group(1),
            # `08-26` or `08-26 (Cycle 462)` -- whatever the author wrote,
            # not re-derived. `append_detail_note` owns that shape.
            "stamp": match.group(2).strip(),
            "text": text[match.end():end].strip(),
        })
    return prose, messages


def unanswered_comments(markdown):
    """Row numbers whose write-up ends on a comment from the owner.

    Descending row number, which is deterministic and nothing more. It
    said "newest first" until a reviewer pointed out that row 4 can carry
    today's freshest comment and row 99 one from last week, and that the
    only caller wraps this in a `set()` anyway -- an ordering claim that
    is both wrong and unused is worse than no ordering claim.

    Idea #64 gave him a comment box on every boarded row (Cycle 219), and
    the design call that made it cheap is the one that left this hole: a
    board comment lands inline in the row's own write-up rather than in a
    queue, so unlike `comments.md` there is no `## New` for a cycle to
    drain. `nova_capture.comment_on_row` says what a cycle owes it -- *"a
    reply on the next line"* -- and nothing anywhere says which rows are
    still waiting for one. He commented, and the next cycle read the same
    52 open rows it always reads.

    So: a row is waiting when the **last** note under it is his. That is
    the whole rule, and it is deliberately positional rather than a count
    of his notes against mine. A thread that ran the owner → Nova → the owner
    is waiting even though both have spoken twice; a thread that ended on
    my reply is not, however much he said before it. Counting would call
    the first one answered.

    Prose between the notes is ignored -- the write-up *is* prose, his
    statement of the problem, and only the `**Author, date:**` lines are
    part of the conversation.

    **This sees the comment box, not the file.** Only the shape
    `append_detail_note` writes counts, so a comment the owner types straight
    into `issues.md` in Obsidian is invisible here -- and he does that:
    `## 59` in the live file already carries `- comment from the owner: i now
    see that this link only appears when...`, written by hand before the
    comment box existed (reviewer, PR #212). The bad case is not the
    missed flag, it is that a hand-typed question landing *after* one of
    my notes leaves the row reading as answered. Recognising free text as
    a comment is the wrong fix -- his whole write-up is free text, so
    anything loose enough to catch that would flag every row he has ever
    described a problem on. The real answer is for the page to write his
    hand-edits through the same call, and it is filed rather than guessed
    at here.
    """
    return sorted(unanswered_comment_bodies(markdown), reverse=True)


_RELAY_RE = re.compile(
    r"posting\s+(?:on|as)\s+(?:Edvard'?s?|his)\s+behalf"
    r"|on\s+Edvard'?s?\s+behalf"
    r"|not\s+Edvard\s+typing\s+this\s+himself",
    re.IGNORECASE,
)

# How far into a comment the disclosure has to sit to count. The cap is
# what stops a comment that merely *talks about* the relay pattern from
# being read as one -- these boards discuss this mechanism at length, and
# the write-ups are long.
#
# Measured 2026-08-29 across both live board files: 351 author notes, of
# which 4 match, every one at offset 42 -- immediately after its own
# `**<owner>, 08-29:**` marker, in the opening clause. Nothing in the
# corpus matches later than that, so 300 is chosen with room rather than
# fitted to the data, and today's negative is a real negative rather than
# one the window guaranteed.
RELAY_WINDOW = 300


def is_relayed(text):
    """Does this text say, in its own opening, that it is a relay?

    Sokrates -- the Claude Code session the owner works with directly --
    posts to these boards through the same `POST /api/board/comment` route
    a cycle uses, and that route takes `author` as free text with nothing
    behind it. So a comment Sokrates writes on the owner's behalf arrives
    signed with the owner's name, and everything downstream treats it as
    the owner typing. Sokrates has been compensating by hand, opening each
    one with a sentence disclosing that Claude is posting on the owner's
    behalf and that the owner is not typing it himself -- which is honest,
    and is the only signal that exists.

    His ask, relayed on `issues.md` 2026-08-29: *"a Sokrates comment
    relaying something [the owner] actually said should not automatically
    inherit the same 'unread comment from [the owner] jumps the queue, act
    now' treatment a comment genuinely typed by him gets, even when
    accurately relaying him. Sokrates being right about what [the owner]
    wants is not the same guarantee as [the owner] having typed it
    himself."*

    **This is self-declared and proves nothing, and that is fine here
    because of which way it points.** Reading the disclosure can only ever
    *lower* the priority of the text carrying it, never raise it -- so a
    forged disclosure demotes the forger, and the honest failure mode is a
    relay that omits the sentence and keeps the owner's priority, which is
    exactly today's behaviour. That asymmetry is why this half of the ask
    ships without waiting on the authentication half (my issue #15): an
    unauthenticated signal is safe to act on when acting on it costs the
    claimant something.

    It is deliberately not a general "who wrote this" answer. It says one
    thing -- the text announces itself as relayed -- and a caller wanting
    identity should wait for the auth work rather than read this as it.
    """
    return bool(_RELAY_RE.search((text or "")[:RELAY_WINDOW]))


def unanswered_comment_bodies(markdown):
    """`{number: his last unanswered comment, verbatim}` for every waiting row.

    Same rule as `unanswered_comments` -- this is where it is actually
    decided, and that function is now the numbers-only view of it.

    The text is what a *reply claim* is named after. A row slug cannot do
    that job: `issue-7` is claimed once and, once released, is finished
    for good, so the second question the owner ever asks on a row could never
    be claimed by anybody. A comment is the unit of work here, not the
    row, so the identity has to come from the comment.

    The body runs from the `**Edvard, MM-DD:**` marker to the end of the  (not-prose: quoting a literal)
    write-up rather than to the end of its first line. Two comments he
    leaves on one row on one day are distinguished by their text and
    nothing else, and a long comment's first line is very often just the
    opening clause -- which is exactly when two of them look identical.
    """
    bodies = {}
    lines = (markdown or "").split("\n")
    for number, span in _detail_spans(markdown or "").items():
        _, body_start, end = span
        block = "\n".join(lines[body_start:end])
        found = list(_COMMENT_NOTE_RE.finditer(block))
        if found and found[-1].group(1) == "Edvard":
            bodies[number] = block[found[-1].start():].strip()
    return bodies


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
    likely thing the owner wants, since `## Done` is where the rows he has
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

    The owner, issue #84: *"I need to be able to edit and especially delete
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


def next_row_number(markdown):
    """The lowest number no row and no write-up is already using.

    Read from all three places a number can live -- both tables and the
    `# Details` headings -- rather than from `## Board` alone. A row the
    owner finished is in `## Done` and its number is still spoken for by
    every journal entry and claim slug that ever pointed at it, and a
    write-up whose row was deleted by hand still owns its heading. Taking
    the highest of the three and adding one is the only allocation that
    cannot hand out a number twice; picking the first *gap* would reuse a
    deleted row's number, which is worse than a sparse sequence.
    """
    highest = 0
    lines = (markdown or "").split("\n")
    in_table = False
    for line in lines:
        heading = _SECTION_RE.match(line)
        if heading:
            in_table = (len(heading.group(1)) == 2
                        and heading.group(2).strip().lower() in ("board", "done"))
            continue
        if not in_table or not line.strip().startswith("|"):
            continue
        masked = _WIKILINK_RE.sub(lambda m: m.group(0).replace("|", _ALIAS_PIPE), line.strip())
        cells = masked.strip("|").split("|")
        found = _ROW_NUMBER_RE.search(cells[0].replace(_ALIAS_PIPE, "|"))
        if found:
            highest = max(highest, int(found.group(1)))
    for number in _detail_spans(markdown):
        highest = max(highest, number)
    return highest + 1


def _board_insert_line(lines):
    """Index to insert a new `## Board` row at -- under the header rule."""
    in_table = False
    seen_pipe = False
    for index, line in enumerate(lines):
        heading = _SECTION_RE.match(line)
        if heading:
            if in_table:
                return None
            in_table = (len(heading.group(1)) == 2
                        and heading.group(2).strip().lower() == "board")
            continue
        if not in_table:
            continue
        if line.strip().startswith("|"):
            seen_pipe = True
            # The header row, then the `|---|` rule, then the data. A new
            # row goes directly under the rule so the newest is first,
            # which is the order every one of these tables already reads in.
            if set(line.strip()) <= set("|-: \t"):
                return index + 1
        elif seen_pipe:
            return None
    return None


def add_row(markdown, title, dated, priority="", write_up="", notes=()):
    """Board a new row. Returns `(markdown, number)`, or `(None, None)`.

    The owner, capture 2026-08-26: *"Whats with the not boarded
    ideas/issues? ... they do no seem to just stay forever in the 'not
    boarded yet' box as unrated. Thats not what the box is for. This a re
    ideas you have not seen before and you pick it up, prioritised them
    and make them as their own nice item like the rest."*

    Nothing in this repo could write a row until now -- `set_row_title`,
    `set_row_priority`, `set_row_status` and `delete_row` all edit a row
    that already exists, and the only way one ever got created was a
    cycle hand-editing his file through the vault. So the box filled up,
    because answering a capture where it stands is one call and boarding
    it was a manual edit nobody reached for. Eight cycles in a row chose
    the cheap correct thing; that is a missing button, not a habit.

    **A title is one line and his capture is a paragraph, so the two are
    not the same string.** The title is his first sentence and the
    write-up is everything he wrote, verbatim -- the row has to be
    readable in a table cell that repeats it three times, and none of his
    text may be lost to make that true. No character count is involved:
    if his first sentence is long, it goes in long.

    `notes` are lines already written under the capture -- a cycle's
    answers -- and they ride across as dated notes so the thread survives
    the promotion. Each must be a single line, which they are: a reply is
    one indented bullet in his file.
    """
    title = (title or "").strip()
    if not title or "|" in title or "\n" in title:
        return None, None
    label = canonical_priority(priority)
    if label is None:
        return None, None
    lines = (markdown or "").split("\n")
    at = _board_insert_line(lines)
    if at is None:
        return None, None
    number = next_row_number(markdown)
    cells = [f"[[#{number} — {title}\\|{number}]]", title,
             STATUS_LABELS["backlog"], dated, label]
    lines.insert(at, "| " + " | ".join(cells) + " |")

    block = [f"### #{number} — {title}", ""]
    body = (write_up or "").strip()
    if body:
        block.extend(body.split("\n") + [""])
    for note in notes:
        one = " ".join(str(note).split())
        if one:
            block.extend([f"**Nova, {dated}:** {one}", ""])
    detail = _details_insert_line(lines)
    if detail is None:
        # No `# Details` section to put it under. The row is still a row
        # and losing it to a missing heading would be worse than a board
        # entry with no write-up, which is a state his files already have.
        return "\n".join(lines), number
    lines[detail:detail] = block
    return "\n".join(lines), number


def _details_insert_line(lines):
    """Index of the first line under `# Details`, or `None`."""
    for index, line in enumerate(lines):
        heading = _SECTION_RE.match(line)
        if heading and len(heading.group(1)) == 1 and heading.group(2).strip().lower() == "details":
            after = index + 1
            while after < len(lines) and not lines[after].strip():
                after += 1
            return after
    return None


def delete_row(markdown, number):
    """Remove one boarded row and its write-up. Returns markdown, or `None`.

    The other half of #84 -- *"and especially delete"* -- and the server
    path #85 needs for the same reason.

    **Both halves go, or neither.** A write-up whose row is gone is
    unreachable from the page and invisible in the board tables, so
    leaving it behind is not a conservative choice: it is an orphan that
    only shows up the next time a cycle renumbers something. The row is
    the thing the owner is looking at when he asks for this, and the write-up
    is mine.

    The deletion is line-wise on the raw file, like `set_row_priority`,
    so everything else comes back byte-identical. `None` if the number is
    not on either table -- there is nothing to delete, which is a
    different answer to the owner than a failed write.
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


def extract_row(markdown, number):
    """The raw text `delete_row` is about to remove, or `None`.

    The owner, capture 2026-08-22: *"Maybe the delete function should tell
    your next cycle that i have deleted it just in case some work was
    being done or just to keep it as a deleted issue for future
    reference."*

    Deliberately the same two spans `delete_row` drops, read through the
    same two helpers -- if the two ever disagree about what a row *is*,
    the archive is a record of something other than what was deleted, and
    a record you cannot trust is worse than none. Text rather than line
    indices, because the caller writes it into a different file where the
    indices mean nothing.
    """
    lines = (markdown or "").split("\n")
    index, _ = _row_span(lines, number)
    if index is None:
        return None
    parts = [lines[index]]
    span = _detail_spans(markdown).get(number)
    if span:
        heading_line, _, end = span
        parts.extend(lines[heading_line:end])
    return "\n".join(parts).rstrip()


def _sections(markdown):
    """`[(level, title, body)]` for every `#`/`##` heading, in order."""
    found = []
    matches = list(_SECTION_RE.finditer(markdown or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        found.append((len(match.group(1)), match.group(2), markdown[match.end():end]))
    return found


def _frontmatter_end(lines):
    """Index of the first line *after* the closing `---`, or 0."""
    if not lines or lines[0].strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i + 1
    return 0


def capture_entries(markdown):
    """`[(start_line, end_line, text, replies)]` for the capture list.

    The one parser for "what is a capture", and it lives here rather than
    in `nova_capture` because that module already imports this one and the
    reverse would be a cycle. It used to live in both: `nova_capture`
    split the owner's sentence from the replies written under it, `_captures`
    below folded them into one string, and the page was built from the
    folding while the write was checked against the split. That is what
    made Edit fail on an answered capture -- the address the page sent was
    his line with my answer welded on, which matches no capture, so the
    route answered "no longer in the list" and he lost the edit he had
    just typed (his `issues.md` capture, 2026-08-25, with a screenshot).

    `text` is his words alone. `replies` are the indented bullets under
    it, in order, each one a cycle answering him. A span rather than a
    line number because one capture can be several lines: a continuation
    line is folded into whatever it continues, since the same files are
    edited in Obsidian on a phone and half a sentence going missing is
    the worst failure this page has.
    """
    lines = (markdown or "").split("\n")
    start = _frontmatter_end(lines)
    entries = []
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("- ") and stripped[2:].strip():
            if entries and lines[i][:1].isspace():
                # An *indented* bullet is a reply written under the capture
                # above it, not something the owner just typed. Reading it
                # as its own capture is what put a cycle's own closing note
                # at the top of his `issues.md` and ranked it first on every
                # cycle's board ranking -- see `roll_done_captures.plan`,
                # which had the same blind spot and orphaned it there.
                begin, _, text, replies = entries[-1]
                entries[-1] = (begin, i + 1, text, replies + [stripped[2:].strip()])
            else:
                entries.append((i, i + 1, stripped[2:].strip(), []))
        elif stripped and entries and not stripped.startswith(("-", "*", "|")):
            begin, _, text, replies = entries[-1]
            if replies:
                entries[-1] = (begin, i + 1, text, replies[:-1] + [replies[-1] + " " + stripped])
            else:
                entries[-1] = (begin, i + 1, text + " " + stripped, replies)
    return entries


def _captures(markdown):
    """The bare bullets above the first heading -- the owner writing directly.

    `([text], [[reply]])`, the two lists parallel and the same length.
    These are the strongest signal a cycle gets and the page should show
    them as unboarded rather than hiding them until a cycle files them.
    """
    entries = capture_entries(markdown)
    return [text for _, _, text, _ in entries], [replies for _, _, _, replies in entries]


def parse_board(markdown):
    """One of the owner's board files -> its captures, rows and detail bodies.

    Returns `{"captures": [...], "captureReplies": [[...]], "items": [...],
    "details": {n: markdown}}`. `captures` is the owner's own text and
    `captureReplies` is parallel to it, holding the cycle replies written
    under each bullet.
    An item is `{number, title, status, statusKey, updated, where, done}`;
    `where` is only ever set from the `## Done` table's fourth column,
    which names the PRs a thing landed in.
    """
    captures, capture_replies = _captures(markdown)
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
                # Normalised on the way out, so a cell still spelled the
                # old way renders as the word. Reviewer finding on #244,
                # and the module comment above was wrong until this line
                # existed: `priority_key` reducing `🟠 High` to `high` is
                # enough for sorting and filtering and does nothing for
                # *display*. `app.js` puts this exact string in the chip
                # and looks it up in its own `PRIORITIES` array to pick
                # the colour class, so an un-normalised legacy value came
                # out as the glyph the owner cannot read, wearing a dead
                # `prio-` class with no colour at all -- strictly worse
                # than before the rename, on every row nobody re-saved.
                priority = canonical_priority(priority) or priority
                # `Project` is a sixth column, appended for the same
                # reason `Priority` was a fifth: every cell above keeps
                # its index, and both live files parse unchanged on the
                # day this ships without a single row being rewritten.
                # An absent or empty cell means `DEFAULT_PROJECT` rather
                # than "no project" -- every row on both boards today
                # predates the column and every one of them is Nova, so
                # a blank is a row nobody has re-filed, not a row that
                # belongs nowhere. `## Done` has its own four-column
                # shape and never carries one.
                project = cells[5] if (not done and len(cells) > 5) else ""
                project = project.strip() or DEFAULT_PROJECT
                items.append({
                    "number": number,
                    "title": cells[1],
                    "status": "✅ Done" if done else cells[2],
                    "statusKey": "done" if done else status_key(cells[2]),
                    "updated": cells[3] if not done else cells[2],
                    "where": cells[3] if done else "",
                    "priority": priority,
                    "priorityKey": priority_key(priority),
                    "project": project,
                    "done": done,
                })
            continue
    lines = (markdown or "").split("\n")
    for number, (_, body_start, end) in _detail_spans(markdown).items():
        details[number] = "\n".join(lines[body_start:end]).strip()
    return {
        "captures": captures,
        # Parallel to `captures`, one list of my answers per bullet of
        # his. Kept apart from his text on purpose: everything that
        # *writes* to a capture addresses it by his sentence, so a
        # payload that hands the page the two glued together hands it
        # an address that cannot match.
        "captureReplies": capture_replies,
        "items": items,
        "details": details,
    }


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


#: What a `## Board` row means when its `Project` cell is empty or absent.
#: Every row on both live files predates the column, and every one of them
#: is this project -- so a blank is "nobody has re-filed this yet", not
#: "belongs to nothing". Reading it as the latter would put 300 rows into
#: an "unassigned" bucket the owner never asked for and would have to
#: empty by hand.
DEFAULT_PROJECT = "Nova"

#: The heading text of the sixth column. Appended to whatever header the
#: file already has rather than written as a full row of six: the two
#: boards do not agree on their own second column (`issues.md` says
#: `Item`, `ideas.md` says `Idea`) and rewriting the header wholesale
#: renamed his column while adding mine. Caught before it shipped by
#: diffing a real write against the live `ideas.md`.
_PROJECT_HEADING = "Project"

#: How wide a `## Board` row is once it carries a project.
_BOARD_WIDTH = 6


def board_projects(items):
    """Every project named on the board, in the order it first appears.

    Derived from the rows rather than from a constant or a second
    document, which is the one design decision in this slice worth
    stating: the plan in `resources/ideas/project-dashboard-and-idea-pool.md`
    says the project list "should come from a document rather than a
    constant, because he will add one" -- and the board *is* that
    document. Typing a new name into a `Project` cell adds a project;
    nothing else has to be edited, and there is no second list that can
    disagree with the rows.
    """
    seen = []
    for item in items or []:
        name = (item.get("project") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _ensure_project_column(lines, index):
    """Give the `## Board` table a sixth column if it has only five.

    A six-cell data row under a five-cell header is not a rendering
    detail -- Obsidian drops the extra cell outright, so the value the
    owner is looking at would be gone from his screen while `parse_board`
    kept reporting it. `index` is a known data row, so the header and the
    `|---|` rule are the two table lines above it that this has to reach;
    they are found by walking back to the rule rather than by a fixed
    offset, because `## Board` is not always the first table in the file.
    """
    rule = None
    for position in range(index - 1, -1, -1):
        text = lines[position].strip()
        if not text.startswith("|"):
            break
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if all(set(cell) <= set("-: ") and cell for cell in cells):
            rule = position
            break
    if rule is None or rule == 0:
        return
    header = [cell.strip() for cell in lines[rule - 1].strip().strip("|").split("|")]
    if len(header) >= _BOARD_WIDTH:
        return
    dashes = [cell.strip() for cell in lines[rule].strip().strip("|").split("|")]
    while len(header) < _BOARD_WIDTH:
        header.append(_PROJECT_HEADING if len(header) == _BOARD_WIDTH - 1 else "")
    while len(dashes) < _BOARD_WIDTH:
        dashes.append("---")
    lines[rule - 1] = "| " + " | ".join(header) + " |"
    lines[rule] = "|" + "|".join(dashes) + "|"


def set_row_project(markdown, number, project):
    """Set one `## Board` row's `Project` cell. `None` means not written.

    Refused rather than written: a `|`, which would split the row into a
    seventh column; a line break, which would end the table; and a `*`,
    which is `set_row_priority`'s boundary for the same reason -- these
    cells sit inside the owner's own file and unbalanced emphasis there
    does not stop at the cell.

    **Any other name is accepted.** There is no allowed-projects list to
    check against, on purpose: `board_projects` reads the names back off
    the rows, so the set of projects is whatever the rows say it is and a
    new one costs one cell. A fixed list would be the constant the plan
    for this explicitly ruled out.

    A `## Done` row is out of reach, the same boundary `set_row_status`
    draws: the two tables do not share a column layout and a sixth cell
    there would land past `Where` in a four-column table.
    """
    name = (project or "").strip()
    if not name or len(name) > 40:
        return None
    if any(c in name for c in "|*\r\n"):
        return None
    lines = (markdown or "").split("\n")
    index, cells = _row_span(lines, number, tables=("board",))
    if index is None:
        return None
    # A row that predates the column is short rather than wrong, so it is
    # padded up to the new width instead of refused -- that is the whole
    # point of appending the column rather than inserting it.
    while len(cells) < 6:
        cells.append("")
    cells[5] = name
    lines[index] = "| " + " | ".join(cells) + " |"
    _ensure_project_column(lines, index)
    return "\n".join(lines)
