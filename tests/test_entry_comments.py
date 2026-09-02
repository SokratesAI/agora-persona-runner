"""Comments on a journal entry that carries no cycle number.

The owner, `issues.md` 2026-09-02: *"The retrospective needs my input, but I
have no ability to give it as those do not have a comment section. Please
make them and other special journals have a comment section like the rest of
the journals."*

Every journal card offered a comment box except the ones written by something
other than an hourly cycle, because a comment was filed under a cycle number
and those have none. They file under the entry's own `date time` now.
"""

import json

import pytest

from agora_runner import nova_comments
from agora_runner.nova_comments import (
    add_entry_comment,
    comment_index,
    comments_by_cycle,
    comments_by_entry,
    entry_key,
    insert_comment,
    insert_reply,
    match_heading,
    needs_comments,
    parse_comments,
)


FILE = "\n".join([
    "# Comments",
    "",
    "## New",
    "",
    "### Entry 2026-09-02 07:09 · 2026-09-02 08:15",
    "",
    "why did the box fall over",
    "",
    "### Cycle 788 · 2026-09-02 08:15",
    "",
    "about a cycle",
    "",
    "### Needs Edvard · 2026-09-02 08:15",
    "",
    "about the digest",
    "",
    "### Project Marcus · 2026-09-02 08:15",
    "",
    "about a project",
    "",
    "## Acknowledged",
    "",
    "### Entry 2026-09-01 22:32 · 2026-09-01 22:40",
    "",
    "an older one, already retired",
    "",
])


def test_entry_heading_parses_and_the_other_three_still_do():
    assert match_heading("### Entry 2026-09-02 07:09 · 2026-09-02 08:15") == (
        None, None, "2026-09-02 07:09", "2026-09-02 08:15",
    )
    assert match_heading("### Cycle 63 · 2026-08-09 22:40") == (
        63, None, None, "2026-08-09 22:40",
    )
    assert match_heading("### Needs Edvard · 2026-08-10 08:20") == (
        None, None, None, "2026-08-10 08:20",
    )
    assert match_heading("### Project k3s-sentinel · 2026-08-28 10:40") == (
        None, "k3s-sentinel", None, "2026-08-28 10:40",
    )


def test_a_project_named_like_a_date_is_still_a_project():
    """The entry pattern is tried before the project one, so it has to be
    narrow enough not to swallow a project a person could plausibly name."""
    assert match_heading("### Project Entry 2026 · 2026-08-28 10:40") == (
        None, "Entry 2026", None, "2026-08-28 10:40",
    )


def test_an_entry_comment_is_not_a_needs_edvard_reply():
    """The guard that matters. Both carry `cycle is None`, and before the
    entry key existed `needs_comments` selected on exactly that -- so an
    unfixed version files every comment on a retrospective into the digest's
    thread, where he would never see it answered."""
    # Precondition: the fixture really does hold a comment of each kind.
    kinds = {(c["cycle"] is not None, bool(c["project"]), bool(c["entry"]))
             for c in parse_comments(FILE)}
    assert (False, False, True) in kinds and (False, False, False) in kinds

    assert [c["text"] for c in needs_comments(FILE)] == ["about the digest"]


def test_comments_by_entry_groups_only_entry_threads():
    grouped = comments_by_entry(FILE)
    assert set(grouped) == {"2026-09-02 07:09", "2026-09-01 22:32"}
    assert [c["text"] for c in grouped["2026-09-02 07:09"]] == [
        "why did the box fall over"
    ]
    # And the cycle grouping is unchanged by any of it.
    assert comments_by_cycle(FILE).keys() == {788}


def test_insert_comment_writes_the_entry_heading_and_reads_back():
    out = insert_comment(FILE, None, "a new one", "2026-09-02 09:00",
                         entry="2026-09-02 07:09")
    stored = [c for c in parse_comments(out) if c["text"] == "a new one"]
    assert len(stored) == 1
    assert stored[0]["entry"] == "2026-09-02 07:09"
    assert stored[0]["cycle"] is None and not stored[0]["project"]
    assert not stored[0]["acknowledged"]


def test_a_reply_lands_on_the_entry_and_not_on_the_cycle_that_shares_its_stamp():
    """`2026-09-02 08:15` is the stamp on all four comments in the fixture,
    so a reply that keys on the stamp alone has three wrong targets to pick
    from -- and the entry one is written first, which is the position that
    makes a naive scan look right."""
    out = insert_reply(FILE, None, "2026-09-02 08:15", "answered", "2026-09-02 09:01",
                       entry="2026-09-02 07:09")
    assert out is not None
    replied = [c for c in parse_comments(out) if c["reply"]]
    assert [c["text"] for c in replied] == ["why did the box fall over"]


def test_two_entries_commented_on_in_the_same_minute_are_two_keys():
    both = insert_comment(
        insert_comment(FILE, None, "about the retro", "2026-09-02 11:00",
                       entry="2026-09-02 07:09"),
        None, "about the silence", "2026-09-02 11:00", entry="2026-09-01 22:32",
    )
    index = comment_index(both)
    assert (None, None, "2026-09-02 07:09", "2026-09-02 11:00") in index
    assert (None, None, "2026-09-01 22:32", "2026-09-02 11:00") in index


@pytest.mark.parametrize("bad", ["", "   ", "2026-09-02", "07:09", "yesterday", 7, None])
def test_add_entry_comment_refuses_a_key_no_card_could_produce(bad):
    ok, message = add_entry_comment(bad, "hello")
    assert not ok and "YYYY-MM-DD HH:MM" in message


def test_entry_key_needs_both_halves():
    assert entry_key("2026-09-02", "07:09") == "2026-09-02 07:09"
    assert entry_key("2026-09-02", "") == ""
    assert entry_key("", "07:09") == ""
    assert entry_key("2026-09-02", "7:09") == ""
