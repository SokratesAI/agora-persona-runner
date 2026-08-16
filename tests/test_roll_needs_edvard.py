"""`tools/roll_needs_edvard.py` -- moving answered asks out of the live block.

Edvard, issue #89: *"I have absolutely no idea what the current 'needs
Edvard' asks of me."* The failure these tests pin is not "the block is
long" -- it is that a cycle unsure whether an item was answered had
nowhere to put it, so leaving it was free and removing it was a decision.
"""

import datetime

import pytest

from tools.rolling import RollError
from tools import roll_needs_edvard as R

TODAY = datetime.date(2026, 8, 16)

LIVE = """---
type: log
---

# Journal — Digest

## Needs Edvard
**Since 08-05** — Raise the GitHub Actions spending limit above $0.

**Since 08-15** — Decide whether the three config repos stay private.

## Next cycle
1. Something else.

## Digest
**Cycle 235** (2026-08-16 09:15) — A thing happened.
"""

ARCHIVE = ""


def _roll(live, archive, phrases, outcome="He answered it.", today=TODAY):
    """What `main` does, without the file I/O -- plan, verify, stamp."""
    head, body, _tail = R.rolling._body(live, R.SPEC)
    items = R.split_items(body)
    picked = R.select_answered(items, phrases)
    new_live, new_archive = R.plan(live, archive, R.SPEC, select=lambda _: picked)
    R.verify(live, archive, new_live, new_archive, R.SPEC, ordered=False)
    for item in (items[i] for i in picked):
        new_archive = new_archive.replace(
            item, R.stamp_answer(item, outcome, today), 1
        )
    return new_live, new_archive


def test_answered_item_leaves_the_live_block_and_lands_in_the_archive():
    new_live, new_archive = _roll(LIVE, ARCHIVE, ["spending limit"])
    assert "spending limit" not in new_live
    assert "three config repos" in new_live, "the live ask must survive"
    assert "spending limit" in new_archive


def test_the_archive_records_what_the_answer_was():
    _, new_archive = _roll(
        LIVE, ARCHIVE, ["spending limit"], outcome="He raised it to $20."
    )
    assert "**Answered 08-16** — He raised it to $20." in new_archive


def test_pulling_from_the_middle_keeps_the_rest_in_order():
    """The reason `verify` needs `ordered=False` here, pinned as behaviour.

    The first item is the one archived, so every remaining item moves up.
    A tail roll can never do this, which is why the ordered comparison
    stays the default for the other three callers.
    """
    new_live, _ = _roll(LIVE, ARCHIVE, ["spending limit"])
    assert R.split_items(R.rolling._body(new_live, R.SPEC)[1]) == [
        "**Since 08-15** — Decide whether the three config repos stay private."
    ]


def test_a_phrase_matching_nothing_is_refused_rather_than_guessed():
    with pytest.raises(RollError, match="no Needs Edvard item contains"):
        _roll(LIVE, ARCHIVE, ["crossplane"])


def test_an_ambiguous_phrase_is_refused():
    """Dropping the wrong one loses a live ask off his only channel."""
    with pytest.raises(RollError, match="2 Needs Edvard items contain"):
        _roll(LIVE, ARCHIVE, ["Since"])


def test_archiving_every_item_leaves_a_block_the_site_reads_as_empty():
    from agora_runner.nova_journal import is_empty_needs

    live = LIVE.replace(
        "\n**Since 08-15** — Decide whether the three config repos stay private.\n",
        "",
    )
    new_live, _ = _roll(live, ARCHIVE, ["spending limit"])
    body = R.rolling._body(new_live, R.SPEC)[1]
    assert not R.split_items(body), "nothing should be left to roll next time"
    # `main` substitutes a placeholder, and it has to be one the site reads
    # as empty. The phrase the live digest actually carries today is not.
    assert is_empty_needs("**Nothing.**")
    assert not is_empty_needs("**Nothing blocking.**")


def test_an_outcome_is_required(tmp_path):
    """An item archived with no answer is the unreadable log, one folder over."""
    live = tmp_path / "live.md"
    live.write_text(LIVE)
    with pytest.raises(RollError, match="--answered needs --outcome"):
        R.main([
            "--live", str(live), "--archive", str(tmp_path / "a.md"),
            "--answered", "spending limit", "--outcome", "  ",
        ])


def test_age_is_read_off_the_since_stamp():
    assert R.age_days("**Since 08-05** — a thing", TODAY) == 11
    assert R.age_days("**Since 08-16** — a thing", TODAY) == 0
    assert R.age_days("no stamp here", TODAY) is None


def test_a_stamp_in_the_future_reads_as_last_year_not_as_negative_age():
    """A January cycle looking at a December ask wants 30 days, not -335."""
    january = datetime.date(2026, 1, 5)
    assert R.age_days("**Since 12-06** — a thing", january) == 30


def test_an_empty_section_holds_no_archivable_item(tmp_path, capsys):
    """Treating `**Nothing.**` as an item would let a cycle archive the word.

    And then write a second one next cycle, and a third. The placeholder has
    to be inert to the same code that produced it.
    """
    live = tmp_path / "live.md"
    live.write_text(
        LIVE.split("## Needs Edvard")[0] + "## Needs Edvard\n**Nothing.**\n\n## Digest\n"
    )
    assert R.main(["--live", str(live), "--archive", str(tmp_path / "a.md")]) == 0
    assert "0 live item(s)" in capsys.readouterr().out

    with pytest.raises(RollError, match="no Needs Edvard item contains"):
        R.main([
            "--live", str(live), "--archive", str(tmp_path / "a.md"),
            "--answered", "Nothing", "--outcome", "x",
        ])


def test_archiving_the_last_item_writes_a_placeholder_the_site_hides(tmp_path):
    """End to end, because the placeholder is written outside `plan`."""
    from agora_runner.nova_journal import is_empty_needs

    live = tmp_path / "live.md"
    live.write_text(
        LIVE.replace(
            "\n**Since 08-15** — Decide whether the three config repos stay private.\n",
            "",
        )
    )
    R.main([
        "--live", str(live), "--archive", str(tmp_path / "a.md"),
        "--answered", "spending limit", "--outcome", "Raised.",
    ])
    text = live.read_text()
    body = R.rolling._body(text, R.SPEC)[1]
    assert is_empty_needs(body), f"the box would still show: {body!r}"
    assert "## Next cycle" in text and "## Digest" in text, "the rest of the file"


def test_listing_writes_nothing(tmp_path):
    live = tmp_path / "live.md"
    live.write_text(LIVE)
    before = live.read_text()
    assert R.main(["--live", str(live), "--archive", str(tmp_path / "a.md")]) == 0
    assert live.read_text() == before
    assert not (tmp_path / "a.md").exists()


def test_the_archive_is_written_before_the_live_file(tmp_path):
    """Stopping between the two writes must duplicate, never lose.

    Same invariant as `roll_digest`, and worth its own test here because
    this script writes the pair itself rather than going through
    `rolling.run`.
    """
    live = tmp_path / "live.md"
    archive = tmp_path / "archive.md"
    live.write_text(LIVE)
    order = []
    real_open = open

    def spy(path, mode="r", *a, **kw):
        if "w" in mode:
            order.append(str(path))
        return real_open(path, mode, *a, **kw)

    R.__builtins__["open"] = spy if isinstance(R.__builtins__, dict) else spy
    try:
        R.main(
            [
                "--live", str(live), "--archive", str(archive),
                "--answered", "spending limit", "--outcome", "Raised.",
            ]
        )
    finally:
        if isinstance(R.__builtins__, dict):
            R.__builtins__["open"] = real_open
    assert order == [str(archive), str(live)]


def test_selecting_by_index_does_not_take_a_twin_elsewhere_in_the_list():
    """The reason `plan`'s `select` returns indices rather than entries.

    Two identical items are legal input. Removing "the chosen ones" by
    value takes *both* copies out of the live file while rolling one, so
    selecting index 2 must leave index 0 exactly where it was.

    `verify` is deliberately not called here, and that is the correction
    worth recording: it refuses this input outright rather than passing it.
    Its left-hand side is deduplicated and its right-hand side is not, so
    twins read as "2 items in, 3 out" whichever way `plan` selected them.
    That makes it a real backstop and not the hole an earlier draft of this
    docstring claimed -- but it is a backstop that reports the wrong reason,
    so `plan` still has to be right on its own.
    """
    dupe = "**Since 08-05** — Raise the GitHub Actions spending limit above $0."
    live = LIVE.replace("## Next cycle", f"{dupe}\n\n## Next cycle")
    items = R.split_items(R.rolling._body(live, R.SPEC)[1])
    assert items.count(dupe) == 2, "fixture must actually hold twins"

    new_live, new_archive = R.plan(live, "", R.SPEC, select=lambda _: [2])
    left = R.split_items(R.rolling._body(new_live, R.SPEC)[1])
    assert left.count(dupe) == 1, "the other copy must survive"
    assert new_archive.count(dupe) == 1


def test_verify_refuses_twins_rather_than_silently_collapsing_them():
    dupe = "**Since 08-05** — Raise the GitHub Actions spending limit above $0."
    live = LIVE.replace("## Next cycle", f"{dupe}\n\n## Next cycle")
    new_live, new_archive = R.plan(live, "", R.SPEC, select=lambda _: [2])
    with pytest.raises(RollError, match="items in, .* out"):
        R.verify(live, "", new_live, new_archive, R.SPEC, ordered=False)
