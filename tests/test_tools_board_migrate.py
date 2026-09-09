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

from agora_runner import board_document, board_store, entity_id, ticket_docs
from tools import board_migrate

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
