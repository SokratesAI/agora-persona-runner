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
at all. 1 when the roadmap could not be read or a board would not answer,
because a sweep that read one board of two must not report a clean
roadmap. 0 otherwise.

**The rows come out of the record store, not out of his markdown**
(issue #203, since 2026-09-10). `board_contents` below is the one door,
and the roadmap is the only document this still fetches from the vault --
a roadmap is his prose, not a board, and stays markdown after the
switchover.

The vault read is the tool's own by default; `--roadmap` takes a local
path instead, and `--issues`/`--ideas` still take a local board markdown
file, which is how the tests drive it and how a cycle can check an edit
before putting it.
"""

import argparse
import pathlib
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import board_records  # noqa: E402
from agora_runner.nova_plan import ROADMAP_PATH, next_items  # noqa: E402
from tools import board_migration_preflight  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: The two boards a `board:` field can name, in the singular form the field
#: uses -- which is also the name the record store keys a board by, so
#: `issue #131` reaches `board_records.contents("issue")` with no second
#: spelling in between. The plural is only the key this tool's own index
#: dict and its `--issues`/`--ideas` flags use.
BOARDS = {"issue": "issues", "idea": "ideas"}

#: `issue #131` / `idea #179`, the exact shape the roadmap writes. The
#: number is required -- a bare `issue` names no row and is not a
#: reference this can check.
_REF_RE = re.compile(r"\b(issue|idea)\s*#(\d+)", re.I)

#: A row in one of these is finished; the roadmap should no longer be
#: standing on it. These are `nova_boards`' own `statusKey` values, which
#: the record store carries verbatim, so a row spelled `✅ Done` and one
#: spelled `done` are the same row. `nova_boards.BLOCKED_STATUS` is
#: deliberately NOT here: a blocked row is open work waiting on the owner,
#: which is exactly the thing a roadmap item should keep pointing at.
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


def board_contents(board, local=None, store=None):
    """One board as the four keys the board parser used to return.

    Issue #203's switchover, for this tool. The default read is
    `board_records.contents`, so a roadmap is judged against the record
    store rather than against a 700KB markdown table parsed with a regex.
    `board` is the singular name in `BOARDS`, which is also the name the
    store keys a board by.

    `local` is the `--issues`/`--ideas` escape hatch and is unchanged in
    what it takes: a path to a board markdown file. It goes through
    `board_migration_preflight.board_contents`, the one module the
    migration is allowed to read markdown in, rather than through
    `parse_board` here -- so this tool no longer names the parser and
    `board_reader_inventory` stops counting it. That door is a migration
    seam and comes out with the window.

    `store=None` rather than the real store as a default argument: a
    default binds its value at import, so `board_records.board_store`
    written there would be the object this module captured and a test
    replacing it would be replacing something nothing reads. Same reason
    as `tools.top_board_rows.board_contents`.

    **Every failure raises** and `main` files the board under
    `COULD NOT READ`, which exits 1. An unmigrated store read as a board
    with no rows would put a MISSING finding on every roadmap item that
    names it -- a confident wrong verdict on a board this tool never saw,
    which is the one answer worse than no answer here.
    """
    if local:
        with open(local, encoding="utf-8") as fh:
            return board_migration_preflight.board_contents(fh.read())
    return board_records.contents(board,
                                  store=store or board_records.board_store)


def board_index(contents):
    """One board's `board_contents` shape -> `{number: statusKey}`.

    Both tables are read, not just `## Board`: the records carry `## Done`
    rows marked closed exactly as `parse_board` merged them, and a roadmap
    standing on a row that has moved to the done table is the main case
    this tool exists for.

    `statusKey` is read straight rather than re-derived from `status`.
    That fallback was here and was dead -- a mutation of it survived,
    which is how I found that out -- and the store's contract is that it
    answers exactly what the parser returned, so a row with no `statusKey`
    is a broken record and should raise here rather than be guessed at.
    """
    return {item["number"]: item["statusKey"] for item in contents["items"]}


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
        missing = [(k, n) for k, n in refs
                   if n not in indexes[BOARDS[k]]]
        if missing:
            findings.append(("missing", item, missing))
            continue
        closed = [(k, n, indexes[BOARDS[k]][n]) for k, n in refs]
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
                 "whole rather than by section, so a row moved to `## Done` is "
                 "seen. NOT JUDGED whether the ranking or the reasoning is "
                 "right — that is the Monday reprioritise run's job and this "
                 "invents no verdict on it." % (len(items), ROADMAP_PATH))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--roadmap", help="local roadmap.md instead of the vault")
    parser.add_argument("--issues", help="local issues.md instead of the vault")
    parser.add_argument("--ideas", help="local ideas.md instead of the vault")
    args = parser.parse_args(argv)

    unreadable = []

    # The roadmap is his prose and stays markdown after the switchover --
    # it is not a board and `board_migrate` never migrates it.
    if args.roadmap:
        try:
            roadmap = open(args.roadmap, encoding="utf-8").read()
        except OSError as exc:
            roadmap = None
            unreadable.append(f"{args.roadmap} ({exc.__class__.__name__})")
    else:
        roadmap = read_vault(ROADMAP_PATH)
        if roadmap is None:
            unreadable.append(ROADMAP_PATH)

    indexes = {}
    for kind, board in BOARDS.items():
        try:
            indexes[board] = board_index(board_contents(kind, getattr(args, board)))
        except Exception as exc:  # noqa: BLE001 -- see `board_contents`
            # Named with the reason: an unmigrated store, a CouchDB that
            # will not answer and a missing local file are one exit code
            # and three different fixes.
            unreadable.append(f"{board} board records "
                              f"({exc.__class__.__name__}: {exc})")

    if unreadable:
        print("COULD NOT READ — %s. A roadmap judged against one board of two "
              "would report rows as missing that are simply unread, so nothing "
              "is judged." % ", ".join(sorted(unreadable)))
        return 1

    items = next_items(roadmap)
    findings = judge(items, indexes)
    print(render(items, findings))
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
