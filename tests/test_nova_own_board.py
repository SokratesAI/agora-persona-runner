"""My own capture files get a board too -- issue #97.

His two files are boarded (a numbered row, a status, a rating, a write-up)
and mine were a flat bullet stream, so the Nova tab on the board page drew
a notes list and his drew rows. He said the tidiness was the point and the
separation was not: *"making your board like mine and giving yourself more
tidiness is an improvement"*.

The half that could go wrong quietly is the note stream. `## Board` and
`# Details` go at the *end* of my files, after `## Entries` and
`## Retired`, so `parse_notes`, `backlog_brief.head_section` and
`rolling`'s `section_bounds` all see exactly the section they saw before.
A test that only checked the new rows appeared would pass just as happily
with 654 notes silently truncated, so the two are asserted together.
"""

from agora_runner import nova_site
from agora_runner.nova_boards import parse_board, parse_notes

# The shape of one of my files after this change: prose, head bullets,
# the note stream, the retired tail, then the board at the bottom.
MINE = """---
type: note
---

# Nova — Issues

Crude capture only, my own notes, one line each.

- 2026-08-24 (Cycle 376) — a head bullet, current friction.

## Entries

- 2026-08-25 (Cycle 406) — the newest note.
- 2026-08-24 (Cycle 405) — an older note.

## Retired

- 2026-08-01 (Cycle 1) — retired, nothing reads this.

## Board

| # | Item | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#1 — Dead newspaper feeds\\|1]] | Dead newspaper feeds | 🟡 In progress | 08-25 | 🟠 High |
| [[#2 — Unrated row\\|2]] | Unrated row | ⚪ Backlog | 08-25 |

# Details

### #1 — Dead newspaper feeds

Four RSS feeds have failed on 82 consecutive nights.

### #2 — Unrated row

No rating on the table row above, on purpose.
"""


def test_my_board_rows_parse_like_his():
    board = parse_board(MINE)
    assert [item["number"] for item in board["items"]] == [1, 2]
    first = board["items"][0]
    assert first["title"] == "Dead newspaper feeds"
    assert first["status"] == "🟡 In progress"
    assert first["statusKey"] == "in-progress"
    assert first["priority"] == "🟠 High"
    assert first["priorityKey"] == "high"
    assert first["updated"] == "08-25"


def test_an_unrated_row_carries_no_priority():
    # Blank means nobody has looked, and that has to survive the parse --
    # a defaulted rating would make every row look considered.
    second = parse_board(MINE)["items"][1]
    assert second["priority"] == ""
    assert second["priorityKey"] == ""


def test_write_ups_come_back_keyed_by_number():
    details = parse_board(MINE)["details"]
    assert set(details) == {1, 2}
    assert "82 consecutive nights" in details[1]
    # The `### #N —` heading is not part of its own body.
    assert "Dead newspaper feeds" not in details[1]


def test_the_note_stream_is_untouched_by_the_new_sections():
    # The failure this is really about: `## Board` at the end closes
    # `## Entries`, so a splitter that read to end-of-file would swallow
    # the table and the write-ups as notes, and a `section_bounds` that
    # did not would drop nothing. Only the two real notes are notes.
    notes = parse_notes(MINE)
    assert [note["cycle"] for note in notes] == [406, 405]
    assert notes[0]["text"] == "the newest note."
    assert all("Dead newspaper feeds" not in note["text"] for note in notes)


def test_board_payload_puts_my_rows_on_the_page(monkeypatch):
    """The parse tests above would pass with `board_payload` untouched.

    `parse_board` is not new; pointing it at my own file is, and that is
    one line in `nova_site`. The first version of this test read the
    committed browser fixture and asserted the two keys were in it --
    which the reviewer on runner#354 correctly called a no-op, because
    the fixture is a file on disk and reverting the server change plus
    re-running `regen.py` would leave it passing. It was a guard
    reporting itself working while guarding nothing, which is the one
    failure shape this loop keeps paying for.

    So this calls the real `board_payload` over real markdown, through
    the read the server actually makes.
    """
    from agora_runner import nova_sources
    from agora_runner.nova_boards import BOARD_PATHS
    from agora_runner.nova_site import board_payload

    def read(path):
        if path == BOARD_PATHS["issues"]["nova"]:
            return MINE
        return ""

    monkeypatch.setattr(nova_sources, "vault_read_path", read)
    monkeypatch.setattr(nova_site, "_his_board", lambda name: parse_board(""))
    payload = board_payload("issues")

    assert [item["number"] for item in payload["novaItems"]] == [1, 2]
    assert payload["novaItems"][0]["priority"] == "\U0001f7e0 High"
    # Rendered blocks, not raw markdown -- the page draws these directly.
    assert payload["novaDetails"]["1"][0]["type"] == "p"
    # And his side is untouched by mine: an empty file for him means no
    # rows, not my rows leaking across the tab.
    assert payload["items"] == []
    assert payload["details"] == {}
    # The note stream still arrives beside the rows rather than instead
    # of them.
    assert len(payload["notes"]) == 2


def test_my_write_ups_are_windowed_like_his_on_a_list_request():
    """`board_page(limit=...)` strips his `details` and must strip mine.

    Reviewer finding on runner#354, and it is right on the substance even
    though the comment it quoted was a plan rather than a claim: at eight
    rows my write-ups are ~4KB and riding along is free, but nothing was
    going to notice at eighty. The client asks for the bodies of the row
    it opens, the same way his tab does.
    """
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [],
        "details": {"9": [{"type": "p"}]},
        "novaItems": [{"number": 1}],
        "novaDetails": {"1": [{"type": "p"}]},
        "notes": [],
    }
    listed = board_page(dict(payload), limit=10)
    assert listed["details"] == {}
    assert listed["novaDetails"] == {}
    # The rows themselves stay -- they are one line each, and dropping
    # them would empty the tab rather than window it.
    assert listed["novaItems"] == [{"number": 1}]

    # No limit is the pre-#354 client asking for everything; it still gets
    # everything, which is what that shape is for.
    whole = board_page(dict(payload))
    assert whole["novaDetails"] == {"1": [{"type": "p"}]}


def test_asking_for_one_of_my_rows_returns_its_write_up():
    """`?item=N&mine=1` -- the fetch the expanded row makes now.

    Deliberately a separate flag rather than reusing `item=`: his #1 and
    my #1 are different rows on the same page, and answering one query
    with whichever dict happened to have the key is the collision this
    keeps out.
    """
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [{"number": 1, "title": "his"}],
        "details": {"1": [{"type": "p", "text": "his body"}]},
        "novaItems": [{"number": 1, "title": "mine"}],
        "novaDetails": {"1": [{"type": "p", "text": "my body"}]},
        "notes": [],
    }
    mine = board_page(payload, item=1, mine=True)
    assert mine["found"] is True
    assert mine["item"]["title"] == "mine"
    assert mine["item"]["blocks"][0]["text"] == "my body"

    his = board_page(payload, item=1)
    assert his["item"]["title"] == "his"
    assert his["item"]["blocks"][0]["text"] == "his body"


def test_my_rows_get_a_search_blob_of_their_own(monkeypatch):
    """`novaSearchText`, built the same way his `searchText` is.

    The page holds my row titles and, since runner#355 windowed
    `novaDetails` away on every list request, none of my write-ups -- so
    the write-up half of a search has to be answered here, exactly as it
    is for him.
    """
    from agora_runner import nova_sources
    from agora_runner.nova_boards import BOARD_PATHS
    from agora_runner.nova_site import board_payload

    def read(path):
        return MINE if path == BOARD_PATHS["issues"]["nova"] else ""

    monkeypatch.setattr(nova_sources, "vault_read_path", read)
    monkeypatch.setattr(nova_site, "_his_board", lambda name: parse_board(""))
    blobs = board_payload("issues")["novaSearchText"]

    assert set(blobs) == {"1", "2"}
    # Title and write-up, lowercased -- the write-up is the half the page
    # cannot search for itself.
    assert "dead newspaper feeds" in blobs["1"]
    assert "82 consecutive nights" in blobs["1"]


def test_searching_my_board_answers_from_my_rows_not_his():
    """Both boards number from 1, so a search that ignored `mine` would
    answer the Nova tab with his row numbers -- the same collision the
    `item=` branch already refuses."""
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [{"number": 7, "title": "his row"}],
        "searchText": {"7": "his row\nsomething about badges"},
        "novaItems": [{"number": 3, "title": "my row"}],
        "novaSearchText": {"3": "my row\nsomething about badges"},
        "notes": [],
    }
    assert board_page(payload, search="badges", mine=True)["matches"] == [3]
    assert board_page(payload, search="badges")["matches"] == [7]


def test_my_search_blob_never_goes_out_with_a_page():
    """It is every write-up on my board again, lowercased -- the exact
    payload `novaDetails` is windowed to avoid."""
    from agora_runner.nova_site import board_page

    payload = {
        "name": "issues",
        "items": [],
        "details": {},
        "novaItems": [{"number": 1}],
        "novaDetails": {"1": [{"type": "p"}]},
        "novaSearchText": {"1": "my row"},
        "notes": [],
    }
    for page in (board_page(dict(payload), limit=10), board_page(dict(payload))):
        assert "novaSearchText" not in page


def test_the_same_query_on_the_two_tabs_does_not_share_one_etag():
    """Without `mine` in the cache variant the second tab is served a 304
    against the first tab's row numbers."""
    import json
    from unittest.mock import patch

    from agora_runner import nova_site, nova_sources
    from agora_runner.nova_boards import BOARD_PATHS
    from tests.test_nova_site import _get

    def read(path):
        return MINE if path == BOARD_PATHS["issues"]["nova"] else ""

    nova_site.reset_cache()
    try:
        with patch.object(nova_sources, "vault_read_path", side_effect=read), \
                patch.object(nova_site, "_his_board",
                             side_effect=lambda name: parse_board("")):
            _, _, his = _get("/api/board?name=issues&q=feeds")
            _, _, mine = _get("/api/board?name=issues&q=feeds&mine=1")
    finally:
        nova_site.reset_cache()
    assert json.loads(his)["matches"] == []
    assert json.loads(mine)["matches"] == [1]
    assert json.loads(his)["version"] != json.loads(mine)["version"]


# --- The module that reads those two files (issue #203, cycle 1356) -------
#
# `nova_site.board_payload` used to make three `parse_board` calls and only
# one of them was the owner's. `tools.board_reader_inventory` greps a module
# for the name, so `nova_site` could not leave the switchover's count however
# much of it was converted, and exempting it would have excused the
# owner-side read too. The split moved the two calls on my own files into
# `agora_runner.nova_own_board`, which has the `NOT_A_BOARD` entry those two
# files earn, and left `nova_site` with one door on his board.
#
# The last test here is the one that stops the split coming undone: it counts
# `parse_board` calls in `nova_site` rather than asking the inventory tool,
# because a guard spelled the same way as the thing it guards agrees with it
# by construction.

import re
from pathlib import Path

from agora_runner.nova_own_board import own_board

ROOT = Path(__file__).resolve().parent.parent



LIVE = """---
type: log
status: built
---

# Nova — Issues

## Board

| # | Title | Status | Rating | Updated |
| --- | --- | --- | --- | --- |
| [[#7 — a live row\\|7]] | a live row | 🟡 In progress | 🟠 High | 2026-09-10 |

# Details

### #7 — a live row

the newer half, written this week

## Entries

- 2026-09-10 (Cycle 1356) — a live note
"""

ARCHIVE = """---
type: log
status: built
maintenance: Captures rolled off Nova's live capture file, newest first.
---

# Nova — Issues Archive

# Details

### #7 — a live row

the older half, rolled off

### #4 — a row whose write-up was rolled

a body with no live half left

## Entries

- 2026-08-05 (Cycle 24) — an archived note
"""


def test_the_archived_half_of_a_write_up_is_drawn_in_front_of_the_live_half():
    """A `# Details` body is append-only, so the archive is the older half.

    `setdefault` here — live wins, archived half dropped — is what stopped
    an open row's write-up from ever being rolled, because the roller moves
    the older paragraphs off and the next cycle writes the row again within
    the hour.
    """
    board, _ = own_board(LIVE, ARCHIVE)
    body = board["details"][7]
    assert "the older half, rolled off" in body
    assert "the newer half, written this week" in body
    assert body.index("the older half") < body.index("the newer half"), \
        "the archive is by construction the older half and is drawn first"


def test_a_row_whose_only_write_up_is_archived_still_gets_one():
    board, _ = own_board(LIVE, ARCHIVE)
    assert "a body with no live half left" in board["details"][4]


def test_the_archive_adds_bodies_and_never_rows():
    """`parse_board` over an archive with no `## Board` table returns no
    items, so an archived row is still a row on the live board."""
    board, _ = own_board(LIVE, ARCHIVE)
    assert [item["number"] for item in board["items"]] == [7]
    # The precondition: #4 really is in the archive and really does have a
    # body, so "not a row" is a measurement rather than a fixture that
    # never mentioned it.
    assert 4 in board["details"]


def test_notes_come_from_both_files_live_first():
    """Both files are newest-first and the archive holds only what is older
    than the live file's oldest, so appending preserves the order."""
    _, notes = own_board(LIVE, ARCHIVE)
    assert [note["cycle"] for note in notes] == [1356, 24]


def test_the_archives_frontmatter_is_not_glued_onto_a_note():
    """Two parses rather than one over a concatenation. `parse_notes` joins
    a non-bullet line onto the note above it, so concatenating would put
    the archive's `maintenance:` line on the end of my oldest live note."""
    _, notes = own_board(LIVE, ARCHIVE)
    assert not any("maintenance:" in note["text"] for note in notes)


def test_a_missing_archive_is_the_behaviour_from_before_the_first_roll():
    board, notes = own_board(LIVE, "")
    assert board["details"][7].strip() == "the newer half, written this week"
    assert [note["cycle"] for note in notes] == [1356]


def _parse_board_calls(source):
    """Every `parse_board(` call in a source file, with its argument text.

    Comments and docstrings are stripped first — `nova_site` discusses
    `parse_board` in prose far more often than it calls it, and a raw grep
    reads every one of those as a call.
    """
    stripped = re.sub(r'"""(?:.|\n)*?"""', "", source)
    stripped = re.sub(r"^\s*#.*$", "", stripped, flags=re.M)
    return re.findall(r"\bparse_board\(\s*([^)]*)", stripped)


def test_nova_site_parses_no_board_now():
    """The seam, asserted where it can come undone.

    Issue #203's flip deleted the last `parse_board` call here, the markdown
    fallback onto his board; his half comes out of the record store and my
    own half out of `nova_own_board`. Nothing stops a later cycle putting a
    `parse_board(...)` back into `board_payload`, and the inventory tool
    would then hold `nova_site` in the count again. This counts the calls in
    the file rather than asking the tool, so the two disagree out loud
    instead of agreeing by construction.
    """
    calls = _parse_board_calls((ROOT / "agora_runner" / "nova_site.py").read_text())
    assert calls == [], calls
    # The precondition: the detector finds calls at all. Without this, a
    # regex that matched nothing would pass the day the door was deleted
    # and every day a second call was added.
    own = _parse_board_calls((ROOT / "agora_runner" / "nova_own_board.py").read_text())
    assert len(own) == 2, own
