"""A conversation per project -- idea #92's phase 4.

The plan calls this *"the same mechanism with a different key"*, so what
these tests are actually pointed at is the two ways that sentence can be
wrong.

**A project name is free text.** It comes off a `Project` cell the owner
types on a phone, so it can carry a hyphen, a space, or a different case
from the last time he typed it -- none of which a cycle number can. The
heading parser has to survive all three, and `k3s-sentinel` is the one
that a loose separator gets wrong in a way nothing would notice: it
parses as project `k3s` with a stamp that swallows the rest of the line.

**A project comment and a Needs Edvard reply are both keyed on no cycle.**  (not-prose: quoting a literal)
`comment_index` is what `verify_write` compares before and after every
write to this file, so if those two collapse to one key, a write can
either be refused for a conflict that is not there or wave through one
that is.
"""

import pytest

from agora_runner import nova_comments
from agora_runner.nova_comments import (
    comment_index,
    insert_comment,
    insert_reply,
    match_heading,
    parse_comments,
    project_comments,
)

FILE = """---
type: log
contract: Nova reads `## New`, acts, then moves it to `## Acknowledged`.
---

# Comments

## New

### Project k3s-sentinel · 2026-08-28 10:40

Should this run on the NAS?

#### Nova · 2026-08-28 10:55

Not until it has a key.

### Needs Edvard · 2026-08-28 09:10

Hold the SSH key.

### Cycle 572 · 2026-08-28 10:20

Good measurement.

## Acknowledged

### Project Nova · 2026-08-27 20:00

The pool page is good.
"""


def test_a_project_name_with_a_hyphen_is_not_split_at_the_hyphen():
    assert match_heading("### Project k3s-sentinel · 2026-08-28 10:40") == (
        None, "k3s-sentinel", "2026-08-28 10:40"
    )


def test_the_three_heading_kinds_stay_distinct():
    assert match_heading("### Cycle 63 · 2026-08-09 22:40")[:2] == (63, None)
    assert match_heading("### Needs Edvard · 2026-08-10 08:20")[:2] == (None, None)
    assert match_heading("### Project Sokrates Post · 2026-08-10 08:20")[:2] == (
        None, "Sokrates Post"
    )


def test_a_project_thread_collects_both_sections_and_the_replies():
    thread = project_comments(FILE, "K3S-SENTINEL")
    assert [c["text"] for c in thread] == ["Should this run on the NAS?"]
    assert [r["text"] for r in thread[0]["replies"]] == ["Not until it has a key."]


def test_a_needs_edvard_reply_is_not_a_project_comment_and_the_reverse():
    assert [c["text"] for c in nova_comments.needs_comments(FILE)] == [
        "Hold the SSH key."
    ]
    assert nova_comments.comments_by_cycle(FILE).keys() == {572}


def test_the_index_keeps_two_same_minute_threads_apart():
    """The collision `comment_index` exists to survive.

    Both of these are `cycle=None`, and before the project went into the
    key they were one entry -- so `verify_write` would have compared the
    second project's comment against the first's and refused the write.
    """
    same_minute = insert_comment(
        insert_comment(FILE, None, "about Agora", "2026-08-28 11:00", project="Agora"),
        None, "about Nova", "2026-08-28 11:00", project="Nova",
    )
    index = comment_index(same_minute)
    assert index[(None, "Agora", "2026-08-28 11:00")]["text"] == "about Agora"
    assert index[(None, "Nova", "2026-08-28 11:00")]["text"] == "about Nova"


COLLIDING = """# Comments

## New

### Needs Edvard · 2026-08-28 09:10

Hold the SSH key.

### Project Agora · 2026-08-28 09:10

Is the public bundle still served?

## Acknowledged
"""


def test_a_reply_lands_on_the_project_it_names_not_on_the_needs_block():
    """Same stamp, same absent cycle -- only the project tells them apart.

    The decoy is deliberately written *above* the target. `insert_reply`
    stops at the first heading it matches, so a version that ignores the
    project would answer whichever of the two comes first in the file --
    and with the target first, that mutation passes and this test says
    nothing.
    """
    out = insert_reply(COLLIDING, None, "2026-08-28 09:10", "answered",
                       "2026-08-28 09:20", project="Agora")
    by_key = comment_index(out)
    assert by_key[(None, "Agora", "2026-08-28 09:10")]["reply"] == "answered"
    assert by_key[(None, None, "2026-08-28 09:10")]["reply"] == ""


def test_an_unwritten_project_has_an_empty_thread_rather_than_an_error():
    assert project_comments(FILE, "Sokrates Post") == []
