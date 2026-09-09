"""Check the roadmap's ranked strip against the boards it points at.

`projects/sokrates/projects/nova/roadmap.md` is the prioritised half of
idea #4 -- what a cycle would do next, in order, with the reasoning
beside it. Each of its ```next fences names the board rows it stands on:
`board: issue #131, issue #130, issue #41, idea #179`.

**Those rows move and the roadmap does not.** It is rewritten when the
reasoning changes, which in practice is the Monday reprioritise run, so
between Mondays a roadmap item can be entirely finished -- every row it
names closed -- and still sit at rank 1 on the `/plan` page the owner
opens on his phone. Cycle 668 built the computed *What happens next*
card above this strip precisely because nothing hand-written stays true;
this is the other half of that, and it does not rewrite his prose. It
says which paragraph has stopped matching the boards, and leaves the
rewrite to a cycle that can also fix the reasoning.

**It compares two things and nothing else**: a roadmap item's own
`status:` against the statuses of the rows in its `board:` line, and
whether those rows exist at all. It does not judge whether the ranking
is right, whether the claim prose is still true, or whether an item is
worth doing -- those are judgements, and a tool that invented a verdict
on them would be a number I chose standing in for the owner's.

**Exit contract.** 2 when an item has drifted -- every named row is
closed while the item is still open, or a named row is not on its board
at all. 1 when a source could not be read -- the roadmap document or
either board's records -- because a sweep that read one board of two must
not report a clean roadmap. 0 otherwise.

**The boards come out of the record store, not out of markdown** (issue
#203). The roadmap is still a vault document read as text -- it is the
owner's prose, not a board -- so `--roadmap` still takes a local path,
which is how a cycle checks an edit before putting it. The two board
flags this tool used to carry are gone with the parse: there is no board
markdown left to point at, and a flag that let one board come from a
file while the other came from the store would be exactly the two live
sources of truth the switchover exists to remove.
"""

import argparse
import pathlib
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_document, board_records, board_store  # noqa: E402
from agora_runner.nova_plan import ROADMAP_PATH, next_items  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: The boards a `board:` field can name. There is no mapping left to keep:
#: the roadmap writes `issue #131` and `issue` is also the store's own
#: board name, so this is `board_document.BOARDS` itself rather than a
#: second copy of it. A third board would have to be taught to `_REF_RE`
#: as well, and a test holds those two together so it cannot be half done.
BOARDS = board_document.BOARDS

#: `issue #131` / `idea #179`, the exact shape the roadmap writes. The
#: number is required -- a bare `issue` names no row and is not a
#: reference this can check.
_REF_RE = re.compile(r"\b(issue|idea)\s*#(\d+)", re.I)

#: A row in one of these is finished; the roadmap should no longer be
#: standing on it. This is `nova_boards`' own vocabulary, read through
#: `status_key`, so a row spelled `✅ Done` and one spelled `done` are the
#: same row. `nova_boards.BLOCKED_STATUS` is deliberately NOT here: a
#: blocked row is open work waiting on the owner, which is exactly the
#: thing a roadmap item should keep pointing at.
CLOSED_KEYS = frozenset({"done", "outdated"})


def read_vault(path):
    """A vault document as text, or `None` if it could not be read.

    `None` covers three different failures on purpose -- no vault client
    on this pod, a non-zero exit, and the `[not found: ...]` line the
    client prints on stdout with exit 0 -- because all three mean the
    same thing to the caller: this sweep did not see the file, and must
    not report on it.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def board_index(board, store=board_store):
    """One board's records -> `{number: statusKey}`.

    There are no longer two tables to remember to read: a row the owner
    finished is one record carrying `statusKey` `done`, wherever the old
    markdown would have drawn it, and a roadmap still standing on it is
    the main case this tool exists for.

    `statusKey` is taken as given rather than re-derived. `from_document`
    sets it on every row; the second normalisation this function used to
    do on the markdown side was dead code, and a mutation of it survived,
    which is how that was found out.

    `store` is injected the same way `board_records.contents` injects it,
    so a test drives this without a CouchDB.
    """
    records = board_records.contents(board, store=store)
    return {item["number"]: item["statusKey"] for item in records["items"]}


def references(field):
    """A `board:` field -> `[(kind, number)]`, in the order written.

    Anything that is not `issue #n` or `idea #n` is dropped rather than
    guessed at. The field is prose the owner and I both edit, so it also
    carries commas, "and", and the occasional bare number; a bare number
    names no board and there are two.
    """
    return [(m.group(1).lower(), int(m.group(2)))
            for m in _REF_RE.finditer(field or "")]


def judge(items, indexes):
    """Roadmap items + `{board: {number: statusKey}}` -> findings.

    A finding is `(kind, item, detail)` with `kind` one of `"finished"`
    (the item is open and every row it names is closed) or `"missing"`
    (it names a row that is not on that board).

    An item with no references at all is not a finding. The field is
    optional -- rank 5 of the first roadmap carried none -- and reading
    "names nothing" as "everything it names is closed" would raise on
    every item that simply did not fill it in.
    """
    findings = []
    for item in items:
        if item["finished"]:
            continue
        refs = references(item.get("board"))
        if not refs:
            continue
        missing = [(k, n) for k, n in refs if n not in indexes[k]]
        if missing:
            findings.append(("missing", item, missing))
            continue
        closed = [(k, n, indexes[k][n]) for k, n in refs]
        if all(status in CLOSED_KEYS for _, _, status in closed):
            findings.append(("finished", item, closed))
    return findings


def render(items, findings):
    lines = []
    if findings:
        lines.append("ROADMAP DRIFTED — %d of %d ranked item(s) no longer "
                     "match the boards they name." % (len(findings), len(items)))
    for kind, item, detail in findings:
        head = "rank %s: %s" % (item.get("rank") or "?", item["title"])
        if kind == "finished":
            lines.append("  FINISHED  %s" % head)
            lines.append("      the roadmap says %s; every row it names is closed:"
                         % (item["statusLabel"] or item.get("status") or "open"))
            for k, n, status in detail:
                lines.append("        %s #%d — %s" % (k, n, status))
        else:
            lines.append("  MISSING   %s" % head)
            lines.append("      names %d row(s) that are not on that board:"
                         % len(detail))
            for k, n in detail:
                lines.append("        %s #%d" % (k, n))
    if not findings:
        lines.append("Nothing to act on. %d ranked item(s) read; every open one "
                     "still stands on at least one open board row." % len(items))
    lines.append("")
    lines.append("Judged %d ```next block(s) in %s against both boards, read "
                 "out of the record store, so a row the owner finished is "
                 "seen wherever it sits. NOT JUDGED whether the ranking or the "
                 "reasoning is right — that is the Monday reprioritise run's "
                 "job and this "
                 "invents no verdict on it." % (len(items), ROADMAP_PATH))
    return "\n".join(lines)


def main(argv=None, store=board_store):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--roadmap", help="local roadmap.md instead of the vault")
    args = parser.parse_args(argv)

    unreadable = []
    if args.roadmap:
        try:
            roadmap = open(args.roadmap, encoding="utf-8").read()
        except OSError:
            roadmap, unreadable = None, [args.roadmap]
    else:
        roadmap = read_vault(ROADMAP_PATH)
        if roadmap is None:
            unreadable = [ROADMAP_PATH]

    indexes = {}
    for board in BOARDS:
        try:
            indexes[board] = board_index(board, store=store)
        except (board_store.StoreError, board_document.DocumentError,
                board_records.RecordError, OSError) as exc:
            # Every one of these means the same thing the unreadable vault
            # file used to mean: this sweep did not see that board. A
            # `RecordError` in particular is a document that contradicts
            # its own board, and judging the roadmap against the rows that
            # did parse would report the rest as missing.
            unreadable.append("the %s records (%s)" % (board, exc))
    if unreadable:
        print("COULD NOT READ — %s. A roadmap judged against one board of two "
              "would report rows as missing that are simply unread, so nothing "
              "is judged." % ", ".join(unreadable))
        return 1

    items = next_items(roadmap)
    findings = judge(items, indexes)
    print(render(items, findings))
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
