"""Retire finished items out of `journal-digest.md`'s **Next cycle** block.

Third roller in the same family as `roll_digest` and `roll_needs_edvard`,
against the one section of that file nothing trims. Measured Cycle 672 on
the live digest: **84,015 of its 94,602 bytes are `## Next cycle`** -- 59
items -- while `roll_digest` trims the `## Digest` section below it and
`roll_needs_edvard` trims the block above it. So the roller that exists
maintains 11% of the file and the 89% grows forever, and `prompt.md` step
1a forbids a cycle from delegating that read.

The accumulation has the same cause `roll_needs_edvard`'s docstring names
for the block above: every cycle rewrites the section, the cheapest way to
rewrite a section you did not write is to keep it and add to it, and
dropping an item requires being sure it is finished while keeping it
requires nothing. What makes it worse here is that most of these items are
phrased as *"do not re-measure this"* -- so leaving one in looks actively
responsible, and the section grows by the very instinct that should prune
it.

**Selection is by slug, and the slug is not a convenience.** Every item
opens `**[some-slug] ...`, written by the cycle that raised it, and that
is a stable name for the item across the rewrites -- unlike a position,
which changes on every cycle, and unlike a substring, which
`roll_needs_edvard` has to fall back on because its items carry no name at
all. A slug naming no item, or two, is refused rather than guessed at.

An item with no slug at all is legal and cannot be selected. There are two
in the live file today and both are standing instructions rather than
findings; refusing to name them is the conservative half of that, and
adding a slug when you next rewrite one is how it becomes retirable.

    python3 -m tools.roll_handoff --live live.md --archive archive.md
    python3 -m tools.roll_handoff --live live.md --archive archive.md \
        --retire nas-dashboard-and-nzbget-are-done-do-not-redo-them \
        --reason 'Superseded: nas_watch judges all four surfaces now.'

With no `--retire` it writes nothing and prints the live block, each item
with its slug, the newest cycle number it cites and its size in bytes --
the reading that makes the 84KB visible, since no single item looks
expensive and the section is nothing but items.

Vault I/O is deliberately not in here, exactly as in the other two -- the
files come in as paths and go out as paths, so this runs from either pod
with whichever vault client that pod actually has:

    python3 /app/bridge/vault_tool.py get '<digest>'  > live.md
    python3 /app/bridge/vault_tool.py get '<archive>' > archive.md
    python3 -m tools.roll_handoff --retire '<slug>' --reason '...'
    python3 /app/bridge/vault_tool.py put '<archive>' archive.md   # archive FIRST
    python3 /app/bridge/vault_tool.py put '<digest>'  live.md
"""

import re

from agora_runner.nova_journal import split_needs_items
from agora_runner.rolling import RollError, RollSpec, _body, join_paragraphs, plan, verify

MARKER = "\n## Next cycle\n"
ARCHIVE_TITLE = "# Next cycle — Retired"

# `**[slug] ` -- the item's name, written by the cycle that raised it.
# Anchored at the start because these bodies quote each other's slugs in
# bold brackets constantly, and a matcher that accepted one further into
# the prose would name an item after whatever it happened to cite.
#
# The anchor is written twice -- `\A` here and `.match` in `item_slug` --
# and that is recorded rather than tidied because it changes what the
# mutation round can tell you. Breaking either one alone leaves the tests
# green, not because they pin nothing but because the other anchor still
# holds; breaking both together fails five of them. So the number worth
# quoting for this line is five, against the pair.
_SLUG_RE = re.compile(r"\A\*\*\[(?P<slug>[a-z0-9][a-z0-9-]*)\]")

# `Cycle 671` anywhere in the body. Used only to print how old an item is;
# an item citing no cycle prints as undated rather than as new.
_CYCLE_RE = re.compile(r"\bCycle (?P<n>\d+)\b")

ARCHIVE_FRONTMATTER = (
    "---\n"
    "type: log\n"
    "tags: [agora, evolution, self-improvement, agent-context]\n"
    "status: built\n"
    "maintenance: Items that have left the **Next cycle** block in "
    "journal-digest.md because they were finished or superseded, newest "
    "first, each with the reason it was retired. Append only, written by "
    "tools/roll_handoff.py. This file is Nova's handoff history, not "
    "the owner's -- he does not read either one. Nothing renders it.\n"
    "---\n\n"
)

# One paragraph is one item. Measured against the live digest, Cycle 672:
# 61 paragraphs, 59 of them opening with a slug and the other two being
# whole items in their own right, so paragraph splitting and item
# splitting are the same operation here. Shared with the **Needs Edvard**  (not-prose: quoting a literal)
# splitter rather than re-derived, for the reason `nova_needs` gives:
# two definitions of where an item ends is one disagreement waiting.
split_items = split_needs_items

# The stamp `stamp_retired` writes below an archived item, matched at the
# start of a paragraph only.
_STAMP_RE = re.compile(r"^\*\*Retired \d{2}-\d{2}\*\*")


def count_items(text):
    """The items in `text`, not counting the retirement stamps under them.

    `verify` counts with this and `plan` reconstructs with `split_items`,
    and the two must stay separate. A stamp is its own paragraph, so the
    paragraph splitter reads it back as an item; two items retired on one
    day with one reason leave two identical stamp paragraphs, `dedup`
    collapses them on the before side and not on the after side, and every
    multi-item roll is refused with `N in, N+4 out`.

    The tempting fix is to filter stamps out of `split_items` itself.
    **Do not.** `rolling.plan` rebuilds the archive from that same
    splitter, so filtering there writes an archive with every earlier
    roll's reason deleted -- it did, on the live file, and the 29 lost
    stamps had to be recovered from the hourly vault mirror (Cycle 792).
    Counting is allowed to ignore text; reconstructing is not.
    """
    return [item for item in split_items(text) if not _STAMP_RE.match(item)]


def _check_archive(new_archive):
    """No level-two heading in the archive.

    Same guard as the other two archives in this folder, for the same
    reason: `parse_digest` reads named `##` sections, the three archives
    sit beside the live digest, and a cycle repairing one by hand will
    reach for another as its example.
    """
    if re.search(r"^##[ \t]+", new_archive, re.MULTILINE):
        raise RollError(
            "refusing to write: the handoff archive has a level-two "
            "heading, which would make it a rival section if this file is "
            "ever rendered beside the digest"
        )


SPEC = RollSpec(
    marker=MARKER,
    archive_title=ARCHIVE_TITLE,
    archive_frontmatter=ARCHIVE_FRONTMATTER,
    split_entries=split_items,
    count_entries=count_items,
    join_entries=join_paragraphs,
    keep=0,  # unused: the caller always names slugs, never rolls by age
    noun="handoff items",
    check_archive=_check_archive,
)


def live_items(live):
    """The handoff items currently in the section, in file order."""
    return split_items(_body(live, SPEC)[1])


def item_slug(item):
    """The item's `[slug]`, or None if it was written without one."""
    match = _SLUG_RE.match(item)
    return match.group("slug") if match else None


def newest_cycle(item):
    """The highest cycle number the item cites, or None if it cites none."""
    numbers = [int(m.group("n")) for m in _CYCLE_RE.finditer(item)]
    return max(numbers) if numbers else None


def select_slugs(items, slugs):
    """Indices of the items named by each slug, one item per slug.

    Refuses on nothing matched and on more than one matched, the same way
    `nova_needs.select_answered` does and for a sharper reason: this
    section is where the loop keeps its "do not redo this" findings, so a
    slug guessed wrong drops a guard nobody will notice is gone until a
    later cycle repeats the work it was written to prevent.
    """
    picked = []
    by_slug = {}
    for index, item in enumerate(items):
        slug = item_slug(item)
        if slug is not None:
            by_slug.setdefault(slug, []).append(index)
    for slug in slugs:
        name = slug.strip()
        hits = by_slug.get(name, [])
        if not hits:
            raise RollError(
                f"refusing to roll: no **Next cycle** item is named "
                f"[{name}]"
            )
        if len(hits) > 1:
            raise RollError(
                f"refusing to roll: {len(hits)} **Next cycle** items are "
                f"named [{name}] -- the section has a duplicate slug and "
                "guessing which one to retire is not this tool's call"
            )
        if hits[0] in picked:
            raise RollError(
                f"refusing to roll: [{name}] names an item already named "
                "by an earlier --retire"
            )
        picked.append(hits[0])
    return picked


def stamp_retired(item, reason, today):
    """The archived copy: the item as written, then why it left."""
    return f"{item}\n\n**Retired {today:%m-%d}** — {reason.strip()}"


def archive_retired(live, archive, slugs, reason, today):
    """Move the items named by `slugs` out of the live section.

    Returns `(new_live, new_archive, moved)` and writes nothing -- the
    caller owns both files and the order they are written in.
    """
    # Checked here rather than left to `_check_archive`, for the reason
    # `nova_needs.archive_answered` gives: the reason is spliced in after
    # `verify` has run, so the heading guard cannot see it.
    if re.search(r"^#{1,6}[ \t]", reason, re.MULTILINE):
        raise RollError(
            "refusing to roll: the reason contains a markdown heading, "
            "which would land in the archive as a rival section. Quote it "
            "inline or drop the leading '#'."
        )
    if not reason.strip():
        raise RollError(
            "refusing to roll: a retired handoff item needs a reason saying "
            "why it is finished. An item archived with no reason is exactly "
            "the unreadable log this tool exists to stop growing."
        )

    items = live_items(live)
    picked = select_slugs(items, slugs)
    if len(picked) == len(items):
        raise RollError(
            "refusing to roll: that would retire every item in **Next "
            "cycle** and leave the next cycle no handoff at all"
        )
    new_live, new_archive = plan(live, archive, SPEC, select=lambda parsed: picked)
    verify(live, archive, new_live, new_archive, SPEC, ordered=False)

    # Stamped after `verify`, deliberately: `verify` compares entries by
    # their text, so an entry rewritten on the way through reads to it as
    # a lost entry.
    moved = [items[i] for i in picked]
    for item in moved:
        new_archive = new_archive.replace(
            item, stamp_retired(item, reason, today), 1
        )
    return new_live, new_archive, moved
