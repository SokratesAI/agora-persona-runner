"""`tools.board_migrate` -- the #203 step that actually moves the rows.

Everything else built for #203 either composes records in memory or writes
them and deletes them again. This is the first module whose whole job is to
leave records behind, so the cases that matter here are the ones about what
survives the run and what a second run is allowed to do.

The fixture is the same fake CouchDB `test_board_store.py` uses, for the
reason given there: a fake `board_store` would let a migration that never
reached a database pass every test in this file.
"""
import pytest

from agora_runner import (board_document, board_records, board_store,
                          board_view, entity_id, nova_boards, ticket_docs)
from tools import board_migrate, board_migration_preflight

from tests.test_board_store import FakeCouch

HEADER = (
    "## Board\n\n"
    "| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |\n"
    "|---|------|--------|---------|----------|---------|------|-----------|-------|\n"
)


def board(rows, details=(), captures=()):
    """Board markdown for `(number, project, milestone)` triples.

    `captures` are `(text, replies)` pairs, written above the first heading
    the way he writes them from his phone -- newest first, with whatever a
    cycle answered indented underneath.
    """
    lines = [
        f"| [[#{n} — Item {n}\\|{n}]] | Item {n} | ⚪ Backlog | 09-09 "
        f"| 🟡 Medium | {project} | | {milestone} | |"
        for n, project, milestone in rows
    ]
    top = ""
    for capture_text, replies in captures:
        top += f"- {capture_text}\n"
        for reply in replies:
            top += f"  - {reply}\n"
    text = (top + "\n" if top else "") + HEADER + "\n".join(lines) + "\n"
    if details:
        text += "\n# Details\n"
        for number, body in details:
            text += f"\n## #{number} — Item {number}\n\n{body}\n"
    return text


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


def test_a_dry_run_stores_nothing(couch):
    """The default has to be safe: the switchover commit runs this against the
    live boards, and a dry run that wrote would be unrecoverable
    by the time anyone read the report."""
    report = board_migrate.migrate(board([(1, "Nova", "")]), "issue")

    assert report["rows"] == 1
    assert report["applied"] is False
    assert not board_store.stored_documents("issue")
    assert couch.bulk_calls == []


def test_apply_stores_the_rows_and_they_read_back(couch):
    report = board_migrate.migrate(
        board([(1, "Nova", ""), (2, "Marcus", "v1")]), "issue", apply=True)

    assert report["written"] == 2
    assert report["stored"] == 2
    assert [row["number"] for row in board_store.read_rows("issue")] == [1, 2]


def test_a_detail_body_makes_the_trip(couch):
    """`render_tables` draws the two tables only, so a migration that dropped
    every prose body would look identical in every rendered comparison. It is
    counted here instead."""
    markdown = board([(1, "Nova", "")], details=[(1, "why row 1 exists")])
    report = board_migrate.migrate(markdown, "issue", apply=True)

    assert report["details"] == 1
    stored = board_store.read_row("issue", 1)
    assert "why row 1 exists" in (stored.get("detail") or "")


def test_a_board_that_already_holds_records_is_refused(couch):
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)
    before = len(couch.bulk_calls)

    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    assert couch.bulk_calls[before:] == [], "the refused run still wrote"
    assert len(board_store.stored_documents("issue")) == 1


def test_the_other_board_is_not_what_blocks_a_run(couch):
    """`stored_documents` is per board, and a migration of the second board
    has to be possible after the first one landed."""
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)
    report = board_migrate.migrate(board([(9, "Nova", "")]), "idea", apply=True)

    assert report["stored"] == 1
    assert len(board_store.stored_documents("issue")) == 1


def test_the_second_board_reuses_the_first_boards_project_id(couch):
    """Two runs, one project, one id.

    This one is deliberately kept even though it cannot fail: minting is
    deterministic from the name, so a fresh registry gives the same
    `prj_nova` and the assertion holds against a migration that never read
    the store at all. It is here as a statement of the invariant, and the
    test below is the one that actually guards it -- I wrote this one first,
    believed it was the guard, and only found out by mutating the source."""
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)
    board_migrate.migrate(board([(9, "Nova", "")]), "idea", apply=True)

    issue_row = board_store.read_row("issue", 1)
    idea_row = board_store.read_row("idea", 9)
    assert issue_row["projectId"]
    assert issue_row["projectId"] == idea_row["projectId"]


def test_a_project_the_markdown_never_mentions_survives_the_run(couch):
    """The registry is one document for both boards and for everything
    minted before this run, and `write_registry` writes it whole. A
    migration composing against `entity_id.new_registry()` would send back
    a registry holding only the names its own markdown mentioned, which
    either erases the rest or -- because a fresh registry carries no
    revision -- collides. Either way the ids that outlive this are the
    thing #203 says are permanent."""
    seeded = board_store.read_registry()
    kept = entity_id.ensure_project(seeded, "Marcus")
    board_store.write_registry(seeded)

    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    stored = board_store.read_registry()
    assert kept in stored["projects"], "a project no row mentioned was dropped"
    assert board_store.read_row("issue", 1)["projectId"] in stored["projects"]


def test_the_registry_is_stored_not_only_minted(couch):
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    assert board_store.read_registry().get("_rev") is not None
    assert board_store.read_registry()["projects"]


def test_an_unknown_board_is_refused_before_anything_is_read(couch):
    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board([(1, "Nova", "")]), "roadmap", apply=True)
    assert couch.bulk_calls == []


def test_the_cli_dry_run_says_so_and_exits_zero(couch, tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")

    code = board_migrate.main(["--board", "issue", "--file", str(path)])

    assert code == 0
    assert "dry run" in capsys.readouterr().out
    assert not board_store.stored_documents("issue")


def test_the_cli_exits_two_when_the_board_is_already_migrated(couch, tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")
    board_migrate.main(["--board", "issue", "--file", str(path), "--apply"])

    code = board_migrate.main(["--board", "issue", "--file", str(path), "--apply"])

    assert code == 2
    assert "REFUSED" in capsys.readouterr().out


def stored_captures(name):
    """His two parallel lists, read back the way `nova_site` reads them."""
    return board_document.captures_map(board_store.read_captures(name))


def test_a_capture_makes_the_trip_with_the_reply_under_it(couch):
    """The bug this whole commit is about: every earlier version of the
    migration composed the rows and silently left his own bullets in the
    markdown, and the registry it wrote was what `board_records.contents`
    reads to call the board migrated."""
    markdown = board(
        [(1, "Nova", "")],
        captures=[("move marcus to the other node", ["done, cycle 1200"])])

    report = board_migrate.migrate(markdown, "issue", apply=True)

    assert report["captures"] == 1
    assert report["captures_written"] == 1
    assert stored_captures("issue") == {
        "captures": ["move marcus to the other node"],
        "captureReplies": [["done, cycle 1200"]],
    }


def test_his_order_survives_past_ten_captures(couch):
    """The rank is not decoration. `read_captures` is unsorted and `_all_docs`
    answers lexically, so `cap_10` comes back before `cap_2`; a migration that
    minted no rank stores every capture and hands the list back shuffled, with
    nothing missing and nothing to notice. Eleven, because ten sort right."""
    texts = [f"capture number {i}" for i in range(1, 12)]
    markdown = board([(1, "Nova", "")], captures=[(t, []) for t in texts])

    board_migrate.migrate(markdown, "issue", apply=True)

    assert stored_captures("issue")["captures"] == texts


def test_a_dry_run_counts_his_captures_and_writes_none(couch):
    markdown = board([(1, "Nova", "")], captures=[("a bullet", [])])

    report = board_migrate.migrate(markdown, "issue")

    assert report["captures"] == 1
    assert report["applied"] is False
    assert not board_store.stored_capture_documents("issue")
    assert couch.bulk_calls == []


def test_a_board_holding_captures_but_no_rows_is_refused(couch):
    """A rows-only check calls this board clean, and the next run's
    `write_captures` prunes every capture it did not send -- so the state a
    half-finished earlier run leaves behind is the one that loses his words."""
    registry = board_store.read_registry()
    doc = board_document.to_capture_document(
        "his bullet", "issue", entity_id.mint_capture(registry, "issue"))
    board_store.write_captures("issue", [doc])
    before = len(couch.bulk_calls)

    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    assert couch.bulk_calls[before:] == [], "the refused run still wrote"
    assert stored_captures("issue")["captures"] == ["his bullet"]


def test_the_capture_high_water_is_stored_with_the_captures(couch):
    """Same pass as the captures, deliberately: the counter is what stops a
    later capture reusing `cap_1`, and a stored capture whose number the
    registry never recorded is that reuse waiting to happen."""
    markdown = board([(1, "Nova", "")], captures=[("one", []), ("two", [])])

    board_migrate.migrate(markdown, "issue", apply=True)

    stored = board_store.read_registry()
    assert entity_id.capture_high_water(stored, "issue") == 2


# --verify: the switchover's own precondition
#
# `board_migration_preflight --round-trip` already sends the rows through
# CouchDB, and it compares the *rendered markdown* -- the two tables. That
# cannot see a wrong `statusKey`, a lost `order`, a dropped detail body or a
# capture that never arrived, and those four are exactly what eighteen
# readers are about to be handed in place of a parse. So these tests are
# about `board_records.contents` agreeing with `nova_boards.parse_board`,
# and about the run leaving nothing behind.


def test_verify_agrees_with_the_parser_and_keeps_nothing(couch):
    markdown = board(
        [(1, "Nova", "Cycle reliability"), (2, "Marcus", "")],
        details=[(1, "why one matters")],
        captures=[("his bullet", ["a cycle answered"])],
    )
    report, problems = board_migrate.verify(markdown, "issue")
    assert problems == []
    assert report["contents_matches_parse"] is True
    assert (report["rows"], report["rows_back"]) == (2, 2)
    assert (report["captures"], report["captures_back"]) == (1, 1)
    assert (report["details"], report["details_back"]) == (1, 1)
    # Both key ranges, because emptying one is the half-migration state.
    assert board_store.stored_documents("issue") == {}
    assert board_store.stored_capture_documents("issue") == {}


def test_verify_leaves_the_registry_alone(couch):
    """The stated boundary of the run: it mints ids and stores none of them.

    A registry document is the one thing here with no restore path -- ids are
    permanent and there is no delete -- so a verify that wrote one and then
    failed would leave `prj_*` ids behind forever.
    """
    board_migrate.verify(board([(1, "Nova", "")]), "issue")
    assert board_store.read_registry().get("_rev") is None


def test_verify_sees_a_capture_the_store_never_took(couch, monkeypatch):
    """The control. `--round-trip` cannot fail this: captures are not in the
    rendered tables at all, so a store that took none of them renders
    identically."""
    monkeypatch.setattr(board_store, "write_captures",
                        lambda name, docs, prune=True: {"written": 0})
    report, problems = board_migrate.verify(
        board([(1, "Nova", "")], captures=[("his bullet", [])]), "issue")
    assert report["contents_matches_parse"] is False
    assert any("captures" in problem for problem in problems)


def test_verify_sees_a_detail_body_the_store_never_took(couch, monkeypatch):
    """Same control on the other thing the tables cannot carry."""
    real = board_document.to_document

    def without_the_body(item, name, **kwargs):
        kwargs["detail"] = None
        return real(item, name, **kwargs)

    monkeypatch.setattr(board_document, "to_document", without_the_body)
    report, problems = board_migrate.verify(
        board([(1, "Nova", "")], details=[(1, "why one matters")]), "issue")
    assert report["contents_matches_parse"] is False
    assert any("details" in problem for problem in problems)


def test_verify_refuses_a_board_that_already_holds_records(couch):
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)
    before = dict(board_store.stored_documents("issue"))
    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.verify(markdown, "issue")
    assert board_store.stored_documents("issue") == before


def test_verify_empties_the_store_when_the_read_back_raises(couch, monkeypatch):
    """A failure in the middle must not leave a half-migration behind."""
    def boom(name, store=None):
        raise board_records.RecordError("no")

    monkeypatch.setattr(board_records, "contents", boom)
    with pytest.raises(board_records.RecordError):
        board_migrate.verify(
            board([(1, "Nova", "")], captures=[("his bullet", [])]), "issue")
    assert board_store.stored_documents("issue") == {}
    assert board_store.stored_capture_documents("issue") == {}


def test_differences_names_the_field_of_the_row_that_moved():
    want = {"captures": [], "captureReplies": [], "details": {},
            "items": [{"number": 7, "title": "a", "order": 3}]}
    got = {"captures": [], "captureReplies": [], "details": {},
           "items": [{"number": 7, "title": "a", "order": None}]}
    problems = board_migrate.differences(want, got)
    assert len(problems) == 1
    assert "row #7" in problems[0] and "order" in problems[0]


def test_differences_reports_every_key_that_moved_not_just_the_first():
    want = {"captures": ["a"], "captureReplies": [[]], "items": [],
            "details": {1: "body"}}
    got = {"captures": [], "captureReplies": [], "items": [],
           "details": {}}
    problems = board_migrate.differences(want, got)
    assert len(problems) == 3


def test_verify_and_apply_are_refused_together(couch, tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")
    with pytest.raises(SystemExit):
        board_migrate.main(
            ["--board", "issue", "--file", str(path), "--verify", "--apply"])


def test_the_cli_verify_exits_two_and_prints_the_problem(couch, tmp_path,
                                                        capsys, monkeypatch):
    monkeypatch.setattr(board_store, "write_captures",
                        lambda name, docs, prune=True: {"written": 0})
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")], captures=[("his bullet", [])]),
                    encoding="utf-8")
    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--verify"])
    out = capsys.readouterr().out
    assert code == 2
    assert "verify.contents_matches_parse: False" in out
    assert "PROBLEM:" in out


def test_the_cli_verify_exits_zero_when_the_seam_agrees(couch, tmp_path,
                                                       capsys):
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")], captures=[("his bullet", [])]),
                    encoding="utf-8")
    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--verify"])
    out = capsys.readouterr().out
    assert code == 0
    assert "verify.contents_matches_parse: True" in out


# ---------------------------------------------------------------------------
# The layout, stored beside the records
# ---------------------------------------------------------------------------


def _with_archive(markdown):
    """His `## Processed captures` archive, spliced where he actually keeps
    it: between the table and `# Details`. Appending it to the end would
    make the position assertions below pass on a renderer that simply
    tacked the residue on, and position is the requirement --
    `board_migration_preflight.words_lost` is a sequence diff."""
    return markdown.replace(
        "\n# Details\n",
        "\n## Processed captures\n\n- an old bullet he keeps\n\n# Details\n")


def test_a_dry_run_stores_no_layout(couch):
    """Same contract as the rows: the switchover runs this against his live
    boards first, and a dry run that wrote is unrecoverable by the time
    anyone reads the report."""
    report = board_migrate.migrate(board([(1, "Nova", "")]), "issue")

    assert report["layout_blocks"] >= 1
    assert "layout_written" not in report
    assert board_store.read_layout("issue") is None


def test_apply_stores_the_layout_of_the_document_it_migrated(couch):
    markdown = board([(1, "Nova", "")], details=[(1, "why")])
    report = board_migrate.migrate(markdown, "issue", apply=True)

    assert report["layout_written"] is True
    assert (board_store.read_layout("issue")
            == board_document.to_layout_document(
                board_view.document_layout(markdown), "issue")["blocks"])


def test_the_stored_layout_renders_the_residue_back(couch):
    """The point of storing it at all, asserted end to end.

    His boards carry sections `parse_board` does not model -- the
    `## Processed captures` archive, the `# Done — detail` heading,
    `ideas.md`'s `## Discarded` table -- 19,653 words of `issues.md` and
    6,469 of `ideas.md` measured on 2026-09-09. A render driven by the four
    parsed keys alone deletes every one of them. This migrates a board with
    such a section, reads the layout back out of the store, and renders from
    the records: the section has to come back, **in its own position**,
    because `words_lost` is a sequence diff and a residue appended at the
    end still reads as lost.
    """
    markdown = _with_archive(board([(1, "Nova", "")], details=[(1, "why")]))
    board_migrate.migrate(markdown, "issue", apply=True)

    layout = board_store.read_layout("issue")
    rendered = board_view.render_document(
        nova_boards.parse_board(markdown), layout=layout)

    assert "an old bullet he keeps" in rendered
    assert rendered.index("Processed captures") > rendered.index("| # |")
    assert rendered.index("Processed captures") < rendered.index("# Details")


def test_without_the_stored_layout_that_residue_is_gone(couch):
    """The other half of the test above, and the reason it is not vacuous.

    A check that only asserts the section is present passes just as well
    against a renderer that emits the whole source document, or against one
    that happens to keep everything. This renders the same records with no
    layout and asserts the archive is **not** there -- so the assertion
    above is measuring the layout rather than the fixture.
    """
    markdown = _with_archive(board([(1, "Nova", "")], details=[(1, "why")]))

    without = board_view.render_document(nova_boards.parse_board(markdown))

    assert "an old bullet he keeps" not in without


def test_a_second_migration_of_the_same_board_is_refused_before_the_layout(couch):
    """`migrate` refuses a board that already holds records, and the layout
    must not be written by the run that was refused -- it is computed before
    the refusal has a chance to fire in a later version of this function."""
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)
    first = board_store.read_layout("issue")

    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board([(2, "Marcus", "")]), "issue", apply=True)

    assert board_store.read_layout("issue") == first


# ---------------------------------------------------------------------------
# `--verify` takes the layout through the real store too
# ---------------------------------------------------------------------------


def test_verify_carries_the_layout_through_the_store_and_keeps_none(couch):
    """The control the switchover actually needs.

    `verify` writes the board, reads it back through the seam the eighteen
    remaining readers are about to be handed, and restores. Until now it
    took the rows and the captures through and left the layout out -- so
    the one document that holds his `## Processed captures` archive was the
    only part of a migration nothing measured against a live board.
    """
    markdown = _with_archive(board([(1, "Nova", "")], details=[(1, "why")]))

    report, problems = board_migrate.verify(markdown, "issue")

    assert problems == []
    assert report["layout_blocks_back"] == report["layout_blocks"] >= 1
    assert board_store.read_layout("issue") is None


def test_verify_reports_the_words_the_generated_view_would_drop(couch):
    """The number the switchover is waiting on, measured through the store.

    `board_migration_preflight` already counts this, but from a layout it
    computed in memory a line earlier. This one renders from the records
    and from the layout CouchDB handed back, which is the pair the app will
    actually hold.
    """
    markdown = _with_archive(board([(1, "Nova", "")], details=[(1, "why")]))

    report, _problems = board_migrate.verify(markdown, "issue")
    without_layout = board_view.render_document(
        nova_boards.parse_board(markdown),
        board_migration_preflight.frontmatter_of(markdown))

    assert "an old bullet he keeps" not in without_layout
    assert report["document_words_lost"] < len(
        board_migration_preflight.words_lost(markdown, without_layout))


def test_verify_catches_a_layout_the_store_did_not_take(couch, monkeypatch):
    """The guard, defused: a store that drops a block must fail the run.

    Without this the test above passes against a `verify` that reads the
    layout back and never compares it -- `differences` is written in
    `parse_board`'s four keys and none of them can see a layout, so that
    comparison agrees whether the layout survived or not.
    """
    real = board_store.read_layout

    def one_block_short(name):
        blocks = real(name)
        return None if blocks is None else blocks[1:]

    monkeypatch.setattr(board_store, "read_layout", one_block_short)
    markdown = _with_archive(board([(1, "Nova", "")], details=[(1, "why")]))

    report, problems = board_migrate.verify(markdown, "issue")

    assert report["contents_matches_parse"] is False
    assert any("block(s) written" in problem for problem in problems)
    # And the word count is taken from the copy the store handed back, not
    # from the one this run computed. `> 0` would not say that: the full
    # layout loses two words to `render_detail`'s heading reflow, so both
    # sides are non-zero and the assertion could never fail. The number has
    # to be the one the *short* layout produces.
    contents = nova_boards.parse_board(markdown)
    front = board_migration_preflight.frontmatter_of(markdown)
    whole = board_view.document_layout(markdown)
    from_stored = board_migration_preflight.words_lost(
        markdown, board_view.render_document(contents, front,
                                             layout=whole[1:]))
    from_memory = board_migration_preflight.words_lost(
        markdown, board_view.render_document(contents, front, layout=whole))
    assert len(from_stored) > len(from_memory)
    assert report["document_words_lost"] == len(from_stored)


def test_verify_catches_a_layout_that_came_back_changed(couch, monkeypatch):
    """The same length and different content -- the shape a count misses."""
    real = board_store.read_layout

    def relabelled(name):
        blocks = real(name)
        if not blocks:
            return blocks
        return [{**blocks[0], "kind": "verbatim", "markdown": "not his"}] \
            + blocks[1:]

    monkeypatch.setattr(board_store, "read_layout", relabelled)

    report, problems = board_migrate.verify(board([(1, "Nova", "")]), "issue")

    assert report["contents_matches_parse"] is False
    assert any(problem.startswith("layout[0] differs") for problem in problems)


def test_verify_puts_back_a_layout_that_was_already_stored(couch):
    """A layout sits outside both key ranges the refusal guards, so a board
    with no records can still hold one. Deleting it would be the loss this
    run exists to prove does not happen.

    The stored layout and the one this run computes are deliberately
    **different documents** -- the first has an archive section, the second
    does not. A verify that simply left its own layout behind would restore
    the right answer by accident if the two agreed, and the assertion could
    never fail.
    """
    board_migrate.migrate(
        _with_archive(board([(1, "Nova", "")], details=[(1, "why")])),
        "issue", apply=True)
    before = board_store.read_layout("issue")
    board_store.write_rows("issue", [])
    board_store.write_captures("issue", [])

    board_migrate.verify(board([(2, "Marcus", "")]), "issue")

    assert board_store.read_layout("issue") == before


def test_verify_empties_the_layout_when_the_read_back_raises(couch, monkeypatch):
    """The `finally` covers the layout too, not just the two key ranges."""
    def boom(name, store=None):
        raise board_records.RecordError("nope")

    monkeypatch.setattr(board_records, "contents", boom)
    with pytest.raises(board_records.RecordError):
        board_migrate.verify(board([(1, "Nova", "")]), "issue")

    assert board_store.read_layout("issue") is None


def test_verify_catches_a_layout_that_never_came_back(couch, monkeypatch):
    """`read_layout` answers `None` for a document that is not there, and a
    verify that wrote one and read `None` has found the store dropping it.

    Absent is not the same fault as changed and it takes its own branch:
    `layout_differences` cannot compare `None` block by block, so without
    this the one case where the layout vanished entirely would be the one
    case that passed.
    """
    monkeypatch.setattr(board_store, "read_layout", lambda name: None)

    report, problems = board_migrate.verify(board([(1, "Nova", "")]), "issue")

    assert report["layout_blocks_back"] is None
    assert report["contents_matches_parse"] is False
    assert any("nothing came back out of the store" in problem
               for problem in problems)


def test_status_says_never_migrated_before_anything_is_written(couch, capsys):
    """The verdict that decides whether the switchover may flip.

    `board_records.contents` raises `UnmigratedStore` here, and that raise is
    the only thing protecting an unconverted reader from a store nobody has
    written. `status` reports it as an answer instead of propagating it,
    because "never written" IS the answer to the question this asks.
    """
    verdict, problems = board_migrate.status(board([(1, "Nova", "")]), "issue")
    assert verdict == "NEVER MIGRATED"
    assert problems and "never been written" in problems[0]


def test_status_agrees_with_a_store_it_did_not_write(couch):
    """The question `verify` structurally cannot ask.

    `verify` writes the board itself and restores afterwards, so it proves the
    seam round-trips and says nothing about a store somebody else left behind.
    Here the migration runs first and `status` is handed the result cold.
    """
    markdown = board(
        [(1, "Nova", "Cycle reliability"), (2, "Marcus", "")],
        details=[(1, "why one matters")],
        captures=[("his bullet", ["a cycle answered"])],
    )
    board_migrate.migrate(markdown, "issue", apply=True)
    verdict, problems = board_migrate.status(markdown, "issue")
    assert (verdict, problems) == ("AGREES", [])


def test_status_catches_a_board_the_owner_edited_after_the_migration(couch):
    """Drift, which is the whole reason this is not `verify`.

    Nothing on `main` writes the record store, so a board seeded today goes
    stale the moment he adds a row -- and a stale store answers silently where
    an unmigrated one raises. A verdict that could not tell those apart would
    be worse than no check.
    """
    seeded = board([(1, "Nova", "")])
    board_migrate.migrate(seeded, "issue", apply=True)
    verdict, problems = board_migrate.status(
        board([(1, "Nova", ""), (2, "Nova", "")]), "issue")
    assert verdict == "DRIFTED"
    assert any("items" in problem for problem in problems)


def test_status_asks_the_store_it_was_handed(couch):
    """`store=` has to be honoured, not decorated.

    Every other test here reaches the store through the fake CouchDB, so the
    default argument answers them all and an implementation that dropped the
    parameter would pass every one of them -- measured, that mutation
    SURVIVED until this test existed. The stub says "never migrated" while
    the real store is migrated, so the two answers cannot be confused.
    """
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)
    assert board_migrate.status(markdown, "issue")[0] == "AGREES"

    class NeverMigrated:
        """Enough of `board_store` for `contents` to reach its first check."""
        @staticmethod
        def read_registry():
            return {}

    verdict, _ = board_migrate.status(markdown, "issue", store=NeverMigrated)
    assert verdict == "NEVER MIGRATED"


def test_status_writes_nothing(couch):
    """It is offered as the read-only one; a write here would be a trap.

    Compared against both key ranges and the registry, because minting a
    project id is the one write in this module with no restore path.
    """
    board_migrate.status(board([(1, "Nova", "Cycle reliability")]), "issue")
    assert board_store.stored_documents("issue") == {}
    assert board_store.stored_capture_documents("issue") == {}
    assert board_store.read_registry().get("_rev") is None


def test_status_exits_2_on_drift_and_0_on_agreement(couch, tmp_path, capsys):
    """`main`'s contract, which is what preflight would read."""
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")
    assert board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status"]) == 2
    assert "status.verdict: NEVER MIGRATED" in capsys.readouterr().out

    board_migrate.migrate(path.read_text(encoding="utf-8"), "issue", apply=True)
    assert board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status"]) == 0
    assert "status.verdict: AGREES" in capsys.readouterr().out


def test_status_refuses_to_be_combined_with_a_write(tmp_path):
    """`--status` beside `--apply` is two questions, and the write would win."""
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")
    for other in ("--apply", "--verify"):
        with pytest.raises(SystemExit) as raised:
            board_migrate.main(
                ["--board", "issue", "--file", str(path), "--status", other])
        assert raised.value.code == 2
