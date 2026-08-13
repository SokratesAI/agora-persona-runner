"""`tools/lint_entry.py` -- the check that runs before an entry is written.

The tests that matter here are the ones pinning what the checker must
*not* do, because every one of them is a way this tool could pass while
being useless:

- it must apply `_FOOTER_RE` itself, not ask `parse_journal` whether the
  entry ended up with a `pr`. `parse_journal` repairs before it answers,
  so that question cannot come back negative and the first version of
  this checker was blind to all three live entries with a misplaced
  footer.
- it must not require the `---` rule above the footer. 17 live entries
  do not have one and are correct.
- it must not report a bad heading twice, once as a heading and again as
  a consequence.
"""

import pytest

from tools.lint_entry import lint, main

GOOD = """### Cycle 152 — 2026-08-13 02:00 Oslo

Something real happened and here is the honest account of it.

---
PR: #133 | Outcome: merged
"""


def _kinds(findings):
    return sorted(f.split(":")[0] for f in findings)


def test_a_correctly_written_entry_passes():
    assert lint("168-cycle-152.md", GOOD) == []


def test_footer_without_the_rule_above_it_passes():
    """17 live entries end this way and every one of them is correct.

    `_FOOTER_RE` makes the `---` optional on purpose -- Cycle 104 wrote
    the `Reviewer:` line where the rule goes, and its card showed no PR
    for an hour that had merged one. A linter written from
    `personality.md`'s prose instead of from the parser would fail a
    sixth of the real journal.
    """
    entry = GOOD.replace("---\nPR:", "Reviewer: 3 findings, 3 acted on\nPR:")
    assert lint("168-cycle-152.md", entry) == []


def test_heading_at_the_wrong_depth_is_caught():
    findings = lint("168-cycle-152.md", GOOD.replace("### Cycle", "## Cycle"))
    assert _kinds(findings) == ["heading"]
    assert "## Cycle 152" in findings[0]


def test_frontmatter_before_the_heading_is_caught():
    entry = "---\ntype: log\n---\n\n" + GOOD
    assert _kinds(lint("168-cycle-152.md", entry)) == ["heading"]


def test_a_promoted_heading_with_the_wrong_number_reports_both():
    """Two independent defects, and the author needs to see both at once.

    A blanket "skip the cycle check whenever the heading is wrong" guard
    passed every test in this file, because the only case they exercised
    was the synthesised one below, where the check cannot fire at all. It
    meant the author fixed the hash count, re-ran, and only then found out
    the number was wrong too.
    """
    entry = GOOD.replace("### Cycle 152", "## Cycle 153")
    assert _kinds(lint("168-cycle-152.md", entry)) == ["cycle", "heading"]


def test_a_synthesised_heading_cannot_disagree_with_the_filename():
    """The other branch: `normalise_entry` builds the heading *from* the
    filename, so comparing the two afterwards is a check of nothing."""
    entry = "body\n\n---\nPR: #1 | Outcome: merged\n"
    assert _kinds(lint("168-cycle-152.md", entry)) == ["heading"]


def test_a_broken_heading_is_reported_once_not_twice():
    """The heading check must not also surface as a footer or cycle finding.

    `normalise_entry` synthesises a heading from the filename, so an
    entry with no heading still parses and still has its footer. Reporting
    the same defect three ways is how a cycle fixes one thing and sees the
    count go up.
    """
    entry = GOOD.replace("### Cycle 152 — 2026-08-13 02:00 Oslo\n\n", "")
    assert _kinds(lint("168-cycle-152.md", entry)) == ["heading"]


def test_footer_bolded_at_the_top_is_caught():
    """Cycles 146 and 147, verbatim in shape. The site repairs it; the badge
    is right and the author is not there to be told."""
    entry = (
        "### Cycle 152 — 2026-08-13 02:00 Oslo\n\n"
        "**PR: #133 | Outcome: merged**\n\n"
        "Something real happened and here is the honest account of it.\n"
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["footer"]
    assert "not at the end of it" in findings[0]


def test_footer_hard_wrapped_is_caught():
    """Entry 004's shape: correct, in the right place, split across two
    lines, so `_FOOTER_RE`'s `$` lands on the continuation."""
    entry = GOOD.replace(
        "PR: #133 | Outcome: merged",
        "PR: #133 | Outcome: open, green, deliberately unmerged so this reply\nsurvives",
    )
    assert _kinds(lint("168-cycle-152.md", entry)) == ["footer"]


def test_missing_footer_says_so_differently_than_a_misplaced_one():
    entry = "### Cycle 152 — 2026-08-13 02:00 Oslo\n\nNo footer at all.\n"
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["footer"]
    assert "reads as an hour that shipped nothing" in findings[0]


def test_a_quoted_footer_in_a_code_fence_is_not_mistaken_for_the_real_one():
    """`personality.md` states the footer format as a fenced block, so an
    entry quoting it is a thing a cycle would plausibly write."""
    entry = (
        "### Cycle 152 — 2026-08-13 02:00 Oslo\n\n"
        "The rule says to end with:\n\n"
        "```\nPR: #23 | Outcome: merged\n```\n\n"
        "and I did not.\n"
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["footer"]
    assert "reads as an hour that shipped nothing" in findings[0]


def test_heading_cycle_number_disagreeing_with_the_filename_is_caught():
    assert _kinds(lint("168-cycle-153.md", GOOD)) == ["cycle"]


def test_a_filename_with_no_cycle_number_is_not_a_finding():
    """Entry 004 is Edvard's own first message and never had one."""
    assert lint("004-2026-08-02-edvard-s-first-message-not-a.md", GOOD) == []


def test_an_addendum_filename_still_checks_its_cycle_number():
    assert lint("169-cycle-152-addendum.md", GOOD) == []
    assert _kinds(lint("169-cycle-153-addendum.md", GOOD)) == ["cycle"]


def test_two_headings_in_one_document_is_caught():
    assert "split" in " ".join(lint("168-cycle-152.md", GOOD + "\n" + GOOD))


def test_an_empty_file_is_caught_rather_than_passing():
    assert _kinds(lint("168-cycle-152.md", "   \n\n")) == ["empty"]


def test_main_exits_zero_on_a_good_entry_and_one_on_a_bad_one(tmp_path, capsys):
    good = tmp_path / "168-cycle-152.md"
    good.write_text(GOOD, encoding="utf-8")
    assert main([str(good)]) == 0

    bad = tmp_path / "169-cycle-153.md"
    bad.write_text(GOOD.replace("### Cycle", "## Cycle"), encoding="utf-8")
    assert main([str(bad)]) == 1
    assert "would be repaired" in capsys.readouterr().err


def test_main_uses_the_name_it_will_be_written_under(tmp_path):
    """The entry is drafted as `entry.md` and `put` under its real name, so
    the filename checks have to run against the destination."""
    draft = tmp_path / "entry.md"
    draft.write_text(GOOD, encoding="utf-8")
    assert main([str(draft)]) == 0
    assert main([str(draft), "--name", "168-cycle-153.md"]) == 1


def test_main_exits_two_when_the_file_cannot_be_read(tmp_path):
    assert main([str(tmp_path / "nope.md")]) == 2


@pytest.mark.parametrize(
    "name,entry,expected",
    [
        ("146-cycle-131.md", "---\ntype: log\n---\n\n### Cycle 131 — x\n\nb\n\n---\nPR: none | Outcome: shipped", ["heading"]),
        ("162-cycle-146.md", "## Cycle 146 — x\n\n**PR: runner#128 | Outcome: merged**\n\nb\n", ["footer", "heading"]),
    ],
)
def test_the_shapes_that_actually_reached_the_vault(name, entry, expected):
    """Reduced from the live documents. Six cycles wrote these four files
    and every one of them was found afterwards, by Edvard or by a cycle
    reading the folder, never by anything at write time."""
    assert _kinds(lint(name, entry)) == expected
