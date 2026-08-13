"""Nova's files resolve to Nova's own CouchDB database (Cycle 118).

Edvard, 2026-08-11: "You have outgrown a poc project that is allowed to
use my Vault as a database. Move out and get your own space." Nova's 162
documents now live in a `nova` database; everything else stays in
`obsidian`. Which one answers is a pure function of the path, because a
LiveSync document id *is* the lowercased file path.

The failure this module exists to catch is not "wrong database" — that is
loud. It is the quiet one: chunk ids are content hashes with no path, so
a chunk lookup that falls back to the default database finds nothing and
the file reads back as VaultIncompleteDocument, or worse, empty.
"""
from unittest.mock import patch

from agora_runner import vault


NOVA_FILE = "projects/sokrates/projects/agora/nova/journal/118-cycle-118.md"
DIGEST = "projects/sokrates/projects/agora/journal-digest.md"
HIS_FILE = "projects/sokrates/projects/nova/issues.md"


def _routing_on():
    """COUCHDB_NOVA_DB is read as a module global, so patching it is what
    switching the feature on looks like from a test."""
    return patch.object(vault, "COUCHDB_NOVA_DB", "nova")


def test_routing_is_inert_until_the_database_is_named():
    """Unset is the shipped default: every path answers exactly as before,
    so merging this cannot move a single document."""
    with patch.object(vault, "COUCHDB_NOVA_DB", ""):
        for path in (NOVA_FILE, DIGEST, HIS_FILE, "", "anything/else.md"):
            assert vault.db_for(path) == vault.COUCHDB_DB
        for prefix in ("", "projects/", "projects/sokrates/projects/agora/nova/"):
            assert vault.dbs_for_prefix(prefix) == [vault.COUCHDB_DB]


def test_novas_own_paths_route_to_novas_database():
    with _routing_on():
        assert vault.db_for(NOVA_FILE) == "nova"
        assert vault.db_for(DIGEST) == "nova"
        assert vault.db_for("projects/sokrates/projects/agora/nova/resources/identity.md") == "nova"


def test_edvards_files_stay_in_his_vault():
    """He offered issues.md and ideas.md; they deliberately did not move
    database. Obsidian LiveSync may still write them and cannot see this
    rule."""
    with _routing_on():
        assert vault.db_for(HIS_FILE) == vault.COUCHDB_DB
        assert vault.db_for("projects/sokrates/projects/agora/architecture.md") == vault.COUCHDB_DB
        # The name is a prefix of Nova's folder without being inside it.
        assert vault.db_for("projects/sokrates/projects/agora/nova-notes.md") == vault.COUCHDB_DB


def test_the_nova_folder_in_edvards_own_vault_is_not_novas_database():
    """Two folders say "nova" and only one of them is Nova's.

    `projects/sokrates/projects/agora/nova/` is Nova's database;
    `projects/sokrates/projects/nova/` is a folder in Edvard's vault that
    he asked to keep, and on 2026-08-12 the three files he writes by hand
    moved into it -- *"they can be moved into the Nova folder in my Vault
    and not be underneath the agora project folder"*. Adding that second
    prefix to `NOVA_DB_FOLDERS` because it reads like Nova's would take
    every capture he types off his phone, and nothing else would notice:
    the boards would keep working, because the site reads through the
    same wrong rule that wrote them.
    """
    with _routing_on():
        for name in ("issues.md", "ideas.md", "notes.md", "nova.md"):
            path = "projects/sokrates/projects/nova/" + name
            assert vault.db_for(path) == vault.COUCHDB_DB, path
        assert vault.dbs_for_prefix(
            "projects/sokrates/projects/nova/") == [vault.COUCHDB_DB]


def test_every_capture_target_lands_in_edvards_database():
    """The pin that survives a rename. The test above names paths; this
    one asks the module that actually decides, so moving a capture file
    into a folder that routes to Nova cannot pass by being consistent
    with itself."""
    from agora_runner.nova_capture import CAPTURE_TARGETS

    with _routing_on():
        for kind, path in CAPTURE_TARGETS.items():
            assert vault.db_for(path) == vault.COUCHDB_DB, f"{kind} -> {path}"


def test_case_is_not_a_way_out_of_the_routing_rule():
    """Vault paths are lowercased everywhere; a caller that forgets must
    not thereby write Nova's journal into Edvard's database."""
    with _routing_on():
        assert vault.db_for(NOVA_FILE.upper()) == "nova"


def test_an_ancestor_prefix_queries_both_databases():
    """A whole-vault listing that asks only `obsidian` silently loses all
    162 of Nova's files, and looks completely healthy doing it."""
    with _routing_on():
        assert vault.dbs_for_prefix("") == [vault.COUCHDB_DB, "nova"]
        assert vault.dbs_for_prefix("projects/") == [vault.COUCHDB_DB, "nova"]
        assert vault.dbs_for_prefix("projects/sokrates/projects/agora/") == [
            vault.COUCHDB_DB, "nova"]


def test_a_prefix_inside_nova_queries_only_nova():
    with _routing_on():
        assert vault.dbs_for_prefix(
            "projects/sokrates/projects/agora/nova/journal/") == ["nova"]


def test_an_unrelated_prefix_never_touches_novas_database():
    with _routing_on():
        assert vault.dbs_for_prefix("resources/") == [vault.COUCHDB_DB]
        assert vault.dbs_for_prefix("projects/other/") == [vault.COUCHDB_DB]


def test_chunks_are_fetched_from_the_database_holding_their_file():
    """The quiet failure. `vault_assemble` gets a doc and a path; the
    chunks it points at exist only in that file's own database."""
    seen = []

    def fake_couch_req(method, path, body=None):
        seen.append(path)
        return 200, {"rows": [{"key": "h:aaa", "doc": {"data": "hello"}}]}

    doc = {"_id": NOVA_FILE, "path": NOVA_FILE, "children": ["h:aaa"]}
    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        assert vault.vault_assemble(doc, NOVA_FILE) == "hello"
    assert seen and seen[0].startswith("nova/"), seen


def test_chunks_for_edvards_files_still_come_from_his_vault():
    seen = []

    def fake_couch_req(method, path, body=None):
        seen.append(path)
        return 200, {"rows": [{"key": "h:bbb", "doc": {"data": "his"}}]}

    doc = {"_id": HIS_FILE, "path": HIS_FILE, "children": ["h:bbb"]}
    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        assert vault.vault_assemble(doc, HIS_FILE) == "his"
    assert seen and seen[0].startswith(f"{vault.COUCHDB_DB}/"), seen


def test_assemble_routes_from_the_doc_when_no_path_is_passed():
    """`vault_bulk_fetch` and friends hand over a doc with no separate
    path argument; the doc's own id is the routing key."""
    seen = []

    def fake_couch_req(method, path, body=None):
        seen.append(path)
        return 200, {"rows": [{"key": "h:ccc", "doc": {"data": "x"}}]}

    doc = {"_id": NOVA_FILE, "path": NOVA_FILE, "children": ["h:ccc"]}
    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        vault.vault_assemble(doc)
    assert seen and seen[0].startswith("nova/"), seen


def test_a_write_puts_both_chunks_and_doc_in_the_same_database():
    """A doc in one database pointing at chunks in another is exactly the
    VaultIncompleteDocument corruption, authored on the write side."""
    puts = []

    def fake_couch_req(method, path, body=None):
        if method == "PUT":
            puts.append(path)
            return 201, {}
        if method == "GET":
            return 404, {}          # no existing doc: this is a fresh write
        return 200, {"rows": []}    # no chunk already present

    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        vault._vault_put_raw(NOVA_FILE, "# entry\n\nsome content\n")
    assert puts, "nothing was written"
    assert all(p.startswith("nova/") for p in puts), puts


def test_a_write_to_edvards_vault_is_unaffected():
    puts = []

    def fake_couch_req(method, path, body=None):
        if method == "PUT":
            puts.append(path)
            return 201, {}
        if method == "GET":
            return 404, {}          # no existing doc: this is a fresh write
        return 200, {"rows": []}    # no chunk already present

    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        vault._vault_put_raw(HIS_FILE, "- a capture\n")
    assert puts and all(p.startswith(f"{vault.COUCHDB_DB}/") for p in puts), puts


def test_listing_an_ancestor_prefix_merges_both_databases():
    """The union has to be real, not just two queries issued."""
    def fake_couch_req(method, path, body=None):
        if path.startswith("nova/"):
            return 200, {"rows": [{"id": NOVA_FILE, "value": {}}]}
        return 200, {"rows": [{"id": HIS_FILE, "value": {}}]}

    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        ids = vault.vault_list_ids("projects/")
    assert ids == sorted([HIS_FILE, NOVA_FILE])


# --- Findings from the Cycle 118 reviewer, fixed in a follow-up ---------

def test_a_file_merely_starting_with_the_digests_name_is_not_the_digest():
    """`journal-digest.md` is one exact file, not a prefix. Matching it with
    startswith routed `journal-digest.md.bak` — a file Edvard owns — into
    Nova's database."""
    with _routing_on():
        assert vault.db_for(DIGEST) == "nova"
        for impostor in (DIGEST + ".bak", DIGEST + "-old.md", DIGEST + "2"):
            assert vault.db_for(impostor) == vault.COUCHDB_DB, impostor


def test_a_prefix_equal_to_a_single_file_queries_both_databases():
    """As a prefix that path also matches its neighbours, which live in the
    other database, so the conservative answer is both."""
    with _routing_on():
        assert vault.dbs_for_prefix(DIGEST) == [vault.COUCHDB_DB, "nova"]


def test_chunk_helpers_require_a_database():
    """They used to default to COUCHDB_DB. A caller that forgot would send
    Nova's chunk ids to Edvard's database and get VaultIncompleteDocument
    on a file that is perfectly intact."""
    import pytest
    with pytest.raises(TypeError):
        vault._fetch_chunks(["h:aaa"])
    with pytest.raises(TypeError):
        vault._existing_chunk_ids(["h:aaa"])


def test_bulk_fetch_uses_the_database_a_doc_actually_came_from():
    """Not the one db_for predicts. They agree in steady state and disagree
    mid-migration — precisely when a wrong answer costs something."""
    asked = []

    def fake_couch_req(method, path, body=None):
        if "_all_docs?include_docs=true" in path and body and "keys" in body:
            if body["keys"] and body["keys"][0].startswith("h:"):
                asked.append(path.split("/")[0])
                return 200, {"rows": [{"id": "h:zzz", "doc": {"data": "content"}}]}
            return 200, {"rows": [{"id": NOVA_FILE, "doc": {
                "_id": NOVA_FILE, "path": NOVA_FILE, "children": ["h:zzz"]}}]}
        # First-phase id listing: only Edvard's database answers, so the
        # doc is found there despite db_for saying it belongs to nova.
        if path.startswith(f"{vault.COUCHDB_DB}/_all_docs?"):
            return 200, {"rows": [{"id": NOVA_FILE}]}
        return 200, {"rows": []}

    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        out = vault.vault_bulk_fetch("projects/")
    assert out.get(NOVA_FILE) == "content", out
    assert asked == [vault.COUCHDB_DB], asked


def test_a_failing_database_is_logged_not_swallowed():
    """One database down now leaves the other's rows in place, so the caller
    gets a partial listing that looks healthy. It must at least say so."""
    lines = []

    def fake_couch_req(method, path, body=None):
        if path.startswith("nova/"):
            return 503, {}
        return 200, {"rows": [{"id": HIS_FILE}]}

    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req), \
            patch.object(vault, "log", lambda m: lines.append(m)):
        vault.vault_list_ids("projects/")
    assert any("nova" in m and "503" in m for m in lines), lines


def test_assemble_prefers_the_database_the_doc_was_actually_read_from():
    """The drift the second reader found on #152, fixed in Cycle 169.

    `_vault_file_docs` stamps `_SRC_DB_KEY` with the database each doc
    really came out of, and `vault_bulk_fetch` has honoured it since Cycle
    121 -- but `vault_assemble` recomputed the route from the path
    instead, while the bridge's `assemble` preferred the stamp. The two
    agree in steady state and disagree during a migration, which is
    exactly when a doc's chunks would be looked up in a database that does
    not hold them. A chunk that is merely in the other database is
    indistinguishable from one that was never written, so an intact file
    reports itself corrupt.
    """
    seen = []

    def fake_couch_req(method, path, body=None):
        seen.append(path)
        return 200, {"rows": [{"key": "h:ddd", "doc": {"data": "moved"}}]}

    # A Nova-owned path whose doc was read out of Edvard's database, which
    # is what a half-finished migration looks like. Routing by path says
    # `nova`; the stamp says where the chunks really are.
    doc = {"_id": NOVA_FILE, "path": NOVA_FILE, "children": ["h:ddd"],
           vault._SRC_DB_KEY: vault.COUCHDB_DB}
    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        assert vault.vault_assemble(doc, NOVA_FILE) == "moved"
    assert seen and seen[0].startswith(f"{vault.COUCHDB_DB}/"), seen


def test_an_explicit_database_beats_both_the_stamp_and_the_path():
    """The parameter exists so a caller that already knows is not second
    guessed -- same signature as the bridge's `assemble`. Separate test
    from the one above because they are separate branches of one `or`
    chain, and a test named for the stamp that actually passes on the
    parameter pins neither."""
    seen = []

    def fake_couch_req(method, path, body=None):
        seen.append(path)
        return 200, {"rows": [{"key": "h:eee", "doc": {"data": "explicit"}}]}

    doc = {"_id": HIS_FILE, "path": HIS_FILE, "children": ["h:eee"],
           vault._SRC_DB_KEY: vault.COUCHDB_DB}
    with _routing_on(), patch.object(vault, "couch_req", fake_couch_req):
        assert vault.vault_assemble(doc, HIS_FILE, db="nova") == "explicit"
    assert seen and seen[0].startswith("nova/"), seen
