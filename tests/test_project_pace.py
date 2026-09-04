"""The projected finish date on a project -- idea #228's last half.

Four of my cycles wrote the same sentence on that row and handed it back:
a roadmap is a claim about *when*, his rows carry one undated `MM-DD`
each, so he must supply target dates or a cross-project order first. The
claim these tests pin is that the second half of that was wrong. A
*closed* row's `Updated` is the day it was closed, so the closing rate is
already in his boards, and a date follows from it without him.
"""
from datetime import date

from agora_runner.nova_site import PACE_WINDOW_DAYS, _closed_on, _pace


def _row(number, status, updated, priority="high"):
    return {
        "number": number,
        "statusKey": status,
        "updated": updated,
        "priorityKey": priority,
    }


TODAY = date(2026, 9, 4)


def test_closing_rate_becomes_a_date_without_asking_him():
    """Seven closed in the window and seven open is one more window."""
    rows = [_row(i, "done", "09-0%d" % (i % 4 + 1)) for i in range(7)]
    rows += [_row(100 + i, "backlog", "08-20") for i in range(7)]
    pace = _pace(rows, TODAY)
    assert pace["closedInWindow"] == 7
    assert pace["perWeek"] == 3.5
    assert pace["remaining"] == 7
    # 7 remaining at 7 per 14 days is 14 more days, not a rounded guess.
    assert pace["days"] == PACE_WINDOW_DAYS
    assert pace["finishes"] == "2026-09-18"


def test_a_closure_older_than_the_window_is_not_in_the_rate():
    """The window is the point: an old burst must not date today's backlog.

    Both rows are `done`. One is inside the fourteen days and one is a day
    outside it, so a `_pace` that ignored the window entirely would report
    two and the same date this asserts against -- the boundary row is what
    separates the two implementations.
    """
    rows = [
        _row(1, "done", "09-03"),
        _row(2, "done", "08-21"),
        _row(3, "backlog", "08-01"),
    ]
    pace = _pace(rows, TODAY)
    assert pace["closedInWindow"] == 1
    assert pace["perWeek"] == 0.5
    assert pace["finishes"] == "2026-09-18"


def test_rows_blocked_on_edvard_are_not_projected_over():
    """The rate was measured on rows I closed; he unblocks the others.

    Crediting my throughput with his inbox would shorten the date every
    time he stopped answering, which is backwards.
    """
    rows = [_row(i, "done", "09-01") for i in range(4)]
    rows += [_row(10, "backlog", "08-01"), _row(11, "blocked-on-edvard", "08-01")]
    pace = _pace(rows, TODAY)
    assert pace["blocked"] == 1
    assert pace["remaining"] == 1
    # 1 row at 4 per 14 days rounds *up* to a whole day, never to zero.
    assert pace["days"] == 4
    assert pace["finishes"] == "2026-09-08"


def test_nothing_closed_gives_no_date_rather_than_a_far_one():
    """A rate of zero has no date in it, and saying so is the finding."""
    rows = [_row(1, "done", "07-01"), _row(2, "backlog", "08-01")]
    pace = _pace(rows, TODAY)
    assert pace["closedInWindow"] == 0
    assert pace["perWeek"] == 0.0
    assert pace["remaining"] == 1
    assert pace["finishes"] is None
    assert pace["days"] is None


def test_a_finished_project_asks_for_no_date():
    rows = [_row(1, "done", "09-01"), _row(2, "outdated", "09-01")]
    pace = _pace(rows, TODAY)
    assert pace["remaining"] == 0
    assert pace["finishes"] is None


def test_the_assumption_travels_with_the_date():
    """It cannot model arrivals, so it must not read as if it had.

    A backlog row added today and one edited today carry the same
    `Updated`, so there is no arrival rate in the data to net off.
    """
    rows = [_row(1, "done", "09-01"), _row(2, "backlog", "08-01")]
    assert _pace(rows, TODAY)["assumes"] == "nothing new is added"


def test_a_december_date_read_in_september_is_last_year():
    """No year is in the data, and the only reading with no future is back."""
    assert _closed_on({"updated": "12-30"}, TODAY) == date(2025, 12, 30)
    assert _closed_on({"updated": "09-03"}, TODAY) == date(2026, 9, 3)


def test_a_date_a_day_ahead_is_today_year_not_last_year():
    """Clock skew must not roll a row back three hundred and sixty-five days.

    Without the slack a row stamped tomorrow lands in 2025 and drops out
    of every window, so a closure would silently stop counting.
    """
    assert _closed_on({"updated": "09-05"}, TODAY) == date(2026, 9, 5)


def test_an_unparseable_date_is_dropped_not_guessed():
    """A cell that is not exactly `MM-DD` is dropped, not mined for digits.

    The third case is the one that matters and the first two do not test
    it: `2026-09-03` and `13-40` both yield an impossible month whichever
    way the cell is matched, so a scan that searched *inside* the string
    would return `None` on them anyway and look correct. A cell with a
    real date and something after it separates the two -- searching finds
    `09-03` and counts the row, and a hand-typed cell nobody validated is
    exactly where a wrong closing date would come from. Dropping it makes
    the rate a floor, which is the safe direction for a projected date.
    """
    assert _closed_on({"updated": ""}, TODAY) is None
    assert _closed_on({"updated": "2026-09-03"}, TODAY) is None
    assert _closed_on({"updated": "13-40"}, TODAY) is None
    assert _closed_on({"updated": "09-03 (reopened)"}, TODAY) is None
