"""Do the three merged migration primitives actually compose on a real board?

    python3 -m tools.board_migration_preflight --board issues.md --board ideas.md
    python3 -m tools.board_migration_preflight --board issues.md --assert-clean
    python3 -m tools.board_migration_preflight --board issues.md --round-trip issue

Issue #203 turns each board row into a record, and three pieces of that
change are already merged and wired to nothing: `rank_key` (the ordering
key), `entity_id` (the project and milestone ids) and `board_view` (the
generated markdown the backup keeps). Each has its own unit tests and none
of them has ever been run against the other two, so what nobody has
measured is the only thing the migration commit actually does -- take the
rows that exist and hand every one of them an id and a rank.

This runs that composition over real board markdown and reports the
numbers the migration will have to live with. `--assert-clean` is there so
the cycle that takes the commit can find out in one command whether the
ground moved under the primitives since they merged.

## `--round-trip` is the one part of this that writes

Everything above composes records in memory, and a store that dropped a
cell or reordered a row would compose exactly the same way -- the mistake
only appears on the way back. So `--round-trip BOARD` sends the composed
records through the real `board_store`, reads them back, rebuilds the rows
and compares the markdown `board_view` renders from them against the
markdown they were parsed from.

It is still not a migration and must not become one. It restores the store
to empty afterwards, which is only a restore when the store *was* empty, so
a board that already holds records raises `RoundTripRefused` and nothing is
written -- against a migrated board this run would tombstone real rows and
could not put them back. That refusal exits 2 whether or not
`--assert-clean` was passed, because a control that never ran must never
read as one that came back clean.

Measured 2026-09-09, before the switchover: 198 issue rows and 274 idea
rows both round trip with the rendered markdown identical and the store
back at zero documents.

What counts as a problem, and why each one would be a bad surprise
*during* the one big commit rather than before it:

- **Two distinct project names sharing one id.** That is two projects
  silently becoming one. It cannot come from case or spacing, because
  merging those is the entire point of `entity_id.normalise` and is
  correct -- it would have to come from slug truncation or from a
  clash-suffix bug, so it is worth a check that names the pair.
- **A rank key that is invalid, out of order, or has an uninsertable
  gap.** `sequence` and `between` are separately tested; what is not
  tested is `between` over the *whole* sequence at the size of a real
  board, which is where a midpoint runs out of room between two adjacent
  keys.

**There is deliberately no "row with no project" check, and the reason is
worth carrying into the migration.** `nova_boards.parse_board` reads an
absent or empty `Project` cell as `DEFAULT_PROJECT`, which is `Nova` --
documented at the cell, and correct for a board where every row predates
the column. So a project-less row never reaches this module, a count of
them would be zero on any input and would report a guard working while
guarding nothing. What it means downstream is the part that matters: the
migration mints `prj_nova` for a row whose cell is blank, and that id is
permanent, so "nobody has re-filed this yet" becomes "filed under Nova"
the moment the records land. That is a decision for the commit, not a
defect for a preflight to fail on.

What it does not check: whether the generated markdown round-trips. That
already has its own coverage in `board_view`, against these same files.
"""
import argparse
import difflib
import re
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import (board_document, board_store, board_view,  # noqa: E402
                          entity_id, nova_boards, rank_key)


def board_items(markdown):
    """The row dicts in a board, whatever shape `parse_board` hands back."""
    parsed = nova_boards.parse_board(markdown)
    if isinstance(parsed, list):
        return parsed
    for key in ("items", "rows"):
        rows = parsed.get(key)
        if isinstance(rows, list):
            return rows
    return []


def board_details(markdown):
    """A board's `{number: prose body}` map, `{}` on any older parse shape."""
    parsed = nova_boards.parse_board(markdown)
    details = parsed.get("details") if isinstance(parsed, dict) else None
    return details if isinstance(details, dict) else {}


def frontmatter_of(markdown):
    """The document's own frontmatter block, or `""` when it has none.

    Taken off the document rather than rebuilt, because it is the owner's:
    both board files carry a `contract:` line there explaining themselves,
    and nothing in the records holds it.
    """
    text = markdown or ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return "" if end < 0 else text[:end + 4].rstrip()


def document_round_trip(markdown):
    """`parse -> render_document -> parse` on one board file.

    The check `round_trip` above cannot make. That one compares the two
    *tables* through the real store, so a lost capture bullet, a dropped
    detail body or a mangled frontmatter all pass it -- the tables carry
    none of those. This one renders the whole document `board_view` now
    draws and reads it straight back, and it needs no store at all, so it
    runs on every `--board` rather than on the one board a round trip is
    allowed to touch.

    Byte-identity is deliberately not the assertion: `board_view`'s module
    comment says the live files are ragged and the first generated write is
    a one-time reflow. Meaning is the assertion, key by key, and the report
    names which key moved rather than a single boolean, because "the
    document does not round trip" and "detail #57 lost its body" are the
    same failure at two useful distances.

    **It renders through the source document's own layout**, which is what
    took `document_words_lost` on the two live boards from 19,653 and 6,469
    to 120 and 216 (measured 2026-09-09). Without it the sections
    `parse_board` does not model -- the owner's `## Processed captures`
    archive, `# Done — detail`, `ideas.md`'s `## Discarded` table -- are
    absent from the render, and the four-key comparison above cannot see
    that because it is written in the parser's own four words. What is left
    is the detail-heading reflow `board_view.render_detail` chose on
    purpose: 60 and 108 write-ups on those boards are still written in the
    older `## N —` shape and are emitted in the newer `### #N —` one, which
    is two tokens each and no prose.
    """
    was = nova_boards.parse_board(markdown)
    document = board_view.render_document(
        was, frontmatter_of(markdown),
        layout=board_view.document_layout(markdown))
    now = nova_boards.parse_board(document)
    problems = []
    for key in ("captures", "captureReplies", "items", "details"):
        if was.get(key) != now.get(key):
            problems.append(
                f"{key} did not survive the document round trip "
                f"({len(was.get(key) or ())} in, {len(now.get(key) or ())} back)")
    lost = words_lost(markdown, document)
    if lost:
        problems.append(
            f"{len(lost)} word(s) of the document did not survive as written: "
            + " / ".join(sample_lost(lost)))
    return {
        "document_round_trip": not problems,
        "document_bytes": len(document),
        "document_words_lost": len(lost),
    }, problems


#: A markdown table's rule row, as one whitespace-free token. The dashes
#: are padded to the widths of the header above, which the renderer redraws
#: at a fixed three, so the same rule is a different word before and after.
_RULE_RE = re.compile(r"^\|(?:-+\|)+$")


def _normalise(word):
    """One word of a document, with a table rule's padding taken out."""
    return "|---|" if _RULE_RE.match(word) else word


def words_lost(markdown, document):
    """Words in the source that the rendered document does not carry.

    **The four-key comparison above cannot see this and reported `True` on
    both live boards while deleting 26,122 words between them.** That is
    the positive result guaranteed in advance: `render_document` is built
    out of `parse_board`'s four keys, so anything the parser does not model
    is absent from both sides of every comparison made in its own terms.
    Measured 2026-09-09, the sections in that hole are the owner's
    `## Processed captures` archive on both boards, the `# Done — detail`
    heading, and `ideas.md`'s `## Discarded` table.

    So this compares the raw word streams instead, which is the one check
    that is not written in the renderer's own vocabulary. It is a
    *sequence* diff rather than a bag of words: a word deleted here and
    added there is still a document that changed, and a multiset
    comparison would call that clean.

    Not byte-identity, deliberately -- `board_view`'s module comment says
    the first generated write is a one-time reflow of ragged tables, and a
    reflow moves whitespace rather than prose. Exactly one token moves with
    it: a table's `|---|------|---|` rule is a single word whose dashes are
    padded to the column widths above it, so a reflowed board would report
    one lost word per table that it did not lose. `_RULE_RE` collapses that
    one shape and nothing else -- a token made only of pipes and dashes is
    a rule by definition, and a test pins that prose is still caught. The
    line to hold is that no *content* rule may be added here: a filter
    written in the renderer's own vocabulary is precisely how the four-key
    comparison above went blind.
    """
    was = [_normalise(word) for word in (markdown or "").split()]
    now = [_normalise(word) for word in (document or "").split()]
    matcher = difflib.SequenceMatcher(None, was, now, autojunk=False)
    lost = []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            lost.extend(was[i1:i2])
    return lost


def sample_lost(lost, limit=12):
    """The first few lost words, so the report names the section, not a count."""
    head = " ".join(lost[:limit])
    return [head + (" ..." if len(lost) > limit else "")]


def compose(items):
    """Mint an id and a rank for every row; report what would not compose.

    Returns `(report, problems)` -- counts for the log, and a list of
    human-readable strings that is empty when the migration has clean
    ground to stand on.
    """
    registry = entity_id.new_registry()
    problems = []

    projects = {}
    for item in items:
        name = item.get("project")
        if name and name not in projects:
            projects[name] = entity_id.ensure_project(registry, name)

    by_id = {}
    for name, pid in projects.items():
        by_id.setdefault(pid, []).append(name)
    for pid, names in sorted(by_id.items()):
        if len({entity_id.normalise(n) for n in names}) > 1:
            problems.append(f"project id {pid} is shared by {sorted(names)}")

    milestones = {}
    for item in items:
        name = item.get("project")
        stone = item.get("milestone")
        if not (name and stone):
            continue
        pair = (name, stone)
        if pair not in milestones:
            milestones[pair] = entity_id.ensure_milestone(
                registry, projects[name], stone
            )

    keys = rank_key.sequence(len(items))
    for key in keys:
        if not rank_key.is_valid(key):
            problems.append(f"rank key {key!r} is not valid")
            break
    if sorted(keys) != list(keys) or len(set(keys)) != len(keys):
        problems.append("rank keys are not strictly increasing")
    else:
        for before, after in zip(keys, keys[1:]):
            try:
                middle = rank_key.between(before, after)
            except rank_key.RankError as exc:
                problems.append(f"no key fits between {before!r} and {after!r}: {exc}")
                break
            if not before < middle < after:
                problems.append(
                    f"between({before!r}, {after!r}) gave {middle!r}, out of order"
                )
                break

    report = {
        "rows": len(items),
        # Distinct *ids*, not distinct names: merging `Nova` and `nova`
        # into one project is the whole point of `entity_id`, so counting
        # the names would report the migration creating projects it does
        # not create.
        "projects": len(set(projects.values())),
        "milestones": len(set(milestones.values())),
        "rank_keys": len(keys),
    }
    return report, problems


def _lines(rendered):
    """`board_view.render_tables` output as a flat list of lines.

    It hands back a mapping of table name -> markdown, not one string, so a
    comparison that wants to name the first differing line has to flatten it
    in a fixed order.
    """
    if isinstance(rendered, str):
        return rendered.splitlines()
    lines = []
    for name in sorted(rendered):
        lines.append(f"# {name}")
        value = rendered[name]
        lines.extend(value.splitlines() if isinstance(value, str) else list(value))
    return lines


class RoundTripRefused(RuntimeError):
    """The store already holds records, so the run has nothing safe to restore to."""


def records(items, board, registry, details=None):
    """Mint ids and ranks for `items` and return their record documents.

    Returns `(docs, project_names, milestone_names)` -- the two maps are
    id -> name, which is what `board_document.from_document` needs to put
    the names back into a row it reconstructs. `compose` above counts what
    would happen; this builds it.

    `details` is `parse_board`'s `{number: prose body}` map. It is a
    separate argument because a body is not a key on the row it belongs to,
    and it is threaded here rather than left out because a document written
    without one loses the body with no symptom -- `render_tables` draws the
    two tables only, so a round trip that dropped every `# Details` section
    would still report `render_identical`.
    """
    details = details or {}
    projects = {}
    milestones = {}
    for item in items:
        name = item.get("project")
        if name and name not in projects:
            projects[name] = entity_id.ensure_project(registry, name)
        stone = item.get("milestone")
        if name and stone:
            pair = (projects[name], stone)
            if pair not in milestones:
                milestones[pair] = entity_id.ensure_milestone(
                    registry, projects[name], stone)

    keys = rank_key.sequence(len(items))
    docs = []
    for item, key in zip(items, keys):
        name = item.get("project")
        pid = projects.get(name)
        stone = item.get("milestone")
        mid = milestones.get((pid, stone)) if (pid and stone) else None
        docs.append(board_document.to_document(
            item, board, project_id=pid, milestone_id=mid, rank=key,
            detail=details.get(item.get("number"))))
    return docs, {v: k for k, v in projects.items()}, \
        {mid: stone for (_pid, stone), mid in milestones.items()}


def round_trip(items, board, store=board_store, details=None):
    """Write `items` to the record store, read them back, render both ways.

    This is the control the in-memory checks above cannot take: everything
    else in this tool composes records in memory, so a store that dropped a
    field or reordered a row would look identical to one that did not. Here
    the documents make the trip through CouchDB and come back.

    It restores the store to empty afterwards, which is only a restore when
    the store *was* empty -- so a board that already holds records raises
    `RoundTripRefused` rather than tombstoning a real migration. Reversible
    first, then act.
    """
    before = store.stored_documents(board)
    if before:
        raise RoundTripRefused(
            f"{board} already holds {len(before)} record(s); this run would "
            "have to delete them and cannot put them back")

    registry = entity_id.new_registry()
    docs, project_names, milestone_names = records(
        items, board, registry, details=details)
    written = store.write_rows(board, docs)
    try:
        back = store.read_rows(board)
        rebuilt = [
            board_document.from_document(
                doc,
                project_name=project_names.get(doc.get("projectId")),
                milestone_name=milestone_names.get(doc.get("milestoneId")))
            for doc in back
        ]
        rendered = board_view.render_tables(rebuilt)
    finally:
        deleted = store.write_rows(board, [])

    from_markdown = board_view.render_tables(items)
    report = {
        "board": board,
        "rows": len(items),
        "written": written.get("written"),
        "read_back": len(back),
        "restored_to": len(store.stored_documents(board)),
        "deleted_on_restore": deleted.get("deleted"),
        "render_identical": rendered == from_markdown,
        # Counted separately because `render_identical` cannot see it: the
        # two tables carry no prose, so a lost body leaves that flag True.
        "details_in": len(details or {}),
        "details_back": len(board_document.details_map(back)),
    }
    problems = []
    if written.get("failures"):
        problems.append(f"{len(written['failures'])} row(s) failed to write")
    if len(back) != len(items):
        problems.append(f"wrote {len(items)} row(s), read back {len(back)}")
    if report["details_back"] != report["details_in"]:
        problems.append(
            f"{report['details_in']} detail body(ies) went in, "
            f"{report['details_back']} came back")
    if not report["render_identical"]:
        left = _lines(from_markdown)
        right = _lines(rendered)
        for index, (one, two) in enumerate(zip(left, right)):
            if one != two:
                problems.append(
                    f"line {index} differs: markdown {one!r} vs store {two!r}")
                break
        else:
            problems.append(
                f"rendered {len(left)} line(s) from markdown, "
                f"{len(right)} from the store")
    if report["restored_to"]:
        problems.append(
            f"{report['restored_to']} record(s) left behind after the restore")
    return report, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--board", action="append", default=[], metavar="FILE",
                        help="a board markdown file; repeatable")
    parser.add_argument("--assert-clean", action="store_true",
                        help="exit 2 when anything would not compose")
    parser.add_argument("--round-trip", metavar="BOARD", choices=sorted(board_document.BOARDS),
                        help="also send the rows through the real record store and "
                             "compare the markdown it renders back; needs exactly "
                             "one --board, and refuses a board that already holds records")
    args = parser.parse_args(argv)
    if not args.board:
        parser.error("give at least one --board FILE")
    if args.round_trip and len(args.board) != 1:
        parser.error("--round-trip takes exactly one --board FILE")

    items = []
    details = {}
    document_reports = []
    for path in args.board:
        with open(path, encoding="utf-8") as handle:
            markdown = handle.read()
        items.extend(board_items(markdown))
        details.update(board_details(markdown))
        document_reports.append((path, document_round_trip(markdown)))

    report, problems = compose(items)
    for name, value in report.items():
        print(f"{name}: {value}")
    for path, (trip, trip_problems) in document_reports:
        for name, value in trip.items():
            print(f"{path} {name}: {value}")
        problems.extend(f"{path}: {text}" for text in trip_problems)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if args.round_trip:
        try:
            trip, trip_problems = round_trip(
                items, args.round_trip, details=details)
        except (RoundTripRefused, board_store.StoreError) as exc:
            print(f"PROBLEM: round trip: {exc}")
            return 2
        for name, value in trip.items():
            print(f"round_trip.{name}: {value}")
        problems.extend(f"round trip: {text}" for text in trip_problems)
        for text in trip_problems:
            print(f"PROBLEM: round trip: {text}")
    if problems and args.assert_clean:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
