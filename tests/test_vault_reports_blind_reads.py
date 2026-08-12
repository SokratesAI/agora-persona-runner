"""A vault read that failed no longer looks like an empty vault.

The failure, in full, because it has now been shipped twice. `_vault_file_docs`
loses a database, logs a line nobody reads, and returns the rows it did get.
`vault_bulk_fetch` passes that on. Every tool downstream then answers from an
empty dict -- and an empty dict has no search matches, no stub notes, no
duplicate titles and no gaps in it, so all of them report a clean result while
meaning "I read nothing". Cycle 136 shipped a loop health check that said the
loop was perfectly healthy an hour after the journal folder skipped a cycle;
it had read zero entries and printed nothing, which is the most reassuring
possible way to be wrong. That one caller was patched by counting entries.
This pins the general shape instead: the read itself carries back what it
could not see, so the next caller does not have to rediscover the trap.

Continuing on a partial read stays the behaviour -- one unreachable database
must not blank the website's journal page. Only the silence is fixed.
"""
from unittest.mock import patch

from agora_runner import vault


HIS_FILE = "projects/sokrates/projects/nova/issues.md"


def _routing_on():
    return patch.object(vault, "COUCHDB_NOVA_DB", "nova")


def _one_db_down(status=503):
    """Nova's database refuses everything; Edvard's answers with one file."""
    def fake_couch_req(method, path, body=None):
        if path.startswith("nova/"):
            return status, {}
        if "include_docs=true" in path:
            return 200, {"rows": [{"id": HIS_FILE, "doc": {
                "_id": HIS_FILE, "path": HIS_FILE, "data": "# Issues\n"}}]}
        return 200, {"rows": [{"id": HIS_FILE}]}
    return patch.object(vault, "couch_req", fake_couch_req)


def _everything_down(status=401):
    def fake_couch_req(method, path, body=None):
        return status, {}
    return patch.object(vault, "couch_req", fake_couch_req)


def _healthy():
    def fake_couch_req(method, path, body=None):
        if "include_docs=true" in path:
            return 200, {"rows": [{"id": HIS_FILE, "doc": {
                "_id": HIS_FILE, "path": HIS_FILE, "data": "# Issues\nbody\n"}}]}
        return 200, {"rows": [{"id": HIS_FILE}]}
    return patch.object(vault, "couch_req", fake_couch_req)


def test_a_partial_read_says_which_database_it_lost():
    """The rows that were read are still returned -- the site keeps working --
    but the result now knows it is partial, and names the database and the
    status code so a reader can act on it."""
    with _routing_on(), _one_db_down(503), patch.object(vault, "log", lambda m: None):
        files = vault.vault_bulk_fetch("projects/")
    assert files[HIS_FILE] == "# Issues\n"
    assert len(files.unreadable) == 1
    assert "nova" in files.unreadable[0]
    assert "503" in files.unreadable[0]


def test_a_healthy_read_carries_nothing():
    """The flag has to be empty on the happy path or every tool below it
    grows a permanent warning banner."""
    with _routing_on(), _healthy():
        files = vault.vault_bulk_fetch("projects/")
    assert files.unreadable == []
    assert vault.unreadable_note(files, "vault_search") == ""


def test_with_mtimes_keeps_the_flag_on_the_mapping():
    """`cycle_health` asks for both; the flag rides on the contents, since
    the two are never read apart."""
    with _routing_on(), _one_db_down(503), patch.object(vault, "log", lambda m: None):
        files, mtimes = vault.vault_bulk_fetch("projects/", with_mtimes=True)
    assert files.unreadable
    assert isinstance(mtimes, dict)


def test_search_does_not_report_no_matches_when_it_could_not_look():
    """The exact lie this exists to stop: "no matches for X" is a conclusion
    an agent writes into its permanent memory."""
    with _routing_on(), _everything_down(401), patch.object(vault, "log", lambda m: None):
        out = vault.vault_search("anything", prefix="projects/")
    assert "INCOMPLETE READ" in out
    assert "401" in out


def test_a_partial_search_returns_its_hits_and_the_warning():
    """Both halves. The files that were read are real and must still be
    handed over; suppressing them would trade one silent failure for another."""
    with _routing_on(), _one_db_down(503), patch.object(vault, "log", lambda m: None):
        out = vault.vault_search("Issues", prefix="projects/")
    assert "INCOMPLETE READ" in out
    assert HIS_FILE in out


def test_the_finders_and_metrics_stop_certifying_an_unread_vault():
    """"0 file(s) checked, no stubs found" and "no files under that prefix"
    are the same lie in three more places."""
    with _routing_on(), _everything_down(401), patch.object(vault, "log", lambda m: None):
        stubs = vault.vault_find_stub_notes("projects/")
        dupes = vault.vault_find_duplicate_titles("projects/")
        metrics = vault.vault_get_token_metrics("projects/")
        schema = vault.vault_validate_frontmatter_schema("projects/")
        query = vault.vault_query_frontmatter("type", prefix="projects/")
    for out in (stubs, dupes, metrics, schema, query):
        assert "INCOMPLETE READ" in out, out


def test_healthy_tool_output_is_unchanged():
    """The note is a prefix and nothing else moved, so a working vault reads
    exactly as it did before this file existed."""
    with _routing_on(), _healthy():
        assert vault.vault_search("nothingatall", "projects/").startswith("[vault_search: no matches")
        assert vault.vault_find_stub_notes("projects/").startswith("1 stub(s) out of 1:")
        assert vault.vault_get_token_metrics("projects/").startswith("1 file(s),")


def test_cycle_health_prints_the_reason_the_read_failed():
    """The caller that was patched by hand last cycle now gets told why, not
    just that. It guessed at credentials; the read knows the status code."""
    from agora_runner.cycle_health import describe, findings
    from datetime import datetime
    from agora_runner.config import OSLO

    report = findings([], {}, datetime.now(OSLO), unreadable=["listing failed on database 'nova' (401)"])
    line = describe(report)
    assert "cannot tell" in line
    assert "401" in line
    assert "nova" in line


def test_a_lost_entry_is_not_reported_as_a_cycle_that_wrote_nothing():
    """The reviewer's finding on the first draft of this fix, and it is the
    same bug one level up. A single entry document that loses its content
    chunks -- which has happened in production, to `ideas.md`, 6 of 184 --
    leaves the rest of the folder intact, so `entries` is healthy and only
    that one cycle number is absent. The check then said "cycle 131 ran and
    wrote no journal entry", which is a confident false claim about a cycle
    whose entry is sitting right there. The reason has to survive a read that
    was partial rather than empty."""
    from datetime import datetime
    from agora_runner.cycle_health import describe, findings
    from agora_runner.config import OSLO

    paths = ["j/130-cycle-130.md", "j/132-cycle-132.md", "j/133-cycle-133.md"]
    report = findings(
        paths, {}, datetime.now(OSLO),
        unreadable=["131-cycle-131.md omitted from bulk fetch -- 1 of 4 content chunks missing"],
    )
    assert report["entries"] == 3
    line = describe(report)
    assert "could not be read" in line
    assert "131-cycle-131.md" in line
    assert line.index("could not be read") < line.index("wrote no journal entry")


def test_the_listing_tool_stops_saying_no_files_when_it_could_not_look():
    """`sorted()` on the mapping returns a plain list and dropped the flag, so
    `vault_list` was left exactly as blind as before -- "[no files under that
    prefix]" for a database that refused to answer."""
    from agora_runner import tools_dispatch

    with _routing_on(), _everything_down(401), patch.object(vault, "log", lambda m: None):
        paths = vault.vault_list_prefix("projects/")
        out = tools_dispatch.execute_tool(
            "vault_list", {"prefix": "projects/"}, {"name": "nova"}, "conv",
        )
    assert paths == []
    assert paths.unreadable
    assert "INCOMPLETE READ" in out, out
