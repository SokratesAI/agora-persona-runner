"""The **Needs the owner** block: what an item is, and how one leaves.

**The block itself is retired.** The owner asked for it gone on 2026-08-16;
#229 deleted it from the page and #236 the server half that fed it, so an
ask now lives on the journal card that raised it. What survives here is
the *archive*: `needs-edvard-archive.md` holds every ask this loop ever
made and the answer it got, it is still read, and `tools/roll_needs_edvard.py`
is what moves an item into it. That CLI is now the only caller.

This was split out of that CLI on 2026-08-16 so the *site* could run it
too -- the container image copies `agora_runner/` and not `tools/`, so the
CLI's copy was unreachable from a web handler. The handler is gone and the
split is not worth undoing: the code is I/O-free either way and the shim
in `tools/` re-exports it by name.

Nothing here does vault I/O. It takes the two files' text in and hands
two new texts back.
"""

import datetime
import re

from agora_runner.nova_journal import needs_items, split_needs_items
from agora_runner.rolling import (
    RollError,
    RollSpec,
    _body,
    join_paragraphs,
    plan,
    verify,
)

MARKER = "\n## Needs Edvard\n"
ARCHIVE_TITLE = "# Needs Edvard — Answered"

# `**Since 08-15**` -- the date the ask was first put to him. Written by the
# cycle that raises the item; this module only reads it, and only to say how
# long the item has waited.
_SINCE_RE = re.compile(r"\A\*\*Since (?P<date>\d{2}-\d{2})\*\*")

ARCHIVE_FRONTMATTER = (
    "---\n"
    "type: log\n"
    "tags: [agora, evolution, self-improvement, agent-context]\n"
    "status: built\n"
    "maintenance: Items that have left the Needs Edvard block in "
    "journal-digest.md because they were answered, newest first, each with "
    "what the answer was. Append only, written by tools/roll_needs_edvard.py. "
    "This file is Nova's, not Edvard's -- he reads the live block, and the "
    "whole point of this archive is that answered items stop competing with "
    "live ones for his attention. Nothing renders it.\n"
    "---\n\n"
)

# What the live section says when it holds nothing. The exact text matters:
# `is_empty_needs` is what hides the box on the owner's page, and it accepts
# only "nothing" or "none" once emphasis and a trailing full stop are
# stripped. `**Nothing blocking.**` -- which the live file used to carry --
# does *not* pass it, so writing that would leave him an empty box claiming
# his attention forever.
EMPTY_BLOCK = "**Nothing.**"


# The splitter lives in `nova_journal` beside `is_empty_needs`. It was one
# place so the site's payload and this module's roll could not disagree
# about where one ask ends; the payload half is gone as of #236 and this is
# now the only reader, but the helpers stay where they are rather than
# being moved for the sake of it.
split_items = split_needs_items


def _check_archive(new_archive):
    """No level-two heading in the archive.

    Inherited from `roll_digest`'s guard rather than copied out of caution:
    the two archives live in the same folder and a cycle repairing one by
    hand will reach for the other as its example. Nothing concatenates
    this file onto the digest today, so a `##` here is harmless *now* --
    which is precisely the state `digest-archive.md` was in before it
    started silently displacing the live file's newest lines.
    """
    if re.search(r"^##[ \t]+", new_archive, re.MULTILINE):
        raise RollError(
            "refusing to write: the Needs Edvard archive has a level-two "
            "heading, which would make it a rival section if this file is "
            "ever rendered beside the digest"
        )


SPEC = RollSpec(
    marker=MARKER,
    archive_title=ARCHIVE_TITLE,
    archive_frontmatter=ARCHIVE_FRONTMATTER,
    split_entries=split_items,
    join_entries=join_paragraphs,
    keep=0,  # unused: every caller selects explicitly, never by age
    noun="items",
    check_archive=_check_archive,
)


def live_items(live):
    """The items currently waiting on the owner, in file order.

    Takes the whole digest and finds the section; `needs_items` takes the
    section body. Since #236 this is the only splitter -- the site's payload
    used to call `needs_items` directly and the pair could not be allowed to
    drift, which is why they still share one definition.
    """
    return needs_items(_body(live, SPEC)[1])


def age_days(item, today):
    """Days since the item's `**Since MM-DD**` stamp, or None if unstamped.

    The year is not in the stamp, so a stamp in the future is read as last
    year rather than as a negative age -- which is what a January caller
    looking at a December ask actually wants.
    """
    match = _SINCE_RE.match(item)
    if not match:
        return None
    month, day = (int(part) for part in match.group("date").split("-"))
    try:
        since = datetime.date(today.year, month, day)
    except ValueError:
        return None
    if since > today:
        since = datetime.date(today.year - 1, month, day)
    return (today - since).days


def select_answered(items, phrases):
    """Indices of the items matching each phrase, one item per phrase.

    Refuses on nothing matched and on more than one matched. Both are the
    same failure -- the caller named something the file does not uniquely
    hold -- and the cost of guessing is either dropping a live ask off the
    one channel the owner has, or leaving an answered one in it. Neither is
    worth saving the caller a retype.

    This is also what makes the button on his page safe. The client sends
    back the text of the item it drew, not its position, so a cycle that
    rewrote the block between the render and the tap makes the dismissal
    *fail* rather than silently retire whatever moved into that slot.
    """
    picked = []
    for phrase in phrases:
        needle = phrase.strip().lower()
        hits = [i for i, item in enumerate(items) if needle in item.lower()]
        if not hits:
            raise RollError(
                f"refusing to roll: no Needs Edvard item contains {phrase!r}"
            )
        if len(hits) > 1:
            raise RollError(
                f"refusing to roll: {len(hits)} Needs Edvard items contain "
                f"{phrase!r} -- use a phrase that picks out exactly one"
            )
        if hits[0] in picked:
            raise RollError(
                f"refusing to roll: {phrase!r} names an item already named "
                "by an earlier --answered"
            )
        picked.append(hits[0])
    return picked


def stamp_answer(item, outcome, today):
    """The archived copy: the ask as written, then what answered it."""
    return f"{item}\n\n**Answered {today:%m-%d}** — {outcome.strip()}"


def archive_answered(live, archive, phrases, outcome, today):
    """Move the items matching `phrases` out of the live block.

    Returns `(new_live, new_archive, moved)` and writes nothing -- the
    caller owns both files and the order they are written in.
    """
    # `outcome` is free text and is spliced into the archive *after*
    # `verify` has run `_check_archive` on it, so the heading guard cannot
    # see it -- a `##` in it would otherwise land in the archive as a rival
    # section with a clean exit 0. Check it here, before anything is built,
    # rather than moving the splice earlier: `verify`'s invariant is that
    # entries pass through unchanged, and stamping before it would mean
    # loosening the one check that proves nothing was dropped.
    if re.search(r"^#{1,6}[ \t]", outcome, re.MULTILINE):
        raise RollError(
            "refusing to roll: the outcome contains a markdown heading, "
            "which would land in the archive as a rival section. Quote it "
            "inline or drop the leading '#'."
        )
    if not outcome.strip():
        raise RollError(
            "refusing to roll: an answered item needs an outcome saying what "
            "the answer was. An item archived with no answer is the same "
            "unreadable log one folder over."
        )

    items = live_items(live)
    picked = select_answered(items, phrases)
    new_live, new_archive = plan(live, archive, SPEC, select=lambda parsed: picked)
    verify(live, archive, new_live, new_archive, SPEC, ordered=False)

    # The answer is stamped on *after* `verify`, deliberately. `verify`'s
    # invariant is "out equals in, with duplicates collapsed" -- it compares
    # entries by their text, so an entry rewritten on the way through is a
    # lost entry as far as it can tell.
    moved = [items[i] for i in picked]
    for item in moved:
        new_archive = new_archive.replace(
            item, stamp_answer(item, outcome, today), 1
        )

    # Archiving the last live item empties the section, and `prompt.md`'s
    # house style for that is `**Nothing.**` -- the form the site agrees
    # with rather than the form that reads best.
    head, body, tail = _body(new_live, SPEC)
    if not split_items(body):
        new_live = head + "\n" + EMPTY_BLOCK + "\n" + tail

    return new_live, new_archive, moved
