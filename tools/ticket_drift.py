"""Is the CouchDB ticket store still the same tickets as the markdown?

Slice 2 (`agora_runner.ticket_docs`, runner#669) put all 441 tickets into
`nova_tickets`, one document each, and nothing has updated them since.
The markdown is still the source of truth and every writer in this loop
writes markdown -- the site's own board routes, `tools.board_capture`,
`tools.board_row`, and a cycle running `vault_tool.py put` by hand. So
the store started out identical and drifts from the first write onward.

**It had already drifted when I looked, one day in.** Cycle 826 posted a
reply under a capture on the owner's `ideas.md` after the migration ran;
that reply is in the markdown and not in `nova_tickets`, 896 bytes of
difference. That is the whole reason this exists: slice 3 is switching a
reader onto this store, and a reader switched onto a store nothing keeps
current serves the owner a board that is quietly a day old.

So this is the instrument that says whether the store can be trusted,
and `--sync` is the one command that makes it true again.

**The comparison is the render, not the document count.** A bulk write
answers `ok` per document, so a dropped field is 445 successes -- the
same trap `ticket_migrate` documents. The only check that cannot pass
while a ticket is damaged or missing is rendering the board out of
CouchDB and diffing it against the markdown, which is what this does.

**Both sides go through `ticket_store`.** The markdown side is
`to_markdown(to_records(source))` rather than the raw file, because
slice 1 already measured one board where those two differ on empty-cell
padding (`| |` against `|  |`) and that difference is not drift -- it is
the renderer, it was there on the day of the migration, and reporting it
every morning would train me to ignore this check. Comparing renders on
both sides subtracts it exactly.

**`--sync` stamps the revision it read, and until 2026-09-03 it did
not.** `ticket_docs.currency` answers "is the store current" without
fetching the markdown, which is the one thing standing between the board
page and dropping a 537KB fetch per build. That answer needs a
source-revision document, `to_documents` omits it when the writer does
not know the revision, and `write_board` tombstones anything it did not
produce -- so every repair run *deleted* the stamp the site's own writer
and `tools.board_put` had put there. Measured live before the fix: both
`ideas.md` boards said `current` and both `issues.md` boards said
`unknown`, and the difference was which of them had last been repaired.

**A board that matches gets stamped, and that is a write in a checking
tool.** `--sync` only ever runs on a board that has drifted, so a board
that never drifts had no way to record which revision it was built from
-- measured 2026-09-03, `issues.md` matched on all 170 rows, carried no
stamp, and `ticket_docs.currency` therefore answered `unknown` forever.
The comparison above is the strongest evidence available that the store
reproduces a given revision of the file, so writing it down here is the
one place it can be done from. See `stamp` below.

Exit codes are the sweep's: 2 = a board has drifted, 1 = a board could
not be read from the vault or from CouchDB, or its stamp could not be
written, 0 = every board matches.
"""

import argparse
import subprocess
import sys

# See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import ticket_docs, ticket_store  # noqa: E402
from tools import board_put  # noqa: E402
from tools.ticket_migrate import BOARDS  # noqa: E402


def read_vault(path):
    """`(markdown, revision)` for the vault document at `path`.

    `(None, None)` if it could not be read. The revision comes back
    alongside the document rather than from a second call: `vault_tool.py`
    has no `rev` subcommand, so `--rev-file` on this `get` is the only way
    to learn one, and the read costs the same either way.

    `tools.board_put.vault_get` already does exactly this, including
    taking the `print` newline back off the end of the document, so it is
    imported rather than written a second time -- two copies of one vault
    read is the duplication this repo keeps filing against itself.

    **The `except` is not decoration and it is not inherited.** The reader
    this replaced caught `OSError` and `subprocess.SubprocessError` itself
    and returned nothing, so a vault client that was missing or timed out
    came out as `CANNOT READ` and exit 1. `vault_get` does not catch
    those, so without this the morning sweep would raise a traceback out
    of `preflight` instead of reporting a board it could not read -- and
    an unreadable board must never read as a clean one.
    """
    try:
        return board_put.vault_get(path)
    except (OSError, subprocess.SubprocessError):
        return None, None


def first_difference(left, right):
    """`(line number, left line, right line)` for the first line that differs.

    One line rather than a whole diff: the report says *that* a board is
    stale and points at where, and `--sync` is what fixes it. A full diff
    of a 648KB board in the morning sweep would bury the verdict.
    """
    before, after = left.split("\n"), right.split("\n")
    for index in range(max(len(before), len(after))):
        old = before[index] if index < len(before) else None
        new = after[index] if index < len(after) else None
        if old != new:
            return index + 1, old, new
    return None


def compare(path, source):
    """`(status, detail)` for one board. `status` is this tool's exit code."""
    live = ticket_store.to_markdown(ticket_store.to_records(source))
    try:
        stored = ticket_docs.render_from_couch(path)
    except Exception as exc:
        return 1, f"UNREADABLE  {path} — {str(exc)[:200]}"
    if stored == live:
        return 0, f"CURRENT     {path} — {len(live):,} B, matches the markdown"
    spot = first_difference(stored, live)
    where = ""
    if spot:
        index, old, new = spot
        where = (f"\n  first differing line {index}"
                 f"\n    stored   {(old or '')[:200]!r}"
                 f"\n    markdown {(new or '')[:200]!r}")
    return 2, (f"DRIFTED     {path} — stored {len(stored):,} B, "
               f"markdown {len(live):,} B{where}")


def sync(path, source, source_rev=None):
    """Rewrite one board's documents from the markdown. `(status, detail)`.

    **`source_rev` is the revision `source` was read at, and passing it is
    the whole point of this argument existing.** `to_documents` omits the
    source-revision document when it is not given and `write_board`
    tombstones anything it did not produce, so a sync without it *deletes*
    the stamp the writers put there -- and `ticket_docs.currency` then
    answers `unknown` forever after, which is the one verdict that stops a
    reader trusting the store. Measured live 2026-09-03: both `issues.md`
    boards carried no stamp while both `ideas.md` boards did, and this is
    why. The repair tool was un-doing the thing it repairs.

    Stamping the read-time revision is correct even if the file moves
    between the read and the write: the documents really were built from
    that revision, so a reader comparing it against the live one gets
    `stale`, which is the true answer. The stamp records provenance, not a
    promise.
    """
    records = ticket_store.to_records(source)
    ok, detail = ticket_docs.ensure_database()
    if not ok:
        return 1, f"CANNOT SYNC {path} — {ticket_docs.TICKET_DB}: {detail}"
    try:
        summary = ticket_docs.write_board(path, records, source_rev=source_rev)
    except Exception as exc:
        return 1, f"CANNOT SYNC {path} — {str(exc)[:200]}"
    if summary["failures"]:
        return 1, (f"CANNOT SYNC {path} — {len(summary['failures'])} document(s) "
                   f"rejected: {summary['failures'][:2]}")
    # Verify by reading it back, never by the write's own answer.
    return compare(path, source)


def stamp(path, source_rev):
    """Write down that this board's store matches the markdown at `source_rev`.

    `(ok, note)` -- `note` is appended to the board's line, or empty when
    there was nothing to say.

    **This is a write, in the tool whose job is to check.** It is here
    rather than in `--sync` because `--sync` only ever runs on a board that
    has *drifted*, and a board that never drifts is precisely the one whose
    currency nothing can prove: measured 2026-09-03, `issues.md` matched
    the markdown on all 170 rows and carried no stamp at all, so
    `ticket_docs.currency` answered `unknown` and the reader that is
    supposed to stop fetching 537KB had nothing to go on. The stamp is one
    document under 200 bytes and it records the comparison this function's
    caller just made -- nothing else in the store is touched.

    A failed stamp raises the sweep to 1 and says so. The board really is
    current, so this is not drift; what failed is the instrument that lets
    a reader find that out without the fetch, and an instrument that did
    not run must never read as one that came back clean.
    """
    if not source_rev:
        # The vault read answered with no revision. Nothing to stamp and
        # nothing wrong with the board -- say it rather than stamping a
        # falsehood or staying silent about why the verdict will not move.
        return True, "\n  not stamped — the vault read carried no revision"
    try:
        moved = ticket_docs.stamp_source_rev(path, source_rev)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        return False, f"\n  CANNOT STAMP — {str(exc)[:200]}"
    return True, f"\n  stamped {source_rev}" if moved else ""


def run(boards, do_sync):
    """Check (and optionally re-sync) every board. Returns the exit code."""
    worst = 0
    drifted = []
    for path in boards:
        source, source_rev = read_vault(path)
        if source is None:
            worst = max(worst, 1)
            print(f"CANNOT READ {path} — the vault did not answer")
            continue
        status, detail = compare(path, source)
        if status == 2 and do_sync:
            status, detail = sync(path, source, source_rev)
            detail = detail.replace("CURRENT    ", "RE-SYNCED  ", 1)
        if status == 0:
            stamped, note = stamp(path, source_rev)
            worst = max(worst, 0 if stamped else 1)
            if note:
                detail += note
        if status == 2:
            drifted.append(path)
        worst = max(worst, status)
        print(detail)
    verb = "re-synced where it had drifted" if do_sync else "compared"
    print(f"{verb.capitalize()} {len(boards)} board(s) against "
          f"{ticket_docs.TICKET_DB}, by rendering each one out of CouchDB and "
          f"diffing it against the owner's markdown.")
    if drifted:
        print(f"{len(drifted)} board(s) are stale. `python3 -m tools.ticket_drift "
              f"--sync` rewrites them from the markdown, which is still the "
              f"source of truth.")
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--board", action="append",
                        help="one board path; repeatable. Default: all four.")
    parser.add_argument("--sync", action="store_true",
                        help="rewrite the documents of any board that has drifted")
    args = parser.parse_args(argv)
    return run(args.board or list(BOARDS), args.sync)


if __name__ == "__main__":
    sys.exit(main())
