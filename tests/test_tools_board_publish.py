"""`tools.board_publish` -- the records drawn back out as his board file.

`board_migrate` fills the store and `--status` says whether it still agrees.
This is the other direction, and it is the one the switchover is blocked on:
once a writer changes records instead of markdown, his `issues.md` is stale
until something draws it. So the cases here are about what survives the trip
*out* -- his frontmatter, his non-table blocks, a row's write-up -- and about
the two things that must never happen: a vault write, and a file written when
the render did not come back faithful.

Same fake CouchDB as `test_tools_board_migrate.py`, for the same reason: a
fake `board_store` would let a render that never reached a database pass.
"""
import pytest

from agora_runner import board_view, ticket_docs
from tools import board_migrate, board_publish

from tests.test_board_store import FakeCouch
from tests.test_tools_board_migrate import board


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


FRONTMATTER = (
    "---\n"
    "type: log\n"
    "contract: Edvard writes in the bare bullet list at the top.\n"
    "---\n"
)


def seeded(couch, markdown, board_name="issue"):
    board_migrate.migrate(markdown, board_name, apply=True)
    return markdown


def test_a_seeded_board_draws_back_faithfully(couch):
    """The whole claim of the tool in one case: what comes out re-reads as
    the records it was drawn from, on all four keys and the layout."""
    markdown = seeded(couch, FRONTMATTER + board(
        [(1, "Nova", ""), (2, "Marcus", "v1")], details=[(1, "why row 1")]))

    text, problems = board_publish.render("issue", markdown)

    assert problems == []
    assert "why row 1" in text
    assert "Item 2" in text


def test_his_frontmatter_survives_because_the_store_does_not_hold_it(couch):
    """`board_records.contents` has no frontmatter key and never will --
    it is the parser's four keys. A render that took the store alone would
    publish his board without the `contract:` line that explains it."""
    markdown = seeded(couch, FRONTMATTER + board([(1, "Nova", "")]))

    text, problems = board_publish.render("issue", markdown)

    assert problems == []
    assert text.startswith("---\n")
    assert "contract: Edvard writes in the bare bullet list" in text


def test_a_block_the_parser_does_not_model_is_drawn_from_the_layout(couch):
    """Cycle 1360's measured failure, in the other direction: his
    `## Processed captures` archive is not in the four keys, so a render
    built from them alone drops it silently and every key still agrees."""
    markdown = seeded(couch, FRONTMATTER + board([(1, "Nova", "")])
                      + "\n## Processed captures\n\nhis nineteen thousand words\n")

    text, problems = board_publish.render("issue", markdown)

    assert problems == []
    assert "## Processed captures" in text
    assert "his nineteen thousand words" in text


def test_a_render_that_drops_a_write_up_is_refused(couch, monkeypatch, tmp_path):
    """The refusal has to have a failure it can actually see. A render bug
    that loses a row's prose leaves every table identical, so the check is
    the re-read rather than the tables."""
    markdown = seeded(couch, FRONTMATTER + board(
        [(1, "Nova", "")], details=[(1, "prose only this row has")]))
    real = board_view.render_document

    def lossy(contents, **kwargs):
        stripped = dict(contents)
        stripped["details"] = {}
        return real(stripped, **kwargs)

    monkeypatch.setattr(board_view, "render_document", lossy)
    source = tmp_path / "issues.md"
    source.write_text(markdown, encoding="utf-8")
    out = tmp_path / "rendered.md"

    code = board_publish.main(
        ["--board", "issue", "--file", str(source), "--out", str(out)])

    assert code == 2
    assert not out.exists()


def test_an_unmigrated_store_refuses_rather_than_drawing_an_empty_board(
        couch, tmp_path):
    """`read_rows` answers `[]` for a board nobody seeded, so without the
    registry check this would publish his board as an empty table."""
    source = tmp_path / "issues.md"
    source.write_text(FRONTMATTER + board([(1, "Nova", "")]), encoding="utf-8")
    out = tmp_path / "rendered.md"

    code = board_publish.main(
        ["--board", "issue", "--file", str(source), "--out", str(out)])

    assert code == 2
    assert not out.exists()


def test_without_out_nothing_is_written_anywhere(couch, tmp_path, capsys):
    """The default is read-only for the same reason `board_migrate`'s is:
    this runs against his live board and a default that wrote would be
    unrecoverable by the time anyone read the report."""
    markdown = seeded(couch, FRONTMATTER + board([(1, "Nova", "")]))
    source = tmp_path / "issues.md"
    source.write_text(markdown, encoding="utf-8")

    code = board_publish.main(["--board", "issue", "--file", str(source)])

    assert code == 0
    assert list(tmp_path.iterdir()) == [source]
    assert "nothing was written" in capsys.readouterr().out


def test_out_writes_the_file_and_prints_the_board_put_command(couch, tmp_path,
                                                              capsys):
    """It prints the command instead of running it: `tools.board_put` is the
    one door a board goes through, because the vault write has to be followed
    by the ticket store, and a second door here is the split brain #203
    exists to remove."""
    markdown = seeded(couch, FRONTMATTER + board([(1, "Nova", "")]))
    source = tmp_path / "issues.md"
    source.write_text(markdown, encoding="utf-8")
    out = tmp_path / "rendered.md"

    code = board_publish.main(
        ["--board", "issue", "--file", str(source), "--out", str(out),
         "--vault-path", "projects/sokrates/projects/nova/issues.md"])

    printed = capsys.readouterr().out
    assert code == 0
    assert out.read_text(encoding="utf-8").startswith("---\n")
    assert "tools.board_put 'projects/sokrates/projects/nova/issues.md'" in printed


def test_word_delta_counts_repeats_not_distinct_words():
    """A set difference reports zero for a word his archive holds four times
    and the render holds once, which is exactly the shape of a dropped
    block."""
    added, dropped = board_publish.word_delta(
        "padding padding padding padding", "padding new")

    assert dropped == 3
    assert added == 1


def test_a_render_that_drops_his_archive_is_refused_by_the_layout_check(
        couch, monkeypatch):
    """The four keys structurally cannot see this and would report a clean
    render: `board_records.contents` and the board parser are both written in
    the parser's four keys, and a `## Processed captures` block is in neither.
    So the layout is asked separately, and this is the failure that proves the
    second question is not the first one twice."""
    markdown = seeded(couch, FRONTMATTER + board([(1, "Nova", "")])
                      + "\n## Processed captures\n\nhis nineteen thousand words\n")
    real = board_view.render_document

    def layoutless(contents, **kwargs):
        kwargs["layout"] = None
        return real(contents, **kwargs)

    monkeypatch.setattr(board_view, "render_document", layoutless)

    text, problems = board_publish.render("issue", markdown)

    assert "his nineteen thousand words" not in text
    assert problems
    assert any(problem.startswith("layout") for problem in problems)


def test_a_render_that_drops_his_capture_bullet_is_refused_by_the_four_keys(
        couch, monkeypatch):
    """The mirror of the layout case, and it is why both checks are here. His
    capture bullets sit above the first heading, so no layout block holds them
    and the layout comparison agrees whether they survived or not. The four
    keys are the only side that can see this one."""
    markdown = seeded(couch, FRONTMATTER + "\n- a bare capture of his\n- \n\n"
                      + board([(1, "Nova", "")]))
    real = board_view.render_document

    def captureless(contents, **kwargs):
        stripped = dict(contents)
        stripped["captures"] = []
        stripped["captureReplies"] = []
        return real(stripped, **kwargs)

    monkeypatch.setattr(board_view, "render_document", captureless)

    text, problems = board_publish.render("issue", markdown)

    assert "a bare capture of his" not in text
    assert problems
    assert any(problem.startswith("captures") for problem in problems)
