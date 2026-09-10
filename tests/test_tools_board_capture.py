"""`tools.board_capture` -- one bullet leaves the box and arrives as a row.

The #203 conversion of the last writer that had no records door. What is
tested here is the two halves that door made separable:

**`promote` is pure now** -- his bullet in, `add_row`'s arguments out, no
store touched -- so every question about what a capture *becomes* (its
rating, its `DONE` marker, its project tag, its title) is answered without a
board at all. Those tests carry no markdown and no fake.

**Everything else goes through the fake store**, the rule
`tests/test_tools_board_size.py` set and for its reason: a test that asserts
on `parse_board` of a file on disk agrees with a converted and an unconverted
tool alike, so it cannot tell the two apart.

The row half of the after-check is `board_write.add_row`'s and is tested
there. What is still this module's own is the capture half -- that removing
one bullet removed exactly one bullet, with every other capture's replies
intact -- and the order of the two writes, which is the difference between a
failure that costs nothing and a failure that eats his words.
"""

import pytest

from agora_runner import board_records
from agora_runner.nova_boards import DEFAULT_PROJECT, canonical_priority
from tests.test_board_records import writable
from tools import board_capture
from tools.board_capture import (
    capture_pairs,
    check_captures,
    first_sentence,
    known_names,
    main,
    promote,
)

BOARD = """- Give me a landing page for the app. It should say what Nova is.
  - Nova, cycle 900: noted.
- 🟠 High: The journal page is slow on my phone.
- 

## Board

| # | Item | Status | Updated | Priority | Project | Size | Milestone | Order |
|---|---|---|---|---|---|---|---|---|
| [[#100 — Weekly work\\|100]] | Weekly work | 🟡 In progress | 08-24 | 🟠 High | Nova | M | Cost and quota | |
| [[#104 — Metered API\\|104]] | Metered API | ⚪ Backlog | 08-24 | 🟠 High | Marcus | | | |

## Done

| # | Item | Landed | Where |
|---|---|---|---|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

# Details

## #100 — Weekly work

Three heartbeats, one prompt file each.
"""


@pytest.fixture
def store(monkeypatch):
    """A migrated, writable fake of that board, wired in where `main` looks."""
    _, fake = writable(board="idea", markdown=BOARD)
    monkeypatch.setattr(board_capture, "board_store", fake)
    return fake


def _contents(store):
    return board_records.contents("idea", store=store)


def _rows(store):
    return {item["number"]: item for item in _contents(store)["items"]}


def _run(*args):
    return main(["--board", "idea", "--dated", "09-10", *args])


# --- the pure half: what a bullet becomes -------------------------------


def test_the_title_is_the_first_sentence_and_the_write_up_is_all_of_it():
    fields, refusal = promote(
        "Give me a landing page. It should say what Nova is.",
        None, "backlog", "09-10")
    assert refusal is None
    assert fields["title"] == "Give me a landing page."
    assert fields["write_up"] == "Give me a landing page. It should say what Nova is."


def test_first_sentence_keeps_a_long_one_whole():
    """No character count is involved, on purpose -- a truncated title reads
    as a different item from the write-up under it."""
    long_one = "I want " + "a very long thing " * 12 + "on the board. And more."
    assert first_sentence(long_one).endswith("on the board.")
    assert len(first_sentence(long_one)) > 120


def test_a_rated_capture_keeps_its_own_rating():
    fields, _ = promote("🟠 High: The journal page is slow.", None, "backlog", "09-10")
    assert canonical_priority(fields["priority"]) == "🟠 High"
    assert fields["write_up"] == "The journal page is slow."


def test_an_explicit_priority_beats_the_bullets_own():
    fields, _ = promote("🟠 High: The journal page is slow.", "immediate",
                        "backlog", "09-10")
    assert canonical_priority(fields["priority"]) == "🔴 Immediately"


def test_a_done_marker_lands_done_not_backlog():
    """His own case: *"Even some issues are fixed and done but still not moved
    out."* Boarding one as backlog puts a shipped item back in the queue."""
    fields, _ = promote("DONE (Cycle 900): The slow journal page.", "high",
                        "backlog", "09-10")
    assert fields["status"] == "done"
    assert fields["write_up"] == "The slow journal page."


def test_a_done_marker_does_not_override_a_status_the_caller_named():
    fields, _ = promote("DONE (Cycle 900): The slow journal page.", "high",
                        "in-progress", "09-10")
    assert fields["status"] == "in-progress"


def test_a_capture_that_is_only_prefixes_is_refused():
    fields, refusal = promote("DONE (Cycle 900): ", "high", "backlog", "09-10")
    assert fields is None
    assert "empty" in refusal


def test_a_rating_that_is_not_one_is_refused_before_anything_is_written():
    fields, refusal = promote("Something", "nonsense", "backlog", "09-10")
    assert fields is None
    assert "nonsense" in refusal


def test_a_project_tag_is_lifted_out_of_the_title_into_the_cell():
    fields, _ = promote("Fix the icon grid #marcus", None, "backlog", "09-10",
                        known=["Nova", "Marcus"])
    assert fields["project"] == "Marcus"
    assert "#marcus" not in fields["write_up"]
    assert fields["title"] == "Fix the icon grid"


def test_an_unknown_slug_is_left_alone_rather_than_invented():
    fields, _ = promote("Fix the icon grid #atlantis", None, "backlog", "09-10",
                        known=["Nova", "Marcus"])
    assert fields["project"] == ""
    assert "#atlantis" in fields["write_up"]


def test_an_explicit_project_flag_beats_the_bullets_own_tag():
    fields, _ = promote("Fix the icon grid #marcus", None, "backlog", "09-10",
                        project="Nova", known=["Nova", "Marcus"])
    assert fields["project"] == "Nova"


# --- the known names: the registry replaces `--projects-from` -----------


def test_the_known_names_are_the_boards_own_cells_plus_the_registry(store):
    """The flag that used to widen this took a path to the sibling board's
    markdown, so forgetting it silently narrowed the list -- PR #888's defect
    surviving in the three projects with no issue open. Both boards mint into
    one registry, so the union is a document now rather than an argument."""
    contents = _contents(store)
    assert board_projects_of(contents) == ["Nova", "Marcus"], "fixture's own cells"
    store.registry["projects"]["prj_demos"] = {
        "name": "Demos", "key": "demos", "aliases": []}
    names = known_names(contents, board_records.project_names(store=store))
    assert names[:2] == ["Nova", "Marcus"], "the board's own spelling first"
    assert "Demos" in names


def board_projects_of(contents):
    from agora_runner.nova_boards import board_projects
    return board_projects(contents["items"])


def test_a_project_only_in_the_registry_reaches_the_cell(store):
    store.registry["projects"]["prj_demos"] = {
        "name": "Demos", "key": "demos", "aliases": []}
    store.docs = [
        dict(doc, text="A landing page for the demos #demos")
        if doc.get("captureId") == "cap_1" else doc
        for doc in store.docs]
    assert _run("--index", "0", "--priority", "high") == 0
    assert _rows(store)[105]["project"] == "Demos"


# --- the store half: the two writes and the check between them ----------


def test_the_capture_leaves_the_box_and_arrives_as_a_row(store):
    before = _contents(store)
    assert _run("--index", "0", "--priority", "high") == 0

    after = _contents(store)
    assert after["captures"] == ["🟠 High: The journal page is slow on my phone."]
    row = _rows(store)[105]
    assert row["title"] == "Give me a landing page for the app."
    assert row["priority"] == "🟠 High"
    assert row["status"] == "⚪ Backlog"
    assert row["project"] == DEFAULT_PROJECT
    assert len(after["items"]) == len(before["items"]) + 1


def test_the_new_row_goes_to_the_top_of_his_board(store):
    """`add_row`'s rank, asserted from here because it is what he sees."""
    assert _run("--index", "0", "--priority", "high") == 0
    assert [item["number"] for item in _contents(store)["items"]][0] == 105


def test_the_reply_written_under_it_rides_across(store):
    assert _run("--index", "0", "--priority", "high") == 0
    assert "noted." in _contents(store)["details"][105]


def test_the_other_captures_keep_their_replies(store):
    """The failure this module has: against markdown an off-by-one in the span
    being cut took a neighbour's answer with it and left both documents
    looking fine."""
    before = capture_pairs(_contents(store))
    assert before[0][1] == ("Nova, cycle 900: noted.",), "the fixture must carry a reply"
    assert _run("--index", "1", "--priority", "high") == 0
    assert capture_pairs(_contents(store)) == [before[0]]


def test_the_index_is_his_order_and_not_the_stores(store):
    """`read_captures` answers in lexical id order, where `cap_10` sits
    between `cap_1` and `cap_2` -- so past ten captures a lookup that skipped
    `captures_in_order` boards the bullet he pointed at and deletes another.

    The fixture is grown past ten here rather than asserted on three, because
    the two orders agree for any board under ten and the bug is invisible.
    """
    from agora_runner import board_document, rank_key
    keys = rank_key.sequence(20)
    for index in range(3, 13):
        store.docs.append(dict(board_document.to_capture_document(
            f"Capture number {index}", "idea", f"cap_{index + 1}",
            rank=keys[index]), _rev="1-stored"))
    wanted = _contents(store)["captures"][10]
    # The precondition, asserted rather than assumed: the two orders have to
    # actually disagree at this position, or a tool that skipped the sort
    # would pass this test.
    raw = board_document.capture_text_of(store.read_captures("idea")[10])
    assert raw != wanted, "the fixture must reach past ten captures"

    assert _run("--index", "10", "--priority", "high") == 0
    assert wanted not in _contents(store)["captures"]
    assert raw in _contents(store)["captures"], "the store's tenth is untouched"
    assert _rows(store)[105]["title"].startswith(wanted[:20])


def test_the_delete_carries_the_revision_the_capture_was_read_at(store):
    """`board_store.delete_capture` refuses a document with no `_rev`, so a
    caller that re-minted one to get a shape it liked would be handing over a
    delete conditional on nothing -- and against the real store that is the
    blind delete of a bullet somebody answered in between."""
    assert _run("--index", "0", "--priority", "high") == 0
    sent = [doc for name, doc in store.calls if name == "delete_capture"]
    assert len(sent) == 1
    assert sent[0]["_rev"] == "1-stored"
    assert sent[0]["captureId"] == "cap_1"


def test_the_row_is_written_before_the_bullet_is_removed(store):
    """Not arbitrary. `add_row` refuses on its own after-check, so a failure
    there leaves his bullet where he left it; the other order takes his words
    out of the box and then discovers the row could not be written."""
    assert _run("--index", "0", "--priority", "high") == 0
    names = [name for name, _ in store.calls]
    assert names.index("write_row") < names.index("delete_capture")


def test_a_row_that_cannot_be_written_leaves_the_bullet_alone(store):
    """The reason for that order, asserted rather than described."""
    class NudgesASibling(type(store)):
        """A store whose row write also alters a row nobody named, so
        `add_row`'s own after-check raises *after* it has written."""

        def write_row(self, doc):
            stored = super().write_row(doc)
            self.docs = [
                dict(held, title=held["title"] + " (nudged)")
                if str(held.get("_id", "")).startswith("board:idea:")
                and held.get("number") != doc.get("number")
                else held
                for held in self.docs]
            return stored

    fake = NudgesASibling(store.docs, store.registry)
    board_capture.board_store = fake
    assert _run("--index", "0", "--priority", "high") == 1
    assert _contents(fake)["captures"][0].startswith("Give me a landing page")
    assert "delete_capture" not in [name for name, _ in fake.calls]


def test_it_refuses_an_index_that_is_not_there(store, capsys):
    assert _run("--index", "9", "--priority", "high") == 1
    assert "no capture at index 9" in capsys.readouterr().err
    assert store.calls == []


def test_it_refuses_a_negative_index(store):
    """`capture_at` answers `None` rather than counting back from the end, or
    `--index -1` boards the last bullet and the range check never fires."""
    assert _run("--index", "-1", "--priority", "high") == 1
    assert store.calls == []


def test_it_refuses_a_pipe_in_the_date(store, capsys):
    """A stray `|` shifts every column right of it, and the row still reads as
    a well-formed table."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09|10",
                 "--priority", "high"]) == 1
    assert "--dated" in capsys.readouterr().err
    assert store.calls == []


def test_it_refuses_a_rating_that_is_not_one(store, capsys):
    assert _run("--index", "0", "--priority", "nonsense") == 1
    assert "nonsense" in capsys.readouterr().err
    assert store.calls == []


def test_dry_run_writes_nothing(store):
    assert _run("--index", "0", "--priority", "high", "--dry-run") == 0
    assert store.calls == []
    assert len(_contents(store)["captures"]) == 2


def test_an_unmigrated_store_is_a_refusal_and_not_an_empty_board(store):
    """Otherwise the first run against one mints a row into a store whose
    every other row is missing, and `contents` reads that one row as his
    board."""
    store.registry.pop("_rev")
    assert _run("--index", "0", "--priority", "high") == 1
    assert store.calls == []


@pytest.mark.parametrize("status,cell", [
    ("backlog", "⚪ Backlog"),
    ("in-progress", "🟡 In progress"),
    ("done", "✅ Done"),
    ("blocked-on-edvard", "⏸ Blocked on Edvard"),
])
def test_every_status_reaches_the_cell(store, status, cell):
    assert _run("--index", "0", "--priority", "high", "--status", status) == 0
    assert _rows(store)[105]["status"] == cell


def test_outdated_is_not_a_status_a_cycle_may_set(store):
    """The split of labour is his: a cycle proposes it on an existing row and
    he deletes. Nothing he typed this week arrives already written off."""
    with pytest.raises(SystemExit):
        _run("--index", "0", "--priority", "high", "--status", "outdated")


# --- the capture half of the after-check --------------------------------


def _pairs(*items):
    return {"captures": [text for text, _ in items],
            "captureReplies": [list(replies) for _, replies in items]}


def test_check_catches_a_capture_lost_beside_the_one_boarded():
    before = _pairs(("one", []), ("two", []), ("three", []))
    after = _pairs(("two", []))
    assert check_captures(before, after, "one")


def test_check_catches_a_reply_taken_with_the_cut():
    before = _pairs(("one", []), ("two", ["an answer"]))
    after = _pairs(("two", []))
    problems = check_captures(before, after, "one")
    assert problems and "changed underneath" in problems[0]


def test_check_catches_the_wrong_capture_removed():
    before = _pairs(("one", []), ("two", []))
    after = _pairs(("one", []))
    problems = check_captures(before, after, "one")
    assert problems and "wrong capture" in problems[0]


def test_check_passes_the_honest_case():
    before = _pairs(("one", ["answered"]), ("two", []))
    after = _pairs(("two", []))
    assert check_captures(before, after, "one") == []
