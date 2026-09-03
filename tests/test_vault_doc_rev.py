"""`vault_doc_rev` reads a revision without paying for the document.

`vault_read_path_rev` answers the same question and fetches the whole
file to do it -- 656KB on `ideas.md`. The ticket store's currency check
runs on every board payload build, so it needs the cheap form, and it
needs the same three-way split between absent, present and unreadable
that `vault_read_path_rev` makes: folding "CouchDB refused" into "the
file is missing" is the bug `VaultUnreadableDocument` exists to stop.
"""

import json
import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agora_runner import vault  # noqa: E402


PATH = "projects/sokrates/projects/nova/Issues.md"


def _asked(monkeypatch, status, body):
    calls = []

    def couch_req(method, path, body_=None, timeout=60):
        calls.append((method, path))
        return status, body

    monkeypatch.setattr(vault, "couch_req", couch_req)
    return calls


def test_it_returns_the_revision_of_the_row(monkeypatch):
    calls = _asked(monkeypatch, 200, {
        "rows": [{"id": PATH.lower(), "key": PATH.lower(),
                  "value": {"rev": "11-b386"}}]})
    assert vault.vault_doc_rev(PATH) == "11-b386"
    method, url = calls[0]
    assert method == "GET"
    # The whole point: `_all_docs` with one key, not a document read. A
    # `include_docs` here would fetch the bytes this exists to avoid.
    assert "_all_docs?" in url and "include_docs" not in url
    assert json.dumps([PATH.lower()]) in urllib.parse.unquote(url)


def test_the_path_is_lowercased_like_every_other_document_id(monkeypatch):
    calls = _asked(monkeypatch, 200, {"rows": []})
    vault.vault_doc_rev(PATH)
    assert "Issues" not in calls[0][1]


def test_a_missing_document_is_none_not_an_error(monkeypatch):
    _asked(monkeypatch, 200, {"rows": [{"key": PATH.lower(),
                                        "error": "not_found"}]})
    assert vault.vault_doc_rev(PATH) is None


def test_an_empty_answer_is_none(monkeypatch):
    _asked(monkeypatch, 200, {"rows": []})
    assert vault.vault_doc_rev(PATH) is None


def test_a_database_that_refuses_raises_rather_than_reading_as_absent(monkeypatch):
    _asked(monkeypatch, 500, {"error": "internal"})
    with pytest.raises(vault.VaultUnreadableDocument):
        vault.vault_doc_rev(PATH)


def test_a_401_raises_too(monkeypatch):
    # The bridge pod reads this database with different credentials, so an
    # unauthorised answer is a real shape here and must not come back as
    # "the file does not exist" -- that would report every board as
    # unstamped from that pod.
    _asked(monkeypatch, 401, {"error": "unauthorized"})
    with pytest.raises(vault.VaultUnreadableDocument):
        vault.vault_doc_rev(PATH)
