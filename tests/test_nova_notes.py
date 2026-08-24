"""`notes.md` -> the `/notes` page.

`LIVE_SHAPE` is shaped off the live file as it stood on 2026-08-21 --
nine answered notes, one of them carrying two cycle replies, and the
empty bullet `nova_capture` leaves at the top as the owner's cursor.

**The two wrapping cases are not in that file and the fixture does not
pretend they are.** Every note there is a single line, because the file
is written one line per paragraph by convention. The convention is not
enforced anywhere -- the Note button posts whatever was typed -- so both
wrap tests below are written against text the live file has never
contained, which is the point of them.
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

    It is punctuation in a file, not something the owner wrote, and a card
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

    The indented half: markdown's own continuation, which is what a
    hand-edit of the file produces. Same rule `parse_notes` follows for
    my own capture files, and the same failure if it is skipped -- the
    tail sentence disappears from the page without anything saying so.
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
    # Oldest first, so the ci-builder note -- lower in `## Read` and
    # therefore older -- comes before the platform-config one.
    assert [r["cycle"] for r in read[0]["responses"]] == [241, 244]
    assert [r["cycle"] for r in read[1]["responses"]] == [258]


def test_a_waiting_note_carries_no_reply_and_says_it_is_waiting():
    payload = _payload(LIVE_SHAPE)
    waiting = [note for note in payload["notes"] if note["waiting"]]
    assert len(waiting) == 1
    assert waiting[0]["responses"] == []
    assert waiting[0]["answered"] is False


def test_the_page_reads_oldest_first_with_the_unanswered_notes_last():
    """The owner: *"ordered with the latest note at the bottom."*

    This used to assert the opposite -- waiting notes first, so the
    page's first card answered "did anyone pick my note up". The page is
    a conversation now and it opens scrolled to the bottom, so the
    newest message is the one he lands on either way; what changed is
    that everything above it now reads downwards in time like a chat
    log instead of upwards like a feed.

    `notes.md` is newest-first in both halves, so ascending time is the
    `## Read` list reversed with the unanswered bullets after it. Both
    halves matter: reversing only one of them would put the newest note
    in the middle.
    """
    payload = _payload(LIVE_SHAPE)
    assert [note["text"] for note in payload["notes"]] == [
        "Follow-up on the ci-builder idea: please research it before anything gets built.",
        "Platform-config dispatch billing block is fine to leave as-is.",
        "A note nobody has picked up yet.",
    ]
    assert [note["waiting"] for note in payload["notes"]] == [False, False, True]
    assert payload["waitingTotal"] == 1
    assert payload["readTotal"] == 2
    assert payload["notesTotal"] == 3


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
    assert payload == {
        "notes": [],
        "notesTotal": 0,
        "waitingTotal": 0,
        "readTotal": 0,
    }


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


# --- the address the page needs to edit, delete or convert a note ---------
#
# The owner, 2026-08-24: *"i have no way of changing it or editing it"*. The
# two boards have had Edit and Delete since issues #66; this page was built
# without them, and what it was missing was the address.


def test_a_waiting_note_carries_its_capture_index():
    payload = _payload(LIVE_SHAPE)
    waiting = [n for n in payload["notes"] if n["waiting"]]
    assert waiting, "the fixture must have a waiting note for this to mean anything"
    assert [n["index"] for n in waiting] == list(range(len(waiting)))


def test_a_note_already_read_has_no_index():
    """The edit, delete and convert endpoints can only address the bare
    bullets above the first heading. A note under `## Read` has a cycle's reply written under it, and
    rewriting it would leave that reply answering text that is gone."""
    payload = _payload(LIVE_SHAPE)
    assert [n["index"] for n in payload["notes"] if not n["waiting"]] \
        == [None] * payload["readTotal"]


def test_a_waiting_note_with_an_indented_bullet_under_it_gets_no_controls():
    """A real divergence between the two parsers, with nothing mocked.

    `_bullets` reads an indented bullet as a *cycle's reply* to the note
    above it; `capture_entries` strips the line first, sees `- `, and reads
    it as its own capture. So a waiting note somebody has already scribbled
    under shifts every capture index after it, and the naive
    position-for-position mapping would hand the edit/delete endpoints an
    address one line off. The guard notices and draws nothing.

    Reviewer finding on this change: the test that used to sit here asserted
    `addresses[note["index"]] == note["text"]`, which is what the guard's own
    `if` had just established, on a fixture where the two parsers agreed
    anyway. It passed with the guard deleted.
    """
    from agora_runner.nova_capture import list_captures

    markdown = LIVE_SHAPE.replace(
        "- A note nobody has picked up yet.",
        "- A note nobody has picked up yet.\n  - a stray indented line\n- and a later one",
    )
    # The precondition, asserted rather than assumed: the two parsers really
    # do disagree here, or the negative below proves nothing.
    parsed = parse_notes_page(markdown)
    assert len(list_captures(markdown)) != len(parsed["waiting"]), \
        "the parsers agree on this fixture, so the guard is never reached"

    payload = _payload(markdown)
    waiting = [n for n in payload["notes"] if n["waiting"]]
    assert [n["text"] for n in waiting] == [
        "A note nobody has picked up yet.", "and a later one"]
    assert [n["index"] for n in waiting] == [0, None], \
        "the note after the divergence must lose its controls, not get a wrong address"


def test_a_note_the_capture_parser_disagrees_about_gets_no_controls():
    """If the two ever drift, the page must draw nothing rather than hand
    `/api/capture/delete` an index pointing at a different line of his file."""
    with patch.object(nova_notes, "list_captures", return_value=["something else"]):
        payload = _payload(LIVE_SHAPE)
    assert [n["index"] for n in payload["notes"] if n["waiting"]] == [None]
