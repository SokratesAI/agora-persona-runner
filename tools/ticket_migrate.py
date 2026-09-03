"""Round-trip every board file through the ticket records, and report.

    python3 -m tools.ticket_migrate
    python3 -m tools.ticket_migrate --records /tmp/tickets.json

The first slice of the store migration the owner approved on 2026-09-02
(idea #5 / idea #231). It reads each board out of the vault, lifts every
ticket into a record with `agora_runner.ticket_store`, renders the file
back from those records, and compares the result byte for byte with what
it read.

**A pass here is the precondition for the rest of the migration and
nothing more.** It says a ticket survives the trip out of the markdown
and back; it says nothing about CouchDB, which the default run does not
touch.

`--write` is slice 2 and it does touch CouchDB: it writes one document
per ticket into the `nova_tickets` database (its own database, never the
vault's -- see `agora_runner.ticket_docs`), reads every one of them back,
renders the board file from what came back, and compares that with the
markdown it started from. **The markdown is still the source of truth and
nothing reads a board out of CouchDB yet**; what `--write` proves is that
it could. A read-back that does not reproduce the file exits 2 the same
way a failed round-trip does.

Exit contract, the same one the `preflight` checks share: **2 means a
file did not survive the round-trip**, 1 means a file could not be read
(which never reads as clean), 0 means every board swept came back
identical or differs only in table-cell padding -- an empty cell written
`| |` on one row and `|  |` on the other 397, which carries no character
of his text. Every differing line is printed, whichever way it goes.
"""

import argparse
import json
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import pathlib as _pathlib  # noqa: E402
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import ticket_docs, ticket_store  # noqa: E402


VAULT_TOOL = "/app/bridge/vault_tool.py"

# Defined in `agora_runner.ticket_docs`, next to the store itself, because
# the write-through in `agora_runner.vault` needs the same list and
# `agora_runner` may not import from `tools`. Re-exported here so the two
# tools that already import it from this module keep working.
BOARDS = ticket_docs.BOARDS


def strip_the_print_newline(stdout):
    """`vault_tool.py get` prints the document, so its stdout is one byte long.

    Line 1359 of the bridge's vault client is `print(content)`, and `print`
    appends a newline the vault does not hold. Every tool that reads a board
    through that subprocess therefore sees the document plus one `\n`.

    That was invisible while both sides of the ticket store came through it:
    `ticket_migrate` stored the one-byte-longer text and `ticket_drift`
    compared against the same one-byte-longer text, so they agreed. The
    write-through added in runner#672 reads the markdown in-process, where
    no `print` is involved, so it stores what the vault actually holds --
    and from the first board the app writes, this comparison reports one
    byte of drift that is not drift, every morning, forever.

    Removing exactly one trailing newline is lossless rather than a
    heuristic: `print` always adds exactly one, so this is its inverse and
    not a guess about how the owner's file ends. A document that genuinely
    ends in four blank lines still round-trips with four.
    """
    return stdout[:-1] if stdout.endswith("\n") else stdout


def _read(path):
    """The vault document at `path`, or `None`."""
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "get", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"CANNOT READ  {path} — {exc}")
        return None
    if done.returncode != 0 or done.stdout.strip() == "[not found]":
        print(f"CANNOT READ  {path} — {(done.stderr or done.stdout).strip()[:200]}")
        return None
    return strip_the_print_newline(done.stdout)


def _classify(source, rendered):
    """`(differing_lines, padding_only)` for a round-trip that is not identical."""
    before, after = source.split("\n"), rendered.split("\n")
    differing = [
        (index, before[index] if index < len(before) else "",
         after[index] if index < len(after) else "")
        for index in range(max(len(before), len(after)))
        if (before[index] if index < len(before) else None)
        != (after[index] if index < len(after) else None)
    ]
    # Padding only when nothing but run-length of spaces changed. Written
    # as a whitespace-collapse rather than a rule about empty cells, so a
    # difference this does not anticipate cannot pass as one it does.
    padding_only = all(
        " ".join(old.split()) == " ".join(new.split()) for _, old, new in differing)
    return differing, padding_only


def check(path, source):
    """Round-trip one board. Returns `(status, records)`."""
    records = ticket_store.to_records(source)
    rendered = ticket_store.to_markdown(records)
    owned, residue = ticket_store.coverage(source, records)
    share = 100.0 * owned / len(source) if source else 0.0
    tickets = len(records["tickets"])
    if rendered == source:
        print(f"IDENTICAL    {path} — {tickets} ticket(s), "
              f"{owned:,} B of {len(source):,} ({share:.0f}%) is tickets, "
              f"{residue:,} B residue")
        return 0, records
    differing, padding_only = _classify(source, rendered)
    verdict = "PADDING ONLY" if padding_only else "LOST CONTENT"
    print(f"{verdict} {path} — {tickets} ticket(s), {len(differing)} line(s) differ, "
          f"{owned:,} B of {len(source):,} ({share:.0f}%) is tickets, "
          f"{residue:,} B residue")
    for index, old, new in differing[:20]:
        print(f"  line {index}")
        print(f"    read     {old[:300]!r}")
        print(f"    rendered {new[:300]!r}")
    if len(differing) > 20:
        print(f"  ... and {len(differing) - 20} more")
    return (0 if padding_only else 2), records


def store(path, source, records):
    """Write one board into CouchDB and render it back out. Returns a status.

    The verification is deliberately end to end rather than a count of
    documents written: a bulk write that reports `ok` for 108 documents
    and drops a field inside one of them is a clean-looking write of a
    board that no longer renders. Comparing the rendered read-back with
    the markdown that produced it is the one check that cannot pass
    while a ticket is damaged.
    """
    ok, detail = ticket_docs.ensure_database()
    if not ok:
        print(f"CANNOT WRITE {path} — {ticket_docs.TICKET_DB}: {detail}")
        return 1
    try:
        summary = ticket_docs.write_board(path, records)
        rendered = ticket_docs.render_from_couch(path)
    except RuntimeError as exc:
        print(f"CANNOT WRITE {path} — {exc}")
        return 1
    if summary["failures"]:
        print(f"WRITE FAILED {path} — {len(summary['failures'])} document(s) refused: "
              f"{summary['failures'][:3]}")
        return 2
    if rendered == source:
        print(f"STORED       {path} — {summary['written']} document(s) written, "
              f"{summary['deleted']} tombstoned, and the file renders back "
              f"byte-identical from CouchDB")
        return 0
    differing, padding_only = _classify(source, rendered)
    verdict = "STORE PADDING" if padding_only else "STORE LOST CONTENT"
    print(f"{verdict} {path} — {summary['written']} document(s) written, "
          f"{len(differing)} line(s) differ on the read-back")
    for index, old, new in differing[:20]:
        print(f"  line {index}")
        print(f"    file  {old[:300]!r}")
        print(f"    couch {new[:300]!r}")
    if len(differing) > 20:
        print(f"  ... and {len(differing) - 20} more")
    return 0 if padding_only else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", help="write the records as JSON to this path")
    parser.add_argument("--board", action="append", help="a vault path; repeatable")
    parser.add_argument("--write", action="store_true",
                        help=f"also store each board in the {ticket_docs.TICKET_DB} "
                             "database, one document per ticket, and read it back")
    args = parser.parse_args(argv)

    boards = args.board or list(BOARDS)
    status = 0
    everything = {}
    for path in boards:
        source = _read(path)
        if source is None:
            status = max(status, 1)
            continue
        result, records = check(path, source)
        status = max(status, result)
        everything[path] = records
        if args.write:
            status = max(status, store(path, source, records))
    if args.records and everything:
        with open(args.records, "w") as handle:
            json.dump(everything, handle, ensure_ascii=False, indent=1)
        print(f"Wrote records for {len(everything)} board(s) to {args.records}")
    total = sum(len(r["tickets"]) for r in everything.values())
    stored = (f"Each is also stored in {ticket_docs.TICKET_DB} as one document per "
              f"ticket; the markdown is still the source of truth and nothing reads "
              f"a board from CouchDB yet."
              if args.write else
              "A round trip proves a ticket survives the markdown, not that anything "
              "has moved to a per-ticket store yet — pass --write for that.")
    print(f"Swept {len(everything)} of {len(boards)} board(s), {total} ticket(s). "
          + stored)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
