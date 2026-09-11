"""`tools.roll_done_captures` -- the finished bullets leave his box.

Two failures point opposite ways and both are guarded here. Moving too
little leaves the box he types into full of closed work, which is the
complaint this answers. Moving too much takes a sentence of his out of
the only place he looks, and `delete_capture` is a delete: there is
nothing to restore it from.

**Everything goes through the fake record store**, the rule
`tests/test_tools_board_capture.py` set and for its reason: a test that
asserts on `parse_board` of a file on disk agrees with a converted and an
unconverted tool alike, so it cannot tell the two apart.

The one thing asserted here that lives nowhere else is the **order of the
two writes**. The archive is written before the captures are deleted, so
a run that dies between them duplicates a bullet rather than losing one --
and an ordering claim needs a witness that remembers, which is what
`WritableFakeStore.calls` is for.
"""

import pytest

from agora_runner import board_document, board_records, board_store
from tests.test_board_records import writable
from tools import roll_done_captures
from tools.roll_done_captures import (
    ArchiveRefused, PROCESSED_HEADING, archived, bullet_lines, check_after,
    finished,
)

SHIPPED = "DONE (Cycle 434): the search bar closes my keyboard"
ALSO = "DONE (Cycle 435): make the chat modal full height"
OPEN = "connect Nova to my home NAS"

BOARD = f"""- {SHIPPED}
  - Nova, cycle 434: shipped it.
- {OPEN}
- {ALSO}
-

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| #2 | The search bar closes my keyboard | ⚪ Backlog | 08-20 | 🟠 High |

# Details

### #2 — The search bar closes my keyboard

Every letter dismisses it.

## Processed captures

- DONE (Cycle 400): an older one, already filed
"""

def capture(text, replies=(), capture_id="cap_9"):
    """A real capture document, because `capture_text_of` checks its identity.

    A dict of the two fields this tool reads would pass every assertion here
    and raise against anything the store ever handed back.
    """
    return board_document.to_capture_document(
        text, "issue", capture_id, rank="m", replies=list(replies))


ARCHIVE = [
    {"kind": "board"},
    {"kind": "detail", "number": 2},
    {"kind": "verbatim",
     "markdown": PROCESSED_HEADING + "\n\n- DONE (Cycle 400): already filed"},
]


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of that board, with an archive block stored."""
    _, fake = writable(board="issue", markdown=BOARD)
    fake.layouts["issue"] = [dict(block) for block in ARCHIVE]
    monkeypatch.setattr(roll_done_captures, "board_store", fake)
    return fake


def texts(store):
    return board_records.contents("issue", store=store)["captures"]


def archive_of(store):
    return store.layouts["issue"][-1]["markdown"]


def test_the_done_bullets_leave_the_box_and_the_open_one_stays(store):
    """The anchor. Two closed, one open, and the open one is his newest."""
    assert roll_done_captures.main(["--board", "issue"]) == 0

    assert texts(store) == [OPEN]


def test_the_moved_bullet_arrives_in_the_archive_verbatim(store):
    """Nothing is summarised: his sentence is the same string on both sides."""
    assert roll_done_captures.main(["--board", "issue"]) == 0

    body = archive_of(store)
    assert f"- {SHIPPED}" in body
    assert f"- {ALSO}" in body
    assert "- DONE (Cycle 400): already filed" in body, "the old archive stays"


def test_a_reply_written_under_his_bullet_travels_with_it(store):
    """A reply is `replies` on the document, and it is still his page.

    A capture that arrived in the archive without the note a cycle wrote
    under it would read as if nobody had answered him.
    """
    assert roll_done_captures.main(["--board", "issue"]) == 0

    assert "    - Nova, cycle 434: shipped it." in archive_of(store)


def test_the_archive_is_written_before_a_single_capture_is_deleted(store):
    """Stopping between the two writes may duplicate a bullet, never lose one.

    `delete_capture` takes his words and every reply under them with
    nothing to restore them from, so the order is the whole safety
    argument -- asserted against the witness rather than against the
    docstring.
    """
    assert roll_done_captures.main(["--board", "issue"]) == 0

    kinds = [call[0] for call in store.calls]
    assert kinds.count("write_layout") == 1
    assert kinds.index("write_layout") < kinds.index("delete_capture")


def test_a_second_run_moves_nothing(store):
    """Idempotent: the marker is gone from the box, so the walk finds none."""
    assert roll_done_captures.main(["--board", "issue"]) == 0
    store.calls.clear()

    assert roll_done_captures.main(["--board", "issue"]) == 0
    assert store.calls == []


def test_a_dry_run_writes_nothing(store):
    assert roll_done_captures.main(["--board", "issue", "--dry-run"]) == 0

    assert store.calls == []
    assert len(texts(store)) == 3


def test_an_unmigrated_store_is_not_an_empty_box(monkeypatch, capsys):
    """`read_captures` answers `[]` for both, and they mean opposite things.

    The failure `capture_documents` was given its registry check for: the
    tool would otherwise print `nothing to move` and exit 0 having looked
    at nothing at all.
    """
    _, fake = writable(board="issue", markdown=BOARD)
    fake.registry = dict(fake.registry)
    fake.registry.pop("_rev")
    monkeypatch.setattr(roll_done_captures, "board_store", fake)

    assert roll_done_captures.main(["--board", "issue"]) == 1
    assert "never been written" in capsys.readouterr().err
    assert fake.calls == [], "and it wrote nothing on the way to saying so"


def test_a_board_with_no_stored_layout_is_refused(store, capsys):
    """`None` is not an empty archive, and minting one here draws his file."""
    store.layouts.pop("issue")

    assert roll_done_captures.main(["--board", "issue"]) == 1
    assert "no stored layout" in capsys.readouterr().err
    assert store.calls == []
    assert len(texts(store)) == 3, "and the box is untouched"


def test_a_heading_below_the_archive_heading_is_refused(store, capsys):
    """A verbatim block runs to the next heading the layout claims.

    So a `##` below `## Processed captures` inside the same block would
    take his bullet into a section nobody chose. Refusing costs a message.
    """
    store.layouts["issue"][-1]["markdown"] += "\n\n## Something else\n\n- a line"

    assert roll_done_captures.main(["--board", "issue"]) == 1
    assert "further heading" in capsys.readouterr().err
    assert store.calls == []


def test_a_board_with_no_archive_section_gets_one(store):
    """Only `ideas.md` and `issues.md` have one today; a third board would not."""
    store.layouts["issue"] = [{"kind": "board"}]

    assert roll_done_captures.main(["--board", "issue"]) == 0

    assert store.layouts["issue"][-1]["kind"] == "verbatim"
    assert PROCESSED_HEADING in store.layouts["issue"][-1]["markdown"]
    assert f"- {SHIPPED}" in store.layouts["issue"][-1]["markdown"]


def test_the_heading_is_matched_as_a_line_and_not_as_a_substring():
    """A write-up that mentions the phrase is not the archive.

    190KB of his prose and mine sit in these files, so a substring match
    finding it inside a sentence is a matter of time -- and the bullets
    would then be appended to the middle of a write-up. Reviewer finding
    on runner#286, carried across the conversion.
    """
    blocks = [{"kind": "verbatim",
               "markdown": "I moved it to the ## Processed captures list."}]

    after = archived(blocks, [capture("DONE (Cycle 9): x")])

    assert after[0] == blocks[0], "the prose block is untouched"
    assert after[-1]["markdown"].startswith(PROCESSED_HEADING)


def test_archived_does_not_mutate_the_blocks_it_was_handed():
    """A refusal below has to leave the caller's copy of the layout intact."""
    blocks = [{"kind": "verbatim", "markdown": PROCESSED_HEADING}]

    archived(blocks, [capture("DONE (Cycle 9): x")])

    assert blocks == [{"kind": "verbatim", "markdown": PROCESSED_HEADING}]


def test_finished_reads_the_marker_and_not_the_word_done():
    """`split_capture_done` owns the shape; a bullet merely saying "done" stays."""
    docs = [capture("DONE (Cycle 9): closed", capture_id="cap_1"),
            capture("this one is done, honestly", capture_id="cap_2")]

    assert [doc["text"] for doc in finished(docs)] == ["DONE (Cycle 9): closed"]


def test_bullet_lines_indents_a_reply_the_way_the_view_draws_it():
    lines = bullet_lines(capture("his sentence", ["mine", "and mine"]))

    assert lines == ["- his sentence", "    - mine", "    - and mine"]


def test_the_check_catches_a_bullet_that_left_the_box_and_arrived_nowhere():
    """The failure the after-check exists for, and the one a sent-side check
    cannot see: the delete landed, the archive write did not carry it."""
    before = {"items": [], "details": {}, "captures": ["DONE (Cycle 9): x", "open"]}
    after = {"items": [], "details": {}, "captures": ["open"]}

    problems = check_after(before, after, "## Processed captures\n",
                           [capture("DONE (Cycle 9): x")])

    assert problems == ["a moved capture is not in the archive: DONE (Cycle 9): x"]


def test_the_check_catches_a_row_that_changed_underneath_the_roll():
    before = {"items": [{"number": 1}], "details": {}, "captures": []}
    after = {"items": [{"number": 2}], "details": {}, "captures": []}

    assert "board rows changed" in check_after(before, after, "", [])


def test_the_check_catches_an_unfinished_capture_that_left_the_box():
    """Moving too much is the failure with no undo, so it is its own case."""
    before = {"items": [], "details": {}, "captures": ["DONE (Cycle 9): x", "his"]}
    after = {"items": [], "details": {}, "captures": []}

    problems = check_after(before, after, "DONE (Cycle 9): x",
                           [capture("DONE (Cycle 9): x")])

    assert problems == ["2 capture(s) left the box, expected 1"]


def test_the_tool_reaches_the_store_module_and_not_a_second_import():
    """The monkeypatched seam is the one `main` actually calls."""
    assert roll_done_captures.board_store is board_store


def test_the_check_counts_bullets_and_not_distinct_sentences():
    """Nothing stops him typing the same line twice, and a membership test
    reads both copies as still there when one of them was deleted."""
    before = {"items": [], "details": {}, "captures": ["same", "same"]}
    after = {"items": [], "details": {}, "captures": ["same"]}

    problems = check_after(before, after, "", [])

    assert problems == ["1 capture(s) left the box, expected 0"]


def test_a_failed_after_check_says_the_roll_already_landed(store, monkeypatch, capsys):
    """The one refusal in this tool that does not mean "nothing happened".

    Every other one fires before a byte is written. This one fires after the
    archive write and the deletes, and a cycle that read it as "untouched"
    would go on to write the board again on top of a roll that succeeded.
    """
    monkeypatch.setattr(roll_done_captures, "check_after",
                        lambda *a, **k: ["board rows changed"])

    assert roll_done_captures.main(["--board", "issue"]) == 1
    err = capsys.readouterr().err
    assert "the roll LANDED" in err
    assert "REFUSED" not in err
    assert texts(store) == [OPEN], "and it really did land"


def test_the_fake_store_refuses_a_block_kind_the_renderer_cannot_draw(store):
    """The fake goes through `to_layout_document`, as `board_store` does.

    `render_document` silently drops a block it does not recognise, so a
    fake that stored whatever it was handed would let a caller pass here
    and delete his archive against CouchDB. Reviewer finding on #968.
    """
    with pytest.raises(board_document.DocumentError):
        store.write_layout("issue", [{"kind": "nonsense"}])
