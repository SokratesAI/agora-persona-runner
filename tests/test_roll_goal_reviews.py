"""Issue #96: weekly reviews roll off goals.md and still show on /plan."""

import pytest

from agora_runner.nova_plan import (
    plan_payload, roll_reviews, with_archived_reviews,
)
from tools import roll_goal_reviews


def _goals(n):
    reviews = "\n\n".join(
        f"### 2026-09-{28 - i:02d} — week {i}\n\nReview number {i}.\n\n"
        f"#### The full review\n\nDetail {i}."
        for i in range(n))
    return ("---\ntype: note\n---\n\n# Goals\n\nStandfirst.\n\n"
            "## The slate\n\n```goal\nname: G1\n```\n\n"
            f"## Weekly review\n\nNewest first.\n\n{reviews}\n")


def _headings(payload):
    found = []

    def walk(x):
        if isinstance(x, dict):
            if "heading" in x:
                found.append((x["heading"], x.get("open")))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(payload)
    return found


def test_keeps_the_newest_and_archives_the_rest_newest_first():
    goals, archive, moved = roll_reviews(_goals(6), "", keep=4)
    assert moved == ["### 2026-09-24 — week 4", "### 2026-09-23 — week 5"]
    assert "week 3" in goals and "week 4" not in goals
    assert archive.index("week 4") < archive.index("week 5")
    assert "```goal" in goals


def test_the_page_reads_the_same_after_two_rolls():
    original = _goals(7)
    goals, archive, _ = roll_reviews(original, "", keep=5)
    goals, archive, moved = roll_reviews(goals, archive, keep=2)
    assert len(moved) == 3
    # the second roll's entries are newer, so they go above the first's
    assert archive.index("week 2") < archive.index("week 5")
    assert with_archived_reviews(goals, archive).split() == original.split()
    before = plan_payload({"goals": original})
    after = plan_payload({"goals": goals, "goals_archive": archive})
    assert _headings(before) == _headings(after)
    opened = [h for h, is_open in _headings(after) if is_open and h]
    assert opened == ["2026-09-28 — week 0"]


def test_nothing_to_roll_is_unchanged():
    text = _goals(3)
    assert roll_reviews(text, "", keep=4) == (text, "", [])


def test_a_retitled_section_refuses():
    with pytest.raises(ValueError):
        roll_reviews(_goals(6).replace("## Weekly review", "## Reviews"), "")


def test_no_archive_leaves_the_page_alone():
    text = _goals(2)
    assert with_archived_reviews(text, "") == text


def test_cli_rolls_both_files(tmp_path, capsys):
    goals = tmp_path / "goals.md"
    goals.write_text(_goals(6))
    archive = tmp_path / "archive.md"
    assert roll_goal_reviews.main(
        ["--goals", str(goals), "--archive", str(archive)]) == 0
    out = capsys.readouterr().out
    assert out.count("moved: ") == 2
    assert "week 5" in archive.read_text()
    assert "week 5" not in goals.read_text()
    assert roll_goal_reviews.main(
        ["--goals", str(goals), "--archive", str(archive)]) == 0
    assert "nothing to roll" in capsys.readouterr().out
