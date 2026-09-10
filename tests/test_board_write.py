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

from agora_runner import board_records, board_store, board_write
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
