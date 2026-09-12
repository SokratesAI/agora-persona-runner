"""Shares of cycles (issue #214) — the project tier stops being a stack."""

from datetime import datetime, timedelta, timezone

import pytest

from agora_runner.nova_shares import (
    FLOOR_DAYS, SHARE_SEED, WINDOW, cycle_attribution, floor_is_measurable,
    last_worked, ledger_horizon, project_shares, share_deficits, share_ranks,
    starved,
)

PROJECTS = """
| Project | Priority | Updated | Order | TRL | Satisfaction | Lifecycle | Proposed |
|---|---|---|---|---|---|---|---|
| Marcus | 🔴 Immediately | 09-05 | 3 | Functional |  | Active |  |
| Nova | 🟠 High | 09-01 | 1 | Proven |  |  | Active |
| NAS | 🔵 Medium | 09-01 | 4 | Prototype |  | Paused |  |
| Agora | 🔵 Medium | 09-01 | 5 | Hardened |  | Active |  |
| Infra |  |  | 9 |  |  |  |  |
| Maintenance |  |  | 10 |  |  |  |  |
"""


def _now():
    return datetime(2026, 9, 12, 17, 0, tzinfo=timezone.utc)


def _stamp(days_ago):
    return (_now() - timedelta(days=days_ago)).isoformat()


def test_shares_sum_to_100_and_honour_his_seed():
    shares = project_shares(PROJECTS)
    assert sum(shares.values()) == pytest.approx(100.0)
    # His four numbers, `issues.md` #214. NAS is Paused so its share is
    # redistributed, which is why these are ratios rather than the literals.
    assert shares["marcus"] > shares["nova"] > shares["infra"]
    assert shares["infra"] == pytest.approx(shares["maintenance"])
    assert shares["marcus"] / shares["nova"] == pytest.approx(35.0 / 30.0)


def test_paused_project_earns_nothing_but_is_still_listed():
    shares = project_shares(PROJECTS)
    assert shares["nas"] == 0.0
    assert "nas" in shares


def test_blank_lifecycle_is_not_paused():
    """Most of his rows have never had the cell filled in."""
    assert project_shares(PROJECTS)["nova"] > 0


def test_unseeded_projects_split_the_rest_by_his_hand_order():
    """Agora (order 5) is the only unseeded live project here, so it takes
    what is left; add one further down his list and it must take less."""
    with_two = PROJECTS + "| Demos |  |  | 8 |  |  | Active |  |\n"
    shares = project_shares(with_two)
    assert shares["agora"] > shares["demos"] > 0


def test_unplaced_project_sorts_behind_every_placed_one():
    with_unplaced = PROJECTS + "| Research |  |  |  |  |  | Active |  |\n"
    shares = project_shares(with_unplaced)
    assert shares["research"] < shares["agora"]
    assert shares["research"] > 0


def test_a_board_where_everything_is_paused_hands_back_zeroes():
    paused = """
| Project | Priority | Updated | Order | TRL | Satisfaction | Lifecycle | Proposed |
|---|---|---|---|---|---|---|---|
| Nova |  |  | 1 |  |  | Paused |  |
"""
    assert project_shares(paused) == {"nova": 0.0}


PROJECT_OF = {"issue-1": "nova", "idea-2": "marcus", "issue-3": "agora"}


def test_attribution_counts_a_cycle_once_per_project():
    claims = [
        {"item": "issue-1", "cycle": 100, "at": _stamp(0)},
        {"item": "issue-1", "cycle": 100, "at": _stamp(0)},
        {"item": "idea-2", "cycle": 101, "at": _stamp(0)},
    ]
    counts, counted = cycle_attribution(claims, PROJECT_OF)
    assert counts == {"nova": 1, "marcus": 1}
    assert counted == 2


def test_bookkeeping_and_unresolvable_slugs_are_not_counted():
    claims = [
        {"item": "journal-seq-1514", "cycle": 200, "at": _stamp(0)},
        {"item": "health-line-reviewer-findings", "cycle": 201, "at": _stamp(0)},
        {"item": "issue-1", "cycle": 202, "at": _stamp(0)},
    ]
    counts, counted = cycle_attribution(claims, PROJECT_OF)
    assert counts == {"nova": 1}
    # Two of the three cycles resolved to nothing, and the denominator is
    # cycles I can attribute -- counting them would deflate every share.
    assert counted == 1


def test_attribution_window_keeps_only_the_newest_cycles():
    claims = [{"item": "issue-1", "cycle": n, "at": _stamp(0)}
              for n in range(1, 40)]
    claims.append({"item": "idea-2", "cycle": 1, "at": _stamp(0)})
    counts, counted = cycle_attribution(claims, PROJECT_OF, window=WINDOW)
    assert counted == WINDOW
    # Cycle 1 fell outside the window, so Marcus's only cycle is gone.
    assert "marcus" not in counts


def test_the_project_furthest_below_its_share_ranks_first():
    shares = {"nova": 50.0, "marcus": 50.0}
    counts, counted = {"nova": 9}, 10
    worked = {"nova": _now(), "marcus": _now()}
    ranks = share_ranks(shares, counts, counted, worked, now=_now())
    assert ranks["marcus"] < ranks["nova"]


def test_a_project_that_is_ahead_of_its_share_sinks_below_one_that_is_behind():
    """The stack failure his issue names: Nova holds the picker forever."""
    shares = {"nova": 30.0, "agora": 10.0}
    counts, counted = {"nova": 10}, 10
    worked = {"nova": _now(), "agora": _now() - timedelta(days=1)}
    ranks = share_ranks(shares, counts, counted, worked, now=_now())
    assert ranks["agora"] == 1


def test_the_14_day_floor_beats_the_arithmetic():
    """Nova is 70 points behind and Agora only 10 -- but nothing has touched
    Agora in 20 days, and his floor says that wins anyway.

    The numbers are chosen so the two rules disagree. An earlier version of
    this test had Agora ahead on the deficit as well, so deleting the floor
    band entirely left it green: a mutation that changes nothing is not a
    passing test, it is a test that was never asking the question.
    """
    shares = {"nova": 70.0, "agora": 10.0, "marcus": 20.0}
    counts, counted = {"marcus": 10}, 10
    worked = {"nova": _now(), "agora": _now() - timedelta(days=20),
              "marcus": _now()}
    horizon = _now() - timedelta(days=30)
    deficits = share_deficits(shares, counts, counted)
    assert deficits["nova"][2] > deficits["agora"][2]  # the precondition
    ranks = share_ranks(shares, counts, counted, worked, now=_now(),
                        horizon=horizon)
    assert ranks["agora"] == 1
    assert ranks["nova"] == 2
    assert starved(shares, worked, now=_now(), horizon=horizon) == ["agora"]


def test_a_project_nothing_has_ever_claimed_is_starved():
    shares = {"nova": 50.0, "demos": 50.0}
    ranks = share_ranks(shares, {"nova": 10}, 10, {"nova": _now()}, now=_now(),
                        horizon=_now() - timedelta(days=30))
    assert ranks["demos"] == 1


def test_the_floor_is_measured_in_days_not_in_cycles():
    shares = {"nova": 50.0, "agora": 50.0}
    worked = {"nova": _now(), "agora": _now() - timedelta(days=FLOOR_DAYS - 1)}
    assert starved(shares, worked, now=_now(),
                   horizon=_now() - timedelta(days=30)) == []


def test_a_zero_share_project_ranks_last_even_when_starved():
    shares = {"nova": 100.0, "nas": 0.0}
    ranks = share_ranks(shares, {"nova": 10}, 10, {"nova": _now()}, now=_now(),
                        horizon=_now() - timedelta(days=30))
    assert ranks["nas"] > ranks["nova"]


def test_deficits_carry_all_three_numbers_for_the_projects_page():
    out = share_deficits({"nova": 30.0}, {"nova": 1}, 10)
    assert out["nova"] == (30.0, 10.0, 20.0)


def test_deficits_do_not_divide_by_a_zero_denominator():
    assert share_deficits({"nova": 30.0}, {}, 0)["nova"] == (30.0, 0.0, 30.0)


def test_last_worked_takes_the_newest_claim_per_project():
    claims = [
        {"item": "issue-1", "cycle": 1, "at": _stamp(9)},
        {"item": "issue-1", "cycle": 2, "at": _stamp(2)},
    ]
    assert last_worked(claims, PROJECT_OF)["nova"] == _now() - timedelta(days=2)


def test_an_unparseable_stamp_is_skipped_rather_than_crashing():
    claims = [{"item": "issue-1", "cycle": 1, "at": "not a date"}]
    assert last_worked(claims, PROJECT_OF) == {}


def test_a_naive_stamp_is_read_as_utc_rather_than_raising():
    claims = [{"item": "issue-1", "cycle": 1, "at": "2026-09-01T10:00:00"}]
    got = last_worked(claims, PROJECT_OF)["nova"]
    assert got.tzinfo is not None


def test_his_seed_is_the_four_projects_he_named():
    assert set(SHARE_SEED) == {"marcus", "nova", "infra", "maintenance"}
    assert SHARE_SEED["infra"] + SHARE_SEED["maintenance"] == 20.0


def test_the_floor_is_not_applied_when_the_ledger_cannot_see_that_far_back():
    """`prune` collects finished claims, so the live ledger held 24 hours when
    this was written -- and every project then reads as untouched for 14 days
    whatever the loop actually did."""
    shares = {"nova": 50.0, "agora": 50.0}
    young = _now() - timedelta(hours=24)
    assert starved(shares, {"nova": _now()}, now=_now(), horizon=young) == []
    ranks = share_ranks(shares, {"nova": 10}, 10, {"nova": _now()},
                        now=_now(), horizon=young)
    # Agora still ranks first, but on the deficit rather than on a floor that
    # was never measurable.
    assert ranks["agora"] == 1


def test_the_floor_applies_once_the_ledger_is_older_than_it():
    shares = {"nova": 90.0, "agora": 10.0}
    old = _now() - timedelta(days=30)
    worked = {"nova": _now(), "agora": _now() - timedelta(days=20)}
    assert starved(shares, worked, now=_now(), horizon=old) == ["agora"]


def test_an_empty_ledger_has_no_horizon_and_no_floor():
    assert ledger_horizon([]) is None
    assert not floor_is_measurable(None)
    assert starved({"nova": 100.0}, {}, now=_now(), horizon=None) == []


def test_ledger_horizon_is_the_oldest_stamp():
    claims = [{"item": "a", "cycle": 1, "at": _stamp(3)},
              {"item": "b", "cycle": 2, "at": _stamp(9)}]
    assert ledger_horizon(claims) == _now() - timedelta(days=9)
