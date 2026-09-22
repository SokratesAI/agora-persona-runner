"""tools.note_post: Sokrates' and Nova's writer into the notes store."""

import io

import pytest

from agora_runner import nova_notes_store as store, ticket_docs
from tests.test_board_store import FakeCouch
from tools import note_post


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


def test_a_note_is_stored_under_the_sender_named_on_the_command_line(couch, capsys):
    assert note_post.main(["--as", "sokrates", "--text", "server2 is back"]) == 0
    note_id = capsys.readouterr().out.strip()
    note = store.read_note(note_id)
    assert note["author"] == "sokrates" and note["text"] == "server2 is back"


def test_text_from_stdin_keeps_its_paragraph_breaks(couch, capsys, monkeypatch):
    text = "first\n\nsecond paragraph\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert note_post.main(["--as", "nova", "--text", "-"]) == 0
    assert store.read_note(capsys.readouterr().out.strip())["text"] == text


def test_on_posts_a_comment_on_that_note(couch, capsys):
    note = store.create_note("edvard", "question")
    assert note_post.main(["--as", "nova", "--on", note["_id"], "--text", "answer"]) == 0
    [comment] = store.read_comments(note["_id"])
    assert comment["author"] == "nova" and comment["text"] == "answer"


def test_the_owner_cannot_be_named_from_the_command_line(couch):
    with pytest.raises(SystemExit):
        note_post.main(["--as", "edvard", "--text", "x"])
    with pytest.raises(ValueError):
        note_post.post("edvard", "x")
    assert couch.docs == {}


def test_a_refused_write_exits_1_and_writes_nothing(couch, capsys):
    assert note_post.main(["--as", "nova", "--text", "  "]) == 1
    assert "refused" in capsys.readouterr().err and couch.docs == {}


def test_read_stamps_the_note_without_writing_anything_he_sees(couch, capsys):
    note = store.create_note("edvard", "a note")
    assert store.is_unread(note)
    assert note_post.main(["--as", "nova", "--read", note["_id"]]) == 0
    marked = store.read_note(note["_id"])
    assert not store.is_unread(marked)
    # His text, his `updated`, and no comment: the page draws nothing new.
    assert marked["text"] == "a note" and marked["updated"] == note["updated"]
    assert store.read_comments(note["_id"]) == []


def test_read_on_a_missing_note_exits_1(couch, capsys):
    assert note_post.main(["--as", "nova", "--read", "note:ffffff"]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_text_and_read_are_exclusive(couch):
    with pytest.raises(SystemExit):
        note_post.main(["--as", "nova"])
    with pytest.raises(SystemExit):
        note_post.main(["--as", "nova", "--text", "x", "--read", "note:abc123"])
