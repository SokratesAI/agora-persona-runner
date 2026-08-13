"""`tools/roll_digest.py` -- moving old digest lines into the archive.

The property under test is never "the output looks right". It is that a
line which went in comes out exactly once, on one side or the other, and
that the two sections Edvard actually reads survive the move.
"""

import pytest

from agora_runner.nova_journal import parse_digest
from tools.roll_digest import ARCHIVE_TITLE, plan, verify

LIVE = """---
type: log
---

# Journal — Digest

## Needs Edvard

Should the node be replaced?

## Next cycle

Check the deploy.

## Digest

**Cycle 5** (2026-08-11 17:00) — Fifth.

**Cycle 4** (2026-08-11 16:00) — Fourth.

**Cycle 3** (2026-08-11 15:00) — Third.

**Cycle 2** (2026-08-11 14:00) — Second.

**Cycle 1** (2026-08-11 13:00) — First.
"""

ARCHIVE = f"""---
type: log
---

{ARCHIVE_TITLE}

**Cycle 0** (2026-08-11 12:00) — Zeroth.
"""


def _lines(live, archive):
    """Every cycle number the pair holds, live first, by raw paragraph.

    Deliberately not via `parse_digest`: that drops what it cannot parse,
    so counting through it would agree with a roll that lost exactly the
    lines it also cannot see.
    """
    import re
    body = live.split("\n## Digest\n", 1)[1]
    tail = archive.split(ARCHIVE_TITLE, 1)[-1] if ARCHIVE_TITLE in archive else ""
    paras = [p.strip() for p in re.split(r"\n[ \t]*\n", body + "\n\n" + tail) if p.strip()]
    return [p.split("**")[1] for p in paras]


def test_lines_past_the_keep_move_to_the_archive_in_order():
    live, archive = plan(LIVE, ARCHIVE, keep=2)
    assert _lines(live, archive) == [
        "Cycle 5", "Cycle 4", "Cycle 3", "Cycle 2", "Cycle 1", "Cycle 0",
    ]
    assert "Cycle 3" in archive and "Cycle 3" not in live


def test_a_file_already_under_the_keep_is_left_alone():
    assert plan(LIVE, ARCHIVE, keep=99) == (LIVE, ARCHIVE)


def test_rolling_twice_changes_nothing_the_second_time():
    once = plan(LIVE, ARCHIVE, keep=2)
    assert plan(*once, keep=2) == once


def test_the_sections_edvard_reads_survive_the_roll():
    live, archive = plan(LIVE, ARCHIVE, keep=2)
    digest = parse_digest(f"{live}\n\n{archive}")
    assert "node" in digest["needsEdvard"]
    assert digest["hasNeedsEdvard"] is True
    assert "Check the deploy." in digest["nextCycle"]
    assert [line["cycle"] for line in digest["lines"]] == [5, 4, 3, 2, 1, 0]


def test_a_line_the_parser_cannot_read_is_moved_rather_than_dropped():
    # `**Cycle 94 (addendum)**` is real, is in the live file, and does
    # not match `_DIGEST_LINE_RE` -- so it is invisible to `parse_digest`
    # and a roll that verified only through the parser would be free to
    # lose it silently.
    odd = LIVE.replace(
        "**Cycle 3** (2026-08-11 15:00) — Third.",
        "**Cycle 3 (addendum)** (2026-08-11 15:00) — Third, again.",
    )
    assert not any(l["cycle"] == 3 for l in parse_digest(odd)["lines"])
    live, archive = plan(odd, ARCHIVE, keep=2)
    assert "Cycle 3 (addendum)" in archive
    assert "Third, again." in archive


def test_two_cards_written_without_a_blank_line_between_them_still_roll():
    # The failure this pins is a silent no-op, not a wrong answer. The site
    # ends a card at a `**Cycle N** (` whether or not a blank line precedes
    # it -- it has since Cycle 65 lost its card to exactly that -- and this
    # script used to end one only at a blank line. So a digest whose cards
    # are merged reads as fewer entries than the site shows, drops under the
    # keep, and rolls nothing: no error, no output, the file Edvard reads
    # growing forever.
    merged = LIVE.replace(
        "— Fourth.\n\n**Cycle 3**",
        "— Fourth.\n**Cycle 3**",
    )
    assert [l["cycle"] for l in parse_digest(merged)["lines"]] == [5, 4, 3, 2, 1]
    live, archive = plan(merged, ARCHIVE, keep=2)
    assert [l["cycle"] for l in parse_digest(live)["lines"]] == [5, 4]
    assert "Cycle 3" in archive and "Cycle 3" not in live
    # and the merge is repaired on the way past, not carried into the archive
    assert "— Fourth.\n**Cycle 3**" not in live + archive


def test_an_archive_that_could_hide_needs_edvard_is_refused():
    bad = ARCHIVE.replace(ARCHIVE_TITLE, ARCHIVE_TITLE + "\n\n## Digest")
    with pytest.raises(SystemExit, match="level-two heading"):
        verify(LIVE, ARCHIVE, LIVE, bad)


def test_losing_a_line_the_parser_can_see_is_refused():
    live, archive = plan(LIVE, ARCHIVE, keep=2)
    lost = archive.replace("**Cycle 1** (2026-08-11 13:00) — First.\n\n", "")
    with pytest.raises(SystemExit, match="digest lines in"):
        verify(LIVE, ARCHIVE, live, lost)


def test_losing_a_line_the_parser_cannot_see_is_also_refused():
    # The one the payload comparison is blind to, and the whole reason
    # `verify` counts raw paragraphs as well: `parse_digest` never
    # emitted this line, so dropping it leaves both payloads identical.
    odd = LIVE.replace(
        "**Cycle 3** (2026-08-11 15:00) — Third.",
        "**Cycle 3 (addendum)** (2026-08-11 15:00) — Third, again.",
    )
    live, archive = plan(odd, ARCHIVE, keep=2)
    lost = archive.replace("**Cycle 3 (addendum)** (2026-08-11 15:00) — Third, again.\n\n", "")
    assert parse_digest(f"{live}\n\n{archive}") == parse_digest(f"{live}\n\n{lost}")
    with pytest.raises(SystemExit, match="digest lines in"):
        verify(odd, ARCHIVE, live, lost)


def test_prose_in_the_digest_section_stops_the_roll_rather_than_guessing():
    strayed = LIVE.replace(
        "## Digest\n\n**Cycle 5**", "## Digest\n\nA note somebody left here.\n\n**Cycle 5**"
    )
    with pytest.raises(SystemExit, match="not a cycle line"):
        plan(strayed, ARCHIVE, keep=2)


def test_a_missing_digest_section_stops_the_roll():
    with pytest.raises(SystemExit, match="no '## Digest' section"):
        plan("## Needs Edvard\n\nNothing.\n", "", keep=2)


def test_a_first_roll_with_no_archive_yet_builds_one_the_site_can_read():
    live, archive = plan(LIVE, "", keep=2)
    assert ARCHIVE_TITLE in archive
    assert [l["cycle"] for l in parse_digest(f"{live}\n\n{archive}")["lines"]] == [5, 4, 3, 2, 1]


# --- recovering from a half-applied run -----------------------------------
#
# The two vault writes are not atomic and the archive goes first, so a
# cycle killed between them leaves the same lines in both files. Reviewer
# finding on runner#93: nothing detected that, and the next run rolled
# them again, so a cycle rendered twice on Edvard's phone forever.


def _crashed_between_the_two_writes(keep=2):
    """The state a cycle killed after the archive write leaves behind:
    the new archive, and the live file still un-rolled."""
    _, new_archive = plan(LIVE, ARCHIVE, keep=keep)
    return LIVE, new_archive


def test_a_run_after_a_crash_does_not_file_the_same_line_twice():
    live, archive = _crashed_between_the_two_writes()
    recovered_live, recovered_archive = plan(live, archive, keep=2)
    cycles = [l["cycle"] for l in parse_digest(f"{recovered_live}\n\n{recovered_archive}")["lines"]]
    assert cycles == [5, 4, 3, 2, 1, 0], "every line once, newest first"
    assert len(cycles) == len(set(cycles))


def test_the_crash_state_really_is_a_duplicate_before_the_recovery():
    # Without this the test above could pass against a plan() that never
    # rolls at all: it pins that the state being recovered from is
    # genuinely broken, so the recovery has something to do.
    live, archive = _crashed_between_the_two_writes()
    cycles = [l["cycle"] for l in parse_digest(f"{live}\n\n{archive}")["lines"]]
    assert cycles.count(3) == 2 and cycles.count(2) == 2


def test_recovery_still_refuses_if_it_would_drop_something_new():
    live, archive = _crashed_between_the_two_writes()
    new_live, new_archive = plan(live, archive, keep=2)
    lost = new_archive.replace("**Cycle 1** (2026-08-11 13:00) — First.\n\n", "")
    with pytest.raises(SystemExit, match="digest lines in"):
        verify(live, archive, new_live, lost)


def test_a_recovery_run_actually_passes_its_own_verification():
    # `main` runs plan then verify, so a recovery that plan handles but
    # verify rejects is a tool that cannot recover. Caught by mutation:
    # dropping `_dedup` from verify breaks nothing else in this file.
    live, archive = _crashed_between_the_two_writes()
    verify(live, archive, *plan(live, archive, keep=2))


def test_a_single_rolled_line_is_not_announced_as_plural():
    """The extraction replaced the original's `line(s)` with a plural
    noun, which made a one-line roll print "1 digest lines roll off"."""
    import io, contextlib, tempfile, os
    from tools.roll_digest import main
    with tempfile.TemporaryDirectory() as d:
        live, archive = os.path.join(d, "live.md"), os.path.join(d, "archive.md")
        open(live, "w").write(LIVE)
        open(archive, "w").write(ARCHIVE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--live", live, "--archive", archive, "--keep", "4", "--dry-run"])
        assert "1 digest line roll off" in out.getvalue(), out.getvalue()
