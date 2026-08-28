"""Comments on a cycle: where one lands, what survives the round trip.

The write half is here; what reaches it over HTTP is in test_nova_site.py,
the same split `test_nova_capture.py` already uses.

The property most of these tests are really defending is that the owner's
text comes back **byte for byte**. A comment is prose he typed at a
particular cycle, and the whole value of the channel is that a future
cycle reads what he actually said rather than a reformatted version of
it. So the round-trip tests below assert equality against the original
string, not that it "contains" it -- a test that only checks containment
would pass while the writer quietly escaped, wrapped or re-indented him.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agora_runner import nova_comments, vault
from tests.couch_fake import FakeCouch
from agora_runner.nova_comments import (
    COMMENTS_PATH,
    ACKNOWLEDGED_HEADING,
    add_comment,
    add_needs_comment,
    add_reply,
    clean_comment_text,
    comments_by_cycle,
    needs_comments,
    format_stamp,
    insert_comment,
    # Bound here rather than reached through the module, so a test that
    # patches the module attribute can still call the real one without
    # recursing into its own replacement.
    insert_reply,
    parse_comments,
)

# The revision a read is served at. Every write here is a
# read-modify-write and has to send this back, so a stale one is rejected
# by CouchDB rather than quietly adopted -- see
# `test_the_write_carries_the_revision_it_read_at` below for why a
# hard-coded value is enough: what matters is that it is *this* read's
# revision and not whatever the vault holds at PUT time.
REV = "7-abc"

# The shape the live file is created with -- frontmatter, both sections,
# nothing else. Written out rather than imported so a change to the real
# file cannot silently rewrite what these tests think they are testing.
EMPTY = """---
type: log
tags: [agora, nova, comments, agent-context]
status: built
---

# Comments

## New

## Acknowledged
"""

ONE_COMMENT = """---
type: log
---

# Comments

## New

### Cycle 63 · 2026-08-09 22:40

great research, keep it up!

## Acknowledged

### Cycle 60 · 2026-08-09 18:50

the heartbeat measurement was the useful bit

Cycle 61 acted on this.
"""


# --- text as typed -> text as stored --------------------------------------


def test_a_typed_line_is_stored_exactly():
    assert clean_comment_text("great research, keep it up!") == "great research, keep it up!"


def test_paragraph_breaks_are_his_and_are_kept():
    """The capture box splits lines into separate bullets. A comment must
    not: it is one thought, and its shape is part of what he said."""
    text = "great research.\n\nkeep it up!"
    assert clean_comment_text(text) == "great research.\n\nkeep it up!"


def test_a_single_newline_is_not_joined_into_the_line_above():
    assert clean_comment_text("one\ntwo") == "one\ntwo"


def test_surrounding_blank_lines_go_but_interior_ones_stay():
    assert clean_comment_text("\n\nfirst\n\n\nlast\n\n") == "first\n\n\nlast"


def test_crlf_from_a_phone_keyboard_is_normalised():
    assert clean_comment_text("one\r\ntwo\r\n") == "one\ntwo"


def test_trailing_spaces_go_but_leading_indentation_stays():
    """Leading whitespace could be him quoting or indenting deliberately;
    trailing whitespace is never intentional and is invisible either way."""
    assert clean_comment_text("  indented   \nplain  ") == "  indented\nplain"


def test_whitespace_only_text_is_nothing_to_store():
    assert clean_comment_text("   \n\n  ") == ""
    assert clean_comment_text("") == ""
    assert clean_comment_text(None) == ""


# --- where a comment lands ------------------------------------------------


def test_a_comment_lands_under_new_not_acknowledged():
    out = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    new, _, ack = out.partition("## Acknowledged")
    assert "Cycle 63" in new
    assert "Cycle 63" not in ack


def test_the_newest_comment_is_first():
    once = insert_comment(EMPTY, 63, "first", "2026-08-09 22:40")
    twice = insert_comment(once, 64, "second", "2026-08-09 23:10")
    assert twice.index("second") < twice.index("first")


def test_an_existing_comment_is_never_lost():
    out = insert_comment(ONE_COMMENT, 64, "a new one", "2026-08-09 23:10")
    assert "great research, keep it up!" in out
    assert "the heartbeat measurement was the useful bit" in out
    assert "Cycle 61 acted on this." in out


def test_the_frontmatter_is_untouched():
    out = insert_comment(ONE_COMMENT, 64, "a new one", "2026-08-09 23:10")
    assert out.startswith("---\ntype: log\n---\n")


def test_an_acknowledged_comment_is_not_reopened():
    """Inserting must not drag anything back up into `## New` -- that would
    hand a future cycle work it has already done."""
    out = insert_comment(ONE_COMMENT, 64, "a new one", "2026-08-09 23:10")
    new = out.split("## Acknowledged")[0]
    assert "Cycle 60" not in new


def test_a_missing_new_section_is_created_above_acknowledged():
    without = "---\ntype: log\n---\n\n# Comments\n\n## Acknowledged\n"
    out = insert_comment(without, 63, "keep it up", "2026-08-09 22:40")
    assert out.index("## New") < out.index("## Acknowledged")
    assert "keep it up" in out


def test_a_comment_survives_a_file_with_no_sections_at_all():
    """A comment is never dropped for want of a heading the file did not
    have -- losing what he typed is strictly worse than an odd-looking file."""
    out = insert_comment("---\ntype: log\n---\n", 63, "keep it up", "2026-08-09 22:40")
    assert "## New" in out
    assert "keep it up" in out
    assert parse_comments(out)[0]["text"] == "keep it up"


def test_a_comment_survives_an_entirely_empty_file():
    out = insert_comment("", 63, "keep it up", "2026-08-09 22:40")
    assert parse_comments(out)[0]["text"] == "keep it up"


# --- reading it back ------------------------------------------------------


def test_a_comment_round_trips_byte_for_byte():
    text = "great research, keep it up!\n\nDo more research to make yourself\nmore token efficient"
    out = insert_comment(EMPTY, 63, clean_comment_text(text), "2026-08-09 22:40")
    assert parse_comments(out)[0]["text"] == text


def test_markdown_in_his_text_is_stored_raw_not_escaped():
    """Nothing renders a comment as markdown, so nothing may rewrite it to
    survive a renderer that does not exist."""
    text = "the `pace` field is **good** -- see [[cycle-economics]] and #44"
    out = insert_comment(EMPTY, 63, text, "2026-08-09 22:40")
    assert text in out
    assert parse_comments(out)[0]["text"] == text


def test_a_heading_inside_his_text_does_not_split_the_comment():
    """A `#` line in his prose is his prose. Only `### Cycle <n>` and the
    two section headings are structure -- everything else is content."""
    text = "look at:\n# not a heading\n#### also not one"
    out = insert_comment(EMPTY, 63, text, "2026-08-09 22:40")
    parsed = parse_comments(out)
    assert len(parsed) == 1
    assert parsed[0]["text"] == text


def test_a_second_nova_block_is_its_own_reply_not_text_inside_the_first():
    """The owner's screenshot, 2026-08-21: a cycle appended its own answer under
    a comment the reply worker had already answered, and the app painted
    `#### Nova · 2026-08-21 16:23` as literal text in the middle of the blue
    bubble. Only the first block can be written by `add_reply`, so a later
    one is a cycle's."""
    out = insert_comment(EMPTY, 63, "testing image upload", "2026-08-21 15:51")
    out = insert_reply(out, 63, "2026-08-21 15:51", "Didn't get an image.", "2026-08-21 15:51")
    out = out.replace(
        "Didn't get an image.",
        "Didn't get an image.\n\n#### Nova · 2026-08-21 16:23\n\nCycle 304: the upload worked.",
    )
    replies = parse_comments(out)[0]["replies"]
    assert [(r["author"], r["stamp"], r["text"]) for r in replies] == [
        ("commentator", "2026-08-21 15:51", "Didn't get an image."),
        ("cycle", "2026-08-21 16:23", "Cycle 304: the upload worked."),
    ]


def test_the_reply_field_stays_the_reply_worker_s_own_answer():
    """`_verify_replied` and `nova_replies` both mean the auto-reply when
    they say "the reply", so a cycle appending a second block below it must
    not change what either of them reads back."""
    out = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    out = insert_reply(out, 63, "2026-08-09 22:40", "Thanks.", "2026-08-09 22:41")
    out = out.replace("Thanks.", "Thanks.\n\n#### Nova · 2026-08-09 23:10\n\nCycle 64 here.")
    parsed = parse_comments(out)[0]
    assert parsed["reply"] == "Thanks."
    assert parsed["replyStamp"] == "2026-08-09 22:41"
    assert parsed["text"] == "keep it up"


def test_an_unanswered_comment_has_no_replies():
    out = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    parsed = parse_comments(out)[0]
    assert parsed["replies"] == []
    assert parsed["reply"] == ""


def test_parse_reads_the_cycle_the_stamp_and_the_section():
    parsed = parse_comments(ONE_COMMENT)
    assert [(c["cycle"], c["acknowledged"]) for c in parsed] == [(63, False), (60, True)]
    assert parsed[0]["stamp"] == "2026-08-09 22:40"
    assert parsed[0]["text"] == "great research, keep it up!"


def test_a_comment_outside_both_sections_is_not_picked_up():
    """Prose above `## New` is the file explaining itself, not a comment."""
    stray = "---\ntype: log\n---\n\n### Cycle 12 · x\n\nnot a comment\n\n## New\n\n"
    assert parse_comments(stray) == []


def test_comments_group_by_cycle():
    two = insert_comment(ONE_COMMENT, 63, "and another thing", "2026-08-09 23:10")
    grouped = comments_by_cycle(two)
    assert sorted(grouped) == [60, 63]
    assert [c["text"] for c in grouped[63]] == ["great research, keep it up!", "and another thing"]


def test_a_card_reads_downwards_oldest_first():
    """The owner, 2026-08-10: *"Journal comments must be sorted with the newest
    message at the bottom, so that the conversation goes downwards."* The
    file is still written newest-first; the flip is at this boundary."""
    stored = insert_comment(ONE_COMMENT, 63, "second", "2026-08-09 23:10")
    stored = insert_comment(stored, 63, "third", "2026-08-09 23:40")
    assert [c["stamp"] for c in comments_by_cycle(stored)[63]] == [
        "2026-08-09 22:40", "2026-08-09 23:10", "2026-08-09 23:40",
    ]


def test_an_acknowledged_comment_sorts_by_when_it_was_said_not_which_section():
    """A card mixes both sections, and `## Acknowledged` holds the *older*
    half. Reversing file order would put the retired ones after the new
    ones; only the stamp gets this right."""
    stored = (
        "## New\n\n"
        "### Cycle 63 · 2026-08-09 23:10\n\nstill unanswered\n\n"
        "## Acknowledged\n\n"
        "### Cycle 63 · 2026-08-09 22:40\n\nthe first thing he said\n"
    )
    thread = comments_by_cycle(stored)[63]
    assert [c["text"] for c in thread] == ["the first thing he said", "still unanswered"]
    assert [c["acknowledged"] for c in thread] == [True, False]


def test_two_comments_in_the_same_minute_keep_the_order_the_file_gave_them():
    """The stamp is minute-resolution, so it cannot order these -- the sort
    is stable rather than arbitrary, and newest-first storage means the
    later-inserted one is the one at the top of the file."""
    stored = insert_comment(EMPTY, 63, "first", "2026-08-09 22:40")
    stored = insert_comment(stored, 63, "second", "2026-08-09 22:40")
    assert [c["text"] for c in comments_by_cycle(stored)[63]] == ["second", "first"]


def test_the_needs_edvard_thread_reads_downwards_too():
    """It renders through the same drawer, so it gets the same order."""
    stored = insert_comment(EMPTY, None, "first", "2026-08-10 08:20")
    stored = insert_comment(stored, None, "second", "2026-08-10 09:05")
    assert [c["text"] for c in needs_comments(stored)] == ["first", "second"]


def test_an_empty_file_has_no_comments():
    assert parse_comments("") == []
    assert comments_by_cycle("") == {}


# --- the stamp ------------------------------------------------------------


@pytest.mark.parametrize(
    "utc, expected",
    [
        # Rule 7: he reads Oslo time, never UTC. Midwinter is UTC+1...
        (datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc), "2026-01-15 13:00"),
        # ...and midsummer is UTC+2.
        (datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), "2026-07-15 14:00"),
        # The two switch-overs themselves: last Sunday of March, 01:00 UTC.
        (datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc), "2026-03-29 01:59"),
        (datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc), "2026-03-29 03:00"),
        # ...and the last Sunday of October.
        (datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc), "2026-10-25 02:59"),
        (datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc), "2026-10-25 02:00"),
    ],
)
def test_the_stamp_is_oslo_time(utc, expected):
    with patch.object(nova_comments, "datetime") as fake:
        fake.now.return_value = utc
        # `datetime` is also used to build the DST boundaries, so the real
        # constructor has to keep working underneath the patched `now`.
        fake.side_effect = datetime
        assert format_stamp() == expected


# --- the write ------------------------------------------------------------


def test_a_comment_is_written_to_the_comments_file():
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, message = add_comment(63, "keep it up", stamp="2026-08-09 22:40")
    assert ok, message
    path, content = write.call_args[0]
    assert path == COMMENTS_PATH
    assert parse_comments(content)[0] == {
        "cycle": 63,
        "project": None,
        "stamp": "2026-08-09 22:40",
        "text": "keep it up",
        "reply": "",
        "replyStamp": "",
        "replies": [],
        "acknowledged": False,
    }


def test_a_conflict_is_retried_against_a_re_read_file():
    """409 is the good case: someone else wrote between the read and the
    PUT. The retry must re-read rather than resend, or it would clobber
    exactly the write that just beat it."""
    reads = [EMPTY, insert_comment(EMPTY, 64, "landed first", "2026-08-09 23:00")]
    with patch.object(nova_comments, "vault_read_path_rev",
                         side_effect=[(r, REV) for r in reads]) as read, \
            patch.object(nova_comments, "vault_write_path",
                         side_effect=["409 conflict", "written"]) as write:
        ok, _ = add_comment(63, "mine", stamp="2026-08-09 23:10")
    assert ok
    assert read.call_count == 2
    # The winning write carries both comments, not just the retried one.
    final = write.call_args[0][1]
    assert [c["text"] for c in parse_comments(final)] == ["mine", "landed first"]


def test_a_non_conflict_failure_is_not_retried():
    """Anything that is not a 409 will fail identically next time, so
    retrying it only spins."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="500 boom") as write:
        ok, message = add_comment(63, "keep it up")
    assert not ok
    assert write.call_count == 1
    assert "500 boom" in message


def test_a_missing_file_is_created_rather_than_refused():
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(None, None)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, _ = add_comment(63, "keep it up", stamp="2026-08-09 22:40")
    assert ok
    assert parse_comments(write.call_args[0][1])[0]["text"] == "keep it up"


def test_an_empty_comment_never_reaches_the_vault():
    with patch.object(nova_comments, "vault_read_path_rev") as read, \
            patch.object(nova_comments, "vault_write_path") as write:
        ok, message = add_comment(63, "   \n  ")
    assert not ok
    assert message == "nothing to comment"
    assert not read.called and not write.called


@pytest.mark.parametrize("cycle", ["sixty-three", None, "", -1])
def test_a_cycle_that_is_not_a_number_never_reaches_the_vault(cycle):
    with patch.object(nova_comments, "vault_read_path_rev") as read, \
            patch.object(nova_comments, "vault_write_path") as write:
        ok, _ = add_comment(cycle, "keep it up")
    assert not ok
    assert not read.called and not write.called


def test_a_numeric_string_cycle_is_accepted():
    """The client sends JSON and a phone keyboard can produce either."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, _ = add_comment("63", "keep it up", stamp="2026-08-09 22:40")
    assert ok
    assert parse_comments(write.call_args[0][1])[0]["cycle"] == 63


# --- Replies to the Needs Edvard block (2026-08-10) -------------------------  (not-prose: quoting a literal)
#
# the owner: *"the 'needs the owner' is still missing a comment block, so its hard
# for me to answer it. [...] I want a reply button on it."* These defend the
# one property that makes such a reply different from a comment on a cycle:
# it belongs to no cycle, so it must never be filed under one -- the digest
# is rewritten every cycle and the card it landed on would be arbitrary.


def test_a_needs_reply_is_headed_by_the_block_not_a_cycle():
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, message = add_needs_comment("go ahead and do it", stamp="2026-08-10 08:20")
    assert ok
    written = write.call_args[0][1]
    assert "### Needs Edvard · 2026-08-10 08:20" in written
    assert "### Cycle" not in written
    assert message == "commented on needs edvard"


def test_a_needs_reply_parses_back_with_no_cycle():
    stored = insert_comment(EMPTY, None, "go ahead and do it", "2026-08-10 08:20")
    parsed = parse_comments(stored)
    assert len(parsed) == 1
    assert parsed[0]["cycle"] is None
    assert parsed[0]["text"] == "go ahead and do it"
    assert parsed[0]["acknowledged"] is False


def test_a_needs_reply_never_lands_on_a_card():
    """`comments_by_cycle` is what the site hangs off each journal card. A
    `None` key leaking into it would render as a card for cycle "None"."""
    stored = insert_comment(EMPTY, None, "go ahead and do it", "2026-08-10 08:20")
    stored = insert_comment(stored, 63, "keep it up", "2026-08-09 22:40")
    assert list(comments_by_cycle(stored)) == [63]
    assert [c["text"] for c in needs_comments(stored)] == ["go ahead and do it"]


def test_needs_replies_and_cycle_comments_share_one_new_section():
    """Both are things the owner said that no cycle has answered yet, and
    `prompt.md` step 1a reads `## New` whole -- so a reply that landed in a
    section of its own would be invisible to the step built to collect it."""
    stored = insert_comment(EMPTY, None, "go ahead and do it", "2026-08-10 08:20")
    stored = insert_comment(stored, 63, "keep it up", "2026-08-09 22:40")
    new_section = stored.split(ACKNOWLEDGED_HEADING)[0]
    assert "### Needs Edvard" in new_section
    assert "### Cycle 63" in new_section


def test_a_needs_reply_is_stored_verbatim():
    typed = "Go ahead and do it.\n\nYou do not need permission from me.\n  indented"
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        add_needs_comment(typed, stamp="2026-08-10 08:20")
    assert parse_comments(write.call_args[0][1])[0]["text"] == typed


def test_an_empty_needs_reply_never_reaches_the_vault():
    with patch.object(nova_comments, "vault_read_path_rev") as read, \
            patch.object(nova_comments, "vault_write_path") as write:
        ok, message = add_needs_comment("   \n  ")
    assert not ok
    assert message == "nothing to comment"
    assert not read.called and not write.called


def test_an_acknowledged_needs_reply_is_marked_read():
    """A cycle retires one by moving it down, exactly as it does a comment."""
    stored = (
        "## New\n\n## Acknowledged\n\n"
        "### Needs Edvard · 2026-08-10 08:20\n\ngo ahead and do it\n"
    )
    assert needs_comments(stored)[0]["acknowledged"] is True


# --- Nova's reply, inside the comment it answers -----------------------
#
# The property these defend is the mirror of the one above: his text has
# to survive a reply landing under it, byte for byte, and the reply has to
# land in the comment it actually answers. The failure that matters is not
# a crash -- it is a reply attached to the wrong comment, or the owner's own
# words absorbed into it, both of which parse fine and read as a lie.

THREAD = """---
type: log
---

# Comments

## New

### Cycle 80 · 2026-08-10 13:54

instant replies would be cool

### Cycle 79 · 2026-08-10 12:40

two paragraphs here.

and the second one.

## Acknowledged

### Cycle 75 · 2026-08-10 08:43

an older one, already handled
"""


def _by_stamp(markdown, stamp):
    return next(c for c in parse_comments(markdown) if c["stamp"] == stamp)


def test_a_reply_lands_inside_the_comment_it_answers():
    updated = nova_comments.insert_reply(
        THREAD, 80, "2026-08-10 13:54", "They are. Here is one.", "2026-08-10 14:02")
    replied = _by_stamp(updated, "2026-08-10 13:54")
    assert replied["reply"] == "They are. Here is one."
    assert replied["replyStamp"] == "2026-08-10 14:02"
    # and nobody else got one
    assert [c["reply"] for c in parse_comments(updated) if c["stamp"] != "2026-08-10 13:54"] == ["", ""]


def test_his_text_is_untouched_by_a_reply_landing_under_it():
    """The reason the reply is split off at parse time rather than left in
    the body: a cycle reading `## New` must still read exactly what he
    typed, not his words with Nova's stapled to the end."""
    updated = nova_comments.insert_reply(
        THREAD, 79, "2026-08-10 12:40", "Noted.", "2026-08-10 12:45")
    assert _by_stamp(updated, "2026-08-10 12:40")["text"] == "two paragraphs here.\n\nand the second one."


def test_a_reply_to_the_last_new_comment_stays_out_of_acknowledged():
    """The bound of a comment body is the next comment *or* the next `##`
    section. Missing the section put the reply under `## Acknowledged`,
    where it belonged to a different comment entirely."""
    updated = nova_comments.insert_reply(
        THREAD, 79, "2026-08-10 12:40", "Noted.", "2026-08-10 12:45")
    new_half, _, ack_half = updated.partition(ACKNOWLEDGED_HEADING)
    assert "#### Nova" in new_half
    assert "#### Nova" not in ack_half
    assert _by_stamp(updated, "2026-08-10 08:43")["reply"] == ""


def test_a_comment_that_already_has_a_reply_is_not_replied_to_twice():
    once = nova_comments.insert_reply(
        THREAD, 80, "2026-08-10 13:54", "First answer.", "2026-08-10 14:02")
    assert nova_comments.insert_reply(
        once, 80, "2026-08-10 13:54", "Second answer.", "2026-08-10 14:30") is None


def test_a_reply_to_a_comment_that_is_gone_is_dropped():
    assert nova_comments.insert_reply(
        THREAD, 80, "2026-08-10 09:00", "answering thin air", "2026-08-10 14:02") is None
    assert nova_comments.insert_reply(
        THREAD, 42, "2026-08-10 13:54", "wrong cycle, right stamp", "2026-08-10 14:02") is None


def test_a_reply_does_not_acknowledge_the_comment():
    """Replying is conversation; acknowledging means a cycle acted. If a
    reply ever started marking things read, a comment that needed real work
    would be retired by a paragraph about it."""
    updated = nova_comments.insert_reply(
        THREAD, 80, "2026-08-10 13:54", "They are.", "2026-08-10 14:02")
    assert _by_stamp(updated, "2026-08-10 13:54")["acknowledged"] is False


def test_a_multi_paragraph_reply_survives_the_round_trip():
    reply = "First paragraph.\n\nSecond one, with a blank line before it."
    updated = nova_comments.insert_reply(
        THREAD, 80, "2026-08-10 13:54", reply, "2026-08-10 14:02")
    assert _by_stamp(updated, "2026-08-10 13:54")["reply"] == reply


def test_add_reply_writes_the_whole_file_back():
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(THREAD, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, message = nova_comments.add_reply(
            80, "2026-08-10 13:54", "They are.", reply_stamp="2026-08-10 14:02")
    assert ok, message
    path, content = write.call_args[0]
    assert path == COMMENTS_PATH
    assert _by_stamp(content, "2026-08-10 13:54")["reply"] == "They are."


def test_add_reply_retries_on_a_conflict_against_the_re_read_file():
    """Same reason `_store` does: 409 means someone wrote between the read
    and the PUT, and resending the stale body would clobber them."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(THREAD, REV)) as read, \
            patch.object(nova_comments, "vault_write_path",
                         side_effect=["409 conflict", "written"]) as write:
        ok, _ = nova_comments.add_reply(80, "2026-08-10 13:54", "They are.")
    assert ok
    assert read.call_count == 2
    assert write.call_count == 2


def test_add_reply_gives_up_rather_than_writing_somewhere_else():
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(THREAD, REV)), \
            patch.object(nova_comments, "vault_write_path") as write:
        ok, message = nova_comments.add_reply(80, "2026-08-10 09:00", "answering thin air")
    assert not ok
    assert "left to reply to" in message
    assert not write.called


def test_an_empty_reply_is_refused_before_any_read():
    with patch.object(nova_comments, "vault_read_path_rev") as read, \
            patch.object(nova_comments, "vault_write_path") as write:
        ok, message = nova_comments.add_reply(80, "2026-08-10 13:54", "   \n\n  ")
    assert (ok, message) == (False, "nothing to reply")
    assert not read.called and not write.called


# ---------------------------------------------------------------------------
# The revision (2026-08-12). Both writes above retried on 409 while sending
# no revision at all, so the conflict they waited for could not happen: the
# client looked up a fresh `_rev` immediately before the PUT and adopted
# whoever had written in between. Note what the retry tests above cannot
# see -- they feed the write mock a "409 conflict" directly, so they pass
# whether or not the real write could ever produce one. These are the tests
# that fail when the revision is dropped.
# ---------------------------------------------------------------------------


def test_the_write_carries_the_revision_it_read_at():
    """The owner typing a comment while a cycle rewrites this same file is the
    collision, and his is the write that would have vanished."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, _ = add_comment(63, "keep it up", stamp="2026-08-09 22:40")
    assert ok
    assert write.call_args.kwargs["if_rev"] == REV


def test_a_retry_carries_the_revision_of_the_re_read_not_the_first_read():
    """The retry exists because the file moved. Resending the first read's
    revision would conflict forever; resending none would clobber the
    winner, which is the bug one level down."""
    reads = [(EMPTY, "7-abc"),
             (insert_comment(EMPTY, 64, "landed first", "2026-08-09 23:00"), "8-def")]
    with patch.object(nova_comments, "vault_read_path_rev", side_effect=reads), \
            patch.object(nova_comments, "vault_write_path",
                         side_effect=["409 conflict", "written"]) as write:
        ok, _ = add_comment(63, "mine", stamp="2026-08-09 23:10")
    assert ok
    assert [c.kwargs["if_rev"] for c in write.call_args_list] == ["7-abc", "8-def"]


def test_a_reply_carries_its_revision_too():
    """`nova_replies` writes the same file from the other direction."""
    stored = insert_comment(EMPTY, 63, "what happened here?", "2026-08-09 22:40")
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(stored, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, message = add_reply(63, "2026-08-09 22:40", "fixed it",
                                reply_stamp="2026-08-09 23:00")
    assert ok, message
    assert write.call_args.kwargs["if_rev"] == REV


def test_creating_the_file_expects_it_to_be_absent():
    """Two first comments must not silently become one. `if_rev=None` PUTs
    without a `_rev`, which is CouchDB's own way of saying "there should be
    nothing here" -- it 409s if another writer created the file first."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(None, None)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, _ = add_comment(63, "keep it up", stamp="2026-08-09 22:40")
    assert ok
    assert write.call_args.kwargs["if_rev"] is None


def test_overwriting_a_tombstone_carries_the_tombstone_revision():
    """A deleted comments file has no content and a live revision. Treating
    it as absent would 409 on every attempt and lose what he typed."""
    with patch.object(nova_comments, "vault_read_path_rev", return_value=(None, REV)), \
            patch.object(nova_comments, "vault_write_path", return_value="written") as write:
        ok, _ = add_comment(63, "keep it up", stamp="2026-08-09 22:40")
    assert ok
    assert write.call_args.kwargs["if_rev"] == REV


def test_a_reply_that_loses_a_real_race_keeps_the_comment_that_landed():
    """The end-to-end version of `test_add_reply_retries_on_a_conflict...`.

    That test hands the write mock the string "409 conflict" and watches
    the retry. It proves the loop branches on 409; it cannot prove a 409
    reaches it. Measured Cycle 142: with the mocked version alone, deleting
    `if_rev=rev` from `add_reply` failed exactly one test in this file --
    the narrow one asserting the argument -- while the reply went back to
    silently overwriting whoever it raced.

    `interleave={2: ...}` lands the other writer in the only window that
    matters: after `add_reply`'s own read, before the lookup inside
    `vault_write_path`. Later than that and the unconditional path has
    already taken its revision, so the test would pass either way.
    """
    landed = insert_comment(THREAD, 81, "typed while you were writing",
                            "2026-08-10 13:58")
    couch = FakeCouch()
    couch.seed(COMMENTS_PATH, THREAD)
    couch.interleave = {2: lambda c: c.seed(COMMENTS_PATH, landed)}
    with patch.object(vault, "couch_req", couch.req):
        ok, message = nova_comments.add_reply(
            80, "2026-08-10 13:54", "They are.", reply_stamp="2026-08-10 14:02")
    assert ok, message
    assert couch.rejected == 1, "the losing write must have been refused"
    final = couch.text(COMMENTS_PATH)
    assert _by_stamp(final, "2026-08-10 13:54")["reply"] == "They are."
    assert _by_stamp(final, "2026-08-10 13:58")["text"] == \
        "typed while you were writing", "his comment must survive the reply"


def test_a_comment_that_loses_a_real_race_keeps_the_one_that_landed():
    """`_store` is the path the chat bubble on a journal card writes through
    -- the highest-traffic write in this module and the one that carries
    the owner's own words.

    Reviewer finding on PR #123. The author checked that dropping
    `if_rev=rev` here failed four tests and stopped there. All four assert
    the keyword argument on a mocked write, which is the class this whole
    change exists to move past: they prove the argument is passed, never
    that omitting it loses anything. Nothing in the repo watched an actual
    comment disappear.
    """
    landed = insert_comment(EMPTY, 64, "landed first", "2026-08-09 23:00")
    couch = FakeCouch()
    couch.seed(COMMENTS_PATH, EMPTY)
    couch.interleave = {2: lambda c: c.seed(COMMENTS_PATH, landed)}
    with patch.object(vault, "couch_req", couch.req):
        ok, message = nova_comments.add_comment(
            63, "mine", stamp="2026-08-09 23:10")
    assert ok, message
    assert couch.rejected == 1, "the losing write must have been refused"
    assert [c["text"] for c in parse_comments(couch.text(COMMENTS_PATH))] == \
        ["mine", "landed first"]


# --- the write is checked before it leaves ---------------------------------
#
# `insert_comment` and `insert_reply` are string surgery on the one file
# the owner talks to this loop through, and both run unattended -- one every
# time he types into the app, one every time the reply worker answers. Until
# these existed, nothing between them and the vault could tell a good result
# from a damaged one. `ack_comment` has had that check since Cycle 159; the
# writers that actually run every hour did not.


def _broken_writer(name, replacement):
    """Patch one of the two inserters to return damage instead of a good file."""
    return patch.object(nova_comments, name, replacement)


def test_a_comment_that_lands_inside_the_frontmatter_is_not_written():
    """The 2026-08-13 damage exactly: text spliced into the frontmatter, where
    the app's parser cannot see it and neither can the next cycle."""
    def splices_into_the_frontmatter(markdown, cycle, text, stamp, project=None):
        return markdown.replace(
            "type: log", f"type: log\n### Cycle {cycle} · {stamp}\n\n{text}", 1)

    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_comment", splices_into_the_frontmatter):
        ok, message = add_comment(63, "hello", stamp="2026-08-09 23:00")
    assert not ok
    assert "frontmatter" in message
    write.assert_not_called()


def test_a_comment_that_eats_an_existing_one_is_not_written():
    def drops_a_bystander(markdown, cycle, text, stamp, project=None):
        good = insert_comment(markdown, cycle, text, stamp)
        return good.replace("great research, keep it up!", "")

    with patch.object(nova_comments, "vault_read_path_rev",
                      return_value=(ONE_COMMENT, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_comment", drops_a_bystander):
        ok, message = add_comment(64, "hello", stamp="2026-08-09 23:00")
    assert not ok
    assert "changed too" in message
    write.assert_not_called()


def test_a_comment_filed_under_acknowledged_is_not_written():
    """Landing under the wrong heading is silent: it reads as already dealt
    with, so no cycle ever answers it."""
    def files_it_as_done(markdown, cycle, text, stamp, project=None):
        return markdown.replace(
            ACKNOWLEDGED_HEADING,
            f"{ACKNOWLEDGED_HEADING}\n\n### Cycle {cycle} · {stamp}\n\n{text}\n", 1)

    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_comment", files_it_as_done):
        ok, message = add_comment(63, "hello", stamp="2026-08-09 23:00")
    assert not ok
    assert ACKNOWLEDGED_HEADING in message
    write.assert_not_called()


def test_a_comment_stored_with_the_text_altered_is_not_written():
    """His words are the whole point of the file -- `clean_comment_text` is
    the only thing allowed to touch them, and it runs before this."""
    def rewraps_it(markdown, cycle, text, stamp, project=None):
        return insert_comment(markdown, cycle, text.replace("\n", " "), stamp)

    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_comment", rewraps_it):
        ok, message = add_comment(63, "one\ntwo", stamp="2026-08-09 23:00")
    assert not ok
    assert "text as typed" in message
    write.assert_not_called()


def test_an_ordinary_comment_still_goes_through_untouched():
    """The guard has to be invisible when nothing is wrong, or it is just a
    new way to lose what he typed."""
    with patch.object(nova_comments, "vault_read_path_rev",
                      return_value=(ONE_COMMENT, REV)), \
            patch.object(nova_comments, "vault_write_path",
                         return_value="written") as write:
        ok, message = add_comment(64, "second thoughts", stamp="2026-08-09 23:00")
    assert ok, message
    written = write.call_args[0][1]
    assert [c["text"] for c in parse_comments(written)] == [
        "second thoughts", "great research, keep it up!",
        "the heartbeat measurement was the useful bit\n\nCycle 61 acted on this."]


def test_a_needs_edvard_reply_is_checked_the_same_way():
    """`None` is a real key here, not a missing one -- an exempt set that
    mishandled it would let the whole check pass vacuously."""
    def splices_into_the_frontmatter(markdown, cycle, text, stamp, project=None):
        return markdown.replace("type: log", f"type: log\n{text}", 1)

    with patch.object(nova_comments, "vault_read_path_rev", return_value=(EMPTY, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_comment", splices_into_the_frontmatter):
        ok, message = add_needs_comment("do the second one", stamp="2026-08-09 23:00")
    assert not ok
    write.assert_not_called()


def test_a_reply_that_damages_the_comment_it_answers_is_not_written():
    """The reply worker runs on its own thread, minutes after he has closed
    the app -- a bad write here is seen by nobody."""
    def eats_his_text(markdown, cycle, stamp, reply, reply_stamp, project=None):
        return markdown.replace("great research, keep it up!",
                                f"#### Nova · {reply_stamp}\n\n{reply}")

    with patch.object(nova_comments, "vault_read_path_rev",
                      return_value=(ONE_COMMENT, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_reply", eats_his_text):
        ok, message = add_reply(63, "2026-08-09 22:40", "thanks",
                                reply_stamp="2026-08-09 23:00")
    assert not ok
    assert "text changed" in message
    write.assert_not_called()


def test_a_reply_landing_on_the_wrong_comment_is_not_written():
    def answers_the_wrong_one(markdown, cycle, stamp, reply, reply_stamp, project=None):
        return insert_reply(markdown, 60, "2026-08-09 18:50", reply, reply_stamp)

    with patch.object(nova_comments, "vault_read_path_rev",
                      return_value=(ONE_COMMENT, REV)), \
            patch.object(nova_comments, "vault_write_path") as write, \
            _broken_writer("insert_reply", answers_the_wrong_one):
        ok, message = add_reply(63, "2026-08-09 22:40", "thanks",
                                reply_stamp="2026-08-09 23:00")
    assert not ok
    write.assert_not_called()


def test_an_ordinary_reply_still_goes_through_untouched():
    with patch.object(nova_comments, "vault_read_path_rev",
                      return_value=(ONE_COMMENT, REV)), \
            patch.object(nova_comments, "vault_write_path",
                         return_value="written") as write:
        ok, message = add_reply(63, "2026-08-09 22:40", "thanks",
                                reply_stamp="2026-08-09 23:00")
    assert ok, message
    stored = [c for c in parse_comments(write.call_args[0][1]) if c["cycle"] == 63][0]
    assert stored["text"] == "great research, keep it up!"
    assert stored["reply"] == "thanks"


def test_a_duplicated_document_is_refused():
    """The 2026-08-26 corruption, as the guard now sees it.

    A complete second copy of the file was spliced into the first copy's
    frontmatter, at the point where the `contract:` line quotes the
    literal `## Acknowledged`. Both existing halves of `verify_write` are
    blind to it: `frontmatter()` reads the first block, which was intact,
    and `comment_index()` is keyed on `(cycle, project, stamp)`, so a doubled
    comment comes back under the key it already had.
    """
    good = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    doubled = good + "\n" + good
    with pytest.raises(nova_comments.WriteRefused) as refused:
        nova_comments.verify_write(good, doubled)
    assert "duplicated or spliced into itself" in str(refused.value)


def test_a_section_that_vanishes_is_refused():
    """A write that eats `## Acknowledged` is damage, not a small diff."""
    good = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    assert ACKNOWLEDGED_HEADING in good
    truncated = good.replace(ACKNOWLEDGED_HEADING, "")
    with pytest.raises(nova_comments.WriteRefused) as refused:
        nova_comments.verify_write(good, truncated)
    assert "went from 1 to 0" in str(refused.value)


def test_the_first_comment_ever_may_create_the_sections():
    """`_store` builds the document when the file does not exist yet.

    Every landmark legitimately goes 0 -> 1 there, which is the one case
    the count rule has to let through.
    """
    built = insert_comment("", 63, "keep it up", "2026-08-09 22:40")
    nova_comments.verify_write("", built, exempt={(63, None, "2026-08-09 22:40")})


def test_an_ordinary_reply_still_passes_the_count_rule():
    """The guard must not refuse the writes it sits in front of."""
    good = insert_comment(EMPTY, 63, "keep it up", "2026-08-09 22:40")
    replied = insert_reply(good, 63, "2026-08-09 22:40", "thank you",
                           "2026-08-09 22:45")
    nova_comments.verify_write(good, replied, exempt={(63, None, "2026-08-09 22:40")})
