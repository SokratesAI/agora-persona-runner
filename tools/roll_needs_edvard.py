"""Move answered items out of `journal-digest.md`'s **Needs Edvard** block.

Edvard, issue #89, rated 🔴 Immediately: *"The 'need Edvard' block contains
all previous answers to previous topics. This makes the block very long,
unfriendly to use and confusing. I have absolutely no idea what the current
'needs Edvard' asks of me."*

That block is the one channel where this loop asks him things, and he is
saying it does not work as one. `prompt.md` has always described it
correctly -- *"a live list of decisions only he can make, not a log. Remove
what he's answered"* -- and the file drifted anyway, so the rule is not the
fix. The mechanism behind the drift: every cycle rewrites that section from
scratch, and the cheapest way to rewrite a section you did not write is to
keep what is there and add to it. Deleting an item requires being sure it
was answered; keeping it requires nothing. So answered items accumulate by
default, which is exactly what he described.

This gives the unsure cycle somewhere to *put* an item instead of a reason
to leave it, in the same family as `roll_digest.py` and for the same
reason: a rule that lives only in prose is a rule every cycle has to
remember, and this journal's record is that they don't.

    python3 -m tools.roll_needs_edvard --live live.md --archive archive.md
    python3 -m tools.roll_needs_edvard --live live.md --archive archive.md \
        --answered 'spending limit' --outcome 'He raised it to $20 on 08-16.'

With no `--answered` it writes nothing and prints the live block with each
item's age, which is the reading that makes staleness visible -- an ask
that has been sitting there eleven days is either dead or genuinely
blocking, and either way a cycle can now see which.

**The selection is by substring, not by position.** The block is renumbered
on every rewrite, so an index is a name that changes under whoever holds
it; a phrase from the ask itself does not. A substring matching no item, or
more than one, is refused rather than guessed at.

Vault I/O stays out of here exactly as it does in `roll_digest.py` -- the
two files come in as paths and go out as paths, so this runs from either
pod with whichever vault client that pod actually has:

    python3 /app/bridge/vault_tool.py get '<digest>'  > live.md
    python3 /app/bridge/vault_tool.py get '<archive>' > archive.md
    python3 -m tools.roll_needs_edvard --answered '...' --outcome '...'
    python3 /app/bridge/vault_tool.py put '<archive>' archive.md   # archive FIRST
    python3 /app/bridge/vault_tool.py put '<digest>'  live.md
"""

import argparse
import datetime
import re
import sys

from agora_runner.config import OSLO
from agora_runner.nova_journal import is_empty_needs
from tools import rolling
from tools.rolling import RollError, RollSpec, join_paragraphs, plan, verify

MARKER = "\n## Needs Edvard\n"
ARCHIVE_TITLE = "# Needs Edvard — Answered"

# `**Since 08-15**` -- the date the ask was first put to him. Written by the
# cycle that raises the item; this tool only reads it, and only to say how
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


def split_items(text):
    """Blank-line separated paragraphs, in file order.

    Deliberately not `split_digest_entries`: that splitter knows where a
    `**Cycle N**` line ends, and these items carry no cycle number. A
    Needs Edvard item is one paragraph, which is the shape every cycle has
    written since the section existed.
    """
    return [p.strip() for p in re.split(r"\n[ \t]*\n", text) if p.strip()]


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
    keep=0,  # unused: this caller always selects explicitly, never by age
    noun="items",
    check_archive=_check_archive,
)


def age_days(item, today):
    """Days since the item's `**Since MM-DD**` stamp, or None if unstamped.

    The year is not in the stamp, so a stamp in the future is read as last
    year rather than as a negative age -- which is what a January cycle
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
    one channel Edvard has, or leaving an answered one in it. Neither is
    worth saving the caller a retype.
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


def _describe(items, today):
    if not items:
        return ["The Needs Edvard block is empty — nothing is waiting on him."]
    lines = []
    for item in items:
        days = age_days(item, today)
        if days is None:
            age = "undated (no **Since MM-DD** stamp — add one when you next rewrite it)"
        elif days == 0:
            age = "raised today"
        else:
            age = f"waiting {days} day{'s' if days != 1 else ''}"
        first = " ".join(item.split())
        lines.append(f"  [{age}] {first[:140]}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", default="live.md")
    parser.add_argument("--archive", default="archive.md")
    parser.add_argument(
        "--answered",
        action="append",
        default=[],
        metavar="PHRASE",
        help="a phrase from the item he has now answered; repeatable",
    )
    parser.add_argument(
        "--outcome",
        default="",
        help="what the answer was, recorded beside the ask in the archive",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # Oslo, not the system clock. The pod runs UTC and Edvard reads Oslo
    # (UTC+2 in August), so between 22:00 and 23:59 UTC `date.today()` is
    # still on yesterday while his calendar has already turned over -- the
    # `**Answered MM-DD**` stamp and every age this tool prints would be a
    # day behind for that window, every night. `nova_journal.entry_times`
    # uses OSLO for exactly this reason; reviewer caught that this file did
    # not.
    today = datetime.datetime.now(OSLO).date()
    live = open(args.live).read()
    try:
        archive = open(args.archive).read()
    except FileNotFoundError:
        archive = ""

    head, body, _tail = rolling._body(live, SPEC)
    # A section that only says `**Nothing**` holds no items, and reading it
    # as one would let a cycle solemnly archive the word Nothing and then
    # write a second one next cycle. `is_empty_needs` is the site's own
    # test for this, reused rather than re-derived so the two cannot drift
    # about what "empty" means. Note it is stricter than it looks: today's
    # live file opens `**Nothing blocking.** You answered both of...`, which
    # is a real paragraph and is correctly listed as an item.
    items = [] if is_empty_needs(body) else split_items(body)

    if not args.answered:
        print(f"{len(items)} live item(s) in **Needs Edvard**:")
        for line in _describe(items, today):
            print(line)
        print("nothing archived: pass --answered '<phrase>' --outcome '<what he said>'")
        return 0

    # `--outcome` is free text and is spliced into the archive *after*
    # `verify` has run `_check_archive` on it, so the heading guard cannot
    # see it -- the reviewer reproduced this against the real digest and
    # got a `##` heading written to disk with a clean exit 0. Check the
    # text here, before anything is written, rather than moving the splice
    # earlier: `verify`'s invariant is that entries pass through unchanged,
    # and stamping before it would mean loosening the one check that proves
    # nothing was dropped.
    if re.search(r"^#{1,6}[ \t]", args.outcome, re.MULTILINE):
        raise RollError(
            "refusing to roll: --outcome contains a markdown heading, which "
            "would land in the archive as a rival section. Quote it inline "
            "or drop the leading '#'."
        )
    if not args.outcome.strip():
        raise RollError(
            "refusing to roll: --answered needs --outcome saying what the "
            "answer was. An item archived with no answer is the same "
            "unreadable log one folder over."
        )

    picked = select_answered(items, args.answered)
    new_live, new_archive = plan(
        live, archive, SPEC, select=lambda parsed: picked
    )
    verify(live, archive, new_live, new_archive, SPEC, ordered=False)

    # The answer is stamped on *after* `verify`, deliberately. `verify`'s
    # invariant is "out equals in, with duplicates collapsed" -- it compares
    # entries by their text, so an entry rewritten on the way through is a
    # lost entry as far as it can tell. Adding the stamp before it would
    # mean either a refusal or loosening the one check that proves nothing
    # was dropped, and the stamp is exactly the edit that check should not
    # have to trust.
    for item in (items[i] for i in picked):
        new_archive = new_archive.replace(
            item, stamp_answer(item, args.outcome, today), 1
        )

    # Archiving the last live item empties the section, and the exact text
    # matters: `is_empty_needs` is what hides the box on Edvard's page, and
    # it accepts only "nothing" or "none" once emphasis and a trailing full
    # stop are stripped. `**Nothing blocking.**` -- the phrase the live file
    # carries today -- does *not* pass it, so writing that would leave him
    # an empty box claiming his attention forever. `prompt.md`'s own house
    # style for the section is `**Nothing**`; this writes the form the site
    # agrees with rather than the form that reads best.
    if not split_items(rolling._body(new_live, SPEC)[1]):
        new_live = head + "\n**Nothing.**\n" + _tail

    print(f"verified: {len(picked)} item(s) archived, {len(items) - len(picked)} still live")
    for item in (items[i] for i in picked):
        print(f"  archived: {' '.join(item.split())[:120]}")
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    # Archive first: the two vault writes are not atomic and stopping
    # between them must be able to duplicate an item, never to lose one.
    open(args.archive, "w").write(new_archive)
    open(args.live, "w").write(new_live)
    print(f"wrote {args.archive}, then {args.live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
