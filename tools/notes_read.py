"""Print every live note in the notes store, newest first, with its comments.

The reader half for cycles (idea #333). Since the capture box and the /notes
page write note records instead of `notes.md`, a cycle that only reads
`notes.md` in step 1a would never see a note he typed after the switch. This
prints what the /notes page shows him, as text: sender, Oslo time, the note,
then each comment under it. Archived notes are his to put away and are left
out; `--archived` prints those instead.

    python3 -m tools.notes_read
    python3 -m tools.notes_read --archived

Exit 1 when the store could not be read, which never reads as "no notes".
"""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import nova_notes_store as store

OSLO = ZoneInfo("Europe/Oslo")


def oslo(stamp):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(OSLO).strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError):
        return stamp or "?"


def render(notes, comments_of):
    lines = []
    for doc in notes:
        lines.append(f"== {doc['_id']}  {doc.get('author')}  {oslo(doc.get('created'))}")
        lines.append(doc.get("text", ""))
        for c in comments_of(doc["_id"]):
            lines.append(f"  -- {c.get('author')} {oslo(c.get('created'))}: {c.get('text', '')}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m tools.notes_read", description=__doc__.splitlines()[0])
    parser.add_argument("--archived", action="store_true", help="print the archived notes instead")
    args = parser.parse_args(argv)
    try:
        notes = store.list_notes(archived=args.archived)
        text = render(notes, store.read_comments)
    except store.StoreError as e:
        print(f"COULD NOT READ the notes store: {e}", file=sys.stderr)
        return 1
    print(text if notes else ("no archived notes" if args.archived else "no live notes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
