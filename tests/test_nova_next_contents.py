"""The two board readers in `nova_next`, fed records instead of a file.

Issue #203. `open_rows` and `unboarded_captures` used to *be* their first
line -- `parse_board(markdown)` -- and everything after it was already a
rule about `parse_board`'s return value, which is the same four keys
`board_records.contents` answers out of CouchDB. The split gives the
switchover a door to walk through without a facade over the parser.

Every contents dict here is built by hand and **no markdown exists in this
file at all**. That is the point of the file rather than a style choice: a
test that builds a board file and parses it cannot tell a converted reader
from an unconverted one, because both agree on text the parser produced.
These hand-built dicts carry shapes a board file cannot express -- a
detail body for a row that is not in `items`, an `order` integer, a
capture list with no bullet syntax -- so a reader that quietly went back to
parsing would have nothing to parse.
"""

import json
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from agora_runner import board_records, nova_next


def _refuse_the_file(*_a, **_k):
    """The route must not reach his markdown for either board.

    Patched over `nova_site.edvard_board_markdown` in the two route tests
    below: it is still imported there for `board_payload`'s own fallback,
    so its mere presence proves nothing and a call is what has to fail.
    """
    raise AssertionError("next_up_payload read a board file")
from agora_runner.nova_next import (
    next_payload_from_contents,
    open_rows_from_contents, unboarded_captures_from_contents,
)


def row(number, **over):
    item = {"number": number, "title": f"row {number}", "status": "Backlog",
            "statusKey": "backlog", "priority": "🟡 Soon", "priorityKey": "soon",
            "updated": "09-09", "done": False, "project": "Nova",
            "size": "", "sizeKey": "", "milestone": "", "order": None}
    item.update(over)
    return item


def contents(items=(), captures=(), details=None):
    return {"items": list(items), "captures": list(captures),
            "captureReplies": {}, "details": dict(details or {})}


def test_rows_come_back_with_no_file_to_parse():
    rows = open_rows_from_contents(contents([row(7)]), "issue")
    assert [r["number"] for r in rows] == [7]
    assert rows[0]["board"] == "issue"
    assert rows[0]["slug"] == "issue-7"


def test_a_closed_row_is_not_open():
    got = open_rows_from_contents(
        contents([row(7, statusKey="done", status="✅ Done"), row(8)]), "issue")
    assert [r["number"] for r in got] == [8]


def test_waiting_is_read_out_of_the_details_the_caller_handed_in():
    """The half that would silently vanish if `details` were dropped.

    A row is `waiting` because of text in `details`, which lives nowhere in
    `items`. Reading it from the same dict is what keeps `open_rows_from_contents`'
    promise that a row and its thread cannot come from two different reads
    -- true for free on one string, and something a records reader has to
    actually do, since two queries against a live store can straddle a
    write.
    """
    body = "**Edvard, 09-09:** is this still needed?"
    got = open_rows_from_contents(contents([row(7)], details={7: body}), "issue")
    assert got[0]["waiting"] is True
    assert got[0]["replySlug"] is not None


def test_an_answered_thread_is_not_waiting():
    body = ("**Edvard, 09-09:** is this still needed?\n"
            "\n**Nova, 09-09:** yes, it is.")
    got = open_rows_from_contents(contents([row(7)], details={7: body}), "issue")
    assert got[0]["waiting"] is False
    assert got[0]["replySlug"] is None


def test_a_row_with_no_thread_is_not_waiting():
    got = open_rows_from_contents(contents([row(7)]), "issue")
    assert got[0]["waiting"] is False


def test_a_detail_body_for_a_row_that_is_not_on_the_board_moves_nothing():
    """A shape no board file can hold, so nothing here can be parsed into.

    `details` is keyed by row number and the store answers the two ranges
    separately, so a detail document can outlive its row. It must not mint
    a row, and it must not make some other row wait.
    """
    got = open_rows_from_contents(
        contents([row(7)], details={99: "**Edvard, 09-09:** hello?"}), "issue")
    assert [r["number"] for r in got] == [7]
    assert got[0]["waiting"] is False


def test_the_hand_set_seat_survives_the_handover():
    got = open_rows_from_contents(contents([row(7, order=3)]), "issue")
    assert got[0]["order"] == 3


def test_captures_come_back_with_no_file_to_parse():
    got = unboarded_captures_from_contents(
        contents(captures=["🔴 Immediately: fix the thing"]), "issue")
    assert [c["text"] for c in got] == ["fix the thing"]
    assert got[0]["priority"] == "🔴 Immediately"
    assert got[0]["board"] == "issue"


def test_a_finished_capture_is_not_unprocessed_but_still_holds_its_address():
    got = unboarded_captures_from_contents(
        contents(captures=["DONE (Cycle 3): old one", "the live one"]), "issue")
    assert [c["text"] for c in got] == ["the live one"]
    # The index is the address `/api/capture/comment` resolves against, so it
    # counts the finished bullet it skipped.
    assert got[0]["index"] == 1


def test_a_capture_already_closed_as_a_row_drops_out():
    items = [row(7, title="fix the thing", statusKey="done", status="✅ Done")]
    got = unboarded_captures_from_contents(
        contents(items, captures=["fix the thing"]), "issue")
    assert got == []


@pytest.mark.parametrize("door,reader", [
    ("open_rows", "open_rows_from_contents"),
    ("unboarded_captures", "unboarded_captures_from_contents"),
    ("next_payload", "next_payload_from_contents"),
])
def test_the_markdown_door_is_gone_and_the_reader_is_not(door, reader):
    """The file-shaped function is deleted, not deprecated.

    It used to be the parse and nothing else, and this test used to pin
    the two answers equal. Issue #203 deleted it once the last source
    caller was gone: while a `<name>(markdown, board)` exists, a module
    being migrated can reach a board by importing it, and the switchover's
    whole claim is that after conversion there is one way in. A door
    nothing calls today is the one a future caller finds.

    The second assertion is not decoration. `hasattr` on a name nobody
    defines is `False` for a module that never had it, for a typo, and for
    a module that failed to import the way this test expects -- so the
    absence only means anything beside a name that must be present.
    """
    assert not hasattr(nova_next, door)
    assert callable(getattr(nova_next, reader))


# --- `next_payload`, the composition on top of the two readers -------------
#
# The split above gave the two readers a records-shaped door; this one gives
# it to the function his phone actually calls. Same rule about markdown: not
# a byte of it below either, so a payload builder that quietly went back to
# parsing has nothing to parse.

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 8, 30, 17, 0, tzinfo=OSLO)


def test_the_whole_payload_comes_back_with_no_file_to_parse():
    payload = next_payload_from_contents(
        contents([row(7, priority="🔴 Immediately", priorityKey="immediate")],
                 captures=["something he typed"]),
        contents([row(64)]), json.dumps({"claims": []}), NOW)

    assert [c["text"] for c in payload["captures"]] == ["something he typed"]
    assert payload["captures"][0]["board"] == "issues"
    assert [(r["board"], r["number"]) for r in payload["next"]] == [
        ("issue", 7), ("idea", 64)]
    assert payload["claimsReadable"] is True


def test_the_payload_never_reaches_a_parser():
    """The records door must not be a facade over `parse_board`.

    `board-records.md` bans an accessor that can fall back to the parser,
    and the ban is only worth anything if something fails when one does.
    Handing in records and then parsing would still return the right answer
    for a caller that had markdown, which is exactly why this cannot be
    left to reading the diff.

    This used to monkeypatch `nova_next.parse_board` to raise. Issue #203
    deleted the import along with the last door that used it, so the
    stronger assertion is available and is made instead: the name the
    fallback would need is not in the module at all. The payload build
    below is what keeps that from being a bare absence check -- a module
    that failed to import would satisfy the first line and not the rest.
    """
    assert not hasattr(nova_next, "parse_board")
    payload = next_payload_from_contents(
        contents([row(7)], captures=["a bullet"]), contents([row(64)]),
        json.dumps({"claims": []}), NOW)
    assert [r["number"] for r in payload["next"]] == [7, 64]


def test_a_bullet_its_own_board_already_carries_is_not_offered_as_new():
    """The captures and the rows of one board are a single read.

    The old body called the two deleted markdown doors on the same
    file, each parsing it again -- harmless on a string, two reads a write
    can land between once the source is CouchDB. `unboarded_captures_from_contents`
    decides a bullet is unprocessed by looking at the *rows*, so a capture
    list read before a row list reports a bullet whose row already exists,
    and a waking cycle is handed it above the whole board as work nobody
    has started. Cycle 1007 got two finished items that way.
    """
    boarded = row(7, title="fix the thing", statusKey="done",
                  status="✅ Done", done=True)
    payload = next_payload_from_contents(
        contents([boarded], captures=["fix the thing"]),
        contents(), json.dumps({"claims": []}), NOW)

    assert payload["captures"] == []


def test_an_unreadable_ledger_is_said_out_loud_and_keeps_the_rows():
    """An empty ledger and an unreadable one mean opposite things, and the
    door did not stop being the place that distinguishes them."""
    payload = next_payload_from_contents(
        contents([row(7)]), contents(), "{not json", NOW)

    assert payload["claimsReadable"] is False
    assert [r["number"] for r in payload["next"]] == [7]


def test_the_site_route_builds_the_payload_off_the_records():
    """`next_up_payload` was `next_payload`'s last source caller.

    That is what let the door be deleted, so it is the thing to pin: the
    route reaches `board_records.contents` for each of his two boards and
    reaches no board file at all. Asserting only that the answer is right
    would pass just as well on the markdown it replaced.
    """
    from agora_runner import nova_site

    asked = []

    def fake_contents(board, **_kw):
        asked.append(board)
        return contents([row(7 if board == "issue" else 64)])

    with mock.patch.object(nova_site.board_records, "contents", fake_contents), \
            mock.patch.object(nova_site, "claims_ledger_json",
                              lambda: json.dumps({"claims": []})), \
            mock.patch.object(nova_site, "project_meta_markdown", lambda: ""), \
            mock.patch.object(nova_site, "milestone_pins_markdown", lambda: ""), \
            mock.patch.object(nova_site, "edvard_board_markdown",
                              _refuse_the_file):
        payload = nova_site.next_up_payload()

    assert asked == ["issue", "idea"]
    assert [(r["board"], r["number"]) for r in payload["next"]] == [
        ("issue", 7), ("idea", 64)]


def test_the_site_route_refuses_an_unmigrated_store_rather_than_emptying_it():
    """A store that cannot answer must reach the client as an error.

    `board_records.contents` raises instead of returning an empty board
    precisely because `read_rows` cannot tell "never migrated" from "every
    row closed". Catching that here would put an empty plan on his phone,
    which is the wrong answer wearing the right shape -- the failure the
    refusal exists to stop.
    """
    from agora_runner import nova_site

    def unmigrated(_board, **_kw):
        raise board_records.UnmigratedStore("nothing has ever been migrated")

    with mock.patch.object(nova_site.board_records, "contents", unmigrated), \
            mock.patch.object(nova_site, "edvard_board_markdown",
                              _refuse_the_file):
        with pytest.raises(board_records.RecordError):
            nova_site.next_up_payload()
