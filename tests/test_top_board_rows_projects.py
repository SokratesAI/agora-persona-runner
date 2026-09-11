"""Proposed projects reach the pick, and an Immediately one says onboard first.

His capture of 2026-09-11: *"If I rate a whole new (unboarded) project
Immediately, the next cycle should first onboard it -- break it into
milestones and tasks -- then place it on top, not just slot the raw project
in."* Until this, `top_board_rows` never read `proposed-projects.md`.
"""

from tools import top_board_rows as t

PROPOSED = """---
type: log
---

- 🔴 Immediately: A bike route sharer for my Garmin
- ⚪ Low: A reading list
-

## Processed

- ⚪ Low: an old one a cycle already answered
"""


def test_only_the_bullets_above_processed_are_read_with_his_rating_split_off():
    got = t.unread_projects(PROPOSED)
    assert [(c["board"], c["priority"], c["text"]) for c in got] == [
        ("project", "🔴 Immediately", "A bike route sharer for my Garmin"),
        ("project", "⚪ Low", "A reading list")]
    # The reply route matches the bullet exactly, so `original` keeps the rating.
    assert got[0]["original"] == "🔴 Immediately: A bike route sharer for my Garmin"
    assert got[0]["index"] == 0 and got[1]["index"] == 1


def test_an_immediately_project_line_says_onboard_it_before_anything_else():
    immediate, low = t.unread_projects(PROPOSED)
    line = t._capture_line(immediate)
    assert line.startswith("proposed-projects.md")
    assert "ONBOARD FIRST" in line and "milestones" in line and "projects.md" in line
    assert "ONBOARD FIRST" not in t._capture_line(low)


def test_an_immediately_capture_is_printed_first_in_the_box():
    """Written low-first so file order and the asserted order disagree."""
    low, immediate = t.unread_projects(
        "- ⚪ Low: A reading list\n- 🔴 Immediately: A bike route sharer\n")
    page = t.render([], captures=[low, immediate])
    assert page.index("A bike route sharer") < page.index("A reading list")


def test_an_empty_file_is_no_captures_not_an_error():
    assert t.unread_projects("") == []
    assert t.unread_projects(None) == []
