"""Move one comment from `## New` to `## Acknowledged`, as a command.

`prompt.md` step 1a tells every cycle to read `## New`, act on what
Edvard said, then "move each one under `## Acknowledged` with one line
saying what it did". That instruction has been carried out by hand, in
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
output and checks three things: the frontmatter is byte-identical, the
set of comments is unchanged apart from the one named, and that one
moved from new to acknowledged carrying Edvard's text unaltered. A move
that cannot prove all three is not written, and the file on disk is left
exactly as it was found.
"""

import argparse
import sys

from agora_runner.md_sections import find_heading, section_bounds
from agora_runner.nova_comments import (
    ACKNOWLEDGED_HEADING,
    NEW_HEADING,
    format_stamp,
    parse_comments,
)


class AckError(Exception):
    """Refusing to write. The message is for a human, so it says what to do."""


def _key(comment):
    return (comment["cycle"], comment["stamp"])


def _frontmatter(text):
    """The frontmatter block including both `---` lines, or "" if there is none."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[: i + 1])
    return ""


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
    before = {_key(c): c for c in parse_comments(markdown)}

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

    rest = lines[:first] + lines[last:]
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
    """Refuse the write unless exactly the intended change happened."""
    if _frontmatter(updated) != _frontmatter(original):
        raise AckError(
            "the frontmatter changed -- this is the 2026-08-13 bug and the "
            "write is refused; nothing written"
        )

    after = {_key(c): c for c in parse_comments(updated)}
    if set(after) != set(before):
        lost = sorted(str(k) for k in set(before) - set(after))
        gained = sorted(str(k) for k in set(after) - set(before))
        raise AckError(
            f"the set of comments changed (lost {lost}, gained {gained}) -- "
            "nothing written"
        )

    target = (cycle, stamp)
    moved = after[target]
    if not moved["acknowledged"]:
        raise AckError(f"{target} did not end up under {ACKNOWLEDGED_HEADING}")
    if moved["text"] != before[target]["text"]:
        raise AckError(f"{target}'s text changed -- nothing written")
    if note and note not in moved["reply"]:
        raise AckError(f"{target} lost the note -- nothing written")

    for key, was in before.items():
        if key == target:
            continue
        now = after[key]
        if (now["text"], now["acknowledged"]) != (was["text"], was["acknowledged"]):
            raise AckError(f"{key} changed too -- nothing written")


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
