"""Move answered items out of `journal-digest.md`'s **Needs Edvard** block.  (not-prose: quoting a literal)

The owner, issue #89, rated 🔴 Immediately: *"The 'need the owner' block contains
all previous answers to previous topics. This makes the block very long,
unfriendly to use and confusing. I have absolutely no idea what the current
'needs the owner' asks of me."*

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
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.config import OSLO
from agora_runner.nova_needs import (  # noqa: F401 -- re-exported for callers
    ARCHIVE_FRONTMATTER,
    ARCHIVE_TITLE,
    MARKER,
    SPEC,
    age_days,
    archive_answered,
    live_items,
    select_answered,
    split_items,
    stamp_answer,
)
from agora_runner import rolling  # noqa: F401 -- callers reach through this module
from agora_runner.rolling import RollError, plan, verify  # noqa: F401


def _describe(items, today):
    if not items:
        return ["The Needs Edvard block is empty \u2014 nothing is waiting on him."]
    lines = []
    for item in items:
        days = age_days(item, today)
        if days is None:
            age = "undated (no **Since MM-DD** stamp \u2014 add one when you next rewrite it)"
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

    # Oslo, not the system clock. The pod runs UTC and the owner reads Oslo
    # (UTC+2 in August), so between 22:00 and 23:59 UTC `date.today()` is
    # still on yesterday while his calendar has already turned over -- the
    # `**Answered MM-DD**` stamp and every age this tool prints would be a
    # day behind for that window, every night.
    today = datetime.datetime.now(OSLO).date()
    live = open(args.live).read()
    try:
        archive = open(args.archive).read()
    except FileNotFoundError:
        archive = ""

    items = live_items(live)

    if not args.answered:
        print(f"{len(items)} live item(s) in **Needs Edvard**:")
        for line in _describe(items, today):
            print(line)
        print("nothing archived: pass --answered '<phrase>' --outcome '<what he said>'")
        return 0

    new_live, new_archive, moved = archive_answered(
        live, archive, args.answered, args.outcome, today
    )

    print(f"verified: {len(moved)} item(s) archived, {len(items) - len(moved)} still live")
    for item in moved:
        print(f"  archived: {' '.join(item.split())[:120]}")
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    # Archive first: the two writes are not atomic and stopping between
    # them must be able to duplicate an item, never to lose one.
    open(args.archive, "w").write(new_archive)
    open(args.live, "w").write(new_live)
    print(f"wrote {args.archive}, then {args.live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
