"""Optimistic concurrency on the *persona tool belt* (idea #63, last slice).

Both vault clients and the bridge CLI have been able to refuse a write that
would flatten somebody since 2026-08-12. The tool belt could not: `vault_write`
and `scoped_write` in `tools_dispatch` called `vault_write_path` with no
revision at all, so any persona tool call still won every race it lost.

The window this closes is not a millisecond wide. A persona edits a file by
calling `vault_read`, reasoning for a while, then calling `vault_write` some
turns later -- so the revision that has to travel is the one from the *read*,
and the tests below all put the other writer between two separate
`execute_tool` calls for that reason. A test that raced the write against
itself would pass against a revision fetched just before the PUT, which is
the bug rather than the fix.

`FakeCouch` is imported rather than restubbed on purpose: it applies
CouchDB's actual rule (a PUT whose `_rev` does not match the stored one is
rejected) instead of being told what status to return. A fake handed a
conflict directly proves the code branches on 409; it does not prove a 409
can happen. That distinction is the one this repo's other conflict tests get
wrong, so it is worth paying for here.
"""
from unittest.mock import patch

import pytest

from agora_runner import tools_dispatch, vault
from tests.test_vault_conditional_writes import FakeCouch

PATH = "notes/issues.md"
PERSONA = {"name": "nova"}
CONV = "conv-1"


@pytest.fixture(autouse=True)
def _clean_rev_memory():
    tools_dispatch._READ_REVS.clear()
    yield
    tools_dispatch._READ_REVS.clear()


def _run(couch, name, args, conversation_id=CONV, active_step=None):
    with patch.object(vault, "couch_req", couch.req), \
            patch.object(tools_dispatch, "audit", lambda *a, **k: None):
        return tools_dispatch.execute_tool(
            name, args, PERSONA, conversation_id, active_step=active_step)


def test_a_write_after_someone_else_wrote_loses_instead_of_clobbering():
    """The whole point, end to end through the tool belt. Edvard types a
    comment while a cycle is mid-edit; the cycle's write must fail rather
    than take his words with it."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")

    _run(couch, "vault_read", {"path": PATH})
    couch.seed(PATH, "# Issues\n\n- one\n- his\n")  # the other writer
    out = _run(couch, "vault_write", {"path": PATH, "content": "# Issues\n\n- mine\n"})

    assert "409 conflict" in out, out
    assert couch.text(PATH) == "# Issues\n\n- one\n- his\n"


def test_the_conflict_tells_the_model_what_to_do_about_it():
    """The caller is a model with the tool it needs to recover and no way to
    guess that from a bare 409. Retrying the write alone would resend the
    body built from the text it lost to -- the clobber, spelled out."""
    couch = FakeCouch()
    couch.seed(PATH, "one\n")

    _run(couch, "vault_read", {"path": PATH})
    couch.seed(PATH, "theirs\n")
    out = _run(couch, "vault_write", {"path": PATH, "content": "mine\n"})

    assert "vault_read" in out and "write again" in out, out


def test_a_lost_race_is_audited_as_a_failure_and_not_as_a_diff():
    """The recovery sentence is appended to the client's string, so the
    "FAILED" prefix `_audit_vault_write` keys on has to survive it. If it
    did not, the audit log -- the only durable record of what a persona did
    to Edvard's vault -- would render a completed before/after diff for a
    write that never happened."""
    couch = FakeCouch()
    couch.seed(PATH, "one\n")
    entries = []

    with patch.object(vault, "couch_req", couch.req), \
            patch.object(tools_dispatch, "audit",
                         lambda *a, **k: entries.append((a, k))):
        tools_dispatch.execute_tool("vault_read", {"path": PATH}, PERSONA, CONV)
        couch.seed(PATH, "theirs\n")
        tools_dispatch.execute_tool(
            "vault_write", {"path": PATH, "content": "mine\n"}, PERSONA, CONV)

    _args, kwargs = entries[-1]
    assert "after" not in kwargs, kwargs
    assert "409 conflict" in _args[-1], _args


def test_an_uncontested_write_after_a_read_still_lands():
    """A protection that fails the ordinary path is worse than the bug."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")

    _run(couch, "vault_read", {"path": PATH})
    out = _run(couch, "vault_write", {"path": PATH, "content": "# Issues\n\n- two\n"})

    assert out == "written", out
    assert couch.text(PATH) == "# Issues\n\n- two\n"


def test_a_write_with_no_read_before_it_is_still_unconditional():
    """Nothing was promised about a path this conversation never looked at,
    so this write behaves exactly as it did before the change."""
    couch = FakeCouch()
    couch.seed(PATH, "# Issues\n\n- one\n")

    out = _run(couch, "vault_write", {"path": PATH, "content": "fresh\n"})

    assert out == "written", out
    assert couch.text(PATH) == "fresh\n"


def test_two_personas_creating_the_same_new_file_do_not_silently_become_one():
    """`vault_read` on a path holding nothing is a real expectation -- "there
    should still be nothing here" -- and it is a different one from never
    having looked. Remembering only the found revisions would leave the
    create race exactly as silent as it was."""
    couch = FakeCouch()

    assert "[not found:" in _run(couch, "vault_read", {"path": PATH})
    couch.seed(PATH, "theirs\n")  # the other persona got there first
    out = _run(couch, "vault_write", {"path": PATH, "content": "mine\n"})

    assert "409 conflict" in out, out
    assert couch.text(PATH) == "theirs\n"


def test_a_second_write_with_no_new_read_is_not_rejected_forever():
    """The remembered revision is stale the moment the write lands, so it is
    consumed by the write rather than kept. Keeping it would fail every
    following write in the conversation against a revision that can never
    match again -- a protection that bricks the tool."""
    couch = FakeCouch()
    couch.seed(PATH, "one\n")

    _run(couch, "vault_read", {"path": PATH})
    assert _run(couch, "vault_write", {"path": PATH, "content": "two\n"}) == "written"
    out = _run(couch, "vault_write", {"path": PATH, "content": "three\n"})

    assert out == "written", out
    assert couch.text(PATH) == "three\n"


def test_one_conversation_does_not_borrow_another_conversation_s_read():
    """Two conversations editing one file are the exact collision this
    guards, and the memory is keyed by both or it is wrong in both
    directions -- borrowing a *current* revision would call a race won, and
    borrowing a *stale* one rejects a write nobody promised anything about.

    So the other writer lands here deliberately. With the conversation
    dropped from the key, conv-b inherits conv-a's now-stale revision and
    409s on a write that was always unconditional. Without the interloper
    this test passes under path-only keying, which is the version I wrote
    first: the borrowed revision was still current, so the write landed
    either way and the mutation went unnoticed.
    """
    couch = FakeCouch()
    couch.seed(PATH, "one\n")

    _run(couch, "vault_read", {"path": PATH}, conversation_id="conv-a")
    couch.seed(PATH, "theirs\n")
    out = _run(couch, "vault_write", {"path": PATH, "content": "mine\n"},
               conversation_id="conv-b")

    assert out == "written", out
    assert couch.text(PATH) == "mine\n"
    # ...and conv-a's expectation survives, still waiting to protect the
    # write that conversation actually makes.
    assert tools_dispatch._READ_REVS[("conv-a", PATH)] == "1-x"


def test_scoped_write_carries_the_read_revision_too():
    """The workflow-step write is the same overwrite through a different
    door, and fixing one door is how the copy you skipped becomes a bug you
    introduced."""
    couch = FakeCouch()
    couch.seed(PATH, "draft\n")
    step = {"filepath": PATH, "toolWhitelist": ["scoped_write"]}

    _run(couch, "vault_read", {"path": PATH}, active_step=step)
    couch.seed(PATH, "theirs\n")
    out = _run(couch, "scoped_write", {"content": "mine\n"}, active_step=step)

    assert "409 conflict" in out, out
    assert couch.text(PATH) == "theirs\n"


def test_the_revision_memory_does_not_grow_for_the_life_of_the_pod():
    """Process-lifetime state in a pod that runs for days, across every
    persona and conversation. Eviction is oldest-first and costs only the
    protection on that one path -- never a rejected or lost write."""
    for i in range(tools_dispatch._READ_REVS_MAX + 20):
        tools_dispatch._remember_read_rev(CONV, f"notes/{i}.md", "1-x")

    assert len(tools_dispatch._READ_REVS) == tools_dispatch._READ_REVS_MAX
    assert (CONV, "notes/0.md") not in tools_dispatch._READ_REVS
    assert (CONV, f"notes/{tools_dispatch._READ_REVS_MAX + 19}.md") in tools_dispatch._READ_REVS


def test_an_evicted_path_falls_back_to_the_old_behaviour_not_to_a_failure():
    """The one thing eviction must never do is turn a working write into a
    rejected one."""
    couch = FakeCouch()
    couch.seed(PATH, "one\n")

    _run(couch, "vault_read", {"path": PATH})
    for i in range(tools_dispatch._READ_REVS_MAX):
        tools_dispatch._remember_read_rev(CONV, f"notes/filler{i}.md", "1-x")
    assert (CONV, PATH) not in tools_dispatch._READ_REVS

    couch.seed(PATH, "theirs\n")
    assert _run(couch, "vault_write", {"path": PATH, "content": "mine\n"}) == "written"
