"""Append one Friday retrospective to `retro-ledger.json`, or refuse.

Edvard asked the Friday cycle to *"actually note down data and compare it
to previous retros"*. This is how a retro cycle writes its row without
having to know the shape by heart -- the shape lives in
`agora_runner.nova_retro`, and this validates against it before anything
touches the vault.

    python3 /app/bridge/vault_tool.py get \
      'projects/sokrates/projects/agora/nova/resources/retro-ledger.json' \
      --rev-file /tmp/retro.$$.rev > ledger.json
    python3 -m tools.append_retro --ledger ledger.json --row row.json
    python3 /app/bridge/vault_tool.py put \
      'projects/sokrates/projects/agora/nova/resources/retro-ledger.json' \
      ledger.json --if-rev-file /tmp/retro.$$.rev

`--row` is a JSON object:

    {
      "date": "2026-08-14",
      "cycle": 181,
      "scores": {"going": 7, "effectiveness": 6, "feeling": 8},
      "overall": "One sentence on how it actually feels.",
      "good": "What is working.",
      "bad": "What is not.",
      "changes": ["What I am changing because of this retro."]
    }

Vault I/O is deliberately not in here, the same as `roll_digest.py`: the
ledger comes in as a path and goes out as the same path, so this runs
from either pod with whichever vault client that pod actually has. A
missing ledger file is not an error -- the first retro writes the first
one -- but a ledger that will not parse is, because "start over from
empty" would delete every previous retro on the way past.

`--print` writes the new ledger to stdout instead, for a dry run.
"""

import argparse
import json
import os
import re
import sys

from agora_runner.nova_retro import RetroError, append, load


# What `vault_tool.py get` writes to stdout for a path that does not
# exist. It exits 0 and prints this, so the documented flow
# (`get > ledger.json` then this tool) hands the *first* retro a file
# containing a sentence rather than an empty one -- measured 2026-08-14
# against the real client, on the real path, before the first retro ran.
# Without this the very first append would die on "the existing ledger
# will not parse", which is the one run that has no previous ledger to
# blame.
#
# It has to match the *whole* document, which is what the trailing `\s*$`
# is for -- `re.match` anchors the start for free, so the end is the only
# anchor doing work here. Without it, a real ledger that merely opened
# with that sentence would read as absent, and "absent" here means "start
# a new file", which replaces every retro ever written.
_ABSENT_RE = re.compile(r"\[not found: [^\]]*\]\s*$")


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return "" if _ABSENT_RE.match(text) else text


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Append one retro row to the retro ledger.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ledger", required=True, help="path to the ledger JSON")
    parser.add_argument("--row", required=True, help="path to the row JSON")
    parser.add_argument(
        "--print",
        dest="to_stdout",
        action="store_true",
        help="print the new ledger instead of rewriting --ledger",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.row, encoding="utf-8") as handle:
            row = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read the row: {exc}", file=sys.stderr)
        return 2

    document = _read(args.ledger)
    try:
        updated = append(document, row)
    except RetroError as exc:
        print(f"refusing to write: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        # Not RetroError: this is the existing ledger being unreadable,
        # not the new row being wrong, and the two want different fixes.
        print(f"refusing to write: the existing ledger will not parse: {exc}", file=sys.stderr)
        return 2

    if args.to_stdout:
        sys.stdout.write(updated)
        return 0

    with open(args.ledger, "w", encoding="utf-8") as handle:
        handle.write(updated)
    print(f"appended retro {row['date']} (cycle {row['cycle']}); "
          f"{len(load(updated))} retro(s) in the ledger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
