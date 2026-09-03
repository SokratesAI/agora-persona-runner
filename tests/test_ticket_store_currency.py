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
import urllib.parse

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


def _docs(source_rev):
    records = ticket_store.to_records(MARKDOWN)
    return ticket_docs.to_documents(BOARD, records, source_rev=source_rev)


def _of_type(docs, kind):
    return [doc for doc in docs if doc["type"] == kind]


def test_the_revision_is_its_own_document():
    stamps = _of_type(_docs("7-abc"), "source")
    assert len(stamps) == 1
    assert stamps[0]["_id"] == f"rev:{BOARD}"
    assert stamps[0]["sourceRev"] == "7-abc"


def test_a_writer_that_does_not_know_the_revision_stamps_nothing():
    # The bridge pod's `tools.board_*` writes the file in another process
    # and cannot know the rev. Emitting no document at all is what makes
    # `write_board` tombstone a stamp already stored, so the verdict is
    # UNKNOWN and never a false CURRENT.
    assert _of_type(_docs(None), "source") == []


def test_the_stamp_never_touches_the_layout_document():
    # The whole reason it is a separate document. His `issues.md` layout is
    # 112KB and the rev moves on every write by definition, so a field on
    # the layout would make a status change cost a 112KB revision in the
    # store built to stop it costing a 656KB one in the vault.
    with_rev = _of_type(_docs("7-abc"), "board")
    without = _of_type(_docs(None), "board")
    assert with_rev == without
    assert "sourceRev" not in with_rev[0]


def test_the_stamp_document_is_small():
    # It exists to be cheap to rewrite; a layout or a ticket list leaking
    # into it would defeat the split above.
    import json
    assert len(json.dumps(_of_type(_docs("7-abc"), "source")[0])) < 200


def test_the_render_ignores_the_stamp():
    # `from_documents` -> `to_markdown` is what renders his file, and a
    # third document type must not reach it as a ticket or a layout.
    records = ticket_store.to_records(MARKDOWN)
    assert ticket_store.to_markdown(
        ticket_docs.from_documents(_docs("7-abc"))) == ticket_store.to_markdown(
            records)


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


def _fake_store(monkeypatch, held):
    """`write_board` against a store holding exactly `held`. Returns the sends."""
    sent = []

    def req(method, path, body=None, timeout=60):
        if method == "GET" and "_all_docs" in path:
            return 200, {"rows": [{"id": doc["_id"], "doc": doc}
                                  for doc in held.values()
                                  if doc["_id"].startswith("ticket:")]}
        if method == "GET":
            doc_id = urllib.parse.unquote(path.split("/", 1)[1])
            if doc_id in held:
                return 200, held[doc_id]
            return 404, {"error": "not_found"}
        if method == "POST" and path.endswith("_bulk_docs"):
            sent.extend(body["docs"])
            return 201, [{"ok": True} for _ in body["docs"]]
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(ticket_docs, "_req", req)
    return sent


def test_the_stamp_is_updated_rather_than_conflicted_with(monkeypatch):
    """It carries the stored `_rev`, so the second write is an update.

    This is the mutation that survived the first version of these tests:
    `write_board` only knew about the `ticket:` documents and the layout,
    so a stamp already in the store was sent back with no `_rev`, CouchDB
    answered `conflict` inside `_bulk_docs`, and the failure landed in the
    summary's `failures` list where nothing looks. `currency` would then
    have reported STALE forever after the very first write -- the exact
    false negative this whole change exists to avoid.
    """
    held = {
        ticket_docs.source_rev_doc_id(BOARD): {
            "_id": ticket_docs.source_rev_doc_id(BOARD), "_rev": "3-old",
            "type": "source", "board": BOARD, "sourceRev": "7-abc"},
    }
    sent = _fake_store(monkeypatch, held)
    records = ticket_store.to_records(MARKDOWN)
    ticket_docs.write_board(BOARD, records, source_rev="8-def")
    stamps = [doc for doc in sent if doc.get("type") == "source"]
    assert len(stamps) == 1
    assert stamps[0]["sourceRev"] == "8-def"
    assert stamps[0]["_rev"] == "3-old"


def test_an_unchanged_stamp_is_not_rewritten(monkeypatch):
    # Same rev in and out means nothing to write -- otherwise every
    # payload build would add a revision to a document that never changed.
    held = {
        ticket_docs.source_rev_doc_id(BOARD): {
            "_id": ticket_docs.source_rev_doc_id(BOARD), "_rev": "3-old",
            "type": "source", "board": BOARD, "sourceRev": "7-abc"},
    }
    sent = _fake_store(monkeypatch, held)
    ticket_docs.write_board(BOARD, ticket_store.to_records(MARKDOWN),
                            source_rev="7-abc")
    assert [doc for doc in sent if doc.get("type") == "source"] == []


def test_a_write_that_knows_no_revision_tombstones_the_stamp(monkeypatch):
    # The clearing half. A stamp left behind beside newer content would
    # claim a currency the store cannot prove.
    held = {
        ticket_docs.source_rev_doc_id(BOARD): {
            "_id": ticket_docs.source_rev_doc_id(BOARD), "_rev": "3-old",
            "type": "source", "board": BOARD, "sourceRev": "7-abc"},
    }
    sent = _fake_store(monkeypatch, held)
    ticket_docs.write_board(BOARD, ticket_store.to_records(MARKDOWN))
    tombstones = [doc for doc in sent if doc.get("_deleted")]
    assert [doc["_id"] for doc in tombstones] == [
        ticket_docs.source_rev_doc_id(BOARD)]
    assert tombstones[0]["_rev"] == "3-old"
