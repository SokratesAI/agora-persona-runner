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
    DONE_WHEN_PREFIX,
    apply_done_when,
    capture_pairs,
    check_captures,
    first_sentence,
    known_names,
    main,
    promote,
    route,
    split_into_tasks,
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
    # `--no-milestone` is injected only when the caller named neither flag, so
    # a test about something else does not have to carry it and a test about
    # the milestone still says which one it means. The guard itself is tested
    # through `main` directly, never through here -- a helper that supplies
    # the flag cannot also prove the flag is required.
    named = "--milestone" in args or "--no-milestone" in args
    # Same shape for `--done-when` (issue #212) and the same reason: a test
    # about the project cell should not have to carry a definition of done,
    # and the requirement itself is proved through `main` directly below.
    stated = "--done-when" in args
    return main(["--board", "idea", "--dated", "09-10",
                 *([] if named else ["--no-milestone"]),
                 *([] if stated else ["--done-when", "the page loads in 1s"]),
                 *args])


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
                 "--priority", "high", "--no-milestone"]) == 1
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


# --- the milestone, which used to have no flag at all -------------------
#
# Every row this tool boarded landed under no milestone, because there was
# nothing to name one with. `project_goals_check` counted 24 of them on
# 2026-09-14 -- issue #227's task rule, found only because a check went
# looking. These four pin the flag that stops the inventory regrowing.


def test_it_refuses_when_neither_milestone_flag_is_named(store, capsys):
    """The whole point: boarding with no milestone has to be a choice."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-10",
                 "--priority", "high"]) == 1
    assert "--no-milestone" in capsys.readouterr().err
    assert store.calls == []


def test_it_refuses_both_milestone_flags_at_once(store, capsys):
    """Naming a milestone and saying none fits are opposite instructions, so
    honouring either one would be this tool guessing which he meant."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-10",
                 "--priority", "high", "--milestone", "Cost and quota",
                 "--no-milestone"]) == 1
    assert "--no-milestone" in capsys.readouterr().err
    assert store.calls == []


def test_the_named_milestone_reaches_the_cell(store):
    """A row boarded with a milestone is placed, not merely announced: the
    print line and the cell are different claims and only the cell is read by
    `project_goals_check`."""
    assert _run("--index", "0", "--priority", "high",
                "--milestone", "Cost and quota") == 0
    assert _rows(store)[105]["milestone"] == "Cost and quota"


def test_no_milestone_boards_the_row_ungrouped(store):
    """The escape hatch really does board the row -- a refusal wearing a flag
    would be worse than no flag."""
    assert _run("--index", "0", "--priority", "high", "--no-milestone") == 0
    assert _rows(store)[105]["milestone"] == ""


def test_a_pipe_in_the_milestone_is_refused_before_any_write(store, capsys):
    """A `|` ends a cell, so it would shift every column right of it."""
    assert _run("--index", "0", "--priority", "high",
                "--milestone", "Cost | quota") == 1
    assert "--milestone" in capsys.readouterr().err
    assert store.calls == []


def test_dry_run_with_a_milestone_writes_nothing(store, capsys):
    assert _run("--index", "0", "--priority", "high", "--dry-run",
                "--milestone", "Cost and quota") == 0
    assert "Cost and quota" in capsys.readouterr().out
    assert store.calls == []


def test_the_milestone_is_written_after_the_bullet_is_cut(store):
    """The pair above -- row written, bullet cut -- is what a re-run repairs,
    so a third write may not sit between them. Put it there and a refused
    regrouping leaves the bullet in the box, and the re-run boards the item a
    second time."""
    assert _run("--index", "0", "--priority", "high",
                "--milestone", "Cost and quota") == 0
    names = [name for name, _ in store.calls]
    last_row_write = max(i for i, name in enumerate(names) if name == "write_row")
    assert names.index("delete_capture") < last_row_write

# --- issue #212: a task carries a checkable definition of done ----------


def _promoted(status="backlog"):
    fields, refusal = promote("The journal page is slow.", "high", status,
                              "09-10")
    assert refusal is None
    return fields


def test_a_definition_of_done_is_appended_after_his_words():
    """His text is the write-up verbatim -- `add_row`'s rule -- so the
    sentence lands after it, where a `**Nova, MM-DD:**` reply lands."""
    fields, refusal = apply_done_when(_promoted(), "the page loads in 1s")
    assert refusal is None
    assert fields["write_up"] == (
        "The journal page is slow.\n\n"
        + DONE_WHEN_PREFIX + "the page loads in 1s"
    )


def test_boarding_without_a_definition_of_done_is_refused():
    fields, refusal = apply_done_when(_promoted(), None)
    assert fields is None
    assert "--done-when" in refusal


def test_a_blank_definition_of_done_is_refused_like_a_missing_one():
    """`--done-when ' '` would otherwise satisfy the flag and write a row
    whose definition of done is an empty line."""
    fields, refusal = apply_done_when(_promoted(), "   ")
    assert fields is None
    assert "--done-when" in refusal


def test_a_capture_that_arrived_finished_needs_no_definition_of_done():
    """Asking when a finished thing will be finished is ceremony. This is
    the resolved status, not the caller's: a `DONE (Cycle N)` bullet
    boarded with the default `--status backlog` is what `promote` turns
    into `done`, and that is the case this carve-out is for."""
    fields, refusal = promote("DONE (Cycle 900): The slow journal page.",
                              "high", "backlog", "09-10")
    assert fields["status"] == "done"
    fields, refusal = apply_done_when(fields, None)
    assert refusal is None
    assert fields["write_up"] == "The slow journal page."


def test_the_promoted_fields_are_not_mutated_in_place():
    """`main` prints from `fields` after this returns; a caller that still
    holds the pre-call dict must not see the line appear in it."""
    original = _promoted()
    fields, _ = apply_done_when(original, "the page loads in 1s")
    assert original["write_up"] == "The journal page is slow."
    assert fields is not original


def test_main_refuses_a_capture_with_no_definition_of_done(store, capsys):
    """Through `main` and not `_run`, which injects the flag -- a helper
    that supplies the flag cannot also prove the flag is required."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-10",
                 "--priority", "high", "--no-milestone"]) == 1
    assert "--done-when" in capsys.readouterr().err
    assert store.calls == []


def test_the_definition_of_done_reaches_the_row_he_reads(store):
    assert _run("--index", "0", "--priority", "high",
                "--done-when", "the landing page opens without the menu") == 0
    detail = _contents(store)["details"][105]
    assert DONE_WHEN_PREFIX + "the landing page opens without the menu" in detail


# --- issue #212: one capture becomes SEVERAL tasks ----------------------


def test_one_capture_becomes_one_row_when_no_task_is_named():
    """The single-row case is the list-of-one case, so `main` keeps one write
    path. Nothing about a capture nobody cut up may change."""
    rows, refusal = split_into_tasks(_promoted(), None, ["the page loads in 1s"])
    assert refusal is None
    assert len(rows) == 1
    assert rows[0]["title"] == "The journal page is slow."
    assert rows[0]["write_up"].endswith(f"{DONE_WHEN_PREFIX}the page loads in 1s")


def test_each_task_becomes_its_own_row_with_its_own_definition_of_done():
    """His ask is the plural: *"break each capture into tasks that each have a
    checkable definition of done."*"""
    rows, refusal = split_into_tasks(
        _promoted(),
        ["Profile the journal query", "Paginate the journal page"],
        ["a flame graph names the slow call", "the page ships 20 entries"],
    )
    assert refusal is None
    assert [one["title"] for one in rows] == [
        "Profile the journal query", "Paginate the journal page"]
    assert f"{DONE_WHEN_PREFIX}a flame graph names the slow call" in rows[0]["write_up"]
    assert f"{DONE_WHEN_PREFIX}the page ships 20 entries" in rows[1]["write_up"]


def test_every_task_row_carries_his_words_whole_and_says_which_task_it_is():
    """Slicing his paragraph between the rows would be this tool deciding
    which of his sentences belongs to which task, on a page he reads. So each
    row repeats it, and each row says the repetition is one capture cut up."""
    rows, _ = split_into_tasks(
        _promoted(), ["First", "Second"], ["one done", "two done"])
    for one in rows:
        assert one["write_up"].startswith("The journal page is slow.")
    assert "*Task 1 of 2, cut from one capture.*" in rows[0]["write_up"]
    assert "*Task 2 of 2, cut from one capture.*" in rows[1]["write_up"]


def test_a_task_with_no_definition_of_done_is_refused():
    rows, refusal = split_into_tasks(
        _promoted(), ["First", "Second"], ["one done"])
    assert rows is None
    assert "2 --task and 1 --done-when" in refusal


def test_more_definitions_of_done_than_tasks_is_refused():
    """Taking the shorter of the two would silently drop one of them."""
    rows, refusal = split_into_tasks(
        _promoted(), ["First"], ["one done", "two done"])
    assert rows is None
    assert "1 --task and 2 --done-when" in refusal


def test_a_second_definition_of_done_with_no_task_names_a_task_that_is_missing():
    rows, refusal = split_into_tasks(_promoted(), None, ["one done", "two done"])
    assert rows is None
    assert "--task" in refusal


def test_a_blank_task_title_is_refused():
    rows, refusal = split_into_tasks(_promoted(), ["First", "  "],
                                     ["one done", "two done"])
    assert rows is None
    assert "needs a title" in refusal


def test_a_blank_definition_of_done_beside_a_task_is_refused():
    rows, refusal = split_into_tasks(_promoted(), ["First", "Second"],
                                     ["one done", "   "])
    assert rows is None
    assert "--done-when" in refusal


def test_a_finished_capture_may_not_be_cut_into_tasks():
    """`promote` has already turned its DONE marker into the Done status, so
    three rows here would be three finished rows for work that shipped once."""
    rows, refusal = split_into_tasks(
        _promoted(status="done"), ["First", "Second"], ["one done", "two done"])
    assert rows is None
    assert "already finished" in refusal


def test_title_and_task_together_are_refused():
    """Both name the row's title and there is no order between them."""
    rows, refusal = split_into_tasks(
        _promoted(), ["First"], ["one done"], title="Something else")
    assert rows is None
    assert "--title" in refusal


def test_the_promoted_fields_survive_being_cut_into_tasks():
    fields = _promoted()
    rows, _ = split_into_tasks(fields, ["First", "Second"],
                               ["one done", "two done"])
    assert fields["title"] == "The journal page is slow."
    assert fields["write_up"] == "The journal page is slow."
    assert all(one["priority"] == fields["priority"] for one in rows)


def test_main_boards_two_rows_from_one_capture_and_cuts_the_bullet_once(store):
    """End to end: the rows really arrive on his board, and his one bullet
    leaves the box once rather than twice."""
    before = len(capture_pairs(_contents(store)))
    assert main(["--board", "idea", "--index", "1", "--dated", "09-14",
                 "--priority", "high", "--milestone", "Cost and quota",
                 "--task", "Profile the journal query",
                 "--done-when", "a flame graph names the slow call",
                 "--task", "Paginate the journal page",
                 "--done-when", "the page ships 20 entries"]) == 0
    rows = _rows(store)
    titles = {one["title"] for one in rows.values()}
    assert "Profile the journal query" in titles
    assert "Paginate the journal page" in titles
    assert len(capture_pairs(_contents(store))) == before - 1


def test_both_task_rows_land_under_the_named_milestone(store):
    """The second row is the one a loop over `rows[0]` would leave ungrouped,
    and an ungrouped row serves no key result (issue #227)."""
    assert main(["--board", "idea", "--index", "1", "--dated", "09-14",
                 "--priority", "high", "--milestone", "Cost and quota",
                 "--task", "First", "--done-when", "one done",
                 "--task", "Second", "--done-when", "two done"]) == 0
    placed = {one["title"]: one["milestone"] for one in _rows(store).values()}
    assert placed["First"] == "Cost and quota"
    assert placed["Second"] == "Cost and quota"


def test_his_replies_go_on_the_first_task_row_only(store):
    """One conversation about one capture. Copied onto every task it would be
    the same answer on his board three times."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--no-milestone",
                 "--task", "First", "--done-when", "one done",
                 "--task", "Second", "--done-when", "two done"]) == 0
    by_title = {one["title"]: one["number"] for one in _rows(store).values()}
    details = _contents(store)["details"]
    assert "Nova, cycle 900" in details[by_title["First"]]
    assert "Nova, cycle 900" not in details[by_title["Second"]]


def test_a_pipe_in_a_task_title_is_refused_before_any_write(store, capsys):
    """A `|` ends a cell, the same as it does in `--title`."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--no-milestone",
                 "--task", "First | second", "--done-when", "one done"]) == 1
    assert "--task" in capsys.readouterr().err
    assert store.calls == []


def test_main_refuses_unpaired_tasks_before_any_write(store, capsys):
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--no-milestone",
                 "--task", "First", "--task", "Second",
                 "--done-when", "one done"]) == 1
    assert "--done-when" in capsys.readouterr().err
    assert [name for name, _ in store.calls if name.startswith("write")] == []


# --- issue #212's classify step -----------------------------------------
#
# Five tiers, and only two of them are a board row. The pure half is `route`,
# which answers "what does --as <kind> do to this call" without a store, so
# the refusals below cost nothing and can be asserted on directly.


def test_route_defaults_to_a_task_at_backlog():
    """The default has to mean exactly what every existing caller meant, or
    shipping the classify step re-boards yesterday's rows differently."""
    assert route("task", [], "Cost and quota", None) == ("backlog", None)


def test_route_keeps_an_explicit_status_on_a_task():
    assert route("task", [], None, "done") == ("done", None)


def test_a_project_is_not_a_board_row():
    status, why = route("project", [], None, None)
    assert status is None
    assert "not a board row" in why
    assert "project note" in why


def test_a_goal_is_not_a_board_row_and_says_it_is_his():
    status, why = route("goal", [], None, None)
    assert status is None
    assert "project-goals.md" in why


def test_a_question_boards_at_blocked_on_edvard():
    """Issue #212: the capture waits in a needs-him state and does not block
    the queue, which is the status the picker already ranks out."""
    assert route("question", [], None, None) == ("blocked-on-edvard", None)


def test_a_question_refuses_a_contradicting_status():
    status, why = route("question", [], None, "in-progress")
    assert status is None
    assert "--status in-progress" in why


def test_a_question_carrying_tasks_is_refused():
    """If it can be cut into tasks it was never a question."""
    status, why = route("question", ["First"], None, None)
    assert status is None
    assert "--task" in why


def test_a_milestone_needs_more_than_one_task():
    status, why = route("milestone", ["Only one"], "Cost and quota", None)
    assert status is None
    assert "several tasks" in why


def test_a_milestone_refuses_no_milestone():
    status, why = route("milestone", ["First", "Second"], None, None)
    assert status is None
    assert "--no-milestone" in why


def test_a_milestone_with_two_tasks_and_a_name_is_a_backlog_row():
    assert route(
        "milestone", ["First", "Second"], "Cost and quota", None,
    ) == ("backlog", None)


def test_a_question_waives_the_definition_of_done():
    """`waived` is keyed on the classification, not on the status: --as
    question is a statement that nobody can write one."""
    fields = {"status": "backlog", "write_up": "his words"}
    kept, why = apply_done_when(fields, "", waived=True)
    assert why is None
    assert kept["write_up"] == "his words"
    assert DONE_WHEN_PREFIX not in kept["write_up"]


def test_blocked_on_edvard_alone_still_needs_a_definition_of_done():
    """The separating case: the same status reached by --status rather than
    by --as question is an operator's guess, and still owes the sentence."""
    fields = {"status": "blocked-on-edvard", "write_up": "his words"}
    assert apply_done_when(fields, "")[0] is None


def test_main_refuses_a_project_before_reading_the_store(store, capsys):
    """It is not a row, so it never asks which milestone the row sits under
    -- and his bullet stays in the box, because nothing was built for it."""
    before = len(capture_pairs(_contents(store)))
    store.calls.clear()
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--as", "project"]) == 1
    assert "project note" in capsys.readouterr().err
    assert store.calls == []
    assert len(capture_pairs(_contents(store))) == before


def test_main_boards_a_question_with_no_done_when(store, capsys):
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--no-milestone",
                 "--as", "question"]) == 0
    from agora_runner.nova_boards import STATUS_LABELS
    boarded = [one for one in _rows(store).values()
               if one["status"] == STATUS_LABELS["blocked-on-edvard"]]
    assert len(boarded) == 1
    assert DONE_WHEN_PREFIX not in _contents(store)["details"][
        boarded[0]["number"]]
    assert "needs_input" in capsys.readouterr().out


def test_main_still_demands_a_done_when_for_an_unclassified_capture(
        store, capsys):
    """The default did not change: a row is a task and a task needs one."""
    assert main(["--board", "idea", "--index", "0", "--dated", "09-14",
                 "--priority", "high", "--no-milestone"]) == 1
    assert "--done-when" in capsys.readouterr().err


def test_a_waived_question_keeps_a_done_when_that_was_given_anyway():
    """The waiver drops the requirement, not the sentence. Swallowing an
    argument the caller typed is the silence this module is built against."""
    fields = {"status": "backlog", "write_up": "his words"}
    kept, why = apply_done_when(fields, "the thread has an answer", waived=True)
    assert why is None
    assert kept["write_up"].endswith(
        DONE_WHEN_PREFIX + "the thread has an answer")
