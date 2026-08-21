"""Move one comment from `## New` to `## Acknowledged`, as a command.

`prompt.md` step 1a tells every cycle to read `## New`, act on what
Edvard said, then "move each one under `## Acknowledged` with one line
on what you did". That instruction has been carried out by hand, in
a throwaway script, once an hour, for weeks -- and on 2026-08-13 at
07:06 one of those scripts spliced Edvard's newest comment into the
middle of the file's YAML frontmatter, because it looked for
`## Acknowledged` with a substring search and the frontmatter quotes
that heading in its own `contract:` line. The app's parser cannot see a
comment there. Neither can the next cycle. It was found by accident,
fifteen minutes later, by a cycle doing something else.

A rule in prose did not stop that and a fourth restatement of it would
not either. A command does, so this is the command. It is the same
argument `roll_digest.py` and `lint_entry.py` make: the moment a
mistake is cheap is before the write, and at that moment the author is
still here.

    python3 -m tools.ack_comment comments.md --cycle 156 \\
        --stamp '2026-08-13 06:44' --note 'Filed as issue #73, done.'

    python3 -m tools.ack_comment comments.md --needs \\
        --stamp '2026-08-12 10:42' --note 'Moved both boards, Cycle 133.'

Vault I/O stays outside, same as every other tool here, so it runs from
either pod with whichever client that pod has:

    python3 /app/bridge/vault_tool.py get '<comments>' --rev-file /tmp/c.rev > c.md
    python3 -m tools.ack_comment c.md --cycle 156 --stamp '...' --note '...'
    python3 /app/bridge/vault_tool.py put '<comments>' c.md --if-rev-file /tmp/c.rev

It refuses rather than guesses. Before writing it re-parses its own
output and checks that the frontmatter is byte-identical, that the set
of comments is unchanged, that the one named moved from new to
acknowledged carrying Edvard's text and its existing reply unaltered,
and that every other comment is untouched in all four of those
respects. A move that cannot prove all of it is not written, and the
file on disk is left exactly as it was found.

What it deliberately does *not* prove is anything about blank lines,
because `parse_comments` cannot see them -- and the first version of
this tool left a doubled blank line behind at the cut, which is where a
card ends on Edvard's screen. That is pinned by a test on the output
text rather than by `_verify`, and the distinction is worth keeping in
mind before trusting the verifier with a new kind of damage.
"""

import argparse
import sys

from agora_runner.md_sections import find_heading, section_bounds
from agora_runner.nova_comments import (
    ACKNOWLEDGED_HEADING,
    NEW_HEADING,
    WriteRefused,
    comment_index,
    format_stamp,
    verify_write,
)


class AckError(Exception):
    """Refusing to write. The message is for a human, so it says what to do."""


def _block_bounds(lines, start, end, cycle, stamp):
    """(first, last) line indexes of the comment `(cycle, stamp)` within [start, end).

    `first` is its `###` heading; `last` is one past its final non-blank
    line, so trailing blanks stay behind as the gap before whatever came
    after it rather than travelling with the block.
    """
    from agora_runner.nova_comments import _COMMENT_HEADING_RE, _SECTION_RE

    first = None
    for i in range(start, end):
        heading = _COMMENT_HEADING_RE.match(lines[i])
        if first is not None and (heading or _SECTION_RE.match(lines[i])):
            last = i
            break
        if not heading:
            continue
        found = int(heading.group("cycle")) if heading.group("cycle") else None
        if found == cycle and (heading.group("stamp") or "").strip() == stamp:
            first = i
    else:
        last = end

    if first is None:
        return None
    while last > first and not lines[last - 1].strip():
        last -= 1
    return first, last


def acknowledge(markdown, cycle, stamp, note, note_stamp):
    """Markdown with `(cycle, stamp)` moved to the top of `## Acknowledged`.

    `cycle` is an int, or None for a reply to the Needs Edvard block. The
    note is appended inside the comment as a `#### Nova` block, which is
    the same shape a live reply uses -- what a cycle did about a comment
    and what it said back are the same kind of thing to whoever reads the
    card later, and giving them two shapes would mean the site needs to
    know about both.
    """
    lines = (markdown or "").split("\n")
    before = comment_index(markdown)

    new_bounds = section_bounds(lines, NEW_HEADING)
    if new_bounds is None:
        raise AckError(f"no real {NEW_HEADING!r} heading in this file")
    ack_at = find_heading(lines, ACKNOWLEDGED_HEADING)
    if ack_at is None:
        raise AckError(f"no real {ACKNOWLEDGED_HEADING!r} heading in this file")

    found = _block_bounds(lines, *new_bounds, cycle=cycle, stamp=stamp)
    if found is None:
        label = "Needs Edvard" if cycle is None else f"Cycle {cycle}"
        raise AckError(
            f"no comment on {label} at {stamp!r} under {NEW_HEADING} -- "
            "check the heading in the file; nothing written"
        )
    first, last = found
    block = lines[first:last]
    if note:
        block = block + ["", f"#### Nova · {note_stamp}", "", note]

    # Cutting the block leaves its blank line behind next to the blank line
    # that preceded it. Two in a row is not cosmetic in this vault: a blank
    # line is where a card ends, so a doubled one splits what is left into
    # a second, empty card on Edvard's screen.
    tail = lines[last:]
    while tail and not tail[0].strip() and first > 0 and not lines[first - 1].strip():
        tail.pop(0)
    rest = lines[:first] + tail
    # Re-find the destination in `rest`: removing the block moved every line
    # below it up, and reusing an index taken before the cut is how an
    # off-by-one becomes a comment in the wrong section.
    ack_at = find_heading(rest, ACKNOWLEDGED_HEADING)
    at = ack_at + 1
    while at < len(rest) and not rest[at].strip():
        at += 1
    out = "\n".join(rest[:at] + block + [""] + rest[at:])

    _verify(markdown, out, before, cycle, stamp, note)
    return out


def _verify(original, updated, before, cycle, stamp, note):
    """Refuse the write unless exactly the intended change happened.

    The frontmatter and the bystanders are `nova_comments.verify_write`,
    shared with the two writers that run unattended -- adding a comment
    and replying to one. There must be one definition of "nothing else in
    this file changed", because all three writers are string surgery on
    the same document and the damage they can do is identical. What stays
    here is the part only a *move* can check.

    Note that the move is not in the exempt set: acknowledging a comment
    must not create or destroy one, so the set of keys is required to be
    conserved exactly, which is stricter than the two writers can be.
    """
    target = (cycle, stamp)
    try:
        _, after = verify_write(original, updated, exempt={target})
    except WriteRefused as refused:
        raise AckError(str(refused)) from None
    if set(after) != set(before):
        lost = sorted(str(k) for k in set(before) - set(after))
        gained = sorted(str(k) for k in set(after) - set(before))
        raise AckError(
            f"the set of comments changed (lost {lost}, gained {gained}) -- "
            "nothing written"
        )

    moved = after[target]
    if not moved["acknowledged"]:
        raise AckError(f"{target} did not end up under {ACKNOWLEDGED_HEADING}")
    if moved["text"] != before[target]["text"]:
        raise AckError(f"{target}'s text changed -- nothing written")
    # The note goes into its own `#### Nova` block, which is a *second* one
    # whenever the reply worker already answered him -- so look across every
    # block, not just the first. This used to read `moved["reply"]` and pass
    # because that field was once everything below the first heading; now
    # that each block is parsed separately (so the app can paint a cycle's
    # answer purple instead of printing its heading as text), the note is in
    # the last of them.
    if note and not any(note in reply["text"] for reply in moved["replies"]):
        raise AckError(f"{target} lost the note -- nothing written")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("path", help="a local copy of comments.md, edited in place")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--cycle", type=int, help="the cycle the comment is on")
    target.add_argument(
        "--needs", action="store_true", help="a reply to the Needs Edvard block"
    )
    parser.add_argument("--stamp", required=True, help="the comment's stamp, verbatim")
    parser.add_argument("--note", default="", help="one line on what you did")
    parser.add_argument("--dry-run", action="store_true", help="check, write nothing")
    args = parser.parse_args(argv)

    with open(args.path, encoding="utf-8") as handle:
        original = handle.read()

    try:
        updated = acknowledge(
            original,
            None if args.needs else args.cycle,
            args.stamp.strip(),
            args.note.strip(),
            format_stamp(),
        )
    except AckError as error:
        print(f"refusing: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("ok -- would move it; nothing written (--dry-run)")
        return 0
    with open(args.path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    label = "Needs Edvard" if args.needs else f"Cycle {args.cycle}"
    print(f"moved {label} · {args.stamp} to {ACKNOWLEDGED_HEADING}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
