"""Append this week's goal numbers to `goal-history.json`, read off `goals.md`.

Idea #38 asked for history and charts against the goals. The weekly
review has been writing one number per goal into the ```goal``` fence and
overwriting last week's on the way past, so there was never a series to
draw. This takes the snapshot -- it reads the fences the review has just
finished editing and files them under a date, which means the numbers on
the chart are the same numbers on the page by construction rather than by
a cycle remembering to type them twice.

Run it at the end of the Monday review, after `goals.md` is written:

    cd /data/workspace/agora-persona-runner
    G='projects/sokrates/projects/nova/goals.md'
    H='projects/sokrates/projects/agora/nova/resources/goal-history.json'
    python3 /app/bridge/vault_tool.py get "$G" > goals.md \\
      && python3 /app/bridge/vault_tool.py get "$H" --rev-file /tmp/gh.$$.rev > history.json \\
      && python3 -m tools.append_goal_snapshot --goals goals.md --history history.json \\
           --date "$(TZ=Europe/Oslo date +%F)" --cycle <N> \\
      && python3 /app/bridge/vault_tool.py put "$H" history.json --if-rev-file /tmp/gh.$$.rev

Vault I/O is deliberately not in here, the same as `append_retro.py` and
`roll_digest.py`: files come in as paths and go out as the same paths, so
it runs from either pod with whichever vault client that pod has.

Exit 0 wrote a row, exit 2 refused and said why. It refuses a date the
ledger already carries, so running it twice on one Monday is safe rather
than silently doubled -- and a refusal is the signal that the review
already ran, which is worth more than an idempotent no-op would be.

`--print` writes the new ledger to stdout instead, for a dry run.
"""

import argparse
import json
import re
import sys

from agora_runner.nova_goal_history import GoalHistoryError, append, goal_key, load
from agora_runner.nova_plan import _fenced, _goal


# What `vault_tool.py get` prints for a path that does not exist. It
# exits 0 and prints this, so the documented flow above hands the *first*
# snapshot a file containing a sentence rather than an empty one. Same
# guard, same reason, as `append_retro._read` -- and the same anchoring:
# a real ledger that merely opened with those words must not read as
# absent, because absent here means "start from scratch".
_ABSENT_RE = re.compile(r"\s*\[not found[^\]]*\]\s*$")


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return ""
    return "" if _ABSENT_RE.match(text) else text


def snapshot(goals_markdown):
    """`goals.md` -> `{goal key: number}` for every goal with a number.

    A goal whose `now:` is not a number is skipped rather than refused.
    `nova_plan._goal` already treats that as a legitimate state -- the
    scoreboard prints "not measured yet" -- and a review that has not
    got to one goal yet should still be able to file the four it did.
    """
    blocks, _text = _fenced(goals_markdown or "", {"goal": _goal})
    values = {}
    for goal in blocks["goal"]:
        if goal["nowValue"] is None:
            continue
        key = goal_key(goal["name"])
        if key in values:
            # Two goals with the same short id -- a copy-paste when a
            # sixth goal was added, an id half-renumbered. Overwriting
            # would file one of them and lose the other for that week,
            # silently and permanently.
            raise GoalHistoryError(f"two goals in that file are keyed {key!r}: {goal['name']!r}")
        values[key] = goal["nowValue"]
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--goals", required=True, help="path to a copy of goals.md")
    parser.add_argument("--history", required=True, help="path to goal-history.json")
    parser.add_argument("--date", required=True, help="the review's date, YYYY-MM-DD Oslo")
    parser.add_argument("--cycle", required=True, type=int, help="the cycle taking the snapshot")
    parser.add_argument(
        "--print", dest="to_stdout", action="store_true", help="write to stdout, not the file"
    )
    args = parser.parse_args(argv)

    try:
        with open(args.goals, encoding="utf-8") as handle:
            goals_markdown = handle.read()
    except OSError as exc:
        print(f"cannot read goals.md: {exc}", file=sys.stderr)
        return 2

    try:
        values = snapshot(goals_markdown)
    except GoalHistoryError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    if not values:
        # Not an empty snapshot: `goals.md` with no readable ```goal```
        # fence is a file that did not fetch, or a slate somebody has
        # just rewritten by hand into a shape this cannot read. Writing
        # a row would record "the week we measured nothing".
        print(
            "refusing to write: no goal in that file carries a numeric 'now:'",
            file=sys.stderr,
        )
        return 2

    row = {"date": args.date, "cycle": args.cycle, "values": values}
    document = _read(args.history)
    try:
        updated = append(document, row)
    except GoalHistoryError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        # Not GoalHistoryError: the *existing* ledger is unreadable, not
        # the new row, and those two want different fixes. Starting over
        # from empty would delete every earlier week on the way past.
        print(f"refusing to write: the existing ledger will not parse: {exc}", file=sys.stderr)
        return 2

    if args.to_stdout:
        sys.stdout.write(updated)
        return 0

    with open(args.history, "w", encoding="utf-8") as handle:
        handle.write(updated)
    print(
        f"snapshot {args.date} (cycle {args.cycle}): "
        f"{', '.join(f'{k}={v}' for k, v in sorted(values.items()))}; "
        f"{len(load(updated))} week(s) in the ledger"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
