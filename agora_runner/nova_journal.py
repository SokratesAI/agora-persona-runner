"""Parsers turning Nova's two vault markdown files into render-ready data.

Kept apart from nova_site.py (which serves them) because this half is
pure: text in, dicts out, no network. That is deliberate -- the journal
has accumulated four mutually incompatible heading formats over 49
cycles and every one of them is still in the file, so this is the part
that actually needs tests.

The four, oldest to newest, all of which appear in the live file today:

    ### 2026-08-02 — Cycle 5
    ### 2026-08-03 03:19Z — Cycle 6, closing status (two lines, ...)
    ### Cycle 29 — 2026-08-05, 14:10 Oslo — I stopped at 8% and banked ...
    ### 2026-08-09 04:20 (Oslo) — Cycle 49

So nothing here pattern-matches a whole heading. Each em-dash-separated
segment is classified independently as date/time metadata, a cycle
number, or prose -- which is why a heading carrying no cycle number at
all (`### 2026-08-02 — the owner's first message (not a cycle)`) still
parses into a renderable entry instead of being dropped.
"""

import math
import re
from datetime import datetime, timezone

from agora_runner.config import OSLO
from agora_runner.nova_uploads import ATTACHMENT_PATTERN

JOURNAL_PATH = "projects/sokrates/projects/agora/nova/journal.md"
# One document per entry, which is where entries live as of 2026-08-09.
# JOURNAL_PATH is the frozen archive it was split out of. It was read as a
# fallback until 2026-08-13, so the two orderings of "migrate" and "deploy"
# both worked; the file was emptied on 2026-08-10, which left the fallback
# able to return only zero entries on the one branch that reached it, so it
# is gone -- see `journal_markdown` in nova_sources.py. Nothing on any
# hourly path reads JOURNAL_PATH now; `tools/split_journal.py` is the last
# caller and only ever ran once.
JOURNAL_DIR = "projects/sokrates/projects/agora/nova/journal/"
DIGEST_PATH = "projects/sokrates/projects/agora/journal-digest.md"
# The digest lines that have rolled off the live file. Same split as the
# journal above and for the same reason: DIGEST_PATH grew to 100KB of
# which 97KB was 54 old digest lines, and a Nova cycle has to read the
# whole thing every hour to get the two short sections at the top. The
# site reads both and concatenates, so nothing a card ever showed
# disappears -- see `digest_markdown` in nova_sources.py. This one lives
# under `resources/` because it is ours; The owner opens DIGEST_PATH.
DIGEST_ARCHIVE_PATH = "projects/sokrates/projects/agora/nova/resources/digest-archive.md"

_ENTRY_HEADING_RE = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# No trailing \b: `03:19Z` is one of the real heading formats, and a word
# boundary between "19" and "Z" does not exist.
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}")
_CYCLE_RE = re.compile(r"\bCycle[ \t]+(\d+)", re.IGNORECASE)
_TZ_TOKEN_RE = re.compile(r"\boslo\b|\bZ\b", re.IGNORECASE)
_SEGMENT_SPLIT_RE = re.compile(r"[ \t]+—[ \t]+")
# The one rigid part of an entry (personality.md): a `---` rule, then the
# PR and outcome on one line.
#
# The rule is optional, and that is the whole of a bug the owner could see.
# The review rubric asks every entry to carry a `Reviewer: n findings`
# line, and Cycle 104 wrote it where the rule used to sit -- so the
# entry ended `Reviewer: ...\nPR: #88 | Outcome: merged` with no `---`
# anywhere, the search failed, and its card on the site showed no PR and
# no outcome for a cycle that had merged one. An entry is written once
# and never edited, so the parser is the only end of this that can move.
#
# What actually keeps this off a `---` used as an ordinary rule mid-entry
# is the `$`, not the rule: with no re.MULTILINE it anchors to the end of
# the entry, so the only `PR: ... | Outcome: ...` line that can match is
# the last one. That anchor was always doing the work -- a mutation run
# (2026-08-09) showed as much. It also stops `outcome` from matching a
# single character, since `.+?` is non-greedy: without it `Outcome:
# merged` parses as `m`.
#
# This comment used to add "and adding re.DOTALL here changes nothing at
# all", which was true only while the rule was mandatory and is false now.
# A reviewer demonstrated it: with re.DOTALL, an entry that quotes the
# footer format mid-prose and then ends with its real footer matches the
# *quoted* one, and `outcome` swallows everything from there to the end of
# the entry. No live entry does that today. Do not add the flag.
#
# `Board:` is the third field and the only optional one (the owner, ideas.md
# #68: "Journal cards in Nova should mark the issue or idea number they
# worked on like they do with the prs. With links."). It sits between the
# other two rather than after them because `outcome` is anchored to `$`
# and every entry ever written ends on it; a field after `Outcome:` would
# be swallowed by that group instead of parsed. Optional because 197
# entries predate it and none of them can be rewritten -- rule 3 -- and
# because a cycle that did not work from the board should say nothing
# rather than invent a number.
_FOOTER_RE = re.compile(
    r"\n(?:-{3,}[ \t]*\n)?PR:[ \t]*(?P<pr>.+?)[ \t]*\|[ \t]*"
    r"(?:Board:[ \t]*(?P<board>.+?)[ \t]*\|[ \t]*)?"
    r"Outcome:[ \t]*(?P<outcome>.+?)[ \t]*$",
    re.IGNORECASE,
)
# The repair side of `_FOOTER_RE`, and deliberately a separate pattern:
# that one is anchored to the end of the entry on purpose and must stay
# that way. This one finds a footer *wherever* the cycle put it and
# whether or not it bolded it; `stray_footer` decides whether moving it
# is safe.
_STRAY_FOOTER_RE = re.compile(
    r"\A[ \t]*\*{0,2}[ \t]*PR:[ \t]*(?P<pr>.+?)[ \t]*\|[ \t]*"
    r"(?:Board:[ \t]*(?P<board>.+?)[ \t]*\|[ \t]*)?"
    r"Outcome:[ \t]*(?P<outcome>.+?)[ \t]*\*{0,2}[ \t]*\Z",
    re.IGNORECASE,
)
# Not the `_FENCE_RE` further down this file: that one has no capture
# group and is bound *after* this line, so naming this one the same
# silently won the binding and every lookup here raised `no such group`.
# The tests caught it; the collision is worth a sentence because the
# failure was nowhere near the cause. Backticks and tildes both, since a
# closing marker has to match its opening one.
_OPEN_FENCE_RE = re.compile(r"\A[ \t]{0,3}(`{3,}|~{3,})")
_RULE_ONLY_RE = re.compile(r"\A[ \t]*-{3,}[ \t]*\Z")


def stray_footer(body):
    """One entry body -> `(body, pr, board, outcome)`, misplaced footer lifted.

    `personality.md` asks for one rigid line at the very end of an entry:
    `PR: ... | Outcome: ...`, bare, under a `---`. Three of the 165 live
    entries do not have it there, and the result is not a parse error --
    their cards render with no PR badge and no outcome at all, which
    reads as a cycle that shipped nothing. Cycles 146 and 147 wrote it
    **bolded, directly under the heading**; entry 004 wrote it correctly
    and hard-wrapped it, so `_FOOTER_RE`'s `$` lands on the continuation.

    The fix people reach for first is loosening `_FOOTER_RE`, and it is
    the wrong one -- its `$` is what stops a `PR: ...` line quoted
    mid-prose from being read as the entry's real outcome, and a reviewer
    has already demonstrated the damage when that anchor gives way. So
    the strict rule stays and the odd shapes are repaired in front of it.

    Three conditions, and each one is refusing to guess:

    - the caller only asks when `_FOOTER_RE` found nothing. An entry that
      ends correctly is never touched, whatever else it contains.
    - candidates inside a fenced code block do not count. `personality.md`
      states the footer format *as* a fenced block, so an entry quoting it
      is a thing a cycle would plausibly write, and a badge invented out
      of an example is worse than no badge.
    - exactly one candidate, or nothing moves. Two means the document is
      making two claims and picking one is inventing an answer.

    A candidate is a whole *paragraph*, not a line, and that is not
    tidiness -- it is the difference between repairing entry 004 and
    corrupting it. A line-at-a-time version of this, run against the live
    folder before any of it was written, matched that entry's first line,
    moved it, and left `survives; next cycle should merge it...` dangling
    at the end of the body under a badge whose outcome stopped
    mid-sentence. Joining the paragraph gives the whole outcome back.
    """
    lines = body.split("\n")
    fenced = [False] * len(lines)
    fence = None
    for index, line in enumerate(lines):
        marker = _OPEN_FENCE_RE.match(line)
        fenced[index] = True if marker else fence is not None
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else (fence or token)

    blocks = list(_paragraphs(lines))
    if not blocks:
        return body, "", "", ""
    ends = (blocks[0][0], blocks[-1][1])

    hits = []
    for start, end in blocks:
        if any(fenced[start:end]):
            continue
        # First paragraph or last, never the middle. The fence guard alone
        # is not enough and a reviewer proved it: `personality.md` states
        # the footer format as a *quotable* example, and this journal
        # quotes rule text as plain unfenced paragraphs constantly, so an
        # entry explaining the rule and then forgetting its own footer got
        # `#23 (or "none")` promoted to a badge -- the exact "invented out
        # of an example" failure this function claims to refuse.
        #
        # An end is where a footer can honestly be. All three live repairs
        # are at one: 146 and 147 wrote it as the opening paragraph, 004
        # as the closing one. A footer-shaped paragraph with prose on both
        # sides of it is somebody quoting the format.
        if start != ends[0] and end != ends[1]:
            continue
        # The footer's own `---` rule shares the paragraph when no blank
        # line separates them, which is how it is actually written.
        head = start + 1 if _RULE_ONLY_RE.match(lines[start]) else start
        if head >= end:
            continue
        match = _STRAY_FOOTER_RE.match(" ".join(lines[head:end]).strip())
        if match:
            hits.append((start, end, match))
    if len(hits) != 1:
        return body, "", "", ""
    start, end, match = hits[0]
    rest = "\n".join(lines[:start] + lines[end:]).strip()
    return rest, match.group("pr"), match.group("board") or "", match.group("outcome")


def _paragraphs(lines):
    """Line indices of each blank-line-separated block, as `(start, end)`."""
    start = None
    for index, line in enumerate(lines):
        if line.strip():
            start = index if start is None else start
        elif start is not None:
            yield start, index
            start = None
    if start is not None:
        yield start, len(lines)


_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_DIGEST_LINE_RE = re.compile(
    r"^\*\*Cycle[ \t]+(?P<cycle>\d+)\*\*[ \t]*\((?P<at>[^)]*)\)[ \t]*—[ \t]*(?P<text>.*)$",
    re.DOTALL,
)
# One card per cycle, and a blank line is not what decides where a card
# ends -- a `**Cycle N** (` at the start of a line does.
#
# The digest is hand-written by Nova every hour and one cycle dropped the
# blank line between its line and the one below it. Splitting on blank
# lines alone, that is not a formatting slip: the two paragraphs become
# one, `_DIGEST_LINE_RE` matches only the first `**Cycle N**` and swallows
# the second into its `text` (the regex is DOTALL). So Cycle 65 had no
# card on the site at all, and Cycle 66's card ended in Cycle 65's closing
# sentence -- for a day, unnoticed, with every test green. Found
# 2026-08-10 by a reviewer subagent reading this parser against the live
# file, not by the cycle that wrote the file.
#
# The lookahead keeps the blank-line split as well, so a paragraph that
# is not a digest line (the section's own prose) still separates normally.
_DIGEST_SPLIT_RE = re.compile(r"\n[ \t]*\n|\n(?=\*\*Cycle[ \t]+\d+\*\*[ \t]*\()")


def split_digest_entries(text):
    """Where one digest line ends and the next begins -- the only answer.

    Exported rather than kept private because `tools/roll_digest.py` has
    to agree with this file about where a card ends, and for a while it
    did not: it split on blank lines alone, which is the rule the comment
    above records as already broken. The two statements of one rule then
    drifted in the direction that hides itself. A digest holding two cards
    written without a blank line between them shows both on the site and
    counts as *one* entry to the roller, so a file of 14 real cards looks
    like 12, sits under the keep-12 cap, and rolls nothing -- no error, no
    output, the file the owner reads growing forever, which is the single
    thing that script exists to prevent. Measured before this was moved:
    exactly that, silently.

    So there is one splitter now, and `parse_digest` below is a caller of
    it rather than the owner of it. Anything that needs to know where a
    digest line ends imports this; nothing re-derives it.
    """
    return [p.strip() for p in _DIGEST_SPLIT_RE.split(text) if p.strip()]


# What "Needs Edvard" says when it has nothing in it. Item 3 of idea #34  (not-prose: quoting a literal)
# wants that section completely invisible rather than showing the word
# "Nothing", so the emptiness test lives here next to the parsing.
#
# Emphasis is stripped before the comparison, and that is the whole of the
# bug the owner reported on 2026-08-09 -- "the 'needs the owner' box should not
# show when nothing is expected". The section was compared literally, so
# `Nothing.` counted as empty and `**Nothing.**` counted as a live claim
# on his attention. Every cycle writes the bold one, because bold is the
# house style for that section, so the box had never once been correctly
# hidden since it shipped.
_EMPTY_NEEDS = ("", "nothing", "none")
_EMPHASIS_RE = re.compile(r"[*_`]")


def is_empty_needs(text):
    """True if the `Needs Edvard` section is asking for nothing."""  # not-prose: quoting a literal
    plain = _EMPHASIS_RE.sub("", text or "").strip().lower()
    return plain.rstrip(".").strip() in _EMPTY_NEEDS


def split_needs_items(text):
    """The `Needs Edvard` body -> one string per ask, in file order.  # not-prose: quoting a literal

    Blank-line separated paragraphs, because that is the shape every cycle
    has written since the section existed. Deliberately not
    `split_digest_entries`: that splitter knows where a `**Cycle N**` line
    ends, and these items carry no cycle number.
    """
    return [p.strip() for p in re.split(r"\n[ \t]*\n", text or "") if p.strip()]


def needs_items(text):
    """The asks actually waiting on him -- empty when the block says so.

    A section that only says `**Nothing**` holds no items, and reading it
    as one puts a "clear this" button on the word Nothing.
    """
    return [] if is_empty_needs(text) else split_needs_items(text)


# The owner, comments board 2026-08-16: "the solution i want is to remove the
# 'needs the owner' block entirely. If you need something from me, it should be
# added in the Journal card somehow and i'll answer in the comment of a
# journal card. [...] add a new yellow block below the title or somehow
# higlight your issue so that i see it."
#
# So an ask now belongs to the cycle that raised it, written as one paragraph
# of that cycle's own entry, and the answer lands in that card's existing
# comment thread. That is what fixes the failure mode the block had: it was a
# shared list nobody owned, rewritten from scratch every cycle by an author
# who had not written the items in it, so keeping an item cost nothing and
# removing one required being sure. An entry document is written once and
# never edited, so an ask cannot silently outlive its answer here -- it scrolls
# away with its own card.
# The colon is required, and that is the whole difference between a label
# and a mention. Cycles 11 and 12 predate this convention and write about
# the old digest section in ordinary prose -- "**Needs Edvard**, **Next  # not-prose: quoting a literal
# cycle**, and a one-line-per-cycle **Digest**" -- at the start of a line,
# which an optional colon matched. Both parsed as open asks, and because
# they are the oldest cards in the corpus the header's "waiting on you"
# pill pointed at 2026-08-11 instead of at the live one (Cycle 261 shipped
# the pill and found this in the live payload; Cycle 262 fixed it here).
#
# Requiring the colon rather than adding an age cutoff is not a guess.
# Across all 315 entries, every one of the five real asks writes
# `**Needs Edvard:**` and every bare `**Needs Edvard**` is prose naming  # not-prose: quoting a literal
# the section. A horizon would have hidden these two and still have let
# the next such sentence through.
# **Two labels, forever.** The owner, unboarded capture 2026-08-21: *"Change
# the 'needs the owner' to 'needs input'."* New entries write `**Needs
# input:**`; the 363 entries already written say `**Needs Edvard:**` and  # not-prose: quoting a literal
# are never edited, so dropping the old spelling would unrender every ask
# in the archive. This is the one place the alternation is defined --
# `tools/lint_entry` imports it rather than restating it, because a
# heading matcher defined per module is the duplication shape `prompt.md`
# step 2 tells me to stop creating.
ASK_LABEL = r"Needs (?:Edvard|input)"

_ASK_RE = re.compile(
    r"^\*\*" + ASK_LABEL + r"(?::\*\*|\*\*:)[ \t]*(?P<ask>.*?)(?=\n[ \t]*\n|\Z)",
    re.MULTILINE | re.DOTALL,
)


def split_ask(body):
    """An entry body -> (body without the ask paragraph, the ask text).

    The ask is one paragraph opening `**Needs Edvard:**`. It is cut out of  # not-prose: quoting a literal
    the body rather than left in place because the card renders it above the
    brief, and an entry that printed it in both would be the wall of text
    this replaced.

    Only the first is taken. A cycle with two genuinely separate asks should
    write one paragraph -- the block's whole failure was that a list of asks
    is easy to add to and hard to clear, and reproducing the list inside the
    card would reproduce that.
    """
    match = _ASK_RE.search(body or "")
    if not match:
        return body or "", ""
    remainder = (body[: match.start()] + body[match.end():]).strip()
    ask = " ".join(match.group("ask").split())
    # The label with nothing after it is asking nothing -- but the paragraph
    # still has to go. Returning the untouched body here put `**Needs
    # the owner:**` in `_first_paragraph`, so the card's one-line brief read as
    # the label and the entry's real opening sentence never appeared.
    return remainder, ask


# The owner, on the comments board at Cycle 156: "every 8 cycles (at 06:00,
# 14:00 & 22:00) I want a report like you just did for the last 8 cycles.
# They should appear like a journal card, but stand out in both color and
# form to show that they are just summaries."
#
# A report is a journal document like any other, so the feed already
# carries it -- what it needs is to say so. The declaration is the whole
# heading segment and nothing less: an anchored match on a fixed shape,
# on a heading rather than on prose, and only for an entry that carries no
# cycle number of its own. Every lesson in this file about guessing from
# text is about *bodies*, which are free prose; a heading is already
# parsed for structure. A cycle whose entry is titled "Report on the last
# eight cycles" is still an ordinary card, because that is not this shape.
_REPORT_TITLE_RE = re.compile(r"\AReport[ \t]+·[ \t]+Cycles[ \t]+\d+[–-]\d+\Z")
REPORT_EMOJI = "📋"

#: The heading a silence marker declares itself with -- `cycle_stub` writes
#: it, `parse_heading` reads it back. One constant rather than a string in
#: each module, because a marker that stops matching stops being excluded
#: from the silence measure and quietly disarms the stall notice.
SILENCE_TITLE = "Silence · a heartbeat run failed before it could write"
_SILENCE_TITLE_RE = re.compile(r"\ASilence[ \t]+·[ \t]+")
SILENCE_EMOJI = "🔇"


def _is_metadata_only(segment):
    """True if a heading segment carries only a date/time and punctuation."""
    rest = _DATE_RE.sub("", segment)
    rest = _TIME_RE.sub("", rest)
    rest = _TZ_TOKEN_RE.sub("", rest)
    return not re.search(r"[A-Za-z0-9]", rest)


def parse_heading(heading):
    """Split one `### ...` line into date, time, cycle number and title.

    Any of the four may be absent; a heading with no cycle number is a
    real entry (the owner's own message, Cycle 6's addendum) and gets
    `cycle: None` rather than being skipped.
    """
    date_match = _DATE_RE.search(heading)
    time_match = _TIME_RE.search(heading)
    cycle_match = _CYCLE_RE.search(heading)

    prose = []
    for segment in _SEGMENT_SPLIT_RE.split(heading):
        segment = segment.strip()
        if not segment or _is_metadata_only(segment):
            continue
        segment = _CYCLE_RE.sub("", segment, count=1).strip()
        # Leftover separators once the cycle number is lifted out of a
        # segment it shared with prose: `Cycle 30, postscript`.
        segment = segment.strip(" \t,–—-")
        # Cycles 1-19 suffixed every heading with the persona name back
        # when there was more than one persona. It is not a title.
        if not segment or segment.lower() == "(nova)":
            continue
        prose.append(segment)

    cycle = int(cycle_match.group(1)) if cycle_match else None
    title = " — ".join(prose)
    return {
        "cycle": cycle,
        "date": date_match.group(0) if date_match else "",
        "time": time_match.group(0) if time_match else "",
        "title": title,
        # "cycle", "report" or "silence" -- the card's shape, decided here so that
        # every consumer of an entry gets the same answer. See
        # `_REPORT_TITLE_RE` for why the declaration is safe to read off a
        # heading when it would not be safe to read off a body.
        "kind": _kind(cycle, title),
    }


def _kind(cycle, title):
    """`"cycle"`, `"report"` or `"silence"` -- the card's shape.

    A silence marker is written by the runner, not by a cycle, and carries
    no cycle number for the same reason a report does not: the cycle it is
    about never got far enough to have one. It is a separate kind rather
    than a report because the two are excluded from different things --
    a report is kept out of the header, a marker is kept out of the
    *silence measure*, and folding them together would mean a report
    could no longer end a stall (which it should, since a cycle wrote it).
    """
    if cycle is not None:
        return "cycle"
    if _REPORT_TITLE_RE.match(title):
        return "report"
    if _SILENCE_TITLE_RE.match(title):
        return "silence"
    return "cycle"


# The renderer's own answer to "does this entry have a title", ported
# from `cleanTitle` in `nova_public/app.js`. `parse_heading`'s raw
# `title` is *not* that answer: `Cycle 91 (2026-08-10 22:00)` leaves
# `(2026-08-10 22:00)` behind, which is non-empty and which the card
# then renders as nothing at all. Anything deciding whether a card will
# be labelled has to ask this, not the raw field.
#
# **This is a hand-copy of JavaScript and nothing enforces that it stays
# one.** `test_clean_title_matches_the_renderer` pins the four rules
# against the literal source of `app.js`, which is the cheapest drift
# check available from Python; if that test starts failing, the JS moved
# and this did not.
_CLEAN_TITLE_LEAD_RE = re.compile(r"^[\s·—–-]+")
_CLEAN_TITLE_STAMP_RE = re.compile(r"\(\s*\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?\s*\)")
_CLEAN_TITLE_WRAPPED_RE = re.compile(r"^\([^()]*\)$")


def clean_title(title):
    """`parse_heading`'s raw title -> the text the card actually shows.

    Empty means the card is labelled by nothing. Mirrors `cleanTitle` in
    `app.js` rule for rule, including the order they are applied in: the
    leading separator run goes first, then whole parenthesised stamps
    anywhere in the string, then a pair of parentheses wrapping *all* of
    what is left (`(addendum)` loses them; `a fix (and the bug under it)`
    keeps them), then the leading run again.
    """
    text = _CLEAN_TITLE_LEAD_RE.sub("", str(title or ""))
    text = _CLEAN_TITLE_STAMP_RE.sub("", text).strip()
    if _CLEAN_TITLE_WRAPPED_RE.match(text):
        text = text[1:-1].strip()
    text = _CLEAN_TITLE_LEAD_RE.sub("", text).strip()
    return text[:1].upper() + text[1:] if text else ""


_OUTCOME_SPLIT_RE = re.compile(r"^(?P<label>[A-Za-z-]+(?:[ \t]+[A-Za-z-]+)?)[ \t]*(?P<detail>[(,—-].*)?$", re.DOTALL)


def split_outcome(outcome):
    """`Outcome:` -> the word for the badge, and the rest as detail.

    Five of the 51 live entries qualify their outcome in prose --
    `stuck — CI outage, merged nothing`, `shipped (vault-only: issues #34
    boarded and done, ...)`. All of it is true and none of it fits in an
    uppercase pill, so the qualifier is separated out rather than
    truncated away: the badge shows the word, the detail sits beside it.
    Nothing is dropped, which is the whole point.
    """
    outcome = (outcome or "").strip()
    if not outcome:
        return "", ""
    match = _OUTCOME_SPLIT_RE.match(outcome)
    if not match:
        return outcome, ""
    label = match.group("label").strip()
    detail = (match.group("detail") or "").strip().lstrip("(,—- ").rstrip(")").strip()
    return label, detail


# The owner, ideas.md 2026-08-09: "I actually see that The hashtag to the prs
# are listed, but they are not clickable. Maybe leave it as is, but make
# the listed prs be clickable links."
#
# "Leave it as is" is the constraint that shapes this. The `PR:` footer is
# free text and the live file holds 47 distinct shapes of it -- `#28`,
# `agora#45`, `runner#58, runner-config#6, platform-config#490`,
# `#38 (runner) + bridge#11`, `#40 (SokratesAI/agora)`, `#49, #50 (both
# merged)`. So this linkifies the references it recognises and leaves
# every other character exactly where it stood; it never reformats,
# reorders or drops any of the field. A reference it cannot place stays
# plain text rather than becoming a confidently wrong link.
_ORG = "SokratesAI"
# A bare `#12` is the runner: it is this repo, and it is what every entry
# written before the loop touched a second repo meant.
_DEFAULT_REPO = "agora-persona-runner"
_REPO_ALIASES = {
    "runner": "agora-persona-runner",
    "bridge": "agora-claude-bridge",
    "agora": "agora",
    # Bare `config` appears once, in Cycle 6's `#32, #31, config#2`, and
    # the runner's config repo is what it meant.
    "config": "agora-persona-runner-config",
    "runner-config": "agora-persona-runner-config",
    "bridge-config": "agora-claude-bridge-config",
    "platform-config": "platform-config",
    "agora-config": "agora-config",
    "agora-persona-runner": "agora-persona-runner",
    "agora-persona-runner-config": "agora-persona-runner-config",
    "agora-claude-bridge": "agora-claude-bridge",
    "agora-claude-bridge-config": "agora-claude-bridge-config",
}
_PR_REF_RE = re.compile(r"(?P<repo>[A-Za-z][A-Za-z0-9-]*)?#(?P<num>\d+)")
# A parenthetical naming the repo, which three entries use instead of a
# prefix: `#38 (runner)`, `#40 (SokratesAI/agora)`. It is only ever
# consumed when it resolves to a real repo, so `#51 (merged)` and
# `#49 (open)` keep their qualifier as text and fall back to the default.
_PR_QUALIFIER_RE = re.compile(r"^[ \t]*\((?P<name>[A-Za-z][A-Za-z0-9._/-]*)\)")


def _repo_url(name):
    """An alias or an `Owner/Repo` -> the GitHub path, or None if unknown."""
    if not name:
        return None
    if "/" in name:
        return name
    alias = _REPO_ALIASES.get(name.lower())
    return f"{_ORG}/{alias}" if alias else None


def parse_pr_refs(pr):
    """The `PR:` field -> spans, every `repo#123` in it carrying a url.

    Same span shape as `render_inline`, for the same reason: app.js builds
    every node with textContent, so a link has to arrive as structured data
    with its href already separated out. There is no path by which the
    vault's text can become markup.
    """
    text = pr or ""
    spans = []
    cursor = 0
    for match in _PR_REF_RE.finditer(text):
        if match.start() < cursor:  # already inside a consumed qualifier
            continue
        prefix = match.group("repo")
        repo = _repo_url(prefix)
        if prefix and repo is None:
            continue  # an unrecognised prefix: leave the whole thing as text
        end = match.end()
        if repo is None:
            qualifier = _PR_QUALIFIER_RE.match(text[end:])
            retarget = _repo_url(qualifier.group("name")) if qualifier else None
            if retarget:
                repo, end = retarget, end + qualifier.end()
            else:
                repo = f"{_ORG}/{_DEFAULT_REPO}"
        if match.start() > cursor:
            spans.append({"kind": "text", "text": text[cursor:match.start()]})
        spans.append(
            {
                "kind": "link",
                "text": text[match.start():end],
                "url": f"https://github.com/{repo}/pull/{match.group('num')}",
            }
        )
        cursor = end
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


# The `Board:` field -> the same span shape, with one difference that is
# the whole reason it is a second function rather than an argument: the
# href is *internal*. An idea number is a row on the owner's own board,
# which this app already renders at `/ideas` and `/ideas#68`, so the link
# stays inside the PWA instead of opening a browser tab at GitHub.
#
# It follows `parse_pr_refs`'s rule about not guessing, and applies it
# harder. A bare `#68` is deliberately left as plain text: in the `PR:`
# field a bare number has one meaning (this repo), but here it could be
# either board, and the two are different pages. So the word is required.
# Written `idea #68` or `issue #71`, comma-separated, plural tolerated.
_BOARD_REF_RE = re.compile(r"\b(?P<kind>idea|issue)s?[ \t]*#(?P<num>\d+)", re.IGNORECASE)


def parse_board_refs(board):
    """The `Board:` field -> spans, every `idea #68` carrying an app path."""
    text = board or ""
    spans = []
    cursor = 0
    for match in _BOARD_REF_RE.finditer(text):
        page = "/ideas" if match.group("kind").lower() == "idea" else "/issues"
        if match.start() > cursor:
            spans.append({"kind": "text", "text": text[cursor:match.start()]})
        spans.append(
            {
                "kind": "link",
                "text": match.group(0),
                "url": f"{page}#{match.group('num')}",
            }
        )
        cursor = match.end()
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


# The owner, issues.md 2026-08-09: "Would be fun to use some emojis to
# represent what was done that cycle."
#
# Derived from the text rather than written into each entry's footer,
# because the ask covers the 57 entries that already exist and none of
# them can be rewritten -- `## Entries` is append-only, which is rule 3.
# A footer field would only ever emoji the future.
#
# This is a scanning aid, not a classifier. There is no ground truth for
# "what cycle 43 was about", so it was tuned by reading its output over
# all 57 live entries until nothing was misleading -- which is a weaker
# claim than correct, and worth saying out loud rather than implying.
#
# The siren is deliberately not in the table below. An outage is a
# severity, not a topic: a cycle that spent two hours down also talks
# about pods and manifests, and on plain scoring the infrastructure
# vocabulary wins -- Cycles 53 and 54 both came out as routine wrench
# work, which is a materially misleading thing to show someone scanning
# for what went wrong. So it overrides instead.
#
# It matches the opening paragraph only, and deliberately omits the
# obvious words "incident", "down for" and "killed mid". This corpus is
# relentlessly self-referential -- every entry narrates the previous
# cycle's failures -- so "mentions an outage" and "was an outage" share
# a vocabulary entirely. Over whole bodies, 17 of 57 entries fire; over
# the opening, where a cycle says what *it* did, 5 do.
_INCIDENT_RE = re.compile(
    r"outage|oomkill|crashloop|admission rejected|broke the bridge"
    r"|no cycle ran|was already dying"
)
_INCIDENT_EMOJI = "🚨"
_TOPIC_EMOJI = (
    ("🔒", r"\brbac\b|clusterrole|networkpolicy|forbidden|credential|secret|tailnet|gitleaks"),
    ("🌐", r"\bsite\b|\bpwa\b|website|frontend|browser|capture box|app\.js|service worker"),
    ("💓", r"heartbeat|cron|schedule|cadence|poll loop"),
    ("⚙️", r"kubernetes|kubectl|manifest|argocd|deploy|namespace|limitrange|\bpod\b"),
    ("🧠", r"identity\.md|personality\.md|prompt\.md|playbook|constitution|how to work"),
    ("📊", r"quota|token cost|cost table|memory\.peak|measured|metrics|percent"),
    ("📓", r"journal|digest|vault|obsidian|issues\.md|ideas\.md"),
    ("🧪", r"mutation|pytest|test suite|\bci\b"),
)
_DEFAULT_EMOJI = "🔧"


def _haystack(body, title=""):
    """The text an entry is scored against, opening paragraph weighted x3.

    This journal's voice always leads with the outcome ("The site stops
    going down every time I run"), so the opening is a far better signal
    than a keyword mentioned once in passing four paragraphs down.
    """
    opening = (body or "").split("\n\n", 1)[0]
    return "\n".join([title or "", opening, opening, body or ""]).lower()


def assign_emoji(entries):
    """Give every entry an `emoji`, scoring topics against the whole corpus.

    Raw keyword frequency does not work here, and the way it fails is the
    lesson from Cycle 54 wearing a different costume. Every cycle writes a
    journal, reads the digest and touches the vault -- so "journal",
    "digest" and "vault" appear in all 57 entries and discriminate between
    exactly none of them. Scored naively they won 18 entries outright,
    including ones about outages and heartbeats. A term that matches
    everything tells you nothing about which thing you are looking at.

    So each topic is weighted by how rare it is across the corpus:
    `log(1 + N/df)`. A topic present in every entry keeps a small weight
    rather than zero (with one entry, df == N, and zeroing would leave
    every corpus of one undecidable), while a topic appearing in three
    entries counts for several times as much per match.
    """
    entries = list(entries)
    haystacks = [_haystack(e.get("body", ""), e.get("title", "")) for e in entries]
    total = len(entries)
    weights = []
    for _, pattern in _TOPIC_EMOJI:
        found = sum(1 for text in haystacks if re.search(pattern, text))
        weights.append(math.log(1 + total / found) if found else 0.0)

    for entry, text in zip(entries, haystacks):
        # A report is about eight other cycles, so its text is a mixture of
        # all of their topics and the scorer would pick whichever of them
        # happened to be loudest -- a different emoji every eight hours for
        # a card that is always the same kind of thing.
        if entry.get("kind") == "report":
            entry["emoji"] = REPORT_EMOJI
            continue
        # Same argument as the report above, one step further: a marker's
        # body is a sentence about the runner failing, so the scorer would
        # give every one of them whatever topic that sentence happens to
        # hit. They are all the same kind of thing and should look it.
        if entry.get("kind") == "silence":
            entry["emoji"] = SILENCE_EMOJI
            continue
        opening = (entry.get("body", "") or "").split("\n\n", 1)[0].lower()
        if _INCIDENT_RE.search(opening):
            entry["emoji"] = _INCIDENT_EMOJI
            continue
        best, best_score = _DEFAULT_EMOJI, 0.0
        for (emoji, pattern), weight in zip(_TOPIC_EMOJI, weights):
            score = len(re.findall(pattern, text)) * weight
            if score > best_score:
                best, best_score = emoji, score
        entry["emoji"] = best
    return entries


#: A run of letters long enough to be a word. One letter is not: `06:5x`
#: is a mistyped minute, not prose, and it is the reason this is `{2,}`
#: rather than `_is_metadata_only`'s "no alphanumerics at all".
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _is_stamp_paragraph(text):
    """True if a paragraph carries a timestamp and no words.

    Many entries open on their own clock line -- `2026-08-27, 06:22-06:45
    Oslo` -- before the first sentence. That line is not a label, and the
    card is labelled by the brief: the owner, `issues.md` 2026-08-27,
    rated Immediately, *"Cycles has no text title anymore."* Cycles 521
    and 522 both opened this way and both drew a bare date where their
    headline belonged, because `hasBrief` in `app.js` suppresses the
    heading title whenever a brief exists and this counted as one.

    Deliberately not `_is_metadata_only`, which `parse_heading` uses:
    that one refuses any alphanumeric left over, and Cycle 522's line
    read `06:42-06:5x Oslo, 2026-08-27.` -- a typo'd minute leaves an
    `x` behind and the heading test would call it prose. A single
    stranded letter is not a word.
    """
    rest = _DATE_RE.sub("", text or "")
    rest = _TIME_RE.sub("", rest)
    rest = _TZ_TOKEN_RE.sub("", rest)
    return not _PROSE_WORD_RE.search(rest)


def _first_paragraph(body):
    """An entry body -> its opening paragraph as one unwrapped line.

    Skips a leading bullet or fenced block so an entry that opens with a
    list still briefs from its first real prose, and a leading clock line
    for the same reason -- see `_is_stamp_paragraph`. Lines are joined
    with a space for the same reason `render_blocks` does it: the journal
    is hard-wrapped, and the wrap is not a sentence boundary.
    """
    for chunk in re.split(r"\n[ \t]*\n", body or ""):
        lines = [line.strip() for line in chunk.strip().split("\n") if line.strip()]
        if not lines or _BULLET_RE.match(lines[0]) or _FENCE_RE.match(lines[0]):
            continue
        text = " ".join(lines)
        if _is_stamp_paragraph(text):
            continue
        return text
    return ""


def parse_journal_file(markdown, times_by_cycle=None):
    """A whole `journal.md` -> entries, with its preamble cut off first.

    The preamble above `## Entries` is the file's own instructions to the
    next cycle rather than content. **Whether it is there is a fact about
    the source and cannot be recovered from the text**, which is why this
    is a separate function rather than a flag on `parse_journal`: every
    entry is free prose, this loop's own instructions tell each cycle to
    write about the `## Entries` marker, and an entry that quotes it at
    the start of a line is indistinguishable from the real preamble
    boundary. Run over an entries body, the partition fires inside
    somebody's prose and silently drops every *newer* entry -- measured
    2026-08-13, three entry documents in and one card out, the two lost
    being the two at the top of the feed.

    Guarding on "no `### ` heading above the marker" was tried and does
    not work either: `journal.md`'s preamble documents the entry heading
    format and legitimately contains one
    (`test_a_heading_in_the_preamble_does_not_become_an_entry`).

    There is exactly one live caller, `split_journal` -- the migration
    that cut the archive into the folder in the first place. Everything
    that runs every hour holds an entries body and calls `parse_journal`.

    This was `parse_journal(..., strip_header=True)` until Cycle 156, and
    it was the *default*. A caller that held an entries body and forgot
    the flag got Cycle 154's bug back with nothing failing, and the tests
    modelled that combination as normal -- 35 test call sites, none of
    them passing the flag, most of them holding an entries body. A
    misdeclaration is now a different function name at the call site
    rather than an omitted argument, and it fails in the safe direction:
    calling `parse_journal` on a whole `journal.md` yields the preamble's
    own heading as a spurious extra entry instead of deleting real ones.

    The cut itself is `entries_body`, not a second copy of the partition
    written here. There must be exactly one definition of where the
    preamble ends, because the migration writes out as documents whatever
    this treats as content -- two copies that drift would split the
    archive at one boundary and render it at another.
    """
    if not markdown:
        return []
    return parse_journal(entries_body(markdown), times_by_cycle)


def parse_journal(markdown, times_by_cycle=None, written_by_cycle=None):
    """An entries body -> a list of entries in the order they appear (newest first).

    An entries body is what every hourly path holds: the site's feed,
    both halves of the comment-reply lookup, and `lint_entry`, which
    checks one entry document. `assemble_entries` produces one from the
    per-entry folder and `entries_body` cuts one out of the archive, so
    no caller here has a preamble to worry about. For the one source that
    does -- a whole `journal.md` -- use `parse_journal_file`.

    `times_by_cycle` (from `entry_times`) overrides the date and time a
    cycle typed into its own `### ` heading with the vault's write time
    for the entry document. The owner hit this twice: Cycle 86 woke at
    19:00:14, ran seven minutes, and the card said 19:30, because the
    stamp was never measured from anything -- it was a cycle guessing at
    when it expected to finish. The write time is measured, and a heading
    with no cycle number (his own messages, an addendum) keeps its typed
    stamp rather than borrowing someone else's.

    `written_by_cycle` is the *unmodified* `entry_times` map, and it exists
    because `times_by_cycle` no longer always is one. `with_start_times`
    replaces the write time with the cycle's wake time for display, and
    `lastWrittenAt` -- which `stall_notice` keys the "Nova has stopped
    writing" alarm on -- must not move with it: a card reading 45 minutes
    earlier would make a healthy loop look silent for 45 minutes longer
    than it was, which is the false stall Cycle 376 spent its whole run
    removing. So the display stamp and the written stamp are two fields
    now. Omit it and `writtenDate`/`writtenTime` mirror `date`/`time`,
    which is what every caller that passes one map still means.
    """
    if not markdown:
        return []
    text = markdown

    headings = list(_ENTRY_HEADING_RE.finditer(text))
    entries = []
    seen_per_cycle = {}
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        raw_body = text[start:end].strip()

        pr = board = outcome = ""
        footer = _FOOTER_RE.search(raw_body)
        if footer:
            pr = footer.group("pr")
            board = footer.group("board") or ""
            outcome = footer.group("outcome")
            raw_body = raw_body[: footer.start()].rstrip()
        else:
            raw_body, pr, board, outcome = stray_footer(raw_body)

        entry = parse_heading(match.group(1))
        cycle = entry["cycle"]
        if times_by_cycle and cycle is not None:
            # A cycle that wrote twice (081 + its addendum) has two files
            # and two entries, both carrying the same cycle number. Both
            # lists are ordered newest-first, so the nth entry for a cycle
            # takes the nth write time rather than all of them collapsing
            # onto one.
            stamps = times_by_cycle.get(cycle) or []
            nth = seen_per_cycle.get(cycle, 0)
            seen_per_cycle[cycle] = nth + 1
            if nth < len(stamps):
                entry["date"], entry["time"] = stamps[nth]
            written = (written_by_cycle or {}).get(cycle) or []
            if nth < len(written):
                entry["writtenDate"], entry["writtenTime"] = written[nth]
        entry.setdefault("writtenDate", entry["date"])
        entry.setdefault("writtenTime", entry["time"])
        label, detail = split_outcome(outcome)
        raw_body, ask = split_ask(raw_body)
        entry["ask"] = ask
        entry["askSpans"] = render_inline(ask) if ask else []
        entry["body"] = raw_body
        # The card's brief for the 55 entries that have no digest line --
        # the digest is rewritten every cycle and its older lines are
        # dropped, so that is most of the feed rather than a corner case.
        # Only the brief: their remainder is the journal entry itself, and
        # showing it in both drawers would print the same paragraph twice.
        # `strip_brief_label` because a report's first paragraph opens with
        # `**TL;DR.**`, and without it that label is the whole brief.
        brief_source = strip_brief_label(_first_paragraph(raw_body))
        entry["briefSpans"] = render_inline(split_brief(brief_source)[0])
        entry["pr"] = pr
        entry["prSpans"] = parse_pr_refs(pr)
        entry["board"] = board
        entry["boardSpans"] = parse_board_refs(board)
        entry["outcome"] = label
        entry["outcomeDetail"] = detail
        entries.append(entry)
    return assign_emoji(entries)


def entries_body(markdown):
    """The entries half of `journal.md` -- everything below `## Entries`.

    The one definition of that boundary. `parse_journal_file` is a caller
    rather than a second copy, and so is the migration -- they must not
    drift, because whatever the parser treats as content is exactly what
    gets written out as per-entry documents.

    It named `parse_journal` until Cycle 156 and that had gone stale: the
    split moved to `parse_journal_file` when the flag became two
    functions, and this sentence kept pointing at the half that no longer
    cuts anything. Caught by the reviewer, which is the whole reason a
    second reader exists -- a comment cannot fail a test.
    """
    _, marker, body = (markdown or "").partition("\n## Entries")
    return body if marker else (markdown or "")


def split_entries(markdown):
    """`journal.md` -> [{heading, text}], newest first, in file order.

    `text` starts at the `### ` line and is the entry *verbatim* -- no
    frontmatter, no rewriting. That is what makes the migration provably
    lossless: joining every `text` back together in order reproduces the
    original `## Entries` body byte for byte (modulo the blank lines
    between them), so the split can always be undone from the split
    files alone.
    """
    text = entries_body(markdown)
    headings = list(_ENTRY_HEADING_RE.finditer(text))
    out = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        out.append({
            "heading": match.group(1).strip(),
            "text": text[match.start():end].strip(),
        })
    return out


def entry_filename(seq, heading):
    """`(70, "2026-08-09 22:42 (Oslo) — Cycle 65")` -> `070-cycle-65.md`.

    The sequence number leads because it is the only total order that
    survives: three headings carry no cycle number at all, six cycles
    wrote a second entry, and the dates repeat. Zero-padded so a plain
    lexical sort of the folder is chronological, which is what lets a
    cycle read the newest three without fetching the other sixty-seven.
    """
    cycle = _CYCLE_RE.search(heading or "")
    if cycle:
        slug = f"cycle-{int(cycle.group(1))}"
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", (heading or "").lower()).strip("-")
        # Stripped again after truncating: cutting at 40 characters lands
        # mid-word about as often as not, and `...-not-a-.md` is uglier
        # than the word it saved.
        slug = slug[:40].strip("-") or "entry"
    return f"{seq:03d}-{slug}.md"


def entry_seq(path):
    match = re.match(r"(\d+)", path.rsplit("/", 1)[-1])
    # An unnumbered file sorts oldest rather than being dropped: a
    # hand-added entry should still render, just not jump the queue.
    return int(match.group(1)) if match else -1


def assemble_entries(files):
    """`{path: content}` from `JOURNAL_DIR` -> one newest-first markdown
    blob, shaped exactly like `journal.md`'s **entries half**.

    Not like `journal.md` itself: there is no preamble here, and this
    docstring used to state "so that `parse_journal` cannot tell the two
    sources apart" as the goal. That sentence was the bug. The two sources
    genuinely differ in one respect -- whether a preamble has to be cut
    off the front -- and a parser that cannot tell them apart has to guess
    from the text, which is exactly how an entry quoting `## Entries` in
    its prose deleted every newer card. The caller knows which source it
    holds, and says so by which function it calls: `parse_journal` for
    this, `parse_journal_file` for a whole `journal.md`."""
    ordered = sorted(files.items(), key=lambda kv: (-entry_seq(kv[0]), kv[0]))
    normalised = (normalise_entry(path, content) for path, content in ordered)
    return "\n\n".join(text for text in normalised if text)


_FILE_CYCLE_RE = re.compile(r"-cycle-(\d+)")


def file_cycle(path):
    """The cycle number a `NNN-cycle-M.md` filename claims, or None.

    The filename's claim, never the heading's -- they are two independent
    statements of the same fact and a caller that wants the authoritative
    one has to parse the document. This is only good enough to decide
    *which document to fetch*.
    """
    match = _FILE_CYCLE_RE.search(path.rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_LEADING_HEADING_RE = re.compile(r"\A#{1,6}[ \t]+(.+?)[ \t]*(?:\r?\n|\Z)")


def normalise_entry(path, content):
    """One entry document -> text `parse_journal` reads as exactly one entry.

    A document is *supposed* to start with its `### ` heading and nothing
    else, and 161 of the 164 live ones do. The three that do not were not
    dropped, which would at least have been visible -- they were absorbed
    into the card above them, because `_ENTRY_HEADING_RE` is what decides
    where an entry begins and text before the first match belongs to
    whatever `assemble_entries` concatenated in front of it. So Cycle
    147's entry rendered as the tail of Cycle 148's card, with no card,
    no permalink and no comment bubble of its own, and Cycle 131's
    frontmatter rendered as literal `---` and `type: log` lines inside
    Cycle 132's. Measured against the live folder 2026-08-13.

    Two shapes, both fixed here rather than in the regex. Loosening
    `_ENTRY_HEADING_RE` to accept `##` would split any entry *body*
    containing a `## ` line, and every entry is free prose, so that trades
    three broken cards for an unknown number of entries chopped in half.

    - a leading heading at the wrong depth (`# Cycle 131 — ...`,
      `## Cycle 146 — ...`) is promoted to `###`, keeping its text: the
      cycle wrote a real heading and only the hash count is wrong.
    - anything else gets one synthesised from the filename, which is the
      only other statement of the entry's identity. `entry_filename`
      built that name *from* the heading, so this is its inverse -- exact
      for the `NNN-cycle-M.md` names every cycle writes, lossy for the
      prose-slug ones, and a lossy card title beats no card. Exactly one
      of the 164 live filenames carries no `-cycle-N` token
      (`004-2026-08-02-edvard-s-first-message-not-a.md`); this said
      "three" until a reviewer checked, which was `entry_filename`'s
      count of headings with no cycle number, copied across without being
      re-measured against the folder this function actually reads.

    Frontmatter is stripped either way. It is not content, no entry that
    parses today has any, and leaving it in front of a promoted heading
    would just move the literal `type: log` lines from one card to
    another.
    """
    text = (content or "").strip()
    if not text:
        return ""
    text = _FRONTMATTER_RE.sub("", text, count=1).lstrip()
    if not text:
        return ""
    if text.startswith("### ") or text.startswith("###\t"):
        return text
    heading = _LEADING_HEADING_RE.match(text)
    if heading:
        body = text[heading.end():].lstrip()
        # `\n\n` rather than `\n`, matching the synthesis branch below and
        # every correctly written document. `parse_journal` strips the body
        # either way, so this is about the assembled markdown staying the
        # shape the rest of the file assumes, not about the parse.
        return f"### {heading.group(1)}\n\n{body}".rstrip() if body else f"### {heading.group(1)}"
    return f"### {synthetic_heading(path)}\n\n{text}"


def synthetic_heading(path):
    """`.../163-cycle-147.md` -> `Cycle 147`; a prose slug back to prose.

    The plain inverse of `entry_filename` -- drop the sequence prefix,
    dashes back to spaces -- and that one rule covers both filename
    shapes rather than special-casing the `-cycle-N` one. It was written
    with a `file_cycle(path)` branch in front of it, which a mutation run
    then showed to be unreachable in any observable sense: `071-cycle-66`
    becomes `Cycle 66` down this path too, so the branch could be deleted
    with every test still green. A branch nothing can distinguish is not
    a safety net, it is a second thing to keep in sync.

    Only ever used for a document that failed to write its own heading,
    so it carries no date or time -- `entry_times` supplies those from
    the document's mtime, which is the authoritative stamp anyway.
    """
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    words = re.sub(r"\A\d+-", "", stem).replace("-", " ").strip()
    return words.capitalize() if words else "Entry"


def entry_times(mtimes):
    """`{path: mtime_ms}` from `JOURNAL_DIR` -> `{cycle: [(date, time), ...]}`.

    Oslo time, because that is what the owner reads and what the headings
    have always claimed to be. Ordered newest-first per cycle, matching
    the order `assemble_entries` puts the entries in, so `parse_journal`
    can join the two on nothing more than the cycle number both the
    filename (`NNN-cycle-M.md`) and the heading already carry.
    """
    out = {}
    for path, ms in sorted(mtimes.items(), key=lambda kv: (-entry_seq(kv[0]), kv[0])):
        match = _FILE_CYCLE_RE.search(path.rsplit("/", 1)[-1])
        if not match or not ms:
            continue
        stamp = datetime.fromtimestamp(ms / 1000, tz=OSLO)
        out.setdefault(int(match.group(1)), []).append(
            (stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M"))
        )
    return out


def with_start_times(times_by_cycle, starts_by_cycle):
    """`entry_times` output, with each cycle's stamp moved to when it *woke*.

    The owner, capture 2026-08-24: *"I want the time slot on the journals
    to be when they started, as it seems to show when they ended."* He is
    reading it right. `entry_times` takes the vault document's write time,
    and a cycle writes its entry in the last few minutes of its run -- so
    Cycle 381 woke at 20:40 and its card says 20:54.

    The typed heading is not the answer either, and that is why this needs
    a third source rather than a preference between the two the code
    already had: a cycle types its own stamp at the end as well, and Cycle
    86 typed one 23 minutes into its own future, which is what moved the
    card onto the write time in the first place.

    `starts_by_cycle` is `{cycle: iso8601}` from the Agora conversation
    each cycle runs inside (`cycle_number.starts_in`) -- created by the
    heartbeat *before* the session opens, so it is measured rather than
    typed and it is measured at the right end.

    Two boundaries, both deliberate:

    - **Only cycles already in `times_by_cycle` are touched.** That map is
      keyed on the filename, so it is the answer to "did this cycle write
      an entry"; a conversation with no entry must not become one, and
      `journal_payload` reads exactly that to decide which cycles are
      missing.
    - **Every entry a cycle wrote gets the same start.** A cycle that wrote
      twice has two documents with two write times and one wake, and the
      wake is the true answer for both. The list keeps its length so
      `parse_journal`'s nth-entry indexing is unchanged.

    A cycle with no parseable conversation keeps its write time. That is
    most of the archive's future rather than a corner case -- conversations
    are the owner's to delete, and 382 of them exist today.
    """
    if not starts_by_cycle:
        return times_by_cycle
    out = {}
    for cycle, stamps in (times_by_cycle or {}).items():
        started = starts_by_cycle.get(cycle)
        oslo = _oslo_stamp(started)
        out[cycle] = [oslo] * len(stamps) if oslo else stamps
    return out


def _oslo_stamp(iso):
    """`2026-08-24T17:20:04.072Z` -> `("2026-08-24", "19:20")` in Oslo, or None.

    Agora stamps UTC with a `Z`, which `fromisoformat` refused before 3.11,
    so the explicit `+00:00` is what makes this independent of the runtime
    version rather than of a version claim. A stamp in any other shape
    must not take the page down for a badge's worth of precision, so
    anything unparseable is a `None` the caller reads as "keep what you
    had". A naive stamp is treated as UTC, which is what Agora sends and
    what every other reader of these fields in this package assumes.
    """
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(OSLO)
    return (local.strftime("%Y-%m-%d"), local.strftime("%H:%M"))


def _sections(markdown):
    """`## Heading` -> body text, for a flat two-level markdown file."""
    out = {}
    headings = list(_SECTION_RE.finditer(markdown or ""))
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        out[match.group(1).strip().lower()] = markdown[start:end].strip()
    return out


def parse_digest(markdown):
    """`journal-digest.md` -> its three sections, with the digest lines split out."""
    sections = _sections(markdown)
    lines = []
    for paragraph in split_digest_entries(sections.get("digest", "")):
        match = _DIGEST_LINE_RE.match(paragraph)
        if match:
            text = " ".join(match.group("text").split())
            brief, rest = split_brief(text)
            lines.append(
                {
                    "cycle": int(match.group("cycle")),
                    "at": match.group("at").strip(),
                    "text": text,
                    # The two drawers: the headline on the collapsed card,
                    # and the rest of the digest revealed when it opens.
                    # Between them they are the whole line with its bold
                    # rendered rather than shown as asterisks -- which is
                    # why there is no third `spans` field carrying the same
                    # text a second time. #61 stopped reading one when it
                    # split the card into two drawers, and it went on being
                    # sent: a third of every digest line, for nothing.
                    "briefSpans": render_inline(brief),
                    "restSpans": render_inline(rest),
                }
            )
    return {
        "nextCycle": sections.get("next cycle", ""),
        "lines": lines,
    }


# An attachment this site wrote on the owner's behalf, and nothing else.
# Same two constructs as `nova_uploads.ATTACHMENT_LINE` and app.js's
# `ATTACH_RE`: `![alt](/api/upload/<name>)` for a picture, the same
# without the bang for any other file. The path is required to start
# `/api/upload/` rather than being escaped later, so a pasted
# `[x](javascript:…)` or a remote tracker URL never becomes an element --
# it stays the text it is. This is deliberately not general markdown link
# support; `render_inline`'s survey found zero links in the journal, and
# what changed is that the app now generates one specific line.
#
# It goes **first** in the alternation, before code and bold, so a
# filename containing `**` cannot split the construct in half.
#
# The construct is imported rather than written again: `nova_uploads` is
# the module that *builds* the string, and it already carried an anchored
# copy for `is_attachment_line`. Two hand-copied readers of one format is
# the drift this loop keeps writing detectors for.
_INLINE_RE = re.compile(
    ATTACHMENT_PATTERN
    + r"|`([^`]+)`"
    r"|\*\*(.+?)\*\*",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"^[ \t]*```")
_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")
# Every item on the owner's boards opens with his own words as a blockquote,
# and until the board pages existed nothing rendered one -- a `>` line fell
# through to a paragraph and the marker showed on screen as literal text
# in front of the one thing on the page he wrote himself. 66 lines across
# his two files, plus whatever the journal quotes.
_QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
# `1. `, `2. ` -- a numbered list, which until the `/plan` page existed
# nothing here rendered. The journal survey that scoped this module found
# only bullets, and it was right about the journal; `roadmap.md` opens on
# **The five I would do next, in order**, and the order is the entire
# point of it. Rendered as paragraphs those five ran together into one
# block of prose with the numbers still typed in the middle of it.
#
# Anchored on the digits and a real separator, so a sentence that happens
# to start "1985. " is the only false positive available and a line
# starting "2 " is not one at all.
_ORDERED_RE = re.compile(r"^[ \t]*\d{1,3}[.)][ \t]+(.*)$")


def render_inline(text):
    """One paragraph -> a list of `{kind, text}` spans.

    Code before bold in the alternation so a `**` inside backticks stays
    literal, and an attachment before both. Surveyed against the live
    journal (2026-08-09): 591 inline code spans, 85 bold, zero of
    anything else -- so this handles what the file actually contains
    rather than markdown in general.

    The `attach` span carries its `url` as a separate field for the same
    reason `parse_pr_refs` does: app.js builds every node with
    `textContent`, so there is no path by which the vault's text can
    become markup. `isImage` is the bang, and it decides thumbnail
    against paperclip on the other side.
    """
    spans = []
    cursor = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > cursor:
            spans.append({"kind": "text", "text": text[cursor:match.start()]})
        if match.group(3) is not None:
            spans.append(
                {
                    "kind": "attach",
                    "text": match.group(2),
                    "url": match.group(3),
                    "isImage": bool(match.group(1)),
                }
            )
        elif match.group(4) is not None:
            spans.append({"kind": "code", "text": match.group(4)})
        else:
            spans.append({"kind": "strong", "text": match.group(5)})
        cursor = match.end()
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


# The owner, issues.md 2026-08-09: "I need a 2-3 line short precise Digest
# for each cycle as a title for each journey card ... As short as
# possible, max 3 sentences. It should cover everything important ...
# Then, when a journey card is opened, the Digest is revealed."
#
# So a card carries two summaries, not one, and this is the split. Until
# now the collapsed card showed the *whole* digest line clamped by CSS to
# three lines, which is why he asked: a clamp cuts wherever the line box
# ends, so every card trailed off mid-sentence. A brief that ends on a
# sentence is the actual difference.
#
# Derived from the text rather than authored into each entry, for the
# same reason `assign_emoji` is: `## Entries` is append-only (rule 3), so
# the 68 entries that already exist can never be given a new field. A
# `Summary:` footer would only ever brief the future.
#
# The derivation is not a guess, though, which is the part worth knowing.
# The house style for a digest line is a bolded opening sentence saying
# what changed for him -- all 9 live lines have one, every one of them a
# single sentence of 48-179 chars. That sentence *is* the brief, already
# written to be exactly this. Taking whole sentences from the front finds
# it exactly, because a sentence ends after its closing `**`.
#
# `MAX_BRIEF_CHARS` is the one number here, and it is his second
# constraint ("as short as possible") rather than a defensive cap.
# Measured over the 55 entries with no digest line, whose prose was never
# written to be a headline: three unbudgeted sentences run to 633 chars,
# median 349, against 48-179 for the authored ones. 240 sits above every
# authored brief and well under the unbudgeted median. Nothing is
# discarded -- the remainder is the next drawer down, one tap away.
MAX_BRIEF_SENTENCES = 3
MAX_BRIEF_CHARS = 240
# A sentence ends at `.`/`!`/`?`, then any closing emphasis, then space.
# The emphasis run is what makes `...plainly.** Every cycle` break after
# the `**` instead of between the `.` and it, which would split a bold
# span across both drawers and leave the markers unbalanced.
_SENTENCE_END_RE = re.compile(r"[.!?][*_`]*(?=\s|$)")


def split_sentences(text):
    """Text -> sentences, never breaking inside an inline code span.

    Backticks are tracked because the journal quotes shell and paths
    constantly -- `vault_tool.py get 'a.md'` holds two full stops that
    end nothing, and 591 inline code spans across the live file give that
    plenty of chances to happen.
    """
    text = (text or "").strip()
    if not text:
        return []
    sentences = []
    start = 0
    in_code = False
    for index, char in enumerate(text):
        if char == "`":
            in_code = not in_code
            continue
        if in_code:
            continue
        match = _SENTENCE_END_RE.match(text, index)
        if not match:
            continue
        if match.end() <= start:  # inside emphasis already consumed
            continue
        sentences.append(text[start:match.end()].strip())
        start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _is_bold_sentence(sentence):
    """Is this whole sentence wrapped in `**`?"""
    return sentence.startswith("**") and sentence.rstrip().endswith("**")


def strip_brief_label(text):
    """Drop a leading bold *label* -- `**TL;DR.**` -- from a card's brief.

    An eight-cycle report opens with a bold label rather than a bold
    headline, and `split_brief`'s shortcut above cannot tell the two
    apart: it read report 242's `**TL;DR.**` as the headline and gave the
    card that as its entire title, which is what the owner reported in
    issues #86 -- *"the 8cycle reports have just the word tl;dr as
    title. That might be a missinderstanding about i said i wanted the
    report title to be a precise and short summarization."*

    The discriminator needs no list of known labels: a headline is a
    sentence and always contains a space, and `TL;DR.` does not.

    Only the entry brief calls this. A digest line's `brief` and `rest`
    have to reconstruct the whole line between them, so nothing may be
    dropped on that path -- and a digest line never carries this shape
    anyway, since `_DIGEST_LINE_RE` has already eaten the `**Cycle N**`
    prefix by the time the text gets here.
    """
    sentences = split_sentences(text)
    # `> 1` because a brief that is empty is worse than one that is a
    # label: a first paragraph holding nothing but the label keeps it.
    if len(sentences) > 1 and _is_bold_sentence(sentences[0]):
        if " " not in sentences[0].strip("* "):
            return " ".join(sentences[1:])
    return text


def split_brief(text):
    """A summary -> `(brief, rest)`, split on a sentence boundary.

    The brief is what a collapsed card shows; the rest is revealed when
    it opens. `rest` is empty when the whole summary already fits, so a
    short digest line does not open onto a blank drawer.
    """
    sentences = split_sentences(text)
    if not sentences:
        return "", ""
    # A digest line whose first sentence is entirely bold has already been
    # written as the headline, so that sentence alone is the brief -- the
    # budget below would otherwise pull a second sentence in after it
    # whenever the headline was short, which is the opposite of "as short
    # as possible". All 9 live digest lines are shaped this way; entries
    # briefed from their own prose have no such marker and fall through.
    if _is_bold_sentence(sentences[0]):
        return sentences[0], " ".join(sentences[1:])
    taken = []
    length = 0
    for sentence in sentences[:MAX_BRIEF_SENTENCES]:
        # Always take the first, however long: a brief that is empty
        # because one sentence ran over budget is worse than a long one.
        if taken and length + 1 + len(sentence) > MAX_BRIEF_CHARS:
            break
        taken.append(sentence)
        length += (1 if length else 0) + len(sentence)
    return " ".join(taken), " ".join(sentences[len(taken):])


def render_blocks(text):
    """Entry body -> a list of blocks, each carrying its inline spans.

    Structured data rather than HTML on purpose: app.js builds every
    node with textContent and never touches innerHTML, so nothing the
    vault contains can become markup. That is a stronger guarantee than
    escaping correctly on every path, and it survives someone later
    pasting a `<script>` into a journal entry.

    Paragraph lines are joined with a space because the journal is
    hard-wrapped at ~95 columns; without this the browser would render
    the author's line breaks as real ones on a phone.
    """
    blocks = []
    paragraph = []

    def flush():
        if paragraph:
            blocks.append({"type": "p", "spans": render_inline(" ".join(paragraph))})
            paragraph.clear()

    lines = (text or "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE_RE.match(line):
            flush()
            index += 1
            fenced = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                fenced.append(lines[index])
                index += 1
            blocks.append({"type": "code", "text": "\n".join(fenced)})
            index += 1
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            flush()
            blocks.append({"type": "li", "spans": render_inline(bullet.group(1))})
            index += 1
            continue
        ordered = _ORDERED_RE.match(line)
        if ordered:
            flush()
            # The number is dropped, not kept: the browser renders it from
            # the `<ol>`, and keeping it would print it twice. What that
            # costs is a list that does not start at 1 -- none of these
            # files has one -- and what it buys is the numbering staying
            # right when a cycle inserts an item and renumbers only half.
            blocks.append({"type": "oli", "spans": render_inline(ordered.group(1))})
            index += 1
            continue
        quote = _QUOTE_RE.match(line)
        if quote:
            flush()
            # Consecutive `>` lines are one quote, joined with a space for
            # the same reason paragraph lines are: the source is wrapped
            # and the reader's screen decides where the breaks go.
            quoted = [quote.group(1).strip()]
            index += 1
            while index < len(lines):
                more = _QUOTE_RE.match(lines[index])
                if not more:
                    break
                quoted.append(more.group(1).strip())
                index += 1
            blocks.append({
                "type": "quote",
                "spans": render_inline(" ".join(part for part in quoted if part)),
            })
            continue
        if not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return blocks


def _newest_written_at(entries):
    """When the newest entry was written, as an aware datetime, or `None`.

    For a numbered entry the date and time are the vault's *write* time
    for that document rather than the stamp the cycle typed into its own
    heading (`parse_journal` substitutes it), so this is normally a
    measurement and not a cycle's guess at when it expected to finish --
    the same instrument `cycle_health.newest_entry_at` reads, off the same
    mtimes. The substitution is keyed on the cycle number, so an entry
    without one (the owner's own notes) keeps its typed stamp; that is only
    ever the newest entry in a corpus whose newest entry is not a cycle's,
    and being an hour out on the silence there is not worth reaching for a
    second time source.

    Defensive about the format because the four legacy heading shapes are
    still in the corpus and carry times like `03:19Z`: an entry whose stamp
    will not parse is skipped rather than guessed at, and `None` (no
    usable stamp anywhere) is a different answer from "no silence", which
    is why the caller reports it as `None` instead of `0`.
    """
    for entry in entries:
        # A silence marker is the runner saying a cycle died, not the loop
        # writing. Counting it here would move `lastWrittenAt` on every
        # failed run, and `stall_notice.due` dedupes on exactly that stamp
        # -- so a loop failing every cycle would keep resetting the alarm
        # it was supposed to raise. See `cycle_stub`.
        if entry.get("kind") == "silence":
            continue
        # `writtenDate`/`writtenTime` and not `date`/`time`: since the card
        # started showing when a cycle *woke*, the two differ by the length
        # of the run and only one of them is what this function is named
        # after. `parse_journal` mirrors them when there is nothing to
        # separate, so this is the same read it always was for every caller
        # that passes one map.
        date = entry.get("writtenDate") or entry.get("date")
        time_of_day = entry.get("writtenTime") or entry.get("time")
        if not date or not time_of_day:
            continue
        try:
            stamp = datetime.strptime(date + " " + time_of_day, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        return stamp.replace(tzinfo=OSLO)
    return None


def open_asks(entries):
    """Every card carrying an ask, newest first, as `{cycle, date, time}`.

    An ask lives on the journal card that raised it (`split_ask`), and the
    card scrolls off the top of the feed while the question stays open --
    #94's has been waiting since 08-16 and is fourteen cards down, which is
    the whole reason this list exists. The page cannot work it out for
    itself: it holds twenty entries and the oldest ask is exactly the one
    outside that window.

    **Which of these is still waiting is deliberately not decided here.**
    An ask is answered when the owner has commented on that card, and comments
    live in a different document with its own cache -- folding them in
    would mean the pill kept claiming he had not replied until the *journal*
    cache next rebuilt, minutes after he did. So this stays a pure function
    of the journal, and the client, which already holds both payloads,
    intersects them.

    Entries with no cycle number are skipped rather than carried with a
    `None`: a report has no card of its own for him to reply on, so an ask
    written into one has nowhere to be answered and pointing at it would be
    pointing at nothing.
    """
    return [
        {
            "cycle": entry["cycle"],
            "date": entry.get("date") or "",
            "time": entry.get("time") or "",
        }
        for entry in entries
        if entry.get("ask") and entry.get("cycle") is not None
    ]


def build_status(entries, known_cycles=None):
    """The front-page header: is Nova alive, and what did it just do.

    `runningDays` spans the oldest entry to the newest rather than to
    today on purpose -- it answers "how long has this loop been going",
    and if entries stopped arriving a week ago that number should stop
    growing rather than quietly keep counting.

    `missingCycles` is the history half of the owner's #72: *"Nova is 1
    behind agora. Agora failed a cycle Journal and you did not catch
    it."* These are the holes he found himself, by noticing the numbers
    on the feed jump from 126 to 129. Deliberately the whole list and not
    a count -- the feed marks each gap in the position it happened, so
    the numbers are what the client needs, and trimming a list of six to
    spare a header that never renders it would be a limit with no danger
    behind it.

    **`known_cycles` is which cycles have an entry document, and it must
    come from the filenames.** Measured against the live journal
    2026-08-12: 140 cycle numbers appear in `NNN-cycle-M.md` filenames and
    only 137 can be read back out of the `### ` headings inside them,
    because 131 opens with frontmatter and 146 and 147 wrote `## Cycle N`
    with two hashes instead of three. Those three entries exist and are
    not gaps. Inferring the set from parsed headings instead would have
    printed "Cycle 146 ran and wrote no entry" directly above Cycle 146's
    own words -- the exact false alarm #72 warns about, in the one feature
    built to answer it. The filename is also what `cycle_health` counts,
    so passing it here is what actually makes the two agree; sharing
    `gaps_between` only guarantees they agree about a *set*.

    Falls back to the parsed numbers when the caller has none -- the
    frozen `journal.md` archive is one file with no per-entry names, and a
    slightly wrong list there beats no list at all.

    **Everything here is a pure function of the corpus, and the clock is
    deliberately not consulted.** This payload is cached and warmed at
    startup, so a value derived from `now` would freeze at build time and
    could never become true; the live half of #72 (`stalled`) is stamped
    per request in `nova_site.journal_page` instead. `lastWrittenAt` is
    the measured input it needs, carried as a fact rather than a
    judgement so both layers read one clock and one stamp.

    **The newest document is not the newest cycle, and neither field may
    assume it is.** Two things break that assumption and both are now
    routine. A report (step 6c) is a document with no cycle number that
    lands *after* the entry of the cycle that wrote it, so reading the
    header off `entries[0]` puts `Outcome: report` and `PR: none` on the
    front page three times a day -- the loop's own summary rendered as a
    cycle that shipped nothing. And an addendum written after a later
    cycle's entry carries the earlier number, so taking the first number
    in document order walks the header backwards. `latest` therefore skips
    reports, and the cycle number is the highest written rather than the
    newest filed.

    **Those two are one decision, not two.** Filtering reports out of
    `latest` and taking `max` for the number fixes each symptom on its own
    and still lets the header describe two different cycles at once: with
    an addendum to 128 filed after 129's entry, the number reads 129 while
    the outcome, PR and time are all 128's. So `latest` is the newest
    document *belonging to the cycle the header names*, which is what
    makes all four fields one statement about one cycle.
    """
    from agora_runner.cycle_health import gaps_between, recent_gaps

    dated = [e for e in entries if e["date"]]
    numbered = [e for e in entries if e["cycle"] is not None]
    cycles = [e for e in entries if e.get("kind") not in ("report", "silence")]
    newest_cycle = max((e["cycle"] for e in numbered), default=None)
    # Document order inside one cycle, so a cycle's own addendum wins over
    # the entry it amends -- it is that cycle's latest word.
    of_newest = [e for e in cycles if e["cycle"] == newest_cycle]
    # The last fallback is `spoken`, not `entries`: on a corpus whose only
    # documents are silence markers, `entries[0]` put the marker's own
    # `Outcome: stuck` and `PR: none` into the header, which is the header
    # describing the runner rather than the loop. Production always holds a
    # real entry so this was masked, and nothing enforced that.
    spoken = [e for e in entries if e.get("kind") != "silence"]
    latest = (of_newest or cycles or spoken or [None])[0]
    running_days = 0
    if dated:
        from datetime import date

        try:
            newest = date.fromisoformat(dated[0]["date"])
            oldest = date.fromisoformat(dated[-1]["date"])
            running_days = (newest - oldest).days
        except ValueError:
            running_days = 0
    written_at = _newest_written_at(entries)
    # Materialised once: `known_cycles` is documented as coming from the
    # filenames and arrives as a set, but the fallback is a generator and
    # two callers now read it. Consuming it in the first would have left
    # the second reading an empty sequence -- and an empty sequence is
    # exactly what "no cycle is missing" looks like, so the badge would
    # have been silent rather than wrong, which nothing would have caught.
    cycle_numbers = set(
        known_cycles if known_cycles is not None
        else (e["cycle"] for e in numbered))

    return {
        "cycle": newest_cycle,
        "lastWokeDate": latest["date"] if latest else "",
        "lastWokeTime": latest["time"] if latest else "",
        "lastPr": latest["pr"] if latest else "",
        "lastOutcome": latest["outcome"] if latest else "",
        "lastOutcomeDetail": latest.get("outcomeDetail", "") if latest else "",
        "runningDays": running_days,
        "entryCount": len(entries),
        "missingCycles": gaps_between(cycle_numbers),
        # The half the header can actually act on -- see
        # `cycle_health.recent_gaps`. `missingCycles` stays whole because
        # the feed marks every hole where it happened; this is the subset
        # recent enough to be worth a badge rather than a footnote.
        "recentMissingCycles": recent_gaps(cycle_numbers),
        "lastWrittenAt": written_at.isoformat() if written_at else "",
        # Every card with an ask on it, answered or not -- see `open_asks`.
        "asks": open_asks(entries),
    }
