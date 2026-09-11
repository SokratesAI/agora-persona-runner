"""Seat every open row on his two boards, once -- issue #202's part 1.

    python3 -m tools.seed_row_order            # print the plan, write nothing
    python3 -m tools.seed_row_order --write    # write it, then redraw his files

His row, 2026-09-10: *"order every task inside its milestone seeded from the
rating it carries today (Immediately at the top, unrated at the bottom,
once)"*. The rule is `nova_next.seed_seats`; this reads both boards out of
the record store, hands it their open rows, and writes one `order` per row
through `board_write.change_row`, which checks the whole board after every
write.

Only unseated rows are written, so the restore point is exact: every row it
touched had no seat before. The list of what it wrote is saved to
`--restore-file` before the first write, and `--unseed FILE` puts each of
those rows back to no seat.

Run from the bridge pod it does not go through the site, so nothing asks
nova-site to redraw his markdown; `--write` runs `tools.board_publish
--publish` for each board it changed, the same call a cycle makes by hand.
"""

import argparse
import json
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records, board_store, board_write  # noqa: E402
from agora_runner.nova_next import open_rows_from_contents, seed_seats  # noqa: E402

BOARDS = ("issue", "idea")
_FAILURES = (board_write.WriteRefused, board_write.BoardDamaged,
             board_records.RecordError)


def plan(store=board_store):
    """`([(board, number, seat)], skipped groups)`, off one read per board."""
    rows = []
    for board in BOARDS:
        rows += open_rows_from_contents(
            board_records.contents(board, store=store), board)
    seats, skipped = seed_seats(rows)
    return sorted((b, n, s) for (b, n), s in seats.items()), skipped


def write(moves, store=board_store):
    """Write each seat; `(written, problem or None)`. Stops at the first refusal."""
    for done, (board, number, seat) in enumerate(moves):
        try:
            board_write.change_row(board, number, {"order": seat}, store=store)
        except _FAILURES as problem:
            return done, f"#{number} on {board}: {problem}"
    return len(moves), None


def publish(boards):
    """Redraw his markdown for each board; the names of any that failed."""
    failed = []
    for board in boards:
        run = subprocess.run(
            [sys.executable, "-m", "tools.board_publish", "--board", board,
             "--publish"], capture_output=True, text=True)
        print(run.stdout.rstrip() or f"board_publish {board}: no output")
        if run.returncode:
            print(run.stderr.rstrip(), file=sys.stderr)
            failed.append(board)
    return failed


def main(argv=None, store=board_store, redraw=publish):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="write the seats; without it nothing is written")
    parser.add_argument("--restore-file",
                        default="/data/workspace/row-order-seed-restore.json",
                        help="where --write saves the rows it is about to seat")
    parser.add_argument("--unseed", metavar="FILE",
                        help="put every row listed in FILE back to no seat")
    args = parser.parse_args(argv)

    if args.unseed:
        with open(args.unseed) as handle:
            listed = json.load(handle)
        done, problem = write([(b, n, None) for b, n, _ in listed], store=store)
        print(f"unseated {done} of {len(listed)} row(s)")
        if problem:
            print(f"stopped: {problem}", file=sys.stderr)
            return 1
        return 0 if not redraw({b for b, _, _ in listed}) else 1

    moves, skipped = plan(store=store)
    for board in BOARDS:
        mine = [m for m in moves if m[0] == board]
        print(f"{board}: {len(mine)} open row(s) to seat")
    for project, milestone in skipped:
        print(f"skipped, ordered by hand: {project or '(no project)'} / "
              f"{milestone or '(no milestone)'}")
    if not moves:
        print("nothing to seat")
        return 0
    if not args.write:
        print("plan only -- pass --write to seat them")
        return 0

    with open(args.restore_file, "w") as handle:
        json.dump([list(m) for m in moves], handle)
    print(f"restore point: {args.restore_file} "
          f"(python3 -m tools.seed_row_order --unseed {args.restore_file})")
    done, problem = write(moves, store=store)
    print(f"seated {done} of {len(moves)} row(s)")
    failed = redraw(sorted({b for b, _, _ in moves[:done]}))
    if problem:
        print(f"stopped: {problem} -- run it again to finish; it only "
              "writes rows that still have no seat", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
