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

from datetime import datetime

from agora_runner.config import OSLO
from agora_runner.nova_journal import split_ask
from tools.lint_entry import _raw_body, lint, main

GOOD = """### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title

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
    entry = GOOD.replace("### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\n", "")
    assert _kinds(lint("168-cycle-152.md", entry)) == ["heading"]


def test_footer_bolded_at_the_top_is_caught():
    """Cycles 146 and 147, verbatim in shape. The site repairs it; the badge
    is right and the author is not there to be told."""
    entry = (
        "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\n"
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
    entry = "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\nNo footer at all.\n"
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["footer"]
    assert "reads as an hour that shipped nothing" in findings[0]


def test_a_quoted_footer_in_a_code_fence_is_not_mistaken_for_the_real_one():
    """`personality.md` states the footer format as a fenced block, so an
    entry quoting it is a thing a cycle would plausibly write."""
    entry = (
        "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\n"
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
    assert "should not be written as it stands" in capsys.readouterr().err


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


def test_an_entry_that_quotes_the_entries_marker_is_accepted():
    """This used to be refused, and the refusal was right at the time: the
    site cut the whole assembled corpus at the first `## Entries` line, so
    one such line inside one entry deleted every newer entry from the feed.
    runner#135 fixed that at the parser, and the rule went with it.

    Keeping it would have meant refusing an entry for writing a true
    sentence about the append command `prompt.md` step 6 tells every cycle
    to use -- a rule with no danger left behind it, on the one subject this
    journal is guaranteed to keep writing about.

    The assertion that matters is the second one: `lint` parses a single
    entry document, which has no preamble, so it must pass
    `strip_header=False` or the marker cuts this entry's own heading off
    and the document reports as `unparseable` instead."""
    entry = (
        "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\n"
        "The marker matters here:\n\n## Entries\n\nis what the old file used.\n\n"
        "---\nPR: #133 | Outcome: merged\n"
    )
    findings = lint("168-cycle-152.md", entry)
    assert findings == []


def test_the_same_marker_in_backticks_is_fine():
    """Live entries already quote it inline; only a line start truncates."""
    entry = GOOD.replace("Something real", "The `## Entries` marker, inline. Something real")
    assert lint("168-cycle-152.md", entry) == []


def test_the_footer_check_is_bounded_to_this_entry_not_the_document():
    """Two headings, the first with no footer and the second ending
    correctly. Taking the body to the end of the document let
    `_FOOTER_RE`'s end-anchor match the *second* entry's footer, so a
    missing footer on the first went unreported."""
    entry = (
        "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\nNo footer on this one.\n\n"
        "### Cycle 152 — 2026-08-01 02:30 Oslo\n\nBody.\n\n---\nPR: #133 | Outcome: merged\n"
    )
    kinds = _kinds(lint("168-cycle-152.md", entry))
    assert "footer" in kinds and "split" in kinds


# --- the stamp, which two consecutive cycles got wrong ---------------------

NOW = datetime(2026, 8, 13, 7, 23, tzinfo=OSLO)


def _stamped(time):
    return (
        f"### 2026-08-13 {time} (Oslo) — Cycle 158 — A Real Title\n\n"
        "Something real happened and here is the honest account of it.\n\n"
        "---\nPR: #141 | Outcome: merged\n"
    )


def test_a_heading_stamped_in_the_future_is_caught():
    """Cycle 157 was 34 minutes ahead, Cycle 158 twenty -- both guessed.

    The stamp is not decoration: the feed sorts on it and the eight-cycle
    report selects on it, so a heading dated ahead of the clock reorders
    cards and can pull a cycle into the wrong report.
    """
    findings = lint("175-cycle-158.md", _stamped("07:43"), now=NOW)
    assert len(findings) == 1
    assert findings[0].startswith("stamp:")
    assert "20 minutes from now" in findings[0]


def test_a_heading_stamped_now_passes():
    assert lint("175-cycle-158.md", _stamped("07:23"), now=NOW) == []


def test_a_heading_stamped_earlier_passes():
    """The normal case: the heading is written, then the entry takes minutes.

    Only the future side is checked, because no honest threshold separates
    a slow cycle from a backdated one.
    """
    assert lint("175-cycle-158.md", _stamped("06:40"), now=NOW) == []


def test_an_impossible_time_is_caught_rather_than_swallowed():
    """`_TIME_RE` accepts `\\d{1,2}:\\d{2}`, so `25:10` parses as a stamp.

    It is the exact shape of the mistake this check exists for -- a time
    nobody read off a clock -- and returning None on the parse failure
    passed it.
    """
    findings = lint("175-cycle-158.md", _stamped("25:10"), now=NOW)
    assert len(findings) == 1
    assert findings[0].startswith("stamp:")
    assert "not a real time" in findings[0]


def test_the_shared_fixture_does_not_depend_on_when_the_suite_runs():
    """`GOOD` was stamped with the day it was written, and roughly twenty
    tests here lint it without passing `now`.

    Every one of those would have reported a spurious stamp finding at any
    instant before that heading's own time -- green afterwards purely
    because the calendar had moved on. Pinning it at a fixed instant is
    what makes the rest of this file mean the same thing on every run.
    """
    assert lint("168-cycle-152.md", GOOD) == []
    assert lint("168-cycle-152.md", GOOD, now=datetime(2027, 1, 1, tzinfo=OSLO)) == []


# --- The optional `Board:` field ------------------------------------------


def test_a_board_field_the_site_can_link_passes():
    entry = GOOD.replace("PR: #133 | Outcome: merged", "PR: #133 | Board: idea #68 | Outcome: merged")
    assert lint("168-cycle-152.md", entry) == []


def test_no_board_field_is_never_a_finding():
    """It is optional, and a cycle that did not work off the board should
    say nothing rather than invent a number. 197 entries predate the field
    and every one of them must still pass."""
    assert lint("168-cycle-152.md", GOOD) == []


@pytest.mark.parametrize(
    "field",
    ["#68", "68", "the journal-card idea", "idea 68"],
)
def test_a_board_field_with_nothing_linkable_is_caught(field):
    """The failure this exists for: `parse_board_refs` leaves what it cannot
    place as plain text, on purpose, so `Board: #68` renders as the literal
    characters and looks like a working reference until Edvard taps it. An
    entry is written once and never edited, so this is the last cheap
    moment."""
    entry = GOOD.replace(
        "PR: #133 | Outcome: merged", f"PR: #133 | Board: {field} | Outcome: merged"
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["board"]
    assert field in findings[0]


def test_a_missing_footer_is_not_also_reported_as_a_bad_board():
    """Same rule as the heading: one defect, one finding. The board check
    reads the same match `_footer_finding` did, so an entry with no footer
    at all must report only that."""
    entry = "### Cycle 152 — 2026-08-01 02:00 Oslo — A Real Title\n\nNo footer here at all.\n"
    assert _kinds(lint("168-cycle-152.md", entry)) == ["footer"]


# --- the turn clock (idea #77) -----------------------------------------
#
# Cycle 246 claimed "four minutes left" and "spent half an hour" inside a
# cycle that measured 673 seconds. The tests that matter are the ones
# pinning what this check must *not* do: it must stay silent when there
# is no clock to read, and it must not fire on a duration the entry is
# reporting about some other cycle, which is most of what a journal says
# about time.

def _with(body):
    return f"### Cycle 250 — 2026-08-17 00:20 Oslo — A Real Title\n\n{body}\n\nPR: none | Outcome: n/a"


def _clock_findings_only(body, clock):
    findings = lint(
        "260-cycle-250.md",
        _with(body),
        now=datetime(2026, 8, 17, 0, 30, tzinfo=OSLO),
        clock=clock,
    )
    return [f for f in findings if f.startswith("clock:")]


def test_an_elapsed_claim_longer_than_the_cycle_is_caught():
    found = _clock_findings_only("I spent half an hour perfecting the guard.", (11.0, 34.0))
    assert len(found) == 1
    assert "30 minutes" in found[0] and "11 minutes ago" in found[0]


def test_a_remaining_claim_the_clock_contradicts_is_caught():
    found = _clock_findings_only("I finished with four minutes left.", (11.0, 34.0))
    assert len(found) == 1
    assert "34 minutes are left" in found[0]


def test_an_honest_elapsed_claim_passes():
    assert _clock_findings_only("I spent 20 minutes on the composition.", (34.0, 11.0)) == []


def test_an_honest_remaining_claim_passes():
    assert _clock_findings_only("I am writing this with 12 minutes left.", (34.0, 11.0)) == []


def test_no_clock_means_no_finding_rather_than_a_guess():
    # The file is absent between turns and this tool is also run by hand.
    assert _clock_findings_only("I spent half an hour perfecting the guard.", None) == []


# Every one of these is real prose from the live journal, or the shape of
# it. The reviewer found this list was originally all elapsed-shaped, so
# the remaining pattern's total lack of a first-person guard went
# unexercised and it fired on ordinary retrospective writing.
@pytest.mark.parametrize("body", [
    "Cycle 12 burned ~16 minutes proving that waiting is a deadlock.",
    "Waiting on the new pod cost that cycle 40 minutes.",
    "The turn cap is 45 minutes and a turn that overruns is killed.",
    "Each cycle has 45 minutes left of a five-hour window.",
    "A cycle with an hour left has every reason to keep going.",
    "It speaks at 15, 8 and 3 minutes remaining, and every single time.",
    "There are two position reports now, at 30 and 22 minutes left.",
    "The previous cycle would not ship this with twenty minutes left.",
    "Cycle 246 claimed four minutes left when it had thirty-four.",
    "Starting it with twenty minutes left would have left a mess.",
    "The hook warns me when I have 15 minutes left, then 8, then 3.",
])
def test_a_duration_that_is_not_a_claim_about_this_turn_is_left_alone(body):
    assert _clock_findings_only(body, (11.0, 34.0)) == []


# The whitelist that used to exempt 40/45/60/72/300 was deleted, and this
# is why. It was compensating for the loose remaining pattern above, and
# the price was silently passing "an hour" -- 60 minutes inside a
# 45-minute cap, physically impossible and the single roundest number a
# made-up figure reaches for.
@pytest.mark.parametrize("claim,minutes", [
    ("I spent an hour on the composition.", 60),
    ("I spent 1 hour on the composition.", 60),
    ("I spent 60 minutes on the composition.", 60),
    ("I spent 45 minutes on the composition.", 45),
    ("I spent 40 minutes on the composition.", 40),
])
def test_a_round_constant_is_not_a_free_pass_for_an_elapsed_claim(claim, minutes):
    found = _clock_findings_only(claim, (11.0, 34.0))
    assert len(found) == 1
    assert f"{minutes} minutes" in found[0]


@pytest.mark.parametrize("body", [
    "I had about ten minutes left, which is how this happened.",
    "I finished the code with four minutes left.",
    "I did not build it with twenty minutes left.",
])
def test_a_first_person_remaining_claim_is_still_caught(body):
    assert len(_clock_findings_only(body, (11.0, 34.0))) == 1


def test_a_turn_past_its_deadline_is_described_as_overdue_not_as_negative():
    found = _clock_findings_only("I have thirty minutes left.", (65.0, -20.0))
    assert len(found) == 1
    assert "20 minutes past its deadline" in found[0]
    assert "-20" not in found[0]


def test_the_clock_check_reads_the_deadline_file_when_one_exists(tmp_path):
    from tools.lint_entry import read_turn_clock
    import json as _json
    import time as _time

    path = tmp_path / "turn-deadline.json"
    started = _time.time() - 600
    path.write_text(_json.dumps({
        "started_at": started,
        "deadline_at": started + 2700,
        "timeout_seconds": 2700,
    }))
    elapsed, remaining = read_turn_clock(str(path))
    assert 9.5 < elapsed < 10.5
    assert 34.5 < remaining < 35.5


@pytest.mark.parametrize("payload", ["", "not json", '{"started_at": "soon"}', "[]"])
def test_an_unusable_deadline_file_reads_as_no_clock(tmp_path, payload):
    from tools.lint_entry import read_turn_clock

    path = tmp_path / "turn-deadline.json"
    path.write_text(payload)
    assert read_turn_clock(str(path)) is None


def test_a_missing_deadline_file_reads_as_no_clock(tmp_path):
    from tools.lint_entry import read_turn_clock

    assert read_turn_clock(str(tmp_path / "nope.json")) is None


@pytest.mark.parametrize("body", [
    'Cycle 246 wrote "I spent half an hour perfecting" it, which was untrue.',
    'Its reply said "with four minutes left" against a 673-second run.',
    "The entry claimed `I spent half an hour` and nothing checked it.",
    "> I finished with four minutes left.",
])
def test_a_quoted_claim_is_not_the_authors_own_claim(body):
    # The entries most likely to discuss a confabulated number are the
    # ones written *about* confabulated numbers -- this check refusing
    # them is how it would get routed around.
    assert _clock_findings_only(body, (11.0, 34.0)) == []


def test_an_unquoted_claim_beside_a_quoted_one_is_still_caught():
    found = _clock_findings_only(
        'Cycle 246 said "with four minutes left". I spent half an hour on this.',
        (11.0, 34.0),
    )
    assert len(found) == 1
    assert "30 minutes" in found[0]


# --- The `Needs Edvard` ask label -----------------------------------------
#
# Cycle 262 made `_ASK_RE` require the colon, which fixed two 2026-08-11
# entries that named the old digest section in prose and parsed as open
# asks. It also made the opposite mistake silent: the bare label now has
# its ask dropped with no error, which is worse, because the cycle
# believes it asked and nothing on the card says otherwise.


def test_a_correctly_written_ask_passes():
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs Edvard:** Do you want the status "
        "glyphs gone as well?",
    )
    assert lint("168-cycle-152.md", entry) == []


def test_an_ask_that_lost_its_colon_is_caught():
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs Edvard** Do you want the status "
        "glyphs gone as well?",
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["ask"]
    assert "colon inside the bold" in findings[0]


def test_the_colon_outside_the_bold_is_the_parsers_other_accepted_form():
    """`_ASK_RE` takes `**Needs Edvard**:` as well, so this check must not
    contradict it -- a linter that disagrees with the parser is worse than
    no linter."""
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs Edvard**: Do you want the glyphs "
        "gone?",
    )
    assert lint("168-cycle-152.md", entry) == []


def test_an_entry_with_no_ask_at_all_says_nothing():
    """7 of 326 live entries carry one. Silence is the common answer."""
    assert lint("168-cycle-152.md", GOOD) == []


def test_prose_naming_the_section_mid_paragraph_is_not_an_ask():
    """The measured false positive, and why the anchor is a blank line
    rather than a line start.

    `012-cycle-12.md` wraps a sentence so that `**Needs Edvard** and **Next
    cycle** in there with it` begins a line in the middle of a paragraph.
    The entries are hard-wrapped, so a line start is not a paragraph start;
    anchoring on one fires on that entry, and on the blank line it matches
    none of the 326 live documents.
    """
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "I moved the digest's three sections into their own file, taking\n"
        "**Needs Edvard** and **Next cycle** in there with it rather than\n"
        "leaving two behind.",
    )
    assert lint("168-cycle-152.md", entry) == []


def test_prose_naming_the_section_at_a_paragraph_start_still_needs_no_colon_after_it():
    """A paragraph genuinely opening with the label as prose reads
    `**Needs Edvard**,` -- punctuation, not whitespace -- so it is out of
    reach of the check by shape rather than by exception."""
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "**Needs Edvard**, **Next cycle**, and a one-line-per-cycle "
        "**Digest** are what he asked for.",
    )
    assert lint("168-cycle-152.md", entry) == []


# --- The ask has to open with the question --------------------------------
#
# Edvard, unboarded capture 2026-08-20, naming Cycle 273's block: *"its a
# wall of text and a question hidden in it at the very bottom ... Example
# is 'yes or no, keep the symbols for x, y, z?' After that, you can explain
# the reason"*. Measured over all 333 live entries before the check was
# written: 8 carry an ask, and it fires on exactly 6 of them.


def _ask_entry(ask):
    return GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs Edvard:** " + ask,
    )


def test_an_ask_opening_with_a_statement_is_caught():
    """Cycle 273's shape, shortened: the reason first, the question buried."""
    findings = lint(
        "168-cycle-152.md",
        _ask_entry(
            "You told me on the 19th that the coloured circles were "
            "unreadable, and the priorities now carry words. Should the "
            "status circles follow?"
        ),
    )
    assert _kinds(findings) == ["ask"]
    assert "opens with a statement" in findings[0]


def test_an_ask_that_leads_with_the_question_passes():
    ask = (
        "Yes or no, should the status circles become words like the "
        "priorities did? You told me on the 19th that the coloured "
        "circles were unreadable."
    )
    entry = _ask_entry(ask)
    # Asserted first, because `lint(...) == []` on its own cannot tell "the
    # check read this ask and approved it" from "there was no ask to read".
    # Reviewer finding on #254.
    assert split_ask(_raw_body(entry))[1].startswith("Yes or no,")
    assert lint("168-cycle-152.md", entry) == []


@pytest.mark.parametrize(
    "opening",
    [
        "Should I raise the limit above $0.00, i.e. to any non-zero number?",
        "Should Mr. Anderson sign off on this change, yes or no?",
        "Should we go with plan A vs. plan B?",
        "Should we cover x, y, z, etc. before shipping, yes or no?",
    ],
)
def test_an_abbreviation_does_not_end_the_first_sentence(opening):
    """Refusing a correctly written ask is the expensive direction to fail
    in -- it is an ask that cannot be published at all.

    The first version tested only `i.e.`, where every letter is its own
    token, and that was the one abbreviation shape its one-character
    lookbehind handled; `Mr.`, `vs.` and `etc.` were all refused. The
    reviewer found that by running these four rather than reading the
    regex. `$0.00` is here for completeness and is *not* what pins the
    abbreviation rule -- it is protected by the `(?=\\s|\\Z)` lookahead
    instead, since neither of its dots is followed by whitespace.
    """
    assert lint("168-cycle-152.md", _ask_entry(opening + " The reason.")) == []


def test_a_question_mark_inside_a_quotation_does_not_make_the_opening_a_question():
    """The failure this check exists to catch, surviving the check.

    Every ask ever written quotes Edvard or an earlier entry, so a `?`
    early in the paragraph is normal and says nothing about whether *this*
    paragraph opens by asking something. Reviewer finding on #254; the
    first version passed both of these silently.
    """
    for ask in (
        'The card that said "should I proceed?" was from last week and is '
        "now stale. Should I proceed now, yes or no?",
        "Remember when you asked `is this ready?` -- it still is not. Do you "
        "want me to keep waiting, yes or no?",
    ):
        findings = lint("168-cycle-152.md", _ask_entry(ask))
        assert _kinds(findings) == ["ask"], ask
        assert "opens with a statement" in findings[0]


def test_a_sentence_ending_in_no_is_still_the_end_of_a_sentence():
    """`no.` is an abbreviation for "number" and is not on the list, on
    purpose: a sentence of mine ends in "no." far more often than it
    contains "No. 5", so listing it would trade a rare false refusal for a
    common false acceptance."""
    findings = lint(
        "168-cycle-152.md",
        _ask_entry("Last time the answer was no. Should I proceed now?"),
    )
    assert _kinds(findings) == ["ask"]


def test_a_quoted_question_after_a_real_one_still_passes():
    """The mirror of the case above -- masking quotations must not refuse an
    ask that genuinely opens with its question and quotes him afterwards,
    which is the shape `personality.md` actually asks for."""
    assert (
        lint(
            "168-cycle-152.md",
            _ask_entry(
                "Yes or no, should the status circles become words? You said "
                '"i can\'t really see the difference as they are colors".'
            ),
        )
        == []
    )


def test_an_ask_with_no_sentence_end_at_all_is_caught():
    """A trailing fragment has no `?` anywhere, so it is not a question --
    the `match is None` branch, which the two cases above never reach."""
    findings = lint("168-cycle-152.md", _ask_entry("The token has expired"))
    assert _kinds(findings) == ["ask"]
    assert "opens with a statement" in findings[0]


def test_the_bare_label_is_told_about_the_colon_and_not_about_the_shape():
    """The two ask checks are mutually exclusive by construction: one needs
    `split_ask` to have found nothing, the other needs it to have found
    something. An entry missing the colon should get one finding, not two."""
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs Edvard** The token has expired.",
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["ask"]
    assert "colon inside the bold" in findings[0]


def test_the_new_ask_label_without_a_colon_is_a_finding():
    """`**Needs input**` drops the same silent way `**Needs Edvard**` did.

    The bare-label check imports `ASK_LABEL` from the parser instead of
    respelling it, so this is the test that the import actually carries
    both spellings rather than the linter guarding only the old one.
    """
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs input** Do you want the status "
        "glyphs gone as well?",
    )
    findings = lint("168-cycle-152.md", entry)
    assert _kinds(findings) == ["ask"]
    assert "colon inside the bold" in findings[0]


def test_the_new_ask_label_with_a_colon_is_clean():
    entry = GOOD.replace(
        "Something real happened and here is the honest account of it.",
        "Something real happened.\n\n**Needs input:** Do you want the status "
        "glyphs gone as well?",
    )
    assert lint("168-cycle-152.md", entry) == []


# --- the title check -------------------------------------------------
#
# `parse_heading` leaves `title: ""` when the heading is nothing but a
# date, a time and a cycle number, and `app.js` then appends no
# `entry-title` paragraph at all -- a card labelled by nothing. Measured
# on the live feed 2026-08-22: 169 of 366 entries, 6 of the newest 60.

UNTITLED = """### 2026-08-01 02:00 (Oslo) — Cycle 152

Something real happened and here is the honest account of it.

---
PR: #133 | Outcome: merged
"""


def test_a_heading_with_no_title_is_caught():
    assert _kinds(lint("168-cycle-152.md", UNTITLED)) == ["title"]


def test_the_title_finding_names_the_convention():
    finding = lint("168-cycle-152.md", UNTITLED)[0]
    assert "### Cycle N — HH:MM Oslo — <Title>" in finding


def test_a_promoted_heading_with_no_title_is_still_caught():
    """A `## ` heading keeps its text through `normalise_entry`, so an
    empty title there is the author's and not an artefact of the repair.
    Two findings, deliberately -- the depth and the title are independent
    defects and fixing one does not fix the other."""
    assert _kinds(lint("168-cycle-152.md", UNTITLED.replace("### ", "## ", 1))) == [
        "heading",
        "title",
    ]


def test_a_synthesised_heading_does_not_report_a_missing_title_too():
    """No heading at all -> `normalise_entry` builds `Cycle 152` from the
    filename, which has no prose left over by construction. Reporting a
    missing title there is the same defect said twice."""
    body = "Something real happened.\n\n---\nPR: #133 | Outcome: merged\n"
    assert _kinds(lint("168-cycle-152.md", body)) == ["heading"]


def test_edvards_own_message_needs_no_title():
    """The one live filename with no cycle token is Edvard's own first
    message. It is not a cycle and owes no title."""
    body = "### 2026-08-02 — Edvard\n\nA message.\n"
    assert "title" not in _kinds(lint("004-2026-08-02-edvard-s-first-message-not-a.md", body))
