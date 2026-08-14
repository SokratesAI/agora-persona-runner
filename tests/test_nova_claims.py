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
    ClaimError,
    dumps,
    is_stale,
    load,
    prune,
    release,
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


def test_pruning_never_drops_an_open_claim_however_stale():
    # A stale open claim is evidence a cycle died. Dropping it silently
    # would delete the only record that anyone was ever working on it.
    ledger = empty()
    take(ledger, "confirm-deploy-171", 189, T0)
    prune(ledger, at(90 * 24 * 60))
    assert len(ledger["claims"]) == 1


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
