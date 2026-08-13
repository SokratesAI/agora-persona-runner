"""A database that will not answer must not read as a file that is not there.

Why this exists (2026-08-13). `vault_read_path_rev` collapsed every
non-200 from CouchDB into `(None, None)`. 404 means the document does not
exist; 500, 503 and 401 mean the database did not answer. One return
value for both, and every caller on the site turns "does not exist" into
`""` via `or ""` and renders the empty result.

So a CouchDB that was overloaded, mid-failure, or holding rotated
credentials showed up as: a comments board with no comments, a digest
with no lines, a costs page with nothing on it, and empty issue and idea
boards. No error anywhere. That is the same failure #147 deleted from the
journal one day earlier -- the wrong answer a reader cannot tell from the
truth -- surviving one layer down because the journal was fixed through
`vault_bulk_fetch.unreadable`, a channel that only ever existed for the
*folder* read. `journal_markdown` raising while `comments_markdown`
returned `""` was never a considered difference. It was the only one of
the two with a way to tell.

The whole suite (1737 tests) passed both before and after the fix, which
is the part worth keeping: nothing here could distinguish a 500 from a
404 in either direction, so the old behaviour was unpinned and the new
one would have been too. Every test below fails if
`vault_read_path_rev` goes back to `if status != 200: return None, None`.

404 deliberately stays `(None, None)`. It is a real answer -- the first
comment ever, an archive that has not been rolled yet -- and the tombstone
case beside it is distinguished by its `rev`, not by its content.
"""
import urllib.parse

import pytest

from agora_runner import nova_sources, vault

PRESENT = "notes/present.md"
ABSENT = "notes/absent.md"


def _couch(status_for):
    """A fake CouchDB that answers each doc id with whatever `status_for`
    says, so one fixture covers 200, 404 and every failure code."""
    def fake_couch_req(method, path, body=None, timeout=60):
        for doc_id, outcome in status_for.items():
            quoted = urllib.parse.quote(doc_id, safe="")
            if path.endswith(quoted) or path.endswith(doc_id):
                return outcome
        return 404, {}

    return fake_couch_req


@pytest.fixture
def couch(monkeypatch):
    def install(status_for):
        monkeypatch.setattr(vault, "couch_req", _couch(status_for))

    return install


def test_a_readable_document_still_reads(couch):
    """The control. Without it every assertion below would pass against a
    fake that failed on everything -- the mistake this repo has made three
    times and now writes a control for every time."""
    couch({PRESENT: (200, {"_id": PRESENT, "data": "hello", "_rev": "3-abc"})})
    assert vault.vault_read_path(PRESENT) == "hello"
    assert vault.vault_read_path_rev(PRESENT) == ("hello", "3-abc")


def test_a_missing_document_is_still_absent_not_an_error(couch):
    """404 must keep working. `add_comment` creates the file on this
    branch and `digest_markdown` skips a not-yet-rolled archive on it, so
    turning absence into a raise would break both."""
    couch({ABSENT: (404, {})})
    assert vault.vault_read_path_rev(ABSENT) == (None, None)


@pytest.mark.parametrize("status", [500, 502, 503, 401, 403])
def test_an_unreadable_document_raises_instead_of_reading_as_missing(couch, status):
    """The four that actually happen: an overloaded or compacting CouchDB
    (500/503), a proxy in the way (502), and credentials that rotated
    without this process noticing (401/403)."""
    couch({PRESENT: (status, {"error": "boom"})})
    with pytest.raises(vault.VaultUnreadableDocument) as excinfo:
        vault.vault_read_path(PRESENT)
    message = str(excinfo.value)
    # The message has to carry both, because the next thing a human does
    # is decide whether the file or the database is the problem.
    assert PRESENT in message
    assert str(status) in message


def test_the_error_is_a_runtimeerror_so_the_site_reports_it(couch):
    """`nova_site`'s handlers catch `Exception` and send a 502 carrying
    `str(e)`. That is what turns this into a page saying what broke rather
    than a page saying nothing happened, and it is inherited, not
    re-implemented -- same as `VaultIncompleteDocument` beside it."""
    assert issubclass(vault.VaultUnreadableDocument, RuntimeError)


# The four site surfaces that were rendering an outage as emptiness. Each
# is the `or ""` at the end of its own reader in `nova_sources`.
@pytest.mark.parametrize(
    "reader, path_attr",
    [
        ("comments_markdown", "COMMENTS_PATH"),
        ("digest_markdown", "DIGEST_PATH"),
        ("cost_ledger_json", "COST_LEDGER_PATH"),
    ],
)
def test_a_site_reader_no_longer_turns_an_outage_into_an_empty_page(
    couch, monkeypatch, reader, path_attr
):
    path = getattr(nova_sources, path_attr)
    couch({path: (503, {"error": "unavailable"})})
    with pytest.raises(vault.VaultUnreadableDocument):
        getattr(nova_sources, reader)()


def test_the_boards_page_stops_short_too(couch):
    """`board_markdown` reads three files. Any one of them unreadable has
    to stop the page, not silently drop a tab to empty."""
    couch({nova_sources.BOARD_PATHS["issues"]["edvard"]: (500, {})})
    with pytest.raises(vault.VaultUnreadableDocument):
        nova_sources.board_markdown("issues")


def test_the_audit_read_never_blocks_a_repairing_overwrite(couch, monkeypatch):
    """The one caller where raising would be *worse* than the old silent
    `None`, and the one I nearly shipped broken.

    `vault_write` is a full overwrite, which is how a persona repairs a
    damaged file. Its pre-read exists only to give the Activity feed a
    before/after diff, and `_before_snapshot` already caught
    `VaultIncompleteDocument` so the audit log could never stand between a
    persona and the repair. A new failed-read exception that escaped that
    `except` would have put it there — for a database that is flaky on one
    read and fine on the write immediately after.

    Same reasoning as the matching test in
    `test_vault_refuses_partial_documents.py`; this is the other exception.
    """
    from agora_runner import tools_dispatch

    couch({PRESENT: (503, {"error": "unavailable"})})
    monkeypatch.setattr(tools_dispatch, "vault_read_path", vault.vault_read_path)
    before = tools_dispatch._before_snapshot(PRESENT)
    assert "unreadable" in before
    assert "503" in before


def test_a_missing_digest_archive_still_reads_as_no_archive(couch):
    """The other half of the boundary, and the one a careless fix breaks:
    `digest_markdown` joins two files and the archive legitimately does
    not exist on a fresh vault. That is a 404, so it must stay `""` and
    return the live file alone."""
    couch({
        nova_sources.DIGEST_PATH: (
            200, {"_id": nova_sources.DIGEST_PATH, "data": "## Digest\nline", "_rev": "1-a"}
        ),
        nova_sources.DIGEST_ARCHIVE_PATH: (404, {}),
    })
    assert nova_sources.digest_markdown() == "## Digest\nline"
