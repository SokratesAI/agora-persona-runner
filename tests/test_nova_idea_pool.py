"""The idea pool -- idea #92, phase 1.

`LIVE_POOL` and `LIVE_IDEAS` are cut from the real documents as they stood
on 2026-08-25, including the two shapes that broke the first draft of the
parser: a blank line between a candidate's heading and its `project:`
field, and a `## Board` table whose first cell is an escaped Obsidian
wikilink. Both are load-bearing rather than decoration -- with the blank
line removed every one of these tests passes against a parser that reads
no fields at all, which is how the bug got as far as a browser.
"""

from unittest.mock import patch

from agora_runner import nova_idea_pool
from agora_runner.nova_idea_pool import (
    STALE_CANDIDATE,
    decide,
    find_candidate,
    insert_board_row,
    insert_detail,
    insert_discarded,
    next_number,
    parse_pool,
    remove_candidate,
    request_generate,
    set_generate_flag,
)


LIVE_POOL = """---
type: log
status: capture
tags: [agora, nova, ideas, pool]
contract: Ten candidate ideas Nova generated, waiting on approve or reject.
generate-requested: no
---

# Idea pool

## Say what a cycle cost on its own journal card

project: Nova
priority: 🔵 Medium

Every journal card tells you what a cycle did and nothing about what it cost.

## Watch for a heartbeat that stopped firing

project: Agora
priority: 🟠 High

There are five heartbeats now and nothing notices when one stops.
"""

LIVE_IDEAS = """---
type: log
---

- \n
## Board

| # | Idea | Status | Updated | Priority |
|---|------|--------|---------|---|
| [[#114 — Home automation on the extra node\\|114]] | Home automation on the extra node | ⚪ Backlog | 08-24 | 🔵 Medium |
| [[#92 — A project dashboard\\|92]] | A project dashboard | 🟡 In progress | 08-24 | 🟠 High |

## Discarded

Kept so neither of us re-proposes them.

| Idea | Why not |
|---|---|
| Local model fallback | The box can't afford a resident model |

---

# Details

## 114 — Home automation on the extra node

Something about the extra node.
"""


def test_fields_survive_the_blank_line_after_the_heading():
    """The bug the first draft shipped: every field read as body prose.

    The pool puts a blank line between `## <title>` and `project:`, so a
    parser that stops looking for fields at the first body line stops at
    that blank one and reads the rating as prose. All ten candidates came
    back with an empty priority, which the page then rendered as a chip
    with no word in it.
    """
    pool = parse_pool(LIVE_POOL)
    assert [c["priority"] for c in pool["candidates"]] == ["🔵 Medium", "🟠 High"]
    assert [c["project"] for c in pool["candidates"]] == ["Nova", "Agora"]
    assert [c["priorityKey"] for c in pool["candidates"]] == ["medium", "high"]
    # And the fields are not *also* left in the body.
    assert "project:" not in pool["candidates"][0]["body"]
    assert pool["candidates"][0]["body"].startswith("Every journal card")


def test_a_field_further_down_stays_prose():
    """The other half of that rule, and the reason it is not just "find any
    `priority:` line": a candidate whose body discusses priorities must not
    have its rating silently replaced by the sentence it is describing."""
    markdown = LIVE_POOL + "\n## Third\n\nproject: Nova\n\nSome prose.\npriority: not a field\n"
    third = parse_pool(markdown)["candidates"][2]
    assert third["priority"] == ""
    assert "priority: not a field" in third["body"]


def test_generate_flag_round_trips():
    assert parse_pool(LIVE_POOL)["generateRequested"] is False
    assert parse_pool(set_generate_flag(LIVE_POOL, True))["generateRequested"] is True
    assert parse_pool(set_generate_flag(set_generate_flag(LIVE_POOL, True), False))[
        "generateRequested"] is False


def test_generate_flag_is_added_when_the_key_is_missing():
    """A pool document written by hand may not carry the key at all, and
    dropping the request on the floor would leave his button doing nothing
    while reporting success."""
    without = LIVE_POOL.replace("generate-requested: no\n", "")
    assert parse_pool(without)["generateRequested"] is False
    assert parse_pool(set_generate_flag(without, True))["generateRequested"] is True


def test_the_index_alone_is_not_an_address():
    """`title` is checked against `index`, so a pool a refill rewrote while
    the page was open is a refusal rather than the wrong idea decided."""
    candidates = parse_pool(LIVE_POOL)["candidates"]
    found, why = find_candidate(LIVE_POOL, 0, candidates[0]["title"])
    assert why is None and found["title"] == candidates[0]["title"]

    assert find_candidate(LIVE_POOL, 0, candidates[1]["title"]) == (None, STALE_CANDIDATE)
    assert find_candidate(LIVE_POOL, 9, "anything") == (None, STALE_CANDIDATE)
    assert find_candidate(LIVE_POOL, -1, "anything") == (None, STALE_CANDIDATE)


def test_remove_takes_one_candidate_and_leaves_the_rest():
    candidates = parse_pool(LIVE_POOL)["candidates"]
    after = remove_candidate(LIVE_POOL, candidates[0]["title"])
    left = parse_pool(after)["candidates"]
    assert [c["title"] for c in left] == [candidates[1]["title"]]
    # The frontmatter and the page heading are untouched -- this is a
    # document a cycle reads, not a payload.
    assert after.startswith("---\ntype: log")
    assert "# Idea pool" in after


def test_next_number_reads_the_board():
    assert next_number(LIVE_IDEAS) == 115


def test_next_number_on_an_empty_board_starts_at_one():
    assert next_number("---\ntype: log\n---\n\n## Board\n\n| # | Idea |\n|---|---|\n") == 1


def test_an_approved_row_renders_as_five_cells():
    """The wikilink's `|` has to be escaped or the row gains a sixth cell
    against five headers, which is a table Obsidian draws wrong on his
    phone. Checked by re-parsing rather than by string match, so the
    assertion is about what the board reader sees."""
    from agora_runner.nova_boards import parse_board

    updated, error = insert_board_row(LIVE_IDEAS, 115, "A new idea", "🟠 High", "08-25")
    assert error == ""
    items = {i["number"]: i for i in parse_board(updated)["items"]}
    assert items[115]["title"] == "A new idea"
    assert items[115]["priority"] == "🟠 High"
    assert items[115]["status"] == "⚪ Backlog"
    assert items[115]["updated"] == "08-25"
    # Newest first, matching the file: #115 above #114, not appended below.
    assert updated.index("#115 —") < updated.index("#114 —")


def test_the_detail_section_exists_so_the_wikilink_is_not_dead():
    updated, error = insert_board_row(LIVE_IDEAS, 115, "A new idea", "🟠 High", "08-25")
    assert error == ""
    updated, error = insert_detail(updated, 115, "A new idea", "The body.", "08-25")
    assert error == ""

    from agora_runner.nova_boards import parse_board

    details = parse_board(updated)["details"]
    assert 115 in details
    assert "The body." in details[115]
    # Directly under `# Details`, above the older write-ups.
    assert updated.index("## 115 —") < updated.index("## 114 —")


def test_a_missing_board_table_is_an_error_not_a_silent_no_op():
    """The failure direction that matters: returning the markdown unchanged
    with no error would report a decision saved and lose the row."""
    _, error = insert_board_row("---\ntype: log\n---\n\nNo board here.\n", 1, "x", "", "08-25")
    assert error
    _, error = insert_detail("No details here.\n", 1, "x", "y", "08-25")
    assert error
    _, error = insert_discarded("No discarded here.\n", "x", "y")
    assert error


def test_a_rejected_candidate_keeps_its_reason():
    updated, error = insert_discarded(LIVE_IDEAS, "A bad idea", "Too expensive (08-25)")
    assert error == ""
    assert "| A bad idea | Too expensive (08-25) |" in updated
    # In the Discarded table, not the board.
    assert updated.index("| A bad idea |") > updated.index("## Discarded")


class _Vault:
    """A two-document fake vault: the pool and his ideas file.

    Records every write so a test can assert on the *order* of the two,
    which is the part of `decide` that has a real design decision in it.
    """

    def __init__(self, pool=LIVE_POOL, ideas=LIVE_IDEAS, fail=None):
        self.docs = {nova_idea_pool.POOL_PATH: pool, nova_idea_pool.IDEAS_PATH: ideas}
        self.fail = fail or set()
        self.writes = []

    def read(self, path):
        return self.docs.get(path), "1-abc"

    def write(self, path, body, if_rev=None):
        self.writes.append(path)
        if path in self.fail:
            return "409 conflict"
        self.docs[path] = body
        return "written"


def _run(vault, fn):
    with patch.object(nova_idea_pool, "vault_read_path_rev", vault.read), \
            patch.object(nova_idea_pool, "vault_write_path", vault.write):
        return fn()


def test_approve_boards_the_row_and_empties_the_pool_slot():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][1]["title"]
    ok, message = _run(vault, lambda: decide(1, title, "approve", "", "08-25"))
    assert ok, message

    from agora_runner.nova_boards import parse_board

    items = {i["number"]: i for i in parse_board(vault.docs[nova_idea_pool.IDEAS_PATH])["items"]}
    assert items[115]["title"] == title
    # The candidate's own rating rode across rather than being re-guessed.
    assert items[115]["priority"] == "🟠 High"
    assert [c["title"] for c in parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"]] \
        == [parse_pool(LIVE_POOL)["candidates"][0]["title"]]


def test_his_ideas_file_is_written_before_the_pool():
    """Two documents, two revisions, no transaction -- so the half-done
    state has to be the recoverable one. Writing the pool first and failing
    the second write loses a decision he made; this order leaves a
    candidate he can decide again."""
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: decide(0, title, "reject", "no thanks", "08-25"))
    assert vault.writes == [nova_idea_pool.IDEAS_PATH, nova_idea_pool.POOL_PATH]


def test_a_failed_pool_write_is_reported_rather_than_called_success():
    """He will see the candidate again, so the message has to say why."""
    vault = _Vault(fail={nova_idea_pool.POOL_PATH})
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    ok, message = _run(vault, lambda: decide(0, title, "approve", "", "08-25"))
    assert not ok
    assert "still in the pool" in message
    # And the board row really did land, which is what the message claims.
    assert "115" in vault.docs[nova_idea_pool.IDEAS_PATH]


def test_a_failed_board_write_leaves_the_pool_alone():
    """The other direction: nothing was decided, so nothing may be removed."""
    vault = _Vault(fail={nova_idea_pool.IDEAS_PATH})
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    ok, _ = _run(vault, lambda: decide(0, title, "approve", "", "08-25"))
    assert not ok
    assert nova_idea_pool.POOL_PATH not in vault.writes
    assert len(parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"]) == 2


def test_his_comment_rides_onto_the_row_and_into_the_discard_reason():
    vault = _Vault()
    candidates = parse_pool(LIVE_POOL)["candidates"]
    _run(vault, lambda: decide(0, candidates[0]["title"], "approve", "do the cheap half", "08-25"))
    assert "do the cheap half" in vault.docs[nova_idea_pool.IDEAS_PATH]

    vault = _Vault()
    _run(vault, lambda: decide(0, candidates[0]["title"], "reject", "already have this", "08-25"))
    assert "| already have this |" in vault.docs[nova_idea_pool.IDEAS_PATH]


def test_a_reject_with_no_reason_still_carries_the_date():
    """A discarded row with an empty reason is one the next refill re-offers
    and he rejects again."""
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: decide(0, title, "reject", "", "08-25"))
    assert "| Rejected 08-25 |" in vault.docs[nova_idea_pool.IDEAS_PATH]


def test_a_stale_decision_writes_nothing_at_all():
    vault = _Vault()
    ok, message = _run(vault, lambda: decide(0, "a title that moved", "approve", "", "08-25"))
    assert not ok and message == STALE_CANDIDATE
    assert vault.writes == []


def test_generate_only_sets_a_flag():
    """Rule 9: this process has no model access and the button must never
    become one. All it may do is ask."""
    vault = _Vault()
    ok, _ = _run(vault, request_generate)
    assert ok
    assert vault.writes == [nova_idea_pool.POOL_PATH]
    assert parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["generateRequested"] is True
    # And it did not touch a candidate on the way past.
    assert len(parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"]) == 2


def test_the_pool_targets_his_ideas_file_and_nova_s_own_database():
    """Two folders say "nova" and only one is his. A decision has to land in
    `projects/sokrates/projects/nova/`, which routes to `obsidian` and
    therefore reaches his phone; the pool itself is Nova's own and is
    disposable."""
    from agora_runner.nova_capture import CAPTURE_TARGETS

    assert nova_idea_pool.IDEAS_PATH == CAPTURE_TARGETS["ideas"]
    assert nova_idea_pool.POOL_PATH.startswith("projects/sokrates/projects/agora/nova/")
