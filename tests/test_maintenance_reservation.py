"""Milestone M6 of `task-prioritization-redesign.md`: the maintenance reservation.

Every Nth cycle the project tier is forced onto Infra/Maintenance, and it
falls through to the ordinary pick when there is no maintenance row a cycle
could actually take.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agora_runner.nova_next import (  # noqa: E402
    _BLOCKED, MAINTENANCE_EVERY, MAINTENANCE_PROJECTS, maintenance_queue,
    maintenance_reserved, rank, reserve_maintenance,
)

ROOT = Path(__file__).resolve().parents[1]


def row(number, project, priority="medium", status="open", **kw):
    r = {"number": number, "board": "idea", "project": project,
         "priorityKey": priority, "priority": priority,
         "statusKey": status, "status": status, "updated": "09-06",
         "title": f"row {number}"}
    r.update(kw)
    return r


def test_reserved_every_fifth_cycle_and_not_the_others():
    assert maintenance_reserved(1105)
    assert maintenance_reserved(1100)
    assert not maintenance_reserved(1101)
    assert not maintenance_reserved(1104)


def test_an_unknown_cycle_is_never_reserved():
    """A caller that does not know which cycle it is must not guess one."""
    assert maintenance_reserved(None) is False
    ranks, note = reserve_maintenance({"nova": 0}, [row(1, "Infra")], None)
    assert ranks == {"nova": 0}
    assert note is None


def test_a_cadence_of_zero_is_refused_rather_than_dividing():
    with pytest.raises(ValueError):
        maintenance_reserved(1105, every=0)


def test_forced_ranks_put_maintenance_ahead_of_every_other_project():
    ranks, note = reserve_maintenance(
        {"nova": 0, "marcus": 1, "infra": 7}, [row(1, "Infra")], 1105)
    assert ranks["infra"] < ranks["nova"] < ranks["marcus"]
    assert ranks["maintenance"] < ranks["nova"]
    # His order underneath is untouched -- the forced projects go below the
    # existing floor rather than renumbering anything.
    assert ranks["nova"] == 0 and ranks["marcus"] == 1
    assert "RESERVED MAINTENANCE CYCLE" in note


def test_an_empty_queue_falls_through_and_says_so():
    ranks, note = reserve_maintenance(
        {"nova": 0}, [row(1, "Nova"), row(2, "Marcus")], 1105)
    assert ranks == {"nova": 0}
    assert "no open, unheld, unblocked row" in note


def test_a_held_or_blocked_maintenance_row_is_not_a_queue():
    """Falling through means empty of *work*, not empty of rows."""
    rows = [row(1, "Infra", heldBy=999),
            row(2, "Maintenance", status=_BLOCKED)]
    assert maintenance_queue(rows) == []
    ranks, note = reserve_maintenance({"nova": 0}, rows, 1105)
    assert ranks == {"nova": 0}
    assert "no open, unheld, unblocked row" in note


def test_the_reservation_changes_which_row_ranks_first():
    rows = [row(10, "Nova", priority="high"), row(11, "Infra", priority="low")]
    ordinary = rank(rows, {"nova": 0, "infra": 1})
    assert ordinary[0]["number"] == 10
    forced, _ = reserve_maintenance({"nova": 0, "infra": 1}, rows, 1105)
    assert rank(rows, forced)[0]["number"] == 11


def test_an_immediately_row_no_longer_beats_the_reservation():
    """Issue #202 took the skip-to-top tier out of `rank`, so a 🔴 row in
    Nova waits for the maintenance row on a reserved cycle like any other."""
    rows = [row(10, "Nova", priority="immediate"), row(11, "Infra")]
    forced, _ = reserve_maintenance({"nova": 0, "infra": 1}, rows, 1105)
    assert rank(rows, forced)[0]["number"] == 11


def test_both_board_names_are_reserved():
    """`Infra` and `Maintenance` are two names for the same kind of hour."""
    assert set(MAINTENANCE_PROJECTS) == {"infra", "maintenance"}
    assert maintenance_queue([row(1, "Maintenance")])
    assert maintenance_queue([row(1, "infra")])
    assert maintenance_queue([row(1, "Nova")]) == []


def test_the_cadence_is_five():
    """Measured, not chosen: cycle 1102 classified cycles 965-1101 and found
    maintenance already at 37.5%, so 1 in 5 is a floor rather than a raise.
    Changing this number changes how much of the loop's time is reserved."""
    assert MAINTENANCE_EVERY == 5


# --- what the page actually says ------------------------------------------

def _render(rows, cycle):
    import tools.top_board_rows as t
    return t.render(rows, cycle=cycle)


def test_the_page_says_when_the_reservation_fired():
    out = _render([row(11, "Infra")], 1105)
    assert "RESERVED MAINTENANCE CYCLE" in out
    assert "1 row(s) are in that queue" in out


def test_the_page_says_when_it_fell_through():
    out = _render([row(10, "Nova")], 1105)
    assert "RESERVED MAINTENANCE CYCLE" in out
    assert "no open, unheld, unblocked row" in out


def test_an_ordinary_cycle_says_nothing_about_it():
    out = _render([row(10, "Nova")], 1101)
    assert "MAINTENANCE" not in out


def test_a_run_with_no_cycle_number_says_it_could_not_tell():
    """Silence here would read as 'this is an ordinary cycle'."""
    out = _render([row(10, "Nova")], None)
    assert "NOT EVALUATED" in out
