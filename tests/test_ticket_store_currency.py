"""The store can say whether it is still current with the markdown.

`nova_site._rows_from_store` proves the store agrees with his file by
fetching the file and comparing every field -- which is the strongest
check available and also why the migration saves nothing yet: the fetch it
would remove is the fetch the check depends on. A revision answers the
same question without the file, so these tests pin the two halves of it:
the stamp goes on the board document when a writer knows the rev, and
`currency` never reports CURRENT from anything but a match.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agora_runner import ticket_docs, ticket_store  # noqa: E402


BOARD = "projects/sokrates/projects/nova/issues.md"

MARKDOWN = "\n".join([
    "## Board",
    "",
    "| # | Title | Status | Updated |",
    "| --- | --- | --- | --- |",
    "| #2 | Second | 🟡 In progress | 09-03 |",
    "| #1 | First | 🟢 Done | 09-01 |",
    "",
    "# Details",
    "",
    "## #1 First",
    "",
    "The write-up.",
])


def _board_doc(source_rev):
    records = ticket_store.to_records(MARKDOWN)
    docs = ticket_docs.to_documents(BOARD, records, source_rev=source_rev)
    board = [doc for doc in docs if doc["type"] == "board"]
    assert len(board) == 1, "one layout document per board"
    return board[0]


def test_the_board_document_carries_the_revision_it_was_built_from():
    assert _board_doc("7-abc")["sourceRev"] == "7-abc"


def test_a_writer_that_does_not_know_the_revision_stamps_nothing():
    # The bridge pod's `tools.board_*` writes the file in another process
    # and cannot know the rev. It must leave no stamp rather than an old
    # one, so the verdict is UNKNOWN and never a false CURRENT.
    assert _board_doc(None)["sourceRev"] is None


def test_the_stamp_is_the_only_field_added():
    # The board document is compared field by field against what is stored
    # to decide whether to write it (`write_board`), so an extra field
    # would rewrite every layout document once and then never again --
    # this pins that there is exactly one.
    with_rev = _board_doc("7-abc")
    without = _board_doc(None)
    assert set(with_rev) == set(without)
    assert {key for key in with_rev if with_rev[key] != without[key]} == {"sourceRev"}


def _currency(monkeypatch, stored, live):
    monkeypatch.setattr(ticket_docs, "stored_source_rev", lambda path: stored)
    return ticket_docs.currency(BOARD, live)


def test_a_matching_revision_is_current(monkeypatch):
    assert _currency(monkeypatch, "7-abc", "7-abc")[0] == ticket_docs.CURRENT


def test_a_moved_file_is_stale(monkeypatch):
    verdict, why = _currency(monkeypatch, "7-abc", "8-def")
    assert verdict == ticket_docs.STALE
    # Both revisions in the message: "stale" alone says nothing about how
    # far behind, and the two strings are what a cycle would go looking for.
    assert "7-abc" in why and "8-def" in why


def test_an_unstamped_board_cannot_claim_to_be_current(monkeypatch):
    assert _currency(monkeypatch, None, "8-def")[0] == ticket_docs.UNKNOWN


def test_no_live_revision_cannot_claim_to_be_current(monkeypatch):
    # A vault read that answered nothing is not agreement. This is the
    # direction that matters: UNKNOWN falls back to the markdown, CURRENT
    # would eventually let a reader skip it.
    assert _currency(monkeypatch, "7-abc", None)[0] == ticket_docs.UNKNOWN


def test_two_boards_that_happen_to_share_a_revision_string(monkeypatch):
    # CouchDB revisions are per document, so "7-abc" on one board says
    # nothing about another. `currency` is asked per path and reads that
    # path's own stamp -- pinned because a cached or module-level stamp
    # would pass every test above and be wrong here.
    seen = {}

    def stamp(path):
        seen[path] = seen.get(path, 0) + 1
        return {BOARD: "7-abc"}.get(path)

    monkeypatch.setattr(ticket_docs, "stored_source_rev", stamp)
    assert ticket_docs.currency(BOARD, "7-abc")[0] == ticket_docs.CURRENT
    other = "projects/sokrates/projects/nova/ideas.md"
    assert ticket_docs.currency(other, "7-abc")[0] == ticket_docs.UNKNOWN
    assert seen == {BOARD: 1, other: 1}
