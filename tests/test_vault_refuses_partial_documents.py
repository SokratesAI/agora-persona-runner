"""A document missing content chunks must fail loudly, not arrive spliced.

Why this exists (2026-08-10). LiveSync stores a note as an ordered list
of content-chunk documents. `vault_assemble` walked that list and
substituted `""` for any chunk CouchDB did not return, so a note with a
hole in the middle came back as its surviving pieces concatenated --
mid-word, with no marker at the seam, and parsing perfectly well.

It happened to `projects/sokrates/projects/agora/ideas.md`, the owner's
idea board. A LiveSync client re-chunked it from 1 chunk into 184 and 6
of those never reached the database. 1238 characters vanished: the
`## Board` heading, the table header, rows #57 through #50, and the tail
of the capture sentence he had typed 83 seconds earlier. What every
reader served instead was his half-eaten sentence welded to the middle
of row #50 -- `... Idea ca` + `veryone else names it|50]] | ...`.

The damage was recoverable only because the corrupting write changed
nothing else: the previous revision's head and tail matched it exactly,
so the older revision could be restored verbatim. That was luck. The
failure this pins is the one that would not have been:
`vault_append_path`, `nova_capture` and `vault_update_frontmatter_batch`
all read a file, modify it, and write it back. A silent truncation on
the read makes the truncation permanent on the write, and the next
reader sees a smaller file with no history of ever having been bigger.

So the rule: a partial read is not a read. `vault_read_path` raises,
`vault_bulk_fetch` omits the file and logs it. The two differ on purpose
-- bulk fetch feeds the journal page and seven search tools, and one
damaged file should not blank a listing of several hundred. That is the
same trade the failed-batch branch beside it already makes.

`VaultIncompleteDocument` extends `RuntimeError` so `nova_site`'s
existing handler turns it into a 502 naming the missing chunks, rather
than rendering the splice.
"""
import json
import urllib.parse

import pytest

from agora_runner import vault

WHOLE = "notes/whole.md"
HOLED = "notes/holed.md"


def _doc(doc_id, chunk_ids):
    return {
        "_id": doc_id,
        "path": doc_id,
        "data": "",
        "children": list(chunk_ids),
        "type": "plain",
        "_rev": "7-abc",
    }


# `holed.md` is the shape of the real incident in miniature: the middle
# chunk is referenced by the file doc and absent from the database, so
# the head and tail would otherwise splice into "one two |five six" --
# a sentence that reads as if nothing were wrong.
DOCS = {
    WHOLE: _doc(WHOLE, ["h:a", "h:b"]),
    HOLED: _doc(HOLED, ["h:a", "h:missing", "h:b"]),
    "h:a": {"_id": "h:a", "data": "one two ", "type": "leaf"},
    "h:b": {"_id": "h:b", "data": "|five six", "type": "leaf"},
}


def _all_docs_rows(path, ids):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    start = json.loads(query.get("startkey", ['""'])[0])
    end = json.loads(query.get("endkey", ['"\U0010FFFF"'])[0])
    return [{"id": i} for i in sorted(ids) if start <= i <= end]


@pytest.fixture
def couch(monkeypatch):
    """CouchDB with one chunk genuinely absent -- 404 on GET, and omitted
    from the `_all_docs` reply, which is exactly how a missing chunk
    presents on the real database."""
    def fake_couch_req(method, path, body=None):
        if "_all_docs" in path and method == "GET":
            return 200, {"rows": _all_docs_rows(path, DOCS)}
        if "_all_docs?include_docs=true" in path and method == "POST":
            return 200, {
                "rows": [{"id": k, "doc": DOCS[k]} for k in body["keys"] if k in DOCS]
            }
        for doc_id, doc in DOCS.items():
            if path.endswith(urllib.parse.quote(doc_id, safe="")) or path.endswith(doc_id):
                return 200, doc
        return 404, {}

    monkeypatch.setattr(vault, "couch_req", fake_couch_req)


def test_an_intact_document_still_reads(couch):
    """The control. Without it every assertion below would pass just as
    well against a fake that served nothing at all -- which is the exact
    mistake that made three earlier checks in this repo worthless."""
    assert vault.vault_read_path(WHOLE) == "one two |five six"


def test_reading_a_document_with_a_missing_chunk_raises(couch):
    with pytest.raises(vault.VaultIncompleteDocument) as excinfo:
        vault.vault_read_path(HOLED)
    message = str(excinfo.value)
    # The message has to name the file and the missing id, because the
    # next thing a human does is go looking for it in CouchDB.
    assert HOLED in message
    assert "h:missing" in message
    assert "1 of 3" in message


def test_the_spliced_text_is_never_returned(couch):
    """The specific danger, stated as itself: the concatenation of the
    surviving chunks is plausible prose and must not escape as a value."""
    with pytest.raises(vault.VaultIncompleteDocument):
        vault.vault_read_path(HOLED)


def test_it_is_a_runtime_error_so_the_site_reports_it_as_502(couch):
    """nova_site already catches RuntimeError out of vault_read_path and
    answers 502 with the message. Subclassing is what makes the owner see
    the damage described instead of rendered."""
    assert issubclass(vault.VaultIncompleteDocument, RuntimeError)


def test_bulk_fetch_omits_the_damaged_file_but_keeps_the_rest(couch):
    fetched = vault.vault_bulk_fetch("notes/")
    assert list(fetched) == [WHOLE]
    assert fetched[WHOLE] == "one two |five six"


def test_bulk_fetch_says_out_loud_what_it_dropped(couch, monkeypatch):
    """Omitting quietly is the bug one layer along: a journal page that
    silently loses an entry looks identical to one that never had it."""
    lines = []
    monkeypatch.setattr(vault, "log", lines.append)
    vault.vault_bulk_fetch("notes/")
    assert any(HOLED in line and "h:missing" in line for line in lines)


# ---------------------------------------------------------------------------
# The two places that must NOT be stopped by the raise. Both were found by a
# reviewer reading this diff, not by the author, and both convert the fix
# into a worse bug than the one it repairs.
# ---------------------------------------------------------------------------


def test_a_damaged_file_does_not_kill_a_heartbeat(couch):
    """fetch_vault_context builds the prompt for every heartbeat, including
    Nova's own cycle, and is called before run_heartbeat's try block on a
    bare daemon thread. An exception escaping it takes the thread down with
    no reply posted, no audit chip, and lastResult stuck on "running" --
    silent in a way the splice never was. It degrades per-file instead."""
    context = vault.fetch_vault_context([WHOLE, HOLED])
    assert "one two |five six" in context
    assert HOLED in context
    assert "h:missing" in context
    assert "unreadable" in context


def test_the_audit_read_never_blocks_a_repairing_overwrite(couch, monkeypatch):
    """`vault_write` is a full overwrite, which is precisely how a damaged
    file gets fixed. Its pre-read exists only to give the Activity feed a
    before/after diff, so raising there would have put the audit log between
    a persona and the repair."""
    from agora_runner import tools_dispatch
    monkeypatch.setattr(tools_dispatch, "vault_read_path", vault.vault_read_path)
    before = tools_dispatch._before_snapshot(HOLED)
    assert "h:missing" in before
    assert "one two |five six" not in before


def test_a_listing_only_read_keeps_the_damaged_file_and_costs_no_chunks(couch, monkeypatch):
    """`vault_bulk_list` answers "which files exist and when were they
    written" from the file docs alone, and the difference from
    `vault_bulk_fetch` is not only cost.

    Dropping a chunk-damaged file is right when the caller wanted its
    text -- half a note is not the note. It is wrong when the caller
    wanted the *set of filenames*, and `cycle_health` is exactly that
    caller: it reads a hole in the run of cycle numbers as "that cycle
    woke and wrote nothing". Feed it a fetch and one lost chunk turns a
    cycle that wrote its entry perfectly well into a reported failure --
    a confident false claim, from the file whose content nobody asked
    for. This is the real incident's shape (`ideas.md`, 6 chunks of 184)
    pointed at a different reader.
    """
    posts = []
    real = vault.couch_req

    def counting(method, path, body=None):
        if method == "POST" and "include_docs" in path:
            posts.append(sorted(body["keys"]))
        return real(method, path, body)

    monkeypatch.setattr(vault, "couch_req", counting)
    paths, mtimes = vault.vault_bulk_list("notes/")
    listing_posts = list(posts)

    assert sorted(paths) == sorted([WHOLE, HOLED])
    assert sorted(mtimes) == sorted([WHOLE, HOLED])
    # One batch, for the file docs. No chunk ids anywhere in it -- the
    # bodies are what this call exists not to pay for.
    assert len(listing_posts) == 1
    assert not any(key.startswith("h:") for key in listing_posts[0])
    # Same fixture, the other reader: the fetch drops the damaged file, so
    # the two assertions above are this function's behaviour and not the
    # fake quietly serving something intact.
    assert sorted(vault.vault_bulk_fetch("notes/")) == [WHOLE]
    assert any(key.startswith("h:") for batch in posts for key in batch)
