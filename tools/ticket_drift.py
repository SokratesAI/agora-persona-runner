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

Exit codes are the sweep's: 2 = a board has drifted, 1 = a board could
not be read from the vault or from CouchDB, 0 = every board matches.
"""

import argparse
import subprocess
import sys

# See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import ticket_docs, ticket_store  # noqa: E402
from tools import ticket_migrate  # noqa: E402
from tools.ticket_migrate import BOARDS, VAULT_TOOL  # noqa: E402


def read_vault(path):
    """The vault document at `path`, or `None` if it could not be read."""
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "get", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or done.stdout.strip() == "[not found]":
        return None
    # See `ticket_migrate.strip_the_print_newline`: the subprocess prints the
    # document, so its stdout is the document plus the newline `print` added.
    return ticket_migrate.strip_the_print_newline(done.stdout)


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


def sync(path, source):
    """Rewrite one board's documents from the markdown. `(status, detail)`."""
    records = ticket_store.to_records(source)
    ok, detail = ticket_docs.ensure_database()
    if not ok:
        return 1, f"CANNOT SYNC {path} — {ticket_docs.TICKET_DB}: {detail}"
    try:
        summary = ticket_docs.write_board(path, records)
    except Exception as exc:
        return 1, f"CANNOT SYNC {path} — {str(exc)[:200]}"
    if summary["failures"]:
        return 1, (f"CANNOT SYNC {path} — {len(summary['failures'])} document(s) "
                   f"rejected: {summary['failures'][:2]}")
    # Verify by reading it back, never by the write's own answer.
    return compare(path, source)


def run(boards, do_sync):
    """Check (and optionally re-sync) every board. Returns the exit code."""
    worst = 0
    drifted = []
    for path in boards:
        source = read_vault(path)
        if source is None:
            worst = max(worst, 1)
            print(f"CANNOT READ {path} — the vault did not answer")
            continue
        status, detail = compare(path, source)
        if status == 2 and do_sync:
            status, detail = sync(path, source)
            detail = detail.replace("CURRENT    ", "RE-SYNCED  ", 1)
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
