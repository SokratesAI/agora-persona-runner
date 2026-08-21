"""`notes.md` -> the `/notes` page.

Every case here is shaped off the live file as it stood on 2026-08-21 --
nine answered notes, one of them carrying two cycle replies, one running
onto a wrapped line, and the empty bullet `nova_capture` leaves at the
top as Edvard's cursor.
"""

from unittest.mock import patch

from agora_runner import nova_notes
from agora_runner.nova_notes import notes_payload, parse_notes_page


LIVE_SHAPE = """---
type: log
tags: [agora, notes, capture]
contract: Edvard writes in the bare bullet list at the top.
---

- A note nobody has picked up yet.
-

## Read

- Platform-config dispatch billing block is fine to leave as-is.
  - Read Cycle 258. Recorded: I will not chase it until September 1.

- Follow-up on the ci-builder idea: please research it
  before anything gets built.
  - Read Cycle 241. Did the research and wrote it up.
  - Corrected Cycle 244, after you asked which answer was real.
"""


def test_the_bare_bullets_above_the_first_heading_are_the_waiting_notes():
    parsed = parse_notes_page(LIVE_SHAPE)
    assert [note["text"] for note in parsed["waiting"]] == [
        "A note nobody has picked up yet."
    ]


def test_his_cursor_is_not_a_note():
    """`nova_capture` keeps an empty bullet at the top to type into.

    It is punctuation in a file, not something Edvard wrote, and a card
    drawn for it would be a permanent blank note at the top of the page.
    """
    parsed = parse_notes_page(LIVE_SHAPE)
    assert all(note["text"] for note in parsed["waiting"])


def test_a_cycles_reply_belongs_to_the_note_above_it():
    parsed = parse_notes_page(LIVE_SHAPE)
    first, second = parsed["read"]
    assert first["responses"] == [
        "Read Cycle 258. Recorded: I will not chase it until September 1."
    ]
    assert len(second["responses"]) == 2
    assert second["responses"][1].startswith("Corrected Cycle 244")


def test_a_wrapped_note_is_one_note():
    """A line break belongs to whoever wrapped it, not to the text.

    Same rule `parse_notes` follows for my own capture files, and the
    same failure if it is skipped: the tail sentence disappears from the
    page without anything saying so.
    """
    parsed = parse_notes_page(LIVE_SHAPE)
    assert parsed["read"][1]["text"] == (
        "Follow-up on the ci-builder idea: please research it "
        "before anything gets built."
    )


def test_a_note_hard_wrapped_at_column_zero_keeps_its_tail():
    """The half of the wrapping rule an indent-only matcher misses.

    `notes.md` is one line per paragraph by convention, not by
    enforcement, and the Note button in the app posts whatever was
    typed. A note pasted in from something that wraps at 80 columns has
    every line after the first at column zero -- and dropping those
    loses sentences off the page without anything saying so, which is
    the one failure mode worse than not rendering at all.
    """
    parsed = parse_notes_page(
        "- A note that wrapped\nonto a second line at column zero.\n"
    )
    assert [note["text"] for note in parsed["waiting"]] == [
        "A note that wrapped onto a second line at column zero."
    ]


def test_a_reply_carries_the_cycle_it_links_to():
    payload = _payload(LIVE_SHAPE)
    read = [note for note in payload["notes"] if not note["waiting"]]
    assert [r["cycle"] for r in read[0]["responses"]] == [258]
    assert [r["cycle"] for r in read[1]["responses"]] == [241, 244]


def test_a_waiting_note_carries_no_reply_and_says_it_is_waiting():
    payload = _payload(LIVE_SHAPE)
    waiting = [note for note in payload["notes"] if note["waiting"]]
    assert len(waiting) == 1
    assert waiting[0]["responses"] == []
    assert waiting[0]["answered"] is False


def test_waiting_notes_come_first():
    """The page's whole question is "did anyone pick my note up".

    A waiting note buried under nine answered ones answers it last.
    """
    payload = _payload(LIVE_SHAPE)
    assert [note["waiting"] for note in payload["notes"]][:1] == [True]
    assert payload["waitingTotal"] == 1
    assert payload["readTotal"] == 2


def test_a_note_moved_to_read_with_no_reply_is_not_reported_as_answered():
    """Half the contract done is a real state and the page says so."""
    payload = _payload("- \n\n## Read\n\n- Moved and never written up.\n")
    assert payload["waitingTotal"] == 0
    assert payload["notes"][0]["answered"] is False
    assert payload["notes"][0]["responses"] == []


def test_a_file_with_no_read_section_still_shows_the_waiting_notes():
    """What a fresh vault's very first note looks like."""
    payload = _payload("---\ntype: log\n---\n\n- The first note ever.\n- \n")
    assert payload["waitingTotal"] == 1
    assert payload["readTotal"] == 0
    assert payload["notes"][0]["text"] == "The first note ever."


def test_a_missing_notes_file_is_an_empty_page_not_a_crash():
    payload = _payload("")
    assert payload == {"notes": [], "waitingTotal": 0, "readTotal": 0}


def test_the_page_reads_the_path_the_note_button_writes():
    """One constant, not two.

    `nova_capture.CAPTURE_TARGETS["notes"]` is what the Note button
    writes to. A page reading a second copy of that path would show an
    empty file for as long as it took someone to notice.
    """
    from agora_runner.nova_capture import CAPTURE_TARGETS
    from agora_runner import nova_sources

    with patch.object(nova_sources, "vault_read_path", return_value="") as read:
        nova_sources.notes_markdown()
    read.assert_called_once_with(CAPTURE_TARGETS["notes"])


def _payload(markdown):
    with patch.object(nova_notes, "notes_markdown", return_value=markdown):
        return notes_payload()
