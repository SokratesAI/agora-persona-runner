"""Comments on a cycle: The owner replying to a particular entry, not filing work.

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

**The second target is the digest's `Needs the owner` block** (2026-08-10, his
words in the `add_needs_comment` docstring). That block is the one place
Nova asks *him* a direct question, and until now it was the one place with
no way to answer -- idea #56 sat in it unanswered for eight cycles, which
is not him ignoring it but a box with no reply field. Such a reply is
stored here under `### Needs the owner · <stamp>` rather than under a cycle,
because the digest is rewritten every cycle and filing his answer under
whichever cycle last touched the text would attach it to a card at random.

**The channel only exists if a cycle reads it.** A comment nobody collects
is Cycle 58's "Needs the owner" box all over again: built, tested, shipped
and dead. So the file has two sections and a comment is not done when it
is written -- `## New` is the owner's outbox and Nova's inbox, and a cycle
moves what it has acted on down to `## Acknowledged` with what it did,
the same shape `inbox.md` already uses and rule 8's "organised, not
annotated inline". `prompt.md` step 1a reads `## New` every cycle and
never delegates it, because these are the owner's exact words.

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

**A comment can carry one reply from Nova** (2026-08-10, his words on the
card for cycle 80 -- *"A good idea is to have the session that created the
Journal instantly reply to my comments on the Journal! That would be so
cool, to have a conversation with comments on the Journal entry."*). It is
stored inside the comment it answers, under a `#### Nova · <stamp>`
heading -- one level below the comment's own heading, because it belongs
to that comment rather than standing beside it. Structural again: the
marker is a heading shape, not a position, and the owner's text above it is
still stored exactly as typed.

**A reply is written once and never rewritten,** the same rule a journal
entry follows: `insert_reply` refuses a comment that already has one
rather than replacing it. Two different sessions answering the same
comment would be two different answers, and silently keeping the second
would lose the first.

**Replying is not acknowledging.** A reply is conversation; `##
Acknowledged` means a cycle *did* something about it. The two are
deliberately independent, so a comment Nova has chatted about still sits
in `## New` for the next cycle to act on -- see `nova_replies`.

**The write is a read-modify-write against a live vault and can lose,**
so it retries on 409 exactly as `nova_capture.capture` does, re-reading
each time rather than resending. Any non-409 failure is not a conflict
and would fail identically on a retry.

That paragraph was false for two days and is worth leaving as the
correction rather than a clean claim: the retry loop was here, the 409 it
waited for was not. Both writes below read, edited and PUT without
carrying the revision they read at, so `vault_write_path` picked up
whoever had written in between and overwrote them -- a loop watching for a
conflict that the write it wrapped could not produce. `nova_capture` was
fixed this way on 2026-08-12 (runner #118) and this module, holding the
file the owner types comments into, was not.

**This does not make `comments.md` safe, and reading it that way is the
mistake the sentence above invites.** Both writers *here* are conditional
now, which closes phone-against-phone and phone-against-reply. The larger
writer is elsewhere: a cycle acknowledging a comment moves it to
`## Acknowledged` through the generic `vault_write` / `scoped_write` tools
in `tools_dispatch`, which pass no revision at all -- so a cycle that read
this file before the owner's comment landed still overwrites it silently,
which is precisely this bug from the other actor. That is idea #63's later
slice, not an oversight, and it is the last write surface in the platform
that cannot do a conditional write.
"""

import re
from datetime import datetime, timedelta, timezone

from agora_runner.log import log
from agora_runner.md_sections import find_heading, section_bounds
from agora_runner.vault import vault_read_path_rev, vault_write_path

COMMENTS_PATH = "projects/sokrates/projects/agora/nova/resources/comments.md"

NEW_HEADING = "## New"
ACKNOWLEDGED_HEADING = "## Acknowledged"

WRITE_ATTEMPTS = 3

# The heading a reply to the `Needs the owner` block carries instead of a
# cycle number. Such a reply answers a question the *digest* is asking, and
# the digest is rewritten every cycle -- so there is no cycle it belongs
# to, and filing it under whichever cycle happened to write the current
# text would attach his answer to a card at random.
NEEDS_LABEL = "Needs Edvard"

# `### Cycle 63 · 2026-08-09 22:40`, or `### Needs the owner · 2026-08-10 08:20`.
# The separator is matched loosely so a heading hand-edited in Obsidian
# still parses; which of the two targets it names is the only part anything
# depends on.
_COMMENT_HEADING_RE = re.compile(
    r"^###[ \t]+(?:Cycle[ \t]+(?P<cycle>\d+)|(?P<needs>Needs[ \t]+Edvard))"
    r"[ \t]*(?:[·\-—][ \t]*(?P<stamp>.*?))?[ \t]*$",
    re.IGNORECASE,
)


def _heading_label(cycle):
    """`None` -> the Needs the owner block, an int -> that cycle."""
    return NEEDS_LABEL if cycle is None else f"Cycle {cycle}"

_SECTION_RE = re.compile(r"^##[ \t]+(?P<name>.+?)[ \t]*$")

# `#### Nova · 2026-08-10 14:12` -- Nova's reply, inside the comment it
# answers. Four hashes rather than three so it can never be mistaken for a
# comment heading by `_COMMENT_HEADING_RE`, which anchors on exactly three.
_REPLY_HEADING_RE = re.compile(
    r"^####[ \t]+Nova[ \t]*(?:[·\-—][ \t]*(?P<stamp>.*?))?[ \t]*$",
    re.IGNORECASE,
)

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
    or end of file. Frontmatter and fenced code are skipped at both ends --
    this file's own `contract:` line quotes both headings back at the
    reader, and a cycle's throwaway script matching that quote is what put
    the owner's newest comment inside the frontmatter on 2026-08-13. See
    `md_sections`.
    """
    return section_bounds(lines, heading)


def insert_comment(markdown, cycle, text, stamp):
    """Add one comment to the top of `## New`, newest first.

    `cycle` is an int, or `None` for a reply to the Needs the owner block.

    Newest first matches every other file this loop maintains and means a
    cycle reads the freshest thing the owner said without scrolling. If the
    section is missing entirely it is created, so a comment can never be
    dropped for want of a heading it did not have.
    """
    lines = markdown.split("\n") if markdown else []
    block = [f"### {_heading_label(cycle)} · {stamp}", ""] + text.split("\n") + [""]

    bounds = _section_bounds(lines, NEW_HEADING)
    if bounds is None:
        # No `## New` at all. Put it above `## Acknowledged` if that
        # exists, so the two stay in their documented order.
        ack = find_heading(lines, ACKNOWLEDGED_HEADING)
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


def split_replies(lines):
    """A comment's raw lines -> (his text, one entry per `#### Nova` block).

    Each entry is `{author, stamp, text}`. This used to return a single
    reply -- everything below the *first* `#### Nova` heading, later
    headings left inside it as raw text. That was right while only
    `add_reply` could write one, and `insert_reply` still refuses to add a
    second, so a comment can only ever grow one that way. But a cycle
    reading `comments.md` can append its own answer by hand, and several
    have; those landed inside the auto-reply's body and the app painted
    `#### Nova · 2026-08-21 16:23` as literal text in the middle of a
    bubble. The owner sent a screenshot of exactly that on 2026-08-21.

    `author` is `commentator` for the first block and `cycle` for every
    later one. That is positional, and it is right for the ordinary
    ordering -- the worker answers in seconds and a cycle appends later --
    but it is not the fact an earlier version of this docstring claimed.
    The reviewer on runner#279 found the interleaving that breaks it: if a
    cycle acknowledges a comment before the worker's reply lands, the
    cycle's note is the first block and is labelled `commentator`, and the
    worker's own reply is then dropped by `insert_reply` for finding a
    heading already there. Getting that right needs the author written into
    the heading, which changes the shape of a file the owner reads, so it is
    not done here. The failure is one bubble in the wrong colour, not lost
    text.
    """
    starts = [i for i, line in enumerate(lines) if _REPLY_HEADING_RE.match(line)]
    body = "\n".join(lines[: starts[0]] if starts else lines).strip("\n")
    replies = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        replies.append({
            "author": "commentator" if n == 0 else "cycle",
            "stamp": (_REPLY_HEADING_RE.match(lines[i]).group("stamp") or "").strip(),
            "text": "\n".join(lines[i + 1:end]).strip("\n"),
        })
    return body, replies


def insert_reply(markdown, cycle, stamp, reply, reply_stamp):
    """Put one reply inside the comment `(cycle, stamp)` names. Returns the
    new markdown, or `None` if there is nothing to write it into.

    `None` covers both misses and it is deliberate that the caller cannot
    tell them apart: the comment is gone (the owner deleted it in Obsidian),
    or it already carries a reply. Either way the only correct action is
    to drop this reply and log it -- there is no version of "write it
    somewhere else" that is better than not writing it.

    The reply lands at the end of the comment's body, above whatever
    heading follows, so the file keeps reading top to bottom as the
    conversation happened.

    `(cycle, stamp)` is a minute-resolution key, so two comments typed on
    the same cycle inside one minute collide: the second gets no reply,
    because the first match already has one. Nothing is lost or
    misattributed -- the comment still sits in `## New` for the next cycle,
    which is the fallback the whole design rests on -- and a
    second-resolution stamp would change the shape of every heading the owner
    reads to defend against a minute he is unlikely to spend typing twice.
    """
    lines = (markdown or "").split("\n")
    start = None
    for i, line in enumerate(lines):
        heading = _COMMENT_HEADING_RE.match(line)
        # A body ends at the next comment *or* at the next `##` section --
        # the last comment in `## New` is bounded by `## Acknowledged`, and
        # missing that would file the reply under the wrong section.
        if start is not None and (heading or _SECTION_RE.match(line)):
            end = i
            break
        if not heading:
            continue
        found = int(heading.group("cycle")) if heading.group("cycle") else None
        if found == cycle and (heading.group("stamp") or "").strip() == stamp:
            start = i + 1
    else:
        end = len(lines)

    if start is None:
        return None
    body = lines[start:end]
    if any(_REPLY_HEADING_RE.match(line) for line in body):
        return None

    # Trailing blank lines belong to the gap before the next heading, not
    # to the body, so the reply goes above them rather than after.
    while body and not body[-1].strip():
        body.pop()
        end -= 1
    block = ["", f"#### Nova · {reply_stamp}", ""] + reply.split("\n")
    return "\n".join(lines[:end] + block + lines[end:])


def add_reply(cycle, stamp, text, reply_stamp=None):
    """Store Nova's reply to the comment `(cycle, stamp)`. Returns (ok, message).

    Same read-modify-write and same 409 retry as `_store`, and for the same
    reason -- but it cannot share the code, because this one has to give up
    when the target comment is missing rather than create anything. A
    comment can vanish between being commented on and being replied to;
    that is a dropped reply, not a file to build.
    """
    body = clean_comment_text(text)
    if not body:
        return False, "nothing to reply"

    reply_stamp = reply_stamp or format_stamp()
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(COMMENTS_PATH)
        if current is None:
            return False, "could not read comments"
        updated = insert_reply(current, cycle, stamp, body, reply_stamp)
        if updated is None:
            return False, f"no comment on cycle {cycle} at {stamp} left to reply to"
        try:
            _verify_replied(current, updated, cycle, stamp, body)
        except WriteRefused as refused:
            log(f"nova-comment refused replying to cycle {cycle}: {refused}")
            return False, str(refused)
        result = vault_write_path(COMMENTS_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-comment replied to cycle {cycle} at {stamp}")
            return True, "replied"
        if "409" not in result:
            break
    log(f"nova-comment failed replying to cycle {cycle}: {result}")
    return False, f"could not write reply: {result}"


def parse_comments(markdown):
    """Markdown -> [{cycle, stamp, text, reply, replyStamp, acknowledged}].

    Newest-first per section. Order within the file is preserved rather
    than sorted: `## New` is written newest-first and `## Acknowledged`
    accumulates in the order cycles retired things, and both are
    information.
    """
    out = []
    section = None
    current = None

    def flush():
        if current is None:
            return
        body, replies = split_replies(current["lines"])
        out.append({
            "cycle": current["cycle"],
            "stamp": current["stamp"],
            "text": body,
            # `reply`/`replyStamp` are the *first* reply, kept because
            # `_verify_replied` and `nova_replies` both mean the auto-reply
            # when they say "the reply". `replies` is the whole thread.
            "reply": replies[0]["text"] if replies else "",
            "replyStamp": replies[0]["stamp"] if replies else "",
            "replies": replies,
            "acknowledged": current["acknowledged"],
        })

    for line in (markdown or "").split("\n"):
        heading = _COMMENT_HEADING_RE.match(line)
        if heading and section is not None:
            flush()
            current = {
                "cycle": int(heading.group("cycle")) if heading.group("cycle") else None,
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


class WriteRefused(Exception):
    """This write would damage the file, so it is not attempted.

    The message is read by a human -- it goes to the app as the reason a
    comment could not be saved -- so it says what changed, not which
    function noticed.
    """


def frontmatter(text):
    """The frontmatter block including both `---` lines, or "" if there is none.

    Byte-identical frontmatter is the cheapest true statement there is
    about a write to this file, because the 2026-08-13 damage was text
    spliced *into* the frontmatter: `contract:` quotes both headings back
    at whoever opens the file, so a search for `## Acknowledged` that is
    not anchored to a whole line finds the sentence 320 characters before
    the heading. `md_sections` stops the searches this repo owns from
    doing that; this stops the write regardless of how it went wrong.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[: i + 1])
    return ""


def comment_index(markdown):
    """`{(cycle, stamp): comment}` -- what a write is checked against."""
    return {(c["cycle"], c["stamp"]): c for c in parse_comments(markdown)}


# Every field `parse_comments` reports about a comment. A bystander that
# kept its text and its section but lost the reply Nova wrote it is still a
# comment the write damaged, so all four are compared, not the one or two a
# given write is about.
COMPARED_FIELDS = ("text", "acknowledged", "reply", "replyStamp", "replies")


def verify_write(original, updated, exempt=()):
    """Refuse unless the frontmatter and every comment outside `exempt` survived.

    The half of the check that is the same for all three writers -- adding
    a comment, replying to one, moving one to `## Acknowledged`. Each
    caller then proves its own intended change on top; this proves that
    nothing else happened. Raises `WriteRefused`, returns
    `(before, after)` so the caller does not parse twice.

    `exempt` is the keys the caller is deliberately changing. Everything
    else, including keys that appear or vanish, is damage: text landing
    where `parse_comments` cannot see it changes the set, which is exactly
    what a comment inside the frontmatter looks like from here.
    """
    if frontmatter(updated) != frontmatter(original):
        raise WriteRefused(
            "the frontmatter changed -- this is the 2026-08-13 bug and the "
            "write is refused; nothing written"
        )

    before = comment_index(original)
    after = comment_index(updated)
    exempt = set(exempt)
    lost = sorted(str(k) for k in set(before) - set(after) - exempt)
    gained = sorted(str(k) for k in set(after) - set(before) - exempt)
    if lost or gained:
        raise WriteRefused(
            f"the set of comments changed (lost {lost}, gained {gained}) -- "
            "nothing written"
        )
    for key, was in before.items():
        if key in exempt:
            continue
        now = after[key]
        if [now[f] for f in COMPARED_FIELDS] != [was[f] for f in COMPARED_FIELDS]:
            raise WriteRefused(f"{key} changed too -- nothing written")
    return before, after


def _verify_added(original, updated, cycle, stamp, body):
    """Refuse unless `updated` is `original` plus exactly this one comment.

    `insert_comment` is string surgery on the one file the owner talks to
    this loop through, it runs unattended every time he types into the
    app, and until this existed nothing between it and the vault could
    tell a good result from a damaged one. Refusing is the right direction
    to fail in here: the app reports the failure and his text is still in
    the box, where a silent corruption loses a comment invisibly -- which
    is how the 2026-08-13 one was found, by accident, by a cycle doing
    something else.
    """
    key = (cycle, stamp)
    _, after = verify_write(original, updated, exempt={key})
    if key not in after:
        raise WriteRefused(
            f"the new comment on {_heading_label(cycle)} at {stamp!r} is not "
            "readable back -- nothing written"
        )
    if key in comment_index(original):
        raise WriteRefused(f"{key} was already in the file -- nothing written")
    added = after[key]
    if added["text"] != body:
        raise WriteRefused(f"{key} did not keep the text as typed -- nothing written")
    if added["acknowledged"]:
        raise WriteRefused(f"{key} landed under {ACKNOWLEDGED_HEADING} -- nothing written")


def _verify_replied(original, updated, cycle, stamp, reply):
    """Refuse unless exactly the named comment gained exactly this reply."""
    key = (cycle, stamp)
    before, after = verify_write(original, updated, exempt={key})
    if key not in before or key not in after:
        raise WriteRefused(f"{key} is not in both versions -- nothing written")
    was, now = before[key], after[key]
    if now["text"] != was["text"]:
        raise WriteRefused(f"{key}'s text changed -- nothing written")
    if now["reply"] != reply:
        raise WriteRefused(f"{key} did not read back the reply -- nothing written")


def _oldest_first(comments):
    """A thread in the order it was said, not the order the file stores it.

    The owner, 2026-08-10: *"Journal comments must be sorted with the newest
    message at the bottom, so that the conversation goes downwards. That
    feels most natural."* The file stays newest-first -- that is how every
    board in this vault reads and how a cycle wants to find what it has not
    answered yet -- so the flip belongs here, at the boundary where a
    section of a file becomes a conversation on a card.

    A card mixes both sections: what a cycle has retired sits under
    `## Acknowledged` and what it has not sits under `## New`, so file order
    is not chronological across the two and reversing it would interleave
    them wrongly. The stamp is `%Y-%m-%d %H:%M`, which sorts lexically, and
    the sort is stable so two comments in the same minute -- or any missing
    stamp -- keep the order the file gave them.
    """
    return sorted(comments, key=lambda c: c.get("stamp") or "")


def comments_by_cycle(markdown):
    """`{cycle: [comment, ...]}` -- what the site hangs off each card, oldest first.

    Needs the owner replies are deliberately absent: they belong to no cycle,
    and letting `None` through would key a card on it.
    """
    grouped = {}
    for comment in parse_comments(markdown):
        if comment["cycle"] is None:
            continue
        grouped.setdefault(comment["cycle"], []).append(comment)
    return {cycle: _oldest_first(items) for cycle, items in grouped.items()}


def needs_comments(markdown):
    """`[comment, ...]` -- replies to the Needs the owner block, oldest first."""
    return _oldest_first([c for c in parse_comments(markdown) if c["cycle"] is None])


def add_needs_comment(text, stamp=None):
    """Store one reply to the Needs the owner block. Returns (ok, message).

    The owner, 2026-08-10: *"the 'needs the owner' is still missing a comment
    block, so its hard for me to answer it. [...] Where did you intend me
    to answer it? [...] I want a reply button on it."* Idea #56 had been
    sitting in that block unanswered for eight cycles, and the reason was
    this: the box asked a question and offered nowhere to type.
    """
    return _store(None, text, stamp)


def add_comment(cycle, text, stamp=None):
    """Store one comment against `cycle`. Returns (ok, message)."""
    try:
        cycle = int(cycle)
    except (TypeError, ValueError):
        return False, f"cycle must be a number, got {cycle!r}"
    if cycle < 0:
        return False, "cycle must not be negative"
    return _store(cycle, text, stamp)


def _store(cycle, text, stamp=None):
    """The shared read-modify-write. `cycle` is an int, or None for Needs the owner."""
    target = _heading_label(cycle).lower()
    body = clean_comment_text(text)
    if not body:
        return False, "nothing to comment"

    stamp = stamp or format_stamp()
    result = ""
    for _ in range(WRITE_ATTEMPTS):
        current, rev = vault_read_path_rev(COMMENTS_PATH)
        if current is None:
            # First comment ever, or the file was moved. Creating it is
            # strictly better than refusing: the alternative is losing
            # what he typed to a missing heading.
            #
            # `rev` is kept rather than zeroed, and the two no-content cases
            # differ: absent gives None, which PUTs without a `_rev` and so
            # 409s if another writer created the file first --
            # correct, because two "first comments" must not silently become
            # one. A tombstone gives its revision, and overwriting one has
            # to carry it or the write conflicts forever.
            #
            # `(None, None)` used to mean more than "absent":
            # `vault_read_path_rev` collapsed every non-200 into it, so a 500
            # arrived here looking like a missing file. It degraded safely --
            # the unconditional-create attempt 409s against the live document
            # and the retry reports failure rather than losing his text -- but
            # this branch could not tell the two apart. It does not have to
            # any more: only a 404 reaches here, and an unreadable database
            # raises out of the read above (`VaultUnreadableDocument`), which
            # the caller reports as a failed save. The bridge's copy of the
            # client was fixed in the same cycle (agora-claude-bridge#49);
            # what is still open is that nothing detects drift between the
            # two, which is filed rather than claimed fixed here.
            current = ""
        updated = insert_comment(current, cycle, body, stamp)
        try:
            _verify_added(current, updated, cycle, stamp, body)
        except WriteRefused as refused:
            log(f"nova-comment refused writing {target}: {refused}")
            return False, str(refused)
        result = vault_write_path(COMMENTS_PATH, updated, if_rev=rev)
        if result == "written":
            log(f"nova-comment stored on {target}")
            return True, f"commented on {target}"
        if "409" not in result:
            break
    log(f"nova-comment failed writing {target}: {result}")
    return False, f"could not write comment: {result}"
