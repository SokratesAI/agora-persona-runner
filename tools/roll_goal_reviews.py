"""Move weekly reviews older than the newest four out of `goals.md`.

Issue #96: the review stack grew about a thousand words a week with
nothing rolling it off. The transform and its reasons are
`agora_runner.nova_plan.roll_reviews`; the `/plan` page reads the archive
back in, so nothing leaves the page.

    python3 -m tools.roll_goal_reviews --goals goals.md --archive archive.md

Rewrites both files in place and prints what moved. Put the archive
first and `goals.md` second, so a failure between the two leaves an
entry in both places rather than in neither.
"""

import argparse
import sys

import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_plan import REVIEWS_KEPT, roll_reviews, with_archived_reviews


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--goals", required=True)
    parser.add_argument("--archive", required=True,
                        help="may be empty or missing; it is created")
    parser.add_argument("--keep", type=int, default=REVIEWS_KEPT)
    args = parser.parse_args(argv)
    goals = _pathlib.Path(args.goals).read_text()
    path = _pathlib.Path(args.archive)
    archive = path.read_text() if path.exists() else ""
    if archive.strip().startswith("[not found]"):
        archive = ""
    try:
        new_goals, new_archive, moved = roll_reviews(goals, archive, args.keep)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    if not moved:
        print(f"nothing to roll: {args.keep} or fewer reviews in goals.md")
        return 0
    # Every word must survive: the display merge of the two new files has
    # to read exactly like the old pair did.
    before = with_archived_reviews(goals, archive).split()
    after = with_archived_reviews(new_goals, new_archive).split()
    if before != after:
        print("REFUSED: the rolled pair does not read the same as before; "
              "nothing written", file=sys.stderr)
        return 1
    path.write_text(new_archive)
    _pathlib.Path(args.goals).write_text(new_goals)
    for heading in moved:
        print(f"moved: {heading}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
