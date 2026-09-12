"""The landing page's health line -- idea #274, step 3b.

His spec: *"One quiet line: cycle running, gaps in numbering, critical
alerts, quota burn. Silent when fine."*

Every test here is about what the line SAYS, because "silent when fine" is
one assertion and "not silent when not fine" is four, and the four are the
ones that can fail quietly. The clock-dependent half -- has the loop gone
quiet -- is deliberately not here: it is computed in the page against the
browser's own clock, for the reason `health_block`'s docstring gives, and
`tests/browser/app.test.mjs` is where it is pinned.
"""

import pytest

from agora_runner.nova_home import (
    QUOTA_HOT_PACE,
    QUOTA_LOW_REMAINING,
    health_block,
    home_payload,
)

QUIET_ALERTS = {"reachable": True, "blind": False, "firing": [], "rules": 6}
# `[at, fiveHour, fiveHourPace, sevenDay, sevenDayPace]` -- nova_costs.QUOTA_COLUMNS.
HEALTHY_QUOTA = [1789222230000, 3.0, 0.128, 39.0, 0.947]


#: `None` is a value both inputs take and mean something by -- an
#: unreachable Prometheus, a ledger with no readings -- so the helper
#: cannot use it to mean "use the healthy default". Passing `None`
#: deliberately and having it silently become a healthy fixture is how the
#: first draft of these tests passed while asserting nothing.
_DEFAULT = object()


def block(status=None, alerts=_DEFAULT, quota=_DEFAULT, cadence=40):
    return health_block(
        {"cycle": 1457, "lastWrittenAt": "2026-09-12T16:00:00+02:00",
         "recentMissingCycles": [], **(status or {})},
        QUIET_ALERTS if alerts is _DEFAULT else alerts,
        HEALTHY_QUOTA if quota is _DEFAULT else quota,
        cadence,
    )


def test_a_healthy_loop_says_nothing():
    assert block()["concerns"] == []


def test_the_facts_the_page_needs_for_its_own_clock_check_all_go_out():
    """The page does the subtraction, so it needs all three or none.

    Written as one test because a missing field is not a wrong number: the
    page's stall check is skipped entirely when `cadenceMinutes` or
    `stallGrace` is absent, so dropping one makes the line go quiet about
    a stalled loop with nothing failing anywhere.
    """
    facts = block()
    assert facts["cycle"] == 1457
    assert facts["lastWrittenAt"] == "2026-09-12T16:00:00+02:00"
    assert facts["cadenceMinutes"] == 40
    assert facts["stallGrace"] >= 1


def test_the_stall_grace_is_read_from_cycle_health_not_restated():
    from agora_runner.cycle_health import STALL_GRACE_INTERVALS

    assert block()["stallGrace"] == STALL_GRACE_INTERVALS


def test_a_gap_in_the_numbering_names_the_cycles():
    said = block({"recentMissingCycles": [1440, 1441]})["concerns"]
    assert len(said) == 1
    assert "2 cycles wrote no entry" in said[0]
    assert "1440, 1441" in said[0]


def test_one_gap_is_singular():
    assert "1 cycle wrote no entry: 1440" in block(
        {"recentMissingCycles": [1440]})["concerns"][0]


def test_a_firing_alert_is_named():
    said = block(alerts={
        "reachable": True, "blind": False, "rules": 6,
        "firing": [{"name": "KubePodCrashLooping"}],
    })["concerns"]
    assert said == ["1 alert is firing: KubePodCrashLooping"]


def test_unreachable_prometheus_is_a_concern_and_not_quiet():
    """The negative-result-guaranteed-in-advance rule, on this line.

    An empty alert list is what a dead scrape and a healthy cluster both
    produce, so a health line that went silent on a failed fetch would be
    reporting calm it never measured.
    """
    said = block(alerts={
        "reachable": False, "error": "connection refused", "blind": True, "firing": [],
    })["concerns"]
    assert len(said) == 1
    # The distinguishing words, not just "Prometheus". `summarise` sets
    # `blind` on a failed fetch as well, so the blind branch below catches
    # this input too and its sentence also names Prometheus -- a test that
    # only looked for the word passed with the unreachable branch deleted,
    # measured this cycle. The two have different fixes (a dead scrape
    # versus a rules file that silently failed to load), so the line has to
    # be able to say which.
    assert "could not reach" in said[0]
    assert "no alerting rules" not in said[0]


def test_zero_rules_loaded_is_blind_rather_than_calm():
    said = block(alerts={"reachable": True, "blind": True, "firing": [], "rules": 0})["concerns"]
    assert len(said) == 1
    assert "no alerting rules" in said[0]


def test_a_hot_week_says_the_pace():
    said = block(quota=[1, 3.0, 0.1, 60.0, QUOTA_HOT_PACE + 0.3])["concerns"]
    assert len(said) == 1
    assert "running hot" in said[0]
    assert "60% spent" in said[0]


def test_the_pace_threshold_is_exclusive_so_exactly_on_it_is_quiet():
    assert block(quota=[1, 3.0, 0.1, 60.0, QUOTA_HOT_PACE])["concerns"] == []


def test_a_nearly_spent_week_outranks_the_pace():
    """Both conditions hold at once here and only the actionable one prints.

    A 96%-spent week is by construction running hot as well, and saying so
    twice on a one-line block is how a line stops being read.
    """
    said = block(quota=[1, 3.0, 0.1, 100.0 - QUOTA_LOW_REMAINING, 3.0])["concerns"]
    assert len(said) == 1
    assert "nearly spent" in said[0]
    assert str(QUOTA_LOW_REMAINING).rstrip("0").rstrip(".") in said[0]


@pytest.mark.parametrize("quota", [None, [], [1, 2, 3]])
def test_a_missing_quota_reading_says_so_rather_than_going_quiet(quota):
    """Same rule as the unreachable-Prometheus case, on the other input.

    The short row is in here on purpose: `_quota` reads columns 3 and 4
    positionally, so a row the publisher truncates would otherwise come
    back as `None` percent and read as a healthy week.
    """
    said = block(quota=quota)["concerns"]
    assert len(said) == 1
    assert "no quota reading" in said[0]


def test_concerns_stack_rather_than_the_first_one_winning():
    said = block(
        {"recentMissingCycles": [1440]},
        alerts={"reachable": True, "blind": False, "rules": 6,
                "firing": [{"name": "NodeFilesystemAlmostOutOfSpace"}]},
        quota=[1, 3.0, 0.1, 95.0, 3.0],
    )["concerns"]
    assert len(said) == 3


def test_home_payload_carries_none_when_no_block_was_built():
    """`None` and an empty block are different answers.

    Both draw nothing on the page, and only the second is reassurance --
    the first means nobody looked. Keeping them distinct is what stops a
    caller that fails to build the block from looking like a clean bill of
    health.
    """
    assert home_payload({}, {}, {}, [])["health"] is None
    built = home_payload({}, {}, {}, [], health=block())["health"]
    assert built["concerns"] == []


def test_health_and_top_cannot_be_passed_positionally():
    """The trap `health` set when it was inserted in front of `top`.

    `home_payload(recap, projects, next_up, asks, health=None,
    top=TOP_PROJECTS)` put the new argument BEFORE an existing defaulted
    one. Nothing broke, because no caller passes `top` positionally --
    which is exactly what makes it worth a guard rather than a note: the
    next caller that does gets a dict bound to `health`, the default
    number of project cards, and no error anywhere.

    Asserted on the call rather than on `inspect.signature`, because a
    signature check would pass on a function whose `*` had been moved to
    a place that still reads right and behaves differently.
    """
    with pytest.raises(TypeError):
        home_payload({}, {}, {}, [], block())
