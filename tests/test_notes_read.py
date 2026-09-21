"""tools.notes_read prints the live notes a cycle has to read (idea #333)."""

from agora_runner import nova_notes_store as store, ticket_docs
from tests.test_board_store import FakeCouch
from tools import notes_read


def test_prints_live_notes_with_comments_and_leaves_archived_out(monkeypatch, capsys):
    monkeypatch.setattr(ticket_docs, "_req", FakeCouch())
    kept = store.create_note("edvard", "keep the symbols")
    store.add_comment(kept["_id"], "nova", "done")
    gone = store.create_note("sokrates", "old news")
    store.set_archived(store.read_note(gone["_id"]), True)
    assert notes_read.main([]) == 0
    out = capsys.readouterr().out
    assert "keep the symbols" in out and "-- nova" in out and "old news" not in out
    assert notes_read.main(["--archived"]) == 0
    assert "old news" in capsys.readouterr().out
