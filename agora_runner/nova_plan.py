"""The `/plan` page: what Nova would do next, and what any of it is for.

The owner, `issues.md` #7: *"Need evolve to think like a product manager.
Both so it becomes better, but also so i learn and get experience from it
since my dream is to become the worlds best platform product manager."*
Cycle 226 answered the first half by writing `roadmap.md` -- the
prioritised order and the reasoning behind it -- and Cycle 229 added
`goals.md`, the slate of proposed goals and the weekly review against
them. Both were written so he could argue with the reasoning instead of
only the result.

**Neither of them has ever reached his phone.** They live in his vault
and nowhere else, so the one document whose entire purpose is being
argued with is the one he has to open Obsidian to read. `goals.md`'s own
G2 measure names it: *"the number of things you still have to leave the
Nova app to do ... reading the roadmap. Four."* This module is that one
crossed off.

Nothing here does I/O. The two documents arrive as text and leave as a
payload, the same split every other page on this server follows --
`nova_sources.plan_markdown` fetches, this shapes.

**Why the sections are discovered rather than named.** Every other page
here parses a file with a contract: the digest has `## Next cycle`, a
board has `## Board`, the retro ledger has a validator that refuses a row
of the wrong shape. These two have no contract and should not get one.
They are Nova's own prose, restructured by whichever cycle last had
something new to say -- `roadmap.md` has changed its section list twice
already -- and a parser that named the sections it expected would render
a stale page the first time a cycle reorganised its own argument, without
saying so. So `md_sections.outline` takes whatever headings are there.
The page can be wrong about the styling of a section it has never seen;
it cannot silently drop one.

**The one exception, and it is built to keep that rule rather than bend
it.** The owner, 2026-08-20: *"It is just a huge wall of text. I hate that
... i understand visuals much faster."* `/plan` is 4,961 words on one
route with no number pulled out anywhere, so `goals.md` may now carry an
optional fenced ```goal block per goal and `roadmap.md` a ```next block per
item of its ranked five, and this module draws a scoreboard and a ranked
strip from them. The blocks are data a cycle writes for this page; every
other word in both documents is still prose nothing parses. See `_fenced`.
"""

import re

from agora_runner.md_sections import outline
from agora_runner.nova_goal_history import GoalHistoryError, goal_key, series
from agora_runner.nova_journal import render_blocks

ROADMAP_PATH = "projects/sokrates/projects/nova/roadmap.md"
GOALS_PATH = "projects/sokrates/projects/nova/goals.md"

# Order is the reading order he asked for, and it is deliberately goals
# last. `roadmap.md` answers "what next", which is the question he opens
# the page with; `goals.md` answers "what for", which is the one he only
# asks when he disagrees with the first answer.
PLAN_DOCUMENTS = (
    ("roadmap", "Roadmap", ROADMAP_PATH),
    ("goals", "Goals", GOALS_PATH),
)

_UPDATED_RE = re.compile(r"^updated:[ \t]*(?P<value>.+?)[ \t]*$", re.MULTILINE)

# The fences these two documents may carry: ```goal for a scoreboard row and
# ```next for one item of the roadmap's ranked strip. See `_scoreboard` for
# why a fence and not a regex over prose. Everything else in both files is
# prose nothing parses, and that is the rule these two are the exception to.
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*```[ \t]*$")
_FIELD_RE = re.compile(r"^(?P<key>[a-z]+):[ \t]*(?P<value>.*?)[ \t]*$")


def _fence_open_re(name):
    return re.compile(r"^[ \t]*```[ \t]*" + re.escape(name) + r"[ \t]*$")

# Which way is better. Anything else -- including a missing line -- means
# the goal has a number worth showing and no opinion about which
# direction is good, so the row prints the number and no verdict.
_DIRECTIONS = ("up", "down")

# Every field the block understands. A key outside this set is kept out of
# the payload rather than passed through: the page can only render what it
# has a row for, and a silently-ignored `targt:` typo is a goal that shows
# no target for a week before anybody notices.
_FIELDS = ("name", "measure", "now", "target", "unit", "direction")

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def _number(text):
    """`"2.8"` -> `2.8`; anything else -> `None`.

    Deliberately strict. `now: about 2.8` is a legitimate thing for a
    cycle to write when it does not have a clean number, and the right
    answer there is a row with the text and no bar -- not a bar drawn
    from whatever `float()` could be coaxed into parsing out of a
    sentence, which is the prose-regex failure this block exists to
    avoid, moved one level down.
    """
    value = (text or "").strip().replace("%", "").replace(",", "")
    if not _NUMBER_RE.match(value):
        return None
    return float(value)


def _goal(lines):
    """The body lines of one ```goal fence -> one scoreboard row, or `None`.

    A block with no `name` is dropped: the name is the only field the row
    cannot be rendered without, and a nameless meter on the owner's page is
    a number he cannot attribute to anything.
    """
    row = {}
    for line in lines:
        match = _FIELD_RE.match(line)
        if match and match.group("key") in _FIELDS:
            row[match.group("key")] = match.group("value")
    if not row.get("name"):
        return None

    now_value = _number(row.get("now"))
    target_value = _number(row.get("target"))
    direction = row.get("direction", "").strip().lower()
    if direction not in _DIRECTIONS:
        direction = ""

    on_target = None
    if direction and now_value is not None and target_value is not None:
        on_target = (
            now_value <= target_value if direction == "down" else now_value >= target_value
        )

    return {
        "name": row["name"],
        "measure": row.get("measure", ""),
        "now": row.get("now", ""),
        "target": row.get("target", ""),
        "unit": row.get("unit", ""),
        "direction": direction,
        "nowValue": now_value,
        "targetValue": target_value,
        "onTarget": on_target,
    }


def _fenced(text, builders):
    """`({name: rows}, text_without_the_blocks)`, in one pass over `text`.

    **Why a fenced block and not a parser over the prose.** The numbers in
    `goals.md` are hand-written English inside a paragraph -- *"This week:
    208 PRs merged across the three repos, against 74 of your board rows
    closed."* A regex over that renders a wrong number the first cycle
    that rewrites its own sentence, and says nothing when it does. The
    module docstring above argues these two documents must not be given a
    contract; this is the exception that keeps the rule, because the block
    is **optional and additive**. A goal with no block still renders as
    prose, a document with no blocks renders exactly as it did before, and
    nothing here reads a word the owner or a cycle wrote for a human.

    The blocks are removed from the text on the way through, so the fence
    does not also render as a code block underneath the row it drew.

    `builders` maps a fence name to the function that turns one fence's body
    lines into a row, or returns `None` to drop it.

    **Every fence name is scanned in the same pass, and that is the whole
    point rather than an optimisation.** The first version of this ran once
    per fence type, and a bare ``` closes whatever is open regardless of what
    opened it -- so an unterminated ```goal immediately followed by a
    well-formed ```next had its close eaten by the goal pass, and the entire
    `next` block vanished from the card *and* from the prose, with a
    data-free scoreboard row appearing in its place. Measured, not reasoned
    about. `abandon` below is written to make exactly that editing mistake
    survivable, and splitting the scan in two walked straight around it.
    """
    rows = {name: [] for name in builders}
    kept = []
    block = None
    kind = None
    opens = [(name, _fence_open_re(name)) for name in builders]

    def opener(line):
        for name, pattern in opens:
            if pattern.match(line):
                return name
        return None

    def abandon():
        # An unterminated fence is a half-written edit, not a row. Put the
        # lines back rather than swallowing them -- the document is what
        # the owner is reading, and text disappearing is worse than a stray
        # fence appearing. Measured as a real case: deleting a block's last
        # two lines by hand makes the *next* block's opening fence look like
        # this one's body, so without this every paragraph in between
        # vanishes from the page and nothing says so.
        kept.append("```" + kind)
        kept.extend(block)

    for line in (text or "").split("\n"):
        found = opener(line)
        if block is None:
            if found:
                block, kind = [], found
            else:
                kept.append(line)
        elif found:
            abandon()
            block, kind = [], found
        elif _FENCE_CLOSE_RE.match(line):
            row = builders[kind](block)
            if row:
                rows[kind].append(row)
            block = None
        else:
            block.append(line)
    if block is not None:
        abandon()
    return rows, "\n".join(kept)


# The three states a roadmap item can be in, and the words for them. The
# vocabulary is the boards' own -- `issues.md` and `ideas.md` use these exact
# three -- so a row does not mean one thing on `/plan` and another on `/board`.
#
# **The word travels with the symbol, and that is not decoration.** The owner,
# 2026-08-20: *"always pair priority symbols (e.g. 🟠) with the word (e.g.
# 'High') -- don't use the symbol alone, it was hard to read"*, after saying he
# cannot tell the coloured circles apart by colour. So the payload carries both
# and the page prints both; a status this map has never seen carries neither,
# because a chip reading `⚪ Backlog` for something a cycle called `blocked`
# would be the page inventing a fact the file does not state.
_STATUSES = {
    "done": ("✅", "Done"),
    "in progress": ("🟡", "In progress"),
    "in-progress": ("🟡", "In progress"),
    "backlog": ("⚪", "Backlog"),
    "blocked on edvard": ("⏸", "Blocked on Edvard"),
    "blocked-on-edvard": ("⏸", "Blocked on Edvard"),
    "outdated": ("⚫", "Outdated"),
}

# Every field a ```next block understands, same rule as `_FIELDS`: a key
# outside this set is dropped rather than passed through.
_NEXT_FIELDS = ("rank", "title", "status", "claim", "board")


def _next(lines):
    """The body lines of one ```next fence -> one ranked-strip card, or `None`.

    A block with no `title` is dropped, for the same reason a nameless goal
    is: the card is unreadable without it.

    `rank` is taken from the block rather than from the card's position in
    the strip. The file numbers these items in its own prose and strikes one
    through when it is done without renumbering the rest -- item 3 is
    finished and still called 3 -- so position and rank genuinely disagree,
    and the number the owner reads in the paragraph is the one that has to be
    on the card.
    """
    row = {}
    for line in lines:
        match = _FIELD_RE.match(line)
        if match and match.group("key") in _NEXT_FIELDS:
            row[match.group("key")] = match.group("value")
    if not row.get("title"):
        return None

    symbol, label = _STATUSES.get(row.get("status", "").strip().lower(), ("", ""))
    return {
        "rank": row.get("rank", ""),
        "title": row["title"],
        "claim": row.get("claim", ""),
        "board": row.get("board", ""),
        "statusSymbol": symbol,
        "statusLabel": label,
    }


def _updated(text):
    """The `updated:` stamp out of the frontmatter, or `""`.

    Only inside the frontmatter, and only if the file opens with one: an
    `updated:` line further down is somebody's prose, and a stamp that is
    really the middle of a sentence is worse on screen than no stamp. The
    value is not parsed into a date -- these files carry `2026-08-16` and
    the page prints it as written, so validating the format here would
    only give this module an opinion it cannot act on.
    """
    lines = (text or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            match = _UPDATED_RE.search("\n".join(lines[1:i]))
            return match.group("value") if match else ""
    return ""


# A heading that opens with a bare `YYYY-MM-DD`. The weekly reviews in
# `goals.md` are written `### 2026-08-17 — week of 08-16 to 08-17 (Cycle
# 257)`, newest first, and that date is the only thing about them this
# module is willing to recognise.
_DATED_HEADING_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\b")


def _mark_open(sections):
    """Give every section an `open` flag: is it expanded when the page paints?

    `/plan` is 4,961 words on one route -- a 25-minute read on a phone,
    with no entry point but the top, which is the complaint the owner filed
    as issue #96 (*"It is just a huge wall of text. I hate that."*). So
    every headed section arrives collapsed and the page opens at two
    screens of scoreboard and ranked strip instead of at paragraph one.

    Two exceptions, and both are the accordion rule from NN/g that
    `research/plan-page-design.md` settles on: **never hide crucial
    information inside a collapsed panel.**

    - **The standfirst is never folded at all** (level 0, no heading). In
      `goals.md` it is the paragraph saying the slate is a proposal and
      not a settled list, and a reader who never opens a fold still has
      to see it.
    - **The newest entry of a dated stack opens.** A run of two or more
      consecutive sections at the same level whose headings begin with a
      `YYYY-MM-DD` is a stack, and the first one in document order is the
      newest, because both files that have one are written newest-first.

    That second rule is deliberately structural rather than named. This
    module's contract is that sections are *discovered, never named* --
    matching on the literal text "Weekly review" would put a heading
    the owner is free to retitle into the parser, and the page would go
    quietly back to a wall the day he did. A date prefix is a shape.

    A lone dated section is not a stack and stays collapsed: with nothing
    to be newer *than*, opening it is just an opinion about one section.
    """
    def is_dated(section):
        return bool(
            section["heading"] and _DATED_HEADING_RE.match(section["heading"])
        )

    # Maximal runs of adjacent dated sections at one level. Adjacency is
    # what makes it a stack: two dated headings with an undated section
    # between them are two separate things that happen to carry dates.
    newest = set()
    start = 0
    while start < len(sections):
        if not is_dated(sections[start]):
            start += 1
            continue
        end = start + 1
        while (
            end < len(sections)
            and is_dated(sections[end])
            and sections[end]["level"] == sections[start]["level"]
        ):
            end += 1
        if end - start > 1:
            newest.add(start)
        start = end

    for i, section in enumerate(sections):
        section["open"] = not section["heading"] or i in newest


def _attach_history(scoreboard, history):
    """Hang each goal's past readings on its scoreboard row.

    `history` is `nova_goal_history.series` output. A goal with no series
    gets `[]` rather than a missing field, so the renderer has one branch
    ("is this list empty") instead of two.

    The current `now:` is deliberately *not* appended here. It is
    whatever the last review wrote into the fence, and the ledger already
    holds that same reading under the date it was taken -- appending it
    again would draw a duplicated final point, and worse, would draw a
    point at "today" for a number measured last Monday.
    """
    for goal in scoreboard:
        goal["history"] = list((history or {}).get(goal_key(goal["name"]), []))
    return scoreboard


def _document(key, label, text, history=None):
    """One markdown document -> one card's worth of payload.

    A missing or empty document is `missing: True` with no sections
    rather than an error. Both files are written by a cycle and could
    genuinely not exist yet -- the same call `nova_costs` and `nova_retro`
    make about their ledgers -- and a page that says "not written yet" is
    a true answer, where a 502 on the whole page would take the document
    that *is* there down with it.
    """
    text = text or ""
    if not text.strip():
        return {
            "key": key,
            "label": label,
            "title": label,
            "updated": "",
            "missing": True,
            "scoreboard": [],
            "ranked": [],
            "sections": [],
        }

    # Before `outline`, so a block sitting under a heading does not have to
    # be found twice, and after the emptiness check, so a missing document
    # is still one branch.
    blocks, text = _fenced(text, {"goal": _goal, "next": _next})
    scoreboard, ranked = _attach_history(blocks["goal"], history), blocks["next"]

    title = label
    sections = []
    for level, heading, body in outline(text):
        if level == 1 and title == label:
            # The `# ` heading is the document's own title, so it becomes
            # the card's title rather than a section inside it. Its body
            # is the standfirst the file opens with and still has to be
            # rendered, which is why this does not `continue`.
            title = heading
            level, heading = 0, None
        blocks = render_blocks(body)
        if not heading and not blocks:
            continue
        sections.append({"level": level, "heading": heading, "blocks": blocks})
    _mark_open(sections)

    return {
        "key": key,
        "label": label,
        "title": title,
        "updated": _updated(text),
        "missing": False,
        "scoreboard": scoreboard,
        "ranked": ranked,
        "sections": sections,
    }


def plan_payload(documents, history=None):
    """`{key: markdown}` -> the `/plan` payload.

    Every document in `PLAN_DOCUMENTS` appears in the output whether or
    not the fetch found it, in the fixed order above. A page that renders
    only what it managed to read is a page that goes quietly from two
    cards to one, and the missing one is exactly the case worth seeing.

    `history` is the raw `goal-history.json` text and defaults to none,
    which is a scoreboard with no lines under it -- the state of this
    page before the first snapshot, and the state of it if that one fetch
    fails. A goal's *current* number never comes from the ledger, so the
    scoreboard reads the same either way.
    """
    try:
        past = series(history) if history else {}
    except (GoalHistoryError, ValueError):
        # A ledger that will not parse costs the sparklines and nothing
        # else. Taking the whole `/plan` page down -- the roadmap, the
        # goals, every word of both -- over a chart decoration is the
        # wrong trade, and the empty chart is visible on the page.
        past = {}
    return {
        "documents": [
            _document(key, label, (documents or {}).get(key, ""), past)
            for key, label, _path in PLAN_DOCUMENTS
        ]
    }
