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

The ranked strip leaves here as two lists, not one -- what is still ahead
and what has closed. `_split_ranked` has the reason and the day it was
wrong.
"""

import re

from agora_runner.md_sections import outline
from agora_runner.nova_boards import (
    parse_milestone_keeps, parse_milestone_serves,
)
from agora_runner.nova_goal_history import GoalHistoryError, goal_key, series
from agora_runner.nova_journal import parse_board_refs, render_blocks
from agora_runner.project_goals import (
    KEY_RESULT_FIELDS, KPI_FIELDS, OBJECTIVE_FIELDS, PROJECT_GOALS_PATH,
    kpi_breach, month_name, parse_project_goals, project_goal_coverage,
    split_serves,
)

ROADMAP_PATH = "projects/sokrates/projects/nova/roadmap.md"
GOALS_PATH = "projects/sokrates/projects/nova/goals.md"

# Order is the reading order he asked for, and it is deliberately goals
# last. `roadmap.md` answers "what next", which is the question he opens
# the page with; `goals.md` answers "what for", which is the one he only
# asks when he disagrees with the first answer.
PLAN_DOCUMENTS = (
    ("roadmap", "Roadmap", ROADMAP_PATH),
    ("goals", "Goals", GOALS_PATH),
    # `project-goals.md`, issue #227: one objective, its key results and the
    # project's KPIs, per project. It is last because it is the longest and
    # the most specific -- the roadmap answers "what next", `goals.md`
    # answers "what for" across the whole loop, and this answers it one
    # project at a time.
    #
    # **It is here because it repeated, one level down, the exact problem
    # this module exists to fix.** The docstring above says `roadmap.md` and
    # `goals.md` "live in his vault and nowhere else, so the one document
    # whose entire purpose is being argued with is the one he has to open
    # Obsidian to read". `project-goals.md` is 28KB written to be argued
    # with -- his 09-13 correction deleted the approval step precisely so
    # that each objective is settled in conversation instead -- and until
    # now it reached no screen he owns.
    ("projects", "Project goals", PROJECT_GOALS_PATH),
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
_FIELDS = ("name", "measure", "now", "target", "unit", "direction", "status")

# What the owner has said about a goal. `goals.md`'s own contract calls the
# slate a set of proposed goals awaiting his approval -- "he ticks, edits or
# deletes; nothing here is settled until he does" -- and until now ticking
# one meant opening Obsidian and editing markdown on a phone. He has not
# done it once since Cycle 229 wrote the slate on 2026-08-16, which is why
# idea #38 has sat at "In progress" with its remaining half described as
# "yours" for ten days.
#
# **A missing `status:` means proposed, and that is the whole compatibility
# story.** Every block in `goals.md` today has no such line, so the default
# has to be the state they are actually in rather than a neutral "unknown" —
# and a goal he declines keeps its block and its prose instead of being
# deleted, because a struck goal is a decision worth being able to read
# later and worth being able to reverse in one tap.
GOAL_STATUSES = ("proposed", "approved", "declined")
DEFAULT_GOAL_STATUS = "proposed"

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

    # An unreadable `status:` reads as proposed rather than as its own
    # fourth state. The row is a control he taps, so the only safe way to
    # render a value nothing understands is the one where his next tap
    # writes a value that is understood.
    status = row.get("status", "").strip().lower()
    if status not in GOAL_STATUSES:
        status = DEFAULT_GOAL_STATUS

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
        "status": status,
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



# `project-goals.md`'s own three fences, turned into prose in place rather
# than into payload rows. See `_inline_goal_blocks` for why that is the
# right shape and not a shortcut.
def _block_fields(lines, allowed):
    """One fence's body lines -> `{field: value}`, unknown keys dropped.

    The same rule as `_goal` and `_next`: a key outside the set is left out
    of the row rather than passed through, so a `targt:` typo renders
    nothing instead of rendering wrong.
    """
    row = {}
    for line in lines:
        match = _FIELD_RE.match(line)
        if match and match.group("key") in allowed:
            row[match.group("key")] = match.group("value").strip()
    return row


def _objective_prose(lines, seats=None):
    """A ```objective fence -> one markdown paragraph, or `None`.

    `seats` is unused here and present only so the three builders share one
    signature -- an objective is not a thing a milestone points at; its key
    results are.

    `None` when there is no `statement`, which is the one field the
    paragraph cannot be written without -- the same call `_goal` makes
    about a missing `name`. `project_goals.problems` already reports that
    block as a defect; this module's job is only not to invent a sentence
    for it.

    The status word travels with the statement because the three words are
    the whole state of the thing: `discussing` means he and I have not
    settled it, and an objective printed without it reads as decided.
    """
    row = _block_fields(lines, OBJECTIVE_FIELDS)
    statement = row.get("statement", "")
    if not statement:
        return None
    status = row.get("status", "")
    head = f"**Objective — {status}.**" if status else "**Objective.**"
    out = f"{head} {statement}"
    if not out.endswith("."):
        out += "."
    # The month it covers, from issue #227's seventh rule: *"Monthly
    # objectives, weekly check."* Printed as a month name rather than
    # `2026-09`, and printed here rather than judged -- whether the month is
    # over is a question about today, this builder has no clock, and a page
    # that renders one answer in Oslo and another in UTC is worse than one
    # that states the fact and lets `project_goals_check` do the arithmetic.
    period = row.get("period", "")
    if period:
        out += f" Covers {month_name(period)}."
    else:
        # `objective_periods` already collects this one as `undated` and
        # `project_goals_check` prints the count, so the defect was known --
        # to the command line. The page printed a tidy paragraph for it, the
        # same way it printed one for a key result with no target until
        # yesterday: the sentence is missing, so nothing on the screen says
        # anything is. Same words as the check, so the two cannot disagree.
        out += " No month set."
    if row.get("conversation"):
        out += f" Argued out in Agora conversation `{row['conversation']}`."
    return out


def _key_result_prose(lines, seats=None):
    """A ```key-result fence -> one markdown paragraph, or `None`.

    **A blank `now:` prints "not measured yet", never a zero.** Four of the
    numbers in this document are deliberately blank because no instrument
    exists to take them, and a scoreboard that renders a blank as 0 is the
    one failure every cycle that built those instruments wrote down: an
    unmeasured guardrail and a perfect score look identical, and the wrong
    one looks like the best possible answer.
    """
    row = _block_fields(lines, KEY_RESULT_FIELDS)
    name = row.get("name", "")
    if not name:
        return None
    unit = f" {row['unit']}" if row.get("unit") else ""
    # The status word travels with the name for the same reason it travels
    # with the objective's statement one function up: `discussing` means he
    # and I have not settled this yet, and a key result printed without it
    # reads as decided. `project_goals.KEY_RESULT_STATUSES` is what the three
    # words may be; this builder prints whatever the block says rather than
    # judging it, so a word that module refuses is still visible on the page
    # instead of being silently dropped.
    status = row.get("status", "").strip()
    # Parenthetical rather than a trailing `, discussing`: the status hangs
    # off the label the way the objective's does, and three of the live names
    # already contain a comma -- "The work closes your rows, not my own
    # plumbing, discussing." reads as a third clause of his sentence.
    head = f"**Key result ({status}) — {name}.**" if status else (
        f"**Key result — {name}.**")
    parts = [head]
    if row.get("measure"):
        parts.append(_sentence(row["measure"]))
    parts.append(f"Now {row['now']}{unit}." if row.get("now")
                 else "Not measured yet.")
    # Said out loud when it is missing, the way "Not measured yet." is said
    # for a missing `now` two lines up. `project_goals.problems()` refuses a
    # key result with no target, and a defect it refuses has to stay visible
    # on the page he actually opens rather than rendering as a key result
    # that simply had nothing worth saying about its target.
    parts.append(f"Target {row['target']}{unit}." if row.get("target")
                 else "No target set.")
    if row.get("direction") == "down":
        parts.append("Lower is better.")
    elif row.get("direction") == "up":
        parts.append("Higher is better.")
    parts.extend(_seat_sentence(seats, "served", row.get("id", "")))
    if row.get("id"):
        parts.append(f"`{row['id']}`")
    return " ".join(parts)


def _kpi_prose(lines, seats=None):
    """A ```kpi fence -> one markdown paragraph, or `None`.

    **A KPI's `target:` is deliberately never printed.** Issue #227's own
    rule is that a KPI may never carry one -- *"the moment a guardrail
    carries a target, the dashboard gets optimised instead of the work"* --
    so `KPI_FIELDS` reads the key in only so `project_goals.problems` can
    refuse it. Drawing it here would put the forbidden thing on the page
    and leave the refusal in a tool nobody but a cycle runs.

    **The bounds sentence used to be the end of it, and that is the half that
    was missing.** This paragraph printed *"Now 6."* and *"In bounds 0 to 1."*
    one after the other and left the subtraction to the reader -- so a
    guardrail breached six times over rendered exactly like one being held.
    `kpi_breach` is the comparison, and it is bold because a KPI out of its
    range is the one thing on this page that is asking for something to be
    done. A KPI with no `now`, or a `now` nothing can read as a number, says
    nothing here rather than guessing: unmeasured and in-bounds are not the
    same state.
    """
    row = _block_fields(lines, KPI_FIELDS)
    name = row.get("name", "")
    if not name:
        return None
    unit = f" {row['unit']}" if row.get("unit") else ""
    parts = [f"**KPI — {name}.**"]
    if row.get("measure"):
        parts.append(_sentence(row["measure"]))
    parts.append(f"Now {row['now']}{unit}." if row.get("now")
                 else "Not measured yet.")
    low, high = row.get("low", ""), row.get("high", "")
    if low and high:
        parts.append(f"In bounds {low} to {high}{unit}.")
    elif high:
        parts.append(f"Ceiling {high}{unit}.")
    elif low:
        parts.append(f"Floor {low}{unit}.")
    breach = kpi_breach(row)
    if breach:
        parts.append(f"**Out of bounds: {breach}.**")
    parts.extend(_seat_sentence(seats, "kept", row.get("id", "")))
    if row.get("id"):
        parts.append(f"`{row['id']}`")
    return " ".join(parts)


# What each half of `seat_counts` is called, and the two sentences it turns
# into. `served` counts `milestone-seats.md`'s `Serves` column, `kept` counts
# its `Keeps` column, and the two are never pooled -- issue #227's rule that a
# KPI may never be a key result is enforced one column at a time by
# `project_goals.serves_problems` and `keeps_problems`, and a page that added
# them together would report a broken pointer as coverage.
_SEAT_WORDS = {
    "served": ("Served by", "**No milestone serves this yet.**"),
    "kept": ("Kept by", "**No milestone keeps this in bounds.**"),
}


def seat_counts(serves, keeps):
    """The two seat maps -> `{"served": {id: n}, "kept": {id: n}}`.

    `serves` and `keeps` are `nova_boards.parse_milestone_serves` and
    `parse_milestone_keeps` -- `{(project, milestone): cell}`, every seated
    milestone present including the ones whose cell is empty. This counts how
    many seats name each id, which is `project_goals.unpointed_goals` read
    forwards: that function lists the goals nothing points at, and this says,
    for every goal, how much is pointed at it.

    An id nobody names is simply absent from the map rather than present as
    `0`, and `_seat_sentence` turns both into the same sentence. The
    distinction that matters is one level up and is `None` versus a map --
    see there.
    """
    out = {"served": {}, "kept": {}}
    for name, cells in (("served", serves), ("kept", keeps)):
        for cell in (cells or {}).values():
            for identifier in split_serves(cell):
                out[name][identifier] = out[name].get(identifier, 0) + 1
    return out


def _seat_sentence(seats, column, identifier):
    """`[]` or one sentence saying how many milestones point at `identifier`.

    **`seats` of `None` prints nothing, and that is the whole point of the
    argument being optional** -- `plan_payload` passes `None` whenever the
    seats text is missing or empty. The seats file is a separate vault fetch
    from the document being rendered, so it can fail on its own; if it does,
    every
    key result on the page would otherwise read "No milestone serves this
    yet", which is the worst failure this page can have -- a fetch that did
    not happen rendering as the finding a cycle is meant to act on. Unread
    and unserved are not the same state, so only a real map speaks.

    An id of `""` also prints nothing: a block with no `id` is one no `Serves`
    cell can name, so "nothing points at it" is true and useless.
    """
    if seats is None or not identifier:
        return []
    counted = (seats.get(column) or {}).get(identifier.strip().lower(), 0)
    label, none_of_them = _SEAT_WORDS[column]
    if not counted:
        return [none_of_them]
    return [f"{label} {counted} milestone{'' if counted == 1 else 's'}."]


def _sentence(text):
    return text if text.endswith((".", "!", "?")) else text + "."


_GOAL_BLOCK_PROSE = {
    "objective": _objective_prose,
    "key-result": _key_result_prose,
    "kpi": _kpi_prose,
}


def _inline_goal_blocks(text, seats=None):
    """Turn `project-goals.md`'s fences into prose, where they stand.

    **Why prose and not payload rows, which is the obvious thing to do.**
    Every field name in a ```key-result fence is `_FIELDS`' own vocabulary,
    chosen by `project_goals` so "the scoreboard machinery is reused rather
    than duplicated", so routing them into `scoreboard` costs one line and
    draws a meter per key result for free. It also draws the meter's
    *control*: a scoreboard row is a thing he taps to set a goal's status,
    and that tap writes `goals.md` through `set_status_in_goals`. Pointing
    it at this document would either write the wrong file or re-grow the
    approve/decline gate he deleted on 2026-09-13 -- *"I should not have to
    approve goals. Goals, milestones, kpis and okrs should be derived based
    on a conversation between you and me"*. A button that re-proposes a
    thing he struck out is worse than no button.

    So the blocks render as sentences inside the section they already sit
    in, which needs no new payload field and no new renderer: `/plan`'s
    page walks `sections` for whatever headings are there, which is the
    contract the module docstring sets out. The position matters and is
    kept exactly -- each fence's prose goes where the fence was, under its
    own `## <Project>` heading and above the paragraph explaining where its
    number came from.

    A fence this does not own is untouched, including ```goal and ```next,
    so `_fenced` still sees them. An unterminated or nameless block is put
    back verbatim, fences and all, for `_fenced.abandon`'s measured reason:
    a half-written edit should render as a stray code block, never as
    paragraphs silently disappearing.
    """
    kept, block, kind = [], None, None
    opens = [(name, _fence_open_re(name)) for name in _GOAL_BLOCK_PROSE]

    def opener(line):
        for name, pattern in opens:
            if pattern.match(line):
                return name
        return None

    def put_back(closed):
        kept.append("```" + kind)
        kept.extend(block)
        if closed:
            kept.append("```")

    for line in (text or "").split("\n"):
        found = opener(line)
        if block is None:
            if found:
                block, kind = [], found
            else:
                kept.append(line)
        elif found:
            put_back(False)
            block, kind = [], found
        elif _FENCE_CLOSE_RE.match(line):
            prose = _GOAL_BLOCK_PROSE[kind](block, seats)
            if prose is None:
                put_back(True)
            else:
                # Blank lines either side so the paragraph cannot merge
                # into the note under it -- the file writes that note
                # directly beneath the closing fence, with no blank line.
                kept.extend(("", prose, ""))
            block = None
        else:
            block.append(line)
    if block is not None:
        put_back(False)
    return "\n".join(kept)


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

# The statuses that mean an item is no longer something anybody would do
# next. `_split_ranked` uses this to keep the strip's headline true; see
# there for why that is worth a set of its own.
_FINISHED_STATUSES = frozenset({"done", "outdated"})

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

    status = row.get("status", "").strip().lower()
    symbol, label = _STATUSES.get(status, ("", ""))
    return {
        "rank": row.get("rank", ""),
        "title": row["title"],
        "claim": row.get("claim", ""),
        "board": row.get("board", ""),
        # The same field twice, and the plain string is not redundant:
        # `tools.roadmap_drift` reads `board` to check each block against the
        # live boards, and it wants the text, not spans. The page wants the
        # spans, because `issue #131` on a card is a row the owner can open
        # and could not tap. `parse_board_refs` is the journal footer's own
        # parser -- a `Board:` reference means the same thing here as it does
        # on a journal card, so it must not grow a second spelling.
        "boardSpans": parse_board_refs(row.get("board", "")),
        "statusSymbol": symbol,
        "statusLabel": label,
        "finished": status in _FINISHED_STATUSES,
    }


def next_items(markdown):
    """`roadmap.md` -> its ```next blocks, in document order.

    The same rows `/plan` draws its ranked strip from, exposed because a
    second reader now exists: `tools.roadmap_drift` checks each block's
    `board:` field against the live boards. It has to read the fences the
    way the page does, so it calls this rather than writing a second
    parser -- a drift check that disagrees with the page about what the
    file says is measuring its own regex.
    """
    rows, _ = _fenced(markdown, {"next": _next})
    return rows["next"]


def _split_ranked(items):
    """`(open_items, finished_items)`, document order preserved in both.

    **The strip's headline was false for three of its five cards.** It is
    titled *"What I would do next, in order"*, and on 2026-08-25 it read:
    `1 Get CI back` (in progress), `2 Fix the Enter key` (done), `3 Fix my
    vault write path` (done), `4 Build the weekly goal review` (in
    progress), `5 The two board-editing gaps` (done). Three finished items
    sitting under a heading that says they are what happens next, at the
    top of the page the owner filed issue #96 about.

    `_next`'s docstring already explains why the rank number survives an
    item being finished -- the file strikes an item through without
    renumbering the rest, so item 3 stays 3 -- and that is right. The
    mistake was reading "keep the number" as "keep it in the same list".
    A ✅ chip is a label on a card; the heading above the card is a claim
    about every card under it, and a label does not retract a claim.

    So the split is here rather than in the renderer: what a card *means*
    is this module's job, and a payload that carries the two lists
    separately can be checked by a test that does not need a browser.

    Outdated counts as finished. It is not done, but nobody is going to do
    it either, and this list answers one question -- is this still ahead of
    us. An item whose status this module has never seen is treated as open:
    the page declining to guess is the same call `_next` makes about the
    chip, and guessing wrong in this direction shows the owner one card too
    many rather than hiding one.
    """
    open_items = [item for item in items if not item["finished"]]
    finished = [item for item in items if item["finished"]]
    return open_items, finished


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
    # A deeper heading inside an entry is part of that entry, not a gap
    # between two -- a review that folds its detail under `####` is still
    # one entry of the stack (issue #96: the open newest review was 900
    # words, and folding its detail must not cost it the open slot).
    newest = set()
    start = 0
    while start < len(sections):
        if not is_dated(sections[start]):
            start += 1
            continue
        level = sections[start]["level"]
        entries = 1
        end = start + 1
        while end < len(sections):
            if sections[end]["level"] > level:
                end += 1
            elif is_dated(sections[end]) and sections[end]["level"] == level:
                entries += 1
                end += 1
            else:
                break
        if entries > 1:
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


def _document(key, label, text, history=None, seats=None):
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
            "rankedDone": [],
            "sections": [],
        }

    # Before `outline`, so a block sitting under a heading does not have to
    # be found twice, and after the emptiness check, so a missing document
    # is still one branch.
    # Before `_fenced`, and it owns three fence names `_fenced` does not,
    # so the two scans cannot fight over a `` ``` `` close.
    text = _inline_goal_blocks(text, seats)
    blocks, text = _fenced(text, {"goal": _goal, "next": _next})
    scoreboard = _attach_history(blocks["goal"], history)
    ranked, ranked_done = _split_ranked(blocks["next"])

    title = label
    sections = []
    # `####` is a section too (issue #96): a weekly review keeps its
    # one-line lead open and folds the detail under a sub-heading.
    for level, heading, body in outline(text, max_level=4):
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
        "rankedDone": ranked_done,
        "sections": sections,
    }


def _coverage(text, rows):
    """`{total, withGoal, missing:[{project, openRows}]}`, or `None`.

    Issue #227's own title, on the page it is about. The check has printed
    `7 of 12 project(s) have a goal` since cycle 1567 and he has to run a
    tool in a pod to see it; the projects with no goal are *absent* from
    `project-goals.md`, so the card renders as complete by construction --
    which is the guaranteed-positive shape `prompt.md` warns about, drawn
    on his phone.

    `rows` is every row on both boards and `None` means **not read**, which
    is a different claim from "no rows": an unread board would count zero
    projects and print `0 of 0`, the best-looking answer available. So
    `None` returns `None` and the page says nothing, the same convention
    `seats` already uses one function down and `report` uses in the check.

    The set of projects comes off the board rows, never off this document
    -- `projects_without_goals` has the argument, and it is the whole
    reason this number is worth printing.
    """
    if rows is None:
        return None
    missing, total = project_goal_coverage(rows, parse_project_goals(text))
    return {"total": total, "withGoal": total - len(missing),
            "missing": missing}


def plan_payload(documents, history=None, seats=None, rows=None):
    """`{key: markdown}` -> the `/plan` payload.

    Every document in `PLAN_DOCUMENTS` appears in the output whether or
    not the fetch found it, in the fixed order above. A page that renders
    only what it managed to read is a page that goes quietly from two
    cards to one, and the missing one is exactly the case worth seeing.

    `seats` is the raw `milestone-seats.md` text and defaults to none. None
    or empty prints no coverage sentence at all. Passing it makes each key result say
    how many milestones serve it and each KPI how many keep it -- the chain
    issue #227 asks for, read on the page instead of in a tool only a cycle
    runs. `_seat_sentence` has why none and empty are different.

    `rows` is every board row, both boards, and defaults to none, which
    prints no coverage line. It answers "how many projects have no goal at
    all" on the one card that cannot see them -- `_coverage` has why that
    reading needs the boards rather than this document.

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
    # `None` all the way down when there is no seats text, so an unread file
    # renders as silence rather than as "no milestone serves this" against
    # every goal on the page. **Empty is folded in with missing on purpose**:
    # `nova_sources.milestone_seats_markdown` returns `""` both when the
    # fetch fails and when the file is genuinely empty, so the two are not
    # separable here, and the safe reading of an ambiguous pair is the one
    # that does not print a finding.
    counts = seat_counts(parse_milestone_serves(seats),
                         parse_milestone_keeps(seats)) if seats else None
    docs = []
    for key, label, _path in PLAN_DOCUMENTS:
        text = (documents or {}).get(key, "")
        doc = _document(key, label, text, past, counts)
        # Only on the project-goals card, because it is the only one whose
        # subject is projects. The same sentence over `goals.md` would be a
        # count about a document that never claimed to hold one section per
        # project.
        if key == "projects":
            coverage = _coverage(text, rows)
            if coverage:
                doc["coverage"] = coverage
        docs.append(doc)
    return {"documents": docs}


# Bounding the 409 retry, same as `nova_capture.WRITE_ATTEMPTS` and for the
# same reason: the concurrent writer here is a cycle rewriting `goals.md`'s
# weekly review, so a conflict is a real event to re-read past, not a spin.
WRITE_ATTEMPTS = 3


def set_status_in_goals(markdown, name, status):
    """Set one goal's `status:` inside its own ```goal fence. `None` if it moved.

    A thin wrapper over `set_field_in_goals` since Cycle 563, which needed the
    same surgery for `now:` and had no business copying five careful rules
    about duplicate blocks and unterminated fences to get it.
    """
    if status not in GOAL_STATUSES:
        return None
    return set_field_in_goals(markdown, name, "status", status)


def set_field_in_goals(markdown, name, field, value):
    """Set one field inside one goal's ```goal fence. `None` if it moved.

    **The edit is inside the fence and touches nothing else in the file.**
    `goals.md` is 23KB of the owner's prose and my weekly reviews, and the
    fenced block is the one part of it anything parses -- so a status tap,
    or a measured `now:`, rewrites six words of machine-readable data and
    leaves every sentence alone. That is also why these live in the block
    rather than in a heading: a heading is prose a cycle rewrites, and this
    has to survive that.

    The goal is addressed by `name`, which is the block's own required
    field and the string the row on the page is drawn from. A block whose
    name has been rewritten since the page loaded returns `None` -- nothing
    failed, the address moved -- which is `reply_under_capture`'s contract
    and gets `reply_under_capture`'s 409.

    An existing line for the field is replaced in place so the block keeps
    its field order; a block with none gets one appended as its last line,
    which is where a reader looking for "what did he say about this"
    expects it and where it cannot be mistaken for part of `measure`.
    """
    field = (field or "").strip()
    value = str("" if value is None else value).strip()
    # A field name that is not a bare word, or a value carrying a newline,
    # would write something `_goal` cannot parse back -- and the caller would
    # be told it succeeded. Refusing is the same `None` as a name that moved.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field) or "\n" in value:
        return None
    wanted = (name or "").strip()
    if not wanted:
        return None

    lines = (markdown or "").split("\n")
    opener = _fence_open_re("goal")
    name_re = re.compile(r"^(?P<indent>[ \t]*)name:[ \t]*(?P<value>.*?)[ \t]*$")
    field_re = re.compile(r"^(?P<indent>[ \t]*)" + re.escape(field) + r":[ \t]*.*$")

    # Every block that claims this name, not the first. Two goals sharing a
    # `name:` render as two identical rows he can tap separately, and
    # editing whichever comes first would report success on the one he did
    # not touch. There is no way to tell them apart from the wire, so the
    # honest answer is to refuse -- same `None` as a name that moved, which
    # the page already shows him as "no longer where the page thought it
    # was". Found by my own reviewer on this diff, not by a test.
    hits = []
    start = None
    for index, line in enumerate(lines):
        if start is None:
            if opener.match(line):
                start = index
            continue
        if _FENCE_CLOSE_RE.match(line) or opener.match(line):
            body = range(start + 1, index)
            match = None
            for i in body:
                found = name_re.match(lines[i])
                if found and found.group("value") == wanted:
                    match = i
                    break
            if match is not None:
                # An unterminated fence is a half-written edit (see
                # `_fenced.abandon`), so only a block that really closed is
                # writable -- editing inside one would move his own text.
                if not _FENCE_CLOSE_RE.match(line):
                    return None
                hits.append((match, index, body))
            start = index if opener.match(line) else None
    if len(hits) != 1:
        return None

    match, close, body = hits[0]
    indent = name_re.match(lines[match]).group("indent")
    # Every line for this field in the block, not the first. `_goal` builds its
    # row with a plain dict assignment over all the lines, so on a block
    # that already carries two the *last* one is what the page renders --
    # rewriting only the first would return 200 and change nothing he can
    # see. Collapsing them also leaves the block with one answer in it,
    # which is the only shape either side can agree on.
    written = [i for i in body if field_re.match(lines[i])]
    if written:
        for i in written:
            lines[i] = f"{indent}{field}: {value}"
        for i in reversed(written[1:]):
            del lines[i]
        return "\n".join(lines)
    lines.insert(close, f"{indent}{field}: {value}")
    return "\n".join(lines)


def set_goal_status(name, status):
    """Record what the owner said about one goal. Returns `(ok, message)`.

    Idea #38's remaining half, and it is the half he was left holding:
    *"the five goals in `goals.md` are still a slate awaiting your tick,
    and nothing in it is settled until you edit it"* -- written on
    2026-08-19 and still true today, because editing it meant opening
    Obsidian on a phone to hand-edit markdown. It is also one of the four
    things goal **G2** counts as still needing an app other than Nova, and
    it is the only one of the four that blocks another board row.

    Same read-modify-write, same `if_rev`, same bounded 409 retry as
    `nova_capture.comment_on_capture`, because the losing writer is the
    same class: a cycle appending this week's review while he taps.
    """
    from agora_runner.log import log
    from agora_runner.vault import vault_read_path_rev, vault_write_path

    if status not in GOAL_STATUSES:
        return False, f"status must be one of {list(GOAL_STATUSES)}"
    if not (name or "").strip():
        return False, "no goal named"

    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(GOALS_PATH)
        if current is None:
            return False, "goals.md not found"
        amended = set_status_in_goals(current, name, status)
        if amended is None:
            return False, "that goal is no longer where the page thought it was"
        if amended == current:
            return True, f"already {status}"
        result = vault_write_path(GOALS_PATH, amended, if_rev=rev)
        if result == "written":
            log(f"nova-plan goal {name!r} marked {status}")
            return True, status
        if "409" not in result:
            break
    log(f"nova-plan failed marking goal {name!r} {status}: {result}")
    return False, f"could not write to goals.md: {result}"
