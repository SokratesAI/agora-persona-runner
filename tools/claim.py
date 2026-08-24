"""Take a handoff item before you work on it, so an overlapping cycle can't.

Edvard, `issues.md` #74 — two cycles that overlap both read the same
**Next cycle** list and both do the same item. The decision lives in
`agora_runner.nova_claims`; this is the CLI, and the atomicity is
CouchDB's, which is why the `get`/`put` pair below is not optional.

Take an item:

    cd /data/workspace/agora-persona-runner
    C='projects/sokrates/projects/agora/nova/resources/claims.json'
    python3 /app/bridge/vault_tool.py get "$C" --rev-file /tmp/claim.$$.rev > claims.json
    python3 -m tools.claim take --ledger claims.json --item confirm-deploy-171 \
      --cycle 189 --note 'handoff item 1' \
      && python3 /app/bridge/vault_tool.py put "$C" claims.json --if-rev-file /tmp/claim.$$.rev

Release it the same way, with `release` and **one of two words you have to
type**:

    python3 -m tools.claim release --ledger claims.json --item confirm-deploy-171 \
      --cycle 189 --done --outcome 'merged #172'

`--done` spends the slug forever: nobody can `take` it again. `--progress`
gives it back -- the next cycle sees your `--outcome` beside a take command
that still works. Neither is a default, because for eleven days the default
was `--done` and three cycles used it for work that was still open (Cycle
343 on Edvard's 20x capture, Cycle 347 on idea #63, Cycle 281 on a board
bullet). The choice is the one thing this command knows that the ledger
cannot infer, so it is the one thing it refuses to guess.

Exit codes are the whole interface: **0 the item is yours, 2 somebody
else has it or already did it, 1 something is wrong**. So the `&&` above
means an unclaimed item is never written back as claimed, and a refusal
never touches the vault. If the `put` exits 3 you lost the
compare-and-swap to a cycle that claimed something in between -- start
over from the `get`, because your local `claims.json` was built on text
that no longer exists.

`list` prints the ledger without changing it and always exits 0.
"""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_claims import (
    DONE,
    PROGRESSED,
    ClaimError,
    dumps,
    load,
    release,
    summarise,
    take,
)

OSLO = ZoneInfo("Europe/Oslo")

#: A refusal is not an error -- it is the answer the caller asked for, and
#: it has to be distinguishable from a broken ledger, because one means
#: "pick another item" and the other means "stop and look".
REFUSED_EXIT = 2


class _Parser(argparse.ArgumentParser):
    """Argparse exits 2 on a usage error, and 2 is taken.

    2 means "somebody else has this item", and `prompt.md` tells every
    cycle to accept that answer without arguing. So a cycle that mistypes
    `--cycle`, or leaves the `<N>` placeholder unsubstituted, or drops
    `--ledger`, would be told a free item was taken -- and would silently
    go and do something else, exactly the way it was instructed to. A
    usage error is "stop and look", which is 1.
    """

    def error(self, message):
        self.exit(1, f"{self.prog}: error: {message}\n")


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def main(argv=None):
    parser = _Parser(
        description="Claim a handoff item so an overlapping cycle does not repeat it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("action", choices=["take", "release", "list"])
    parser.add_argument("--ledger", required=True, help="path to claims.json")
    parser.add_argument("--item", help="claim slug, e.g. confirm-deploy-171")
    parser.add_argument("--cycle", type=int, help="the cycle number doing the work")
    parser.add_argument("--note", help="what the item is, in a few words")
    parser.add_argument("--outcome", help="on release: what happened")
    # Deliberately not `default=DONE`. The whole bug is that stopping and
    # finishing looked like one act, and a default would put them back
    # together: the cycle that types nothing gets the answer that spends
    # the slug, which is exactly what happened three times.
    finished = parser.add_mutually_exclusive_group()
    finished.add_argument("--done", dest="release_state", action="store_const",
                          const=DONE,
                          help="on release: the work is finished, spend the slug")
    finished.add_argument("--progress", dest="release_state", action="store_const",
                          const=PROGRESSED,
                          help="on release: you stopped, the work did not -- "
                               "the next cycle can take it and will see --outcome")
    args = parser.parse_args(argv)

    now = datetime.now(OSLO)
    try:
        ledger = load(_read(args.ledger))
    except ClaimError as exc:
        print(f"claim: {exc}", file=sys.stderr)
        return 1

    if args.action == "list":
        print(summarise(ledger, now))
        return 0

    if not args.item or args.cycle is None:
        print(f"claim: {args.action} needs --item and --cycle", file=sys.stderr)
        return 1

    if args.action == "release" and args.release_state is None:
        # 1, not REFUSED_EXIT: 2 means "somebody else has this" and every
        # cycle is told to accept a 2 without arguing, so a 2 here would
        # be read as "already handled" and the claim would be left open.
        print("claim: release needs --done or --progress. --done spends the slug "
              "forever; --progress hands the item on with your --outcome beside "
              "it. If the work is not finished, it is --progress.", file=sys.stderr)
        return 1

    try:
        if args.action == "take":
            ok, message = take(ledger, args.item, args.cycle, now, note=args.note)
        else:
            ok, message = release(ledger, args.item, args.cycle, now,
                                  outcome=args.outcome, state=args.release_state)
    except ClaimError as exc:
        print(f"claim: {exc}", file=sys.stderr)
        return 1

    if not ok:
        # Leave the ledger file exactly as it was read. The documented flow
        # puts the vault write behind `&&`, but a caller who ignores the
        # exit code still must not be able to write a refusal back as a
        # grant.
        print(message, file=sys.stderr)
        return REFUSED_EXIT

    with open(args.ledger, "w", encoding="utf-8") as handle:
        handle.write(dumps(ledger))
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
