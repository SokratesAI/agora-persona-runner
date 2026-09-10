"""`board_write.change_row` -- the after-check the nine writers each own a copy
of today, against the record store instead of against a second parse.

Every test here goes through a fake store that routes on the document's own
`_id` prefix (`test_board_records.FakeStore`), because that is the only kind of
fake that can say *"that id is not in the range you asked for"* -- the property
whose absence kept a missing `read_captures` call green for a whole cycle.

The four tests that matter are the four damage cases, and each of them damages
the board **inside the store**, not by handing `change_row` a bad argument. A
guard tested by breaking its input is a guard tested against the caller; this
one exists to catch a store that answered wrong.
"""

import pytest

from agora_runner import board_records, board_store, board_write, nova_boards
from tests.test_board_records import WritableFakeStore, item_numbered, writable


# The status a row's document actually derives `statusKey` from, so a change
# set naming one and not the other is incomplete rather than partial. See
# `test_a_derived_key_left_out_of_the_change_set_is_caught` below.
_OPEN = {"status": "\u26aa Backlog", "statusKey": "backlog"}

# Row 42 is already `⚪ Backlog`, so the damage tests that name it move it the
# other way -- a change set that changes nothing cannot show a damaged write.
_OPEN_FROM_BACKLOG = {"status": "\U0001f7e1 In progress",
                      "statusKey": "in-progress"}


def test_one_cell_changes_and_the_rest_of_the_board_is_identical():
    """The anchor. One key moves; everything `parse_board` models is equal."""
    parsed, store = writable()
    before, after = board_write.change_row(
        "issue", 42, {"status": "🟡 In progress", "statusKey": "in-progress"},
        store=store)
    assert before == item_numbered(parsed, 42)
    assert after == dict(before, status="🟡 In progress", statusKey="in-progress")
    now = board_records.contents("issue", store=store)
    assert now["captures"] == parsed["captures"]
    assert now["captureReplies"] == parsed["captureReplies"]
    assert now["details"] == parsed["details"]
    assert [item["number"] for item in now["items"]] == [
        item["number"] for item in parsed["items"]]
    for was, is_now in zip(parsed["items"], now["items"]):
        if was["number"] != 42:
            assert was == is_now


def test_a_row_that_is_not_on_the_board_is_refused_with_nothing_written():
    """The refusal has to come *before* the write, or `store_item` mints a row
    he never asked for and `contents` then reads it as part of his board."""
    _parsed, store = writable()
    with pytest.raises(board_write.WriteRefused) as refused:
        board_write.change_row("issue", 9999, _OPEN_FROM_BACKLOG, store=store)
    assert "9999" in str(refused.value)
    assert store.calls == []


def test_a_key_that_is_not_on_a_row_is_refused_with_nothing_written():
    """A misspelled key would be written into the document and read back by
    nothing, so the cell he asked to change would silently not change."""
    _parsed, store = writable()
    with pytest.raises(board_write.WriteRefused) as refused:
        board_write.change_row("issue", 42, {"statuss": "backlog"}, store=store)
    assert "statuss" in str(refused.value)
    assert store.calls == []


class _NudgesASibling(WritableFakeStore):
    """A store whose row write also alters a row nobody named.

    This is the failure the nine hand-written after-checks exist for, and it
    is invisible to a check that only looks at the row it changed.
    """

    def write_row(self, doc):
        stored = super().write_row(doc)
        self.docs = [
            dict(held, title=held["title"] + " (nudged)")
            if held.get("_id", "").startswith("board:issue:")
            and held.get("number") != doc.get("number")
            else held
            for held in self.docs]
        return stored


def test_a_write_that_alters_another_row_is_caught():
    parsed, source = writable()
    store = _NudgesASibling(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    other = next(item["number"] for item in parsed["items"] if item["number"] != 42)
    assert f"#{other}" in str(damaged.value)
    assert "title" in str(damaged.value)


class _DropsACapture(WritableFakeStore):
    def write_row(self, doc):
        stored = super().write_row(doc)
        self.docs = [held for held in self.docs
                     if not held.get("_id", "").startswith("capture:")]
        return stored


def test_a_write_that_drops_his_capture_bullets_is_caught():
    """`write_rows`' prune is what makes this reachable rather than theoretical:
    the captures live in their own key range precisely so a row write cannot
    tombstone them, and this is the check that says so out loud.

    **The assertion names the bullets specifically, and that is the whole
    difference between this test and a broken one.** It read `"capture" in
    ...` first, and killing the bullet check left it green -- because losing
    the bullets loses their replies too, and the *replies* message also
    contains the word "capture". Two checks, one word, and either one could
    have been deleted with this file still green.
    """
    parsed, source = writable()
    assert parsed["captures"], "the fixture must hold a capture for this to test"
    store = _DropsACapture(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert "the capture bullets changed" in str(damaged.value)


class _EmptiesTheReplies(WritableFakeStore):
    """His bullets survive; the answers underneath them do not.

    A capture reply is where a cycle's answer to him lives, so it is the one
    thing on that side of the board that can be lost without the page looking
    any different.
    """

    def write_row(self, doc):
        stored = super().write_row(doc)
        self.docs = [
            dict(held, replies=[]) if held.get("_id", "").startswith("capture:")
            else held
            for held in self.docs]
        return stored


def test_a_write_that_empties_the_replies_under_his_captures_is_caught():
    parsed, source = writable()
    assert any(parsed["captureReplies"]), "the fixture must hold a reply"
    store = _EmptiesTheReplies(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert "the replies under his capture bullets changed" in str(damaged.value)
    assert "the capture bullets changed" not in str(damaged.value)


class _DropsTheRank(WritableFakeStore):
    def write_row(self, doc):
        return super().write_row({key: value for key, value in doc.items()
                                  if key != "rank"})


def test_a_write_that_loses_the_row_rank_is_caught_as_a_reorder():
    """`board_store.in_order` puts an unranked document after every ranked one,
    so losing the rank sends the row to the bottom of his board with no error --
    `capture_plan`'s bug in the row range."""
    parsed, source = writable()
    assert [item["number"] for item in parsed["items"]][-1] != 42, (
        "the row under test must not already be last, or a reorder is invisible")
    store = _DropsTheRank(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert "row order" in str(damaged.value)


class _EatsTheWriteUp(WritableFakeStore):
    def write_row(self, doc):
        return super().write_row({key: value for key, value in doc.items()
                                  if key != "detail"})


def test_a_write_that_eats_his_write_up_is_caught():
    parsed, source = writable()
    assert parsed["details"], "the fixture must hold a write-up"
    numbered = sorted(parsed["details"])[0]
    store = _EatsTheWriteUp(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", numbered, _OPEN, store=store)
    assert f"#{numbered}" in str(damaged.value)


def test_a_passed_write_up_replaces_that_write_up_and_no_other():
    parsed, store = writable()
    numbered = sorted(parsed["details"])[0]
    board_write.change_row(
        "issue", numbered, _OPEN, detail="New prose.", store=store)
    now = board_records.contents("issue", store=store)
    assert now["details"][numbered] == "New prose."
    assert {key: value for key, value in now["details"].items()
            if key != numbered} == {
                key: value for key, value in parsed["details"].items()
                if key != numbered}


def test_an_empty_write_up_removes_it_and_is_not_the_same_as_none():
    """The contrast is inside one test on purpose: `detail=""` and
    `detail=None` are one keyword apart and mean opposite things, so a test
    that only exercises one of them leaves the collapse of the two green."""
    parsed, store = writable()
    numbered = sorted(parsed["details"])[0]
    board_write.change_row("issue", numbered, _OPEN, detail=None, store=store)
    assert board_records.contents("issue", store=store)["details"][numbered] == (
        parsed["details"][numbered])
    board_write.change_row(
        "issue", numbered, _OPEN_FROM_BACKLOG, detail="", store=store)
    assert numbered not in board_records.contents("issue", store=store)["details"]


def test_a_change_set_that_changes_nothing_is_allowed():
    """He can ask twice for the rating a row already has, and the tools each
    have their own vocabulary for refusing what does not make sense."""
    parsed, store = writable()
    held = item_numbered(parsed, 42)
    before, after = board_write.change_row(
        "issue", 42, {"status": held["status"]}, store=store)
    assert before == after == held


def test_an_unmigrated_store_raises_before_anything_is_written():
    """`contents` owns this refusal; the point of the test is that the *first*
    read is the one that hits it, so no row is minted into an empty store."""
    _parsed, store = writable()
    store.registry = {key: value for key, value in store.registry.items()
                      if key != "_rev"}
    with pytest.raises(board_records.UnmigratedStore):
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert store.calls == []


def test_a_derived_key_left_out_of_the_change_set_is_caught():
    """`{"status": ...}` alone is not a change set.

    `board_document.to_document` stores the status cell and `from_document`
    derives `statusKey` back off it, so a writer that names one and not the
    other has asked for a row that cannot exist -- and today each of the nine
    writers passes both by hand, with nothing checking that it did. This is
    the check, and I found it by writing a test that meant to be about
    something else.
    """
    parsed, store = writable()
    # 41 is `🟡 In progress` in the fixture, so this really does move the cell
    # -- asking 42 for the status it already has would test nothing.
    assert item_numbered(parsed, 41)["statusKey"] == "in-progress"
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 41, {"status": "\u26aa Backlog"},
                               store=store)
    assert "statusKey" in str(damaged.value)


def test_the_default_store_answers_every_call_this_module_reaches_through():
    """`store=board_store` is the production path and no test above touches it.

    Every test in this file injects a fake, which is what makes them fast and
    what makes them blind to a rename on the other side: `change_row` reaches
    `read_registry`, `read_rows`, `read_captures`, `read_row`, `write_row` and
    `write_registry` -- three of those through `board_records` -- and a fake
    that grew the same six names by hand would agree with a module that no
    longer has them.

    It asserts attributes rather than calling them, because calling them needs
    a CouchDB. That is a narrower claim than "the production path works" and it
    is the one this can honestly make: the names still line up.
    """
    for name in ("read_registry", "read_rows", "read_captures",
                 "read_row", "write_row", "write_registry"):
        assert callable(getattr(board_store, name, None)), (
            f"agora_runner.board_store has no callable {name}, which "
            "board_write.change_row reaches through its default store")


class _MovesTheRowUnderneath(WritableFakeStore):
    """A store where the row changes between the board read and the write.

    This is not a fake being awkward: it is the second cycle. The nine writers
    it stands in for run from a twenty-minute heartbeat with three of me alive,
    and two of them changing different cells of one row is an ordinary evening.
    """

    def __init__(self, docs, registry, number):
        super().__init__(docs, registry)
        self.number = number
        self.reads = 0

    def read_row(self, board, number):
        held = super().read_row(board, number)
        self.reads += 1
        # The first read is `change_row`'s snapshot; before the second one --
        # which is the compare-and-swap -- somebody else lands a write.
        if self.reads == 1 and held is not None and number == self.number:
            self.docs = [dict(doc, title="Changed by another cycle",
                              _rev="9-elsewhere")
                         if doc.get("_id") == held.get("_id") else doc
                         for doc in self.docs]
        return held


def test_a_row_that_moved_since_the_board_was_read_is_refused_not_clobbered():
    """The failure this cannot be allowed to report as success.

    `wanted` is built from the board read at the top of `change_row`, and the
    after-check compares what landed against `wanted` -- so a write that puts
    another cycle's change back would be compared against the stale copy it
    just wrote and agree with itself. The revision check is the only thing
    between that and a silent lost update.
    """
    _parsed, source = writable()
    store = _MovesTheRowUnderneath(source.docs, source.registry, 42)
    with pytest.raises(board_write.WriteRefused) as refused:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert "changed between reading the board and writing it" in str(refused.value)
    assert [call for call in store.calls if call[0] == "write_row"] == []
    # And the other cycle's change is still there, which is the whole point.
    assert next(item for item in
                board_records.contents("issue", store=store)["items"]
                if item["number"] == 42)["title"] == "Changed by another cycle"


def test_a_row_that_did_not_move_is_not_refused_by_the_revision_check():
    """The negative half. Without this the test above passes on a `change_row`
    that refuses every write, which is a guard that guards by doing nothing."""
    _parsed, store = writable()
    board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    assert [call for call in store.calls if call[0] == "write_row"]


class _RewritesACaptureBullet(WritableFakeStore):
    """The bullet count is unchanged and its text is not -- the one capture
    failure the count in the error message cannot describe."""

    def write_row(self, doc):
        stored = super().write_row(doc)
        self.docs = [
            dict(held, text="Rewritten behind his back")
            if held.get("_id", "").startswith("capture:") else held
            for held in self.docs]
        return stored


def test_a_rewritten_capture_bullet_is_reported_as_text_not_as_a_count():
    parsed, source = writable()
    store = _RewritesACaptureBullet(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_row("issue", 42, _OPEN_FROM_BACKLOG, store=store)
    message = str(damaged.value)
    assert "the text of at least one is different" in message
    assert f"still {len(parsed['captures'])} of them" in message


# --- append_note -------------------------------------------------------------
#
# The records half of `nova_boards.append_detail_note`. Every test below asserts
# against `board_records.contents`, never against a rendered board, and the two
# that matter most are the round-trip through `nova_boards`' own note reader
# (a line only I can read is not a note) and the single-write one (a status and
# its reason are one thing he asked for, so they are one write).

_NOTE_ROW = 41  # the fixture's only row with a write-up to append to
_NO_WRITE_UP = 42


def _details(store, board="issue"):
    return board_records.contents(board, store=store)["details"]


def _writes(store):
    """The store calls that actually wrote a document."""
    return [call for call in store.calls if "write" in call[0] or "store" in call[0]]


def test_a_note_lands_at_the_end_of_the_write_up_and_stamps_updated():
    """The anchor: his prose is kept, the line is added under it, and the cell
    that ranks the row moves to the note's own date."""
    parsed, store = writable()
    body_before = parsed["details"][_NOTE_ROW]
    before, after = board_write.append_note(
        "issue", _NOTE_ROW, "closed by the switchover", "09-10", cycle=1330,
        store=store)

    assert before == item_numbered(parsed, _NOTE_ROW)
    assert after["updated"] == "09-10"
    body = _details(store)[_NOTE_ROW]
    assert body.startswith(body_before), "his own write-up is untouched"
    assert body == body_before + "\n\n**Nova, 09-10 (Cycle 1330):** closed by the switchover"


def test_a_note_with_no_cycle_leaves_the_cycle_marker_off():
    parsed, store = writable()
    board_write.append_note("issue", _NOTE_ROW, "no cycle", "09-10", store=store)
    assert _details(store)[_NOTE_ROW].endswith("**Nova, 09-10:** no cycle")


def test_the_second_note_lands_directly_under_the_first():
    """The walk-back. Without it every note drifts one blank line further from
    the write-up than the one before it."""
    _parsed, store = writable()
    board_write.append_note("issue", _NOTE_ROW, "one", "09-10", store=store)
    board_write.append_note("issue", _NOTE_ROW, "two", "09-11", store=store)

    tail = _details(store)[_NOTE_ROW].split("\n")[-3:]
    assert tail == ["**Nova, 09-10:** one", "", "**Nova, 09-11:** two"]


def test_the_line_written_is_the_line_his_board_page_reads_back():
    """The round-trip that matters. `unanswered_comment_bodies_from_details` is
    what puts `UNANSWERED` on a row of his board, and it is regex over the note
    line -- so a note this module writes in a shape that reader does not match
    is invisible on his phone while every test here still passes."""
    _parsed, store = writable()
    board_write.append_note(
        "issue", _NOTE_ROW, "why is this still open?", "09-10", author="edvard",
        store=store)
    waiting = nova_boards.unanswered_comment_bodies_from_details(_details(store))
    assert _NOTE_ROW in waiting
    assert waiting[_NOTE_ROW].endswith("why is this still open?")

    # And the negative half, or this passes against a reader that flags every
    # row: my answer under his question clears it.
    board_write.append_note("issue", _NOTE_ROW, "answered", "09-10", store=store)
    assert nova_boards.unanswered_comment_bodies_from_details(_details(store)) == {}


def test_a_note_rides_with_the_change_set_as_one_write():
    """`--status done --note 'what closed it'` is one thing he asked for. Two
    writes would leave a window where his board says a row closed and nothing
    on it says why."""
    _parsed, store = writable()
    _before, after = board_write.append_note(
        "issue", _NOTE_ROW, "shipped", "09-10",
        changes={"status": "✅ Done", "statusKey": "done"}, store=store)

    assert after["status"] == "✅ Done"
    assert after["statusKey"] == "done"
    assert after["updated"] == "09-10"
    assert _details(store)[_NOTE_ROW].endswith("**Nova, 09-10:** shipped")
    assert len(_writes(store)) == 1, _writes(store)


def test_a_change_set_naming_updated_is_refused_with_nothing_written():
    """Two dates for one write is a bug in the caller, and picking one hides
    it."""
    _parsed, store = writable()
    with pytest.raises(board_write.NoteRefused) as refused:
        board_write.append_note(
            "issue", _NOTE_ROW, "shipped", "09-10",
            changes={"updated": "01-01"}, store=store)
    assert "updated" in str(refused.value)
    assert _writes(store) == []


@pytest.mark.parametrize("note", ["two\nlines", "carriage\rreturn", "", "   "])
def test_a_note_that_is_not_one_line_is_refused_with_nothing_written(note):
    """A newline can no longer truncate his file the way it could in markdown --
    but `_COMMENT_NOTE_RE` is still `re.MULTILINE`, so a two-line note reads
    back as one note plus an orphaned sentence with nobody's name on it."""
    _parsed, store = writable()
    with pytest.raises(board_write.NoteRefused):
        board_write.append_note("issue", _NOTE_ROW, note, "09-10", store=store)
    assert _writes(store) == []


@pytest.mark.parametrize("dated", ["09|10", "", "09\n10"])
def test_a_date_that_cannot_be_a_cell_is_refused_with_nothing_written(dated):
    """`dated` is also the row's `updated` cell now, and `board_view.render_row`
    raises on a pipe rather than escaping it."""
    _parsed, store = writable()
    with pytest.raises(board_write.NoteRefused):
        board_write.append_note("issue", _NOTE_ROW, "fine", dated, store=store)
    assert _writes(store) == []


def test_an_unknown_author_is_refused_rather_than_signed_with_my_name():
    """The name lands inside `**...**` in his own file. Attributing his sentence
    to me is the one outcome this argument exists to prevent, so an unset
    payload field must not fall back to `Nova`."""
    _parsed, store = writable()
    for author in ["", "  ", "sokrates"]:
        with pytest.raises(board_write.NoteRefused):
            board_write.append_note(
                "issue", _NOTE_ROW, "hello", "09-10", author=author, store=store)
    assert _writes(store) == []


def test_a_row_with_no_write_up_is_refused_with_nothing_written():
    """Parity with `append_detail_note`, which returned `None` here. Against
    records a note would be a perfectly good first write-up -- lifting this is a
    real improvement and deliberately not part of the conversion."""
    parsed, store = writable()
    assert _NO_WRITE_UP not in parsed["details"], "the fixture's row without one"
    with pytest.raises(board_write.NoteRefused) as refused:
        board_write.append_note("issue", _NO_WRITE_UP, "hello", "09-10", store=store)
    assert "write-up" in str(refused.value)
    assert _writes(store) == []


def test_a_row_that_is_not_on_the_board_is_refused_by_the_change_it_makes():
    """`append_note` reads the details before `change_row` reads the rows, so a
    bad number has to survive that far to be refused for the right reason."""
    _parsed, store = writable()
    with pytest.raises(board_write.WriteRefused) as refused:
        board_write.append_note("issue", 9999, "hello", "09-10", store=store)
    assert "9999" in str(refused.value)
    assert _writes(store) == []


def test_a_note_refusal_is_catchable_as_a_write_refusal():
    """The four converting tools each print their own sentence about a bad
    `--note`, and none of them should need a second `except` to promise that
    nothing was written."""
    assert issubclass(board_write.NoteRefused, board_write.WriteRefused)


def test_a_write_up_ending_in_blank_lines_still_gets_the_note_directly_under_it():
    """The walk-back, tested where it can actually fire.

    `test_the_second_note_lands_directly_under_the_first` does not reach it:
    the first `append_note` leaves no trailing blank line, so the second one
    walks back over nothing and the loop could be deleted with that test still
    green. `contents` hands a stored body back verbatim -- it does not strip --
    so any caller that wrote one ending in blank lines is the case, and without
    the walk-back the note drifts one line further from his prose each time.
    """
    parsed, store = writable()
    board_records.store_item(
        "issue", item_numbered(parsed, _NOTE_ROW), detail="prose.\n\n\n",
        store=store)
    assert _details(store)[_NOTE_ROW] == "prose.\n\n\n", "the store keeps it"

    board_write.append_note("issue", _NOTE_ROW, "under it", "09-10", store=store)
    assert _details(store)[_NOTE_ROW] == "prose.\n\n**Nova, 09-10:** under it"


# --- add_row: the door the next three writers are blocked on ----------------


def test_add_row_lands_at_the_top_of_the_board_like_the_markdown_did():
    """Newest first. An unranked row would sort to the bottom instead."""
    parsed, store = writable()
    before = [row["number"] for row
              in board_records.contents("issue", store=store)["items"]]

    landed = board_write.add_row(
        "issue", "A brand new thing", "09-10", "high", store=store)

    after = board_records.contents("issue", store=store)
    assert [row["number"] for row in after["items"]] == [landed["number"]] + before
    assert landed["title"] == "A brand new thing"
    assert landed["priority"] == "🟠 High"
    assert landed["status"] == "⚪ Backlog"


def test_add_row_mints_the_highest_number_plus_one_never_a_gap():
    """A closed row's number is still spoken for by everything that cited it."""
    parsed, store = writable()
    highest = max(row["number"] for row in parsed["items"])

    first = board_write.add_row("issue", "One", "09-10", "low", store=store)
    second = board_write.add_row("issue", "Two", "09-10", "low", store=store)

    assert first["number"] == highest + 1
    assert second["number"] == highest + 2


def test_add_row_carries_the_write_up_and_the_replies_as_dated_notes():
    """The thread under his capture has to survive the promotion."""
    _, store = writable()
    landed = board_write.add_row(
        "issue", "Boarded", "09-10", "medium",
        write_up="He wrote this, verbatim.",
        notes=["I answered it once.", "And again."],
        cycle=1336, store=store)

    body = board_records.contents("issue", store=store)["details"][landed["number"]]
    assert body.splitlines()[0] == "He wrote this, verbatim."
    assert "**Nova, 09-10 (Cycle 1336):** I answered it once." in body
    assert "**Nova, 09-10 (Cycle 1336):** And again." in body


def test_add_row_refuses_a_reply_it_cannot_render_rather_than_dropping_it():
    """A note lost in silence is the thread this argument exists to keep."""
    _, store = writable()
    with pytest.raises(board_write.RowRefused) as refusal:
        board_write.add_row(
            "issue", "Boarded", "09-10", "medium",
            notes=["one line", "two\nlines"], store=store)
    assert "two" in str(refusal.value)
    assert board_records.contents("issue", store=store)["items"] == \
        board_records.contents("issue", store=store)["items"]


def test_add_row_writes_nothing_when_a_reply_is_refused():
    """The refusal is before the write, not halfway through it."""
    parsed, store = writable()
    before = board_records.contents("issue", store=store)
    with pytest.raises(board_write.RowRefused):
        board_write.add_row("issue", "Boarded", "09-10", "medium",
                            notes=["\r"], store=store)
    assert board_records.contents("issue", store=store) == before


def test_add_row_refuses_a_title_that_would_escape_its_own_cell():
    _, store = writable()
    for bad in ("a | b", "a\nb", "a\rb", "   "):
        with pytest.raises(board_write.RowRefused):
            board_write.add_row("issue", bad, "09-10", "high", store=store)


def test_add_row_refuses_a_rating_that_is_not_one():
    _, store = writable()
    with pytest.raises(board_write.RowRefused) as refusal:
        board_write.add_row("issue", "Boarded", "09-10", "banana", store=store)
    assert "not a rating" in str(refusal.value)


def test_add_row_refuses_a_status_that_is_not_one():
    _, store = writable()
    with pytest.raises(board_write.RowRefused) as refusal:
        board_write.add_row("issue", "Boarded", "09-10", "high",
                            status="shipped", store=store)
    assert "not a status" in str(refusal.value)


def test_add_row_boards_a_done_capture_as_done_on_both_halves():
    """`done` says which table and `status` says what the cell reads."""
    _, store = writable()
    landed = board_write.add_row(
        "issue", "Already shipped", "09-10", "high", status="done", store=store)
    assert landed["done"] is True
    assert landed["status"] == "✅ Done"
    assert landed["statusKey"] == "done"


def test_add_row_files_the_row_under_a_project_the_registry_now_holds():
    """A project named here and minted nowhere breaks the read of the board."""
    _, store = writable()
    landed = board_write.add_row(
        "issue", "Filed", "09-10", "high", project="Marcus", store=store)
    assert landed["project"] == "Marcus"
    # The join is the registry's, so the whole board has to still read.
    assert board_records.contents("issue", store=store)["items"][0] == landed


def test_add_row_leaves_every_capture_and_every_other_row_alone():
    """The after-check's subject: nothing but the new row moved."""
    parsed, store = writable()
    landed = board_write.add_row("issue", "New", "09-10", "high", store=store)
    after = board_records.contents("issue", store=store)
    assert after["captures"] == parsed["captures"]
    assert after["captureReplies"] == parsed["captureReplies"]
    assert [row for row in after["items"] if row["number"] != landed["number"]] \
        == parsed["items"]
    assert after["details"] == parsed["details"]


def test_add_row_raises_board_damaged_when_the_store_drops_the_rank():
    """A store that loses the position sends the new row to the bottom."""
    parsed, store = writable()
    damaged = _DropsTheRank(store.docs, store.registry)
    with pytest.raises(board_write.BoardDamaged):
        board_write.add_row("issue", "New", "09-10", "high", store=damaged)


def test_add_row_catches_a_write_that_also_nudged_a_row_nobody_named():
    """The per-row comparison has to skip past the new row, not zip through it.

    With the new row left in place the pairs are off by one, every pair
    disagrees on `number`, and the loop's own `continue` then compares nothing
    at all -- so the check reads clean while a sibling row was rewritten. Every
    other test here has one added row and no damaged sibling, which is exactly
    the shape that cannot see it.
    """
    parsed, source = writable()
    store = _NudgesASibling(source.docs, source.registry)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.add_row("issue", "New", "09-10", "high", store=store)
    assert "title" in str(damaged.value)


# ---------------------------------------------------------------------------
# change_capture_text -- the door `tools.close_done_captures` is blocked on
# ---------------------------------------------------------------------------
#
# Same shape as the `change_row` tests above and for the same reason: every
# damage case breaks the board *inside the store*, never by handing the
# function a bad argument, because this guard exists to catch a store that
# answered wrong rather than a caller that typed wrong.


def _first_capture(store, board="issue"):
    return board_records.capture_documents(board, store=store)[0]


def test_one_capture_is_rewritten_and_the_rest_of_the_board_is_identical():
    """The anchor. His bullet gains a DONE prefix; nothing else moves."""
    parsed, store = writable()
    doc = _first_capture(store)
    was = parsed["captures"][0]
    old, new = board_write.change_capture_text(
        "issue", doc, f"DONE (Cycle 1340): {was}", store=store)

    assert old == was
    assert new == f"DONE (Cycle 1340): {was}"
    now = board_records.contents("issue", store=store)
    assert now["captures"] == [new] + parsed["captures"][1:]
    assert now["captureReplies"] == parsed["captureReplies"]
    assert now["items"] == parsed["items"]
    assert now["details"] == parsed["details"]


def test_the_replies_under_his_bullet_are_carried_across_untouched():
    """A caller cannot drop an answer by omission -- the signature has no
    place to pass one, and this proves the write does not lose them."""
    parsed, store = writable()
    doc = _first_capture(store)
    assert board_records.contents("issue", store=store)["captureReplies"], \
        "the fixture must hold at least one reply, or this passes vacuously"
    board_write.change_capture_text("issue", doc, "rewritten", store=store)
    assert board_records.contents("issue", store=store)["captureReplies"] == \
        parsed["captureReplies"]


def test_a_document_with_no_revision_is_refused_with_nothing_written():
    """`_rev` is the only thing between a rewrite and clobbering a reply that
    landed while the caller was deciding to mark the bullet."""
    _parsed, store = writable()
    doc = dict(_first_capture(store))
    doc.pop("_rev")
    with pytest.raises(board_write.CaptureRefused) as refused:
        board_write.change_capture_text("issue", doc, "rewritten", store=store)
    assert "revision" in str(refused.value)
    assert store.calls == []


def test_a_capture_from_another_board_is_refused_with_nothing_written():
    """The board argument decides which board the after-check reads, so a
    mismatch would check the wrong board and find it undamaged."""
    _parsed, store = writable()
    doc = _first_capture(store)
    with pytest.raises(board_write.CaptureRefused) as refused:
        board_write.change_capture_text("idea", doc, "rewritten", store=store)
    assert "issue" in str(refused.value)
    assert store.calls == []


def test_blank_words_are_refused_with_nothing_written():
    """A capture with no words is a capture deleted, and deleting one has its
    own door with its own rules."""
    _parsed, store = writable()
    doc = _first_capture(store)
    with pytest.raises(board_write.CaptureRefused):
        board_write.change_capture_text("issue", doc, "   ", store=store)
    assert store.calls == []


def test_rewriting_a_capture_to_what_it_already_says_is_refused():
    """Zero positions move, so the after-check could not tell that from a
    write that landed nowhere at all."""
    parsed, store = writable()
    doc = _first_capture(store)
    with pytest.raises(board_write.CaptureRefused):
        board_write.change_capture_text(
            "issue", doc, parsed["captures"][0], store=store)
    assert store.calls == []


class _RewritesTheWrongBullet(WritableFakeStore):
    """A store whose capture write lands on the *next* bullet instead.

    The failure the position check exists for: the count is right, one
    position moved, and it is the wrong one.
    """

    def write_capture(self, doc):
        ordered = board_records.capture_documents(doc["board"], store=self)
        victim = ordered[1]
        return super().write_capture(
            dict(victim, text=doc.get("text", "")))


def test_a_write_that_landed_on_another_bullet_is_caught():
    _parsed, store_ok = writable()
    store = _RewritesTheWrongBullet(store_ok.docs, store_ok.registry)
    doc = _first_capture(store)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_capture_text("issue", doc, "rewritten", store=store)
    assert "not the one that was written" in str(damaged.value)


class _AlsoDropsAReply(WritableFakeStore):
    """A store whose capture write also drops the replies under it."""

    def write_capture(self, doc):
        return super().write_capture(dict(doc, replies=[]))


def test_a_write_that_ate_the_replies_is_caught():
    _parsed, store_ok = writable()
    store = _AlsoDropsAReply(store_ok.docs, store_ok.registry)
    doc = next(held for held in board_records.capture_documents(
        "issue", store=store) if held.get("replies"))
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_capture_text("issue", doc, "rewritten", store=store)
    assert "replies" in str(damaged.value)


class _AlsoTombstonesASibling(WritableFakeStore):
    """A store whose capture write prunes a bullet nobody named -- the shape
    `board_store.delete_capture`'s docstring warns `write_captures(prune=True)`
    expresses as an absence."""

    def write_capture(self, doc):
        stored = super().write_capture(doc)
        # The **last** bullet in his order, deliberately. Tombstoning one
        # from the middle shifts every bullet under it, so the "exactly one
        # position moved" check catches it and the count check never has to
        # fire -- which is how a mutation that deleted the count check
        # survived this test in its first form.
        ordered = board_records.capture_documents(stored["board"], store=self)
        victim = ordered[-1]
        if victim.get("_id") != stored.get("_id"):
            self.docs = [held for held in self.docs
                         if held.get("_id") != victim.get("_id")]
        return stored


def test_a_write_that_tombstoned_a_sibling_bullet_is_caught():
    _parsed, store_ok = writable()
    store = _AlsoTombstonesASibling(store_ok.docs, store_ok.registry)
    doc = _first_capture(store)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_capture_text("issue", doc, "rewritten", store=store)
    # The count and not the substring: "the replies under his capture
    # bullets changed" contains "capture bullets changed" too, and asserting
    # on that shared phrase let a mutation deleting the count check pass.
    assert "the capture bullets changed: 2 -> 1" in str(damaged.value)


class _AlsoNudgesARow(WritableFakeStore):
    """A store whose capture write also moves a row of his board."""

    def write_capture(self, doc):
        stored = super().write_capture(doc)
        self.docs = [
            dict(held, status="✅ Done", statusKey="done")
            if str(held.get("_id", "")).startswith("board:") else held
            for held in self.docs]
        return stored


def test_a_write_that_moved_a_row_is_caught():
    _parsed, store_ok = writable()
    store = _AlsoNudgesARow(store_ok.docs, store_ok.registry)
    doc = _first_capture(store)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_capture_text("issue", doc, "rewritten", store=store)
    assert "rows changed" in str(damaged.value)


class _WritesDifferentWords(WritableFakeStore):
    """A store whose capture write stores something other than what it was
    handed -- the half of the check that `was[index] != old_text` cannot
    see, because the right bullet did move."""

    def write_capture(self, doc):
        return super().write_capture(dict(doc, text=doc.get("text", "")[:4]))


def test_a_write_that_landed_as_something_else_is_caught():
    _parsed, store_ok = writable()
    store = _WritesDifferentWords(store_ok.docs, store_ok.registry)
    doc = _first_capture(store)
    with pytest.raises(board_write.BoardDamaged) as damaged:
        board_write.change_capture_text(
            "issue", doc, "rewritten at length", store=store)
    assert "not as written" in str(damaged.value)
