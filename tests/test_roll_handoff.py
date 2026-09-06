"""`tools.roll_handoff` -- retiring a finished item out of **Next cycle**.

The section this rolls is where the loop keeps its "do not redo this"
findings, so the tests that matter are the refusals: a slug that names
nothing, a slug that names two, a retire-everything, and an unslugged
item that must stay unnameable rather than be matched by position.
"""

import datetime

import pytest

from agora_runner.nova_handoff import (
    ARCHIVE_TITLE,
    archive_retired,
    item_slug,
    live_items,
    count_items,
    newest_cycle,
    select_slugs,
    split_items,
)
from agora_runner.rolling import RollError
from tools import roll_handoff

TODAY = datetime.date(2026, 8, 30)

LIVE = """---
type: log
---

# Journal — Digest

## Needs input

**Nothing.**

## Next cycle

**[first-thing]** Cycle 671, done and merged.

**[second-thing]** Cycle 668, also done.

**[third-thing]** Cycle 660, superseded.

**Never hand-edit a vault document I maintain.** See **[second-thing]** above.

## Digest

**Cycle 671** (2026-08-30 18:00) — did a thing.
"""


def _live(text=LIVE):
    return text


def test_items_and_slugs_read_off_the_live_section():
    items = live_items(_live())
    assert len(items) == 4
    assert [item_slug(i) for i in items] == [
        "first-thing",
        "second-thing",
        "third-thing",
        None,
    ]
    assert newest_cycle(items[0]) == 671
    assert newest_cycle(items[3]) is None


def test_retiring_moves_the_named_item_and_leaves_the_rest():
    new_live, new_archive, moved = archive_retired(
        _live(), "", ["second-thing"], "Superseded by the fourth thing.", TODAY
    )
    assert len(moved) == 1
    assert "second-thing" in moved[0]

    slugs = [item_slug(i) for i in live_items(new_live)]
    assert slugs == ["first-thing", "third-thing", None]

    assert ARCHIVE_TITLE in new_archive
    assert "second-thing" in new_archive
    assert "**Retired 08-30** — Superseded by the fourth thing." in new_archive
    # The other two must not have followed it out.
    assert "first-thing" not in new_archive
    assert "third-thing" not in new_archive
    # And the sections around it are untouched.
    assert "## Digest" in new_live
    assert "**Cycle 671** (2026-08-30 18:00) — did a thing." in new_live


def test_an_unknown_slug_is_refused_rather_than_ignored():
    with pytest.raises(RollError) as excinfo:
        archive_retired(_live(), "", ["fourth-thing"], "gone", TODAY)
    assert "no **Next cycle** item is named [fourth-thing]" in str(excinfo.value)


def test_a_duplicated_slug_is_refused_rather_than_guessed_at():
    doubled = LIVE.replace(
        "**[third-thing]** Cycle 660, superseded.",
        "**[first-thing]** Cycle 660, a second item with the same name.",
    )
    with pytest.raises(RollError) as excinfo:
        archive_retired(doubled, "", ["first-thing"], "gone", TODAY)
    assert "2 **Next cycle** items are named [first-thing]" in str(excinfo.value)


def test_an_unslugged_item_cannot_be_named():
    """The two real unslugged items are standing instructions, not findings.

    Selection is by slug only, so there is no string a caller can pass that
    reaches one. This asserts the *absence* of a fallback -- a matcher that
    fell back to substrings would make them retirable by accident.
    """
    items = live_items(_live())
    assert select_slugs(items, []) == []
    with pytest.raises(RollError):
        select_slugs(items, ["Never hand-edit a vault document I maintain."])


def test_a_bracket_in_the_prose_is_not_a_slug():
    """The slug matcher is anchored, and this is what the anchor buys.

    The unslugged standing instruction in the live section cites another
    item's slug in its body. An unanchored matcher reads that as its name,
    so `--retire second-thing` would resolve to two items -- and on the
    live digest, where these bodies quote each other constantly, it would
    resolve to the wrong one silently rather than refusing.
    """
    items = live_items(_live())
    assert item_slug(items[3]) is None
    picked = select_slugs(items, ["second-thing"])
    assert picked == [1]


def test_retiring_every_item_is_refused():
    """An empty **Next cycle** is a worse state than a stale one.

    The live file here has no unslugged item, because with one present the
    section cannot be emptied through this tool at all -- which is a second
    reason the two standing instructions are safe where they are.
    """
    only_slugged = LIVE.replace(
        "**Never hand-edit a vault document I maintain.** See **[second-thing]** above.\n\n", ""
    )
    with pytest.raises(RollError) as excinfo:
        archive_retired(
            only_slugged,
            "",
            ["first-thing", "second-thing", "third-thing"],
            "all done",
            TODAY,
        )
    assert "leave the next cycle no handoff at all" in str(excinfo.value)


def test_naming_the_same_slug_twice_is_refused():
    with pytest.raises(RollError) as excinfo:
        archive_retired(
            _live(), "", ["first-thing", "first-thing"], "done", TODAY
        )
    assert "already named by an earlier --retire" in str(excinfo.value)


def test_a_reason_is_required():
    with pytest.raises(RollError) as excinfo:
        archive_retired(_live(), "", ["first-thing"], "   ", TODAY)
    assert "needs a reason" in str(excinfo.value)


def test_a_heading_in_the_reason_is_refused():
    with pytest.raises(RollError) as excinfo:
        archive_retired(_live(), "", ["first-thing"], "## nope", TODAY)
    assert "markdown heading" in str(excinfo.value)


def test_nothing_is_lost_when_the_archive_already_holds_items():
    live_one, archive_one, _ = archive_retired(
        _live(), "", ["third-thing"], "first retirement", TODAY
    )
    live_two, archive_two, _ = archive_retired(
        live_one, archive_one, ["second-thing"], "second retirement", TODAY
    )
    assert "third-thing" in archive_two
    assert "second-thing" in archive_two
    assert "first retirement" in archive_two
    assert "second retirement" in archive_two
    assert [item_slug(i) for i in live_items(live_two)] == ["first-thing", None]


def test_cli_with_no_retire_writes_nothing_and_lists_the_section(tmp_path, capsys):
    live = tmp_path / "live.md"
    archive = tmp_path / "archive.md"
    live.write_text(LIVE)

    assert roll_handoff.main(["--live", str(live), "--archive", str(archive)]) == 0

    out = capsys.readouterr().out
    assert "4 item(s) in **Next cycle**" in out
    assert "[first-thing]" in out
    assert "(no slug -- cannot be retired)" in out
    assert "11 behind" in out  # third-thing cites 660 against a newest of 671
    assert live.read_text() == LIVE
    assert not archive.exists()


def test_cli_writes_the_archive_before_the_live_file(tmp_path, capsys):
    live = tmp_path / "live.md"
    archive = tmp_path / "archive.md"
    live.write_text(LIVE)

    code = roll_handoff.main(
        [
            "--live", str(live),
            "--archive", str(archive),
            "--retire", "third-thing",
            "--reason", "Superseded on Cycle 672.",
        ]
    )
    assert code == 0
    assert "third-thing" in archive.read_text()
    assert "third-thing" not in live.read_text()
    assert "**Retired" in archive.read_text()
    assert "1 item(s) retired, 3 still in the handoff" in capsys.readouterr().out


def test_cli_dry_run_writes_neither_file(tmp_path):
    live = tmp_path / "live.md"
    archive = tmp_path / "archive.md"
    live.write_text(LIVE)

    assert roll_handoff.main(
        [
            "--live", str(live),
            "--archive", str(archive),
            "--retire", "third-thing",
            "--reason", "Superseded.",
            "--dry-run",
        ]
    ) == 0
    assert live.read_text() == LIVE
    assert not archive.exists()


def test_a_roll_onto_an_archive_holding_a_duplicated_stamp():
    """The jam that made `roll_handoff` refuse every multi-item roll.

    `stamp_retired` writes the reason as its own paragraph, so the
    paragraph splitter reads each stamp back as an item -- and two items
    retired in one call with one reason leave two identical paragraphs.
    `archive_retired` splices the stamps in *after* `verify`, so the roll
    that writes them passes; the **next** roll is the one that reads them
    back, `rolling.dedup` collapses the pair on the before side and not on
    the after side, and a correct roll is refused as `N in, N+1 out`.

    So the reproduction needs two rolls, and the first one has to retire
    two items under a single reason. One roll onto an empty archive
    cannot show this, which is what the mutation check caught.
    """
    live_one, archive_one, moved = archive_retired(
        _live(),
        "",
        ["second-thing", "third-thing"],
        "both landed in the same PR",
        TODAY,
    )
    assert len(moved) == 2
    # The identical stamp, twice, is what the next roll has to survive.
    assert archive_one.count("**Retired 08-30** — both landed in the same PR") == 2

    live_two, archive_two, _ = archive_retired(
        live_one, archive_one, ["first-thing"], "finished too", TODAY
    )
    assert [item_slug(i) for i in live_items(live_two)] == [None]
    assert archive_one.count("**Retired 08-30** — both landed in the same PR") == 2
    assert "**Retired 08-30** — finished too" in archive_two


def test_an_earlier_rolls_reason_survives_the_next_roll():
    """The wrong fix, caught: do not filter stamps out of `split_items`.

    Cycle 792 hid stamps from the splitter itself, which made `verify`
    pass and made `plan` rebuild the archive without them -- 29 reasons
    were deleted from the live file and had to be recovered from the
    hourly vault mirror. `count_items` is a separate splitter for exactly
    this reason: counting may ignore a stamp, reconstructing may not.
    """
    live_one, archive_one, _ = archive_retired(
        _live(), "", ["third-thing"], "first retirement", TODAY
    )
    live_two, archive_two, _ = archive_retired(
        live_one, archive_one, ["second-thing"], "second retirement", TODAY
    )
    assert "**Retired 08-30** — first retirement" in archive_two
    assert "**Retired 08-30** — second retirement" in archive_two


def test_count_items_ignores_stamps_and_split_items_does_not():
    _, archive, _ = archive_retired(
        _live(), "", ["second-thing", "third-thing"], "one reason", TODAY
    )
    body = archive.split(ARCHIVE_TITLE, 1)[1]
    assert len(split_items(body)) == 4  # two items and two stamps
    assert len(count_items(body)) == 2  # the two items


# The age roll. `AGED` is deliberately not `LIVE`: its digest section
# carries more than one line, listed newest first the way the real file
# is, so `min` and "the first one" are different answers and a mutation
# swapping them cannot survive.
AGED = """---
type: log
---

# Journal — Digest

## Next cycle

**[recent-thing]** Cycle 671, still worth reading.

**[amended-thing]** Raised at Cycle 640 and rewritten at Cycle 670.

**[boundary-thing]** Cycle 669 -- exactly the oldest cycle the digest shows.

**[stale-thing]** Cycle 640, long finished.

**[undatable-thing]** A standing rule that cites no cycle.

**A standing instruction with no slug at all.** Cycle 600.

## Digest

**Cycle 671** (2026-08-30 18:00) — did a thing.

**Cycle 669** (2026-08-30 16:00) — mentions Cycle 200 in passing.
"""


def test_the_cut_is_the_oldest_cycle_the_digest_still_shows():
    # 669, not 671 (the first line) and not 200 (quoted inside a line).
    assert roll_handoff.oldest_digest_cycle(AGED) == 669


def test_no_digest_line_with_a_cycle_number_means_no_cut():
    undated = AGED.replace("**Cycle 671** (2026-08-30 18:00) — did a thing.", "").replace(
        "**Cycle 669** (2026-08-30 16:00) — mentions Cycle 200 in passing.",
        "**Ideas & research** (2026-08-30 16:00) — a weekly line.",
    )
    assert roll_handoff.oldest_digest_cycle(undated) is None


def test_selection_by_age_skips_what_it_cannot_name_or_date():
    items = live_items(AGED)
    picked = roll_handoff.select_older_than(items, 669)
    assert [item_slug(items[i]) for i in picked] == ["stale-thing"]
    # boundary-thing cites exactly 669 and stays: the digest still shows
    # that cycle, so the two sections cover the same stretch of time.
    # amended-thing cites 640 and 670; the newest citation keeps it.
    # undatable-thing and the unslugged paragraph are never selectable.


def test_age_roll_moves_the_old_items_and_records_the_rule():
    new_live, new_archive, moved = roll_handoff.archive_older_than(
        AGED, "", 669, TODAY
    )
    assert [item_slug(i) for i in moved] == ["stale-thing"]
    assert [item_slug(i) for i in live_items(new_live)] == [
        "recent-thing",
        "amended-thing",
        "boundary-thing",
        "undatable-thing",
        None,
    ]
    assert "oldest cycle the digest section" in new_archive
    assert "**Retired 08-30**" in new_archive
    assert ARCHIVE_TITLE in new_archive


def test_age_roll_writes_nothing_when_every_item_is_inside_the_window():
    assert roll_handoff.archive_older_than(AGED, "", 600, TODAY) is None


def test_cli_refuses_to_mix_the_two_selection_rules(tmp_path, capsys):
    live = tmp_path / "live.md"
    live.write_text(AGED)
    archive = tmp_path / "archive.md"
    code = roll_handoff.main(
        [
            "--live", str(live), "--archive", str(archive),
            "--retire", "stale-thing", "--reason", "done",
            "--retire-older-than-digest",
        ]
    )
    assert code == 1
    assert "two rolls" in capsys.readouterr().err
    assert live.read_text() == AGED


def test_cli_rolls_by_age_and_writes_both_files(tmp_path, capsys):
    live = tmp_path / "live.md"
    live.write_text(AGED)
    archive = tmp_path / "archive.md"
    code = roll_handoff.main(
        ["--live", str(live), "--archive", str(archive), "--retire-older-than-digest"]
    )
    assert code == 0
    assert "older than cycle 669" in capsys.readouterr().out
    assert "stale-thing" not in live.read_text()
    assert "stale-thing" in archive.read_text()
    assert "recent-thing" in live.read_text()


# A duplicate slug among the aged-out items. The age roll resolves items
# by index and must not be stopped by an ambiguity that only exists in
# the slug namespace -- cycle 1017's reviewer found this after the merge.
AGED_DUPLICATE = AGED.replace(
    "**[stale-thing]** Cycle 640, long finished.",
    "**[stale-thing]** Cycle 640, long finished.\n\n"
    "**[stale-thing]** Cycle 641, a second item wearing the same slug.",
)


def test_the_age_roll_survives_a_duplicate_slug_among_the_aged_items():
    # Both copies are older than the cutoff and both must move. Going
    # through `select_slugs` raised "the section has a duplicate slug"
    # and aborted the whole roll, including the five unambiguous items.
    rolled = roll_handoff.archive_older_than(AGED_DUPLICATE, "", 669, TODAY)
    assert rolled is not None
    new_live, new_archive, moved = rolled
    text = [" ".join(i.split()) for i in moved]
    assert sum("[stale-thing]" in t for t in text) == 2
    assert any("Cycle 641" in t for t in text)
    assert "[stale-thing]" not in new_live
    assert "[recent-thing]" in new_live


def test_a_duplicate_slug_still_stops_a_roll_that_names_it_by_hand():
    # The index path is for the age roll only. `--retire stale-thing`
    # is still a guess between two items and is still refused.
    with pytest.raises(roll_handoff.RollError) as excinfo:
        roll_handoff.archive_retired(
            AGED_DUPLICATE, "", ["stale-thing"], "finished", TODAY
        )
    assert "duplicate slug" in str(excinfo.value)


def test_nothing_to_roll_names_the_items_it_cannot_date():
    # `AGED` holds one undated item and one with no slug, and neither is
    # retirable at any cutoff. At cutoff 600 nothing moves, and the old
    # message said all six "cite cycle 600 or later" -- four do.
    items = live_items(AGED)
    said = roll_handoff.explain_none_older_than(items, 600)
    assert "of 6 handoff item(s)" in said
    assert "4 cite(s) cycle 600 or later" in said
    assert "1 carr(y/ies) no slug" in said
    assert "1 cite(s) no cycle at all" in said


def test_nothing_to_roll_says_only_the_true_thing_when_every_item_is_recent():
    # No unslugged or undated item, so no clause about them at all.
    every_item_dated = "\n\n".join(
        [
            "**[a-thing]** Cycle 671, recent.",
            "**[b-thing]** Cycle 670, also recent.",
        ]
    )
    said = roll_handoff.explain_none_older_than(
        live_items(
            AGED.split("## Next cycle")[0]
            + "## Next cycle\n\n"
            + every_item_dated
            + "\n\n## Digest\n\n**Cycle 671** (2026-08-30 18:00) — did a thing.\n"
        ),
        669,
    )
    assert "2 cite(s) cycle 669 or later" in said
    assert "no slug" not in said
    assert "no cycle at all" not in said


def test_nothing_to_roll_says_so_when_it_is_asked_out_of_sequence():
    # `explain_none_older_than` measures "recent" against the cutoff
    # rather than inferring it as everything left over, so a caller that
    # asks before `select_older_than` has returned empty is told, instead
    # of being handed a confident sentence about items nothing looked at.
    # AGED at cutoff 669: three items are genuinely at or after it, one
    # (stale-thing, cycle 640) is not, plus the unslugged and undated pair.
    items = live_items(AGED)
    said = roll_handoff.explain_none_older_than(items, 669)
    assert "3 cite(s) cycle 669 or later" in said
    assert "1 (y)our caller should have retired" in said
    assert "asked out of sequence" in said


def test_the_cli_prints_the_bucket_breakdown_when_nothing_rolls(tmp_path, capsys):
    # The wiring, end to end: `main` must print what the helper returns.
    # Cut against a digest whose oldest line is older than every handoff
    # item, so nothing is selected and the message is the whole output.
    live = tmp_path / "live.md"
    live.write_text(
        AGED.replace(
            "**Cycle 669** (2026-08-30 16:00)", "**Cycle 600** (2026-08-30 16:00)"
        )
    )
    archive = tmp_path / "archive.md"
    before = live.read_text()
    code = roll_handoff.main(
        ["--live", str(live), "--archive", str(archive),
         "--retire-older-than-digest"]
    )
    said = capsys.readouterr().out
    assert code == 0
    assert "nothing to roll: of 6 handoff item(s)" in said
    assert "4 cite(s) cycle 600 or later" in said
    assert "1 carr(y/ies) no slug" in said
    assert "1 cite(s) no cycle at all" in said
    assert "asked out of sequence" not in said
    assert live.read_text() == before


def test_a_bad_reason_is_reported_before_a_bad_slug():
    # Both are wrong. The reason guard ran first before `archive_retired`
    # became a wrapper, and a refactor is not the place to change which
    # of two complaints a caller hears.
    with pytest.raises(roll_handoff.RollError) as excinfo:
        roll_handoff.archive_retired(LIVE, "", ["no-such-slug"], "", TODAY)
    assert "needs a reason" in str(excinfo.value)


def test_an_index_passed_twice_is_stamped_and_reported_once():
    # `plan` sorts and dedups internally; `moved` is built from `picked`,
    # so without the same normalisation the second `.replace` would find
    # the already-stamped copy, change nothing, and still be counted.
    items = live_items(AGED)
    stale = [i for i, item in enumerate(items) if "[stale-thing]" in item]
    assert len(stale) == 1
    _, new_archive, moved = roll_handoff.archive_indices(
        AGED, "", stale * 2, "finished", TODAY
    )
    assert len(moved) == 1
    assert new_archive.count("**Retired") == 1
