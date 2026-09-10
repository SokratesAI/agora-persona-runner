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

from agora_runner import board_store, entity_id, ticket_docs
from tools import board_migrate

from tests.test_board_store import FakeCouch

HEADER = (
    "## Board\n\n"
    "| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |\n"
    "|---|------|--------|---------|----------|---------|------|-----------|-------|\n"
)


def board(rows, details=()):
    """Board markdown for `(number, project, milestone)` triples."""
    lines = [
        f"| [[#{n} — Item {n}\\|{n}]] | Item {n} | ⚪ Backlog | 09-09 "
        f"| 🟡 Medium | {project} | | {milestone} | |"
        for n, project, milestone in rows
    ]
    text = HEADER + "\n".join(lines) + "\n"
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


ARCHIVE = "## Processed captures\n\nAn archive the four keys do not model.\n"


def board_with_captures(rows, captures=(), tail=""):
    """`board()` plus his own bullets above the first heading, and a tail.

    His bullets and anything after the tables are the half of the document
    `parse_board`'s four keys do not carry, which is the half a seed of the
    rows alone silently drops.
    """
    bullets = "".join(f"- {text}\n" for text in captures) + "- \n\n"
    return bullets + board(rows) + tail


def test_his_capture_bullets_are_stored_and_read_back(couch):
    """The bullets live in their own key range so that a writer touching the
    rows cannot reach them -- which also means a migration writing the rows
    alone leaves them out, and a view rendered off that store hands his board
    back with the box he types into emptied."""
    from agora_runner import board_records

    markdown = board_with_captures(
        [(1, "Nova", "")], captures=["first thing he wrote", "second thing"])

    report = board_migrate.migrate(markdown, "issue", apply=True)

    assert report["captures"] == 2
    assert report["captures_stored"] == 2
    assert board_records.contents("issue")["captures"] == [
        "first thing he wrote", "second thing"]


def test_the_capture_order_is_the_order_he_wrote_them_in(couch):
    """Rank is his position in the file. Without it `captures_in_order` puts
    every unranked bullet in wire order, which CouchDB decides by id."""
    from agora_runner import board_records

    markdown = board_with_captures(
        [(1, "Nova", "")], captures=["alpha", "beta", "gamma"])
    board_migrate.migrate(markdown, "issue", apply=True)

    assert board_records.contents("issue")["captures"] == [
        "alpha", "beta", "gamma"]
    ranks = [doc.get("rank") for doc
             in board_store.stored_capture_documents("issue").values()]
    assert None not in ranks, "an unranked capture is ordered by its id"


def test_the_layout_is_stored_and_it_is_what_keeps_his_archive(couch):
    """The strong version of this test is a comparison, not an assertion that
    a document exists: rendering the same stored board with and without the
    layout has to differ, and differ by his prose. Asserting only that
    `read_layout` is not `None` would pass for a layout that had lost every
    verbatim block on the way in."""
    from agora_runner import board_records, board_view

    markdown = board_with_captures([(1, "Nova", "")], tail="\n" + ARCHIVE)
    board_migrate.migrate(markdown, "issue", apply=True)

    layout = board_store.read_layout("issue")
    assert layout is not None
    contents = board_records.contents("issue")
    assert "An archive the four keys do not model." in board_view.render_document(
        contents, layout=layout)
    assert "An archive the four keys do not model." not in \
        board_view.render_document(contents)


def test_a_dry_run_stores_no_captures_and_no_layout(couch):
    markdown = board_with_captures(
        [(1, "Nova", "")], captures=["something"], tail="\n" + ARCHIVE)

    report = board_migrate.migrate(markdown, "issue")

    assert report["captures"] == 1
    assert report["layout_blocks"] > 0
    assert report["captures_stored"] == 0
    assert board_store.stored_capture_documents("issue") == {}
    assert board_store.read_layout("issue") is None


def test_a_board_holding_captures_but_no_rows_is_refused(couch):
    """The state a half-finished migration leaves behind. Reading only
    `stored_documents` there calls the board empty and mints a second id for
    every bullet he has written, which is how an old reply lands under new
    words."""
    from agora_runner import board_document

    registry = board_store.read_registry()
    board_store.write_captures("issue", [board_document.to_capture_document(
        "his bullet", "issue", entity_id.mint_capture(registry, "issue"))])
    board_store.write_registry(registry)
    before = len(couch.bulk_calls)

    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board_with_captures(
            [(1, "Nova", "")], captures=["his bullet"]), "issue", apply=True)

    assert couch.bulk_calls[before:] == [], "the refused run still wrote"
    assert len(board_store.stored_capture_documents("issue")) == 1


def test_a_board_holding_a_layout_but_no_rows_is_refused(couch):
    from agora_runner import board_view

    board_store.write_layout("issue", board_view.document_layout(
        board([(1, "Nova", "")])))
    before = len(couch.bulk_calls)

    with pytest.raises(board_migrate.MigrationRefused):
        board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    assert couch.bulk_calls[before:] == [], "the refused run still wrote"


def test_the_cli_prints_the_capture_and_layout_counts(couch, tmp_path, capsys):
    path = tmp_path / "issues.md"
    path.write_text(board_with_captures(
        [(1, "Nova", "")], captures=["something"], tail="\n" + ARCHIVE),
        encoding="utf-8")

    code = board_migrate.main(["--board", "issue", "--file", str(path), "--apply"])

    out = capsys.readouterr().out
    assert code == 0
    assert "captures: 1" in out
    assert "captures_stored: 1" in out
    assert "layout_stored: " in out


def test_the_replies_under_a_bullet_make_the_trip(couch):
    """A reply is a cycle's answer to him, indented under his own words, and
    it is a separate field on the capture document -- so a migration that
    carried the bullets and dropped the replies would store his board with
    every answer he has been given deleted, and count the captures right."""
    from agora_runner import board_records

    markdown = ("- his bullet\n"
                "    - Nova, cycle 1: the answer\n"
                "    - Nova, cycle 2: and again\n"
                "- \n\n") + board([(1, "Nova", "")])

    board_migrate.migrate(markdown, "issue", apply=True)

    contents = board_records.contents("issue")
    assert contents["captures"] == ["his bullet"]
    assert contents["captureReplies"] == [
        ["Nova, cycle 1: the answer", "Nova, cycle 2: and again"]]


def test_the_migration_does_not_add_itself_to_the_gate():
    """`board_reader_inventory` is the gauge #203 drives to zero and it counts
    a module that names `parse_board` in its own text. The migration has to
    read markdown -- that is its whole job -- so it reads it through
    `board_migration_preflight`, which is already on the list. Parsing in
    `board_migrate` instead put a 22nd module on a count of 21, for a module
    that is not a board reader at all.

    The assertion is against the tool's own scan rather than a grep of the
    file, because a grep here would be a second spelling of the rule under
    test and would agree with itself."""
    from tools import board_reader_inventory

    found, _refs, _unreadable, _untokenized = board_reader_inventory.scan()

    names = {str(path) for path in found}
    assert not any(name.endswith("tools/board_migrate.py") for name in names), (
        "board_migrate parses board markdown itself again")
    assert any(name.endswith("tools/board_migration_preflight.py")
               for name in names), (
        "the module the migration reads markdown through left the gate; "
        "this test can no longer tell a move from a deletion")


# --- `--status`: does the seeded store still agree with his markdown? -------
#
# `migrate` refuses a board that already holds records, so the only board it
# can say anything about is one nobody has seeded -- and the seeded board is
# the one that can go wrong. These tests are about the board after the seed.


def test_a_store_nobody_has_written_is_never_migrated(couch):
    verdict, problems = board_migrate.status(board([(1, "Nova", "")]), "issue")

    assert verdict == "NEVER MIGRATED"
    assert problems and "never been written" in problems[0]


def test_the_markdown_that_was_seeded_agrees_with_the_store(couch):
    markdown = board([(1, "Nova", ""), (2, "Marcus", "v1")],
                     details=[(1, "Some body.")])
    board_migrate.migrate(markdown, "issue", apply=True)

    assert board_migrate.status(markdown, "issue") == ("AGREES", [])


def test_an_edit_he_makes_after_the_seed_reads_as_drift(couch):
    """The whole reason this exists: his boards are still served from
    markdown, so every edit he makes moves the file and leaves the store
    where it was, and `board_records.contents` answers a stale store
    silently."""
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)

    verdict, problems = board_migrate.status(
        board([(1, "Nova", ""), (2, "Marcus", "")]), "issue")

    assert verdict == "DRIFTED"
    assert any("items:" in problem for problem in problems)


def test_a_field_that_moved_is_named_with_the_row_it_moved_on(couch):
    """One line per key, and the line says which field of which row -- a dump
    of four hundred rows on both sides is not a finding."""
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)

    verdict, problems = board_migrate.status(
        markdown.replace("⚪ Backlog", "🔵 Done"), "issue")

    assert verdict == "DRIFTED"
    assert any("row #1" in problem and "status" in problem
               for problem in problems)


def test_a_layout_edit_is_drift_that_the_four_keys_cannot_see(couch):
    """`parse_board` and `board_records.contents` are both written in the
    parser's four keys and neither models a layout, so a comparison in those
    keys agrees about his `## Processed captures` archive whether it survived
    or not. This is why `layout_differences` is a separate question."""
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)
    edited = markdown + "\n## Processed captures\n\nSomething he archived.\n"

    from agora_runner import board_records
    from tools import board_migration_preflight
    assert not board_migrate.differences(
        board_migration_preflight.board_contents(edited),
        board_records.contents("issue"))

    verdict, problems = board_migrate.status(edited, "issue")

    assert verdict == "DRIFTED"
    assert any(problem.startswith("layout") for problem in problems)


def test_the_store_is_the_one_it_was_handed(couch):
    """`store=` is a decoration until a test passes one: every other test here
    reaches the store through the fake CouchDB, so dropping the parameter
    passes all of them."""
    class Refuses:
        def read_registry(self):
            raise AssertionError("status read the module-level store")

    with pytest.raises(AssertionError, match="status read"):
        board_migrate.status(board([(1, "Nova", "")]), "issue", store=Refuses())


def test_the_cli_exits_zero_and_writes_nothing_when_the_board_agrees(
        couch, tmp_path, capsys):
    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)
    path = tmp_path / "issues.md"
    path.write_text(markdown, encoding="utf-8")
    couch.bulk_calls.clear()

    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status"])

    assert code == 0
    assert "status: AGREES" in capsys.readouterr().out
    assert couch.bulk_calls == []


def test_the_cli_exits_two_on_drift(couch, tmp_path, capsys):
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", ""), (2, "Nova", "")]),
                    encoding="utf-8")

    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status"])

    assert code == 2
    assert "status: DRIFTED" in capsys.readouterr().out


def test_the_cli_refuses_status_together_with_apply(couch, tmp_path, capsys):
    """The two modes disagree about whether the run writes, so a caller who
    asked for both cannot be assumed to have meant the writing one."""
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")

    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status", "--apply"])

    assert code == 2
    assert "REFUSED" in capsys.readouterr().out
    assert couch.bulk_calls == []


def test_a_differing_layout_block_is_named_not_dumped(couch):
    """His live `issues.md` block 200 is his whole `## Processed captures`
    archive: 112,225 characters. Printed whole on both sides that is one
    221KB line, and the finding is only ever *which* block moved -- the block
    itself is a verbatim slice of the file the run was just handed."""
    markdown = (board([(1, "Nova", "")])
                + "\n## Archive\n\n" + ("padding line\n" * 400))
    board_migrate.migrate(markdown, "issue", apply=True)
    edited = markdown.replace("padding line", "padded line")

    verdict, problems = board_migrate.status(edited, "issue")

    assert verdict == "DRIFTED"
    line = next(p for p in problems if p.startswith("layout"))
    assert "char(s)" in line and "padding line" not in line
    assert len(line) < 400


def test_a_store_with_no_layout_at_all_is_drift_not_agreement(couch):
    """A rows-only seed is what the first #203 migration actually wrote, and
    it compared byte-identical on every key the parser models."""
    class NoLayout:
        def read_rows(self, board):
            return board_store.read_rows(board)

        def read_captures(self, board):
            return board_store.read_captures(board)

        def read_registry(self):
            return board_store.read_registry()

        def read_layout(self, board):
            return None

    markdown = board([(1, "Nova", "")])
    board_migrate.migrate(markdown, "issue", apply=True)

    verdict, problems = board_migrate.status(
        markdown, "issue", store=NoLayout())

    assert verdict == "DRIFTED"
    assert any("holds no layout" in problem for problem in problems)


def test_a_differing_table_block_is_named_by_its_columns(couch):
    """A table block holds no markdown -- its rows are the row documents --
    so the generic "N char(s), starting ..." line describes both sides of a
    real disagreement as empty and says nothing. Measured 2026-09-10 on his
    live `ideas.md`, whose board table has eight columns while `board_view`
    draws nine."""
    eight = board_migrate._head(
        {"kind": "board", "columns": ["#", "Idea", "Status", "Milestone"]})

    assert "columns" in eight
    assert "Milestone" in eight
    assert "char(s)" not in eight


# `--resync`: the second write of an already-seeded board. `migrate` is a
# one-way door on purpose and stays one; these are about the door beside it,
# and every one of them is about what SURVIVES the second write rather than
# what it stores, because storing is the easy half.


def test_a_resync_keeps_the_id_of_a_bullet_he_did_not_edit(couch):
    """The whole reason this is not "empty the board and seed it again".

    `entity_id.mint_capture` is deliberately not idempotent, so a re-seed
    hands every bullet a fresh id, and `nova_site` addresses his Edit route
    and the replies underneath by that id. Re-minting an unchanged bullet
    silently moves both."""
    markdown = board_with_captures(
        [(1, "Nova", "")], captures=["a thing he wrote", "another thing"])
    board_migrate.migrate(markdown, "issue", apply=True)
    before = {doc["text"]: doc["captureId"] for doc
              in board_store.stored_capture_documents("issue").values()}

    board_migrate.resync(markdown, "issue", apply=True)

    after = {doc["text"]: doc["captureId"] for doc
             in board_store.stored_capture_documents("issue").values()}
    assert after == before, "an unchanged bullet was re-minted"


def test_a_resync_mints_only_the_bullet_he_actually_added(couch):
    """The other half of the same rule: a new bullet has to get an id, and
    the report has to say which of the two happened -- a run that reports
    "3 captures" says nothing about whether it just orphaned two of them."""
    board_migrate.migrate(
        board_with_captures([(1, "Nova", "")], captures=["first"]),
        "issue", apply=True)

    report = board_migrate.resync(
        board_with_captures([(1, "Nova", "")], captures=["first", "second"]),
        "issue", apply=True)

    assert (report["captures_kept"], report["captures_minted"]) == (1, 1)
    from agora_runner import board_records
    assert board_records.contents("issue")["captures"] == ["first", "second"]


def test_a_resync_takes_his_edits_and_drops_what_he_deleted(couch):
    """Markdown in, store out. A row he closed, a write-up he changed and a
    bullet he deleted all have to land, or the store goes on serving what he
    replaced."""
    from agora_runner import board_records

    board_migrate.migrate(
        board_with_captures([(1, "Nova", ""), (2, "Nova", "")],
                            captures=["keep me", "delete me"]),
        "issue", apply=True)

    board_migrate.resync(
        board_with_captures([(1, "Nova", "")], captures=["keep me"]),
        "issue", apply=True)

    contents = board_records.contents("issue")
    assert [item["number"] for item in contents["items"]] == [1]
    assert contents["captures"] == ["keep me"]


def test_a_resync_refuses_a_board_nobody_has_seeded(couch):
    """A resync of an unmigrated board is a seed, and a seed is `migrate`'s
    job with `migrate`'s report and `migrate`'s refusals. Two commands that
    both first-write a board is the split brain #203 exists to remove."""
    with pytest.raises(board_migrate.MigrationRefused) as refused:
        board_migrate.resync(board([(1, "Nova", "")]), "issue", apply=True)

    assert "never been migrated" in str(refused.value)
    assert couch.bulk_calls == []


def test_a_resync_refuses_markdown_that_parses_to_no_rows(couch):
    """A board file is fetched over the vault tool and an oversized read comes
    back as a ~2KB preview rather than an error, so "no rows" is what a
    truncated fetch looks like from here. `write_rows` prunes, so accepting it
    would tombstone his whole board off a read that failed quietly."""
    board_migrate.migrate(board([(1, "Nova", "")]), "issue", apply=True)
    stored_before = set(board_store.stored_documents("issue"))

    with pytest.raises(board_migrate.MigrationRefused) as refused:
        board_migrate.resync("# Not a board\n", "issue", apply=True)

    assert "no rows at all" in str(refused.value)
    assert set(board_store.stored_documents("issue")) == stored_before


def test_a_resync_dry_run_writes_nothing(couch):
    """Same default as `migrate`: this runs against his live boards, and a
    dry run that wrote would be unrecoverable by the time anyone read it."""
    board_migrate.migrate(
        board_with_captures([(1, "Nova", "")], captures=["first"]),
        "issue", apply=True)
    calls = len(couch.bulk_calls)

    report = board_migrate.resync(
        board_with_captures([(1, "Nova", "")], captures=["first", "second"]),
        "issue")

    assert report["applied"] is False
    assert (report["captures_kept"], report["captures_minted"]) == (1, 1)
    assert len(couch.bulk_calls) == calls


def test_two_identical_bullets_keep_their_two_ids(couch):
    """He writes `- ` placeholders and repeats himself, so the same words can
    sit on his board twice. Matching text to a single id would hand both
    copies the first one's, and `_bulk_write` refuses a duplicate id -- the
    right refusal in the wrong place, after a bullet was already lost."""
    markdown = board_with_captures(
        [(1, "Nova", "")], captures=["same words", "same words"])
    board_migrate.migrate(markdown, "issue", apply=True)
    before = sorted(doc["captureId"] for doc
                    in board_store.stored_capture_documents("issue").values())

    report = board_migrate.resync(markdown, "issue", apply=True)

    assert report["captures_kept"] == 2
    assert sorted(doc["captureId"] for doc
                  in board_store.stored_capture_documents("issue").values()
                  ) == before


def test_the_cli_refuses_status_together_with_resync(capsys, tmp_path):
    """One reads and one writes; a run carrying both has asked for opposite
    things and there is no reading of it that is obviously what was meant."""
    path = tmp_path / "issues.md"
    path.write_text(board([(1, "Nova", "")]), encoding="utf-8")

    code = board_migrate.main(
        ["--board", "issue", "--file", str(path), "--status", "--resync"])

    assert code == 2
    assert "REFUSED" in capsys.readouterr().out


def test_a_resync_re_ranks_every_row_from_the_markdown(couch):
    """Pinned because it is a boundary, not because it is desirable. Ranks are
    minted fresh from the file's row order on every run, so a resync moves a
    row back to where the markdown puts it. Correct while markdown is truth;
    the day #202 writes a drag-reorder into the store, this is what would
    undo it, and a test that says so is cheaper than finding out."""
    board_migrate.migrate(board([(1, "Nova", ""), (2, "Nova", "")]),
                          "issue", apply=True)
    moved = board_store.read_row("issue", 2)
    moved["rank"] = "zzz"
    board_store.write_row(moved)
    assert board_store.read_row("issue", 2)["rank"] == "zzz"

    board_migrate.resync(board([(1, "Nova", ""), (2, "Nova", "")]),
                         "issue", apply=True)

    assert board_store.read_row("issue", 2)["rank"] != "zzz"
