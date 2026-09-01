"""`tools/roll_health.py` -- captures stranded outside the rollable section.

The live shape this was written against: `nova/resources/issues.md` carried
94 captures between its frontmatter and its own `# ` title, which the roller
cannot see and the board does not render. The fixtures below are that shape
in miniature, plus the shapes that must NOT fire.
"""

import io

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
