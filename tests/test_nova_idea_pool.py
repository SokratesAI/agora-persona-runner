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

from agora_runner import board_records, board_write
from tests.test_board_records import writable

from agora_runner import nova_idea_pool
from agora_runner.nova_idea_pool import (
    COMMENT_HEADING,
    STALE_CANDIDATE,
    add_comment,
    comment as pool_comment,
    decide,
    find_candidate,
    insert_discarded,
    parse_history,
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
| Local model fallback (Ollama/LocalAI) | The box can't afford a resident model — see 3 |

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


def test_a_missing_discarded_table_is_an_error_not_a_silent_no_op():
    """The failure direction that matters: returning the markdown unchanged
    with no error would report a decision saved and lose the reason.

    The approve half of this test went with `insert_board_row` and
    `insert_detail` in the #203 conversion -- `board_write.add_row` raises
    `RowRefused` rather than returning a string, and the board it writes to
    cannot be missing a table because it is not a table.
    """
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
        # Issue #203: an approval lands in the records, not in `ideas`
        # above. The markdown is still the fixture the store is built from,
        # so both halves of `decide` are exercised against the same board he
        # actually has -- but only the reject half writes a document.
        _, self.store = writable(board="idea", markdown=ideas)

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
            patch.object(nova_idea_pool, "vault_write_path", vault.write), \
            patch.object(nova_idea_pool, "board_store", vault.store):
        return fn()


def _boarded(vault):
    """His ideas board as records: `{number: item}`, plus the write-ups.

    Every approval assertion below goes through this rather than through
    `parse_board` of a string. A test that reads the markdown agrees with a
    converted and an unconverted `decide` alike, so it cannot tell the two
    apart -- `tests/test_tools_board_size.py` set that rule and this is the
    same reason.
    """
    contents = board_records.contents("idea", store=vault.store)
    return ({item["number"]: item for item in contents["items"]},
            contents["details"])


def _history(vault, markdown=None):
    """`parse_history` with both its halves.

    The approvals come out of the records and the rejections out of the
    markdown -- see `parse_history` for why those are two places -- so a test
    that wants either has to hand it both. `markdown` overrides the vault's
    copy for the tests that build a document by hand.
    """
    return parse_history(
        vault.docs[nova_idea_pool.IDEAS_PATH] if markdown is None else markdown,
        board_records.contents("idea", store=vault.store))


def test_approve_boards_the_row_and_empties_the_pool_slot():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][1]["title"]
    ok, message = _run(vault, lambda: decide(1, title, "approve", "", "08-25"))
    assert ok, message

    items, _ = _boarded(vault)
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
    assert 115 in _boarded(vault)[0]


def test_a_failed_board_write_leaves_the_pool_alone():
    """The other direction: nothing was decided, so nothing may be removed.

    An approval no longer writes a vault document, so the failure is
    injected where the write now is -- `board_write.add_row` raising through
    the store. `_board_the_candidate` catches `WriteRefused`/`BoardDamaged`
    and returns `(False, ...)`, and the pool write is only reached on a
    success.
    """
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]

    def _refuse(_doc):
        raise board_write.BoardDamaged("the board came back damaged")

    vault.store.write_row = _refuse
    ok, message = _run(vault, lambda: decide(0, title, "approve", "", "08-25"))
    assert not ok
    assert "damaged" in message
    assert nova_idea_pool.POOL_PATH not in vault.writes
    assert len(parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"]) == 2


def test_his_comment_rides_onto_the_row_and_into_the_discard_reason():
    vault = _Vault()
    candidates = parse_pool(LIVE_POOL)["candidates"]
    _run(vault, lambda: decide(0, candidates[0]["title"], "approve", "do the cheap half", "08-25"))
    assert "do the cheap half" in "\n".join(_boarded(vault)[1].values())

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


def test_a_second_approve_of_the_same_idea_does_not_board_it_twice():
    """Reviewer finding: two taps are two threads, and both pass the
    staleness check before either has emptied the pool. The loser of the
    compare-and-swap re-reads, recomputes the next number, and boards the
    same idea again -- and both requests report success, so nothing says he
    now has two identical rows.

    Simulated by decoding the retry rather than by driving two threads: the
    fake refuses the first `ideas.md` write with a 409 exactly once, which
    is the state the losing request sees, and the board it re-reads already
    carries the winner's row.
    """
    candidate = parse_pool(LIVE_POOL)["candidates"][0]
    winner = _Vault()
    _run(winner, lambda: decide(0, candidate["title"], "approve", "", "08-25"))
    assert sum(1 for i in _boarded(winner)[0].values()
               if i["title"] == candidate["title"]) == 1

    # The loser: the pool still holds the candidate (it read before the
    # winner's removal) and his board already has the row. Same store, so
    # the second request sees exactly what the first one wrote.
    loser = _Vault()
    loser.store = winner.store
    ok, _ = _run(loser, lambda: decide(0, candidate["title"], "approve", "", "08-25"))
    assert ok
    rows = [i for i in _boarded(loser)[0].values()
            if i["title"] == candidate["title"]]
    assert len(rows) == 1, f"boarded {len(rows)} times, not once"
    # And it still cleared the pool, so the candidate does not linger.
    assert candidate["title"] not in [
        c["title"] for c in parse_pool(loser.docs[nova_idea_pool.POOL_PATH])["candidates"]]


def test_an_explicitly_passed_store_is_the_one_written_to():
    """`store=` on `decide` has to reach the write, not just be accepted.

    Found by a mutation, not by a review: `store = store or board_store`
    collapsed to `store = board_store` and every test still passed, because
    they all patch the module global and pass nothing. That makes the
    argument a decoration -- a caller handing `decide` a store would have
    its approval land somewhere else, silently. This is the only test that
    passes one, so it is the only thing holding the parameter honest.
    """
    vault = _Vault()
    other = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    with patch.object(nova_idea_pool, "vault_read_path_rev", vault.read), \
            patch.object(nova_idea_pool, "vault_write_path", vault.write), \
            patch.object(nova_idea_pool, "board_store", other.store):
        ok, message = decide(0, title, "approve", "", "08-25", store=vault.store)
    assert ok, message
    assert title in [i["title"] for i in _boarded(vault)[0].values()]
    assert title not in [i["title"] for i in _boarded(other)[0].values()]


def test_a_different_idea_with_a_different_title_still_boards():
    """The guard is a title match, so it has to not swallow real work."""
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(0, candidates[0]["title"], "approve", "", "08-25"))
    _run(vault, lambda: decide(0, candidates[1]["title"], "approve", "", "08-25"))
    titles = [i["title"] for i in _boarded(vault)[0].values()]
    assert candidates[0]["title"] in titles
    assert candidates[1]["title"] in titles


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


def test_history_reads_back_a_decision_the_pool_itself_made():
    """The round trip that matters: decide, then show him what he decided.

    Written against `decide` rather than against a hand-built fixture on
    purpose. The history parses the writes `insert_detail` and
    `insert_discarded` make, so a fixture would let the two drift and still
    pass -- which is the whole class of bug this repo keeps shipping guards
    for. Break the byline in `insert_detail` and this test fails.
    """
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(
        0, candidates[0]["title"], "reject",
        "No. I do not care about the cost per journal.", "08-25"))
    _run(vault, lambda: decide(
        0, candidates[1]["title"], "approve", "Yes, and make it loud.", "08-25"))

    history = _history(vault)

    approved = [a for a in history["approved"] if a["title"] == candidates[1]["title"]]
    assert len(approved) == 1
    assert approved[0]["comment"] == "Yes, and make it loud."
    assert approved[0]["dated"] == "08-25"
    assert approved[0]["number"] == 115

    rejected = [r for r in history["rejected"] if r["title"] == candidates[0]["title"]]
    assert len(rejected) == 1
    assert rejected[0]["why"] == "No. I do not care about the cost per journal."


def test_history_leaves_out_write_ups_the_pool_did_not_write():
    """`## 114` is a row he typed himself. It carries no pool byline, and
    reporting it as something he approved would be a made-up decision."""
    history = _history(_Vault())
    assert [a["title"] for a in history["approved"]] == []


def test_history_shows_a_discarded_row_that_predates_the_pool():
    """The `## Discarded` table already had rows before the pool existed and
    no page has ever rendered any of them. They are still decisions."""
    history = _history(_Vault())
    assert history["rejected"] == [
        {"title": "Local model fallback (Ollama/LocalAI)",
         "why": "The box can't afford a resident model — see 3"},
    ]


def test_history_keeps_an_approval_he_said_nothing_about():
    """Silence is a decision too -- it just has no comment on it. Dropping
    these would under-report what he approved."""
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(0, candidates[0]["title"], "approve", "", "08-25"))
    approved = _history(vault)["approved"]
    assert [(a["title"], a["comment"]) for a in approved] == [
        (candidates[0]["title"], ""),
    ]


def test_history_does_not_read_past_the_discarded_table():
    """`## Discarded` is followed by `# Details`, which is full of `|` in
    wikilinks. A parser that keeps going swallows write-ups as rejections."""
    history = _history(_Vault(), LIVE_IDEAS + "\n## 92 — A project dashboard\n\n"
                       "| not | a rejection |\n")
    assert [r["title"] for r in history["rejected"]] == [
        "Local model fallback (Ollama/LocalAI)"]


def test_history_reports_a_missing_ideas_file_rather_than_failing():
    with patch.object(nova_idea_pool, "vault_read_path_rev", return_value=(None, None)):
        payload = nova_idea_pool.history_payload()
    assert payload == {"approved": [], "rejected": [], "missing": True}


def test_a_reason_with_a_pipe_in_it_does_not_break_his_table():
    """He types into a `<textarea>`, so a reason can carry anything.

    A raw `|` opens a third cell against a two-column header and leaves a
    permanently malformed table in a file he reads in Obsidian, and the
    history then shows him the fragment before the pipe as the whole reason.
    Reviewer finding on this PR, reproduced before it was fixed.
    """
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(
        0, candidates[0]["title"], "reject", "No good | too expensive", "08-25"))
    written = vault.docs[nova_idea_pool.IDEAS_PATH]

    row = [l for l in written.split("\n") if candidates[0]["title"] in l
           and l.startswith("|")][0]
    assert len(row.strip("|").split("|")) == 2 + row.count(r"\|")
    assert row.count("|") - row.count(r"\|") == 3  # two cells, three delimiters

    rejected = _history(vault, written)["rejected"]
    assert [r["why"] for r in rejected if r["title"] == candidates[0]["title"]] == [
        "No good | too expensive",
    ]


def test_a_reason_written_over_several_lines_survives_whole():
    """A newline inside a table cell ends the row. Every word he typed has to
    come back, and the table has to stay a table."""
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(
        0, candidates[0]["title"], "reject",
        "No.\nI do not care about the cost per journal.", "08-25"))
    written = vault.docs[nova_idea_pool.IDEAS_PATH]

    assert "|\nI do not care" not in written
    rejected = _history(vault, written)["rejected"]
    assert [r["why"] for r in rejected if r["title"] == candidates[0]["title"]] == [
        "No. I do not care about the cost per journal.",
    ]


def test_a_multi_line_approval_comment_is_not_cut_at_the_first_line():
    """`decide` writes `You said: <everything he typed>` into the write-up, so
    a two-paragraph comment is a `You said:` line followed by more lines.
    Reading only the first shows him less than he wrote, silently."""
    candidates = parse_pool(LIVE_POOL)["candidates"]
    vault = _Vault()
    _run(vault, lambda: decide(
        0, candidates[0]["title"], "approve",
        "This is worth doing.\nPlease raise the priority too.", "08-25"))
    approved = _history(vault)["approved"]
    assert [a["comment"] for a in approved] == [
        "This is worth doing.\nPlease raise the priority too.",
    ]


# --- The Comment button (idea #92's third answer) --------------------------
#
# What phase 1 shipped was Approve, Reject and Skip, and Skip writes
# nothing, so a card he was not ready to decide had exactly two outcomes:
# decide it anyway, or lose whatever he had typed into the box.


def test_a_comment_keeps_the_candidate_and_writes_only_the_pool():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    ok, message = _run(vault, lambda: pool_comment(0, title, "narrower than this", "2026-08-30"))
    assert ok, message
    # His ideas file is not touched at all: this is not a decision.
    assert vault.writes == [nova_idea_pool.POOL_PATH]
    pool = parse_pool(vault.docs[nova_idea_pool.POOL_PATH])
    assert [c["title"] for c in pool["candidates"]] == [
        c["title"] for c in parse_pool(LIVE_POOL)["candidates"]]
    assert pool["candidates"][0]["comments"] == [
        {"dated": "2026-08-30", "text": "narrower than this"}]
    # And it landed on the one he was looking at, not on its neighbour.
    assert pool["candidates"][1]["comments"] == []


def test_a_comment_does_not_leak_into_the_body_i_wrote():
    """`body` is what `decide` copies into his write-up. His own words
    arriving back on his board under my byline is the failure here."""
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: pool_comment(0, title, "not convinced", "2026-08-30"))
    after = parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"][0]
    assert after["body"] == parse_pool(LIVE_POOL)["candidates"][0]["body"]
    assert "not convinced" not in after["body"]


def test_a_second_comment_appends_rather_than_replacing():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: pool_comment(0, title, "first thought", "2026-08-30"))
    _run(vault, lambda: pool_comment(0, title, "second thought", "2026-08-31"))
    said = parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"][0]["comments"]
    assert [c["text"] for c in said] == ["first thought", "second thought"]
    # One heading, not one per comment.
    assert vault.docs[nova_idea_pool.POOL_PATH].count(COMMENT_HEADING) == 1


def test_a_two_paragraph_comment_survives_the_round_trip():
    """The box is a textarea. A note stored as one line is a note he wrote
    twice as much of as the card shows him."""
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: pool_comment(0, title, "the first half\nand the second", "2026-08-30"))
    said = parse_pool(vault.docs[nova_idea_pool.POOL_PATH])["candidates"][0]["comments"]
    assert said == [{"dated": "2026-08-30", "text": "the first half\nand the second"}]


def test_an_earlier_comment_rides_onto_the_row_when_he_finally_approves():
    """He notes something today and approves next week. Without this the
    note goes to the grave with the pool document."""
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: pool_comment(0, title, "only the cheap half", "2026-08-30"))
    ok, _ = _run(vault, lambda: decide(0, title, "approve", "and rename it", "08-31"))
    assert ok
    write_up = "\n".join(_boarded(vault)[1].values())
    assert "only the cheap half" in write_up
    assert "and rename it" in write_up
    # One `You said:` block, because `parse_history` keeps only the last one
    # and two blocks would show him the newer note as the whole of it.
    assert write_up.count("You said:") == 1
    approved = _history(vault)["approved"]
    assert approved[0]["comment"] == "only the cheap half\nand rename it"


def test_an_earlier_comment_becomes_the_discard_reason():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    _run(vault, lambda: pool_comment(0, title, "we already have this", "2026-08-30"))
    _run(vault, lambda: decide(0, title, "reject", "", "08-31"))
    assert "| we already have this |" in vault.docs[nova_idea_pool.IDEAS_PATH]


def test_an_empty_comment_is_refused_before_any_write():
    vault = _Vault()
    title = parse_pool(LIVE_POOL)["candidates"][0]["title"]
    ok, message = _run(vault, lambda: pool_comment(0, title, "   ", "2026-08-30"))
    assert not ok
    assert "empty" in message
    assert vault.writes == []


def test_a_stale_comment_writes_nothing_at_all():
    """A refill that ran while the card was open renumbers everything below
    it, and annotating the wrong idea is worse than refusing."""
    vault = _Vault()
    ok, message = _run(vault, lambda: pool_comment(0, "an idea that left the pool", "x", "2026-08-30"))
    assert not ok
    assert message == STALE_CANDIDATE
    assert vault.writes == []


def test_a_comment_block_stays_inside_its_own_candidate():
    """`remove_candidate` cuts to the next `## `, so a comment appended past
    the end of a block would be deleted with the wrong idea -- or survive
    the right one."""
    markdown, error = add_comment(LIVE_POOL, parse_pool(LIVE_POOL)["candidates"][0]["title"],
                                  "mine", "2026-08-30")
    assert error == ""
    remaining = remove_candidate(markdown, parse_pool(LIVE_POOL)["candidates"][0]["title"])
    assert "mine" not in remaining
    assert COMMENT_HEADING not in remaining
    assert len(parse_pool(remaining)["candidates"]) == 1



# ---------------------------------------------------------------------------
# Issue #203: the records-shaped rules.
#
# Every dict below is hand-built and none of these tests holds a line of
# board markdown, on purpose. A test that writes a table and parses it
# agrees with a converted and an unconverted reader alike, so it cannot
# tell the two apart -- and `board_records.contents` hands back shapes a
# board file cannot express, which is exactly where a reader that assumed
# the parser's invariants breaks.


def _contents(items):
    """`parse_board`'s four keys, built by hand rather than parsed."""
    return {"captures": [], "captureReplies": {}, "items": items, "details": {}}


def _row(number, title):
    return {"number": number, "title": title, "status": "🟡 In progress",
            "statusKey": "in-progress", "done": False, "updated": "09-09",
            "priority": "🔵 Medium", "priorityKey": "medium",
            "project": "", "milestone": "", "size": ""}


def test_already_boarded_in_contents_matches_on_the_title():
    contents = _contents([_row(114, "  a fine idea  ")])
    assert nova_idea_pool._already_boarded_in_contents(contents, "a fine idea")
    assert not nova_idea_pool._already_boarded_in_contents(contents, "another one")


def test_already_boarded_in_contents_refuses_an_empty_title():
    """An empty candidate title would match every row whose title is blank,
    so the guard would report `True` and silently drop a real write."""
    assert not nova_idea_pool._already_boarded_in_contents(
        _contents([_row(114, "")]), "   ")


def test_the_module_cannot_reach_the_parser_at_all():
    """`board-records.md` bans a facade, and this is what that means here.

    The guard used to be a monkeypatched `parse_board` that raised, because
    the module still imported one and a twin that quietly fell back to it
    would return the *right answer* for every caller still holding markdown
    -- nothing but a raising parser catches that. There is nothing left to
    patch: `decide` writes through `board_write.add_row` and `parse_history`
    reads `board_records.contents`, so the import is gone. The absence is
    the assertion now, and it is the stronger one -- a name that is not
    there cannot be called from a branch no test covers.
    """
    assert not hasattr(nova_idea_pool, "parse_board")
    assert "parse_board" not in open(nova_idea_pool.__file__).read()


def test_approving_parses_his_ideas_board_exactly_once(monkeypatch):
    """Two questions, one read of the board.

    The dedup guard and the number used to parse the file separately. On a
    string that is two identical answers; on `board_records.contents` it is
    two `_all_docs` pairs a write can land between -- and the write that
    lands between them is the concurrent approve this guard exists to
    absorb, so it would answer "no row carries this title" against one
    board and "the highest number is 114" against another.
    """
    vault = _Vault()
    calls = []
    real = vault.store.read_rows

    def _counted(board):
        calls.append(board)
        return real(board)

    monkeypatch.setattr(vault.store, "read_rows", _counted)
    title = parse_pool(LIVE_POOL)["candidates"][1]["title"]
    ok, message = _run(vault, lambda: decide(1, title, "approve", "", "08-25"))
    assert ok, message
    # One for the dedup guard's `contents`; the rest belong to `add_row`'s
    # own before/after check, which is tested where it lives. What must not
    # happen is this module asking twice for its own two questions.
    assert calls[:1] == ["idea"]
    assert len(calls) <= 4, calls
