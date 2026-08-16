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
    with pytest.raises(RollError, match="needs an outcome saying what"):
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


def test_a_heading_in_the_outcome_is_refused_before_anything_is_written(tmp_path):
    """The archive guard cannot see `--outcome`, so this check has to.

    `verify` runs `_check_archive` on the planned archive, and the outcome
    is spliced in *after* that -- deliberately, because `verify`'s
    invariant is that entries pass through unchanged. The reviewer
    reproduced the gap against the real digest: an outcome carrying a
    `## Entries` line wrote that heading to disk with a clean exit 0.
    """
    live = tmp_path / "live.md"
    archive = tmp_path / "a.md"
    live.write_text(LIVE)
    with pytest.raises(RollError, match="markdown heading"):
        R.main([
            "--live", str(live), "--archive", str(archive),
            "--answered", "spending limit",
            "--outcome", "He said this:\n## Entries\nand then that.",
        ])
    assert not archive.exists(), "nothing may be written before the refusal"
    assert live.read_text() == LIVE


def test_plan_refuses_keep_zero_with_no_select():
    """`keep=0` plus the wrapper shape every other caller uses wipes the lot.

    `rolling.plan(live, archive, SPEC)` is exactly how `roll_digest`'s own
    wrapper calls through. With this SPEC's `keep=0` and no `select`, that
    archived every live item and `verify` passed it, because moving
    everything is still a multiset-preserving roll.
    """
    with pytest.raises(RollError, match="keep=0 with no select"):
        R.plan(LIVE, "", R.SPEC)


def test_dates_come_from_oslo_not_the_system_clock(tmp_path, monkeypatch):
    """The pod runs UTC; Edvard reads Oslo. At 22:30 UTC they disagree.

    This must go through `main`, not through `stamp_answer` with a date the
    test computed itself -- the first version of this test did the latter,
    survived the mutation back to `datetime.now()`, and pinned nothing.
    """
    import datetime as dt

    frozen = dt.datetime(2026, 8, 16, 22, 30, tzinfo=dt.timezone.utc)
    assert frozen.date() == dt.date(2026, 8, 16), "UTC is still on the 16th"
    assert frozen.astimezone(R.OSLO).date() == dt.date(2026, 8, 17), "Oslo turned over"

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(R.datetime, "datetime", Frozen)

    live = tmp_path / "live.md"
    archive = tmp_path / "a.md"
    live.write_text(LIVE)
    R.main([
        "--live", str(live), "--archive", str(archive),
        "--answered", "spending limit", "--outcome", "Raised.",
    ])
    assert "**Answered 08-17**" in archive.read_text(), archive.read_text()


# --- The same transform, reached from the site (issue #93) -----------------


@pytest.mark.parametrize("digest", [
    LIVE,
    # An ask that quotes a heading inside a fenced block. Nova writes prose
    # about its own machinery into this file every hour, so this is a
    # "when", not an "if" -- `md_sections` exists because a marker written
    # with real newlines instead of escaped ones fired for real on 08-13.
    LIVE.replace(
        "## Next cycle",
        "**Since 08-16** \u2014 An ask that quotes a heading:\n\n"
        "```\n## Needs Edvard\n```\n\n"
        "and then keeps going.\n\n## Next cycle",
        1,
    ),
])
def test_the_site_and_the_cli_split_the_block_into_the_same_items(digest):
    """One splitter, or the button clears something the roller kept.

    `parse_digest` is what builds Edvard's page -- it is where `item.text`
    on a Done button comes from -- and `live_items` is what the archive
    matches that text against. If the two disagree about where one ask
    ends, the ask that leaves the file is not the one he pointed at, and
    nothing anywhere says so.

    This asserts against `parse_digest` on the *whole digest*, deliberately.
    An earlier version of this test fed both sides the body that
    `rolling._body` had already cut, which made it `needs_items(x) ==
    needs_items(x)` -- true however `parse_digest` behaved. My reviewer
    caught that, and the second parameter above is the input it was blind
    to: the naive scan stops the section at the fence's fake heading, so
    the page loses an ask entirely and mangles the next one.
    """
    from agora_runner.nova_journal import parse_digest
    from agora_runner.nova_needs import live_items

    assert live_items(digest) == parse_digest(digest)["needsEdvardItems"]
    assert len(live_items(digest)) > 1


def test_an_empty_block_offers_nothing_to_clear():
    """`**Nothing.**` is not an ask, and a button on it would archive the
    word Nothing and leave the next cycle writing a second one."""
    from agora_runner.nova_journal import needs_items

    assert needs_items("**Nothing.**") == []
    assert needs_items("**Nothing**") == []
    assert needs_items("") == []


def test_dismissing_by_text_archives_that_item_and_leaves_the_others():
    import datetime as dt

    from agora_runner.nova_needs import archive_answered

    items = R.live_items(LIVE)
    target = items[1]
    new_live, new_archive, moved = archive_answered(
        LIVE, "", [target], "He said yes.", dt.date(2026, 8, 16)
    )
    assert moved == [target]
    assert target in new_archive
    assert "**Answered 08-16** — He said yes." in new_archive
    remaining = R.live_items(new_live)
    assert target not in remaining
    assert remaining == [item for item in items if item != target]


def test_a_stale_page_cannot_clear_the_wrong_ask():
    """The whole reason the client sends text rather than an index.

    A cycle rewrites this block every forty minutes. If Edvard's page was
    rendered before that rewrite, the item at position 1 is a different ask
    by the time he taps Done -- so a dismissal naming an ask the file no
    longer holds has to fail, not fall through to whatever is there now.
    """
    import datetime as dt

    from agora_runner.nova_needs import archive_answered

    with pytest.raises(RollError, match="no Needs Edvard item contains"):
        archive_answered(
            LIVE, "", ["an ask that was rewritten away"], "n/a", dt.date(2026, 8, 16)
        )
