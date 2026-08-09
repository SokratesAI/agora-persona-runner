"""One-shot migration: `journal.md` -> one vault document per entry.

Edvard, 2026-08-09: *"I think its quite urgent that you stop writing
your journals to a huge huge file in Vault and start putting them into a
database! You spent a huge amount of time and tokens reading and
searching through that huge file. Prioritised this!"*

The vault already is a database -- CouchDB, under Obsidian LiveSync --
and the problem was never the store, it was that all 70 entries lived in
one 291KB document, so "read my own memory" meant fetching every entry
ever written to look at the newest three.

This is committed rather than run as a heredoc because a migration that
only ever existed inside one cycle's session is a claim, not a
migration: the next cycle cannot check what it did, and cannot re-run it
if the split has to be redone. Idempotent -- rerunning overwrites each
entry document with the same bytes.

    python3 -m tools.split_journal --dry-run   # verify only, write nothing
    python3 -m tools.split_journal             # verify, then write

Verification runs either way and a failure aborts before any write. The
check that matters is not that the files look right; it is that
`parse_journal` -- the thing that actually renders the site -- produces
an identical entry list from the split as it does from the original.
"""

import sys

from agora_runner.nova_journal import (
    JOURNAL_DIR,
    JOURNAL_PATH,
    assemble_entries,
    entry_filename,
    parse_journal,
    split_entries,
)
from agora_runner.vault import vault_read_path, vault_write_path


def plan(markdown):
    """`journal.md` -> {vault path: entry text}, oldest entry seq 1.

    Entries come out of the file newest first, so the sequence is
    assigned from the back: seq 1 is the oldest entry and never changes
    when a new one is written on top.
    """
    entries = split_entries(markdown)
    total = len(entries)
    out = {}
    for index, entry in enumerate(entries):
        seq = total - index
        out[JOURNAL_DIR + entry_filename(seq, entry["heading"])] = entry["text"]
    return out


def verify(markdown, files):
    """Raise unless the split renders identically to the original.

    Three separate things can go wrong and each gets its own check: an
    entry can be dropped, two entries can collide onto one filename
    (which loses one silently, since the second write wins), and the
    reassembled text can parse differently from the original.
    """
    original = parse_journal(markdown)
    if len(files) != len(original):
        raise SystemExit(
            f"refusing to write: {len(original)} entries parsed but "
            f"{len(files)} files planned -- two entries share a filename"
        )
    rebuilt = parse_journal(assemble_entries(files))
    if rebuilt != original:
        for before, after in zip(original, rebuilt):
            if before != after:
                raise SystemExit(
                    "refusing to write: entry renders differently after the "
                    f"split -- {before.get('heading')!r}"
                )
        raise SystemExit(
            f"refusing to write: {len(original)} entries in, {len(rebuilt)} out"
        )
    return len(original)


def main(argv):
    dry_run = "--dry-run" in argv
    markdown = vault_read_path(JOURNAL_PATH)
    if not markdown:
        raise SystemExit(f"could not read {JOURNAL_PATH}")

    files = plan(markdown)
    count = verify(markdown, files)
    print(f"{count} entries verified; {len(markdown)} bytes -> {len(files)} documents")
    print(f"largest entry: {max(len(t) for t in files.values())} bytes")

    if dry_run:
        print("--dry-run: nothing written")
        return 0

    for path in sorted(files):
        vault_write_path(path, files[path] + "\n")
        print(f"wrote {path} ({len(files[path])} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
