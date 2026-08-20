"""Check a journal entry before it is written, while the author can fix it.

Four live documents cannot be rendered as written -- six breakages in
all, three of the heading rule and three of the footer rule, with two
documents failing both -- and every repair so far has been code that
reads the mistake back afterwards.
`normalise_entry` promotes a `## ` heading and synthesises one from the
filename when there is none (Cycle 150); `stray_footer` lifts a `PR: ...`
line the cycle bolded and put at the top (Cycle 151). Both work, and
both are guesses made by something that was not there when the entry was
written. The author was. An entry is written once and never edited, so
the only moment a mistake is cheap to correct is before the `put`.

    python3 -m tools.lint_entry entry.md --name 168-cycle-152.md

Exits 0 when the document renders as written, 1 when a repair would be
needed, 2 when it could not be read. `prompt.md` step 7 chains the `put`
behind it with `&&`, so a failed check does not write.

**It reports where the renderer would have to repair the document; it
does not restate the renderer's rules.** That distinction is the whole
design. A linter carrying its own copy of what a valid entry looks like
is a seventh statement of the rules, free to drift from the six already
in `nova_journal.py`, and a linter that disagrees with the parser is
worse than no linter. So every check here runs the real function and
compares: heading by calling `normalise_entry`, footer by applying
`_FOOTER_RE` and then `stray_footer` exactly as `parse_journal` does.

Measured against all 166 live entries (2026-08-13): 4 documents are
flagged and 162 pass untouched -- 3 fail the heading check (Cycle 150's
bug) and 3 the footer check (Cycle 151's), two of them failing both. The
cycle-number check fires on none of them, which is said plainly rather
than dropped: the one live heading/filename disagreement is Cycle 131's,
and the heading check already has it. It stays because the failure it
guards is distinct and silent -- a correct-looking heading carrying the
wrong number puts one cycle's words under another's name, while the gap
detector counts that cycle from the filename, so the two halves of the
site disagree and nothing anywhere raises an error. That measurement is
also why there is no rule here
Most checks here report where the renderer would have to repair the
document. Two do not, deliberately: the stamp check, because a heading
dated ahead of the clock renders exactly as written and is wrong anyway
since the feed sorts on it, and the clock check, because an entry that
claims it spent half an hour inside an eleven-minute cycle renders
perfectly and is simply untrue. So the tool is "what must not be
written", which is a superset of "what would be repaired".

requiring the `---` above the footer, which `personality.md` asks for
and 17 live entries do not have -- every one of those 17 because it
carries the `Reviewer: n findings` line the review rubric asks for, in
the place the rule would go. `_FOOTER_RE` was deliberately changed to make that rule
optional (Cycle 104's card showed no PR for a cycle that had merged
one). A check written from the prose alone would fail a sixth of the
real journal, which is how a linter becomes something cycles learn to
ignore.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

from agora_runner.config import OSLO
from agora_runner.nova_journal import (
    _ENTRY_HEADING_RE,
    _FOOTER_RE,
    JOURNAL_DIR,
    normalise_entry,
    parse_journal,
    parse_board_refs,
    split_ask,
    stray_footer,
)

# `<seq>-cycle-<n>.md`, and the `-addendum` suffixes twelve live files
# carry. Entry 004 has no cycle token at all and never will -- it is
# Edvard's own first message -- so a filename that does not match is not
# a finding, it just means there is no second statement of the cycle
# number to check the heading against.
_FILENAME_CYCLE_RE = re.compile(r"\A\d+-cycle-(\d+)(?:-|\.)")


def _heading_finding(path, content):
    """The document does not start where `parse_journal` looks for a start."""
    normalised = normalise_entry(path, content)
    if normalised == (content or "").strip():
        return None
    first = ((content or "").strip().split("\n") or [""])[0]
    return (
        "heading: this document does not begin with its `### ` heading, so "
        "the site would repair it rather than read it. Its first line is "
        f"{first[:70]!r}. Write the entry starting `### ` on line 1, with no "
        "frontmatter and exactly three hashes -- two makes the whole hour "
        "render as the tail of the previous cycle's card."
    )


def _raw_body(normalised):
    """The entry body exactly as `parse_journal` slices it, before any repair.

    One document is one entry, so this is everything after the first
    `### ` line. It has to be recomputed rather than read off the parsed
    entry because `parse_journal` hands back a body with the footer
    already removed -- by the strict rule *or* by the repair, and it does
    not say which.
    """
    headings = list(_ENTRY_HEADING_RE.finditer(normalised))
    if not headings:
        return normalised.strip()
    start = headings[0].end()
    # Bounded by the next heading, exactly as `parse_journal` bounds each
    # entry. Taking everything to the end instead let a document with two
    # headings pass the footer check on the *second* entry's footer, since
    # `_FOOTER_RE` anchors to the end of what it is given -- so a first
    # entry with no footer at all reported nothing. Masked by the `split`
    # finding today, and still the parser disagreeing with the checker.
    end = headings[1].start() if len(headings) > 1 else len(normalised)
    return normalised[start:end].strip()


def _footer_finding(body):
    """The `PR: ... | Outcome: ...` line is not where the renderer reads it.

    Applies `_FOOTER_RE` directly, which is the only way to see the
    answer. Asking the parsed entry whether it has a `pr` cannot fail:
    `parse_journal` falls back to `stray_footer` when the strict rule
    misses, so by the time it returns, a repaired entry and a correct one
    are indistinguishable. The first version of this check did exactly
    that and reported nothing on all three of the live entries whose
    missing PR badge Edvard could see -- a negative result that was
    guaranteed in advance.
    """
    if _FOOTER_RE.search(body):
        return None
    _, pr, _, _ = stray_footer(body)
    if pr:
        return (
            "footer: the `PR: ... | Outcome: ...` line is in the document but "
            "not at the end of it, so the site would have to move it to give "
            "this cycle a badge. Put it bare on the last line, not bolded and "
            "not wrapped across two lines."
        )
    return (
        "footer: no `PR: ... | Outcome: ...` line the site can read, so this "
        "cycle's card would show no PR and no outcome -- which reads as an "
        "hour that shipped nothing. Add it as the last line, e.g. "
        "`PR: #123 | Outcome: merged`, or `PR: none | Outcome: ...`."
    )


# A paragraph that opens `**Needs Edvard**` with no colon, and then says
# something. Cycle 262 made `_ASK_RE` require the colon (runner#242), which
# fixed a real defect -- two 2026-08-11 entries name the old digest section
# in ordinary prose and were parsing as open asks, so the header's "waiting
# on you" pill pointed at them instead of at the live one. It also made the
# opposite mistake silent: an entry that writes the bare label now has its
# ask dropped with no error anywhere, which is worse than the bug that was
# fixed, because the cycle believes it asked.
#
# **The paragraph anchor is what keeps the prose out, and it is measured
# rather than reasoned.** Anchoring on the line instead fires on
# `012-cycle-12.md`, which wraps a sentence so that `**Needs Edvard** and
# **Next cycle** in there with it` starts a line mid-paragraph -- the
# entries are hard-wrapped, so a line start is not a paragraph start. With
# the blank-line anchor this matches **0 of the 326 live entries** and
# still fires on the bare label written as an ask.
_BARE_ASK_RE = re.compile(r"(?:\A|\n[ \t]*\n)\*\*Needs Edvard\*\*[ \t]+\S")


def _ask_finding(body):
    """A `Needs Edvard` ask the site would drop for want of a colon.

    Runs the real parser rather than restating it, the same way
    `_board_finding` runs `parse_board_refs`: the finding is "`split_ask`
    found no ask *and* this looks like one", so the day the parser's shape
    changes this check changes with it instead of drifting from it.

    Silence is the common answer -- 7 of 326 live entries carry an ask at
    all. `prompt.md` is explicit that most cycles raise none.
    """
    if split_ask(body)[1]:
        return None
    if not _BARE_ASK_RE.search(body):
        return None
    return (
        "ask: this paragraph opens `**Needs Edvard**` without the colon, so "
        "`split_ask` reads it as prose and the site drops the ask -- no "
        "yellow block, no open comment drawer, and nothing anywhere saying "
        "so. Write `**Needs Edvard:**`, colon inside the bold."
    )


# Finding the end of the ask's first sentence, which is harder than one
# regex because both directions of error are live in real asks.
#
# **A `?` inside a quotation is not this sentence asking anything**, and
# that is the failure that matters, because every ask ever written quotes
# Edvard or quotes an earlier entry. Reviewer's case on #254, run rather
# than argued: *The card that said "should I proceed?" was from last week
# and is now stale. Should I proceed now, yes or no?* opens with a
# statement, is exactly the wall-of-text shape this check exists to
# refuse, and passed silently on the first version. So quoted spans are
# masked out before anything is scanned -- with a same-length filler, so
# every offset still points at the original text.
_ASK_QUOTED_RE = re.compile(r"\"[^\"]*\"|“[^”]*”|`[^`]*`")
_ASK_MASK = "░"

# **And a `.` that ends an abbreviation is not the end of a sentence.**
# The first version tested this with a one-character lookbehind, which
# covers `i.e.` and `e.g.` -- where every letter is its own token -- and
# nothing else, so `Should Mr. Anderson sign off, yes or no?` was refused
# for opening with a statement. That is the expensive direction to fail
# in: a correctly written ask that cannot be published. A named list is
# duller than a lookbehind and is right about the cases that occur.
_ASK_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof st jr sr vs etc eg ie approx cf no fig".split()
)
_ASK_TERMINATOR_RE = re.compile(r"[?!]|\.(?=\s|\Z)")
_ASK_WORD_BEFORE_RE = re.compile(r"([A-Za-z0-9]+)\Z")


def _first_sentence_end(ask):
    """The match ending the ask's first sentence, or `None` if it has none.

    Offsets are into `ask` itself: masking preserves length on purpose, so
    the caller can slice the original text out of a scan of the masked
    copy.
    """
    masked = _ASK_QUOTED_RE.sub(lambda m: _ASK_MASK * len(m.group()), ask)
    for match in _ASK_TERMINATOR_RE.finditer(masked):
        if match.group() != ".":
            return match
        word = _ASK_WORD_BEFORE_RE.search(masked[: match.start()])
        token = word.group(1).lower() if word else ""
        # A one-character token is `i.`, `e.` or a list marker; a decimal
        # never reaches here at all, because `(?=\s|\Z)` already requires
        # whitespace after the dot and `$0.00` has none.
        if len(token) <= 1 or token in _ASK_ABBREVIATIONS:
            continue
        return match
    return None


def _ask_question_finding(body):
    """A `Needs Edvard` ask whose first sentence is not the question.

    Edvard, unboarded capture 2026-08-20: *"The 'needs Edvard' blocks needs
    to present the issue in the first line to me as a question. Take the
    block in cycle 273, its a wall of text and a question hidden in it at
    the very bottom ... is it a simple yes and no question? Or something
    else? ... Example is 'yes or no, keep the symbols for x, y, z?' After
    that, you can explain the reason"*.

    So the mechanical half of that is binary and is the half worth
    refusing on: **the first sentence of the ask ends in a question mark.**
    Measured against all 333 live entries before it was written -- 8 carry
    an ask, and 6 of them open with a statement, including the Cycle 273
    block he named (35 words before the first full stop, no question in
    it). The two that pass are the same ask written twice, opening *"Do you
    use ChatGPT Codex against these repos?"* at 8 words. So this check can
    fail and can pass, which the six-out-of-eight split is the evidence
    for -- a rule that every existing document already satisfies would have
    been a rule that pins nothing.

    The other half of his ask -- be *direct*, be *specific*, say whether it
    is yes/no -- is judgement, and a word cap standing in for it would be a
    number I invented rather than measured. That half is written into
    `personality.md` as prose instead, deliberately.
    """
    ask = split_ask(body)[1]
    if not ask:
        return None
    match = _first_sentence_end(ask)
    if match and match.group() == "?":
        return None
    first = ask[: match.end()] if match else ask
    return (
        f"ask: this ask opens with a statement, not a question -- {first!r}. "
        "Edvard has to read to the bottom to find out what is being asked of "
        "him, which is the thing he reported on 2026-08-20. Lead with the "
        "question in one sentence, say whether it is yes/no, and put the "
        "reasoning after it: `**Needs Edvard:** Yes or no, should the status "
        "circles become words like the priorities did? You told me on the "
        "19th that ...`"
    )


def _board_finding(body):
    """A `Board:` field the site cannot turn into a single link.

    Optional, so silence is the common answer and never a finding. But a
    field that is present and unlinkable is the one shape worth refusing:
    `parse_board_refs` leaves anything it cannot place as plain text, on
    purpose, so `Board: #68` renders as the literal characters `#68` and
    looks exactly like a working reference until Edvard taps it. The
    entry is written once and never edited, so this is the last moment
    that is cheap to fix.
    """
    match = _FOOTER_RE.search(body)
    if not match:
        return None  # already reported by `_footer_finding`
    board = (match.group("board") or "").strip()
    if not board:
        return None
    if any(span["kind"] == "link" for span in parse_board_refs(board)):
        return None
    return (
        f"board: `Board: {board}` carries no `idea #N` or `issue #N` the site "
        "can link, so it would render as plain text. Write the word and the "
        "number -- `Board: idea #68`, `Board: issue #71, idea #62` -- or drop "
        "the field, which is optional and means this cycle worked off-board."
    )


def _cycle_finding(name, entry):
    """The heading and the filename disagree about which cycle this is.

    Read off the parsed entry's `cycle`, not its `title` -- `parse_heading`
    classifies each segment of a heading independently and lifts the cycle
    number *out* of the title, so searching the title reports every
    correctly written entry as having no cycle number. Caught by running
    this against the live folder, where the first version failed Cycle
    151's own entry.

    Safe to run on a repaired document as well as a correct one: a
    synthesised heading is built *from* the filename, so it agrees by
    construction, and a promoted one carries the author's own words and
    can genuinely disagree. See the comment at the call site for why
    there is no guard in front of this.
    """
    declared = _FILENAME_CYCLE_RE.match(name)
    if not declared:
        return None
    want = int(declared.group(1))
    found = entry.get("cycle")
    if found == want:
        return None
    return (
        f"cycle: the filename says cycle {want} and the heading says "
        f"{found if found is not None else 'no cycle number'}. The gap "
        "detector counts cycles from the filenames and the cards title "
        "themselves from the headings, so a disagreement puts one cycle's "
        "words under another cycle's name."
    )


# Minutes a heading's stamp may sit ahead of the clock before it is a
# guess rather than a stamp. The entry is written and linted seconds
# apart in the same pod, so the honest tolerance is small; this is wide
# enough that nothing about the order of those two steps can trip it.
STAMP_TOLERANCE_MINUTES = 3


def _stamp_finding(entry, now):
    """The heading claims a time the cycle has not reached yet.

    Two cycles running have stamped a heading from memory instead of
    reading a clock -- Cycle 157 by 34 minutes and Cycle 158 by 20, the
    second while writing down the first as a bug. It is a quiet failure
    in both directions: the feed sorts on these stamps, and the eight-
    cycle report picks which cycles it covers by reading them, so a
    future stamp reorders cards and can pull a cycle into the wrong
    report. Nothing about it looks wrong on the page.

    Two known blind spots, both judged and both left. A heading carrying
    no time at all (`### 2026-08-02 — Cycle 5`, the oldest live shape) is
    skipped, because there is no time to check. And `parse_heading` drops
    the timezone token, so the three live `03:19Z` headings are read as
    Oslo -- which understates how far ahead they are and can therefore
    only ever fail to fire, never fire wrongly. No cycle has written `Z`
    since 2026-08-03.

    Only the future side is checked. An entry stamped *earlier* than now
    is the normal case -- a cycle writes its heading and then spends
    minutes finishing the document -- and there is no honest threshold
    that separates that from a backdated one.
    """
    date, time = entry.get("date"), entry.get("time")
    if not date or not time:
        return None
    try:
        stamped = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        # `_TIME_RE` accepts any `\d{1,2}:\d{2}`, so `25:10` reaches here.
        # Swallowing it would pass exactly the entry this check exists to
        # catch -- a stamp nobody read off a clock.
        return (
            f"stamp: the heading says {date} {time} Oslo, which is not a real "
            "time. Read the clock rather than estimating it "
            "(`TZ=Europe/Oslo date +'%F %H:%M'`)."
        )
    ahead = (stamped.replace(tzinfo=OSLO) - now).total_seconds() / 60
    if ahead <= STAMP_TOLERANCE_MINUTES:
        return None
    return (
        f"stamp: the heading says {date} {time} Oslo, which is {round(ahead)} "
        f"minutes from now. Read the clock rather than estimating it "
        f"(`TZ=Europe/Oslo date +'%F %H:%M'`) -- the feed sorts on this "
        "stamp and the eight-cycle report selects on it."
    )


# The turn clock `bridge/deadline.py` writes at the start of every turn:
# `started_at`, `deadline_at`, `timeout_seconds`, all epoch seconds. It
# lives on the bridge pod's CLAUDE_HOME, which is where this tool runs
# from (`prompt.md` step 7 chains it in a `Bash` call), but nothing in
# this repo can import `bridge.deadline` -- different package, different
# image -- so the path is read directly. A missing file is the normal
# resting state between turns and means no ground truth, not a finding.
DEADLINE_FILE = os.path.join(
    os.environ.get("CLAUDE_HOME", "/data/claude-home"), "turn-deadline.json"
)

_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "half an": 0.5, "half": 0.5,
}
_NUMBER = r"\d+(?:\.\d+)?|" + "|".join(
    sorted((re.escape(w) for w in _WORD_NUMBERS), key=len, reverse=True)
)
# `[\s-]+`, not `*`: a zero-width separator let the typo "amin" parse
# as "a" + "min" and score as a real one-minute claim -- a silent
# mis-score rather than a clean non-match.
_DURATION = rf"(?P<n>{_NUMBER})[\s-]+(?P<unit>minutes?|mins?|hours?|hrs?)"

# Only first-person, present-cycle framings. An entry that says "Cycle 12
# burned ~16 minutes proving that" is reporting history and is not a
# claim about this hour; the whole value of this check is that it fires
# on the sentences the author had no source for.
_ELAPSED_RE = re.compile(
    rf"\b(?:I|this cycle|I have|I've)\s+(?:just\s+)?"
    rf"(?:spent|burned|took|ran for|worked for|have spent|had spent)\s+"
    rf"(?:about|roughly|nearly|almost|around|some|~)?\s*{_DURATION}\b",
    re.IGNORECASE,
)
# The remaining-time pattern needs the same first-person restriction and
# for one revision it did not have one -- the leading group was fully
# optional, so it constrained nothing and fired on any "N minutes left".
# Built from the real corpus rather than guessed: across 295 entries the
# self-framed shapes are all "I had ten minutes left", "I finished the
# code with four minutes left", "I did not build it with twenty minutes
# left", and the ones that must stay silent are all subject-less or about
# some other cycle -- "a cycle with an hour left has every reason to...",
# "it speaks at 15, 8 and 3 minutes remaining", "the previous cycle wrote
# down that it would not ship this with twenty minutes left". An `I` in
# the same clause separates every one of those correctly.
#
# It under-fires on "with twenty minutes left I was not willing", where
# the subject trails the duration. That is the safe direction and it is
# left alone: a check that refuses a true sentence is one this loop
# learns to route around, which costs more than a claim slipping past.
_REMAINING_RE = re.compile(
    rf"\bI\b[^.;\n]{{0,80}}?{_DURATION}\s+(?:left|remaining|to spare)\b",
    re.IGNORECASE,
)

# "the hook warns me when I have 15 minutes left" describes a mechanism,
# not this turn. A subordinator immediately governing the clause is the
# cheapest reliable signal for that; `re` has no variable-width
# lookbehind, so it is checked against the text before the match.
_SUBORDINATOR_RE = re.compile(r"\b(?:when|whenever|if|unless|until)\b", re.IGNORECASE)
_SUBORDINATOR_WINDOW = 30

# How far a claim may sit from the clock before it is a guess. Generous
# on purpose: prose is written minutes before the lint runs, and a check
# that fires on honest rounding is one cycles learn to route around.
ELAPSED_TOLERANCE_MINUTES = 5
REMAINING_TOLERANCE_MINUTES = 10


def _minutes(match):
    raw = match.group("n").lower()
    value = _WORD_NUMBERS.get(raw, None)
    if value is None:
        value = float(raw)
    if match.group("unit").lower().startswith(("hour", "hr")):
        value *= 60
    return value


def read_turn_clock(path=None):
    """`(elapsed_minutes, remaining_minutes)` for the running turn, or `None`.

    `None` means there is no ground truth to check against -- the file is
    absent between turns, and this tool is also run by hand. That is the
    honest answer and it is deliberately not a finding: idea #77 asks for
    mismatches to be flagged and for anything with no available source to
    be left alone.
    """
    try:
        with open(path or DEADLINE_FILE) as handle:
            record = json.load(handle)
        started, ends = record["started_at"], record["deadline_at"]
    except Exception:
        return None
    if not isinstance(started, (int, float)) or not isinstance(ends, (int, float)):
        return None
    now = datetime.now(OSLO).timestamp()
    return ((now - started) / 60, (ends - now) / 60)


# Quoted spans, blanked before matching. Found by running this check
# against the entry announcing it, which quotes both of Cycle 246's
# claims in order to explain them and was therefore refused three times.
# That is not a corner case: the entries most likely to discuss a
# confabulated number are the ones written about confabulated numbers,
# including every Friday retro, so a check that cannot tell "I spent
# half an hour" from `"I spent half an hour"` would be routed around
# within two cycles. Single quotes are deliberately not handled --
# apostrophes are everywhere in this prose and would blank half of it.
_QUOTED_RE = re.compile(
    r"`[^`]*`"          # inline code
    r"|\"[^\"\n]*\""    # straight double quotes
    r"|[“][^”\n]*[”]"  # curly double quotes
    r"|^>.*$",          # blockquote line
    re.MULTILINE,
)


def _strip_quoted(body):
    """Blank quoted spans, preserving length so offsets stay meaningful."""
    return _QUOTED_RE.sub(lambda m: " " * len(m.group(0)), body)


def _clock_findings(body, clock):
    """Elapsed- and remaining-time claims the running clock contradicts.

    Cycle 246 wrote "four minutes left" into a reply and "spent half an
    hour perfecting" a fix into its entry, inside a cycle whose measured
    runtime was 673 seconds. Edvard asked twice how that could be true
    and it is not: both numbers were written as confident prose and
    neither was read off anything. The retro had already prescribed
    "read the clock as a real tool call" two days earlier and issue #72
    had already put a real timestamp in every tool result, so the fix
    that did not hold was the one that relied on remembering.

    The claim this check makes is narrow and worth stating: introspection
    cannot catch a confabulated number, because a confabulated number
    does not feel uncertain from the inside. Comparing it to a file does.
    """
    if clock is None:
        return []
    elapsed, remaining = clock
    body = _strip_quoted(body)
    findings = []
    for match in _ELAPSED_RE.finditer(body):
        claimed = _minutes(match)
        if claimed <= elapsed + ELAPSED_TOLERANCE_MINUTES:
            continue
        findings.append(
            f"clock: {match.group(0).strip()!r} claims {claimed:g} minutes, but "
            f"this turn started {elapsed:.0f} minutes ago -- it cannot have "
            "spent longer than it has existed. Read the elapsed time rather "
            "than estimating it, or drop the number."
        )
    for match in _REMAINING_RE.finditer(body):
        claimed = _minutes(match)
        before = body[max(0, match.start() - _SUBORDINATOR_WINDOW):match.start()]
        if _SUBORDINATOR_RE.search(before):
            continue
        if abs(claimed - remaining) <= REMAINING_TOLERANCE_MINUTES:
            continue
        # A turn past its deadline reports a negative remaining --
        # `deadline.seconds_left` documents that as real, not
        # hypothetical -- and "-20 minutes are left" is not a sentence.
        actual = (
            f"{remaining:.0f} minutes are left of this turn"
            if remaining >= 0
            else f"this turn is {abs(remaining):.0f} minutes past its deadline"
        )
        findings.append(
            f"clock: {match.group(0).strip()!r} claims {claimed:g} minutes, but "
            f"{actual}. Read the clock rather than estimating it, or drop the "
            "number."
        )
    return findings


# `None` is a real answer from `read_turn_clock` -- it means "no ground
# truth", which is the whole point of that check being skippable -- so it
# cannot double as "the caller did not pass one". Using it for both made
# `clock=None` silently read the live bridge file, which is a test that
# passes for the wrong reason in the one direction that matters.
_UNSET = object()


def lint(name, content, now=None, clock=_UNSET):
    """`(filename, text)` -> a list of findings, empty when it renders as written.

    `now` is injected rather than read here so the stamp check is testable
    at a fixed instant; the CLI passes nothing and gets the real clock.
    `clock` is the same arrangement for the turn clock, except that
    `None` is one of its real values -- see `_UNSET`.
    """
    now = now or datetime.now(OSLO)
    clock = read_turn_clock() if clock is _UNSET else clock
    if not (content or "").strip():
        return ["empty: there is nothing in this file to write."]
    path = JOURNAL_DIR + name
    findings = []
    heading = _heading_finding(path, content)
    if heading:
        findings.append(heading)
    # Every later check reads the document the way the site does, which
    # means through the repair -- otherwise a bad heading would report
    # itself a second time as a missing footer, and the cycle would fix
    # one thing and see two.
    normalised = normalise_entry(path, content)
    # One entry document, so there is no preamble to cut off the front --
    # `parse_journal`, the entries-body parser, same as the site and the
    # reply lookup. This used to call the whole-file parser, and a
    # `## Entries` line in
    # an entry's prose therefore cut the entry's own heading off and left
    # nothing to parse. There was a whole rule here refusing such an entry
    # outright; it is gone with the hazard that justified it (runner#135),
    # because the cost of keeping it was refusing a cycle that wrote a true
    # sentence about the append command `prompt.md` step 6 mandates.
    entries = parse_journal(normalised)
    if not entries:
        findings.append(
            "unparseable: the site could not read a single entry out of this "
            "document, even after repair."
        )
        return findings
    entry = entries[0]
    if len(entries) > 1:
        findings.append(
            f"split: this document holds {len(entries)} `### ` headings, so it "
            "would render as that many separate cards. One entry per file."
        )
    raw = _raw_body(normalised)
    footer = _footer_finding(raw)
    if footer:
        findings.append(footer)
    board = _board_finding(raw)
    if board:
        findings.append(board)
    ask = _ask_finding(raw)
    if ask:
        findings.append(ask)
    # Only one of these two can ever fire: `_ask_finding` needs `split_ask`
    # to have found nothing, `_ask_question_finding` needs it to have found
    # something. So an entry that writes the bare label is told about the
    # colon and not also lectured on the shape of an ask the site is going
    # to drop anyway.
    ask_question = _ask_question_finding(raw)
    if ask_question:
        findings.append(ask_question)
    # Runs unconditionally, and that is a measurement rather than an
    # oversight. This started as a blanket "skip when the heading is
    # broken", which hid the wrong cycle number in `## Cycle 153` inside
    # `...-152.md` -- two real, independent defects, so the author fixed
    # the hash count and only then learned the number was wrong. The
    # narrower replacement, skipping only a *synthesised* heading, then
    # survived having the guard deleted entirely: `synthetic_heading`
    # builds the heading out of the filename, so the two agree by
    # construction for every name shape in the folder, and a filename
    # with no cycle token returns above. The guard could not change an
    # answer, so it is gone rather than tested -- an unreachable branch
    # and a blind test look identical in a mutation report and need
    # opposite fixes. The invariant it leaned on is pinned instead, by
    # `test_a_synthesised_heading_cannot_disagree_with_the_filename`.
    cycle = _cycle_finding(name, entry)
    if cycle:
        findings.append(cycle)
    stamp = _stamp_finding(entry, now)
    if stamp:
        findings.append(stamp)
    findings.extend(_clock_findings(raw, clock))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", help="the entry as written, on local disk")
    ap.add_argument(
        "--name",
        help="the filename it will be written under, if it differs from the "
        "local one (e.g. 168-cycle-152.md)",
    )
    args = ap.parse_args(argv)
    try:
        with open(args.file, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        print(f"lint_entry: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    name = args.name or args.file.rsplit("/", 1)[-1]
    findings = lint(name, content)
    if not findings:
        print(f"lint_entry: {name} renders as written.")
        return 0
    # Not "would be repaired by the site": that was true of every check
    # this tool had when it was written, and the stamp check is the
    # first one whose finding the site cannot repair -- a heading dated
    # ahead of the clock renders exactly as written and sorts wrongly.
    print(f"lint_entry: {name} should not be written as it stands:", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
