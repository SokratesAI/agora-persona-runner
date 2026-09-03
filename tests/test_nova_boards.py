"""The board pages: the parser, and the route that serves them.

The owner, issues.md #57: *"Create more pages to contain more, such as
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
    priority_key,
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


def _serve(board_md, notes_md, archive_md=""):
    """Patch the three vault reads a board page makes, by path.

    The archive defaults to empty, which is the state of the live vault
    until `tools/roll_captures.py` is first pointed at these files -- and
    is the case that has to keep behaving exactly as it did before the
    archive existed. Pass `archive_md` for the other one.
    """
    def read(path):
        if path.endswith("-archive.md"):
            return archive_md
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


def test_the_rolled_off_captures_still_reach_the_page(board_md, notes_md):
    """The blocker Cycle 112 refused to roll around.

    `roll_captures.py` moves everything past the newest 60 into an
    archive beside the live file. This page is what the owner opens, so if
    it reads only the live path, the first roll silently deletes two
    thirds of it. Live notes first, archived ones after, because both
    files are newest-first and the archive holds only what is older.
    """
    archive_md = (
        "---\ntype: log\nstatus: built\n"
        "maintenance: Captures rolled off Nova's live capture file, newest first.\n"
        "---\n\n"
        "# Nova — Issues Archive\n\n"
        "## Entries\n\n"
        "- 2026-08-05 (Cycle 24) — an old capture that was rolled off\n"
    )
    with _serve(board_md, notes_md, archive_md):
        _, _, body = _get("/api/board?name=issues&limit=10")
    payload = json.loads(body)
    assert payload["notesTotal"] == 4
    assert [note["cycle"] for note in payload["notes"]] == [63, 62, None, 24]
    # The archive's own frontmatter is not a capture and must not be
    # glued onto the note above it, which is exactly what concatenating
    # the two files before parsing would have done.
    text = json.dumps(payload["notes"])
    assert "maintenance:" not in text
    assert "rolled off" in text


def test_a_board_reads_the_archive_beside_the_live_file(board_md, notes_md):
    """The archive path is derived from nothing the request carries, and
    it is the sibling of the live file rather than some other folder."""
    seen = []

    def read(path):
        seen.append(path)
        return "" if path.endswith("-archive.md") else board_md

    with patch.object(nova_sources, "vault_read_path", side_effect=read):
        _get("/api/board?name=ideas&limit=1")
    assert BOARD_PATHS["ideas"]["nova_archive"] in seen
    assert BOARD_PATHS["ideas"]["nova_archive"].endswith(
        "nova/resources/ideas-archive.md"
    )


def test_the_board_reads_the_same_file_the_capture_button_writes():
    """The one invariant nothing was pinning, found by mutation-checking
    the 2026-08-12 folder move: reverting `BOARD_PATHS["issues"]["edvard"]`
    to the old path left all 1519 tests green.

    These are two literal strings in two modules and they must name one
    document. When they drift, nothing raises -- a capture is written to
    one path and the board reads the other, so the page shows an
    unchanging list and the writes go somewhere nobody opens. That is
    indistinguishable from "he hasn't typed anything", which is why it
    needs a test rather than attention.
    """
    from agora_runner.nova_capture import CAPTURE_TARGETS

    for name, paths in BOARD_PATHS.items():
        assert paths["edvard"] == CAPTURE_TARGETS[name], name


def test_priority_is_read_from_the_fifth_column():
    """The owner's rating, appended rather than inserted.

    Appended so that every cell above it keeps its index: a board he has
    not rated yet has four columns and must still parse, and it does --
    see the test below. Inserting it anywhere else would have shifted
    status and updated one to the right on every unrated row.
    """
    md = (
        "## Board\n\n"
        "| # | Idea | Status | Updated | Priority |\n"
        "|---|------|--------|---------|----------|\n"
        "| [[#68 — A thing\\|68]] | A thing | 🟡 In progress | 08-13 | 🔴 Immediately |\n"
        "| [[#67 — Another\\|67]] | Another | ⚪ Backlog | 08-13 | Low |\n"
    )
    items = {i["number"]: i for i in parse_board(md)["items"]}
    # Normalised on the way out even though the fixture cell still
    # carries the glyph -- that is the point of the reviewer's finding
    # on #244: the raw cell is what `app.js` puts in the chip.
    assert items[68]["priority"] == "🔴 Immediately"
    assert items[68]["priorityKey"] == "immediate"
    assert items[67]["priorityKey"] == "low"
    # The columns it must not have disturbed.
    assert items[68]["status"] == "🟡 In progress"
    assert items[68]["updated"] == "08-13"
    assert items[67]["title"] == "Another"


def test_a_board_with_no_priority_column_still_parses():
    """The state both live files are in until a cycle backfills them, and
    the state a hand-edit can return one row to at any time."""
    md = (
        "## Board\n\n"
        "| # | Idea | Status | Updated |\n"
        "|---|------|--------|---------|\n"
        "| [[#68 — A thing\\|68]] | A thing | 🟡 In progress | 08-13 |\n"
    )
    item = parse_board(md)["items"][0]
    assert item["priority"] == ""
    assert item["priorityKey"] == ""
    assert item["status"] == "🟡 In progress"
    assert item["updated"] == "08-13"


def test_done_rows_never_take_a_priority():
    """`## Done` is a four-column table with a different meaning in its
    last two cells -- `updated` then `where`. Reading a fifth column there
    would be reading a column that does not exist, and reading the fourth
    as a priority would put the PR list in the chip."""
    md = (
        "## Done\n\n"
        "| # | Idea | Updated | Where |\n"
        "|---|------|---------|-------|\n"
        "| [[#46 — Shipped\\|46]] | Shipped | 08-10 | #131, #133 |\n"
        # Five cells: a row carried down from `## Board` with its rating
        # still attached, which is what moving one by hand actually
        # produces. The fifth cell must be ignored, not shown -- a done
        # thing has no priority left to argue about.
        "| [[#45 — Also shipped\\|45]] | Also shipped | 08-10 | #140 | 🔴 Immediately |\n"
    )
    items = {i["number"]: i for i in parse_board(md)["items"]}
    assert items[46]["done"] is True
    assert items[46]["priority"] == ""
    assert items[46]["where"] == "#131, #133"
    assert items[45]["priority"] == ""
    assert items[45]["priorityKey"] == ""


def test_immediately_and_immediate_are_one_bucket():
    """"immediately" is the word the owner used in the capture; "immediate"
    is the word a hand-edit reaches for. A rating that fell into its own
    bucket would sort last, which is the opposite of what it says."""
    assert priority_key("Immediately") == "immediate"
    assert priority_key("🔴 immediate") == "immediate"
    assert priority_key("") == ""


def test_search_matches_the_write_up_and_not_only_the_title(board_md, notes_md):
    """The point of doing this server-side (ideas.md #71).

    "visualisations" appears nowhere in any row title -- it is inside
    #57's quoted detail body, which `board_page` strips from the list.
    A client-side search over what the page holds could not find it.
    """
    with _serve(board_md, notes_md):
        status, _, body = _get("/api/board?name=issues&q=visualisations")
    assert status == 200
    payload = json.loads(body)
    assert payload["matches"] == [57]
    assert payload["query"] == "visualisations"
    # Rows are not resent: the page already has them and only wants to
    # know which ones matched.
    assert "items" not in payload


def test_search_is_case_insensitive_and_covers_titles_too(board_md, notes_md):
    with _serve(board_md, notes_md):
        _, _, body = _get("/api/board?name=issues&q=GEMINI")
    assert json.loads(body)["matches"] == [58]


def test_an_empty_search_matches_nothing_rather_than_everything(board_md, notes_md):
    """Answering "" with all 71 rows looks exactly like a working search."""
    with _serve(board_md, notes_md):
        _, _, body = _get("/api/board?name=issues&q=%20%20")
    assert json.loads(body)["matches"] == []


def test_a_search_that_hits_nothing_says_so(board_md, notes_md):
    with _serve(board_md, notes_md):
        _, _, body = _get("/api/board?name=issues&q=zzzznotinthisfile")
    assert json.loads(body)["matches"] == []


def test_the_search_blob_never_goes_out_with_a_page(board_md, notes_md):
    """It is every write-up on the board again, lowercased -- the exact
    payload `details` is stripped to avoid. All three page shapes."""
    with _serve(board_md, notes_md):
        for url in (
            "/api/board?name=issues&limit=2",
            "/api/board?name=issues",
            "/api/board?name=issues&item=57",
        ):
            _, _, body = _get(url)
            assert "searchText" not in json.loads(body), url


def test_two_different_searches_do_not_share_one_etag(board_md, notes_md):
    """The cache is keyed per variant. Without `q` in the key, the second
    query would be served a 304 against the first query's answer."""
    with _serve(board_md, notes_md):
        _, _, first = _get("/api/board?name=issues&q=gemini")
        _, _, second = _get("/api/board?name=issues&q=visualisations")
    assert json.loads(first)["version"] != json.loads(second)["version"]


# --- unanswered_comments: which rows are still waiting on a reply -----------
# Idea #64 put a comment box on every board row (Cycle 219) and stored the
# thread inline in the row's write-up, which is what made it cheap and what
# left it with no `## New` queue. These pin the "last note wins" rule.

_UC_BOARD = """## Board

| # | Item | Status | Updated | Priority |
|---|---|---|---|---|
| [[#4 — a\\|4]] | a | ⚪ Backlog | 08-01 | 🟠 High |

# Details

## 4 — a

{body}
"""


def _uc(body):
    from agora_runner.nova_boards import unanswered_comments
    return unanswered_comments(_UC_BOARD.format(body=body))


def test_unanswered_comments_flags_a_write_up_ending_on_edvard():
    assert _uc("Problem.\n\n**Edvard, 08-15:** what about this?") == [4]


def test_unanswered_comments_clears_once_nova_replies():
    assert _uc("Problem.\n\n**Edvard, 08-15:** q?\n\n"
               "**Nova, 08-15 (Cycle 221):** a.") == []


def test_unanswered_comments_is_positional_not_a_count():
    """The owner, Nova, the owner is one note each way and still waiting on me."""
    assert _uc("**Edvard, 08-13:** one\n\n**Nova, 08-13 (Cycle 1):** two\n\n"
               "**Edvard, 08-15:** three") == [4]


def test_unanswered_comments_ignores_a_write_up_with_no_notes_at_all():
    """Most rows are only his statement of the problem, and that is not a comment."""
    assert _uc("Just the problem statement, with **bold** in it.") == []


def test_unanswered_comments_ignores_bold_prose_that_is_not_a_note():
    """A write-up opening on a bold lead-in must not read as a comment."""
    assert _uc("**Edvard wrote this ages ago** and it is prose, not a note.") == []


def test_unanswered_comments_matches_the_authors_nova_boards_allows():
    """Built from NOTE_AUTHORS so a third author cannot be silently unreadable."""
    from agora_runner.nova_boards import NOTE_AUTHORS, _COMMENT_NOTE_RE
    for name in NOTE_AUTHORS.values():
        assert _COMMENT_NOTE_RE.search(f"**{name}, 08-15:** hi")


def test_unanswered_comments_reads_the_note_append_detail_note_actually_writes():
    """The two must agree by construction, not by both being edited together."""
    from agora_runner.nova_boards import append_detail_note, unanswered_comments
    after_his = append_detail_note(_UC_BOARD.format(body="Problem."), 4,
                                   "is this right?", "08-15", author="Edvard")
    assert unanswered_comments(after_his) == [4]
    after_mine = append_detail_note(after_his, 4, "yes", "08-15",
                                    cycle=221, author="Nova")
    assert unanswered_comments(after_mine) == []


def test_unanswered_comments_is_positional_where_a_count_would_disagree():
    """The case that separates the two rules, and the earlier test did not.

    Two comments from him then one reply from me: a count says he is one
    ahead and still waiting, position says I had the last word and he is
    not. Position is right -- one reply can answer two questions, and the
    count rule would leave this row flagged forever, which is the state
    that makes the flag worth ignoring.

    Written after mutating the rule to a count and watching all 63 tests
    pass: `the owner, Nova, the owner` is 2-vs-1 and both rules call it waiting,
    so the test that claimed to pin this pinned nothing.
    """
    assert _uc("**Edvard, 08-13:** one\n\n**Edvard, 08-13:** and another\n\n"
               "**Nova, 08-13 (Cycle 1):** answering both") == []


def test_unanswered_comments_ignores_bold_prose_that_looks_like_a_note():
    """`**Edvard, in his own words:**` is prose and must not flag the row.  (not-prose: quoting a literal)

    A false positive here never clears: no reply of mine can answer a note
    that was never a note, so the row claims to be waiting forever. The
    `MM-DD` in the pattern is what separates the two, and every real note
    has one because `append_detail_note` refuses an empty date.
    """
    assert _uc("**Edvard, in his own words:** this is the problem.") == []
    assert _uc("**Nova, on reflection:** still prose.") == []


# --- unanswered_comment_bodies: the text a reply claim is named after -------

def _ucb(body):
    from agora_runner.nova_boards import unanswered_comment_bodies
    return unanswered_comment_bodies(_UC_BOARD.format(body=body))


def test_the_body_starts_at_his_marker_and_not_at_the_write_up():
    """The write-up is his statement of the problem and never changes.
    Naming a claim after it would give every comment on the row one name."""
    got = _ucb("Problem, stated at length.\n\n**Edvard, 08-15:** what about this?")
    assert got == {4: "**Edvard, 08-15:** what about this?"}


def test_the_body_runs_past_the_first_line_of_his_comment():
    """Two comments on one row on one day are told apart by their text, and
    a long comment's opening clause is exactly where they look alike."""
    got = _ucb("**Edvard, 08-15:** I have been thinking about this,\nand the "
               "second half is where they differ.")
    assert got[4].endswith("where they differ.")


def test_only_the_last_note_is_the_one_owed_a_reply():
    got = _ucb("**Edvard, 08-15:** first\n\n**Nova, 08-15 (Cycle 1):** answered\n\n"
               "**Edvard, 08-16:** and now this")
    assert got == {4: "**Edvard, 08-16:** and now this"}


def test_an_answered_row_has_no_body_and_no_claim():
    assert _ucb("**Edvard, 08-15:** q\n\n**Nova, 08-15 (Cycle 1):** a") == {}


def test_unanswered_comments_is_the_numbers_of_the_same_answer():
    from agora_runner.nova_boards import unanswered_comments
    body = "**Edvard, 08-15:** q"
    assert unanswered_comments(_UC_BOARD.format(body=body)) == \
        sorted(_ucb(body), reverse=True)


def test_a_reply_indented_under_a_capture_is_not_a_capture_of_its_own():
    """A cycle's reply is written as an indented bullet under the capture.

    Read as its own bullet it becomes a capture from him that he never
    typed, and `top_board_rows` ranks it above every board row for as
    long as it sits there. That happened on his `issues.md` on
    2026-08-25: `roll_done_captures` moved the owner's `DONE` bullet and
    left the reply alone at the top of the file.
    """
    board = parse_board(
        "---\ntype: board\n---\n\n"
        "- the notes text is grey and hard to read\n"
        "  - Fixed in runner#360 — say the word and the byline goes white too.\n"
        "- \n\n"
        "## Board\n"
    )
    assert board["captures"] == ["the notes text is grey and hard to read"]
    # And it is not welded onto his sentence either. It used to be, and
    # that is what made every write on an answered capture fail: the page
    # sent the folded string back as the address and no capture reads
    # that way (his `issues.md` capture, 2026-08-25).
    assert board["captureReplies"] == [
        ["Fixed in runner#360 — say the word and the byline goes white too."]
    ]


# The owner, issues.md 2026-08-27: *"The Nova app has become extremely slow.
# Opening, navigating, loading comments, anything."* Navigating is a sidebar
# press into a board. `warm_cache` deliberately left the boards out on a
# 2026-08-12 measurement of 0.53s and 0.39s cold; measured against the live
# pod 2026-08-28, six minutes into a process that had served nothing since
# it started, `/api/board?name=ideas&limit=30` answered in 5.05s and
# `/api/board?name=issues` in 3.15s, against 0.03-0.09s warm.


def test_the_first_press_on_a_board_after_a_deploy_reads_no_vault(board_md, notes_md):
    """Both boards, because they are separate cache keys and warming one
    leaves the other exactly as slow as it was.

    Asserted as "the vault was not read again" rather than as a duration,
    the same way the journal's warm test is: the five seconds is a bulk
    fetch plus a parse, and a wall clock over a fixture measures neither.
    """
    nova_site.reset_cache()

    def read(path):
        return notes_md if "/nova/resources/" in path else board_md

    with patch.object(nova_sources, "vault_read_path", side_effect=read) as reader:
        nova_site.warm_cache()
        warmed = reader.call_count
        assert warmed, "the warm built nothing at all"
        for board in ("issues", "ideas"):
            status, _, body = _get(f"/api/board?name={board}&limit=1")
            assert status == 200, board
            assert json.loads(body)["items"], f"{board} warmed to an empty board"
            assert reader.call_count == warmed, (
                f"the first visitor to {board} paid the cold build anyway"
            )


def test_the_projects_page_shares_one_cache_key_with_the_board_route(board_md, notes_md):
    """Two spellings of one key is two builds and one of them uneditable.

    `/projects` read `cached_payload("issues", ...)` while `/api/board`
    read `cached_payload("board:issues", ...)`, over the identical
    `board_payload("issues")`. So the warm reached one and not the other,
    and all five `invalidate("board:" + target)` call sites missed the
    `/projects` copy -- a row edited from the app stayed stale there.

    Asserted on the cache keys rather than on a read count, because a
    read count cannot tell one key warmed twice from two keys warmed
    once, and the second is the bug.
    """
    nova_site.reset_cache()

    def read(path):
        return notes_md if "/nova/resources/" in path else board_md

    with patch.object(nova_sources, "vault_read_path", side_effect=read), \
            patch.object(nova_site, "project_priorities", dict):
        # The ratings are a separate uncached read and are stubbed rather
        # than left to fall back, so this test still measures cache keys
        # instead of accidentally measuring the fallback path.
        nova_site.warm_cache()
        warmed = set(nova_site._cache)
        status, _, _ = _get("/api/project")
    assert status == 200
    assert set(nova_site._cache) - warmed == set(), (
        f"/api/project built {sorted(set(nova_site._cache) - warmed)}, "
        "which the warm did not reach"
    )
    assert {"board:issues", "board:ideas"} <= warmed


# `is_relayed` — a comment that says of itself that Sokrates typed it.

def test_the_live_disclosure_sentence_is_recognised():
    from agora_runner.nova_boards import is_relayed
    assert is_relayed("**Edvard, 08-29:** Sokrates here (Claude, posting on "
                      "Edvard's behalf, not Edvard typing this himself): "
                      "decision on the auth proposal.")


def test_an_ordinary_comment_is_not_a_relay():
    from agora_runner.nova_boards import is_relayed
    assert not is_relayed("**Edvard, 08-29:** what about this?")
    assert not is_relayed("")
    assert not is_relayed(None)


def test_a_comment_that_merely_discusses_relaying_is_not_one():
    """The window is what separates these two, and this is why it exists."""
    from agora_runner.nova_boards import RELAY_WINDOW, is_relayed
    prose = "x" * RELAY_WINDOW
    assert not is_relayed(prose + " a comment posted on Edvard's behalf "
                                  "should rank below one he typed.")



# --- His rows come out of the ticket store now ---------------------------
#
# The first reader of the one-document-per-ticket migration. Until this,
# `nova_tickets` was written on every board write and read by nothing, so
# a drift in it could only be found by `tools.ticket_drift` running once a
# cycle. These pin the three answers `_rows_from_store` can give, because
# every other test in this file exercises the fallback by accident --
# there is no CouchDB under them, so `read_rows` raises and the parsed
# rows come back looking exactly like a working switch.


def test_his_rows_come_from_the_ticket_store_when_it_agrees():
    parsed = [{"number": 9, "title": "b"}, {"number": 4, "title": "a"}]
    stored = [{"number": 9, "title": "b"}, {"number": 4, "title": "a"}]
    with patch.object(nova_site, "read_rows", return_value=stored):
        got = nova_site._rows_from_store("issues", parsed)
    # Identity, not equality: equality is what the function already
    # checked, so asserting it again would pass on the fallback too and
    # this test would never fail if the switch were reverted.
    assert got is stored


def test_a_store_that_disagrees_draws_the_file_and_says_so():
    """His file is the source of truth and a silent fallback is the bug.

    A row-projection view that has drifted is not a smaller answer to
    degrade to, it is a different board, and the page cannot tell. So the
    file wins -- and it is said out loud, because a fallback nobody is
    told about is the invisible failure this loop keeps filing.
    """
    parsed = [{"number": 9, "title": "b"}, {"number": 4, "title": "a"}]
    stored = [{"number": 4, "title": "a"}, {"number": 9, "title": "b"}]
    said = []
    with patch.object(nova_site, "read_rows", return_value=stored), \
            patch.object(nova_site, "log", said.append):
        got = nova_site._rows_from_store("issues", parsed)
    assert got is parsed
    assert said and "disagree" in said[0]


def test_an_unreachable_store_draws_the_file_and_says_so():
    said = []
    with patch.object(nova_site, "read_rows", side_effect=RuntimeError("boom")), \
            patch.object(nova_site, "log", said.append):
        got = nova_site._rows_from_store("ideas", parsed := [{"number": 1}])
    assert got is parsed
    assert said and "unreadable" in said[0]


def test_board_payload_actually_asks_the_store_for_his_rows(board_md, notes_md):
    """The wiring, not the helper.

    Every test above this one passes whether or not `board_payload` calls
    `_rows_from_store` at all: there is no CouchDB under them, so the
    helper returns the parsed rows and the payload looks identical either
    way. Deleting the call is a mutation the rest of the file cannot see,
    and it survived until this test existed.
    """
    asked = []

    def only_the_store(name, parsed):
        asked.append(name)
        return parsed

    with _serve(board_md, notes_md), \
            patch.object(nova_site, "_rows_from_store", only_the_store):
        payload = nova_site.board_payload("issues")
    assert asked == ["issues"]
    assert payload["items"], "the rows still have to reach the page"


# --- And his write-ups come out of it too --------------------------------
#
# The second reader. Same three answers, same reason they need pinning
# separately: with no CouchDB under the suite `read_details` raises and
# the parsed bodies come back looking exactly like a working switch.


def test_his_write_ups_come_from_the_ticket_store_when_it_agrees():
    parsed = {9: "nine's write-up", 4: "four's"}
    stored = {9: "nine's write-up", 4: "four's"}
    with patch.object(nova_site, "read_details", return_value=stored):
        got = nova_site._details_from_store("issues", parsed)
    # Identity again, for the same reason: equality is what the function
    # itself checked, so asserting it would pass on the fallback.
    assert got is stored


def test_a_store_missing_a_write_up_draws_the_file_and_says_so():
    """The failure this actually guards. A body is kilobytes of his prose
    about his own problem, so a store that has one of them and not the
    other is not a smaller answer -- it is a page with a blank write-up
    on a row that has one, and nothing on the page could tell."""
    parsed = {9: "nine's write-up", 4: "four's"}
    said = []
    with patch.object(nova_site, "read_details", return_value={9: "nine's write-up"}), \
            patch.object(nova_site, "log", said.append):
        got = nova_site._details_from_store("issues", parsed)
    assert got is parsed
    assert said and "disagree" in said[0]


def test_a_write_up_that_stopped_tracking_the_file_draws_the_file():
    parsed = {9: "the edited write-up"}
    said = []
    with patch.object(nova_site, "read_details", return_value={9: "the old one"}), \
            patch.object(nova_site, "log", said.append):
        got = nova_site._details_from_store("issues", parsed)
    assert got is parsed
    assert said and "disagree" in said[0]


def test_a_store_that_cannot_be_read_for_write_ups_draws_the_file():
    said = []
    with patch.object(nova_site, "read_details", side_effect=RuntimeError("boom")), \
            patch.object(nova_site, "log", said.append):
        got = nova_site._details_from_store("ideas", parsed := {1: "body"})
    assert got is parsed
    assert said and "unreadable" in said[0]


def test_board_payload_actually_asks_the_store_for_his_write_ups(board_md, notes_md):
    """The wiring, not the helper -- deleting the call is a mutation
    nothing above this can see, exactly as it was for the rows."""
    asked = []

    def only_the_store(name, parsed):
        asked.append(name)
        return parsed

    with _serve(board_md, notes_md), \
            patch.object(nova_site, "_details_from_store", only_the_store):
        payload = nova_site.board_payload("issues")
    assert asked == ["issues"]
    assert payload["details"], "the write-ups still have to reach the page"
