"""`tools/roll_health.py` -- captures stranded outside the rollable section.

The live shape this was written against: `nova/resources/issues.md` carried
94 captures between its frontmatter and its own `# ` title, which the roller
cannot see and the board does not render. The fixtures below are that shape
in miniature, plus the shapes that must NOT fire.
"""

import io
import re

import pytest

from tools import roll_health
from tools.roll_captures import MARKER


def document(strays=(), entries=(), title="# Nova — Issues",
             frontmatter="---\ntype: note\nstatus: capture\n---\n"):
    """A capture file, with `strays` sitting above the `## Entries` marker."""
    parts = [frontmatter]
    parts.extend("\n" + s + "\n" for s in strays)
    parts.append("\n" + title + "\n")
    parts.append(MARKER)
    parts.extend("\n" + e + "\n" for e in entries)
    parts.append("\n## Retired\n\n- an old one\n\n# Details\n\n- a detail\n")
    return "".join(parts)


ARCHIVE = "---\ntype: log\n---\n\n# Nova — Issues Archive\n\n## Entries\n"


def test_a_capture_above_the_marker_is_stranded():
    live = document(strays=["- 2026-08-31 (Cycle 733) — one",
                            "- 2026-08-30 (Cycle 700) — two"],
                    entries=["- 2026-08-29 (Cycle 600) — three"])
    assert roll_health.stranded_bullets(live) == [
        "- 2026-08-31 (Cycle 733) — one",
        "- 2026-08-30 (Cycle 700) — two",
    ]


def test_a_tidy_file_strands_nothing():
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert roll_health.stranded_bullets(live) == []


def test_a_yaml_list_in_the_frontmatter_is_not_a_stranded_capture():
    """`tags:` written as a block list puts `- ` lines above the marker.

    Written un-indented on purpose: YAML allows a sequence at the parent
    key's own column, and an indented one would not start with `- ` at all,
    so the fixture would pass whether or not the frontmatter were stripped.
    """
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"],
                    frontmatter="---\ntype: note\ntags:\n- agora\n- nova\n---\n")
    assert roll_health.stranded_bullets(live) == []


def test_bullets_below_the_section_are_not_counted():
    """`## Retired` and `# Details` hold bullets legitimately."""
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert "- an old one" in live and "- a detail" in live
    assert roll_health.stranded_bullets(live) == []


def test_span_names_the_oldest_and_newest_cycle():
    assert roll_health.span(["- 2026-08-31 (Cycle 733) — one",
                             "- 2026-08-01 (Cycle 355) — two"]) == \
        "Cycle 355 to Cycle 733"


def test_span_says_so_when_nothing_carries_a_marker():
    assert "none carrying" in roll_health.span(["- an undated note"])


def test_inspect_reports_a_refusal_instead_of_guessing_whether_a_roll_is_owed():
    """A mis-ordered file makes the roller raise; `owed` is unknowable then."""
    live = document(entries=["- 2026-08-01 (Cycle 100) — old",
                             "- 2026-08-29 (Cycle 600) — new"])
    stranded, refusal, owed = roll_health.inspect(live, ARCHIVE)
    assert refusal is not None and "newest-first" in refusal
    assert owed is None


def test_inspect_says_a_roll_is_owed_past_keep():
    entries = [f"- 2026-08-29 (Cycle {900 - i}) — n{i}"
               for i in range(roll_health.roll_captures.KEEP + 3)]
    stranded, refusal, owed = roll_health.inspect(document(entries=entries), ARCHIVE)
    assert refusal is None and owed is True and stranded == []


def test_inspect_says_no_roll_is_owed_under_keep():
    entries = [f"- 2026-08-29 (Cycle {900 - i}) — n{i}" for i in range(3)]
    stranded, refusal, owed = roll_health.inspect(document(entries=entries), ARCHIVE)
    assert refusal is None and owed is False


def _fetch_from(docs):
    return lambda path: docs.get(path)


def test_a_stranded_capture_exits_2_and_names_the_file():
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(strays=["- 2026-08-31 (Cycle 733) — one"],
                                entries=["- 2026-08-29 (Cycle 600) — three"]),
            archive_path: ARCHIVE}
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert unreadable == [] and clean == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 2
    printed = out.getvalue()
    assert live_path in printed
    assert "1 capture(s) sit above" in printed


def test_a_document_that_could_not_be_read_exits_1_and_not_0():
    live_path, archive_path = roll_health.PAIRS[0]
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from({}))
    assert unreadable == [live_path] and findings == [] and clean == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 1
    assert "no instrument" in out.getvalue()


def test_a_missing_archive_alone_is_still_unreadable():
    """`get` prints `[not found:]` and exits 0, so a half-read pair must not
    be judged as a clean one."""
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(entries=["- 2026-08-29 (Cycle 600) — three"])}
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert unreadable == [archive_path] and clean == []


def test_a_clean_pair_exits_0_and_prints_what_it_did_not_judge():
    live_path, archive_path = roll_health.PAIRS[0]
    docs = {live_path: document(entries=["- 2026-08-29 (Cycle 600) — three"]),
            archive_path: ARCHIVE}
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],), fetch=_fetch_from(docs))
    assert findings == [] and unreadable == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 0
    assert "Not judged" in out.getvalue()


def test_roll_health_is_in_preflight():
    from tools import preflight
    assert "roll_health" in preflight.CHECKS


def _board(rows, details):
    """A live capture file whose `## Board` and `# Details` are real.

    `document()` above writes a `# Details` section holding one bullet and no
    `### #N` block at all, which is the shape `writeups` must answer `None`
    for -- so a fixture with real write-ups has to be built separately rather
    than by passing another argument to it.
    """
    head = ["---", "type: note", "---", "", "# Nova — Issues", MARKER.strip(),
            "", "- 2026-08-29 (Cycle 600) — a capture", "", "## Board", "",
            "| # | Item | Status | Updated | Priority |",
            "|---|------|--------|---------|---|"]
    for number, status in rows:
        head.append(f"| [[#{number} — T{number}\\|{number}]] | T{number} "
                    f"| {status} | 09-02 |  |")
    head += ["", "# Details", ""]
    for number, body in details:
        head += [f"### #{number} — T{number}", "", body, ""]
    return "\n".join(head) + "\n"


def test_writeups_is_none_when_the_file_has_no_write_up_blocks():
    """The precondition for the test below: `document()` has a `# Details`
    heading and no `### #N` block under it, so a `None` here is the absence
    of write-ups and not the absence of the section."""
    live = document(entries=["- 2026-08-29 (Cycle 600) — three"])
    assert "# Details" in live
    assert roll_health.writeups(live) is None


def test_writeups_counts_the_bodies_and_names_the_largest():
    live = _board([(1, "⚪ Backlog"), (2, "⚪ Backlog")],
                  [(1, "x" * 40), (2, "y" * 400)])
    marks = roll_health.writeups(live)
    assert marks["count"] == 2
    assert marks["bytes"] >= 440
    assert marks["largest"][0] == 2
    assert marks["done_count"] == 0 and marks["done_bytes"] == 0


def test_writeups_separates_a_done_rows_body_from_an_open_ones():
    live = _board([(1, "✅ Done"), (2, "⚪ Backlog")],
                  [(1, "x" * 40), (2, "y" * 400)])
    marks = roll_health.writeups(live)
    assert marks["count"] == 2 and marks["done_count"] == 1
    assert 0 < marks["done_bytes"] < marks["bytes"]


def test_the_report_says_how_little_the_capture_roll_moves():
    """The finding this was built for: `owed` is true, and the roll it names
    moves a rounding error against the write-ups nothing rolls."""
    live_path, archive_path = roll_health.PAIRS[0]
    entries = "\n".join(f"- 2026-08-29 (Cycle {900 - i}) — n{i}"
                        for i in range(roll_health.roll_captures.KEEP + 2))
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture", entries)
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 2
    printed = out.getvalue()
    assert "A roll is owed" in printed
    moved = int(re.search(r"The capture roll moves ([\d,]+) of", printed)
                .group(1).replace(",", ""))
    # A real, non-zero number that is nonetheless small against the file --
    # `in printed` alone passes on a hardcoded 0, which is the whole claim.
    assert 0 < moved < len(live) // 2
    assert "write-up bodies across 1 row(s)" in printed
    assert "would move nothing" in printed
    assert "largest single write-up is row #1" in printed


def test_the_report_points_at_roll_done_details_when_one_would_move():
    live_path, archive_path = roll_health.PAIRS[0]
    entries = "\n".join(f"- 2026-08-29 (Cycle {900 - i}) — n{i}"
                        for i in range(roll_health.roll_captures.KEEP + 2))
    live = _board([(1, "✅ Done")], [(1, "y" * 4000)])
    live = live.replace("- 2026-08-29 (Cycle 600) — a capture", entries)
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 2
    printed = out.getvalue()
    assert "tools.roll_done_details" in printed
    assert "would move nothing" not in printed


def test_a_clean_file_still_says_what_it_is_made_of():
    """The hole the first version left: `Rollable` says nothing about the
    62,801 bytes of write-ups that survive the roll, and the whole finding
    disappeared the moment the roll it named was actually run."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    assert findings == [] and unreadable == []
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 0
    printed = out.getvalue()
    assert "Rollable" in printed
    assert "write-up bodies across 1 row(s)" in printed
    assert "largest single write-up is row #1" in printed


def test_the_clean_summary_line_is_last_and_carries_the_write_up_weight():
    """`preflight` collapses an exit-0 check to its last line holding a
    digit, so a decomposition printed above the tail note is invisible on a
    normal morning."""
    live_path, archive_path = roll_health.PAIRS[0]
    live = _board([(1, "⚪ Backlog")], [(1, "y" * 4000)])
    findings, unreadable, clean = roll_health.check(
        pairs=(roll_health.PAIRS[0],),
        fetch=_fetch_from({live_path: live, archive_path: ARCHIVE}))
    out = io.StringIO()
    assert roll_health.report(findings, unreadable, clean, out=out) == 0
    lines = [ln for ln in out.getvalue().split("\n") if ln.strip()]
    last_with_digit = [ln for ln in lines if re.search(r"\d", ln)][-1]
    assert "write-ups no roller moves" in last_with_digit
    assert "0 of 1 on a done row" in last_with_digit
