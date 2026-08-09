"""A file Edvard deleted must not still be readable by his agents.

Why this exists (2026-08-07): Obsidian LiveSync does not remove a
document when a note is deleted. It keeps the document, attaches its
content chunks, and sets `deleted: true` -- that flag is how other
clients learn to drop their local copy. Nothing in vault.py knew the
flag existed, so `_all_docs` kept listing tombstones and `couch_get_doc`
kept assembling their chunks back into text.

Measured on the live vault the day this was found: 309 of 897 documents
were tombstones. A third of everything the vault tools could see was
content that no longer exists. It was not junk, either -- the vault had
been *reorganised*, so the tombstones were the pre-move copies of real
files (`architecture.md`, the ADRs, `identity.md`) sitting at their old
paths, one prefix away from their live replacements and indistinguishable
from them.

The concrete damage: `kanban.md` was deleted outright on 2026-08-06 with
no replacement, and Nova's own `prompt.md` step 1 told every cycle to
read it as "Edvard's own real backlog". Four cycles a day were being
handed a board frozen on 2026-07-29 -- open PR numbers, a "Phase 9" that
had long since shipped -- and had no way to tell it from a live file.
That is worse than an empty result: a tool that returns nothing prompts
a question, and a tool that returns a stale board prompts confident,
wrong work.

So the rule these tests pin is: deleted means gone. `vault_read` says
not-found, `vault_list` omits it, and `vault_bulk_fetch` -- which feeds
the seven search/frontmatter/metrics tools -- never yields it. Recovery
is `vault_git_revision_history` against the daily GitHub mirror, which
is what that tool is for.

The last two tests are the pair that keeps this honest. Refusing to read
a tombstone is only correct if writing to that path still *works* --
otherwise a deleted note would become a permanently unusable filename.
So: append refuses (it needs something to append to), write resurrects.
"""
import json
import urllib.parse

import pytest

from agora_runner import vault

LIVE = "notes/live.md"
GONE = "notes/gone.md"


def _doc(doc_id, chunk_id, deleted=False):
    doc = {
        "_id": doc_id,
        "path": doc_id,
        "data": "",
        "children": [chunk_id],
        "type": "plain",
        "_rev": "4-abc",
    }
    if deleted:
        # LiveSync's tombstone: the flag goes on, the children stay put.
        # A tombstone that dropped its chunks would make every test here
        # pass for the wrong reason.
        doc["deleted"] = True
    return doc


DOCS = {
    LIVE: _doc(LIVE, "h:live"),
    GONE: _doc(GONE, "h:gone", deleted=True),
    "h:live": {"_id": "h:live", "data": "still here\n", "type": "leaf"},
    "h:gone": {"_id": "h:gone", "data": "deleted text\n", "type": "leaf"},
}


def _all_docs_rows(path, ids):
    """What a real `_all_docs` returns for this query, honouring
    `startkey`/`endkey`.

    The fakes used to answer every `_all_docs` GET with the whole
    dictionary, which meant a listing scoped to one folder and a listing
    of the entire vault were indistinguishable to them. `_vault_file_docs`
    now asks CouchDB for a key range instead of filtering client-side, so
    a wrong range would be invisible to a fake that ignores it -- and the
    failure mode is files silently missing from a listing, which is the
    exact bug tests/test_vault_hides_deleted_files.py exists about.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    start = json.loads(query.get("startkey", ['""'])[0])
    end = json.loads(query.get("endkey", ['"\U0010FFFF"'])[0])
    return [{"id": i} for i in sorted(ids) if start <= i <= end]


@pytest.fixture
def couch(monkeypatch):
    """A CouchDB stand-in that serves tombstones exactly like the real one
    does -- present in _all_docs, retrievable by id, chunks intact."""
    puts = []

    def fake_couch_req(method, path, body=None):
        if method == "PUT":
            puts.append(body)
            return 201, {"ok": True}
        if "_all_docs" in path and method == "GET":
            return 200, {"rows": _all_docs_rows(path, DOCS)}
        if "_all_docs?include_docs=true" in path and method == "POST":
            return 200, {
                "rows": [
                    {"id": k, "doc": DOCS[k]} for k in body["keys"] if k in DOCS
                ]
            }
        for doc_id, doc in DOCS.items():
            from urllib.parse import quote
            if path.endswith(quote(doc_id, safe="")) or path.endswith(doc_id):
                return 200, doc
        return 404, {}

    monkeypatch.setattr(vault, "couch_req", fake_couch_req)
    return puts


def test_reading_a_deleted_file_returns_not_found(couch):
    """The control matters as much as the assertion: if the fake served
    nothing at all, the first line would pass on its own."""
    assert vault.vault_read_path(LIVE) == "still here\n"
    assert vault.vault_read_path(GONE) is None


def test_listing_omits_deleted_files(couch):
    assert vault.vault_list_prefix("notes/") == [LIVE]


def test_bulk_fetch_omits_deleted_files(couch):
    """vault_search, vault_query_frontmatter, vault_find_stub_notes,
    vault_find_duplicate_titles, vault_get_token_metrics and
    vault_validate_frontmatter_schema all read through this one function,
    so filtering here is what keeps a deleted note out of six more tools.
    vault_update_frontmatter_batch reads through it too -- and then
    *writes* every file it matched, which would have resurrected every
    tombstone under the prefix it was pointed at."""
    fetched = vault.vault_bulk_fetch("notes/")
    assert list(fetched) == [LIVE]
    assert "deleted text\n" not in fetched.values()


def test_appending_to_a_deleted_file_refuses_instead_of_reviving_it(couch):
    """Before the flag was understood this silently succeeded: append read
    the tombstone's old text, glued the new content onto it, and wrote the
    whole thing back -- resurrecting a deleted note and making it look
    like it had never left."""
    result = vault.vault_append_path(GONE, "new line")

    assert result.startswith("FAILED(not found")
    assert not couch, f"a refused append still wrote to the vault: {couch}"


def test_writing_to_a_deleted_path_recreates_the_file(couch):
    """The other half of the pair. Deleting a note must not burn its path
    forever, so `vault_write` on a tombstone is a create: it reuses the
    doc's `_rev` (CouchDB requires that) but emits no `deleted` key, which
    is what clears the tombstone."""
    assert vault.vault_write_path(GONE, "brand new content") == "written"

    filedoc = [p for p in couch if p["_id"] == GONE]
    assert len(filedoc) == 1, f"expected one file-doc PUT, got {couch}"
    assert "deleted" not in filedoc[0], (
        "the rewritten document is still flagged deleted, so the file "
        f"stays invisible in Obsidian: {filedoc[0]}"
    )


def test_the_key_range_covers_a_filename_that_starts_with_an_emoji():
    """`_all_docs` collates ids by raw UTF-8 bytes, so the endkey sentinel
    has to sort above every character a filename can start with.

    The common CouchDB idiom is `\\ufff0`, and it is wrong here: it
    encodes to EF BF B0, while an emoji encodes to F0 9F ... and would
    sort *above* it. A note called `🔥.md` would then be missing from
    every listing of its folder, which reads exactly like a deleted file
    -- the failure this whole module exists to prevent.
    """
    query = urllib.parse.parse_qs(vault._id_range("notes/"))
    start = json.loads(query["startkey"][0])
    end = json.loads(query["endkey"][0])
    assert start <= "notes/🔥.md" <= end
    assert start <= "notes/plain.md" <= end
    # ...and stops short of the folders either side of it. The endkey is
    # what excludes the one after; the startkey is the only thing
    # excluding the one before, and without that assertion a startkey of
    # `""` passes every other test in this suite -- the client-side
    # prefix filter still returns the right answer, it just makes CouchDB
    # read the whole database first, which is the entire point of the
    # range.
    assert not start <= "notesx/other.md" <= end
    assert not start <= "diary/other.md" <= end
