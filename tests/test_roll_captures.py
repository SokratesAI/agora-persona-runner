"""`tools/roll_captures.py` -- moving old captures into a cold archive.

Same property as `test_roll_digest.py`: a capture that went in comes out
exactly once, on one side or the other. The differences worth their own
tests are the two things the capture files do that the digest does not --
a bullet can span several lines, and the archive title is derived from
the live file rather than hard-coded.
"""

import pytest

from tools.roll_captures import KEEP, archive_title, plan, spec_for, verify
from tools.rolling import split_bullets

LIVE = """---
type: note
---

# Nova — Issues

Crude capture only, my own notes, one line each.

## Entries

- 2026-08-11 (Cycle 5) — Fifth.

- 2026-08-11 (Cycle 4) — Fourth, and it wraps:
  a continuation line that belongs to the bullet above it.

- 2026-08-11 (Cycle 3) — Third.
- 2026-08-11 (Cycle 2) — Second.

- 2026-08-11 (Cycle 1) — First.
"""

ARCHIVE = """---
type: log
---

# Nova — Issues Archive

- 2026-08-10 (Cycle 0) — Zeroth.
"""


def _captures(live, archive):
    """Every capture the pair holds, live first, as raw text."""
    body = live.split("\n## Entries\n", 1)[1]
    title = "# Nova — Issues Archive"
    tail = archive.split(title, 1)[-1] if title in archive else ""
    return split_bullets(body) + split_bullets(tail)


def test_nothing_rolls_while_the_file_is_short_enough():
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=KEEP)
    assert (new_live, new_archive) == (LIVE, ARCHIVE)


def test_every_capture_survives_the_roll():
    before = _captures(LIVE, ARCHIVE)
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=2)
    verify(LIVE, ARCHIVE, new_live, new_archive)
    assert _captures(new_live, new_archive) == before
    assert len(before) == 6


def test_the_newest_stay_live_and_the_rest_go_cold():
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=2)
    live_body = new_live.split("\n## Entries\n", 1)[1]
    assert split_bullets(live_body) == _captures(LIVE, ARCHIVE)[:2]
    assert "Cycle 3" not in new_live
    assert "Cycle 3" in new_archive


def test_a_wrapped_bullet_is_not_torn_in_half():
    """The continuation line has to move with its own bullet.

    Splitting on newline would leave `- ... it wraps:` live and file the
    continuation on its own, which reads as a whole capture in neither
    file. This is the one real difference from the digest's paragraphs.
    """
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=1)
    wrapped = [c for c in _captures(new_live, new_archive) if "Fourth" in c]
    assert len(wrapped) == 1
    assert wrapped[0] == (
        "- 2026-08-11 (Cycle 4) — Fourth, and it wraps:\n"
        "  a continuation line that belongs to the bullet above it."
    )
    assert "a continuation line" not in new_live


def test_the_head_above_the_entries_is_untouched():
    new_live, _ = plan(LIVE, ARCHIVE, keep=2)
    assert new_live.startswith(LIVE[: LIVE.index("\n## Entries\n")])


def test_the_archive_title_comes_from_the_live_file():
    assert archive_title(LIVE) == "# Nova — Issues Archive"
    assert archive_title("# Nova — Ideas\n\n## Entries\n") == "# Nova — Ideas Archive"


def test_a_live_file_with_no_title_is_refused_rather_than_guessed():
    with pytest.raises(SystemExit, match="no '# ' title"):
        archive_title("## Entries\n\n- 2026-08-11 — orphan.\n")


def test_a_fresh_archive_gets_the_derived_title():
    new_live, new_archive = plan(LIVE, "", keep=2)
    verify(LIVE, "", new_live, new_archive)
    assert "# Nova — Issues Archive" in new_archive
    assert "roll_captures.py" in new_archive


def test_an_existing_archive_keeps_its_own_header_verbatim():
    _, new_archive = plan(LIVE, ARCHIVE, keep=2)
    assert new_archive.startswith("---\ntype: log\n---\n\n# Nova — Issues Archive")


def _crashed_between_the_two_writes():
    """The archive is written first, so this state is reachable: the roll
    is in the archive and the live file never got rewritten."""
    _, new_archive = plan(LIVE, ARCHIVE, keep=2)
    return LIVE, new_archive


def test_a_half_applied_run_is_repaired_not_compounded():
    live, archive = _crashed_between_the_two_writes()
    new_live, new_archive = plan(live, archive, keep=2)
    verify(live, archive, new_live, new_archive)
    for capture in _captures(LIVE, ARCHIVE):
        assert (new_live + new_archive).count(capture) == 1


def test_losing_a_capture_is_refused():
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=2)
    lost = new_archive.replace("- 2026-08-11 (Cycle 1) — First.\n\n", "")
    with pytest.raises(SystemExit, match="captures in"):
        verify(LIVE, ARCHIVE, new_live, lost)


def test_prose_under_entries_is_refused_rather_than_split_blind():
    broken = LIVE.replace(
        "- 2026-08-11 (Cycle 5) — Fifth.",
        "A paragraph explaining the file, which is not a capture.",
    )
    with pytest.raises(SystemExit, match="neither a bullet nor a heading"):
        plan(broken, ARCHIVE, keep=2)


def test_a_missing_entries_section_is_refused():
    with pytest.raises(SystemExit, match="'## Entries'"):
        plan("# Nova — Issues\n\n- 2026-08-11 — orphan.\n", ARCHIVE, keep=2)


def test_a_stray_heading_travels_as_its_own_capture():
    """Both real files hold one `### Cycle 94` from a cycle that used a
    different shape. It must move rather than be swallowed or dropped."""
    live = LIVE.replace(
        "- 2026-08-11 (Cycle 3) — Third.",
        "### Cycle 94\n- 2026-08-11 (Cycle 3) — Third.",
    )
    new_live, new_archive = plan(live, ARCHIVE, keep=2)
    verify(live, ARCHIVE, new_live, new_archive)
    assert "### Cycle 94" in new_archive
    assert "### Cycle 94" not in new_live


def test_spec_noun_reaches_the_failure_message():
    spec = spec_for(LIVE)
    assert spec.noun == "captures"


def test_a_file_that_is_not_newest_first_is_refused():
    """The defect that stopped this tool being pointed at the live files.

    `plan` keeps the top `keep` entries, which is "keep the newest" only
    if the file is newest-first. The live `issues.md` is not: its top
    half descends from the prepend era and its bottom half ascends from
    the append era, so the newest captures are at the bottom.
    """
    live = LIVE.replace(
        "- 2026-08-11 (Cycle 1) — First.",
        "- 2026-08-11 (Cycle 1) — First.\n\n- 2026-08-11 (Cycle 9) — Appended at the bottom.",
    )
    with pytest.raises(SystemExit, match=r"not newest-first"):
        plan(live, ARCHIVE, keep=2)


def test_the_order_guard_fires_even_when_nothing_would_roll():
    """A short file is still told it is mis-ordered.

    Otherwise the refusal only appears on the day the file grows past
    `keep` -- which is the day it would silently roll the wrong end.
    """
    live = LIVE.replace(
        "- 2026-08-11 (Cycle 1) — First.",
        "- 2026-08-11 (Cycle 1) — First.\n\n- 2026-08-11 (Cycle 9) — Appended at the bottom.",
    )
    with pytest.raises(SystemExit, match=r"not newest-first"):
        plan(live, ARCHIVE, keep=KEEP)


def test_a_descending_file_passes_the_order_guard():
    """The guard must not refuse the shape it is meant to allow."""
    new_live, new_archive = plan(LIVE, ARCHIVE, keep=2)
    verify(LIVE, ARCHIVE, new_live, new_archive)
    assert "Cycle 5" in new_live


def test_entries_without_a_cycle_number_do_not_trip_the_guard():
    """Only ~a third of real captures carry `(Cycle N)`; the rest must be
    ignored rather than treated as out of order."""
    live = LIVE.replace(
        "- 2026-08-11 (Cycle 3) — Third.",
        "- 2026-08-11 — no cycle number here at all.",
    )
    new_live, new_archive = plan(live, ARCHIVE, keep=2)
    verify(live, ARCHIVE, new_live, new_archive)
