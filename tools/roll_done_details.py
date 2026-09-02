"""Move the `# Details` write-up of a finished row off Nova's live board file.

The second half of the job `agora_runner/nova_site.board_payload` was
taught in Cycle 808. My own `issues.md` is one file holding three things:
the capture bullets under `## Entries`, the `## Board` table, and a
`# Details` write-up per row. `tools/roll_captures.py` rolls the first of
those and nothing rolled the third, so the write-ups only ever grew --
**143,470 bytes on 2026-09-02, 80,104 of it `# Details`**, which is past
what one `vault_tool.py get` can hand a cycle without the harness
swapping a preview in for it.

A row that is `✅ Done` is the safe half of that to move: it is finished,
nobody is going to write another paragraph under it, and the page can
already read a write-up out of the archive -- `board_payload` merges
`parse_board(archive)["details"]` into the live ones, live winning on a
collision. **That merge shipped first on purpose** and the comment in
`nova_site.py` says why: move a body before the page can read one and
every rolled row draws an empty write-up with nothing failing anywhere.

What moves is the write-up and nothing else. **The board row stays in the
live file**, with its status, its date and its rating, because an archived
write-up is still that row's write-up and the archive contributes bodies
and never rows -- `parse_board` over a file with no `## Board` table
returns no items at all.

    python3 -m tools.roll_done_details --live issues.md --archive issues-archive.md --dry-run
    python3 -m tools.roll_done_details --live issues.md --archive issues-archive.md

Then, the same two writes `roll_captures` does and in the same order --
archive first, so the worst case is a duplicated write-up rather than a
lost one:

    python3 /app/bridge/vault_tool.py put '<archive>' archive.md
    python3 /app/bridge/vault_tool.py put '<live>'    live.md --allow-shrink

`--allow-shrink` is on the live half for the same reason it is there in
`roll_captures`: the vault client refuses a write under a quarter of the
document it replaces, and a roll is the one legitimate operation shaped
like that failure. Today's move is 14% of the file rather than 75%, so
the flag is not needed yet; it is written here because the flag being
absent the day it *is* needed is a refusal a cycle will read as a bug.

**One block per number in the archive.** A number the archive already
carries has its old block replaced rather than a second one appended.
`_detail_spans` keeps the *first* block for a repeated number, so two
blocks would mean the page silently drew whichever one happened to sit
higher -- a duplicate here is not redundancy, it is a coin flip.
"""

import argparse
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_boards import _detail_spans, parse_board, parse_notes
from tools.rolling import RollError

DETAILS_HEADING = "# Details"


def _done_numbers(board):
    return [item["number"] for item in board["items"] if item["statusKey"] == "done"]


def _cut_blocks(markdown, numbers):
    """Return `(remaining_markdown, {number: block_text})`, in file order.

    Blocks are addressed through `_detail_spans` -- the same line indices
    the page reads a body from -- rather than by re-deriving a heading
    pattern here. `prompt.md`: a matcher defined twice is the duplication,
    not the second definition.
    """
    lines = (markdown or "").split("\n")
    spans = _detail_spans(markdown)
    wanted = sorted(
        ((spans[n][0], spans[n][2], n) for n in numbers if n in spans),
    )
    blocks = {}
    drop = set()
    for start, end, number in wanted:
        blocks[number] = "\n".join(lines[start:end]).rstrip()
        drop.update(range(start, end))
    remaining = "\n".join(line for i, line in enumerate(lines) if i not in drop)
    return remaining, blocks


def _splice_into_archive(archive, blocks):
    """Put each block at the top of the archive's `# Details`, newest first.

    Both files are newest-first and the live file is newer than the whole
    archive, so prepending is what preserves the order -- the same
    argument `board_payload` makes for appending the archive's notes
    below the live ones.
    """
    archive = (archive or "").rstrip("\n")
    replaced = _cut_blocks(archive, list(blocks))[0] if _detail_spans(archive) else archive
    if DETAILS_HEADING not in [l.strip() for l in replaced.split("\n")]:
        replaced = replaced.rstrip("\n") + "\n\n" + DETAILS_HEADING + "\n"
    lines = replaced.split("\n")
    at = next(
        i for i, line in enumerate(lines) if line.strip() == DETAILS_HEADING
    )
    new = "\n\n".join(blocks[n] for n in blocks)
    head = "\n".join(lines[: at + 1]).rstrip("\n")
    tail = "\n".join(lines[at + 1 :]).strip("\n")
    out = head + "\n\n" + new
    if tail:
        out += "\n\n" + tail
    return out + "\n"


def _merged(live, archive):
    """The details the page would draw, live winning on a collision."""
    merged = dict(parse_board(archive)["details"])
    merged.update(parse_board(live)["details"])
    return merged


def _check(live, archive, new_live, new_archive, moved):
    before, after = _merged(live, archive), _merged(new_live, new_archive)
    if before != after:
        raise RollError(
            "refusing to write: the page would draw "
            f"{len(after)} write-up(s) where it drew {len(before)}"
            + (
                ""
                if set(before) == set(after)
                else f" -- numbers {sorted(set(before) ^ set(after))} differ"
            )
        )
    for name, old, new in (
        ("live", live, new_live),
        ("archive", archive, new_archive),
    ):
        if parse_board(old)["items"] != parse_board(new)["items"]:
            raise RollError(
                f"refusing to write: the {name} board rows changed -- this "
                "moves write-ups and must never move a row"
            )
        if parse_notes(old) != parse_notes(new):
            raise RollError(
                f"refusing to write: the {name} capture bullets changed -- "
                "this moves write-ups and must never touch `## Entries`"
            )
    still = set(moved) & set(parse_board(new_live)["details"])
    if still:
        raise RollError(
            f"refusing to write: {sorted(still)} is still in the live file "
            "after being moved"
        )


def plan(live, archive):
    """`(new_live, new_archive, {number: block})`, or no-op with `{}`."""
    board = parse_board(live)
    numbers = [n for n in _done_numbers(board) if n in board["details"]]
    if not numbers:
        return live, archive, {}
    new_live, blocks = _cut_blocks(live, numbers)
    new_archive = _splice_into_archive(archive, blocks)
    _check(live, archive, new_live, new_archive, blocks)
    return new_live, new_archive, blocks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    live = _pathlib.Path(args.live).read_text()
    archive = _pathlib.Path(args.archive).read_text()
    try:
        new_live, new_archive, blocks = plan(live, archive)
    except RollError as exc:
        print(exc)
        return 1
    if not blocks:
        print("nothing to roll: no ✅ Done row has a write-up in the live file")
        return 0
    moved = sum(len(b) for b in blocks.values())
    print(
        f"{len(blocks)} write-up(s), {moved} bytes: "
        + ", ".join(f"#{n}" for n in blocks)
    )
    print(f"live {len(live)} -> {len(new_live)} bytes")
    print(f"archive {len(archive)} -> {len(new_archive)} bytes")
    if args.dry_run:
        print("dry run, nothing written")
        return 0
    _pathlib.Path(args.archive).write_text(new_archive)
    _pathlib.Path(args.live).write_text(new_live)
    print("written -- put the archive first, then the live file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
