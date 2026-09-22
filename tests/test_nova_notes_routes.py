"""The /api/notes record routes (idea #333): who may write, and what comes back.

The store runs against the fake CouchDB the board store tests use, and the
requests go through the real handler classes, so the port a request arrived
on is decided the way it is in the pod: by which class served it.
"""

import json
from unittest.mock import patch

import pytest

from agora_runner import nova_notes_store as store, nova_site, ticket_docs
from tests.test_board_store import FakeCouch
from tests.test_nova_site import _FakeServer, _FakeSocket

HIM = "him@example.com"


@pytest.fixture(autouse=True)
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    with patch.object(nova_site.config, "NOVA_OWNER_LOGIN", HIM):
        yield fake


def _request(method, path, payload=None, login=HIM, owner_port=True):
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = f"Host: nova\r\n"
    if login is not None:
        headers += f"Tailscale-User-Login: {login}\r\n"
    if payload is not None:
        headers += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
    sock = _FakeSocket(f"{method} {path} HTTP/1.1\r\n{headers}\r\n".encode() + body)
    handler = nova_site.OwnerSiteHandler if owner_port else nova_site.NovaSiteHandler
    handler(sock, ("127.0.0.1", 50000), _FakeServer())
    head, _, response = sock.sent.getvalue().partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(response or b"null")


def _post(path, payload, **kw):
    return _request("POST", path, payload, **kw)


def test_his_login_on_the_owner_port_creates_a_note_under_his_name():
    text = "first paragraph\n\nsecond paragraph"
    status, answer = _post("/api/notes/create", {"text": text, "author": "sokrates"})
    assert status == 200
    # The body's `author` is ignored: the port decided who wrote it.
    assert answer["note"]["author"] == "Edvard"
    assert answer["note"]["text"] == text
    assert store.list_notes()[0]["author"] == "edvard"


@pytest.mark.parametrize("login, owner_port", [
    (HIM, False),               # 8083: any pod in agents can type his login
    ("someone@example.com", True),
    (None, True),
])
def test_a_caller_it_cannot_vouch_for_writes_nothing(login, owner_port):
    status, answer = _post("/api/notes/create", {"text": "x"}, login=login, owner_port=owner_port)
    assert status == 403
    assert store.list_notes() == []


def test_list_returns_notes_newest_first_with_their_comments():
    note = store.create_note("sokrates", "a note from the host")
    store.add_comment(note["_id"], "nova", "a reply")
    status, page = _request("GET", "/api/notes/records", login=None, owner_port=False)
    assert status == 200
    [shown] = page["notes"]
    assert shown["author"] == "Sokrates"
    assert [(c["author"], c["text"]) for c in shown["comments"]] == [("Nova", "a reply")]


def test_edit_archive_and_delete_need_the_rev_the_page_read():
    _, created = _post("/api/notes/create", {"text": "v1"})
    note = created["note"]
    status, _ = _post("/api/notes/edit", {"id": note["id"], "text": "v2"})
    assert status == 400  # no rev: refused, not applied
    status, edited = _post("/api/notes/edit", {"id": note["id"], "rev": note["rev"], "text": "v2"})
    assert status == 200 and edited["note"]["text"] == "v2"
    # The tab that still holds the first rev is told, not obeyed.
    status, _ = _post("/api/notes/edit", {"id": note["id"], "rev": note["rev"], "text": "stale"})
    assert status == 409
    status, archived = _post("/api/notes/archive", {"id": note["id"], "rev": edited["note"]["rev"]})
    assert status == 200 and archived["note"]["archived"] is True
    _, page = _request("GET", "/api/notes/records?archived=1")
    assert [n["id"] for n in page["notes"]] == [note["id"]]
    status, _ = _post("/api/notes/archive", {"id": note["id"], "rev": archived["note"]["rev"], "archived": False})
    assert status == 200 and store.list_notes()[0]["archived"] is False
    rev = store.read_note(note["id"])["_rev"]
    status, answer = _post("/api/notes/delete", {"id": note["id"], "rev": rev})
    assert status == 200 and answer == {"deleted": note["id"]}
    assert store.read_note(note["id"]) is None


def test_a_comment_is_signed_by_the_port_and_a_missing_note_is_a_404():
    note = store.create_note("nova", "a note of mine")
    status, answer = _post("/api/notes/comment", {"id": note["_id"], "text": "his reply", "author": "nova"})
    assert status == 200 and answer["comment"]["author"] == "Edvard"
    status, _ = _post("/api/notes/comment", {"id": "note:ffffff", "text": "x"})
    assert status == 404
    status, _ = _post("/api/notes/comment", {"id": "board:1", "text": "x"})
    assert status == 400


def test_the_capture_box_writes_a_note_record_signed_by_the_port():
    status, answer = _post("/api/capture", {"target": "notes", "text": "from the box"})
    assert (status, answer["ok"]) == (200, True)
    assert [(d["author"], d["text"]) for d in store.list_notes()] == [("edvard", "from the box")]


def test_the_capture_box_refuses_a_note_it_cannot_sign():
    status, answer = _post("/api/capture", {"target": "notes", "text": "x"}, owner_port=False)
    assert (status, answer["ok"]) == (403, False)
    assert store.list_notes() == []


def test_a_capture_converted_to_a_note_becomes_a_record_not_a_notes_md_line():
    # `notes.md` is read by nothing a cycle opens any more, so a capture
    # moved there would leave his board for a file nobody looks at.
    with patch.object(nova_site, "amend", return_value=(True, "ok")) as amend, \
            patch.object(nova_site, "convert_capture") as conv:
        status, answer = _post("/api/capture/convert", {
            "from": "issues", "to": "notes", "index": 2, "original": "🟠 High: actually a note"})
    assert (status, answer["ok"]) == (200, True)
    conv.assert_not_called()
    amend.assert_called_once_with("issues", 2, "🟠 High: actually a note", "")
    assert [(d["author"], d["text"]) for d in store.list_notes()] == [("edvard", "actually a note")]


def test_a_convert_to_a_note_it_cannot_sign_touches_neither_side():
    with patch.object(nova_site, "amend") as amend:
        status, answer = _post("/api/capture/convert", {
            "from": "issues", "to": "notes", "index": 0, "original": "x"}, owner_port=False)
    assert (status, answer["ok"]) == (403, False)
    amend.assert_not_called()
    assert store.list_notes() == []


def test_a_convert_to_a_note_whose_removal_failed_says_it_is_in_both():
    with patch.object(nova_site, "amend", return_value=(False, "the write failed")):
        status, answer = _post("/api/capture/convert", {
            "from": "ideas", "to": "notes", "index": 0, "original": "x"})
    assert status == 502 and "in both" in answer["message"]
    with patch.object(nova_site, "amend", return_value=(False, "that capture is no longer in the list")):
        status, answer = _post("/api/capture/convert", {
            "from": "ideas", "to": "notes", "index": 0, "original": "y"})
    assert status == 502 and "duplicate" in answer["message"]
    assert len(store.list_notes()) == 2


def test_every_note_write_is_audited_whether_or_not_it_landed(monkeypatch):
    # nova-site logs no request lines, so the audit is the only place a
    # cycle can see that his phone tried to write a note and what came back.
    seen = []
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: seen.append((a, k)))
    _post("/api/notes/create", {"text": "kept"})
    _post("/api/notes/create", {"text": "refused"}, login="someone@example.com")
    _post("/api/capture", {"target": "notes", "text": "from the box"})
    details = [a[3] for a, _ in seen]
    assert details == ["Note create · ok",
                       "Note create · 403 only the owner's own login, through the Tailscale proxy, can write a note",
                       "Note create · ok"]
    assert [k["output"] for _, k in seen] == [HIM, "someone@example.com", HIM]
    assert [k["is_error"] for _, k in seen] == [False, True, False]
    assert seen[0][1]["after"] == "kept"
    assert [n["text"] for n in store.list_notes()] and len(store.list_notes()) == 2
