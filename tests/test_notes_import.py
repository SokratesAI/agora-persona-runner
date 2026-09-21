"""tools.notes_import: notes.md copied into the notes store, once."""

import pytest

from agora_runner import nova_notes_store as store, ticket_docs
from tests.test_board_store import FakeCouch
from tools import notes_import

SOKRATES = "- Sokrates here: server2 is back continued line\n- ROOT CAUSE: a missing token file"

MD = """---
type: log
---

- newest note

- Sokrates here: server2 is back
  continued line
- ROOT CAUSE: a missing token file

## Read

- older rule
  - Read Cycle 290. a reply
"""


@pytest.fixture
def couch(monkeypatch):
    fake = FakeCouch()
    monkeypatch.setattr(ticket_docs, "_req", fake)
    return fake


def test_every_note_lands_with_its_sender_and_reply(couch):
    records = notes_import.plan(MD)
    assert notes_import.write(records, now="2026-09-22T00:00:00Z") == (3, 0)
    notes = {n["text"]: n for n in store.list_notes()}
    assert set(notes) == {"newest note", SOKRATES, "older rule"}
    assert notes[SOKRATES]["author"] == "sokrates"
    assert notes["newest note"]["author"] == "edvard"
    assert notes["newest note"]["imported"]["position"] == 0
    [reply] = store.read_comments(notes["older rule"]["_id"])
    assert reply["author"] == "nova" and reply["text"] == "Read Cycle 290. a reply"


def test_a_second_run_writes_nothing(couch):
    records = notes_import.plan(MD)
    notes_import.write(records)
    before = dict(couch.docs)
    assert notes_import.write(records) == (0, 3)
    assert set(couch.docs) == set(before)
