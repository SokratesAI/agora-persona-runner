"""Retire finished items out of `journal-digest.md`'s **Next cycle** block.

The whole write-up lives in `agora_runner/nova_handoff.py` beside the
transform. This file is the CLI, and it is in `tools/` rather than in
`agora_runner/` because nothing on the site needs to run it, unlike the
block above it: `## Next cycle` is Nova's own handoff and no
page renders it.

    python3 -m tools.roll_handoff --live live.md --archive archive.md
    python3 -m tools.roll_handoff --live live.md --archive archive.md \
        --retire some-slug --reason 'Why it is finished.'
"""

import argparse
import datetime
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.config import OSLO
from agora_runner.nova_handoff import (  # noqa: F401 -- re-exported for callers
    ARCHIVE_FRONTMATTER,
    ARCHIVE_TITLE,
    MARKER,
    SPEC,
    archive_retired,
    item_slug,
    live_items,
    newest_cycle,
    select_slugs,
    split_items,
    stamp_retired,
)
from agora_runner import rolling  # noqa: F401 -- callers reach through this module
from agora_runner.rolling import RollError, plan, verify  # noqa: F401


def _describe(items):
    if not items:
        return ["**Next cycle** is empty -- there is no handoff."]
    newest = max(
        [c for c in (newest_cycle(i) for i in items) if c is not None] or [0]
    )
    lines = []
    for item in items:
        slug = item_slug(item)
        cycle = newest_cycle(item)
        if cycle is None:
            age = "no cycle cited"
        elif newest:
            age = f"cycle {cycle}, {newest - cycle} behind"
        else:
            age = f"cycle {cycle}"
        name = f"[{slug}]" if slug else "(no slug -- cannot be retired)"
        first = " ".join(item.split())
        lines.append(f"  {len(item):>6}B  {age:<22}  {name}\n           {first[:110]}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", default="live.md")
    parser.add_argument("--archive", default="archive.md")
    parser.add_argument(
        "--retire",
        action="append",
        default=[],
        metavar="SLUG",
        help="the [slug] of a finished item; repeatable",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="why those items are finished, recorded beside them in the archive",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    # Oslo, not the system clock -- same reason `roll_needs_edvard` gives:
    # the pod runs UTC and between 22:00 and 23:59 UTC the `**Retired
    # MM-DD**` stamp would be a day behind his calendar, every night.
    today = datetime.datetime.now(OSLO).date()
    live = open(args.live).read()
    try:
        archive = open(args.archive).read()
    except FileNotFoundError:
        archive = ""

    items = live_items(live)

    if not args.retire:
        total = sum(len(i) for i in items)
        print(f"{len(items)} item(s) in **Next cycle**, {total} bytes:")
        for line in _describe(items):
            print(line)
        print("nothing archived: pass --retire '<slug>' --reason '<why>'")
        return 0

    new_live, new_archive, moved = archive_retired(
        live, archive, args.retire, args.reason, today
    )

    print(
        f"verified: {len(moved)} item(s) retired, "
        f"{len(items) - len(moved)} still in the handoff"
    )
    for item in moved:
        print(f"  retired: {' '.join(item.split())[:120]}")
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
