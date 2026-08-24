"""Claiming a handoff item, and the four ways two cycles can collide over one.

`nova_claims` does no I/O -- the vault `get`/`put` pair around it is what
makes a claim atomic -- so every test here is a pure function against a
literal ledger and an explicit clock. The clock is passed in rather than
read, because the one rule that cannot be tested any other way is the
expiry: a claim goes stale after 45 minutes, and a test that waited for
that would take 45 minutes.

The tests that matter most are the refusals. A granted claim that should
have been refused is the bug this module exists to prevent, and it is
invisible from inside either cycle -- both are told they own the item and
both go to work.
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agora_runner.nova_claims import (
    CLAIM_TTL_MINUTES,
    PROGRESSED,
    ClaimError,
    dumps,
    finished_claims,
    held_by,
    is_stale,
    load,
    progressed_claims,
    prune,
    release,
    slug_for_comment,
    summarise,
    take,
)
from tools import claim as claim_cli

OSLO = ZoneInfo("Europe/Oslo")
T0 = datetime(2026, 8, 14, 11, 0, tzinfo=OSLO)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def empty():
    return {"claims": []}


# --- reading the ledger ----------------------------------------------------


def test_absent_document_is_an_empty_ledger_not_an_error():
    # `vault_tool.py get` exits 0 and prints this for a path with no
    # document, so the first cycle ever to claim anything reads a sentence.
    assert load("[not found: .../claims.json]") == {"claims": []}
    assert load("") == {"claims": []}
    assert load("   \n") == {"claims": []}


def test_a_ledger_that_will_not_parse_is_refused_rather_than_reset():
    # Starting over from empty would silently free every live claim.
    with pytest.raises(ClaimError):
        load("{not json")
    with pytest.raises(ClaimError):
        load('{"claims": "confirm-deploy"}')
    with pytest.raises(ClaimError):
        load('{"claims": [{"item": "x"}]}')


def test_a_real_ledger_opening_with_the_absent_sentence_still_parses():
    text = json.dumps({"claims": [], "note": "[not found: nothing]"})
    assert load(text)["note"] == "[not found: nothing]"


# --- taking an item --------------------------------------------------------


def test_claiming_a_free_item_is_granted_and_recorded():
    ledger = empty()
    granted, message = take(ledger, "confirm-deploy-171", 189, T0, note="handoff item 1")
    assert granted is True
    assert "cycle 189" in message
    row = ledger["claims"][0]
    assert row == {
        "item": "confirm-deploy-171",
        "cycle": 189,
        "state": "open",
        "at": T0.isoformat(),
        "note": "handoff item 1",
    }


def test_a_second_cycle_is_refused_an_item_a_live_cycle_holds():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    granted, message = take(ledger, "confirm-deploy-171", 190, at(20))
    assert granted is False
    assert "held by cycle 189" in message
    assert "20 min ago" in message
    # And the refusal changed nothing: 189 still owns exactly one row.
    assert [(r["item"], r["cycle"]) for r in ledger["claims"]] == [("confirm-deploy-171", 189)]


def test_an_item_already_finished_is_refused_rather_than_re_done():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    release(ledger, "confirm-deploy-171", 189, at(10))
    granted, message = take(ledger, "confirm-deploy-171", 190, at(15))
    assert granted is False
    assert "finished by cycle 189" in message


def test_a_cycle_retaking_its_own_open_claim_is_granted_and_changes_nothing():
    # A cycle that loses the vault compare-and-swap re-reads and retries;
    # it must not be told it lost the item to itself.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0, note="handoff item 1")
    before = json.loads(dumps(ledger))
    granted, message = take(ledger, "confirm-deploy-171", 189, at(5))
    assert granted is True
    assert "already yours" in message
    assert json.loads(dumps(ledger)) == before


def test_a_claim_older_than_the_turn_cap_can_be_taken_over():
    # 45 minutes is the hard turn cap, so a claim older than that belongs
    # to a cycle that was killed. Without this one dead cycle fences off a
    # handoff item forever, and an unclaimable item looks exactly like one
    # somebody is handling.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    assert is_stale(ledger["claims"][0], at(CLAIM_TTL_MINUTES)) is False
    granted, message = take(ledger, "confirm-deploy-171", 190, at(CLAIM_TTL_MINUTES + 1))
    assert granted is True
    assert "taken over from cycle 189" in message
    assert [(r["item"], r["cycle"]) for r in ledger["claims"]] == [("confirm-deploy-171", 190)]
    assert ledger["claims"][0]["took_over_from"] == 189


def test_a_claim_exactly_at_the_cap_is_still_live():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    granted, _ = take(ledger, "confirm-deploy-171", 190, at(CLAIM_TTL_MINUTES))
    assert granted is False


def test_a_finished_claim_is_never_stale_however_old():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    release(ledger, "confirm-deploy-171", 189, at(1))
    assert is_stale(ledger["claims"][0], at(10_000)) is False


def test_two_different_items_do_not_collide():
    ledger = empty()
    assert take(ledger, "confirm-deploy-171", 189, T0)[0] is True
    assert take(ledger, "monday-reprioritise", 190, at(5))[0] is True
    assert len(ledger["claims"]) == 2


# --- slugs -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "ab", "Confirm-Deploy", "confirm deploy", "confirm_deploy", "-leading", "x" * 49],
)
def test_a_slug_that_would_alias_another_slug_is_refused(bad):
    # The whole mechanism is string equality, so `Confirm-Deploy` and
    # `confirm-deploy` would be two claims on one item and both cycles
    # would be told they had it.
    with pytest.raises(ClaimError):
        take(empty(), bad, 189, T0)


def test_a_cycle_number_must_be_a_positive_integer():
    for bad in [0, -1, "189", None]:
        with pytest.raises(ClaimError):
            take(empty(), "confirm-deploy-171", bad, T0)


# --- releasing -------------------------------------------------------------


def test_releasing_marks_it_done_with_the_outcome():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    ok, message = release(ledger, "confirm-deploy-171", 189, at(30), outcome="merged #172")
    assert ok is True
    assert "released by cycle 189" in message
    assert ledger["claims"][0]["state"] == "done"
    assert ledger["claims"][0]["outcome"] == "merged #172"
    assert ledger["claims"][0]["at"] == at(30).isoformat()


def test_a_cycle_cannot_release_an_item_another_cycle_took_over():
    # This is the one moment the loop can notice the duplication happened,
    # so it has to say so rather than shrug.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    take(ledger, "confirm-deploy-171", 190, at(CLAIM_TTL_MINUTES + 1))
    ok, message = release(ledger, "confirm-deploy-171", 189, at(50))
    assert ok is False
    assert "held by cycle 190, not 189" in message
    assert ledger["claims"][0]["state"] == "open"


def test_releasing_something_nobody_claimed_is_refused():
    ok, message = release(empty(), "confirm-deploy-171", 189, T0)
    assert ok is False
    assert "not claimed by anyone" in message


def test_releasing_twice_is_a_no_op_rather_than_an_error():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    release(ledger, "confirm-deploy-171", 189, at(30))
    ok, message = release(ledger, "confirm-deploy-171", 189, at(40))
    assert ok is True
    assert "already released" in message
    assert ledger["claims"][0]["at"] == at(30).isoformat()


# --- stopping without finishing --------------------------------------------
#
# The state that did not exist for eleven days. Every test here is about one
# distinction: `done` means `take` refuses forever, `progressed` means `take`
# grants. A bug in either direction is invisible from inside the cycle that
# hits it -- a wrongly-spent slug reads as "somebody else is on it", and a
# wrongly-granted one is the duplicate work this module exists to stop.


def test_a_progressed_claim_can_be_taken_by_the_next_cycle():
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(30), outcome="three of four pieces built",
            state=PROGRESSED)
    ok, message = take(ledger, "idea-63", 353, at(90))
    assert ok is True
    assert "resumed from cycle 347" in message
    assert "three of four pieces built" in message


def test_a_resumed_claim_carries_the_previous_outcome_forward():
    # The breadcrumb has to survive the moment somebody picks the item up,
    # or the only record of what was already done is gone precisely when
    # it is being acted on.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(30), outcome="worktree per cycle", state=PROGRESSED)
    take(ledger, "idea-63", 353, at(90))
    row = ledger["claims"][0]
    assert row["cycle"] == 353
    assert row["state"] == "open"
    assert row["resumed_from"] == 347
    assert row["resumed_after"] == "worktree per cycle"
    assert len(ledger["claims"]) == 1


def test_a_done_claim_is_still_refused_forever():
    # The mutation guard on the branch above: if `take` had been loosened
    # to grant any released claim rather than progressed ones only, this
    # is the test that fails.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(30), outcome="built", state="done")
    ok, message = take(ledger, "idea-63", 353, at(90))
    assert ok is False
    assert "was finished by cycle 347" in message


def test_a_progressed_claim_is_not_held_by_anyone():
    # It is not a lock. A cycle reading the board must not see 🔒 on a row
    # nobody is working on -- that is the "somebody is on it" answer, and
    # it sinks the row in the ranking.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(30), outcome="half", state=PROGRESSED)
    assert held_by(ledger, at(31)) == {}
    assert finished_claims(ledger) == {}
    assert set(progressed_claims(ledger)) == {"idea-63"}


def test_a_progressed_claim_can_later_be_finished_by_the_same_cycle():
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(10), outcome="half", state=PROGRESSED)
    ok, _ = release(ledger, "idea-63", 347, at(20), outcome="all of it", state="done")
    assert ok is True
    assert ledger["claims"][0]["state"] == "done"
    assert ledger["claims"][0]["outcome"] == "all of it"


def test_a_finished_claim_cannot_be_downgraded_back_to_progressed():
    # The dangerous direction. Reopening a slug whose work really was
    # finished re-grants it, which is the duplicate the ledger exists to
    # stop -- so a late `--progress` on a spent slug is a no-op, not an
    # edit.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(10), outcome="built", state="done")
    ok, message = release(ledger, "idea-63", 347, at(20), outcome="more", state=PROGRESSED)
    assert ok is True
    assert "already released" in message
    assert ledger["claims"][0]["state"] == "done"


def test_releasing_with_a_state_the_ledger_does_not_know_is_an_error():
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    with pytest.raises(ClaimError):
        release(ledger, "idea-63", 347, at(10), state="paused")


def test_a_progressed_claim_is_pruned_on_the_same_clock_as_a_done_one():
    # It is a breadcrumb, not a lock, so it does not need to outlive the
    # window in which a cycle could still be racing it -- and leaving it
    # would grow the one file every claim in this loop rewrites.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(5), outcome="half", state=PROGRESSED)
    prune(ledger, at(23 * 60))
    assert len(ledger["claims"]) == 1
    prune(ledger, at(25 * 60))
    assert ledger["claims"] == []


def test_summarise_says_prog_rather_than_done():
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(5), outcome="half", state=PROGRESSED)
    line = summarise(ledger, at(10))
    assert line.startswith("prog ")
    assert "half" in line


def test_progress_without_an_outcome_is_an_error_not_an_empty_note():
    # The outcome is the entire content of a progressed row. Without it
    # the board says "left this open: no outcome recorded", which implies
    # a note exists and carries none -- worse than an unmarked row.
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    with pytest.raises(ClaimError):
        release(ledger, "idea-63", 347, at(10), state=PROGRESSED)
    with pytest.raises(ClaimError):
        release(ledger, "idea-63", 347, at(10), outcome="   ", state=PROGRESSED)
    assert ledger["claims"][0]["state"] == "open"


def test_a_resumed_breadcrumb_survives_the_resuming_cycle_being_killed():
    """The case the state was invented for, and the one that dropped it.

    A cycle takes a progressed item, is killed at the 45-minute cap
    without releasing, and a later cycle takes the stale claim over. That
    later cycle is the one that most needs to know what was already done.
    """
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(10), outcome="three of four built", state=PROGRESSED)
    take(ledger, "idea-63", 353, at(20))            # resumes it
    take(ledger, "idea-63", 360, at(20 + CLAIM_TTL_MINUTES + 1))   # 353 died
    row = ledger["claims"][0]
    assert row["cycle"] == 360
    assert row["took_over_from"] == 353
    assert row["resumed_after"] == "three of four built"
    assert "three of four built" in summarise(ledger, at(70))


def test_a_progressed_row_is_not_reported_as_held_when_releasing():
    # The one sentence the whole change exists to deny. `held_by` returns
    # nothing for this row and the board prints no lock on it, so the
    # refusal must not say "held".
    ledger = empty()
    take(ledger, "idea-63", 347, T0)
    release(ledger, "idea-63", 347, at(10), outcome="half", state=PROGRESSED)
    ok, message = release(ledger, "idea-63", 353, at(20), outcome="rest", state="done")
    assert ok is False
    assert "is held by" not in message
    assert "was left open by cycle 347" in message
    assert "take it before releasing it" in message


def test_summarise_keeps_the_note_as_well_as_the_outcome():
    # It used to print `outcome or note`, which silently dropped the note
    # from every released row -- a behaviour change nothing asked for.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0, note="handoff item 1")
    release(ledger, "confirm-deploy-171", 189, at(5), outcome="merged #172")
    line = summarise(ledger, at(10))
    assert "handoff item 1" in line
    assert "merged #172" in line


# --- keeping the file small ------------------------------------------------


def test_finished_claims_survive_long_enough_to_answer_an_overlapping_cycle():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    release(ledger, "confirm-deploy-171", 189, at(5))
    prune(ledger, at(23 * 60))
    assert len(ledger["claims"]) == 1


def test_finished_claims_older_than_a_day_are_dropped():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    release(ledger, "confirm-deploy-171", 189, at(5))
    prune(ledger, at(25 * 60))
    assert ledger["claims"] == []


def test_an_open_claim_survives_long_enough_to_be_taken_over():
    # The 45-minute expiry is what lets a later cycle *take over* a dead
    # claim and have the handover recorded, so pruning must not close that
    # window. A day out it is still there and `take` still records it.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    prune(ledger, at(23 * 60))
    assert len(ledger["claims"]) == 1
    granted, message = take(ledger, "confirm-deploy-171", 190, at(23 * 60))
    assert granted is True
    assert "taken over from cycle 189" in message


def test_an_open_claim_older_than_a_day_is_dropped_like_a_released_one():
    # This reverses the old rule, which was "never drop an open claim
    # however stale" on the grounds that it was the only record a cycle had
    # been working on the item. `take` deletes that same row the moment
    # anybody claims the slug again, so the record only ever survived in the
    # case where nothing wanted the slug. Measured Cycle 355: four such rows
    # from three separate days, permanent, in the file every claim rewrites.
    ledger = empty()
    take(ledger, "board-sweep-rest", 203, T0)
    prune(ledger, at(25 * 60))
    assert ledger["claims"] == []


def test_a_dropped_open_claim_leaves_the_slug_freshly_claimable():
    # Pruning is not a release: nothing about it says the work was finished,
    # so the next cycle to want the slug gets it clean rather than refused.
    ledger = empty()
    take(ledger, "board-sweep-rest", 203, T0)
    prune(ledger, at(25 * 60))
    granted, message = take(ledger, "board-sweep-rest", 355, at(25 * 60))
    assert granted is True
    assert "taken over" not in message


def test_pruning_leaves_an_open_claim_a_live_cycle_could_still_hold():
    # The dangerous direction: a row dropped while its cycle is still
    # running would let a second cycle claim the same item.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    prune(ledger, at(30))
    assert held_by(ledger, at(30)) == {"confirm-deploy-171": 189}


# --- the summary a cycle actually reads ------------------------------------


def test_summarise_names_the_three_states():
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0, note="handoff item 1")
    take(ledger, "monday-reprioritise", 188, T0)
    release(ledger, "monday-reprioritise", 188, at(1))
    take(ledger, "stale-thing", 187, T0 - timedelta(hours=3))

    out = summarise(ledger, at(2))
    assert "open  confirm-deploy-171" in out
    assert "handoff item 1" in out
    assert "done  monday-reprioritise" in out
    assert "stale stale-thing" in out


def test_summarise_says_so_when_there_is_nothing():
    assert summarise(empty(), T0) == "no claims"


# --- the CLI, which is where the exit codes live ---------------------------


def write(tmp_path, text):
    path = tmp_path / "claims.json"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_cli_take_writes_the_ledger_and_exits_zero(tmp_path):
    path = write(tmp_path, "")
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"]) == 0
    assert load(open(path, encoding="utf-8").read())["claims"][0]["cycle"] == 189


def test_cli_release_without_done_or_progress_is_refused(tmp_path):
    # The cause fix. For eleven days `release` had one meaning and it was
    # "finished forever", so a cycle stopping halfway had no word for what
    # it was doing and spent the slug. Exit 1, not 2: a 2 means "somebody
    # else has this" and every cycle is told to accept a 2 without
    # arguing, so a 2 here would leave the claim open and read as handled.
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    before = open(path, encoding="utf-8").read()
    code = claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--outcome", "half of it"])
    assert code == 1
    assert open(path, encoding="utf-8").read() == before


def test_cli_release_guard_names_the_flag_rather_than_the_states(tmp_path, capsys):
    """The cause fix, pinned by its own words.

    Reviewer finding on runner#313: without this, deleting the guard
    entirely still passes, because `release(state=None)` raises inside the
    library and `main` maps that to the same exit 1 with the same
    untouched file. The only thing the guard adds is the sentence that
    tells a cycle which flag it wants, so that sentence is what the test
    has to assert.
    """
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    capsys.readouterr()
    assert claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--outcome", "half"]) == 1
    err = capsys.readouterr().err
    assert "--done or --progress" in err
    assert "If the work is not finished, it is --progress." in err


def test_cli_rejects_the_release_flags_on_take_and_list(tmp_path):
    # `take --progress`, meaning "record that I made progress", used to
    # open a fresh claim and say nothing at all.
    path = write(tmp_path, "")
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--progress"]) == 1
    assert claim_cli.main(["list", "--ledger", path, "--done"]) == 1
    assert open(path, encoding="utf-8").read() == ""


def test_cli_progress_without_an_outcome_is_refused(tmp_path):
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    before = open(path, encoding="utf-8").read()
    assert claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--progress"]) == 1
    assert open(path, encoding="utf-8").read() == before


def test_cli_release_progress_leaves_the_item_claimable(tmp_path):
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    assert claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--progress",
                           "--outcome", "half of it"]) == 0
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "190"]) == 0


def test_cli_release_done_still_spends_the_slug(tmp_path):
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    assert claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "189", "--done", "--outcome", "merged"]) == 0
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item",
                           "--cycle", "190"]) == 2


def test_cli_release_cannot_be_both_done_and_progress(tmp_path):
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    with pytest.raises(SystemExit) as exit_info:
        claim_cli.main(["release", "--ledger", path, "--item", "a-real-item",
                        "--cycle", "189", "--done", "--progress"])
    assert exit_info.value.code == 1


def test_cli_refusal_exits_two_and_leaves_the_file_byte_identical(tmp_path):
    # The documented flow puts the vault write behind `&&`, but a caller
    # that ignores the exit code still must not be able to write a refusal
    # back as a grant.
    #
    # The seeded ledger is deliberately not in the formatting `dumps`
    # produces -- one line, no trailing newline. Comparing bytes against a
    # canonically-formatted file would pass whether or not the refusal
    # wrote, because a refused `take` does not change the ledger it was
    # handed; the only thing that proves nothing was written is a file that
    # would come back *looking* different if it had been.
    # `now` is the CLI's own wall clock here, not T0 -- a claim stamped at a
    # fixed date would quietly stop being refused once it aged past the TTL,
    # and this test would go green for the wrong reason on a later day.
    now = datetime.now(OSLO).isoformat()
    held = json.dumps(
        {"claims": [{"item": "a-real-item", "cycle": 189, "state": "open", "at": now}]}
    )
    path = write(tmp_path, held)
    code = claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "190"])
    assert code == 2
    assert open(path, encoding="utf-8").read() == held


def test_cli_bad_slug_exits_one_not_two(tmp_path):
    # 2 means "pick another item"; 1 means "stop and look". A cycle that
    # cannot tell them apart quietly skips work it was told to do.
    path = write(tmp_path, "")
    assert claim_cli.main(["take", "--ledger", path, "--item", "Nope", "--cycle", "189"]) == 1


def test_cli_unparseable_ledger_exits_one_and_writes_nothing(tmp_path):
    path = write(tmp_path, "{not json")
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "1"]) == 1
    assert open(path, encoding="utf-8").read() == "{not json"


def test_cli_list_never_changes_the_file(tmp_path):
    path = write(tmp_path, "")
    claim_cli.main(["take", "--ledger", path, "--item", "a-real-item", "--cycle", "189"])
    before = open(path, encoding="utf-8").read()
    assert claim_cli.main(["list", "--ledger", path]) == 0
    assert open(path, encoding="utf-8").read() == before


def test_cli_take_without_a_cycle_exits_one(tmp_path):
    path = write(tmp_path, "")
    assert claim_cli.main(["take", "--ledger", path, "--item", "a-real-item"]) == 1


def test_a_usage_error_exits_one_not_two(tmp_path, capsys):
    # 2 is "somebody else has it, pick another item, do not argue". A cycle
    # that leaves the `<N>` placeholder unsubstituted, or drops `--ledger`,
    # must not be told a free item was taken -- it would obey that and
    # quietly skip the work it was sent to do. argparse's own default for a
    # usage error is 2, which is why this needs a test at all.
    path = write(tmp_path, "")
    for argv in (
        ["take", "--ledger", path, "--item", "a-real-item", "--cycle", "<N>"],
        ["take", "--item", "a-real-item", "--cycle", "189"],
        ["nonsense", "--ledger", path],
    ):
        with pytest.raises(SystemExit) as exit_info:
            claim_cli.main(argv)
        assert exit_info.value.code == 1, argv


# --- Slugs and liveness, for the claim-aware board read -------------------
#
# `top_board_rows` exercises these through `main()`, which cannot see a bug
# *inside* the slug functions: it hashes the capture with the same function
# the code under test uses, so both sides move together. Reviewer finding,
# PR #301. These assert against literals instead.

from datetime import timezone

from agora_runner.nova_claims import (
    CLAIMS_PATH, SLUG_RE, held_by, slug_for_capture, slug_for_row,
)


def test_a_row_slug_names_its_board_because_the_two_are_numbered_separately():
    assert slug_for_row("issue", 7) == "issue-7"
    assert slug_for_row("idea", 7) == "idea-7"
    # Live on both boards right now: issue #94 and idea #94 are both open.
    assert slug_for_row("issue", 94) != slug_for_row("idea", 94)


def test_a_row_slug_is_a_legal_claim_slug_at_one_digit_and_at_four():
    for board in ("issue", "idea"):
        for number in (1, 7, 92, 100, 9999):
            assert SLUG_RE.match(slug_for_row(board, number)), (board, number)


def test_a_capture_slug_ignores_how_the_bullet_was_wrapped():
    """The same bullet read twice can come back wrapped differently.

    That is the claim `slug_for_capture`'s docstring makes, and until this
    test nothing checked it -- two cycles reading one capture through two
    different line widths would have computed two slugs and both claimed it.
    """
    one_line = "Considering scaling to Claude 20x: parallel cycles will run."
    wrapped = "Considering scaling to Claude 20x:\n  parallel cycles will run."
    padded = "   Considering scaling to Claude   20x: parallel cycles will run.  "
    assert slug_for_capture(one_line) == slug_for_capture(wrapped)
    assert slug_for_capture(one_line) == slug_for_capture(padded)


def test_two_different_captures_do_not_share_a_slug():
    assert slug_for_capture("reopen idea 63") != slug_for_capture("reopen idea 64")


def test_a_capture_slug_is_a_legal_claim_slug_for_an_empty_bullet_too():
    # `unboarded_captures` filters the trailing empty bullet out, but the
    # function must not be the thing that decides that -- an unclaimable
    # slug would raise inside `take` rather than refuse.
    for text in ("", "   ", "a", "\u00e5 \u00f8 \u00e6 emoji \U0001f534", "x" * 4000):
        assert SLUG_RE.match(slug_for_capture(text)), repr(text[:20])


def test_held_by_reports_the_holder_and_forgets_the_stale_and_the_finished():
    now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    ledger = {"claims": [
        {"item": "issue-7", "cycle": 341, "state": "open",
         "at": (now - timedelta(minutes=2)).isoformat()},
        {"item": "idea-92", "cycle": 300, "state": "open",
         "at": (now - timedelta(minutes=90)).isoformat()},
        {"item": "issue-96", "cycle": 200, "state": "done",
         "at": (now - timedelta(minutes=2)).isoformat()},
    ]}
    assert held_by(ledger, now) == {"issue-7": 341}


def test_the_ledger_path_is_the_one_the_cli_docstring_tells_a_cycle_to_use():
    # The path was hand-typed in `tools/claim.py`'s docstring and nowhere
    # else until a second reader appeared. If the two ever disagree, one of
    # them is reading an empty ledger and neither says so.
    import pathlib
    cli = (pathlib.Path(__file__).resolve().parents[1] / "tools" / "claim.py")
    assert CLAIMS_PATH in cli.read_text(encoding="utf-8")


def test_a_comment_slug_is_named_for_the_row_and_the_text():
    slug = slug_for_comment("issue", 7, "is this really done?")
    assert slug.startswith("reply-issue-7-")
    assert len(slug.split("-")[-1]) == 8


def test_two_comments_on_one_row_are_two_claims():
    """A row slug is claimed once and finished forever, so a reply slug
    derived from the row alone would lock every later question out."""
    a = slug_for_comment("issue", 7, "first question")
    b = slug_for_comment("issue", 7, "second question")
    assert a != b


def test_the_same_comment_read_twice_is_one_claim():
    """Two cycles have to agree on the name, and the same block can come
    back wrapped differently -- same reason `slug_for_capture` normalises."""
    assert slug_for_comment("issue", 7, "one   two\nthree") == \
        slug_for_comment("issue", 7, "one two three")


def test_the_two_boards_do_not_share_a_comment_claim():
    assert slug_for_comment("issue", 7, "q") != \
        slug_for_comment("idea", 7, "q")
