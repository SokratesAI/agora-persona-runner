"""The owner changing a rating a cycle wrote (`issues.md` capture, 2026-08-14)."""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import (
    CAPTURE_PRIORITY_SEP,
    OUTDATED_STATUS,
    _CLOSED_STATUS_KEYS,
    PRIORITY_LABELS,
    parse_board,
    set_row_priority,
)

BOARD = """---
type: board
---

- an unboarded capture

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#57 — More pages\\|57]] | More pages | 🟡 In progress | 08-11 | 🔵 Medium |
| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |
| [[#76 — Already finished\\|76]] | Already finished | ✅ Done | 08-14 |

## Done

| # | Item | Landed | Where |
|---|------|--------|-------|
| [[#51 — One way\\|51]] | One way | 08-10 | inbox.md |

## #57 — More pages

Body text I must not touch.
"""


def test_changes_the_cell_and_parse_board_reads_it_back():
    updated = set_row_priority(BOARD, 57, "Immediately")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 57][0]
    assert row["priority"] == "🔴 Immediately"
    assert row["priorityKey"] == "immediate"
    assert row["title"] == "More pages" and row["status"] == "🟡 In progress"


def test_grows_the_fifth_cell_on_a_row_that_never_had_one():
    updated = set_row_priority(BOARD, 59, "High")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 59][0]
    assert row["priority"] == "🟠 High"
    assert row["updated"] == "08-11"


def test_clearing_back_to_unrated_is_reachable():
    updated = set_row_priority(BOARD, 57, "")
    row = [i for i in parse_board(updated)["items"] if i["number"] == 57][0]
    assert row["priority"] == "" and row["priorityKey"] == ""


def test_touches_nothing_but_the_one_row():
    updated = set_row_priority(BOARD, 57, "High")
    before, after = BOARD.split("\n"), updated.split("\n")
    assert len(before) == len(after)
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    assert len(differing) == 1 and "#57" in before[differing[0]]


def test_refuses_a_done_row_a_missing_row_and_a_rating_i_did_not_offer():
    assert set_row_priority(BOARD, 51, "High") is None
    assert set_row_priority(BOARD, 999, "High") is None
    assert set_row_priority(BOARD, 57, "🟣 Whenever") is None
    # A word that is not one of the four is still refused, glyph or no glyph.
    assert set_row_priority(BOARD, 57, "Whenever") is None


def test_a_rating_spelled_the_old_way_writes_the_new_way():
    """The four labels have now been renamed twice; callers get the memo late.

    `"high"` and `"🟠 High"` were both refused before Cycle 268, on an
    exact match against `PRIORITY_LABELS.values()`. That was safe while
    the values never moved. They moved -- twice, in two days and in
    opposite directions -- so a phone holding a cached `app.js` would
    have had every rating it set answered with a 400, and nothing on the
    page would have said why. Normalising accepts what any of them means
    and stores the current spelling, which is the point, and is why this
    reversal cost nothing on the wire.
    """
    for spelled in ("🟠 High", "High", "high", "HIGH", " High "):
        updated = set_row_priority(BOARD, 57, spelled)
        assert updated is not None, spelled
        row = [i for i in parse_board(updated)["items"] if i["number"] == 57][0]
        assert row["priority"] == "🟠 High", spelled


def test_labels_round_trip_through_priority_key():
    from agora_runner.nova_boards import priority_key
    for key, label in PRIORITY_LABELS.items():
        assert priority_key(label) == key


# --- the app's rating button, which writes the #203 record store (Cycle 1381) ---
#
# Every test below goes through a fake store and a vault that raises if it is
# touched at all: a test that let the markdown writer run would pass against
# the converted and the unconverted button alike.

import pytest  # noqa: E402

from agora_runner import board_records  # noqa: E402
from tests.test_board_records import writable  # noqa: E402
from tests.test_tools_board_priority import BOARD as RECORD_BOARD  # noqa: E402


class _VaultTouched(BaseException):
    """Not an `Exception`: `set_priority` catches every `Exception` on the
    read, and a landmine it swallows proves nothing."""


@pytest.fixture
def records(monkeypatch):
    _, fake = writable(board="idea", markdown=RECORD_BOARD)
    monkeypatch.setattr(nova_capture, "board_store", fake)

    def landmine(*a, **k):
        raise _VaultTouched("the rating button touched the markdown")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", landmine)
    monkeypatch.setattr(nova_capture, "vault_write_path", landmine)
    return fake


def _row(store, number):
    return {item["number"]: item
            for item in board_records.contents("idea", store=store)["items"]}[number]


def _row_writes(store):
    return [call for call in store.calls if call[0] == "write_row"]


def test_the_landmine_is_armed(records):
    with pytest.raises(_VaultTouched):
        nova_capture.vault_write_path("x", "y")


def test_set_priority_writes_the_record_once_and_never_the_file(records):
    before = board_records.contents("idea", store=records)
    ok, message = nova_capture.set_priority("ideas", 260, "High")
    assert ok and "#260" in message
    assert len(_row_writes(records)) == 1
    row = _row(records, 260)
    # The invariant that survived both renames, and the only one the owner
    # ever actually asked for: the **word** is in what gets written. The
    # glyph came back beside it in Cycle 274 (*"if you use the symbol and
    # text, thats completely fine!"*); what may never come back is the
    # glyph on its own, which is what he could not read.
    assert row["priority"] == "🟠 High"
    assert row["priorityKey"] == "high"
    after = board_records.contents("idea", store=records)
    assert [r for r in after["items"] if r["number"] != 260] == \
        [r for r in before["items"] if r["number"] != 260]
    assert after["details"] == before["details"]
    assert after["captures"] == before["captures"]


def test_clearing_a_rating_back_to_unrated_is_still_reachable(records):
    ok, _ = nova_capture.set_priority("ideas", 259, "")
    assert ok
    assert _row(records, 259)["priority"] == ""
    assert _row(records, 259)["priorityKey"] == ""


def test_a_store_passed_in_is_the_one_written(records, monkeypatch):
    """`store = store or board_store` collapsing to `board_store` left every
    test that only patches the module global green (Cycle 1345's lesson)."""
    _, other = writable(board="idea", markdown=RECORD_BOARD)
    ok, _ = nova_capture.set_priority("ideas", 260, "Low", store=other)
    assert ok
    assert _row(other, 260)["priority"] == "⚪ Low"
    assert _row_writes(records) == []


@pytest.mark.parametrize("target, number, priority, why", [
    ("ideas", 258, "High", "not an open row"),      # ✅ Done, still in ## Board
    ("ideas", 51, "High", "not an open row"),       # in ## Done
    ("ideas", 999, "High", "not an open row"),      # not there at all
    ("ideas", 260, "🟣 Whenever", "unknown priority"),
    ("notes", 260, "High", "unknown target"),       # not a board
])
def test_set_priority_writes_nothing_it_refuses(records, target, number, priority, why):
    ok, message = nova_capture.set_priority(target, number, priority)
    assert not ok and why in message
    assert _row_writes(records) == []


def test_a_row_in_done_is_refused_even_when_its_status_cell_reads_open(records):
    """`done` is which table a row is in, not what its status cell says.

    Every `## Done` row the migration writes also reads `✅ Done`, so the
    status check hides the `done` check on any fixture built from markdown --
    dropping `row.get("done")` left the whole file green. A stored record can
    carry both, so this one does.
    """
    doc = next(d for d in records.docs if d.get("number") == 51)
    doc["status"] = "🟡 In progress"
    row = _row(records, 51)
    assert row["done"] is True and row["status"] == "🟡 In progress", \
        "the fixture no longer builds the row this test is about"
    ok, message = nova_capture.set_priority("ideas", 51, "High")
    assert not ok and "not an open row" in message
    assert _row_writes(records) == []


def test_an_outdated_row_is_refused_by_the_button_too(monkeypatch):
    board = RECORD_BOARD.replace("| Demos live two weeks | 🟡 In progress |",
                                 "| Demos live two weeks | " + OUTDATED_STATUS + " |")
    assert board != RECORD_BOARD, "the fixture row was not rewritten"
    _, fake = writable(board="idea", markdown=board)
    ok, message = nova_capture.set_priority("ideas", 259, "High", store=fake)
    assert not ok and "not an open row" in message
    assert [call for call in fake.calls if call[0] == "write_row"] == []


# --- the other half of the same capture: a rating typed with the capture ---

from agora_runner.nova_boards import split_capture_priority


def test_a_rated_bullet_splits_into_a_rating_and_his_words():
    assert split_capture_priority("🟠 High: fix the sort order") == ("🟠 High", "fix the sort order")
    assert split_capture_priority("🔴 Immediately: the app is down") == (
        "🔴 Immediately", "the app is down")
    # The wordless spelling Cycle 268 wrote into his files still reads.
    assert split_capture_priority("High: fix the sort order") == ("🟠 High", "fix the sort order")


def test_a_bullet_captured_before_the_glyph_was_dropped_still_reads():
    """The owner's two files are full of these and nothing rewrites them.

    Cycle 268 stopped *writing* the coloured ball; every bullet captured
    before it still carries one, so dropping the read would silently
    demote each of them to unrated and swallow the glyph into his prose.
    """
    assert split_capture_priority("🟠 fix the sort order") == ("🟠 High", "fix the sort order")
    assert split_capture_priority("🔴 the app is down") == ("🔴 Immediately", "the app is down")


def test_the_word_form_needs_its_colon_because_a_word_can_open_a_sentence():
    """The whole reason `CAPTURE_PRIORITY_SEP` exists.

    A leading glyph is unambiguous -- no sentence opens with a coloured
    ball. "High" opens one easily, and reading it as a rating would eat
    the first word of the capture and file it under a rating the owner never
    gave it. This is the regression the rename could have shipped.
    """
    assert split_capture_priority("High memory use in the runner pod") == (
        "", "High memory use in the runner pod")
    assert split_capture_priority("Low signal from the health check") == (
        "", "Low signal from the health check")


# --- and the marker a cycle writes when its work closed one of them ---

from agora_runner.nova_boards import split_capture_done
from agora_runner.nova_site import _capture_parts


def test_a_closed_capture_gives_up_its_cycle_and_his_words():
    assert split_capture_done("DONE (Cycle 247): shipped it — the old ask") == (
        "Cycle 247", "shipped it — the old ask")


def test_an_open_capture_comes_back_whole():
    assert split_capture_done("just a thought") == ("", "just a thought")
    # Only a prefix is a marker; the word mid-sentence is his prose.
    assert split_capture_done("I am DONE (Cycle 9): with it") == (
        "", "I am DONE (Cycle 9): with it")
    # The colon is required, so "DONE (Cycle 9) yesterday" is prose too.
    assert split_capture_done("DONE (Cycle 9) yesterday, roughly") == (
        "", "DONE (Cycle 9) yesterday, roughly")


def test_the_page_reads_the_marker_before_the_rating_not_after():
    """Both are prefixes on one line and the done marker sits outermost --
    read the rating first and every closed capture reports as unrated."""
    assert _capture_parts("DONE (Cycle 247): 🟠 High: fix the sort order") == (
        "Cycle 247", "🟠 High", "fix the sort order")
    assert _capture_parts("🟠 High: fix the sort order") == ("", "🟠 High", "fix the sort order")


def test_an_unrated_bullet_comes_back_whole():
    assert split_capture_priority("just a thought") == ("", "just a thought")
    # Only a leading glyph is a rating; the same emoji mid-sentence is prose.
    assert split_capture_priority("the 🔴 dot is wrong") == ("", "the 🔴 dot is wrong")


def test_capture_prefixes_only_the_first_bullet_of_a_paste(monkeypatch):
    written = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: ("---\n---\n\n- \n\n## Board\n", "1-a"))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: written.update(body=body) or "written")
    ok, _ = nova_capture.capture("issues", "first line\nsecond line", "High")
    assert ok
    assert "- 🟠 High: first line" in written["body"]
    assert "- second line" in written["body"]
    assert "🟠 High: second line" not in written["body"]


def test_capture_refuses_a_rating_that_is_not_one_of_the_four(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("must not read or write")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", refuse)
    ok, message = nova_capture.capture("issues", "text", "🟣 Whenever")
    assert not ok and "unknown priority" in message


def test_an_unrated_capture_is_written_exactly_as_typed(monkeypatch):
    written = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: ("---\n---\n\n- \n\n## Board\n", "1-a"))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, body, if_rev=None: written.update(body=body) or "written")
    ok, _ = nova_capture.capture("issues", "plain thought")
    assert ok and "- plain thought" in written["body"]


def test_a_finished_row_is_refused_even_though_it_sits_in_the_board_table():
    """Most finished rows never move to `## Done` -- #76 is `✅ Done` in
    `## Board`, and `parse_board` reports `done=False` for it. Rating one
    would show a priority chip on a finished item."""
    row = [i for i in parse_board(BOARD)["items"] if i["number"] == 76][0]
    assert row["statusKey"] == "done" and row["done"] is False
    assert set_row_priority(BOARD, 76, "🟠 High") is None


def test_the_javascript_rating_list_is_byte_identical_to_the_python_one():
    """The one guard that could have caught the escape bug, and did not exist.

    `app.js` holds its own copy of the four ratings because the browser
    cannot import `nova_boards`. The server checks a submitted rating
    against `PRIORITY_LABELS` and rejects anything else, and the picker
    preselects by string equality against a row's existing rating -- so a
    single wrong character on either side makes the feature look present
    and fail on every write.

    It did. The first version of that line was written with Python's
    `\\U########` escape, which JavaScript does not have: it drops the
    backslash and keeps the digits, so three of the four became strings
    like `U0001f535 Medium`. No Python test executes `app.js` and no
    browser test referenced the array, so both suites stayed green with
    the feature dead for every rating except Low.
    """
    import json
    import pathlib
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "agora_runner" / "nova_public" / "app.js"
    ).read_text(encoding="utf-8")
    found = re.search(r"var PRIORITIES = (\[[^\]]*\]);", source)
    assert found, "app.js no longer declares PRIORITIES as a single-line array"
    # `json.loads` and not a hand-rolled split: it refuses an escape that is
    # not real JSON, which is the exact class of bug this test exists for.
    assert json.loads(found.group(1)) == list(PRIORITY_LABELS.values())

    # And the separator beside it, for the same reason and with a sharper
    # failure: `app.js` builds the capture bullet and `split_capture_priority`
    # parses it back, so a drift here does not raise anything -- it writes
    # `High fix the thing` into the owner's file, which reads as unrated prose
    # with his rating gone and the word left in his sentence.
    sep = re.search(r'var PRIORITY_SEP = ("[^"]*");', source)
    assert sep, "app.js no longer declares PRIORITY_SEP as a single-line string"
    assert json.loads(sep.group(1)) == CAPTURE_PRIORITY_SEP


def test_an_outdated_row_is_refused_a_rating_the_same_way_a_done_one_is():
    """`⚫ Outdated` is the fifth status, from the owner's `issues.md` #85.

    It means the row will never be built, which is a closed row -- so it
    takes no rating, for the same reason `✅ Done` takes none: a priority
    chip on it says somebody is still deciding when to do it.

    The emoji is deliberately not switched on anywhere; `status_key`
    strips it and reads the word, so this passes on `⚫ Outdated` and on
    a hand-typed `Outdated` alike.
    """
    board = BOARD.replace(
        "| [[#59 — Small pickings\\|59]] | Small pickings | ⚪ Backlog | 08-11 |",
        "| [[#59 — Small pickings\\|59]] | Small pickings | " + OUTDATED_STATUS + " | 08-11 |",
    )
    assert OUTDATED_STATUS in board, "the fixture row was not rewritten"
    row = [i for i in parse_board(board)["items"] if i["number"] == 59][0]
    assert row["statusKey"] == "outdated"
    assert set_row_priority(board, 59, "🟠 High") is None
    # And an open row in the same file still writes, so the refusal above
    # is the status and not the fixture.
    assert set_row_priority(board, 57, "🟠 High") is not None


def test_the_javascript_outdated_key_is_the_one_python_actually_derives():
    """The reviewer's finding on runner#191, and it is the same shape as the
    rating-list guard above.

    `OUTDATED_STATUS` is the display text and it lives in one place. The
    thing every branch actually tests is the *key* `status_key` reduces it
    to -- and that word is written out by hand in three languages:
    `_CLOSED_STATUS_KEYS` here, `isOutdated` in `app.js`, and
    `.chip-outdated` in `style.css`. None of them derives it. So rewording
    `OUTDATED_STATUS` would leave all three matching a status nothing
    writes any more, with both suites green, which is exactly what the
    comment beside it claims to have prevented.
    """
    from pathlib import Path

    from agora_runner.nova_boards import OUTDATED_STATUS, status_key

    key = status_key(OUTDATED_STATUS)
    assert key == "outdated", f"the derived key moved: {key!r}"
    assert key in _CLOSED_STATUS_KEYS

    public = Path(__file__).resolve().parent.parent / "agora_runner" / "nova_public"
    app_js = (public / "app.js").read_text(encoding="utf-8")
    style = (public / "style.css").read_text(encoding="utf-8")
    assert f'item.statusKey === "{key}"' in app_js, "app.js branches on a different key"
    assert f'{{ key: "{key}", label: "Outdated"' in app_js, "the filter key does not match"
    assert f".chip-{key} " in style, "the chip has no stylesheet rule under that key"


from agora_runner.nova_boards import split_capture_project  # noqa: E402


def test_a_project_tag_comes_off_the_front_of_a_capture():
    assert split_capture_project("(Project: Marcus) log a session by talking") == (
        "Marcus",
        "log a session by talking",
    )
    # His own typing: lower case, padded, a two-word name.
    assert split_capture_project("(project:  Sokrates Post ) a thing") == (
        "Sokrates Post",
        "a thing",
    )


def test_a_sentence_that_merely_opens_with_a_parenthesis_is_left_alone():
    """The boundary the prefix match exists for -- his prose is his prose."""
    for bullet in (
        "just a thought",
        "(a parenthetical opener) and then the point",
        "the Project: Marcus page is slow",
        "a note (Project: Marcus) in the middle",
    ):
        assert split_capture_project(bullet) == ("", bullet)


def test_a_tag_no_project_cell_could_hold_is_not_lifted():
    """`set_row_project` refuses a `|`, a `*` and anything past 40 characters.

    Lifting one of those out of the title would move his text into a cell
    that then refuses it, which loses the words. Left in the title, where
    it reads oddly and is still his.
    """
    for bullet in (
        "(Project: Mar|cus) a thing",
        "(Project: *Marcus*) a thing",
        "(Project: " + "M" * 41 + ") a thing",
    ):
        assert split_capture_project(bullet) == ("", bullet)


def test_the_buttons_board_names_are_the_sites():
    """`nova_capture.RECORD_BOARDS` is a hand copy of `nova_site._RECORD_BOARDS`
    (neither can import `tools/`); a board added on one side only must fail
    here, not on his phone. The reviewer's finding on df42e5c."""
    from agora_runner import nova_site
    assert nova_capture.RECORD_BOARDS == nova_site._RECORD_BOARDS
