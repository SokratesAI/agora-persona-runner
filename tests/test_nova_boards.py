"""The board pages: the parser, and the route that serves them.

Edvard, issues.md #57: *"Create more pages to contain more, such as
issue list, idea list (separate pages)"*.

The fixtures are real, for the reason `test_nova_site.py` gives at
length: `board_sample.md` is rows and detail sections lifted verbatim
out of the live `issues.md`, including the Obsidian wiki-link with an
alias pipe inside a table cell -- which is not a curiosity, it is every
row of both live files and it is what a naive `split("|")` gets wrong.
`board_notes_sample.md` is three of my own captures, one of them
deliberately the shape with no cycle number, which the live file has.
"""

import json
import os
from unittest.mock import patch

import pytest

from agora_runner import nova_site, nova_sources
from agora_runner.nova_boards import (
    BOARD_PATHS,
    parse_board,
    parse_notes,
    status_key,
)
from tests.test_nova_site import _get

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def board_md():
    return _fixture("board_sample.md")


@pytest.fixture
def notes_md():
    return _fixture("board_notes_sample.md")


@pytest.fixture(autouse=True)
def _clean_cache():
    nova_site.reset_cache()
    yield
    nova_site.reset_cache()


def _serve(board_md, notes_md):
    """Patch the two vault reads a board page makes, by path."""
    def read(path):
        return notes_md if "/nova/resources/" in path else board_md
    return patch.object(nova_sources, "vault_read_path", side_effect=read)


def test_a_row_survives_the_alias_pipe_inside_its_wiki_link(board_md):
    """`| [[#57 — Title|57]] | Title | 🟡 In progress | 08-11 |` is four
    columns containing five pipes. Split naively, every column shifts one
    to the right and the status renders as the title."""
    items = parse_board(board_md)["items"]
    first = items[0]
    assert first["number"] == 57
    assert first["title"] == "More pages in the Nova app"
    assert first["status"] == "🟡 In progress"
    assert first["statusKey"] == "in-progress"
    assert first["updated"] == "08-11"


def test_the_done_table_is_boarded_too_with_where_it_landed(board_md):
    board = parse_board(board_md)
    done = [item for item in board["items"] if item["number"] == 51][0]
    assert done["statusKey"] == "done"
    assert done["updated"] == "08-10"
    assert done["where"] == "inbox.md, identity.md, prompt.md"


def test_an_item_marked_done_in_the_board_table_still_reads_as_done(board_md):
    """#56 sits in `## Board` with a ✅ Done status rather than having been
    moved. The filter must not depend on which table it is in."""
    board = parse_board(board_md)
    item = [i for i in board["items"] if i["number"] == 56][0]
    assert item["statusKey"] == "done"


def test_captures_are_the_bare_bullets_and_never_his_empty_cursor(board_md):
    captures = parse_board(board_md)["captures"]
    assert captures == [
        'Small pickings on Nova ui - low priority - remove "Oslo" text from Journal timestamp.'
    ]


def test_details_are_keyed_by_number_and_hold_the_write_up(board_md):
    details = parse_board(board_md)["details"]
    assert set(details) == {57, 51}
    assert "Five pages, in the order I would build them." in details[57]


def test_notes_keep_the_files_own_order_and_tolerate_a_missing_cycle(notes_md):
    notes = parse_notes(notes_md)
    assert [note["cycle"] for note in notes] == [63, 62, None]
    assert notes[0]["date"] == "2026-08-09"
    assert notes[0]["text"].startswith("**`vault_tool.py get` does NOT truncate")
    assert notes[2]["text"].startswith("A note with no cycle number")


def test_status_key_survives_an_emoji_it_has_never_seen():
    assert status_key("🟡 In progress") == "in-progress"
    assert status_key("🔵 Waiting on Edvard") == "waiting-on-edvard"
    assert status_key("") == "none"


def test_the_board_page_sends_rows_and_notes_but_no_detail_bodies(board_md, notes_md):
    """The point of the endpoint. `issues.md` is 68KB and ~60KB of that is
    `# Details`; the list needs none of it."""
    with _serve(board_md, notes_md):
        status, head, body = _get("/api/board?name=issues&limit=2")
    assert status == 200
    assert "application/json" in head
    payload = json.loads(body)
    assert payload["details"] == {}
    assert [item["number"] for item in payload["items"]] == [57, 58, 56, 51]
    assert len(payload["notes"]) == 2
    assert payload["notesTotal"] == 3
    assert payload["notes"][0]["blocks"][0]["type"] == "p"
    assert "text" not in payload["notes"][0]


def test_one_item_comes_back_rendered_when_the_row_is_tapped(board_md, notes_md):
    with _serve(board_md, notes_md):
        status, _, body = _get("/api/board?name=issues&item=57")
    assert status == 200
    payload = json.loads(body)
    assert payload["found"] is True
    assert payload["item"]["title"] == "More pages in the Nova app"
    text = json.dumps(payload["item"]["blocks"])
    assert "Five pages, in the order I would build them." in text
    # The list's own rows do not ride along with a single item.
    assert "items" not in payload


def test_an_item_with_no_write_up_is_a_row_rather_than_an_error(board_md, notes_md):
    with _serve(board_md, notes_md):
        status, _, body = _get("/api/board?name=issues&item=58")
    assert status == 200
    payload = json.loads(body)
    assert payload["found"] is True
    assert payload["item"]["blocks"] == []
    assert payload["item"]["title"] == "Remove Gemini from Agora personas"


def test_ideas_and_issues_are_different_boards(board_md, notes_md):
    """Two names, two cache entries. A page asking for ideas must not be
    handed the issues payload the previous request warmed."""
    seen = []

    def read(path):
        seen.append(path)
        return notes_md if "/nova/resources/" in path else board_md

    with patch.object(nova_sources, "vault_read_path", side_effect=read):
        _get("/api/board?name=issues&limit=1")
        _get("/api/board?name=ideas&limit=1")
    assert BOARD_PATHS["issues"]["edvard"] in seen
    assert BOARD_PATHS["ideas"]["edvard"] in seen
    assert BOARD_PATHS["ideas"]["nova"] in seen


def test_an_unknown_board_is_a_400_not_a_vault_read(board_md, notes_md):
    with patch.object(nova_sources, "vault_read_path") as read:
        status, _, body = _get("/api/board?name=../../secrets")
    assert status == 400
    assert read.call_count == 0
    assert "name must be one of" in json.loads(body)["error"]


def test_a_window_gets_its_own_etag_and_a_repeat_gets_a_304(board_md, notes_md):
    with _serve(board_md, notes_md):
        status, head, body = _get("/api/board?name=issues&limit=1")
        etag = json.loads(body)["version"]
        assert f"ETag: {etag}" in head
        # A different window must not validate against it, or widening the
        # notes list would 304 against the shorter one already on screen.
        _, _, wider = _get("/api/board?name=issues&limit=2")
        assert json.loads(wider)["version"] != etag
        again, _, _ = _get(
            "/api/board?name=issues&limit=1", headers=f"If-None-Match: {etag}\r\n"
        )
    assert again == 304


@pytest.mark.parametrize("path", ["/issues", "/ideas", "/issues/"])
def test_the_board_urls_resolve_on_a_cold_load(path):
    """A bookmark and a pasted link have to work, not just a tap on the
    nav -- the same reason `/cycle/49` is a real URL."""
    status, head, _ = _get(path)
    assert status == 200
    assert "text/html" in head


def test_his_own_words_render_as_a_quote_rather_than_a_stray_marker(board_md):
    """Every item on his boards opens with a verbatim `>` quote. Until the
    board pages existed nothing rendered one, so the marker showed on
    screen as literal text in front of the one paragraph he wrote."""
    from agora_runner.nova_journal import render_blocks

    body = parse_board(board_md)["details"][57]
    blocks = render_blocks(body)
    quotes = [block for block in blocks if block["type"] == "quote"]
    assert len(quotes) == 1
    text = "".join(span["text"] for span in quotes[0]["spans"])
    assert text.startswith('"I need more visualisations in the Nova app.')
    assert ">" not in text
    assert not any(
        "".join(s["text"] for s in block.get("spans", [])).startswith(">")
        for block in blocks
    ), "a quote line fell through to a paragraph and kept its marker"


def test_a_multi_line_quote_is_one_block(board_md):
    from agora_runner.nova_journal import render_blocks

    blocks = render_blocks("> first line\n> second line\n\nafter")
    assert [block["type"] for block in blocks] == ["quote", "p"]
    assert "".join(s["text"] for s in blocks[0]["spans"]) == "first line second line"


def test_a_note_wrapped_onto_a_second_line_keeps_its_second_line():
    """One capture in the live `nova/resources/issues.md` runs onto an
    indented continuation line. Read as bullets alone, that sentence is
    dropped silently -- no error, no gap, just a shorter note."""
    notes = parse_notes(
        "## Entries\n\n"
        "- 2026-08-11 (Cycle 103) — the first half of the thought\n"
        "  and the second half, indented.\n"
        "- 2026-08-10 (Cycle 102) — a whole note on one line\n"
    )
    assert len(notes) == 2
    assert notes[0]["text"] == "the first half of the thought and the second half, indented."
    assert notes[0]["cycle"] == 103
    assert notes[1]["text"] == "a whole note on one line"


def test_a_capture_that_wrapped_keeps_its_second_half_and_his_cursor_stays_empty():
    """The capture box splits a paste on newlines, but the same file is
    edited in Obsidian on a phone. Half of his sentence going missing with
    no error is the worst thing this page could do."""
    board = parse_board(
        "---\ntype: board\n---\n\n"
        "- the first half of what he typed\n"
        "  and the second half, wrapped.\n"
        "- \n\n"
        "## Board\n"
    )
    assert board["captures"] == ["the first half of what he typed and the second half, wrapped."]
